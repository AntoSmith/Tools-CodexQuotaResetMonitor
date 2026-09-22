import tempfile
import unittest
from pathlib import Path

from quota_monitor.classify import classify
from quota_monitor.store import Store, BudgetExceeded


class ClassifierTests(unittest.TestCase):
    def test_events(self):
        cases = [
            ('Codex: We will do a global reset of the usage for all paid subscriptions soon.', 'upcoming'),
            ('We have reset Codex usage for everyone.', 'completed'),
            ('Codex usage has been reset.', 'completed'),
            ('Codex users get a reset credit to redeem manually.', 'credit'),
            ('Codex usage limits are doubled.', 'limits'),
            ('We will not reset Codex usage.', 'uncertain'),
            ('Will you reset Codex usage?', 'uncertain'),
            ('Maybe we will reset Codex usage.', 'uncertain'),
            ('If needed we will reset Codex usage.', 'uncertain'),
            ('We will reset the password.', 'ignore'),
            ('We will reset all usage soon.', 'uncertain'),
            ('Codex resets every five hours.', 'routine'),
            ('Codex global usage reset is postponed.', 'correction'),
            ('Yesterday we said we will reset Codex usage.', 'uncertain'),
        ]
        for text, kind in cases:
            with self.subTest(text=text):
                self.assertEqual(classify(text).kind, kind)

    def test_explicit_time_only(self):
        self.assertEqual(classify('We will reset Codex usage at 2026-09-09T12:00:00Z.').reset_at,
                         '2026-09-09T12:00:00+00:00')
        self.assertIsNone(classify('We will reset Codex usage soon at 2 PM PST.').reset_at)

    def test_context_never_promotes_ambiguous_author_text(self):
        self.assertEqual(classify('We will reset usage soon.', context='Codex announcement').kind, 'uncertain')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.db'
        self.s = Store(self.path)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_outbox_survives_restart_and_deduplicates(self):
        self.s.enqueue('post:1', 'title', 'body', now=100)
        self.s.enqueue('post:1', 'title', 'body', now=101)
        self.s.close()
        self.s = Store(self.path)
        rows = self.s.due(102)
        self.assertEqual(len(rows), 1)
        self.s.sent(rows[0]['id'])
        self.assertEqual(self.s.due(1000), [])

    def test_retry_is_durable(self):
        self.s.enqueue('a', 'title', 'body', now=100)
        row = self.s.due(100)[0]
        self.s.failed(row['id'], 100)
        self.assertEqual(self.s.due(101), [])
        self.assertEqual(len(self.s.due(10000)), 1)

    def test_budget_is_reserved_before_io_and_persistent(self):
        self.s.reserve(7, limit=10, month='2026-09')
        with self.assertRaises(BudgetExceeded):
            self.s.reserve(4, limit=10, month='2026-09')
        self.s.close()
        self.s = Store(self.path)
        self.assertEqual(self.s.usage('2026-09'), 7)
        self.s.reserve(1, limit=10, month='2026-10')
        self.assertEqual(self.s.usage('2026-10'), 1)

    def test_cancel_scheduled_reminder(self):
        self.s.enqueue('remind:1', 'title', 'body', now=100, due=1000, group='conversation:1')
        self.s.cancel_reminders('conversation:1')
        self.assertEqual(self.s.due(2000), [])

    def test_versions_are_processed_once(self):
        self.assertTrue(self.s.record_post('1', 'v1', 'upcoming', 'raw'))
        self.assertFalse(self.s.record_post('1', 'v1', 'upcoming', 'raw'))
        self.assertTrue(self.s.record_post('1', 'v2', 'correction', 'changed'))


if __name__ == '__main__':
    unittest.main()
