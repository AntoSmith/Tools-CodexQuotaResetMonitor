"""One explicitly requested test email, independent of monitoring deduplication."""
import copy
import json
import uuid
from datetime import datetime, timezone

from .browser import collect_report
from .config import Config, ConfigError
from .notify import SMTPNotifier, deliver
from .service import BEIJING, parse_time
from .store import Store


def send_latest(config, account='thsottiaux', preview=False, collector=None, notifier=None):
    data = copy.deepcopy(config.data)
    data['accounts'] = [a for a in config.accounts if a['username'].lower() == account.lower()]
    if not data['accounts']:
        raise ConfigError('测试账号不在 accounts 中')
    data.setdefault('notifications', {})['channel'] = 'smtp'
    data.setdefault('browser', {})['max_posts'] = 1
    test_config = Config(data, config.base)
    test_config.validate_browser()
    if not preview:
        test_config.validate_smtp_auth()
    print(f"读取 @{data['accounts'][0]['username']} 最新可见主页帖……", flush=True)
    report = (collector or collect_report)(test_config, profile_only=True)
    posts = report.get('posts', [])
    if len(posts) != 1:
        raise ConfigError('未取得最新帖的完整详情，本次不发送；请运行 browser-once 检查页面访问')
    post = posts[0]
    stamp = parse_time(post.get('created_at'))
    if (post.get('username', '').lower() != account.lower() or post.get('complete') is not True
            or not str(post.get('id', '')).isdigit() or stamp is None
            or not isinstance(post.get('text'), str) or not post['text'].strip()):
        raise ConfigError('最新帖作者、正文或时间核验未通过，本次不发送')
    url = f"https://x.com/{post['username']}/status/{post['id']}"
    title = f"[Codex 工作流测试] @{post['username']} 最新可见帖子"
    body = (f"这是一封你主动触发的邮件测试，不代表发现额度重置。\n"
            f"账号：@{post['username']}\n发布时间（北京时间）：{stamp.astimezone(BEIJING).isoformat()}\n"
            f"原帖：{url}\n\n原文：\n{post['text']}\n\n"
            "本次跳过重置关键词筛选，只读取主页当前可见帖子中 ID 最新的一条，不包含回复页。"
            "公开页面覆盖可能不完整，不能保证是账号全部内容中的最新一条。")
    result = {'test_id': str(uuid.uuid4()), 'checked_at': datetime.now(timezone.utc).isoformat(),
              'to': data['notifications']['to'], 'post_url': url, 'title': title,
              'body': body, 'status': 'preview' if preview else 'sending'}
    path = config.database.parent / 'last-email-test.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    result['report_path'] = str(path)

    def save():
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    save()
    if not preview:
        # Dedicated in-memory outbox cannot deliver pending production messages or alter their history.
        store = Store(':memory:')
        try:
            store.enqueue('test-email:' + result['test_id'], title, body)
            ok, failed = deliver(store, notifier or SMTPNotifier(test_config))
            result['status'] = 'smtp_accepted' if ok == 1 and failed == 0 else 'failed_or_uncertain'
            if failed:
                result['error_type'] = store.get('last_delivery_error')
        finally:
            store.close()
        save()
    return result
