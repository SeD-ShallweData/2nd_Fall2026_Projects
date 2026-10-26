"""Synthetic HTTP boundary, persistence and idempotency checks for the GCP adapter."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import json
import gzip
import sys
import tempfile
import threading
import unittest

from gateway import PersistentPolicy, persistent_gateway
from state_store import Store, digest

class Fake(BaseHTTPRequestHandler):
    seen=[]
    def log_message(self,*args):pass
    def do_GET(self):self.reply()
    def do_POST(self):self.reply()
    def reply(self):
        self.seen.append({'headers':dict(self.headers)})
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        html=self.path.startswith('/index.html')
        body=b'<script src="/dashboard.js"></script><script src="/roa_dashboard.js"></script><script src="/welfare_explorer.js"></script><script src="/activity_explorer.js"></script>' if html else b'{"synthetic":true}'
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.send_header('Content-Type','text/html' if html else 'application/json')
        self.end_headers();self.wfile.write(body)

HOST='synthetic.tailunit.ts.net'
ORIGIN='https://'+HOST


class PersistentGatewayTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        Fake.seen=[]
        self.backend=ThreadingHTTPServer(('127.0.0.1',0),Fake)
        threading.Thread(target=self.backend.serve_forever,daemon=True).start()
        self.addCleanup(self.backend.server_close);self.addCleanup(self.backend.shutdown)
        self.store=Store(Path(self.folder.name)/'state.sqlite')
        self.policy=PersistentPolicy(HOST,'A'*43,self.store,upstream_port=self.backend.server_port)
        self.server=persistent_gateway(self.policy,0)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self,path='/',body=None,cookie=None,extra=None):
        client=HTTPConnection('127.0.0.1',self.server.server_port,timeout=4)
        headers={'Host':HOST}
        if body is not None:headers.update({'Origin':ORIGIN,'Content-Type':'application/json'})
        if cookie:headers['Cookie']=cookie
        headers.update(extra or {})
        client.request('POST' if body is not None else 'GET',path,body,headers)
        r=client.getresponse();result=(r.status,r.read(),dict(r.getheaders()));client.close()
        return result

    def login(self):
        status,_,headers=self.request('/auth/link',b'{"token":"'+b'A'*43+b'"}')
        self.assertEqual(status,200)
        return headers['Set-Cookie'].split(';',1)[0]

    def test_ts_host_is_exact_and_unapproved_host_rejected(self):
        with self.assertRaises(ValueError):PersistentPolicy('evil.example','A'*43,self.store)
        self.assertEqual(self.request('/api/catalog',extra={'Host':'other.tailunit.ts.net'})[0],403)
        self.assertEqual(Fake.seen,[])

    @unittest.skipIf(sys.platform == 'win32', 'Linux service recovery socket semantics')
    def test_stopped_listener_can_restart_immediately(self):
        other=persistent_gateway(self.policy,0)
        port=other.server_port
        threading.Thread(target=other.serve_forever,daemon=True).start()
        client=HTTPConnection('127.0.0.1',port,timeout=4)
        client.request('GET','/',headers={'Host':HOST})
        response=client.getresponse();response.read();client.close()
        other.shutdown();other.server_close()
        restarted=persistent_gateway(self.policy,port)
        self.addCleanup(restarted.server_close)
        self.assertEqual(restarted.server_port,port)

    def test_private_data_and_frontend_overlay_require_login(self):
        self.assertEqual(self.request()[0],200)
        for path in ('/api/catalog','/dashboard.js','/api/runtime/status'):
            self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.request('/infra/gcp/state_store.py')[0],404)
        cookie=self.login()
        status,body,_=self.request('/dashboard.js',cookie=cookie)
        self.assertEqual(status,200)
        self.assertIn(b'crypto.randomUUID()',body)
        self.assertNotIn(b"endsWith('.trycloudflare.com')",body)

    def test_cookie_survives_policy_recreation_and_cannot_be_forged(self):
        cookie=self.login()
        recreated=PersistentPolicy(HOST,'A'*43,Store(self.store.path),upstream_port=self.backend.server_port)
        self.assertTrue(recreated.authenticated(cookie))
        self.assertFalse(recreated.authenticated('__Host-bbigeudeok-demo=forged'))

    def test_duplicate_request_does_not_replay_and_history_persists(self):
        cookie=self.login()
        payload=b'{"engine":"structured","question":"SYNTHETIC"}'
        headers={'X-Request-ID':'a'*32}
        self.assertEqual(self.request('/api/agent',payload,cookie,headers)[0],200)
        self.assertEqual(self.request('/api/agent',payload,cookie,headers)[0],409)
        self.assertEqual(len(Fake.seen),1)
        self.assertNotIn('Cookie',Fake.seen[0]['headers'])
        session=self.policy.session_value(cookie)
        records=Store(self.store.path).history(session)
        self.assertEqual(len(records),1);self.assertEqual(records[0][2],200)

    def test_wrong_origin_never_reaches_backend(self):
        cookie=self.login()
        self.assertEqual(self.request('/api/agent',b'{"engine":"structured"}',cookie,{'Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(Fake.seen,[])

    def test_question_quota_uses_authenticated_session_without_affecting_other_visitors(self):
        cookie=self.login()
        session=self.policy.session_value(cookie)
        with self.store.transaction() as db:
            db.executemany('INSERT INTO reservations(kind,at,session_hash) VALUES (?,?,?)',
                           [('llm_question',self.store.clock(),digest(session))]*30)
        payload=b'{"engine":"llm","question":"SYNTHETIC"}'
        status,body,_=self.request('/api/agent',payload,cookie)
        self.assertEqual(status,429)
        self.assertEqual(json.loads(body)['error']['code'],'DEMO_LLM_HOURLY_LIMIT')
        self.assertEqual(self.request('/api/agent',payload,self.login())[0],200)
        self.assertEqual(len(Fake.seen),1)

    def test_authenticated_document_uses_sequential_feature_loader(self):
        status,body,_=self.request('/index.html',cookie=self.login())
        self.assertEqual(status,200)
        self.assertEqual(body,b'<script src="/dashboard.js"></script>')

    def test_authenticated_script_compression_is_complete_and_can_be_disabled(self):
        cookie=self.login()
        status,body,headers=self.request('/dashboard.js',cookie=cookie,extra={'Accept-Encoding':'gzip, br'})
        self.assertEqual(status,200);self.assertEqual(headers['Content-Encoding'],'gzip')
        self.assertIn(b'crypto.randomUUID()',gzip.decompress(body))
        self.assertEqual(len(body),int(headers['Content-Length']))
        _,plain,headers=self.request('/dashboard.js',cookie=cookie,extra={'Accept-Encoding':'gzip;q=0'})
        self.assertNotIn('Content-Encoding',headers)
        self.assertIn(b'crypto.randomUUID()',plain)


if __name__=='__main__':unittest.main(verbosity=2)
