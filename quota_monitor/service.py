import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from .classify import classify
from .conversation import context_posts, context_text, context_display
from .notify import ConsoleNotifier, SMTPNotifier, deliver
from .store import BudgetExceeded
from .xapi import XClient, APIError

UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8))
LABELS = {'upcoming': '即将重置候选', 'completed': '已重置候选', 'credit': '重置券线索',
          'limits': '额度变化线索', 'correction': '延期/撤回线索', 'uncertain': '待核实'}


def parse_time(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def process_post(payload, config, store, now=None):
    now = time.time() if now is None else now
    p = payload.get('data', {})
    account = next((a for a in config.accounts if str(a['id']) == str(p.get('author_id'))), None)
    if account is None or not str(p.get('id', '')).isdigit():
        return
    refs = p.get('referenced_tweets', [])
    if any(r.get('type') == 'retweeted' for r in refs):
        return
    text = p.get('note_tweet', {}).get('text') or p.get('text', '')
    if not isinstance(text, str):
        return
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # Expansion payloads differ between stream and search; only the authored post defines a version.
    authored = json.dumps({'id': p['id'], 'text': text, 'author_id': p.get('author_id'),
                           'created_at': p.get('created_at'), 'refs': refs}, sort_keys=True)
    version = hashlib.sha256(authored.encode()).hexdigest()[:20]
    context = []
    if payload.get('collection', {}).get('source') == 'browser_dom' and config.section('browser').get('context_enabled', True):
        context = context_posts({**payload['collection'], 'id':p['id']})
    decision = classify(text, context=context_text(context))
    stamp = parse_time(p.get('created_at'))
    fresh = stamp is not None and -300 <= now - stamp.timestamp() <= config.section('monitor').get('max_age_hours', 24) * 3600
    root = str((p.get('edit_history_tweet_ids') or [p['id']])[0])
    group = 'post:' + root
    with store.transaction():
        if not store.record_post(str(p['id']), version, decision.kind, raw):
            if not (fresh and decision.kind in LABELS and
                    store.promote_ignored_post(str(p['id']), version, decision.kind, raw)):
                return
        # Only cancel an edit or direct parent; conversation membership alone is insufficient.
        if decision.kind in ('correction', 'completed', 'uncertain') or len(p.get('edit_history_tweet_ids', [])) > 1:
            store.cancel_reminders(group)
            for ref in refs:
                if ref.get('type') == 'replied_to':
                    store.cancel_reminders('post:' + str(ref['id']))
        if not fresh or decision.kind not in LABELS:
            return
        identity = '机构账号' if account.get('kind') == 'organization' else '个人账号（不等同机构正式公告）'
        time_line = '未确定；请核对原文，不生成倒计时'
        reset = parse_time(decision.reset_at)
        if reset:
            time_line = reset.astimezone(BEIJING).isoformat()
            if reset.timestamp() <= now:
                time_line += '（此时间已过，不能作为提前预警）'
        body = (f"分类：{LABELS[decision.kind]}\n依据：{decision.reason}\n"
                f"账号：@{account['username']} / ID {account['id']} / {identity}\n"
                f"原帖：https://x.com/i/web/status/{p['id']}\n"
                f"发布时间（北京时间）：{stamp.astimezone(BEIJING).isoformat()}\n"
                f"采集时间（北京时间）：{datetime.fromtimestamp(now, BEIJING).isoformat()}\n"
                f"发布至采集延迟：{max(0, int(now - stamp.timestamp()))} 秒\n"
                f"候选重置时间（北京时间）：{time_line}\n\n作者原文：\n{text}{context_display(context)}\n\n"
                '规则只能筛选线索，请确认完整上下文、订阅范围与账户状态。不会自动消耗额度或兑换重置券。')
        if payload.get('collection', {}).get('source') == 'browser_dom':
            body = ('来源：浏览器公开页面；覆盖范围不完整，作者数字 ID 来自本地配置映射。\n'
                    '发布时间按页面读取（无秒数时精度为分钟）；未核验回复关系或编辑历史。\n' + body)
        credit = ''
        if payload.get('collection', {}).get('discovery_source') == 'https://codex-reset.com/':
            credit = '发现来源：Data: codex-reset.com (https://codex-reset.com/)；正文读取自 X 公开页面。\n'
            if payload['collection'].get('context_status') == 'parent_unavailable':
                credit += '本次未能读取父帖正文，对话上下文缺失。\n'
            body = credit + body
        key = f"post:{p['id']}:{version}"
        expiry = min(stamp.timestamp() + config.section('monitor').get('max_age_hours', 24) * 3600,
                     reset.timestamp() if decision.kind == 'upcoming' and reset and reset.timestamp() > now else float('inf'))
        enqueued = store.enqueue(key, '[Codex] ' + LABELS[decision.kind], body, now=now, group=group, expires=expiry,
                                 destination=config.destination)
        lead = config.section('monitor').get('reminder_minutes', 15) * 60
        if decision.kind == 'upcoming' and reset and lead > 0 and reset.timestamp() - lead > now:
            store.enqueue('remind:' + key, '[Codex] 候选重置时间临近，请核实', body,
                          now=now, due=reset.timestamp() - lead, group=group, expires=reset.timestamp(),
                          destination=config.destination)
    # Return only after the outbox transaction commits. Desktop alerts do not depend
    # on SMTP delivery and must never replay historical rows or expired notices.
    if enqueued and expiry > now:
        return {'id': key, 'kind': decision.kind, 'title': LABELS[decision.kind],
                'username': account['username'], 'text': credit + text + context_display(context),
                'url': f"https://x.com/{account['username']}/status/{p['id']}",
                'created_at': p['created_at'], 'reason': decision.reason}


def recover_ignored_posts(config, store, now=None):
    """Re-evaluate fresh ignored evidence, including posts no longer in the page.

    Normal freshness, account checks and transactional deduplication still apply.
    Yield each committed alert immediately, just like live ingestion.
    """
    now = time.time() if now is None else now
    for raw in store.ignored_posts():
        payload = json.loads(raw)
        stamp = parse_time(payload.get('data', {}).get('created_at'))
        if stamp is None or not -300 <= now - stamp.timestamp() <= config.section('monitor').get('max_age_hours', 24) * 3600:
            continue
        alert = process_post(payload, config, store, now)
        if alert is not None:
            yield alert


class Monitor:
    def __init__(self, config, store, client=None, notifier=None):
        self.config, self.store = config, store
        self.client = client or XClient(config, store)
        self.notifier = notifier or (SMTPNotifier(config) if config.section('notifications').get('channel') == 'smtp' else ConsoleNotifier())
        self.stop = threading.Event()
        self.fatal = None

    def health(self, key, message):
        with self.store.transaction():
            self.store.enqueue('health:' + key, '[Codex 监控状态]', message, destination=self.config.destination)

    def catchup(self):
        end = time.time() - 15  # recent search excludes the most recent seconds
        hours = self.config.section('monitor').get('backfill_hours', 24)
        lower = end - min(hours * 3600, 7 * 86400 - 120)
        checkpoint = float(self.store.get('checkpoint', lower))
        if checkpoint < lower:
            self.health('gap:' + iso(lower), '存在无法补拉的时间缺口：' + iso(checkpoint) + ' 至 ' + iso(lower))
        start = max(lower, checkpoint - 120)
        if start >= end:
            return
        # Read the full interval before advancing; failed pagination replays on reconnect.
        posts = list(self.client.backfill(iso(start), iso(end)))
        posts.sort(key=lambda p: (p['data'].get('created_at', ''), int(p['data']['id'])))
        for payload in posts:
            process_post(payload, self.config, self.store)
        self.store.set('checkpoint', end)
        self.store.set('last_backfill_at', time.time())

    def collect(self):
        delay = 1
        outage = None
        try:
            while not self.stop.is_set():
                try:
                    self.client.ensure_rules()
                    for payload in self.client.stream(self.stop):
                        if self.stop.is_set():
                            return
                        if payload is not None and payload.get('_connected'):
                            self.catchup()  # socket already open: arriving posts stay buffered
                            continue
                        if payload is not None:
                            process_post(payload, self.config, self.store)
                        self.store.set('last_stream_at', time.time())
                        # Wall-clock heartbeats may still be buffered. Only a completed
                        # search interval proves durable coverage, never a stream arrival.
                        reconcile = min(6, self.config.section('monitor').get('backfill_hours', 24) / 2) * 3600
                        if time.time() - float(self.store.get('last_backfill_at', time.time())) >= reconcile:
                            self.catchup()
                        if outage:
                            self.health('recovered:' + outage, 'X 采集连接已恢复；已尝试补拉断线区间。')
                            outage = None
                        delay = 1
                    if not self.stop.is_set():
                        raise APIError('X 流已结束')
                except (APIError, OSError) as exc:
                    if isinstance(exc, APIError) and exc.permanent:
                        raise
                    message = str(exc) if isinstance(exc, APIError) else type(exc).__name__
                    self.store.set('last_collection_error', message)
                    if outage is None:
                        outage = str(int(time.time()))
                        self.health('outage:' + outage, 'X 采集中断，正在重连：' + message)
                    self.stop.wait(delay)
                    delay = min(60, delay * 2)
        except Exception as exc:
            self.fatal = str(exc) if isinstance(exc, (APIError, BudgetExceeded)) else type(exc).__name__
            self.store.set('fatal_error', self.fatal)
            self.health('fatal:' + str(int(time.time())), '采集已停止：' + self.fatal)
            self.stop.set()

    def run(self):
        self.store.configure_delivery(self.config.destination)
        self.store.set('started_at', time.time())
        worker = threading.Thread(target=self.collect, name='x-collector', daemon=True)
        worker.start()
        try:
            while not self.stop.is_set():
                deliver(self.store, self.notifier)
                self.stop.wait(1)
        except KeyboardInterrupt:
            self.stop.set()
        finally:
            self.stop.set()
            worker.join(timeout=30)
            if worker.is_alive():
                # Avoid closing a SQLite connection while the collector is writing it.
                raise RuntimeError('采集线程尚未退出，请结束当前进程后重启')
            deliver(self.store, self.notifier)  # bounded final attempt for fatal health notice
        return 1 if self.fatal else 0
