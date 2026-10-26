"""Private, transactional authentication, request history and outbound budgets."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

# Match the co끼리 public chat policy; a question can use four provider calls.
QUESTION_PER_MINUTE = 30
LLM_PER_SESSION_HOUR = 30
LLM_PER_SESSION_DAY = 100
LLM_GLOBAL_DAY = 1000
PROVIDER_PER_MINUTE = 120
PROVIDER_PER_DAY = 4000


def digest(value):
    return sha256(value.encode('utf-8')).hexdigest()


class Store:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS reservations (kind TEXT NOT NULL, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS baseline (kind TEXT PRIMARY KEY, used INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, at REAL NOT NULL,
                    payload TEXT NOT NULL, status INTEGER, result TEXT);
            ''')
        # Keep all existing sessions, history and reservations on upgrade.
        with self.transaction() as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(reservations)')}
            if 'session_hash' not in columns:
                db.execute("ALTER TABLE reservations ADD COLUMN session_hash TEXT NOT NULL DEFAULT ''")
            db.execute('CREATE INDEX IF NOT EXISTS reservation_window ON reservations(kind, at)')
            db.execute('CREATE INDEX IF NOT EXISTS reservation_session_window ON reservations(kind, session_hash, at)')
        if os.name != 'nt':
            self.path.parent.chmod(0o700)
            self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=5000')
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def seed(self, kind, used):
        if kind not in ('llm_question', 'provider') or type(used) is not int or used < 0:
            raise ValueError('INVALID_BASELINE')
        with self.transaction() as db:
            old = db.execute('SELECT used FROM baseline WHERE kind=?', (kind,)).fetchone()
            if old is not None and old[0] != used:
                raise ValueError('BASELINE_CANNOT_BE_RESET')
            db.execute('INSERT OR IGNORE INTO baseline VALUES (?,?)', (kind, used))

    @staticmethod
    def used(db, kind):
        return (db.execute('SELECT COUNT(*) FROM reservations WHERE kind=?', (kind,)).fetchone()[0]
                + (db.execute('SELECT used FROM baseline WHERE kind=?', (kind,)).fetchone() or (0,))[0])

    @staticmethod
    def recent(db, kind, since, session_hash=None):
        sql = 'SELECT COUNT(*) FROM reservations WHERE kind=? AND at>?'
        params = [kind, since]
        if session_hash is not None:
            sql += ' AND session_hash=?'
            params.append(session_hash)
        return db.execute(sql, params).fetchone()[0]

    def reserve(self, engine, session=''):
        if engine not in ('structured', 'llm'):
            raise ValueError('INVALID_ENGINE')
        now = self.clock()
        with self.transaction() as db:
            if self.recent(db, 'question', now-60) >= QUESTION_PER_MINUTE:
                return 'DEMO_RATE_LIMIT'
            if engine == 'llm':
                marker = digest(session)
                if self.recent(db, 'llm_question', now-3600, marker) >= LLM_PER_SESSION_HOUR:
                    return 'DEMO_LLM_HOURLY_LIMIT'
                if self.recent(db, 'llm_question', now-86400, marker) >= LLM_PER_SESSION_DAY:
                    return 'DEMO_LLM_DAILY_LIMIT'
                if self.recent(db, 'llm_question', now-86400) >= LLM_GLOBAL_DAY:
                    return 'DEMO_LLM_GLOBAL_DAILY_LIMIT'
                db.execute('INSERT INTO reservations(kind,at,session_hash) VALUES (?,?,?)', ('llm_question', now, marker))
            db.execute('INSERT INTO reservations(kind,at) VALUES (?,?)', ('question', now))

    def provider_take(self):
        now = self.clock()
        with self.transaction() as db:
            if self.recent(db, 'provider', now-86400) >= PROVIDER_PER_DAY:
                return 'LLM_DAILY_LIMIT'
            if self.recent(db, 'provider', now-60) >= PROVIDER_PER_MINUTE:
                return 'LLM_RATE_LIMIT'
            db.execute('INSERT INTO reservations(kind,at) VALUES (?,?)', ('provider', now))

    def provider_status(self):
        with self.connection() as db:
            return {'max_per_minute': PROVIDER_PER_MINUTE, 'max_per_process': None,
                    'max_per_day': PROVIDER_PER_DAY,
                    'consumed_in_day': self.recent(db, 'provider', self.clock()-86400),
                    'consumed': self.used(db, 'provider'),
                    'question_limits': {'per_minute_global': QUESTION_PER_MINUTE,
                                        'per_session_hour': LLM_PER_SESSION_HOUR,
                                        'per_session_day': LLM_PER_SESSION_DAY,
                                        'global_day': LLM_GLOBAL_DAY},
                    'scope': 'PERSISTENT_ROLLING_WINDOWS_NO_LIFETIME_CAP'}

    def login(self, token, expected):
        now = self.clock()
        with self.transaction() as db:
            if db.execute("SELECT COUNT(*) FROM reservations WHERE kind='login' AND at>?", (now-60,)).fetchone()[0] >= 10:
                return 429, None
            db.execute('INSERT INTO reservations(kind,at) VALUES (?,?)', ('login', now))
            if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token) or not hmac.compare_digest(token, expected):
                return 403, None
            db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
            if db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] >= 50:
                return 429, None
            session = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES (?,?)', (digest(session), now+28800))
            return 200, session

    def session_valid(self, session):
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', session):
            return False
        with self.connection() as db:
            return db.execute('SELECT 1 FROM sessions WHERE hash=? AND expires>?', (digest(session), self.clock())).fetchone() is not None

    def begin_request(self, session, payload, identifier=None):
        identifier = identifier or secrets.token_hex(16)
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', identifier):
            raise ValueError('INVALID_REQUEST_ID')
        with self.transaction() as db:
            try:
                db.execute('INSERT INTO requests VALUES (?,?,?,?,NULL,NULL)',
                           (identifier, digest(session), self.clock(), json.dumps(payload, ensure_ascii=False)))
            except sqlite3.IntegrityError:
                raise ValueError('DUPLICATE_REQUEST_NOT_REPLAYED') from None
        return identifier

    def finish_request(self, identifier, status, body):
        result = body.decode('utf-8', errors='replace') if len(body) <= 1024*1024 else '{"stored":"response size limit"}'
        with self.transaction() as db:
            db.execute('UPDATE requests SET status=?,result=? WHERE id=? AND status IS NULL', (status, result, identifier))

    def history(self, session):
        # An unfinished request remains unknown after a crash and is never automatically replayed.
        with self.connection() as db:
            return db.execute('SELECT id,payload,status,result FROM requests WHERE session_hash=? ORDER BY at', (digest(session),)).fetchall()


class ProviderBudget:
    def __init__(self, store):
        self.store = store

    def take(self):
        return self.store.provider_take()

    def status(self):
        return self.store.provider_status()


def load_secret(path):
    """Create once, with no overwrite; normal starts only read the same credential."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(secrets.token_urlsafe(32) + '\n')
    except FileExistsError:
        pass
    value = path.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
        raise ValueError('INVALID_PERSISTENT_ACCESS_SECRET')
    if os.name != 'nt':
        path.chmod(0o600)
    return value
