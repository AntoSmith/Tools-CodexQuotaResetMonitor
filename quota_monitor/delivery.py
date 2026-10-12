"""Persistent, per-recipient delivery scheduling for the local management panel."""
import hashlib
import time
from collections import defaultdict

from .config import parse_recipients
from .subscription import accepts, scheduled_at


def _contacts(config):
    return {contact['email']: contact for contact in config.recipient_contacts or []}


def reconcile_subscriptions(store, config, now=None):
    """Apply a saved recipient policy without opening any SMTP connection.

    Settings saves use this even while monitoring is stopped, so a later
    re-enable/renewal cannot revive notices cancelled by an intermediate save.
    Repeating the call is safe. All destination/job changes commit together.
    """
    now = time.time() if now is None else now
    with store.transaction():
        store.configure_delivery(config.destination)
        if config.section('notifications').get('channel') != 'smtp' or config.recipient_contacts is None:
            return 0
        return _prepare(store, config, now)


def _prepare(store, config, now):
    contacts = _contacts(config)
    with store.transaction():
        for row in store.mail_pending(now):
            addresses = row['destination'].partition(':')[2]
            recipients = parse_recipients(addresses) if addresses else []
            for recipient in recipients:
                policy = contacts.get(recipient)
                job = store.db.execute('SELECT * FROM mail_jobs WHERE outbox_id=? AND recipient=?',
                                       (row['id'], recipient)).fetchone()
                if job is not None and job['state'] != 'pending':
                    continue
                state = 'pending'
                if store.recipient_sent(row['id'], recipient):
                    state = 'sent'
                elif policy is None or not accepts(policy, row, now):
                    state = 'cancelled'
                mode = policy['delivery_mode'] if policy else 'immediate'
                if job is None:
                    # Keep the window belonging to the event's original due time.
                    # A worker waking just after 20:00 must catch up now, not put
                    # a 19:59 notice into tomorrow's (possibly expired) digest.
                    due = scheduled_at(mode, row['due'])
                    store.db.execute('''INSERT INTO mail_jobs
                        (outbox_id,recipient,due,scheduled,mode,state) VALUES (?,?,?,?,?,?)''',
                                     (row['id'], recipient, due, due, mode, state))
                elif state != 'pending':
                    store.db.execute('UPDATE mail_jobs SET state=? WHERE outbox_id=? AND recipient=?',
                                     (state, row['id'], recipient))
                elif mode != job['mode']:
                    due = scheduled_at(mode, max(row['due'], now))
                    store.db.execute('''UPDATE mail_jobs SET due=?,scheduled=?,mode=?,batch_key=NULL
                        WHERE outbox_id=? AND recipient=?''', (due, due, mode, row['id'], recipient))
            for job in store.db.execute("SELECT recipient FROM mail_jobs WHERE outbox_id=? AND state='pending'",
                                        (row['id'],)).fetchall():
                if job['recipient'] not in recipients:
                    store.db.execute("UPDATE mail_jobs SET state='cancelled' WHERE outbox_id=? AND recipient=?",
                                     (row['id'], job['recipient']))
        return store.finish_mail_jobs()


def _due_groups(store, now):
    with store.lock:
        jobs = [dict(row) for row in store.db.execute('''
            SELECT j.*,o.title,o.body,o.event_key,o.expires,o.created,o.due AS event_due
            FROM mail_jobs j JOIN outbox o ON j.outbox_id=o.id
            WHERE j.state='pending' AND j.due<=? AND o.state='pending' AND o.due<=?
            ORDER BY j.due,j.outbox_id,j.recipient''', (now, now))]
    groups = defaultdict(list)
    for job in jobs:
        if job['batch_key']:
            key = ('batch', job['recipient'], job['batch_key'])
        elif job['mode'] in ('digest_30m', 'digest_daily'):
            # On resume, combine missed windows instead of flooding the inbox
            # with a separate message for every elapsed half-hour bucket.
            key = ('digest', job['recipient'], job['mode'])
        else:
            key = ('single', job['recipient'], job['outbox_id'])
        groups[key].append(job)
    return list(groups.values())


def _check_group(store, config, group, now):
    """Re-read policy and queue immediately before each SMTP call."""
    contacts = _contacts(config)
    result = []
    with store.transaction():
        for job in group:
            key = (job['outbox_id'], job['recipient'])
            row = store.db.execute('SELECT * FROM outbox WHERE id=?', (job['outbox_id'],)).fetchone()
            live = store.db.execute('SELECT state,due,mode FROM mail_jobs WHERE outbox_id=? AND recipient=?', key).fetchone()
            if live is None or live['state'] != 'pending' or live['due'] > now:
                continue
            policy = contacts.get(job['recipient'])
            if (row is None or row['state'] != 'pending' or
                    (row['expires'] is not None and row['expires'] <= now) or
                    config.section('notifications').get('channel') != 'smtp' or
                    policy is None or not accepts(policy, dict(row), now)):
                store.db.execute("UPDATE mail_jobs SET state='cancelled' WHERE outbox_id=? AND recipient=?", key)
                continue
            mode = policy['delivery_mode']
            # A retry crossing 22:00 must also respect quiet hours. An operator's
            # preference change applies to queued messages, including this batch.
            if mode != live['mode'] or mode == 'quiet_night':
                due = scheduled_at(mode, max(row['due'], now))
                if due > now or mode != live['mode']:
                    store.db.execute('''UPDATE mail_jobs SET due=?,scheduled=?,mode=?,batch_key=NULL
                        WHERE outbox_id=? AND recipient=?''', (due, due, mode, *key))
                    continue
            if row['due'] <= now:
                result.append(job)
    return result


def _message(store, group):
    if group[0]['mode'] not in ('digest_30m', 'digest_daily'):
        return {'id': group[0]['outbox_id'], **group[0]}
    key = group[0]['batch_key']
    if not key:
        identity = '\n'.join(str(job['outbox_id']) for job in group)
        key = 'digest:' + hashlib.sha256((group[0]['recipient'] + ':' + identity).encode()).hexdigest()
        # Freeze membership before I/O. New notices form another batch while an
        # existing failed batch retries with the same Message-ID after a restart.
        with store.transaction():
            for job in group:
                store.db.execute('UPDATE mail_jobs SET batch_key=? WHERE outbox_id=? AND recipient=?',
                                 (key, job['outbox_id'], job['recipient']))
    body = ('以下为等待期间收集的额度相关线索。每条分类与原帖来源见下文；候选或待核实内容不代表官方确认。\n\n' +
            '\n\n────────────────────\n\n'.join(
                f"{index}. {job['title']}\n\n{job['body']}" for index, job in enumerate(group, 1)))
    return {'event_key': key, 'title': f'[Codex] 本次线索汇总（{len(group)} 条）', 'body': body}


def deliver_subscriptions(store, notifier, now=None):
    fixed_now = now
    now = time.time() if now is None else now
    ok = _prepare(store, notifier.config, now)
    failed = 0
    for snapshot in _due_groups(store, now):
        check_at = time.time() if fixed_now is None else fixed_now
        group = _check_group(store, notifier.config, snapshot, check_at)
        if not group:
            continue
        row = _message(store, group)
        recipient = group[0]['recipient']
        try:
            notifier.send_to(row, recipient)
        except Exception as exc:
            with store.transaction():
                for job in group:
                    delay = min(3600, 30 * 2 ** min(job['attempts'], 7))
                    store.db.execute('''UPDATE mail_jobs SET attempts=attempts+1,due=?
                        WHERE outbox_id=? AND recipient=? AND state='pending' ''',
                                     (check_at + delay, job['outbox_id'], recipient))
                store.set('last_delivery_error', type(exc).__name__)
            failed += 1
        else:
            with store.transaction():
                for job in group:
                    store.mark_recipient_sent(job['outbox_id'], recipient)
                    store.db.execute("UPDATE mail_jobs SET state='sent' WHERE outbox_id=? AND recipient=?",
                                     (job['outbox_id'], recipient))
                store.set('last_delivery_at', check_at)
    ok += store.finish_mail_jobs()
    return ok, failed
