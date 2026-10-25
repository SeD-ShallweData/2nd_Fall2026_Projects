"""Synthetic unit checks only: no real region values, credentials or network."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import socket
import sys
import unittest
from unittest.mock import patch

WORK = Path(__file__).resolve().parents[1] / 'Code/sk_admin_mapping'
sys.path.insert(0, str(WORK))

from llm_config import load_llm_config, read_env_file, ConfigError
from seoul_config import load_seoul_config
from request_limits import RequestBudget
from roa_data import RoaValidationError, above_median, code_text, read_json_strict
from roa_classification import evaluate_relationship, REFERENCE_MONTHS
from welfare_api import parse_response, ApiFailure, SeoulWelfareClient


def synthetic_temp():
    base = WORK / 'outputs/validation/public_tests'
    base.mkdir(parents=True, exist_ok=True)
    return TemporaryDirectory(dir=base)


class PublicSyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fail immediately if a future change accidentally attempts any outbound connection.
        cls.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden in public tests'))
        cls.network.start()

    @classmethod
    def tearDownClass(cls):
        cls.network.stop()

    def test_strict_decimal_boundary(self):
        self.assertFalse(above_median('50.00', '50.00'))
        self.assertTrue(above_median('50.000000000001', '50.00'))
        self.assertFalse(above_median('49.999999999999', '50.00'))

    def test_leading_zero_code_preserved(self):
        self.assertEqual(code_text('00000001', 8), '00000001')
        for bad in (1, '1', '00000001.0'):
            with self.subTest(value=bad), self.assertRaises(RoaValidationError):
                code_text(bad, 8)

    def test_three_months_required(self):
        monthly = {m: {'R': {'standardized_pct': 60, 'legacy_median_21_pct': 50,
                             'interpretable': True, 'value_status': 'OBSERVED'}} for m in REFERENCE_MONTHS}
        self.assertEqual(evaluate_relationship(monthly, 'R')['state'], 'ON')
        equal = deepcopy(monthly)
        equal[REFERENCE_MONTHS[1]]['R']['standardized_pct'] = 50
        self.assertEqual(evaluate_relationship(equal, 'R')['state'], 'OFF')
        missing = deepcopy(monthly)
        missing[REFERENCE_MONTHS[0]]['R']['interpretable'] = False
        self.assertEqual(evaluate_relationship(missing, 'R')['state'], 'UNDETERMINED')

    def test_duplicate_and_nonfinite_json_rejected(self):
        with synthetic_temp() as folder:
            p = Path(folder) / 'synthetic.json'
            for raw in ('{"value":1,"value":2}', '{"value":NaN}'):
                p.write_text(raw, encoding='utf-8')
                with self.assertRaises(RoaValidationError):
                    read_json_strict(p)

    def test_budget_rate_window_and_total_are_distinct(self):
        now = [0.0]
        b = RequestBudget(1, 2, clock=lambda: now[0])
        self.assertIsNone(b.take())
        self.assertEqual(b.take(), 'LLM_RATE_LIMIT')
        now[0] = 60.0
        self.assertIsNone(b.take())
        now[0] = 120.0
        self.assertEqual(b.take(), 'LLM_PROCESS_BUDGET_EXHAUSTED')
        self.assertEqual(b.status()['consumed'], 2)

    def test_no_credentials_means_not_enabled(self):
        with synthetic_temp() as home:
            c = load_llm_config({}, home=home)
            self.assertFalse(c.enabled)
            self.assertFalse(c.api_key)
            self.assertFalse(load_seoul_config({}, home=home).api_key)

    def test_unsafe_llm_endpoint_fails_closed(self):
        with synthetic_temp() as home:
            for endpoint in ('http://api.upstage.ai/v1', 'https://untrusted.invalid/v1'):
                c = load_llm_config({'LLM_BASE_URL': endpoint}, home=home)
                self.assertTrue(c.error)
                self.assertFalse(c.enabled)

    def test_env_parser_rejects_duplicate_assignment(self):
        with synthetic_temp() as folder:
            p = Path(folder) / 'synthetic.env'
            p.write_text('SYNTHETIC=a\nSYNTHETIC=b\n', encoding='utf-8')
            with self.assertRaises(ConfigError):
                read_env_file(p)

    def test_mock_response_and_no_data(self):
        payload = {'syntheticService': {'RESULT': {'CODE': 'INFO-000'}, 'list_total_count': 1,
                                       'row': [{'id': 'SYNTHETIC_ONLY'}]}}
        parsed = parse_response(json.dumps(payload), 'syntheticService')
        self.assertEqual(parsed['total'], 1)
        self.assertEqual(parsed['rows'][0]['id'], 'SYNTHETIC_ONLY')
        self.assertEqual(parse_response('{"RESULT":{"CODE":"INFO-200"}}', 'syntheticService')['rows'], [])

    def test_malformed_api_and_xml_entities_rejected(self):
        for raw in ('{"a":1,"a":2}', '<!DOCTYPE test [<!ENTITY x "x">]><test/>',
                    '{"syntheticService":{"RESULT":{"CODE":"INFO-000"},"list_total_count":true,"row":[]}}'):
            with self.subTest(raw=raw), self.assertRaises(ApiFailure):
                parse_response(raw, 'syntheticService')


if __name__ == '__main__':
    unittest.main()
