"""Loopback-only demo boundary. Preview never opens a connection to private data."""
from collections import deque
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit
import hmac
import json
import re
import secrets
import threading
import time

GET_PATHS = frozenset({
    '/', '/index.html', '/plotly.js', '/dashboard.css', '/dashboard.js',
    '/roa_dashboard.js', '/welfare_explorer.js', '/activity_explorer.js',
    '/api/catalog', '/api/map', '/api/detail', '/api/roa/catalog', '/api/roa/map',
    '/api/roa/detail', '/api/roa/welfare', '/api/roa/resource',
    '/api/agent/status', '/api/service/status',
})
ROOT = Path(__file__).resolve().parents[2]
PREVIEW = Path(__file__).with_name('preview.html').read_bytes()
COOKIE = '__Host-bbigeudeok-demo'
LINK_LOGIN = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>삐그덕 · 접속 확인</title><style>body{font:16px/1.8 system-ui,sans-serif;background:#f4f4f4;color:#172334;margin:0}main{max-width:560px;margin:12vh auto;padding:32px;background:white;border:1px solid #dbe2e5;border-radius:16px}h1{font-size:25px;color:#0f766e}p{word-break:keep-all}</style></head><body><main><h1>Team. 삐그덕</h1><p id="message" role="status">전달받은 전용 접속 링크를 확인하고 있습니다.</p></main><script>(async()=>{const token=new URLSearchParams(location.hash.slice(1)).get('access');history.replaceState(null,'','/');const message=document.getElementById('message');if(!token){message.textContent='전달받은 전용 접속 링크 전체로 열어 주세요. 일반 주소만으로는 대시보드에 접근할 수 없습니다.';return;}try{const response=await fetch('/auth/link',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token}),redirect:'error'});if(!response.ok)throw Error();location.replace('/index.html#/overview');}catch{message.textContent='접속 링크가 유효하지 않거나 요청이 많습니다. 전달받은 최신 링크로 잠시 후 다시 열어 주세요.';}})();</script></body></html>'''.encode('utf-8')


class Policy:
    def __init__(self, *, mode='preview', rights=None, test_authorization=None, upstream_port=8766, ledger=None, events=None, access_mode='cloudflare_email', link_secret=None):
        if mode not in ('preview', 'dashboard'):
            raise ValueError('INVALID_MODE')
        rights_verified = (
            isinstance(rights, dict) and rights.get('confirmed') is True
            and isinstance(rights.get('evidence'), str) and rights['evidence'].strip()
        )
        team_test_authorized = (
            isinstance(test_authorization, dict)
            and test_authorization.get('approved_by_user') is True
            and test_authorization.get('scope') in ('EMAIL_RESTRICTED_TEAM_TEST', 'SECRET_LINK_TEST')
            and isinstance(test_authorization.get('instruction'), str)
            and bool(test_authorization['instruction'].strip())
        )
        if mode == 'dashboard' and not (rights_verified or team_test_authorized):
            raise ValueError('DATA_ACCESS_AUTHORIZATION_REQUIRED')
        self.access_basis = 'RIGHTS_VERIFIED' if rights_verified else 'USER_AUTHORIZED_TEAM_TEST' if team_test_authorized else 'PREVIEW_ONLY'
        if access_mode not in ('cloudflare_email', 'secret_link'):
            raise ValueError('INVALID_ACCESS_MODE')
        if access_mode == 'secret_link' and (not team_test_authorized or test_authorization.get('scope') != 'SECRET_LINK_TEST'
                                            or not isinstance(link_secret, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', link_secret)):
            raise ValueError('AUTHORIZED_SECRET_LINK_REQUIRED')
        self.access_mode, self.link_secret = access_mode, link_secret
        if access_mode == 'secret_link':
            self.access_basis = 'USER_AUTHORIZED_LINK_TEST'
        self.sessions, self.login_window = {}, deque()
        if not 1024 <= upstream_port <= 65535:
            raise ValueError('INVALID_UPSTREAM_PORT')
        self.mode, self.upstream_port, self.ledger = mode, upstream_port, ledger
        self.events = events
        self.host = None  # Closed until the runner captures this tunnel's exact URL.
        self.lock = threading.Lock()
        self.event_lock = threading.Lock()
        self.agent_slot = threading.BoundedSemaphore(1)
        self.agent_window = deque()
        self.llm_used = 0
        if ledger and ledger.exists():
            saved = json.loads(ledger.read_text(encoding='utf-8'))
            if type(saved.get('llm_questions_reserved')) is not int or saved['llm_questions_reserved'] < 0:
                raise ValueError('INVALID_LEDGER')
            self.llm_used = saved['llm_questions_reserved']

    def record(self, method, target, status, size, elapsed):
        if not self.events:
            return
        path = urlsplit(target).path
        row = {'at_unix': round(time.time(), 3), 'method': method if method in ('GET', 'POST') else 'OTHER',
               'route': path if path in GET_PATHS or path in ('/api/agent', '/auth/link') else '<unlisted>',
               'status': status, 'response_bytes': size, 'elapsed_ms': round(elapsed * 1000, 2)}
        try:
            with self.event_lock:
                with self.events.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row) + '\n')
        except OSError:
            pass  # Diagnostic storage failure must not reveal request data or break the response.

    def open_for(self, url):
        if not re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com', url):
            raise ValueError('INVALID_QUICK_TUNNEL_URL')
        with self.lock:
            if self.host is not None:
                raise ValueError('HOST_ALREADY_SET')
            self.host = urlsplit(url).hostname

    def reserve(self, engine):
        with self.lock:
            now = time.monotonic()
            while self.agent_window and now - self.agent_window[0] >= 60:
                self.agent_window.popleft()
            if len(self.agent_window) >= 6:
                return 'DEMO_RATE_LIMIT'
            if engine == 'llm' and self.llm_used >= 10:
                return 'DEMO_LLM_BUDGET_EXHAUSTED'
            if engine == 'llm':
                # Reserve before forwarding, including failed requests. Restarts do not reset it.
                if not self.ledger:
                    return 'DEMO_LEDGER_REQUIRED'
                value = self.llm_used + 1
                temp = self.ledger.with_suffix('.tmp')
                temp.write_text(json.dumps({'llm_questions_reserved': value}), encoding='utf-8')
                temp.replace(self.ledger)
                self.llm_used = value
            self.agent_window.append(now)
        return None

    def authenticate(self, token):
        with self.lock:
            now = time.monotonic()
            while self.login_window and now - self.login_window[0] >= 60:
                self.login_window.popleft()
            if len(self.login_window) >= 10:
                return 429, None
            self.login_window.append(now)
            if not isinstance(token, str) or not hmac.compare_digest(token, self.link_secret):
                return 403, None
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            if len(self.sessions) >= 50:
                return 429, None
            session = secrets.token_urlsafe(32)
            self.sessions[session] = now + 8 * 3600
            return 200, session

    def authenticated(self, cookie):
        if self.access_mode == 'cloudflare_email':
            return True  # cloudflared validates the email session before this origin.
        try:
            cookies = SimpleCookie()
            cookies.load(cookie or '')
            item = cookies.get(COOKIE)
            if not item:
                return False
            with self.lock:
                return self.sessions.get(item.value, 0) > time.monotonic()
        except (ValueError, TypeError, CookieError):
            return False


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(10)
        return sock, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(.1)
                request.recv(16384)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 5\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        pass  # No traceback, private path, query, question, cookie or token in logs.


def make_gateway(policy, port=8767):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'TeamDemo'
        sys_version = ''

        def log_message(self, *args):
            pass

        def handle_one_request(self):
            self.request_started = time.monotonic()
            super().handle_one_request()

        def respond(self, status, body, mime='application/json; charset=utf-8', session=None):
            policy.record(self.command, self.path, status, len(body),
                          time.monotonic() - self.request_started)
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
            self.send_header('Connection', 'close')
            if session:
                self.send_header('Set-Cookie', f'{COOKIE}={session}; Path=/; Max-Age=28800; Secure; HttpOnly; SameSite=Lax')
            if status in (429, 503, 504):
                self.send_header('Retry-After', '10')
            self.end_headers()
            self.wfile.write(body)

        def error(self, status, code):
            self.respond(status, json.dumps({'error': {'code': code}}, ensure_ascii=False).encode())

        def boundary(self, post=False):
            if not policy.host:
                self.error(503, 'DEMO_CLOSED')
                return False
            if self.headers.get_all('Host', []) != [policy.host]:
                self.error(403, 'HOST_REJECTED')
                return False
            origins = self.headers.get_all('Origin', [])
            if (post and origins != ['https://' + policy.host]) or (
                origins and origins != ['https://' + policy.host]
            ):
                self.error(403, 'ORIGIN_REJECTED')
                return False
            if len(self.path) > 8192 or not self.path.startswith('/') or self.path.startswith('//'):
                self.error(400, 'INVALID_TARGET')
                return False
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                self.error(400, 'INVALID_TARGET')
                return False
            return True

        def forward(self, payload=None):
            # Fixed destination; never use incoming forwarded headers or a supplied URL.
            connection = HTTPConnection('127.0.0.1', policy.upstream_port, timeout=150)
            host = '127.0.0.1:' + str(policy.upstream_port)
            headers = {'Host': host, 'Connection': 'close'}
            if payload is not None:
                # Only after exact external Host/HTTPS Origin validation above.
                headers.update({'Origin': 'http://' + host, 'Content-Type': 'application/json'})
            try:
                connection.request('POST' if payload is not None else 'GET', self.path, payload, headers)
                response = connection.getresponse()
                if response.status not in (200, 400, 403, 404, 429, 503):
                    self.error(502, 'UPSTREAM_RESPONSE_REJECTED')
                    return
                body = response.read(32 * 1024 * 1024 + 1)
                if len(body) > 32 * 1024 * 1024:
                    self.error(502, 'UPSTREAM_RESPONSE_LIMIT')
                    return
                self.respond(response.status, body, response.getheader('Content-Type', 'application/json'))
            except TimeoutError:
                self.error(504, 'UPSTREAM_TIMEOUT')
            except (OSError, ValueError):
                self.error(503, 'UPSTREAM_UNAVAILABLE')
            finally:
                connection.close()

        def do_GET(self):
            if not self.boundary():
                return
            path = urlsplit(self.path).path
            if policy.access_mode == 'secret_link' and not policy.authenticated(self.headers.get('Cookie')):
                if path in ('/', '/index.html', '/login'):
                    self.respond(200, LINK_LOGIN, 'text/html; charset=utf-8')
                else:
                    self.error(401 if path in GET_PATHS else 404, 'AUTH_REQUIRED')
                return
            if policy.mode == 'preview':
                if path in ('/', '/index.html'):
                    self.respond(200, PREVIEW, 'text/html; charset=utf-8')
                else:
                    self.error(403 if path.startswith('/api/') else 404, 'PREVIEW_ONLY')
                return
            if path not in GET_PATHS:
                self.error(404, 'NOT_FOUND')
                return
            self.forward()

        def do_POST(self):
            if not self.boundary(post=True):
                return
            if policy.access_mode == 'secret_link' and self.path == '/auth/link':
                try:
                    if self.headers.get('Transfer-Encoding') or any(len(self.headers.get_all(key, [])) != 1 for key in ('Content-Length', 'Content-Type')):
                        raise ValueError()
                    length = int(self.headers['Content-Length'])
                    if not 0 < length <= 512 or self.headers['Content-Type'] != 'application/json':
                        raise ValueError()
                    raw = self.rfile.read(length).strip()
                    if not raw.startswith(b'{') or not raw.endswith(b'}'):
                        raise ValueError()
                    pairs = json.loads(raw.decode('utf-8'), object_pairs_hook=lambda values: values)
                    if not isinstance(pairs, list) or len(pairs) != 1 or pairs[0][0] != 'token' or not isinstance(pairs[0][1], str) or len(pairs[0][1]) > 100:
                        raise ValueError()
                    status, session = policy.authenticate(pairs[0][1])
                    self.respond(status, b'{"authenticated":true}' if session else b'{"error":{"code":"AUTH_REJECTED"}}', session=session)
                except (ValueError, UnicodeError, TypeError, IndexError):
                    self.error(400, 'INVALID_AUTH_REQUEST')
                return
            if not policy.authenticated(self.headers.get('Cookie')):
                self.error(401, 'AUTH_REQUIRED')
                return
            if policy.mode == 'preview':
                self.error(403, 'PREVIEW_ONLY')
                return
            if self.path != '/api/agent':
                self.error(404, 'NOT_FOUND')
                return
            if self.headers.get('Transfer-Encoding') or any(
                len(self.headers.get_all(key, [])) != 1 for key in ('Content-Length', 'Content-Type')
            ):
                self.error(400, 'INVALID_FRAMING')
                return
            try:
                length = int(self.headers['Content-Length'])
                if not 0 < length <= 12000 or self.headers['Content-Type'].split(';')[0] != 'application/json':
                    raise ValueError()
                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result:
                            raise ValueError()
                        result[key] = value
                    return result
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(payload, dict) or payload.get('engine', 'structured') not in ('structured', 'llm'):
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.error(400, 'INVALID_JSON')
                return
            if not policy.agent_slot.acquire(blocking=False):
                self.error(429, 'DEMO_AGENT_BUSY')
                return
            try:
                reason = policy.reserve(payload.get('engine', 'structured'))
                if reason:
                    self.error(429, reason)
                    return
                self.forward(raw)
            except OSError:
                self.error(503, 'DEMO_LEDGER_UNAVAILABLE')
            finally:
                policy.agent_slot.release()

    return GatewayServer(('127.0.0.1', port), Handler)
