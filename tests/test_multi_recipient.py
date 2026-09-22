import tempfile
import sqlite3
from contextlib import closing
import unittest
from pathlib import Path

from quota_monitor.config import Config, ConfigError
from quota_monitor.notify import SMTPNotifier, deliver
from quota_monitor.store import Store


def config(to='a@example.com:b@example.com'):
    return Config({'accounts': [{'username': 'demo', 'id': '1', 'verified': True, 'kind': 'personal'}],
                   'notifications': {'channel': 'smtp', 'username': 'sender@example.com',
                                     'to': to, 'QQ_SMTP_AUTH_CODE': 'test-only'}})


class MultiRecipientTests(unittest.TestCase):
    def test_separators_whitespace_dedup_and_single_compatibility(self):
        for to in [' a@example.com : b@example.com : a@example.com ',
                   'a@example.com：b@example.com', 'a@example.com, b@example.com',
                   'a@example.com，b@example.com', 'a@example.com; b@example.com',
                   'a@example.com；b@example.com；a@example.com']:
            c = config(to)
            c.validate_browser()
            self.assertEqual(c.recipients, ['a@example.com', 'b@example.com'])
        self.assertEqual(config('a@example.com').recipients, ['a@example.com'])

    def test_invalid_lists_fail_before_network(self):
        for to in ['', 'a@example.com:', ':a@example.com', 'a@example.com::b@example.com',
                   'a@example.com:bad', 'a@example.com;', 'a@example.com;;b@example.com',
                   'a@example.com；；b@example.com', 'a@example.com;bad',
                   'a@example.com\nBcc:bad@example.com', ['a@example.com']]:
            with self.subTest(to=to), self.assertRaises(ConfigError):
                config(to).validate_browser()

    def test_sender_remains_single(self):
        c = config(); c.data['notifications']['username'] = 'a@example.com:b@example.com'
        with self.assertRaises(ConfigError): c.validate_browser()

    def test_equivalent_lists_have_same_destination(self):
        self.assertEqual(config('a@example.com:b@example.com').destination,
                         config('b@example.com， a@example.com:a@example.com').destination)
        self.assertEqual(config('a@example.com').destination, 'smtp:a@example.com')
        self.assertEqual(config('b@example.com; a@example.com').destination,
                         config('a@example.com：b@example.com').destination)

    def test_recipient_changes_do_not_block_monitor_startup(self):
        from quota_monitor.browser import BrowserMonitor
        s = Store(':memory:')
        try:
            s.set('destination', 'smtp:b@example.com, a@example.com')
            m = BrowserMonitor(config(), s)
            m.collect = lambda: m.stop.set()
            self.assertEqual(m.run(), 0)
            self.assertEqual(s.get('destination'), 'smtp:a@example.com:b@example.com')
            m = BrowserMonitor(config('a@example.com:b@example.com:c@example.com'), s)
            m.collect = lambda: m.stop.set()
            self.assertEqual(m.run(), 0)
        finally: s.close()

    def test_semicolon_list_has_no_two_recipient_limit(self):
        for count in (1, 3, 10):
            recipients = [f'user{i}@example.com' for i in range(count)]
            c = config('; '.join(recipients))
            c.validate_browser()
            self.assertEqual(c.recipients, recipients)
            s = Store(':memory:')
            try:
                n = SMTPNotifier(c)
                sent = []
                n.send_to = lambda row, recipient: sent.append(recipient)
                s.enqueue('new', 'title', 'body', now=100)
                self.assertEqual(deliver(s, n, 100), (1, 0))
                self.assertCountEqual(sent, recipients)
            finally: s.close()

    def test_changes_preserve_pending_targets_and_sent_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.db'
            s = Store(path)
            # Simulate an old database, including a delayed reminder and partial delivery.
            s.set('destination', 'smtp:a@example.com;b@example.com;c@example.com')
            s.enqueue('old', 'title', 'body', due=200)
            old_id = s.db.execute('SELECT id FROM outbox').fetchone()[0]
            s.mark_recipient_sent(old_id, 'a@example.com')
            s.close()
            s = Store(path)
            try:
                n = SMTPNotifier(config('a@example.com;c@example.com;d@example.com'))
                sent = []
                n.send_to = lambda row, recipient: sent.append((row['event_key'], recipient))
                deliver(s, n, 100)  # Must reconcile future rows too, before recipients change again.
                n.config = config('a@example.com;b@example.com;c@example.com;d@example.com')
                s.enqueue('new', 'title', 'body', now=200, destination=n.config.destination)
                self.assertEqual(deliver(s, n, 200), (2, 0))
                self.assertEqual([r for key, r in sent if key == 'old'], ['c@example.com'])
                self.assertCountEqual([r for key, r in sent if key == 'new'], n.config.recipients)
                self.assertEqual(deliver(s, n, 300), (0, 0))
                self.assertTrue(s.recipient_sent(old_id, 'a@example.com'))
            finally: s.close()

    def test_channel_change_cancels_old_queue_without_replaying_history(self):
        s = Store(':memory:')
        try:
            s.set('destination', 'console:')
            s.record_post('1', 'v1', 'uncertain', '{}')
            s.enqueue('old', 'title', 'body', now=100)
            n = SMTPNotifier(config())
            sent = []
            n.send_to = lambda row, recipient: sent.append(recipient)
            self.assertEqual(deliver(s, n, 100), (0, 0))
            self.assertEqual(sent, [])
            self.assertEqual(s.status()['outbox'], {'cancelled': 1})
            self.assertEqual(s.status()['posts'], 1)
        finally: s.close()

    def test_legacy_database_upgrade_keeps_history_and_original_pending_recipient(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.db'
            with closing(sqlite3.connect(path)) as db:
                db.executescript('''
                    CREATE TABLE outbox (id INTEGER PRIMARY KEY, event_key TEXT UNIQUE,
                        title TEXT, body TEXT, due REAL, attempts INTEGER DEFAULT 0,
                        state TEXT DEFAULT 'pending', group_id TEXT DEFAULT '');
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
                    INSERT INTO meta VALUES ('destination', 'smtp:a@example.com');
                    INSERT INTO outbox(event_key,title,body,due,state) VALUES
                        ('sent','title','body',100,'sent'), ('pending','title','body',100,'pending');
                ''')
            s = Store(path)
            try:
                n = SMTPNotifier(config('a@example.com;b@example.com;c@example.com'))
                sent = []
                n.send_to = lambda row, recipient: sent.append((row['event_key'], recipient))
                self.assertEqual(deliver(s, n, 100), (1, 0))
                self.assertEqual(sent, [('pending', 'a@example.com')])
                self.assertEqual(s.status()['outbox'], {'sent': 2})
                self.assertEqual(s.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally: s.close()

    def test_replaced_recipients_cancel_pending_message_permanently(self):
        s = Store(':memory:')
        try:
            s.set('destination', 'smtp:a@example.com')
            s.enqueue('old', 'title', 'body', due=200)
            n = SMTPNotifier(config('b@example.com;c@example.com'))
            sent = []
            n.send_to = lambda row, recipient: sent.append(recipient)
            self.assertEqual(deliver(s, n, 100), (0, 0))
            n.config = config('a@example.com;b@example.com;c@example.com')
            self.assertEqual(deliver(s, n, 300), (0, 0))
            self.assertEqual(sent, [])
            self.assertEqual(s.status()['outbox'], {'cancelled': 1})
        finally: s.close()

    def test_legacy_queue_without_known_targets_is_not_sent_to_new_recipients(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.db'
            with closing(sqlite3.connect(path)) as db:
                db.executescript('''
                    CREATE TABLE outbox (id INTEGER PRIMARY KEY, event_key TEXT UNIQUE,
                        title TEXT, body TEXT, due REAL, attempts INTEGER DEFAULT 0,
                        state TEXT DEFAULT 'pending', group_id TEXT DEFAULT '');
                    INSERT INTO outbox(event_key,title,body,due) VALUES ('old','title','body',100);
                ''')
            s = Store(path)
            try:
                n = SMTPNotifier(config())
                sent = []
                n.send_to = lambda row, recipient: sent.append(recipient)
                self.assertEqual(deliver(s, n, 100), (0, 0))
                self.assertEqual(sent, [])
                self.assertEqual(s.status()['outbox'], {'cancelled': 1})
            finally: s.close()

    def test_partial_failure_survives_restart_without_resending_success(self):
        sent = []
        fail = {'b@example.com'}
        class SMTP:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def login(self, *a): pass
            def send_message(self, message):
                recipient = str(message['To'])
                sent.append((recipient, str(message['Message-ID'])))
                if recipient in fail: return {recipient: (550, b'refused')}
                return {}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'mail.db'; s = Store(path)
            s.enqueue('notice', 'subject', 'body', now=100)
            n = SMTPNotifier(config(), SMTP)
            self.assertEqual(deliver(s, n, 100), (0, 1))
            s.close(); s = Store(path); fail.clear()
            self.assertEqual(deliver(s, n, 131), (1, 0))
            self.assertEqual([x[0] for x in sent], ['a@example.com', 'b@example.com', 'b@example.com'])
            self.assertEqual(sent[1][1], sent[2][1])
            self.assertNotEqual(sent[0][1], sent[1][1])
            self.assertEqual(deliver(s, n, 200), (0, 0))
            s.close()

    def test_quit_error_after_acceptance_does_not_resend(self):
        class SMTP:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): raise OSError('quit failed')
            def login(self, *a): pass
            def send_message(self, message): return {}
        s = Store(':memory:')
        try:
            s.enqueue('notice', 'subject', 'body', now=100)
            self.assertEqual(deliver(s, SMTPNotifier(config(), SMTP), 100), (1, 0))
        finally: s.close()


if __name__ == '__main__': unittest.main()
