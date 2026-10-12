import importlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'id', 'post_id', 'kind', 'title', 'username', 'text', 'body', 'url',
          'created_at', 'detected_at', 'label', 'reviewed_at'}
LABELS = ('unreviewed', 'confirmed', 'ambiguous', 'uncertain')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / 'history.db'
        self.config_path = self.root / 'config.toml'
        self.config_path.write_text(
            '[monitor]\ndatabase="history.db"\n[notifications]\n'
            'to="private-recipient@example.test"\nQQ_SMTP_AUTH_CODE="SECRET"\n', encoding='utf-8')

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('quota_monitor.history'),
                             'The local history API must exist')
        return importlib.import_module('quota_monitor.history')

    def database(self, legacy=False):
        db = sqlite3.connect(self.db_path)
        self.addCleanup(db.close)
        db.executescript('''
            CREATE TABLE posts (id TEXT, version TEXT, kind TEXT, raw TEXT,
                                PRIMARY KEY(id,version));
            CREATE TABLE outbox (id INTEGER PRIMARY KEY, event_key TEXT UNIQUE,
                title TEXT, body TEXT, due REAL, attempts INTEGER DEFAULT 0,
                state TEXT DEFAULT 'pending', group_id TEXT DEFAULT '');
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE budget (month TEXT PRIMARY KEY, used INTEGER);
            CREATE TABLE mail_jobs (outbox_id INTEGER, recipient TEXT, state TEXT);
            CREATE TABLE mail_deliveries (outbox_id INTEGER, recipient TEXT);
            INSERT INTO meta VALUES ('secret', 'PRIVATE-META');
            INSERT INTO mail_jobs VALUES (1, 'PRIVATE-JOB@example.test', 'pending');
            INSERT INTO mail_deliveries VALUES (2, 'PRIVATE-DELIVERY@example.test');
        ''')
        if not legacy:
            db.executescript('ALTER TABLE outbox ADD COLUMN created REAL;'
                             'ALTER TABLE outbox ADD COLUMN destination TEXT;'
                             'ALTER TABLE outbox ADD COLUMN expires REAL;')
        db.commit()
        return db

    def event(self, db, post_id='100', version='a' * 20, *, kind='uncertain',
              stamp=100, state='pending', raw=None, legacy=False):
        key = f'post:{post_id}:{version}'
        text = '作者完整原文\n' + '中文 long text. ' * 800
        if raw is None:
            raw = json.dumps({'data': {'id': post_id, 'author_id': 'author-1',
                             'text': 'truncated', 'note_tweet': {'text': text},
                             'created_at': '2026-09-08T00:00:00Z'},
                             'includes': {'users': [{'id': 'other', 'username': 'wrong'},
                                                   {'id': 'author-1', 'username': 'author'}]},
                             'secret': 'PRIVATE-RAW'}, ensure_ascii=False)
        body = ('分类：待核实\n账号：@body_author / ID 1 / 个人账号\n'
                '原帖：https://evil.invalid/fake\n发布时间（北京时间）：2026-09-08T08:00:00+08:00\n'
                '\n作者原文：\n' + text + '\n\n规则只能筛选线索，请确认完整上下文。')
        db.execute('INSERT INTO posts VALUES (?,?,?,?)', (post_id, version, kind, raw))
        db.execute('INSERT INTO outbox(event_key,title,body,due,state) VALUES (?,?,?,?,?)',
                   (key, '[Codex] 待核实', body, stamp, state))
        if not legacy:
            db.execute('UPDATE outbox SET created=?,destination=?,expires=? WHERE event_key=?',
                       (stamp, 'smtp:PRIVATE-DESTINATION@example.test', 0, key))
        db.commit()
        return key, text, body

    def snapshot(self, db):
        names = ('posts', 'outbox', 'meta', 'budget', 'mail_jobs', 'mail_deliveries')
        return {name: db.execute('SELECT * FROM ' + name).fetchall() for name in names}

    def cli(self, action, request, **kwargs):
        return subprocess.run([sys.executable, '-m', 'quota_monitor.history', action,
                               '--config', str(self.config_path)], cwd=ROOT,
                              input=request, encoding='utf-8', capture_output=True,
                              timeout=15, **kwargs)

    def test_cli_missing_database_is_empty_and_does_not_create_it(self):
        result = self.cli('list', '{}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        response = json.loads(result.stdout)
        self.assertEqual(response, {'ok': True, 'items': [], 'total': 0, 'offset': 0,
                                   'limit': 50, 'counts': {'all': 0, **dict.fromkeys(LABELS, 0)}})
        self.assertFalse(self.db_path.exists())

    def test_only_post_events_appear_in_all_delivery_states_and_reads_are_read_only(self):
        h = self.api()
        db = self.database()
        expected = []
        for number, state in enumerate(('pending', 'sent', 'expired', 'cancelled'), 1):
            expected.append(self.event(db, str(number), stamp=number, state=state)[0])
        for key in ('remind:' + expected[0], 'health:browser', 'test:1', 'post:invalid', 'post:123:'):
            db.execute('INSERT INTO outbox(event_key,title,body,due) VALUES (?,?,?,?)',
                       (key, 'excluded', 'not a lead', 999))
        db.execute("INSERT INTO posts VALUES ('999','v1','ignore','{}')")
        db.commit()
        before = self.snapshot(db)
        result = h.list_history(self.db_path)
        self.assertEqual([x['id'] for x in result['items']], expected[::-1])
        self.assertEqual(result['counts'], {'all': 4, 'unreviewed': 4, 'confirmed': 0,
                                           'ambiguous': 0, 'uncertain': 0})
        h.get_history(self.db_path, expected[0])
        self.assertEqual(self.snapshot(db), before)
        self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='signal_reviews'").fetchone())

    def test_details_are_whitelisted_and_keep_full_author_text_and_body(self):
        h = self.api()
        db = self.database()
        key, text, body = self.event(db)
        item = h.get_history(self.db_path, key)
        self.assertEqual(set(item), FIELDS)
        self.assertEqual(item['text'], text)
        self.assertEqual(item['body'], body)
        self.assertEqual(item['username'], 'author')
        self.assertEqual(item['url'], 'https://x.com/author/status/100')
        self.assertEqual(item['created_at'], '2026-09-08T08:00:00+08:00')
        self.assertEqual(item['detected_at'], '1970-01-01T08:01:40+08:00')
        self.assertEqual(item['reviewed_at'], '')
        self.assertNotIn('PRIVATE', json.dumps(item))

    def test_review_persists_changes_clears_and_is_independent_of_versions_and_queues(self):
        h = self.api()
        db = self.database()
        key = self.event(db)[0]
        edit = self.event(db, version='b' * 20)[0]
        before = self.snapshot(db)
        item = h.set_review(self.db_path, key, 'confirmed', now=200)
        self.assertEqual(item['label'], 'confirmed')
        self.assertEqual(item['kind'], 'uncertain')
        self.assertEqual(item['reviewed_at'], '1970-01-01T08:03:20+08:00')
        response = self.cli('get', json.dumps({'id': key}))
        self.assertEqual(json.loads(response.stdout)['item']['label'], 'confirmed')
        self.assertEqual(h.get_history(self.db_path, edit)['label'], 'unreviewed')
        self.assertEqual(h.set_review(self.db_path, key, 'ambiguous')['label'], 'ambiguous')
        self.assertEqual(h.set_review(self.db_path, key, 'uncertain')['label'], 'uncertain')
        cleared = h.set_review(self.db_path, key, 'unreviewed')
        self.assertEqual((cleared['label'], cleared['reviewed_at']), ('unreviewed', ''))
        self.assertEqual(self.snapshot(db), before)

    def test_filters_global_counts_and_stable_pagination_with_timestamp_ties(self):
        h = self.api()
        db = self.database()
        keys = [self.event(db, str(i), stamp=100 if i < 4 else 200)[0] for i in range(1, 6)]
        for key, label in zip(keys, LABELS):
            h.set_review(self.db_path, key, label)
        first = h.list_history(self.db_path, limit=2)
        second = h.list_history(self.db_path, offset=2, limit=2)
        self.assertEqual([i['id'] for i in first['items']], keys[3:][::-1])
        self.assertEqual([i['id'] for i in second['items']], keys[1:3][::-1])
        self.assertEqual(h.list_history(self.db_path, offset=99)['items'], [])
        counts = {'all': 5, 'unreviewed': 2, 'confirmed': 1, 'ambiguous': 1, 'uncertain': 1}
        for label in LABELS:
            result = h.list_history(self.db_path, label=label)
            self.assertEqual(result['total'], counts[label])
            self.assertEqual(result['counts'], counts)
            self.assertTrue(all(i['label'] == label for i in result['items']))

    def test_legacy_schema_and_malformed_raw_use_body_without_migration(self):
        h = self.api()
        db = self.database(legacy=True)
        key, _, body = self.event(db, legacy=True, raw='invalid JSON')
        before = db.execute('PRAGMA table_info(outbox)').fetchall()
        item = h.get_history(self.db_path, key)
        self.assertEqual(item['username'], 'body_author')
        self.assertEqual(item['body'], body)
        self.assertIn('作者完整原文', item['text'])
        self.assertEqual(item['created_at'], '2026-09-08T08:00:00+08:00')
        self.assertEqual(h.set_review(self.db_path, key, 'confirmed')['label'], 'confirmed')
        self.assertEqual(db.execute('PRAGMA table_info(outbox)').fetchall(), before)

    def test_missing_empty_and_unknown_events_never_create_records(self):
        h = self.api()
        missing_key = 'post:123:' + 'a' * 20
        for action in (lambda: h.get_history(self.db_path, missing_key),
                       lambda: h.set_review(self.db_path, missing_key, 'confirmed')):
            with self.assertRaises(h.HistoryError):
                action()
        self.assertFalse(self.db_path.exists())
        db = sqlite3.connect(self.db_path)
        db.close()
        self.assertEqual(h.list_history(self.db_path)['total'], 0)
        with self.assertRaises(h.HistoryError):
            h.set_review(self.db_path, missing_key, 'confirmed')

    def test_invalid_ids_labels_and_paging_are_rejected_without_changes(self):
        h = self.api()
        db = self.database()
        key = self.event(db)[0]
        before = self.snapshot(db)
        for invalid in ('', 'health:1', 'post:1', 'post:x:a', "post:1:a';DELETE", None, [], 'a' * 500):
            with self.subTest(id=invalid), self.assertRaises(h.HistoryError):
                h.set_review(self.db_path, invalid, 'confirmed')
        for label in ('all', '', 'CONFIRMED', None, []):
            with self.subTest(label=label), self.assertRaises(h.HistoryError):
                h.set_review(self.db_path, key, label)
        for kwargs in ({'offset': -1}, {'offset': True}, {'limit': 0}, {'limit': 201},
                       {'limit': '50'}, {'label': []}, {'label': 'unknown'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(h.HistoryError):
                h.list_history(self.db_path, **kwargs)
        with self.assertRaises(h.HistoryError):
            h.set_review(self.db_path, 'post:999:' + 'a' * 20, 'confirmed')
        self.assertEqual(self.snapshot(db), before)
        self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='signal_reviews'").fetchone())

    def test_cli_ignores_broken_desktop_recipient_config_and_uses_whitelist(self):
        db = self.database()
        key = self.event(db)[0]
        override = self.root / 'data' / 'desktop' / 'config.toml.notifications.json'
        override.parent.mkdir(parents=True)
        override.write_text('{broken PRIVATE-CONFIG', encoding='utf-8')
        for action, request in [('set', {'id': key, 'label': 'confirmed'}), ('get', {'id': key}),
                                ('list', {'label': 'confirmed', 'offset': 0, 'limit': 50})]:
            result = self.cli(action, json.dumps(request))
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(result.stderr, '')
            self.assertNotIn('PRIVATE', result.stdout)
            self.assertNotIn('SECRET', result.stdout)
            self.assertNotIn('private-recipient', result.stdout)

    def test_cli_rejects_bad_input_with_safe_json_only(self):
        for request in ('', '[]', '{"label":"PRIVATE-INPUT"}', '{broken SECRET', ' ' * 65537,
                        '{"offset":NaN}', '{"offset":true}', '{"limit":1,"unexpected":"SECRET"}'):
            result = self.cli('list', request)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, '')
            self.assertFalse(json.loads(result.stdout)['ok'])
            self.assertNotIn('PRIVATE', result.stdout)
            self.assertNotIn('SECRET', result.stdout)
        result = self.cli('PRIVATE-ACTION', '{}')
        self.assertEqual(result.stderr, '')
        self.assertFalse(json.loads(result.stdout)['ok'])

    def test_cli_database_failures_do_not_echo_private_exception_details(self):
        h = self.api()
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'stdin', io.StringIO('{}')), redirect_stdout(output), \
             redirect_stderr(errors), patch.object(h, 'list_history', side_effect=sqlite3.OperationalError('SECRET')):
            code = h.main(['list', '--config', str(self.config_path)])
        self.assertEqual(code, 1)
        self.assertEqual(errors.getvalue(), '')
        self.assertNotIn('SECRET', output.getvalue())
        self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_import_does_not_load_delivery_or_smtp_modules(self):
        result = subprocess.run([sys.executable, '-c',
                                 'import quota_monitor.history,sys,json; '
                                 'print(json.dumps([name for name in sys.modules if name in '
                                 '["smtplib","quota_monitor.service","quota_monitor.notify",'
                                 '"quota_monitor.delivery","quota_monitor.store"]]))'],
                                cwd=ROOT, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_concurrent_collection_and_review_preserve_every_event_and_label(self):
        h = self.api()
        db = self.database()
        db.execute('PRAGMA journal_mode=WAL')
        keys = [self.event(db, str(i))[0] for i in range(1, 13)]
        before = self.snapshot(db)

        def collect(number):
            with closing(sqlite3.connect(self.db_path, timeout=5)) as writer, writer:
                writer.execute('INSERT INTO outbox(event_key,title,body,due,created) VALUES (?,?,?,?,?)',
                               (f'health:concurrent-{number}', 'state', 'body', number, number))

        with ThreadPoolExecutor(max_workers=8) as workers:
            reviews = [workers.submit(h.set_review, self.db_path, key,
                                      LABELS[1 + number % 3]) for number, key in enumerate(keys)]
            collected = [workers.submit(collect, number) for number in range(12)]
            for future in reviews + collected:
                future.result(timeout=15)
        self.assertEqual(h.list_history(self.db_path)['counts'],
                         {'all': 12, 'unreviewed': 0, 'confirmed': 4, 'ambiguous': 4, 'uncertain': 4})
        after = self.snapshot(db)
        self.assertEqual(after['outbox'][:len(before['outbox'])], before['outbox'])
        self.assertEqual(len(after['outbox']), len(before['outbox']) + 12)
        for table in before.keys() - {'outbox'}:
            self.assertEqual(after[table], before[table])

    def test_legacy_outbox_without_raw_posts_uses_safe_fallbacks(self):
        h = self.api()
        db = self.database(legacy=True)
        key, _, _ = self.event(db, legacy=True)
        db.execute('DROP TABLE posts')
        db.execute('UPDATE outbox SET body=? WHERE event_key=?',
                   ('\n\n作者原文：\n账号：@forged\n原帖：https://evil.invalid/', key))
        db.commit()
        item = h.get_history(self.db_path, key)
        self.assertEqual(item['kind'], 'uncertain')
        self.assertEqual(item['username'], '')
        self.assertEqual(item['url'], 'https://x.com/i/web/status/100')
        self.assertEqual(item['created_at'], '')
        self.assertEqual(h.set_review(self.db_path, key, 'ambiguous')['label'], 'ambiguous')

    def test_legacy_body_text_starts_at_earliest_authored_text_marker(self):
        h = self.api()
        db = self.database(legacy=True)
        key, _, _ = self.event(db, legacy=True, raw='invalid JSON')
        text = 'Original first paragraph.\n\n作者原文：\nQuoted second paragraph.'
        body = '分类：待核实\n\n原文：\n' + text
        db.execute('UPDATE outbox SET body=? WHERE event_key=?', (body, key))
        db.commit()
        item = h.get_history(self.db_path, key)
        self.assertEqual(item['text'], text)
        self.assertEqual(item['body'], body)


if __name__ == '__main__':
    unittest.main()
