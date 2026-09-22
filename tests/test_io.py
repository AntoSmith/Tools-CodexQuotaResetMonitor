import copy
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from quota_monitor.config import Config, ConfigError
from quota_monitor.xapi import XClient, APIError
from quota_monitor.notify import SMTPNotifier, deliver
from quota_monitor.service import process_post
from quota_monitor.store import Store


def config():
    return Config({'monitor': {'max_age_hours': 24, 'reminder_minutes': 15},
                   'accounts': [{'username': 'example', 'id': '123', 'verified': True, 'kind': 'personal'}],
                   'budget': {'monthly_usd': 5.0, 'estimated_resource_usd': 0.01,
                              'max_resources': 100, 'provider_cap_confirmed': True},
                   'notifications': {'channel': 'console'}, 'x': {}})


class ConfigTests(unittest.TestCase):
    def test_live_requires_credentials_and_budget(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                config().validate_live()
        c = config()
        with patch.dict(os.environ, {'X_BEARER_TOKEN': 'test'}):
            c.validate_live()
            c.data['budget']['provider_cap_confirmed'] = False
            with self.assertRaises(ConfigError):
                c.validate_live()

    def test_unverified_and_duplicate_accounts_rejected(self):
        for field, value in [('verified', False), ('id', 'abc'), ('username', 'x OR from:bad')]:
            c = config()
            c.data['accounts'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ConfigError):
                c.validate()

    def test_limit_uses_both_caps(self):
        c = config()
        self.assertEqual(c.resource_limit, 100)
        c.data['budget']['monthly_usd'] = 0.2
        self.assertEqual(c.resource_limit, 20)


class FakeTransport:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def request(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        result = next(self.replies)
        if isinstance(result, Exception):
            raise result
        return result


class XTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')

    def tearDown(self):
        self.s.close()

    def test_rule_is_account_only_and_foreign_rules_rejected(self):
        c = XClient(config(), self.s, FakeTransport([{'data': [{'id': '1', 'tag': 'other', 'value': 'cat'}]}]))
        with self.assertRaises(APIError):
            c.ensure_rules()
        self.assertEqual(c.rule, '(from:example) -is:retweet')

    def test_rule_creation(self):
        t = FakeTransport([{}, {'data': [{'id': '1'}]}])
        XClient(config(), self.s, t).ensure_rules()
        self.assertEqual(t.calls[1][3]['add'][0]['tag'], 'codex-quota-monitor-v1')

    def test_backfill_paginates_and_reserves_before_io(self):
        t = FakeTransport([{'data': [{'id': '2'}], 'meta': {'next_token': 'next'}},
                           {'data': [{'id': '1'}], 'meta': {}}])
        c = XClient(config(), self.s, t)
        self.assertEqual([p['data']['id'] for p in c.backfill('2026-09-08T01:00:00Z', '2026-09-08T02:00:00Z')], ['2', '1'])
        self.assertEqual(t.calls[1][2]['next_token'], 'next')
        self.assertGreaterEqual(sum(self.s.status()['budget'].values()), 20)

    def test_api_partial_errors_are_not_silently_accepted(self):
        t = FakeTransport([{'errors': [{'detail': 'bad'}]}])
        with self.assertRaises(APIError):
            list(XClient(config(), self.s, t).backfill('2026-09-08T01:00:00Z', '2026-09-08T02:00:00Z'))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
        self.c = config()
        self.now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc).timestamp()
        self.post = {'data': {'id': '100', 'author_id': '123', 'created_at': '2026-09-08T11:59:00Z',
                             'conversation_id': '100', 'text': 'We will reset Codex usage at 2026-09-08T13:00:00Z.'}}

    def tearDown(self):
        self.s.close()

    def test_whitelist_longtext_dedup_and_reminder(self):
        p = copy.deepcopy(self.post)
        p['data']['text'] = 'truncated'
        p['data']['note_tweet'] = {'text': self.post['data']['text']}
        process_post(p, self.c, self.s, self.now)
        process_post(p, self.c, self.s, self.now)
        self.assertEqual(len(self.s.due(self.now)), 1)
        self.assertEqual(len(self.s.due(self.now + 3000)), 2)
        p['data']['id'] = '999'
        p['data']['author_id'] = '666'
        process_post(p, self.c, self.s, self.now)
        self.assertEqual(self.s.status()['posts'], 1)

    def test_stale_message_is_archived(self):
        self.post['data']['created_at'] = '2026-09-01T00:00:00Z'
        process_post(self.post, self.c, self.s, self.now)
        self.assertEqual(self.s.due(self.now), [])
        self.assertEqual(self.s.status()['posts'], 1)

    def test_missing_time_never_becomes_fresh_alert(self):
        del self.post['data']['created_at']
        process_post(self.post, self.c, self.s, self.now)
        self.assertEqual(self.s.due(self.now), [])

    def test_direct_correction_cancels_previous_reminder(self):
        process_post(self.post, self.c, self.s, self.now)
        p = copy.deepcopy(self.post)
        p['data'].update(id='101', text='Codex usage reset is postponed.',
                         referenced_tweets=[{'type': 'replied_to', 'id': '100'}])
        process_post(p, self.c, self.s, self.now + 5)
        self.assertEqual(len(self.s.due(self.now + 3000)), 2)

    def test_notification_failure_retries_without_stream_activity(self):
        process_post(self.post, self.c, self.s, self.now)
        class Failing:
            def send(self, row):
                raise OSError('secret should not be logged')
        self.assertEqual(deliver(self.s, Failing(), self.now), (0, 1))
        self.assertEqual(self.s.due(self.now + 1), [])
        seen = []
        class Working:
            def send(self, row):
                seen.append(row)
        self.assertEqual(deliver(self.s, Working(), self.now + 31), (1, 0))
        self.assertEqual(len(seen), 1)

    def test_context_expansion_does_not_duplicate_same_post(self):
        process_post(self.post, self.c, self.s, self.now)
        self.post['includes'] = {'tweets': [{'id': '11', 'text': 'context'}]}
        process_post(self.post, self.c, self.s, self.now)
        self.assertEqual(len(self.s.due(self.now)), 1)

    def test_expired_reminder_never_sends_after_restart(self):
        process_post(self.post, self.c, self.s, self.now)
        row = self.s.due(self.now)[0]
        self.s.sent(row['id'])
        self.assertEqual(self.s.due(self.now + 4000), [])

    def test_edit_removing_announcement_cancels_old_reminder(self):
        process_post(self.post, self.c, self.s, self.now)
        self.post['data'].update(id='101', text='Unrelated update.', edit_history_tweet_ids=['100', '101'])
        process_post(self.post, self.c, self.s, self.now)
        self.assertFalse(any(r['event_key'].startswith('remind:') for r in self.s.due(self.now + 3000)))

    def test_cancelled_outbox_snapshot_is_not_sent(self):
        self.s.enqueue('post:1', 'first', 'body', now=self.now)
        self.s.enqueue('remind:1', 'second', 'body', now=self.now, group='post:1')
        seen = []
        s = self.s
        class Notifier:
            def send(self, row):
                seen.append(row['event_key'])
                s.cancel_reminders('post:1')
        self.assertEqual(deliver(self.s, Notifier(), self.now), (1, 0))
        self.assertEqual(seen, ['post:1'])


class SMTPTests(unittest.TestCase):
    def test_ssl_email_headers_and_stable_id(self):
        c = config()
        c.data['notifications'] = {'channel': 'smtp', 'host': 'smtp.qq.com', 'port': 465,
                                   'username': 'sender@qq.com', 'to': 'target@qq.com'}
        seen = []
        class SMTP:
            def __init__(self, *a, **kw):
                self.args = (a, kw)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def login(self, user, password):
                seen.append((user, password))
            def send_message(self, msg):
                seen.append(msg)
                return {}
        with patch.dict(os.environ, {'QQ_SMTP_AUTH_CODE': 'fake'}):
            n = SMTPNotifier(c, factory=SMTP)
            row = {'event_key': '1', 'title': '中文提醒', 'body': '内容'}
            n.send(row)
            n.send(row)
        self.assertEqual(seen[1]['Message-ID'], seen[3]['Message-ID'])
        self.assertEqual(str(seen[1]['Subject']), '中文提醒')
        self.assertEqual(seen[1]['To'], 'target@qq.com')


if __name__ == '__main__':
    unittest.main()
