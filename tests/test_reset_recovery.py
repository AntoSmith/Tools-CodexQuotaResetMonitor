import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quota_monitor.classify import classify, Decision
from quota_monitor.config import Config
from quota_monitor.service import process_post, parse_time
from quota_monitor.store import Store


class ResetRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.config = Config({'accounts': [{'username': 'demo', 'id': '123'}],
                              'monitor': {'max_age_hours': 24},
                              'notifications': {'channel': 'console'}})
        self.now = parse_time('2026-09-12T13:20:00Z').timestamp()
        self.post = {'data': {'id': '2098685367058612394', 'author_id': '123',
                             'created_at': '2026-09-12T08:09:00Z',
                             'text': 'Reset all propagated. Sweet dreams.'}}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_omitted_product_reset_announcements_are_not_silently_ignored(self):
        for text in [self.post['data']['text'],
                     'Hi Astra users. A reset and a quick update on quality issues that have been posted around. '
                     'An experiment could cause early stops. And of course, a reset is also landing by midnight today.',
                     'A reset is rolling out.', '重置已经完成，晚安。']:
            with self.subTest(text=text):
                decision = classify(text)
                self.assertEqual(decision.kind, 'uncertain')
                self.assertIsNone(decision.reset_at)

    def test_unrelated_resets_stay_ignored(self):
        for text in ['We will reset the password.', 'Reset your device.',
                     'Codex: reset your password.', 'Reset the cache.', '请重置密码。']:
            with self.subTest(text=text):
                self.assertEqual(classify(text).kind, 'ignore')

    def test_troubleshooting_words_do_not_hide_a_reset_announcement(self):
        for text in ['The Codex reset has propagated; restart your device if needed.',
                     'We have reset Codex for everyone; refresh your browser cache.',
                     'Reset all propagated; reset your device if needed.',
                     '重置已完成；如果看不到，请重置设备。']:
            with self.subTest(text=text):
                self.assertEqual(classify(text).kind, 'uncertain')

    def seed_old_ignore(self, payload=None):
        with patch('quota_monitor.service.classify', return_value=Decision('ignore', 'old rule')):
            self.assertIsNone(process_post(payload or self.post, self.config, self.store, self.now))

    def test_previously_ignored_post_promotes_once_without_new_authored_version(self):
        self.seed_old_ignore()
        alert = process_post(self.post, self.config, self.store, self.now)
        self.assertIsNotNone(alert)
        self.assertEqual(alert['kind'], 'uncertain')
        self.assertEqual(self.store.status()['posts'], 1)
        self.assertEqual(len(self.store.due(self.now)), 1)
        self.assertIsNone(process_post(self.post, self.config, self.store, self.now))
        row = self.store.due(self.now)[0]
        self.store.sent(row['id'])
        self.assertIsNone(process_post(self.post, self.config, self.store, self.now))
        self.assertEqual(self.store.status()['outbox'], {'sent': 1})

    def test_expired_ignored_post_does_not_realert(self):
        self.seed_old_ignore()
        self.assertIsNone(process_post(self.post, self.config, self.store, self.now + 86400))
        self.assertEqual(self.store.status()['outbox'], {})

    def test_promotion_rolls_back_when_enqueue_fails(self):
        self.seed_old_ignore()
        with patch.object(self.store, 'enqueue', side_effect=RuntimeError('offline test')):
            with self.assertRaises(RuntimeError):
                process_post(self.post, self.config, self.store, self.now)
        self.assertEqual(self.store.db.execute('select kind from posts').fetchone()[0], 'ignore')
        self.assertIsNotNone(process_post(self.post, self.config, self.store, self.now))

    def test_recovery_scans_stored_posts_even_when_not_in_latest_page(self):
        from quota_monitor.service import recover_ignored_posts
        self.seed_old_ignore()
        alerts = list(recover_ignored_posts(self.config, self.store, self.now))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(list(recover_ignored_posts(self.config, self.store, self.now)), [])

    def test_recovery_uses_only_latest_observed_edit(self):
        from quota_monitor.service import recover_ignored_posts
        self.seed_old_ignore()
        edited = json.loads(json.dumps(self.post))
        edited['data']['text'] = 'Reset the password.'
        self.seed_old_ignore(edited)
        self.assertEqual(list(recover_ignored_posts(self.config, self.store, self.now)), [])


if __name__ == '__main__':
    unittest.main()
