"""Persistent deployment adapter around the reviewed loopback demo boundary."""
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
import argparse
import gzip
import json
import re
import time
import sqlite3
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/step4'))
from demo_gateway import Policy, make_gateway, COOKIE
import demo_gateway as boundary_core
from state_store import Store, load_secret

# Linux keeps closed TCP connections in TIME_WAIT. Reuse the listener address
# during service recovery; a second live listener still cannot bind the port.
class PersistentGatewayServer(boundary_core.GatewayServer):
    allow_reuse_address = sys.platform != 'win32'

boundary_core.GatewayServer = PersistentGatewayServer

# A visitor who already opened the bare host can paste the full link into the same
# document. Handle that fragment navigation as well as a first document load.
boundary_core.LINK_LOGIN += b"<script>addEventListener('hashchange',()=>{if(new URLSearchParams(location.hash.slice(1)).has('access'))location.reload()})</script>"


class PersistentPolicy(Policy):
    def __init__(self, host, secret, store, *, mode='synthetic', upstream_port=8766):
        if mode not in ('synthetic', 'dashboard'):
            raise ValueError('INVALID_DEPLOYMENT_MODE')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*\.ts\.net', host):
            raise ValueError('EXACT_FUNNEL_HOST_REQUIRED')
        # Explicitly confined to this new deployment; never relax the original runner's host rules.
        approval = {'approved_by_user': True, 'scope': 'SECRET_LINK_TEST',
                    'instruction': 'Persistent gateway adapter; dataset admission enforced by backend configuration'}
        super().__init__(mode='dashboard', rights={'confirmed': False},
                         test_authorization=approval, access_mode='secret_link',
                         link_secret=secret, upstream_port=upstream_port)
        self.host, self.store = host, store
        self.request_context = threading.local()
        self.access_basis = 'SYNTHETIC_ONLY' if mode == 'synthetic' else 'GCP_EXPLICIT_DATA_ADMISSION_REQUIRED'

    def reserve(self, engine):
        return self.store.reserve(engine, getattr(self.request_context, 'session', ''))

    def authenticate(self, token):
        return self.store.login(token, self.link_secret)

    def session_value(self, header):
        try:
            cookie = SimpleCookie()
            cookie.load(header or '')
            return cookie[COOKIE].value if COOKIE in cookie else ''
        except (CookieError, ValueError, TypeError):
            return ''

    def authenticated(self, header):
        return self.store.session_valid(self.session_value(header))


def persistent_gateway(policy, port=8767):
    server = make_gateway(policy, port)
    base = server.RequestHandlerClass

    class PersistentHandler(base):
        question_id = None

        def do_GET(self):
            try:
                if urlsplit(self.path).path == '/api/runtime/status':
                    if not self.boundary():return
                    if not policy.authenticated(self.headers.get('Cookie')):
                        self.error(401,'AUTH_REQUIRED');return
                    path=policy.store.path.parent/'health.json'
                    status=json.loads(path.read_text(encoding='utf-8'))
                    good=status.get('healthy') is True and time.time()-status.get('checked_at_unix',0)<180
                    self.respond(200 if good else 503,json.dumps(status).encode())
                    return
                if urlsplit(self.path).path == '/dashboard.js':
                    if not self.boundary():return
                    if not policy.authenticated(self.headers.get('Cookie')):
                        self.error(401,'AUTH_REQUIRED');return
                    self.respond(200,Path(__file__).with_name('dashboard.js').read_bytes(),'application/javascript; charset=utf-8')
                    return
                super().do_GET()
            except (sqlite3.Error,OSError,ValueError):
                self.error(503,'PRIVATE_STATE_UNAVAILABLE')

        def do_POST(self):
            policy.request_context.session = policy.session_value(self.headers.get('Cookie'))
            try:super().do_POST()
            except sqlite3.Error:self.error(503,'PRIVATE_STATE_UNAVAILABLE')
            finally:policy.request_context.session = ''

        def forward(self, payload=None):
            if payload is not None:
                try:
                    self.question_id = policy.store.begin_request(
                        policy.session_value(self.headers.get('Cookie')),
                        json.loads(payload), self.headers.get('X-Request-ID'))
                except ValueError:
                    self.error(409, 'DUPLICATE_OR_INVALID_REQUEST_ID')
                    return
                except sqlite3.Error:
                    self.error(503, 'PRIVATE_STATE_UNAVAILABLE')
                    return
            super().forward(payload)

        def respond(self, status, body, mime='application/json; charset=utf-8', session=None):
            identifier, self.question_id = self.question_id, None
            if identifier:
                try:
                    policy.store.finish_request(identifier, status, body)
                except sqlite3.Error:
                    status, body, mime = 503, b'{"error":{"code":"PRIVATE_STATE_UNAVAILABLE"}}', 'application/json'
            self.response_gzip=False
            if status==200 and mime.startswith('text/html'):
                # Browser preload can fetch all feature scripts at once and hit
                # the four-request boundary. The adapter loads these in order
                # and retries failed GETs before enabling page controllers.
                scripts=b'<script src="/dashboard.js"></script><script src="/roa_dashboard.js"></script><script src="/welfare_explorer.js"></script><script src="/activity_explorer.js"></script>'
                body=body.replace(scripts,b'<script src="/dashboard.js"></script>')
            encodings=self.headers.get('Accept-Encoding','').split(',')
            accepts_gzip=any(part.split(';',1)[0].strip()=='gzip' and not re.search(r';\s*q=0(?:\.0*)?(?:$|;)',part) for part in encodings)
            if self.command=='GET' and status==200 and len(body)>1024 and accepts_gzip and (mime.startswith('text/') or 'javascript' in mime or 'json' in mime):
                body=gzip.compress(body,compresslevel=5,mtime=0)
                self.response_gzip=True
            # The inherited 10s request-body timeout also applied to large writes.
            # Keep read protection, but allow a bounded large asset response.
            self.connection.settimeout(60 if len(body)>262144 else 10)
            super().respond(status, body, mime, session)

        def end_headers(self):
            if getattr(self,'response_gzip',False):
                self.send_header('Content-Encoding','gzip')
                self.send_header('Vary','Accept-Encoding')
            super().end_headers()

    server.RequestHandlerClass = PersistentHandler
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.settings.read_text(encoding='utf-8'))
    mode = config.get('mode', 'synthetic')
    if mode == 'dashboard' and config.get('gcp_data_transfer_approved') is not True:
        raise ValueError('GCP_DATA_TRANSFER_APPROVAL_REQUIRED')
    state = Store(config['state_db'])
    state.seed('llm_question', config.get('baseline_llm_questions', 0))
    state.seed('provider', config.get('baseline_provider_reservations', 0))
    policy = PersistentPolicy(config['public_host'], load_secret(config['access_secret_file']), state, mode=mode)
    server = persistent_gateway(policy)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
