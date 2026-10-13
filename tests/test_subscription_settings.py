import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from quota_monitor.config import Config, ConfigError
from quota_monitor.desktop_settings import read_settings, save_settings, settings_path


class SubscriptionSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'config.browser.local.toml'
        self.path.write_text('[notifications]\nchannel="smtp"\nusername="sender@example.com"\n'
                             'to="legacy@example.com"\nQQ_SMTP_AUTH_CODE="fixture-only"\n', encoding='utf-8')
        self.contact = dict(name='测试订阅', email='one@example.com', enabled=False,
                            accept_uncertain=False, start_at='2026-09-22T10:00:00+08:00',
                            end_at='2026-10-22T10:00:00+08:00', delivery_mode='digest_daily')

    def test_policy_round_trip_and_legacy_save_cannot_reset_paid_expiry(self):
        save_settings(self.path, [self.contact])
        save_settings(self.path, 'one@example.com;two@example.com')
        actual = read_settings(self.path)['contacts']
        self.assertEqual(actual[0], self.contact)
        self.assertTrue(actual[1]['enabled'])
        self.assertEqual(actual[1]['end_at'], '')
        self.assertEqual(Config.load(self.path).section('notifications')['recipient_contacts'], actual)
        self.assertEqual(json.loads(settings_path(self.path).read_text(encoding='utf-8'))['version'], 4)

    def test_delete_all_persists_an_explicit_empty_list(self):
        save_settings(self.path, [])
        self.assertEqual(read_settings(self.path)['contacts'], [])
        self.assertEqual(Config.load(self.path).recipients, [])

    def test_bad_dates_modes_boolean_and_conflicting_duplicates_preserve_old(self):
        save_settings(self.path, [self.contact])
        for update in ({'enabled': 'false'}, {'delivery_mode': 'unknown'},
                       {'end_at': '2026-09-01T10:00:00+08:00'}, {'start_at': ''},
                       {'start_at': '2026-09-22T10:00:00'}):
            with self.subTest(update=update), self.assertRaises(ConfigError):
                save_settings(self.path, [{**self.contact, **update}])
        with self.assertRaises(ConfigError):
            save_settings(self.path, [self.contact, {**self.contact, 'enabled': True}])
        self.assertEqual(read_settings(self.path)['contacts'], [self.contact])

    def test_initial_test_is_one_saved_contact_and_does_not_change_subscription(self):
        from quota_monitor.subscription_test import send_test
        save_settings(self.path, [self.contact, {'name': '二', 'email': 'two@example.com'}])
        before = settings_path(self.path).read_bytes()
        notifier = Mock()
        result = send_test(self.path, 'one@example.com', notifier=notifier)
        notifier.send_to.assert_called_once()
        row, recipient = notifier.send_to.call_args.args
        self.assertEqual(recipient, 'one@example.com')
        self.assertIn('测试', row['title'])
        self.assertIn('非重置公告', row['body'])
        self.assertEqual(result['status'], 'smtp_accepted')
        self.assertNotIn('fixture-only', json.dumps(result))
        self.assertEqual(settings_path(self.path).read_bytes(), before)
        self.assertFalse((self.path.parent / 'data/state.db').exists())
        with self.assertRaises(ConfigError): send_test(self.path, 'unsaved@example.com', notifier=notifier)
        self.assertEqual(notifier.send_to.call_count, 1)

    def test_test_failure_is_not_retried_or_exposed_as_success(self):
        from quota_monitor.subscription_test import send_test
        save_settings(self.path, [self.contact])
        notifier = Mock()
        notifier.send_to.side_effect = OSError('secret server detail')
        with self.assertRaisesRegex(ConfigError, '先检查') as caught:
            send_test(self.path, 'one@example.com', notifier=notifier)
        self.assertNotIn('secret', str(caught.exception))
        notifier.send_to.assert_called_once()

    def test_saving_disabled_while_monitor_stopped_cancels_old_queue(self):
        from quota_monitor.store import Store
        from quota_monitor.notify import SMTPNotifier, deliver
        contact = {**self.contact, 'enabled': True, 'start_at': '', 'end_at': '', 'delivery_mode': 'immediate'}
        save_settings(self.path, [contact])
        config = Config.load(self.path)
        with_store = Store(config.database)
        with_store.configure_delivery(config.destination)
        with_store.enqueue('old', '[Codex] 已重置候选', 'fixture')
        with_store.close()
        save_settings(self.path, [{**contact, 'enabled': False}])
        save_settings(self.path, [contact])
        store = Store(config.database)
        self.addCleanup(store.close)
        notifier = SMTPNotifier(Config.load(self.path))
        notifier.send_to = Mock()
        deliver(store, notifier)
        notifier.send_to.assert_not_called()

    def test_queue_reconciliation_failure_reports_saved_state_without_secret(self):
        save_settings(self.path, [self.contact])
        from quota_monitor.store import Store
        Store(Config.load(self.path).database).close()
        from unittest.mock import patch
        with patch('quota_monitor.delivery.reconcile_subscriptions', side_effect=OSError('private database path')):
            with self.assertRaisesRegex(ConfigError, '已保存') as caught:
                save_settings(self.path, [{**self.contact, 'enabled': True}])
        self.assertNotIn('private', str(caught.exception))
        self.assertTrue(read_settings(self.path)['contacts'][0]['enabled'])
