import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quota_monitor.config import Config, ConfigError
from quota_monitor.email_test import send_latest


class EmailEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = Config({'monitor': {'database': 'state.db'},
                         'accounts': [{'username': 'thsottiaux', 'id': '123', 'kind': 'personal', 'verified': True}],
                         'notifications': {'channel': 'console', 'username': 'sender@example.com',
                                           'to': 'receiver@example.com', 'QQ_SMTP_AUTH_CODE': 'fake-test-secret'}}, self.tmp.name)
        self.post = {'id': '2097193293532848288', 'username': 'thsottiaux', 'created_at': '2026-09-08T05:20:00Z',
                     'text': 'Codex is helping prepare a slide deck.', 'complete': True}

    def test_ignored_post_sent_once_without_changing_monitor_config(self):
        before = copy.deepcopy(self.c.data)
        collector = MagicMock(return_value={'posts': [self.post], 'accounts': []})
        notifier = MagicMock()
        result = send_latest(self.c, collector=collector, notifier=notifier)
        self.assertEqual(result['status'], 'smtp_accepted')
        notifier.send.assert_called_once()
        mail = notifier.send.call_args.args[0]
        self.assertIn('测试', mail['title'])
        self.assertIn(self.post['text'], mail['body'])
        self.assertEqual(self.c.data, before)
        self.assertTrue(collector.call_args.kwargs['profile_only'])
        self.assertEqual(collector.call_args.args[0].section('browser')['max_posts'], 1)
        self.assertNotIn('fake-test-secret', Path(result['report_path']).read_text(encoding='utf-8'))

    def test_preview_does_not_need_auth_or_send(self):
        self.c.data['notifications']['QQ_SMTP_AUTH_CODE'] = ''
        notifier = MagicMock()
        with patch.dict('os.environ', {}, clear=True):
            result = send_latest(self.c, preview=True, collector=lambda *a, **k: {'posts': [self.post]}, notifier=notifier)
        self.assertEqual(result['status'], 'preview')
        notifier.send.assert_not_called()

    def test_unavailable_or_incomplete_post_never_sends(self):
        for posts in ([], [dict(self.post, complete=False)], [dict(self.post, username='other')]):
            notifier = MagicMock()
            with self.assertRaises(ConfigError):
                send_latest(self.c, collector=lambda *a, **k: {'posts': posts}, notifier=notifier)
            notifier.send.assert_not_called()

    def test_failed_delivery_is_not_reported_as_success_or_retried(self):
        notifier = MagicMock()
        notifier.send.side_effect = OSError('private server message')
        result = send_latest(self.c, collector=lambda *a, **k: {'posts': [self.post]}, notifier=notifier)
        self.assertEqual(result['status'], 'failed_or_uncertain')
        notifier.send.assert_called_once()
        self.assertNotIn('private server message', str(result))
