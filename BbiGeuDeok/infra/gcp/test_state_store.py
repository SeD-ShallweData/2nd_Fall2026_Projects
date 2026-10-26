"""Synthetic persistence and contention tests; no keys, datasets or external calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from state_store import Store, ProviderBudget, load_secret, digest
import sqlite3


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.now = 1000.0
        self.store = Store(self.root / 'state.sqlite', clock=lambda: self.now)

    def test_secret_created_once_and_invalid_file_not_overwritten(self):
        path = self.root / 'access.secret'
        value = load_secret(path)
        self.assertEqual(len(value), 43)
        self.assertEqual(load_secret(path), value)
        path.write_text('invalid', encoding='ascii')
        with self.assertRaises(ValueError):
            load_secret(path)
        self.assertEqual(path.read_text(), 'invalid')

    def test_sessions_survive_new_store_and_expire(self):
        secret = 'A' * 43
        self.assertEqual(self.store.login('B'*43, secret)[0], 403)
        status, session = self.store.login(secret, secret)
        self.assertEqual(status, 200)
        recreated = Store(self.store.path, clock=lambda: self.now)
        self.assertTrue(recreated.session_valid(session))
        self.assertFalse(recreated.session_valid('forged'))
        self.assertNotIn(session.encode(), self.store.path.read_bytes())
        self.now += 28801
        self.assertFalse(recreated.session_valid(session))

    def fill(self, kind, count, session='', at=None):
        with self.store.transaction() as db:
            db.executemany('INSERT INTO reservations(kind,at,session_hash) VALUES (?,?,?)',
                           [(kind, self.now if at is None else at, digest(session))] * count)

    def test_old_lifetime_caps_do_not_block_and_baseline_is_preserved(self):
        self.store.seed('llm_question', 10)
        self.store.seed('provider', 30)
        self.assertIsNone(self.store.reserve('llm', 'one'))
        self.assertIsNone(ProviderBudget(self.store).take())
        recreated = Store(self.store.path, clock=lambda: self.now)
        self.assertEqual(recreated.provider_status()['consumed'], 31)
        self.assertEqual(recreated.provider_status()['consumed_in_day'], 1)
        self.assertIsNone(recreated.provider_status()['max_per_process'])
        with self.assertRaises(ValueError):recreated.seed('provider', 0)

    def test_hourly_limit_is_per_session_and_recovers_without_restart(self):
        self.fill('llm_question', 30, 'one')
        recreated = Store(self.store.path, clock=lambda: self.now)
        self.assertEqual(recreated.reserve('llm', 'one'), 'DEMO_LLM_HOURLY_LIMIT')
        self.assertIsNone(recreated.reserve('llm', 'two'))
        self.assertIsNone(recreated.reserve('structured', 'one'))
        self.now += 3600
        self.assertIsNone(recreated.reserve('llm', 'one'))

    def test_session_daily_and_global_daily_limits_recover(self):
        self.fill('llm_question', 100, 'one', at=self.now-3600)
        self.assertEqual(self.store.reserve('llm', 'one'), 'DEMO_LLM_DAILY_LIMIT')
        self.assertIsNone(self.store.reserve('llm', 'two'))
        self.fill('llm_question', 899, 'others', at=self.now-3600)
        self.assertEqual(self.store.reserve('llm', 'three'), 'DEMO_LLM_GLOBAL_DAILY_LIMIT')
        self.now += 86400
        self.assertIsNone(self.store.reserve('llm', 'one'))

    def test_failed_provider_reservations_stay_spent_until_daily_window_expires(self):
        self.fill('provider', 3999, at=self.now-60)
        self.assertIsNone(ProviderBudget(self.store).take())
        recreated = Store(self.store.path, clock=lambda: self.now)
        self.assertEqual(ProviderBudget(recreated).take(), 'LLM_DAILY_LIMIT')
        self.assertEqual(recreated.provider_status()['consumed_in_day'], 4000)
        self.now += 86400
        self.assertIsNone(recreated.provider_take())
        self.assertEqual(recreated.provider_status()['consumed_in_day'], 1)

    def test_provider_and_question_per_minute_limits_survive_restart(self):
        for _ in range(30):
            self.assertIsNone(self.store.reserve('structured'))
        for _ in range(120):
            self.assertIsNone(self.store.provider_take())
        recreated = Store(self.store.path, clock=lambda: self.now)
        self.assertEqual(recreated.provider_take(), 'LLM_RATE_LIMIT')
        self.assertEqual(recreated.reserve('structured'), 'DEMO_RATE_LIMIT')
        self.now += 60
        self.assertIsNone(recreated.provider_take())
        self.assertIsNone(recreated.reserve('structured'))

    def test_parallel_workers_cannot_exceed_global_provider_daily_limit(self):
        self.fill('provider', 3998, at=self.now-60)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.provider_take(), range(8)))
        self.assertEqual(results.count(None), 2)
        self.assertEqual(self.store.provider_status()['consumed_in_day'], 4000)

    def test_legacy_database_migrates_without_losing_reservations(self):
        path = self.root / 'legacy.sqlite'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE reservations(kind TEXT NOT NULL, at REAL NOT NULL)')
            db.execute('INSERT INTO reservations VALUES (?,?)', ('provider', self.now))
            db.commit()
        upgraded = Store(path, clock=lambda: self.now)
        self.assertEqual(upgraded.provider_status()['consumed'], 1)
        self.assertIsNone(upgraded.provider_take())
        self.assertIsNone(upgraded.reserve('llm', 'one'))
        self.assertEqual(Store(path, clock=lambda: self.now).provider_status()['consumed_in_day'], 2)

    def test_history_is_private_persistent_and_unknown_requests_are_not_replayed(self):
        identifier = 'a'*32
        self.store.begin_request('session-one', {'question':'SYNTHETIC QUESTION'}, identifier)
        recreated = Store(self.store.path, clock=lambda: self.now)
        self.assertEqual(len(recreated.history('session-one')), 1)
        self.assertEqual(recreated.history('session-two'), [])
        self.assertIsNone(recreated.history('session-one')[0][2])
        with self.assertRaises(ValueError):
            recreated.begin_request('session-one', {'question':'same'}, identifier)
        recreated.finish_request(identifier, 200, b'{"synthetic":true}')
        self.assertEqual(self.store.history('session-one')[0][2], 200)

    def test_login_guess_window_persists(self):
        for _ in range(10):
            self.assertEqual(self.store.login('wrong','A'*43)[0],403)
        recreated = Store(self.store.path, clock=lambda: self.now)
        self.assertEqual(recreated.login('A'*43,'A'*43)[0],429)


if __name__ == '__main__':
    unittest.main(verbosity=2)
