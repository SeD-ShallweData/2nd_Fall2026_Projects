"""Own and stop exactly one protected Quick Tunnel and its loopback gateway."""
from pathlib import Path
from http.client import HTTPConnection
import argparse
import datetime
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time

from demo_gateway import Policy, ROOT, make_gateway

OUT = ROOT / 'Code/sk_admin_mapping/outputs/validation/step4a'
STATE = OUT / 'demo_runtime.local.json'
STOP = OUT / 'stop.request'


def save(value):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(STATE)


def config(path):
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    access_mode = value.get('access_mode', 'cloudflare_email')
    emails = value.get('allowed_emails', [])
    if not isinstance(emails, list) or (access_mode == 'cloudflare_email' and not emails) or len(emails) != len(set(emails)) or any(
        not isinstance(email, str) or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', email)
        or '*' in email or ',' in email for email in emails
    ):
        raise ValueError('EXACT_EMAIL_ALLOWLIST_REQUIRED')
    team_test = value.get('team_test_authorization', {})
    scope = 'SECRET_LINK_TEST' if access_mode == 'secret_link' else 'EMAIL_RESTRICTED_TEAM_TEST'
    authorized_test = (team_test.get('approved_by_user') is True
                       and team_test.get('scope') == scope
                       and isinstance(team_test.get('instruction'), str) and bool(team_test['instruction'].strip())
                       and (access_mode == 'secret_link' or team_test.get('allowed_emails') == emails)
                       and team_test.get('relay') == 'Cloudflare Quick Tunnel'
                       and team_test.get('provider') == 'https://api.upstage.ai')
    if value.get('mode', 'preview') == 'dashboard' and not (value.get('third_party_content_reviewed') or authorized_test):
        raise ValueError('THIRD_PARTY_CONTENT_REVIEW_REQUIRED')
    return value


def probe(port, host=None, path='/api/agent/status'):
    connection = HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', path,
                           headers={'Host': host} if host else {})
        response = connection.getresponse()
        response.read()
        return response.status == 200
    except OSError:
        return False
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('demo.local.json'))
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.status:
        print(STATE.read_text(encoding='utf-8') if STATE.exists() else '{"status":"NOT_STARTED"}')
        return
    if args.stop:
        STOP.write_text('stop', encoding='ascii')
        print('Stop requested; wait for runtime status STOPPED.')
        return
    settings = config(args.config)
    link_secret = secrets.token_urlsafe(32) if settings.get('access_mode') == 'secret_link' else None
    policy = Policy(mode=settings.get('mode', 'preview'), rights=settings.get('data_rights'),
                    test_authorization=settings.get('team_test_authorization'),
                    ledger=OUT / 'llm_budget.local.json', events=OUT / 'gateway_events.jsonl',
                    access_mode=settings.get('access_mode', 'cloudflare_email'), link_secret=link_secret)
    cloudflared = OUT / 'cloudflared.exe'
    if not cloudflared.is_file():
        raise ValueError('VERIFIED_CLOUDFLARED_REQUIRED')
    version = subprocess.check_output([str(cloudflared), '--version'], text=True).strip()
    help_text = subprocess.check_output([str(cloudflared), 'tunnel', '--help'], text=True)
    if '--allowed-mail' not in help_text:
        raise ValueError('EMAIL_AUTH_OPTION_UNAVAILABLE')
    if args.check:
        print(json.dumps({'mode': policy.mode, 'allowed_email_count': len(settings['allowed_emails']) if policy.access_mode == 'cloudflare_email' else 0,
                          'version': version, 'data_connected': policy.mode == 'dashboard',
                          'dashboard_rights_confirmed': bool(settings.get('data_rights', {}).get('confirmed')),
                          'access_basis': policy.access_basis,
                          'access_mode': policy.access_mode,
                          'llm_question_reservations_used': policy.llm_used,
                          'llm_question_reservations_max': 10, 'gateway_bind': '127.0.0.1:8767'}, indent=2))
        return
    if STATE.exists() and json.loads(STATE.read_text(encoding='utf-8')).get('status') == 'RUNNING':
        raise ValueError('EXISTING_DEMO_STATE_REQUIRES_STOP_OR_INSPECTION')
    # An exclusive local lock prevents two runners and budget ledgers racing.
    lock_path = OUT / 'runner.lock'
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(lock_fd)
    children, logs, gateway = [], [], None
    runtime = {'status': 'STARTING', 'runner_pid': os.getpid(), 'mode': policy.mode,
               'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'allowed_email_count': len(settings['allowed_emails']) if policy.access_mode == 'cloudflare_email' else 0, 'data_connected': False,
               'version': version, 'external_authenticated_functional_verification': False}
    runtime.update(access_basis=policy.access_basis,
                   access_mode=policy.access_mode,
                   data_rights_confirmed=bool(settings.get('data_rights', {}).get('confirmed')),
                   third_party_content_reviewed=bool(settings.get('third_party_content_reviewed')))
    save(runtime)
    try:
        STOP.unlink(missing_ok=True)
        gateway = make_gateway(policy)
        threading.Thread(target=gateway.serve_forever, daemon=True).start()
        if policy.mode == 'dashboard':
            import socket
            with socket.socket() as test_socket:
                already_running = test_socket.connect_ex(('127.0.0.1', 8766)) == 0
            if already_running:
                if settings.get('reuse_inspected_backend') is not True:
                    raise ValueError('EXISTING_BACKEND_MUST_BE_INSPECTED_NOT_REUSED')
                # Match an exact local asset before reusing the inspected project server.
                connection = HTTPConnection('127.0.0.1', 8766, timeout=5)
                try:
                    connection.request('GET', '/dashboard.js')
                    response = connection.getresponse()
                    matches = response.status == 200 and response.read() == (ROOT / 'Code/sk_admin_mapping/dashboard.js').read_bytes()
                finally:
                    connection.close()
                if not matches or not probe(8766):
                    raise ValueError('INSPECTED_BACKEND_FINGERPRINT_MISMATCH')
                runtime.update(backend_owned=False, backend_port=8766,
                               backend_budget_scope='EXISTING_PROCESS; gateway question budget also applies')
            work = ROOT / 'Code/sk_admin_mapping'
            if not already_running:
                log = open(OUT / 'backend.log', 'ab', buffering=0)
                logs.append(log)
                environment = dict(os.environ, PYTHONIOENCODING='utf-8',
                                   LLM_MAX_REQUESTS_PER_MINUTE='6', LLM_MAX_REQUESTS_PER_PROCESS='30',
                                   LLM_TIMEOUT_SECONDS='20')
                backend = subprocess.Popen([str(work / '.venv/Scripts/python.exe'), '-B',
                                            str(work / 'run_dashboard.py'), '--port', '8766'],
                                           cwd=ROOT, env=environment, stdout=log, stderr=log)
                children.append(backend)
                deadline = time.monotonic() + 120
                while not probe(8766):
                    if backend.poll() is not None or time.monotonic() > deadline or STOP.exists():
                        raise RuntimeError('BACKEND_NOT_READY')
                    time.sleep(1)
                runtime.update(backend_pid=backend.pid, backend_owned=True, backend_port=8766)
            # Parse the large completed cache once before admitting concurrent browser traffic.
            # This performs no API refresh and sends no LLM request.
            warm_start = time.monotonic()
            connection = HTTPConnection('127.0.0.1', 8766, timeout=120)
            try:
                connection.request('GET', '/api/service/status')
                response = connection.getresponse()
                body = response.read()
                if response.status != 200 or not isinstance(json.loads(body), dict):
                    raise RuntimeError('BACKEND_CACHE_WARMUP_FAILED')
            finally:
                connection.close()
            runtime.update(cache_warmup_seconds=round(time.monotonic() - warm_start, 3))
            save(runtime)
        log = open(OUT / 'tunnel.log', 'a', encoding='utf-8', buffering=1)
        logs.append(log)
        # Do not inherit cloudflared tunnel/config/ingress environment overrides.
        clean_env = {k: v for k, v in os.environ.items()
                     if not k.upper().startswith(('TUNNEL_', 'CLOUDFLARED_'))}
        isolated_config = OUT / 'cloudflared_config.local.yml'
        isolated_config.write_text('{}\n', encoding='utf-8')
        command = [str(cloudflared), '--no-autoupdate', 'tunnel', '--config', str(isolated_config),
                   '--url', 'http://127.0.0.1:8767', '--metrics', '127.0.0.1:20245',
                   '--loglevel', 'info', '--management-diagnostics=false']
        if policy.access_mode == 'cloudflare_email':
            for email in settings['allowed_emails']:
                command.extend(['--allowed-mail', email])
        tunnel = subprocess.Popen(command, cwd=OUT, env=clean_env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        children.append(tunnel)
        events = queue.Queue()
        def read_output():
            for line in tunnel.stdout:
                log.write(line)
                events.put(line)
        threading.Thread(target=read_output, daemon=True).start()
        url, registered = None, False
        deadline = time.monotonic() + 120
        while not (url and registered):
            if tunnel.poll() is not None or time.monotonic() > deadline or STOP.exists():
                raise RuntimeError('TUNNEL_NOT_READY_CHECK_PRIVATE_LOG')
            try:
                line = events.get(timeout=.5)
            except queue.Empty:
                continue
            match = re.search(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com', line)
            if match:
                url = match[0]
            registered |= 'Registered tunnel connection' in line
        policy.open_for(url)
        if link_secret:
            # A capability secret, distinct from provider keys. Never print it in logs/state.
            access_file = OUT / 'access_link.local.txt'
            access_file.write_text(url + '/#access=' + link_secret + '\n', encoding='utf-8')
        runtime.update(status='RUNNING', url=url, tunnel_pid=tunnel.pid,
                       data_connected=policy.mode == 'dashboard', gateway_bind='127.0.0.1:8767',
                       cloudflare_email_auth_requested=policy.access_mode == 'cloudflare_email',
                       access_link_file='access_link.local.txt' if link_secret else None)
        save(runtime)
        print(json.dumps(runtime, ensure_ascii=False), flush=True)
        next_check, failures = 0, 0
        while not STOP.exists():
            exited = [{'pid': child.pid, 'exit_code': child.poll()} for child in children if child.poll() is not None]
            if exited:
                runtime['exited_children'] = exited
                raise RuntimeError('OWNED_PROCESS_EXITED')
            if time.monotonic() >= next_check:
                gateway_ok = probe(8767, policy.host, '/' if policy.mode == 'preview' or policy.access_mode == 'secret_link' else '/api/agent/status')
                backend_ok = probe(8766) if policy.mode == 'dashboard' else True
                healthy = gateway_ok and backend_ok
                failures = 0 if healthy else failures + 1
                runtime.update(last_health_check_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                               local_health='RESPONDING' if healthy else 'UNRESPONSIVE',
                               gateway_responding=gateway_ok, backend_responding=backend_ok,
                               consecutive_health_failures=failures)
                verified_file = OUT / 'dashboard_browser_evidence.json'
                if verified_file.exists():
                    try:
                        verified = json.loads(verified_file.read_text(encoding='utf-8'))
                        runtime['external_authenticated_functional_verification'] = verified.get('url') == url and verified.get('passed') is True
                    except (ValueError, OSError):
                        pass
                save(runtime)
                if failures >= 3:
                    raise RuntimeError('LOCAL_SERVICE_UNRESPONSIVE_TUNNEL_CLOSED')
                next_check = time.monotonic() + 15
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        runtime['failure'] = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        raise
    finally:
        policy.host = None
        # Terminate only subprocesses this runner created, never an existing local service.
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=8)
        if gateway:
            gateway.shutdown()
            gateway.server_close()
        for stream in logs:
            stream.close()
        runtime.update(status='STOPPED', data_connected=False,
                       stopped_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        save(runtime)
        lock_path.unlink(missing_ok=True)
        STOP.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
