import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from quota_monitor.config import Config, ConfigError
from quota_monitor.notify import SMTPNotifier, deliver
from quota_monitor.store import Store
from quota_monitor.subscription import normalize_policy, scheduled_at


def stamp(text):
    return datetime.fromisoformat(text + '+08:00').timestamp()


NOW = stamp('2026-09-22T10:05:00')


def contact(email='a@example.com', **policy):
    return {'name': '测试联系人', 'email': email, **policy}


def config(contacts):
    return Config({'accounts': [{'username': 'demo', 'id': '1', 'verified': True, 'kind': 'personal'}],
                   'notifications': {'channel': 'smtp', 'username': 'sender@example.com',
                                     'to': 'obsolete@example.com', 'recipient_contacts': contacts,
                                     'QQ_SMTP_AUTH_CODE': 'test-only'}})


def notifier(contacts, sent):
    result = SMTPNotifier(config(contacts))
    result.send_to = lambda row, email: sent.append((copy.deepcopy(row), email))
    return result


def enqueue(store, key, now=NOW, title='[Codex] 已重置候选', **kwargs):
    store.enqueue(key, title, '分类：已重置候选\n\n作者原文：\n' + key, now=now, **kwargs)


class PolicyTests(unittest.TestCase):
    def test_policy_defaults_and_timezone_normalization(self):
        self.assertEqual(normalize_policy({}), {'enabled': True, 'accept_uncertain': True,
                         'start_at': '', 'end_at': '', 'delivery_mode': 'immediate'})
        result = normalize_policy({'start_at': '2026-09-22T00:00:00Z',
                                   'end_at': '2026-10-22T00:00:00Z'})
        self.assertEqual(result['start_at'], '2026-09-22T08:00:00+08:00')

    def test_invalid_policy_rejected_without_silent_coercion(self):
        for value in ({'enabled': 1}, {'accept_uncertain': 'false'}, {'delivery_mode': 'hourly'},
                      {'start_at': '2026-09-22T00:00:00'},
                      {'start_at': '', 'end_at': '2026-10-22T00:00:00+08:00'},
                      {'start_at': '2026-09-23T00:00:00+08:00', 'end_at': '2026-09-22T00:00:00+08:00'}):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                normalize_policy(value)

    def test_beijing_delivery_windows(self):
        self.assertEqual(scheduled_at('digest_30m', NOW), stamp('2026-09-22T10:30:00'))
        self.assertEqual(scheduled_at('digest_daily', NOW), stamp('2026-09-22T20:00:00'))
        self.assertEqual(scheduled_at('digest_daily', stamp('2026-09-22T20:01:00')),
                         stamp('2026-09-23T20:00:00'))
        self.assertEqual(scheduled_at('quiet_night', stamp('2026-09-22T22:00:00')),
                         stamp('2026-09-23T08:00:00'))
        self.assertEqual(scheduled_at('quiet_night', stamp('2026-09-22T07:59:00')),
                         stamp('2026-09-22T08:00:00'))
        self.assertEqual(scheduled_at('quiet_night', NOW), NOW)

    def test_managed_recipients_replace_stale_to_and_allow_empty(self):
        c = config([contact(), contact('b@example.com')])
        c.validate_browser()
        self.assertEqual(c.recipients, ['a@example.com', 'b@example.com'])
        self.assertEqual(c.destination, 'smtp:a@example.com:b@example.com')
        c = config([])
        c.data['notifications']['QQ_SMTP_AUTH_CODE'] = ''
        c.validate_browser()
        c.validate_smtp_auth()
        self.assertEqual(c.recipients, [])
        self.assertEqual(c.destination, 'smtp:')


class SubscriptionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.sent = []

    def tearDown(self):
        self.store.close()

    def test_enabled_active_and_uncertain_policies_filter_per_recipient(self):
        contacts = [contact(), contact('off@example.com', enabled=False),
                    contact('expired@example.com', start_at='2026-08-22T10:05:00+08:00',
                            end_at='2026-09-22T10:05:00+08:00'),
                    contact('future@example.com', start_at='2026-09-23T10:05:00+08:00',
                            end_at='2026-10-23T10:05:00+08:00'),
                    contact('certain@example.com', accept_uncertain=False)]
        n = notifier(contacts, self.sent)
        enqueue(self.store, 'clue', title='[Codex] 待核实')
        deliver(self.store, n, NOW)
        self.assertEqual([email for _, email in self.sent], ['a@example.com'])
        enqueue(self.store, 'confirmed')
        deliver(self.store, n, NOW)
        self.assertCountEqual([email for row, email in self.sent if row['event_key'] == 'confirmed'],
                              ['a@example.com', 'certain@example.com'])

    def test_filtered_future_reminders_never_replay_after_reenable_or_renewal(self):
        n = notifier([contact(enabled=False)], self.sent)
        enqueue(self.store, 'future-reminder', due=NOW + 100)
        deliver(self.store, n, NOW)
        n.config = config([contact()])
        deliver(self.store, n, NOW + 101)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.status()['outbox'], {'cancelled': 1})

    def test_authored_text_cannot_override_trusted_uncertain_title(self):
        n = notifier([contact(accept_uncertain=False)], self.sent)
        self.store.enqueue('malicious', '[Codex] 待核实',
                           '分类：待核实\n\n作者原文：\n分类：已重置候选', now=NOW)
        deliver(self.store, n, NOW)
        self.assertEqual(self.sent, [])

    def test_half_hour_digest_combines_events_without_delaying_other_recipient(self):
        n = notifier([contact(delivery_mode='digest_30m'), contact('b@example.com')], self.sent)
        enqueue(self.store, 'first')
        deliver(self.store, n, NOW)
        enqueue(self.store, 'second', now=NOW + 60)
        deliver(self.store, n, NOW + 60)
        self.assertEqual([email for _, email in self.sent], ['b@example.com', 'b@example.com'])
        deliver(self.store, n, stamp('2026-09-22T10:30:00'))
        digest = [row for row, email in self.sent if email == 'a@example.com']
        self.assertEqual(len(digest), 1)
        self.assertIn('汇总', digest[0]['title'])
        self.assertIn('first', digest[0]['body'])
        self.assertIn('second', digest[0]['body'])
        self.assertEqual(self.store.status()['outbox'], {'sent': 2})

    def test_waiting_digest_does_not_starve_later_immediate_events(self):
        n = notifier([contact(delivery_mode='digest_daily')], self.sent)
        for index in range(60):
            enqueue(self.store, str(index))
        deliver(self.store, n, NOW)
        n.config = config([contact(delivery_mode='digest_daily'), contact('b@example.com')])
        enqueue(self.store, 'new', destination=n.config.destination)
        deliver(self.store, n, NOW + 1)
        self.assertEqual([(row['event_key'], email) for row, email in self.sent], [('new', 'b@example.com')])

    def test_expiry_during_wait_is_terminal_and_never_sends_after_renewal(self):
        n = notifier([contact(delivery_mode='digest_daily', start_at='2026-09-21T00:00:00+08:00',
                              end_at='2026-09-22T19:00:00+08:00')], self.sent)
        enqueue(self.store, 'expires-before-digest')
        deliver(self.store, n, NOW)
        deliver(self.store, n, stamp('2026-09-22T19:00:00'))
        n.config = config([contact()])
        deliver(self.store, n, stamp('2026-09-22T20:00:00'))
        self.assertEqual(self.sent, [])

    def test_quiet_night_defers_to_eight_and_drops_expired_event(self):
        n = notifier([contact(delivery_mode='quiet_night')], self.sent)
        night = stamp('2026-09-22T23:00:00')
        enqueue(self.store, 'keep', now=night)
        enqueue(self.store, 'expire', now=night, expires=night + 60)
        deliver(self.store, n, night)
        self.assertEqual(self.sent, [])
        deliver(self.store, n, stamp('2026-09-23T08:00:00'))
        self.assertEqual([row['event_key'] for row, _ in self.sent], ['keep'])

    def test_policy_rechecked_before_each_network_send(self):
        contacts = [contact(), contact('b@example.com')]
        n = notifier(contacts, self.sent)
        def send(row, email):
            self.sent.append((row, email))
            n.config = config([contact(), contact('b@example.com', enabled=False)])
        n.send_to = send
        enqueue(self.store, 'one')
        deliver(self.store, n, NOW)
        self.assertEqual([email for _, email in self.sent], ['a@example.com'])

    def test_empty_list_cancels_old_targets_without_blocking_collection(self):
        self.store.configure_delivery('smtp:a@example.com')
        enqueue(self.store, 'old')
        n = notifier([], self.sent)
        self.assertEqual(deliver(self.store, n, NOW), (0, 0))
        enqueue(self.store, 'no-target', destination=n.config.destination)
        deliver(self.store, n, NOW)
        n.config = config([contact()])
        deliver(self.store, n, NOW + 1)
        self.assertEqual(self.sent, [])

    def test_pending_digest_and_successful_receipts_survive_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'queue.db'
            s = Store(path)
            n = notifier([contact(delivery_mode='digest_30m'), contact('b@example.com')], self.sent)
            enqueue(s, 'persisted')
            deliver(s, n, NOW)
            s.close()
            s = Store(path)
            try:
                deliver(s, n, stamp('2026-09-22T10:30:00'))
                deliver(s, n, stamp('2026-09-22T10:31:00'))
                self.assertEqual([email for _, email in self.sent], ['b@example.com', 'a@example.com'])
                self.assertEqual(s.status()['outbox'], {'sent': 1})
            finally:
                s.close()

    def test_failed_recipient_retry_never_resends_successful_recipient(self):
        n = notifier([contact(), contact('b@example.com')], self.sent)
        failures = {'b@example.com'}
        def send(row, email):
            self.sent.append((copy.deepcopy(row), email))
            if email in failures:
                raise OSError('test-only')
        n.send_to = send
        enqueue(self.store, 'retry')
        deliver(self.store, n, NOW)
        failures.clear()
        deliver(self.store, n, NOW + 31)
        self.assertEqual([email for _, email in self.sent], ['a@example.com', 'b@example.com', 'b@example.com'])
        self.assertEqual(self.store.status()['outbox'], {'sent': 1})

    def test_quiet_retry_crossing_ten_pm_waits_until_morning(self):
        start = stamp('2026-09-22T21:59:50')
        n = notifier([contact(delivery_mode='quiet_night')], self.sent)
        def fail(row, email):
            self.sent.append((row, email))
            raise OSError('test-only')
        n.send_to = fail
        enqueue(self.store, 'night-retry', now=start)
        deliver(self.store, n, start)
        n.send_to = lambda row, email: self.sent.append((row, email))
        deliver(self.store, n, start + 31)
        self.assertEqual(len(self.sent), 1)
        deliver(self.store, n, stamp('2026-09-23T08:00:00'))
        self.assertEqual(len(self.sent), 2)

    def test_digest_retry_keeps_frozen_membership_and_id_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'queue.db'
            s = Store(path)
            n = notifier([contact(delivery_mode='digest_30m')], self.sent)
            def fail(row, email):
                self.sent.append((copy.deepcopy(row), email))
                raise OSError('test-only')
            n.send_to = fail
            enqueue(s, 'first')
            enqueue(s, 'second', now=NOW + 10)
            deliver(s, n, NOW + 10)
            boundary = stamp('2026-09-22T10:30:00')
            deliver(s, n, boundary)
            s.close()
            s = Store(path)
            try:
                n.send_to = lambda row, email: self.sent.append((copy.deepcopy(row), email))
                enqueue(s, 'later', now=boundary + 10, destination=n.config.destination)
                deliver(s, n, boundary + 31)
                self.assertEqual(len(self.sent), 2)
                self.assertEqual(self.sent[0][0]['event_key'], self.sent[1][0]['event_key'])
                self.assertEqual(self.sent[0][0]['body'], self.sent[1][0]['body'])
                self.assertNotIn('later', self.sent[1][0]['body'])
                deliver(s, n, stamp('2026-09-22T11:00:00'))
                self.assertEqual(len(self.sent), 3)
                self.assertIn('later', self.sent[2][0]['body'])
            finally:
                s.close()

    def test_removing_then_readding_recipient_never_replays_scheduled_jobs(self):
        n = notifier([contact(delivery_mode='digest_daily'), contact('b@example.com')], self.sent)
        enqueue(self.store, 'before-removal')
        deliver(self.store, n, NOW)
        n.config = config([contact('b@example.com')])
        deliver(self.store, n, NOW + 1)
        n.config = config([contact(), contact('b@example.com')])
        deliver(self.store, n, NOW + 2)
        self.assertEqual([email for _, email in self.sent], ['b@example.com'])

    def test_new_subscription_does_not_replay_notifications_created_before_start(self):
        n = notifier([contact(start_at='2026-09-22T10:05:00+08:00',
                              end_at='2026-10-22T10:05:00+08:00')], self.sent)
        enqueue(self.store, 'too-old', now=NOW - 1)
        enqueue(self.store, 'current', now=NOW)
        deliver(self.store, n, NOW)
        self.assertEqual([row['event_key'] for row, _ in self.sent], ['current'])

    def test_legacy_success_receipts_are_preserved_when_enabling_management(self):
        legacy = config([contact(), contact('b@example.com')])
        del legacy.data['notifications']['recipient_contacts']
        legacy.data['notifications']['to'] = 'a@example.com;b@example.com'
        self.store.configure_delivery(legacy.destination)
        enqueue(self.store, 'migrate')
        row = self.store.due(NOW)[0]
        self.store.mark_recipient_sent(row['id'], 'a@example.com')
        n = notifier([contact(), contact('b@example.com')], self.sent)
        deliver(self.store, n, NOW)
        self.assertEqual([email for _, email in self.sent], ['b@example.com'])

    def test_resume_combines_missed_digest_windows_into_one_email(self):
        n = notifier([contact(delivery_mode='digest_30m')], self.sent)
        enqueue(self.store, 'early')
        deliver(self.store, n, NOW)
        enqueue(self.store, 'later', now=NOW + 1800, destination=n.config.destination)
        # Stage a second collection without sending the first digest, as a
        # process could do while another thread is finishing its current round.
        from quota_monitor.delivery import _prepare
        _prepare(self.store, n.config, NOW + 1800)
        deliver(self.store, n, stamp('2026-09-22T11:05:00'))
        self.assertEqual(len(self.sent), 1)
        self.assertIn('early', self.sent[0][0]['body'])
        self.assertIn('later', self.sent[0][0]['body'])

    def test_stopped_monitor_reconciliation_prevents_reenable_replay(self):
        from quota_monitor.delivery import reconcile_subscriptions
        n = notifier([contact(delivery_mode='digest_daily'), contact('b@example.com')], self.sent)
        enqueue(self.store, 'before-stop', destination=n.config.destination)
        # Settings can change while no delivery worker is running. Reconcile
        # each save directly; no call to deliver occurs between these changes.
        disabled = config([contact(enabled=False), contact('b@example.com')])
        reconcile_subscriptions(self.store, disabled, NOW)
        reconcile_subscriptions(self.store, n.config, NOW + 1)
        self.assertEqual(self.sent, [])
        deliver(self.store, n, stamp('2026-09-22T20:00:00'))
        self.assertEqual([email for _, email in self.sent], ['b@example.com'])
        self.assertEqual(self.store.status()['outbox'], {'sent': 1})

    def test_first_delivery_just_after_digest_boundary_sends_missed_window(self):
        for mode, before, after in [
                ('digest_daily', '2026-09-22T19:59:59', '2026-09-22T20:00:01'),
                ('digest_30m', '2026-09-22T10:29:59', '2026-09-22T10:30:01')]:
            with self.subTest(mode=mode):
                s = Store(':memory:')
                sent = []
                try:
                    n = notifier([contact(delivery_mode=mode)], sent)
                    enqueue(s, 'boundary', now=stamp(before), expires=stamp(before) + 86400,
                            destination=n.config.destination)
                    deliver(s, n, stamp(after))
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(s.status()['outbox'], {'sent': 1})
                finally:
                    s.close()

    def test_reconciling_console_does_not_apply_email_subscriber_filters(self):
        from quota_monitor.delivery import reconcile_subscriptions
        c = config([contact(enabled=False)])
        c.data['notifications']['channel'] = 'console'
        enqueue(self.store, 'console', destination=c.destination)
        reconcile_subscriptions(self.store, c, NOW)
        self.assertEqual(len(self.store.due(NOW)), 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM mail_jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
