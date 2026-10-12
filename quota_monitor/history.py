"""Local post-event history and human review labels; no delivery side effects."""
import argparse
import json
import math
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config


LABELS = ('unreviewed', 'confirmed', 'ambiguous', 'uncertain')
MAX_INPUT = 65536
BEIJING = timezone(timedelta(hours=8))
EVENT_ID = re.compile(r'post:([0-9]{1,30}):([A-Za-z0-9_-]{1,128})')
USERNAME = re.compile(r'[A-Za-z0-9_]{1,15}')
TITLE_KINDS = {'即将重置候选': 'upcoming', '已重置候选': 'completed',
               '重置券线索': 'credit', '额度变化线索': 'limits',
               '延期/撤回线索': 'correction', '待核实': 'uncertain'}


class HistoryError(ValueError):
    """User-facing errors contain fixed messages, never database or config values."""


def _parts(value):
    return EVENT_ID.fullmatch(value) if isinstance(value, str) else None


def _validate_id(event_id):
    if not _parts(event_id):
        raise HistoryError('历史线索 ID 无效')


def _validate_label(label, *, filtering=False):
    allowed = LABELS + (('all',) if filtering else ())
    if not isinstance(label, str) or label not in allowed:
        raise HistoryError('历史线索标记无效')


def _iso(value):
    try:
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                return ''
        elif type(value) in (int, float) and math.isfinite(value):
            stamp = datetime.fromtimestamp(value, timezone.utc)
        else:
            return ''
        return stamp.astimezone(BEIJING).isoformat(timespec='seconds')
    except (ValueError, OverflowError, OSError):
        return ''


@contextmanager
def _connection(database, *, write=False):
    path = Path(database).resolve()
    if not path.exists():
        yield None
        return
    # mode=rw prevents a missing database being created between exists/connect.
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                         uri=True, timeout=5, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.create_function('history_is_post', 1, lambda key: bool(_parts(key)), deterministic=True)
        db.create_function('history_post_id', 1,
                           lambda key: _parts(key).group(1) if _parts(key) else '', deterministic=True)
        db.create_function('history_version', 1,
                           lambda key: _parts(key).group(2) if _parts(key) else '', deterministic=True)
        yield db
    finally:
        db.close()


def _columns(db, table):
    # All callers pass static internal table names.
    return {row['name'] for row in db.execute('PRAGMA table_info(' + table + ')')}


def _query(db):
    """Build a whitelist projection compatible with pre-created-column databases."""
    columns = _columns(db, 'outbox')
    if not columns:
        return None
    if not {'event_key', 'title', 'body', 'due'} <= columns:
        raise HistoryError('历史数据库结构不兼容')
    post_columns = _columns(db, 'posts')
    has_posts = {'id', 'version', 'kind', 'raw'} <= post_columns
    has_reviews = bool(_columns(db, 'signal_reviews'))
    created = 'COALESCE(o.created,o.due,0)' if 'created' in columns else 'COALESCE(o.due,0)'
    posts = ('LEFT JOIN posts p ON p.id=history_post_id(o.event_key) '
             'AND p.version=history_version(o.event_key)') if has_posts else ''
    reviews = 'LEFT JOIN signal_reviews r ON r.event_key=o.event_key' if has_reviews else ''
    label = ("CASE WHEN r.label IN ('confirmed','ambiguous','uncertain') THEN r.label "
             "ELSE 'unreviewed' END") if has_reviews else "'unreviewed'"
    reviewed = 'r.reviewed_at' if has_reviews else 'NULL'
    kind, raw = ('p.kind', 'p.raw') if has_posts else ("''", "''")
    return f'''SELECT o.rowid AS ordinal, o.event_key AS id, o.title, o.body,
                      {created} AS detected, {kind} AS kind, {raw} AS raw,
                      {label} AS label, {reviewed} AS reviewed
               FROM outbox o {posts} {reviews} WHERE history_is_post(o.event_key)'''


def _string(value):
    return value if isinstance(value, str) else ''


def _metadata(body):
    positions = [body.find(marker) for marker in ('\n\n作者原文：\n', '\n\n原文：\n')]
    positions = [position for position in positions if position >= 0]
    return body[:min(positions)] if positions else ''


def _body_text(body):
    markers = [(body.find(marker), marker) for marker in ('\n\n作者原文：\n', '\n\n原文：\n')]
    markers = [(position, marker) for position, marker in markers if position >= 0]
    if not markers:
        return ''
    position, marker = min(markers)
    text = body[position + len(marker):]
    # Full notification text remains in body, including context and notes.
    return text.split('\n\n规则只能筛选线索，', 1)[0]


def _item(row):
    body = _string(row['body'])
    metadata = _metadata(body)
    try:
        payload = json.loads(row['raw'] or '{}')
    except (ValueError, TypeError, RecursionError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get('data')
    data = data if isinstance(data, dict) else {}
    note = data.get('note_tweet')
    note = note if isinstance(note, dict) else {}
    text = _string(note.get('text')) or _string(data.get('text')) or _body_text(body)
    username = _string(data.get('username'))
    includes = payload.get('includes')
    users = includes.get('users', []) if isinstance(includes, dict) else []
    if not USERNAME.fullmatch(username) and isinstance(users, list):
        username = next((_string(user.get('username')) for user in users
                         if isinstance(user, dict) and data.get('author_id') is not None
                         and str(user.get('id')) == str(data['author_id'])
                         and USERNAME.fullmatch(_string(user.get('username')))), '')
    if not USERNAME.fullmatch(username):
        match = re.search(r'^账号：@([A-Za-z0-9_]{1,15})(?:\s|/|$)', metadata, re.MULTILINE)
        username = match.group(1) if match else ''
    created = _iso(data.get('created_at'))
    if not created:
        match = re.search(r'^发布时间（北京时间）：([^\r\n]+)', metadata, re.MULTILINE)
        created = _iso(match.group(1)) if match else ''
    post_id = _parts(row['id']).group(1)
    title = _string(row['title'])
    kind = _string(row['kind']) or TITLE_KINDS.get(title.removeprefix('[Codex] '), '')
    return {'id': row['id'], 'post_id': post_id, 'kind': kind, 'title': title,
            'username': username, 'text': text, 'body': body,
            'url': f'https://x.com/{username}/status/{post_id}' if username
                   else f'https://x.com/i/web/status/{post_id}',
            'created_at': created, 'detected_at': _iso(row['detected']),
            'label': row['label'],
            'reviewed_at': _iso(row['reviewed']) if row['label'] != 'unreviewed' else ''}


def list_history(database, label='all', offset=0, limit=50):
    """List all delivery states, newest collected first; counts are global."""
    _validate_label(label, filtering=True)
    if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
        raise HistoryError('历史线索分页位置无效')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise HistoryError('历史线索每页数量须为 1 至 200')
    counts = dict.fromkeys(('all', *LABELS), 0)
    result = {'items': [], 'total': 0, 'offset': offset, 'limit': limit, 'counts': counts}
    with _connection(database) as db:
        if db is None:
            return result
        db.execute('BEGIN')  # Counts and page share one snapshot during collection.
        query = _query(db)
        if query is None:
            return result
        for row in db.execute(f'SELECT label,count(*) AS count FROM ({query}) GROUP BY label'):
            counts[row['label']] = row['count']
            counts['all'] += row['count']
        result['total'] = counts[label]
        rows = db.execute(f'''SELECT * FROM ({query}) WHERE (?='all' OR label=?)
                              ORDER BY detected DESC,ordinal DESC LIMIT ? OFFSET ?''',
                          (label, label, limit, offset)).fetchall()
        result['items'] = [_item(row) for row in rows]
    return result


def _get_row(db, event_id):
    query = _query(db) if db is not None else None
    row = db.execute(f'SELECT * FROM ({query}) WHERE id=?', (event_id,)).fetchone() if query else None
    if row is None:
        raise HistoryError('未找到该历史线索，请刷新列表')
    return row


def get_history(database, event_id):
    _validate_id(event_id)
    with _connection(database) as db:
        return _item(_get_row(db, event_id))


def set_review(database, event_id, label, now=None):
    """Persist only the review; unknown IDs cannot create a review or database."""
    _validate_id(event_id)
    _validate_label(label)
    reviewed = time.time() if now is None else now
    if not _iso(reviewed):
        raise HistoryError('复核时间无效')
    with _connection(database, write=True) as db:
        if db is None:
            raise HistoryError('未找到该历史线索，请刷新列表')
        db.execute('BEGIN IMMEDIATE')
        try:
            _get_row(db, event_id)
            db.execute('''CREATE TABLE IF NOT EXISTS signal_reviews (
                event_key TEXT PRIMARY KEY,
                label TEXT NOT NULL CHECK(label IN ('confirmed','ambiguous','uncertain')),
                reviewed_at REAL NOT NULL)''')
            if label == 'unreviewed':
                db.execute('DELETE FROM signal_reviews WHERE event_key=?', (event_id,))
            else:
                db.execute('''INSERT INTO signal_reviews(event_key,label,reviewed_at) VALUES (?,?,?)
                    ON CONFLICT(event_key) DO UPDATE SET label=excluded.label,
                    reviewed_at=excluded.reviewed_at''', (event_id, label, reviewed))
            item = _item(_get_row(db, event_id))
            db.commit()
            return item
        except BaseException:
            db.rollback()
            raise


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise HistoryError('历史线索命令参数无效')


def main(argv=None):
    try:
        parser = _Parser()
        parser.add_argument('action', choices=('list', 'get', 'set'))
        parser.add_argument('--config', default='config.browser.local.toml')
        args = parser.parse_args(argv)
        text = sys.stdin.read(MAX_INPUT + 1)
        if len(text) > MAX_INPUT:
            raise HistoryError('历史线索请求过长')
        request = json.loads(text)
        allowed = {'list': {'label', 'offset', 'limit'}, 'get': {'id'}, 'set': {'id', 'label'}}
        if not isinstance(request, dict) or request.keys() - allowed[args.action]:
            raise HistoryError('历史线索请求格式无效')
        config = Config.load(args.config, use_desktop_settings=False)
        if args.action == 'list':
            result = list_history(config.database, **request)
        elif args.action == 'get':
            result = {'item': get_history(config.database, request.get('id'))}
        else:
            result = {'item': set_review(config.database, request.get('id'), request.get('label'))}
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Config, SQLite, encoding and parser exceptions can contain private values.
        error = str(exc) if isinstance(exc, HistoryError) else '无法读取或保存历史线索，请检查本地配置和数据库'
        print(json.dumps({'ok': False, 'error': error}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
