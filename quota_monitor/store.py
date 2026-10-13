"""Single connection guarded across threads; transactions preserve outbox state."""
import sqlite3
import json
import re
import threading
import time
from contextlib import contextmanager

from .config import normalize_destination, parse_recipients


class BudgetExceeded(RuntimeError):
    pass


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT, version TEXT, kind TEXT, raw TEXT,
                PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, event_key TEXT UNIQUE, title TEXT, body TEXT,
                due REAL, attempts INTEGER DEFAULT 0, state TEXT DEFAULT 'pending',
                group_id TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS budget (month TEXT PRIMARY KEY, used INTEGER);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS mail_deliveries (
                outbox_id INTEGER, recipient TEXT, PRIMARY KEY(outbox_id, recipient));
            CREATE TABLE IF NOT EXISTS mail_jobs (
                outbox_id INTEGER, recipient TEXT, due REAL, scheduled REAL, mode TEXT,
                state TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, batch_key TEXT,
                PRIMARY KEY(outbox_id, recipient));
            CREATE INDEX IF NOT EXISTS mail_jobs_due ON mail_jobs(state,due);
            CREATE TABLE IF NOT EXISTS source_assessments (
                post_id TEXT, provider TEXT, metadata TEXT,
                PRIMARY KEY(post_id,provider));
        ''')
        if 'expires' not in {r[1] for r in self.db.execute('PRAGMA table_info(outbox)')}:
            self.db.execute('ALTER TABLE outbox ADD COLUMN expires REAL')
        if 'destination' not in {r[1] for r in self.db.execute('PRAGMA table_info(outbox)')}:
            with self.transaction():
                self.db.execute('ALTER TABLE outbox ADD COLUMN destination TEXT')
                # Old preview-only runs may not have recorded any global target.
                # Do not assign their historical queue to newly configured emails.
                self.db.execute("UPDATE outbox SET destination=? WHERE state='pending'",
                                (self.get('destination') or 'unknown:',))
        if 'created' not in {r[1] for r in self.db.execute('PRAGMA table_info(outbox)')}:
            with self.transaction():
                self.db.execute('ALTER TABLE outbox ADD COLUMN created REAL')
                self.db.execute('UPDATE outbox SET created=due WHERE created IS NULL')

    @contextmanager
    def transaction(self):
        with self.lock:
            nested = self.db.in_transaction
            if not nested:
                self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                if not nested:
                    self.db.commit()
            except BaseException:
                if not nested:
                    self.db.rollback()
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def save_source_assessments(self, assessments):
        """Keep source annotations separately; never alter outbox or reviews."""
        from .divin_feed import normalize_assessment
        if not isinstance(assessments, dict):
            return
        with self.transaction():
            for post_id, raw in list(assessments.items())[:200]:
                metadata = normalize_assessment(raw)
                if (not isinstance(post_id, str) or not re.fullmatch(r'[0-9]{1,25}', post_id)
                        or not metadata or not any(url.endswith('/status/' + post_id)
                                                  for url in metadata['source_urls'])):
                    continue
                self.db.execute('''INSERT INTO source_assessments VALUES (?,?,?)
                    ON CONFLICT(post_id,provider) DO UPDATE SET metadata=excluded.metadata''',
                    (post_id, 'divin.cc', json.dumps(metadata, ensure_ascii=False)))

    def source_assessment(self, post_id):
        from .divin_feed import normalize_assessment
        with self.lock:
            row = self.db.execute('SELECT metadata FROM source_assessments WHERE post_id=? AND provider=?',
                                  (str(post_id), 'divin.cc')).fetchone()
        try:
            return normalize_assessment(json.loads(row['metadata'])) if row else {}
        except (ValueError, TypeError, RecursionError):
            return {}

    def record_post(self, post_id, version, kind, raw):
        # Caller wraps record + enqueue together in transaction().
        with self.lock:
            return self.db.execute('INSERT OR IGNORE INTO posts VALUES (?,?,?,?)',
                                   (post_id, version, kind, raw)).rowcount == 1

    def enqueue(self, key, title, body, now=None, due=None, group='', expires=None, destination=None):
        with self.lock:
            destination = destination if destination is not None else self.get('destination')
            created = now if now is not None else (due if due is not None else time.time())
            return self.db.execute('INSERT OR IGNORE INTO outbox(event_key,title,body,due,group_id,expires,destination,created) VALUES (?,?,?,?,?,?,?,?)',
                                   (key, title, body, due if due is not None else created, group, expires, destination, created)).rowcount == 1

    def configure_delivery(self, destination):
        """Reconcile queued targets, without binding collection history to recipients.

        Queued messages never gain recipients. Removal is permanent for that row,
        even if an address is re-added before its next retry or reminder is due.
        Legacy rows inherit the previous global destination before it is updated.
        """
        destination = normalize_destination(destination)
        channel, _, addresses = destination.partition(':')
        current = set(parse_recipients(addresses)) if channel == 'smtp' and addresses else set()
        with self.transaction():
            previous = self.get('destination')
            self.db.execute("UPDATE outbox SET destination=? WHERE state='pending' AND destination IS NULL",
                            (previous or destination,))
            for row in self.db.execute("SELECT id,destination FROM outbox WHERE state='pending'").fetchall():
                original = normalize_destination(row['destination'])
                old_channel, _, old_addresses = original.partition(':')
                state = 'pending'
                target = original
                if channel != old_channel:
                    state = 'cancelled'
                elif channel == 'smtp':
                    remaining = sorted(set(parse_recipients(old_addresses) if old_addresses else []) & current)
                    if not remaining:
                        state = 'cancelled'
                    else:
                        target = 'smtp:' + ':'.join(remaining)
                if target != row['destination'] or state != 'pending':
                    self.db.execute('UPDATE outbox SET destination=?,state=? WHERE id=?',
                                    (target, state, row['id']))
            self.set('destination', destination)

    def promote_ignored_post(self, post_id, version, kind, raw):
        # Caller includes this and enqueue in one transaction. Preserve the
        # authored version/outbox key so already delivered posts cannot replay.
        with self.lock:
            return self.db.execute("UPDATE posts SET kind=?,raw=? WHERE id=? AND version=? AND kind='ignore'",
                                   (kind, raw, post_id, version)).rowcount == 1

    def ignored_posts(self):
        with self.lock:
            return [r[0] for r in self.db.execute("""SELECT raw FROM posts p WHERE kind='ignore'
                AND rowid=(SELECT MAX(rowid) FROM posts newer WHERE newer.id=p.id)""")]

    def due(self, now=None):
        with self.lock:
            now = time.time() if now is None else now
            self.db.execute("UPDATE outbox SET state='expired' WHERE state='pending' AND expires<=?", (now,))
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM outbox WHERE state='pending' AND due<=? ORDER BY id LIMIT 50",
                (now,))]

    def mail_pending(self, now):
        """All pending events, including future reminders; no due-row starvation."""
        with self.lock:
            self.db.execute("UPDATE outbox SET state='expired' WHERE state='pending' AND expires<=?", (now,))
            return [dict(row) for row in self.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY id")]

    def finish_mail_jobs(self):
        """Finalize only events with jobs, without inventing successful receipts."""
        with self.transaction():
            self.db.execute("""UPDATE mail_jobs SET state='cancelled' WHERE state='pending'
                AND outbox_id IN (SELECT id FROM outbox WHERE state!='pending')""")
            rows = self.db.execute("""SELECT o.id,
                    EXISTS(SELECT 1 FROM mail_jobs j WHERE j.outbox_id=o.id AND j.state='sent') AS sent
                FROM outbox o WHERE o.state='pending'
                AND EXISTS(SELECT 1 FROM mail_jobs j WHERE j.outbox_id=o.id)
                AND NOT EXISTS(SELECT 1 FROM mail_jobs j WHERE j.outbox_id=o.id AND j.state='pending')""").fetchall()
            for row in rows:
                self.db.execute('UPDATE outbox SET state=? WHERE id=?',
                                ('sent' if row['sent'] else 'cancelled', row['id']))
            return sum(bool(row['sent']) for row in rows)

    def sent(self, row_id):
        with self.transaction():
            self.db.execute("UPDATE outbox SET state='sent' WHERE id=? AND state='pending'", (row_id,))

    def recipient_sent(self, row_id, recipient):
        with self.lock:
            return self.db.execute('SELECT 1 FROM mail_deliveries WHERE outbox_id=? AND recipient=?',
                                   (row_id, recipient)).fetchone() is not None

    def mark_recipient_sent(self, row_id, recipient):
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO mail_deliveries VALUES (?,?)', (row_id, recipient))

    def is_pending(self, row_id, now):
        with self.lock:
            return self.db.execute("SELECT 1 FROM outbox WHERE id=? AND state='pending' AND due<=? AND (expires IS NULL OR expires>?)",
                                   (row_id, now, now)).fetchone() is not None

    def failed(self, row_id, now=None):
        with self.transaction():
            row = self.db.execute('SELECT attempts FROM outbox WHERE id=?', (row_id,)).fetchone()
            delay = min(3600, 30 * 2 ** min(row[0], 7))
            self.db.execute('UPDATE outbox SET attempts=attempts+1,due=? WHERE id=?',
                            ((time.time() if now is None else now) + delay, row_id))

    def cancel_reminders(self, group):
        with self.lock:
            self.db.execute("UPDATE outbox SET state='cancelled' WHERE group_id=? AND event_key LIKE 'remind:%' AND state='pending'", (group,))

    def usage(self, month):
        with self.lock:
            r = self.db.execute('SELECT used FROM budget WHERE month=?', (month,)).fetchone()
            return r[0] if r else 0

    def reserve(self, count, limit, month):
        with self.transaction():
            used = self.usage(month)
            if count < 0 or used + count > limit:
                raise BudgetExceeded('本地月度资源预算不足；采集停止')
            self.db.execute('INSERT INTO budget VALUES (?,?) ON CONFLICT(month) DO UPDATE SET used=excluded.used', (month, used + count))

    def get(self, key, default=None):
        with self.lock:
            r = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return r[0] if r else default

    def set(self, key, value):
        with self.transaction():
            self.db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))

    def status(self):
        with self.lock:
            return {'posts': self.db.execute('SELECT count(*) FROM posts').fetchone()[0],
                    'outbox': {r[0]: r[1] for r in self.db.execute('SELECT state,count(*) FROM outbox GROUP BY state')},
                    'meta': {r[0]: r[1] for r in self.db.execute('SELECT key,value FROM meta')},
                    'budget': {r[0]: r[1] for r in self.db.execute('SELECT month,used FROM budget')}}
