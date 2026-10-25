"""Boundary tests use synthetic replies only; zero provider or data calls."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import tempfile
import threading
import time
import unittest

from demo_gateway import Policy, make_gateway
from run_demo import config

URL = 'https://unit-test-demo.trycloudflare.com'
HOST = 'unit-test-demo.trycloudflare.com'


class Fake(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.reply()

    def do_POST(self):
        self.reply()

    def reply(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.seen.append({'path': self.path, 'headers': dict(self.headers), 'body': body.decode()})
        raw = json.dumps({'synthetic': True}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        Fake.seen = []
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        self.addCleanup(self.backend.server_close)
        self.addCleanup(self.backend.shutdown)
        self.policy = Policy(mode='dashboard', rights={'confirmed': True, 'evidence': 'synthetic test'},
                             upstream_port=self.backend.server_port, ledger=Path(self.folder.name) / 'budget.json')
        self.gateway = make_gateway(self.policy, 0)
        threading.Thread(target=self.gateway.serve_forever, daemon=True).start()
        self.addCleanup(self.gateway.server_close)
        self.addCleanup(self.gateway.shutdown)

    def request(self, path='/', method='GET', payload=None, host=HOST, origin=None, extra=None):
        headers = {'Host': host}
        if origin is not None:
            headers['Origin'] = origin
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        client = HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=4)
        client.request(method, path, payload, headers)
        response = client.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        client.close()
        return result

    def post(self, payload=b'{"engine":"structured"}', origin=URL, extra=None):
        return self.request('/api/agent', 'POST', payload, origin=origin, extra=extra)

    def test_closed_until_exact_tunnel_host_set(self):
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(Fake.seen, [])
        for url in ['http://' + HOST, URL + '/path', 'https://example.com', 'https://a.trycloudflare.com.evil.invalid']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.policy.open_for(url)
        self.policy.open_for(URL)
        with self.assertRaises(ValueError):
            self.policy.open_for('https://another.trycloudflare.com')

    def test_dashboard_cannot_start_without_rights_evidence(self):
        for rights in [None, {}, {'confirmed': True}, {'confirmed': False, 'evidence': 'x'}]:
            with self.subTest(rights=rights), self.assertRaises(ValueError):
                Policy(mode='dashboard', rights=rights)

    def test_user_authorized_team_test_keeps_rights_unverified(self):
        rights = {'confirmed': False, 'evidence': ''}
        approval = {'approved_by_user': True, 'scope': 'EMAIL_RESTRICTED_TEAM_TEST', 'instruction': 'explicit user test authorization'}
        policy = Policy(mode='dashboard', rights=rights, test_authorization=approval)
        self.assertEqual(policy.access_basis, 'USER_AUTHORIZED_TEAM_TEST')
        self.assertFalse(rights['confirmed'])
        for invalid in [{}, {**approval, 'approved_by_user': False}, {**approval, 'scope': 'PUBLIC'}, {**approval, 'instruction': ''}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Policy(mode='dashboard', rights=rights, test_authorization=invalid)

    def test_preview_blocks_all_private_paths_and_agent(self):
        self.policy.mode = 'preview'
        self.policy.open_for(URL)
        self.assertIn('제한 데모'.encode(), self.request()[1])
        for path in ['/api/map', '/api/roa/detail?dong=11230510', '/api/agent/status', '/llm.env', '/outputs/x', '/%2e%2e/llm_config.py']:
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], (403, 404))
        self.assertEqual(self.post()[0], 403)
        self.assertEqual(Fake.seen, [])

    def test_host_and_origin_forwarded_headers_do_not_authorize(self):
        self.policy.open_for(URL)
        for host in ['evil.invalid', '127.0.0.1:8767', HOST + ':443']:
            with self.subTest(host=host):
                self.assertEqual(self.request(host=host, extra={'X-Forwarded-Host': HOST, 'Cf-Access-Authenticated-User-Email': 'pretend@example.com'})[0], 403)
        for origin in [None, 'null', 'http://' + HOST, 'https://evil.invalid', URL + '.evil.invalid']:
            with self.subTest(origin=origin):
                self.assertEqual(self.post(origin=origin)[0], 403)
        self.assertEqual(Fake.seen, [])

    def test_allowlist_rewrites_only_validated_local_request(self):
        self.policy.open_for(URL)
        self.assertEqual(self.post(extra={'X-Forwarded-For': 'untrusted', 'Cookie': 'secret', 'Authorization': 'secret', 'Cf-Access-Authenticated-User-Email': 'untrusted'})[0], 200)
        headers = Fake.seen[0]['headers']
        self.assertEqual(headers['Host'], '127.0.0.1:' + str(self.backend.server_port))
        self.assertEqual(headers['Origin'], 'http://' + headers['Host'])
        for name in ['Cookie', 'Authorization', 'X-Forwarded-For', 'Cf-Access-Authenticated-User-Email']:
            self.assertNotIn(name, headers)
        for path in ['/outputs/x', '/llm_config.py', '/api/refresh', '/%2e%2e/llm.env', 'http://evil.invalid/api/map', '//evil.invalid/api/map']:
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], (400, 404))
        self.assertEqual(len(Fake.seen), 1)

    def test_malformed_json_and_framing(self):
        self.policy.open_for(URL)
        for body in [b'{"engine":"llm","engine":"structured"}', b'[]', b'{"x":NaN}', b'{"engine":"other"}', b'x' * 12001]:
            with self.subTest(body=body[:40]):
                self.assertEqual(self.post(body)[0], 400)
        self.assertEqual(self.post(extra={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(Fake.seen, [])

    def test_duplicate_host_and_origin_rejected(self):
        self.policy.open_for(URL)
        for name, value in [('Host', HOST), ('Origin', URL)]:
            with self.subTest(name=name):
                client = HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=4)
                client.putrequest('POST', '/api/agent', skip_host=True)
                for key, val in [('Host', HOST), ('Origin', URL), (name, value), ('Content-Type', 'application/json'), ('Content-Length', '2')]:
                    client.putheader(key, val)
                client.endheaders(b'{}')
                response = client.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                client.close()
        self.assertEqual(Fake.seen, [])

    def test_rate_limit_and_persistent_question_budget(self):
        self.policy.open_for(URL)
        self.policy.llm_used = 9
        self.assertEqual(self.post(b'{"engine":"llm"}')[0], 200)
        self.assertEqual(self.post(b'{"engine":"llm"}')[0], 429)
        restored = Policy(ledger=self.policy.ledger)
        self.assertEqual(restored.llm_used, 10)
        for _ in range(5):
            self.assertEqual(self.post()[0], 200)
        self.assertEqual(self.post()[0], 429)
        self.assertEqual(len(Fake.seen), 6)

    def test_busy_and_backend_down_fail_closed(self):
        self.policy.open_for(URL)
        self.policy.agent_slot.acquire()
        self.assertEqual(self.post()[0], 429)
        self.policy.agent_slot.release()
        self.backend.shutdown()
        self.backend.server_close()
        status, body, _ = self.request('/api/catalog')
        self.assertEqual(status, 503)
        self.assertNotIn(str(Path.cwd()).encode(), body)

    def test_email_wildcards_and_missing_rights_rejected(self):
        path = Path(self.folder.name) / 'settings.json'
        for emails in [[], ['*@example.com'], ['x@example.com,y@example.com'], ['x@example.com', 'x@example.com']]:
            path.write_text(json.dumps({'allowed_emails': emails}), encoding='utf-8')
            with self.subTest(emails=emails), self.assertRaises(ValueError):
                config(path)

    def test_diagnostic_events_drop_query_headers_and_unknown_path(self):
        path = Path(self.folder.name) / 'events.jsonl'
        policy = Policy(events=path)
        policy.record('GET', '/api/roa/welfare?query=PRIVATE_SENTINEL', 200, 12, .1)
        policy.record('GET', '/PRIVATE_SENTINEL', 404, 0, .2)
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE_SENTINEL', text)
        self.assertIn('<unlisted>', text)
        self.assertEqual(json.loads(text.splitlines()[0])['route'], '/api/roa/welfare')


class SecretLinkTests(unittest.TestCase):
    request = Boundaries.request

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        Fake.seen = []
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        self.addCleanup(self.backend.server_close)
        self.addCleanup(self.backend.shutdown)
        approval = {'approved_by_user': True, 'scope': 'SECRET_LINK_TEST', 'instruction': 'user authorizes holders of the access link'}
        self.policy = Policy(mode='dashboard', rights={'confirmed': False}, test_authorization=approval,
                             access_mode='secret_link', link_secret='A' * 43,
                             upstream_port=self.backend.server_port, ledger=Path(self.folder.name) / 'budget.json')
        self.policy.open_for(URL)
        self.gateway = make_gateway(self.policy, 0)
        threading.Thread(target=self.gateway.serve_forever, daemon=True).start()
        self.addCleanup(self.gateway.server_close)
        self.addCleanup(self.gateway.shutdown)

    def login(self, token='A' * 43, origin=URL):
        return self.request('/auth/link', 'POST', json.dumps({'token': token}).encode(), origin=origin)

    def test_public_landing_and_all_data_paths_require_session(self):
        status, body, _ = self.request()
        self.assertEqual(status, 200)
        self.assertNotIn(b'roa_dashboard.js', body)
        self.assertNotIn(b'A' * 43, body)
        for path in ['/api/catalog', '/api/map', '/api/agent/status', '/dashboard.js']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.request('/api/agent', 'POST', b'{}', origin=URL)[0], 401)
        self.assertEqual(Fake.seen, [])

    def test_valid_link_issues_secure_session_and_strips_cookie_from_upstream(self):
        self.assertEqual(self.login('B' * 43)[0], 403)
        status, _, headers = self.login()
        self.assertEqual(status, 200)
        value = headers['Set-Cookie']
        for flag in ['Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/']:
            self.assertIn(flag, value)
        cookie = value.split(';')[0]
        self.assertEqual(self.request('/api/catalog', extra={'Cookie': cookie})[0], 200)
        self.assertEqual(self.request('/api/agent', 'POST', b'{"engine":"structured"}', origin=URL, extra={'Cookie': cookie})[0], 200)
        self.assertNotIn('Cookie', Fake.seen[-1]['headers'])
        self.assertFalse(self.policy.authenticated('__Host-bbigeudeok-demo=forged'))
        session = cookie.split('=', 1)[1]
        self.policy.sessions[session] = time.monotonic() - 1
        self.assertEqual(self.request('/api/catalog', extra={'Cookie': cookie})[0], 401)

    def test_login_csrf_framing_duplicates_and_guess_rate_limit(self):
        self.assertEqual(self.login(origin='https://evil.invalid')[0], 403)
        for body in [b'[]', b'[["token","x"]]', b'{"token":"x","token":"y"}', b'{"token":NaN}', b'{"token":"x","other":1}']:
            with self.subTest(body=body):
                self.assertEqual(self.request('/auth/link', 'POST', body, origin=URL)[0], 400)
        for _ in range(10):
            self.assertEqual(self.login('B' * 43)[0], 403)
        self.assertEqual(self.login()[0], 429)
        self.assertEqual(Fake.seen, [])

    def test_secret_link_cannot_start_without_specific_authorization(self):
        for approval in [None, {'approved_by_user': True, 'scope': 'EMAIL_RESTRICTED_TEAM_TEST', 'instruction': 'email only'}]:
            with self.subTest(approval=approval), self.assertRaises(ValueError):
                Policy(mode='dashboard', test_authorization=approval, access_mode='secret_link', link_secret='A' * 43)


if __name__ == '__main__':
    unittest.main(verbosity=2)
