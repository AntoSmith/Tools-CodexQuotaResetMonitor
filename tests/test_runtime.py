import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quota_monitor.cli import single_instance
from quota_monitor.config import ConfigError
from quota_monitor.service import Monitor
from quota_monitor.store import Store
from quota_monitor.xapi import APIError
from test_io import config


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')

    def tearDown(self):
        self.s.close()

    def test_lock_released_and_rejects_second_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.lock'
            with single_instance(path):
                with self.assertRaises(ConfigError):
                    with single_instance(path):
                        pass
            with single_instance(path):
                pass

    def test_failed_backfill_does_not_advance_checkpoint(self):
        self.s.set('checkpoint', 1788820000)
        class Client:
            def backfill(self, start, end):
                yield {'data': {'id': '1'}}
                raise APIError('page 2 failed')
        m = Monitor(config(), self.s, client=Client())
        with patch('quota_monitor.service.time.time', return_value=1788825000):
            with self.assertRaises(APIError):
                m.catchup()
        self.assertEqual(self.s.get('checkpoint'), '1788820000')

    def test_fatal_startup_error_notifies_before_exit(self):
        class Client:
            def ensure_rules(self):
                raise APIError('X API HTTP 401', permanent=True)
        messages = []
        class Notifier:
            def send(self, row):
                messages.append(row)
        m = Monitor(config(), self.s, client=Client(), notifier=Notifier())
        self.assertEqual(m.run(), 1)
        self.assertTrue(any('401' in r['body'] for r in messages))

    def test_stream_buffer_does_not_advance_search_checkpoint(self):
        self.s.set('checkpoint', 100000)
        self.s.set('last_backfill_at', 100000)
        class Client:
            def ensure_rules(self):
                pass
            def stream(inner, stop):
                yield None
                stop.set()
        m = Monitor(config(), self.s, client=Client())
        with patch('quota_monitor.service.time.time', return_value=100600):
            m.collect()
        self.assertEqual(self.s.get('checkpoint'), '100000')

    def test_temporary_rule_failure_retries(self):
        class Client:
            calls = 0
            def ensure_rules(inner):
                inner.calls += 1
                if inner.calls == 1:
                    raise APIError('temporary')
            def stream(inner, stop):
                stop.set()
                yield None
        c = Client()
        m = Monitor(config(), self.s, client=c)
        m.collect()
        self.assertEqual(c.calls, 2)
        self.assertIsNone(m.fatal)


if __name__ == '__main__':
    unittest.main()
