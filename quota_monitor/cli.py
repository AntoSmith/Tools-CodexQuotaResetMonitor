import argparse
import json
import sqlite3
import sys
import time
from contextlib import contextmanager

from .config import Config, ConfigError
from .notify import ConsoleNotifier, deliver
from .service import Monitor, iso, process_post
from .store import Store


@contextmanager
def single_instance(path):
    handle = open(path, 'a+b')
    try:
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ConfigError('此状态库已有监控进程运行') from None
        yield
    finally:
        handle.close()


def demo():
    print('离线演示：以下为合成样例，不是真实官方公告；不会访问 X 或发送邮件。')
    c = Config({'accounts': [{'username': 'demo', 'id': '123', 'kind': 'personal'}]})
    s = Store(':memory:')
    now = time.time()
    samples = [f'We will do a global reset of Codex usage for all paid subscriptions at {iso(now + 3600)}.',
               'We have reset Codex usage for everyone.',
               'Codex users get a reset credit to redeem manually.',
               'We will not reset Codex usage.',
               'Codex usage reset is postponed.']
    try:
        for i, text in enumerate(samples, 1):
            process_post({'data': {'id': str(i), 'author_id': '123', 'created_at': iso(now), 'text': text}}, c, s, now)
        # The offline demo contains only fixed synthetic samples, so preserve its
        # CLI stdout presentation. Live authored notifications use stderr.
        deliver(s, ConsoleNotifier(stream=sys.stdout), now)
        print('\n演示状态：' + json.dumps(s.status(), ensure_ascii=False))
    finally:
        s.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Codex 额度公告监控（候选线索提醒）')
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('demo', help='离线合成消息演示')
    for cmd, help_text in [('check', '本地配置检查，不联网'), ('run', '启动实时采集'), ('status', '只读查看状态'),
                           ('browser-once', '浏览器单轮试采集，只存储和预览，不发送通知'),
                           ('browser-run', '浏览器定期采集并发送配置的通知'),
                           ('browser-login', '打开专用浏览器手动登录；关窗退出，不采集或发邮件'),
                           ('test-email', '抓取最新可见主页帖并发送一封测试邮件')]:
        sub = subs.add_parser(cmd, help=help_text)
        sub.add_argument('--config', default='config.browser.local.toml' if cmd.startswith('browser-') or cmd in ('status', 'test-email') else 'config.local.toml')
        if cmd == 'test-email':
            sub.add_argument('--account', default='thsottiaux', help='accounts 内的用户名，默认 Tibo')
            sub.add_argument('--preview', action='store_true', help='只抓取并保存邮件预览，不发送')
        if cmd == 'browser-once':
            sub.add_argument('--headed', action='store_true', default=None, help='显示独立浏览器窗口以检查登录/验证页面')
    args = parser.parse_args(argv)
    if args.command == 'demo':
        return demo()
    store = None
    try:
        c = Config.load(args.config)
        if args.command == 'test-email':
            from .email_test import send_latest
            c.database.parent.mkdir(parents=True, exist_ok=True)
            with single_instance(str(c.database) + '.lock'):
                result = send_latest(c, args.account, args.preview)
            print('原帖：' + result['post_url'])
            print('邮件预览/结果：' + result['report_path'])
            if result['status'] == 'smtp_accepted':
                print('SMTP 服务器已接受测试邮件，请检查收件箱或垃圾邮件。')
            elif result['status'] == 'preview':
                print('仅预览，未发送邮件。')
            else:
                print('发送失败或结果不确定：' + result.get('error_type', 'unknown') + '；请先检查邮箱再决定是否重试。', file=sys.stderr)
                return 1
            return 0
        if args.command == 'status':
            if not c.database.exists():
                print('尚未产生状态数据库；监控可能还未启动。')
                return 0
            with sqlite3.connect(c.database.resolve().as_uri() + '?mode=ro', uri=True) as db:
                result = {name: db.execute(sql).fetchall() for name, sql in {
                    'meta': 'SELECT key,value FROM meta', 'budget': 'SELECT month,used FROM budget',
                    'outbox': 'SELECT state,count(*) FROM outbox GROUP BY state'}.items()}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command.startswith('browser-'):
            from .browser import BrowserMonitor, collect_report, ingest, save_report, summary, login_browser
            c.validate_browser()
            if args.command == 'browser-run':
                c.validate_smtp_auth()
            c.database.parent.mkdir(parents=True, exist_ok=True)
            with single_instance(str(c.database) + '.lock'):
                if args.command == 'browser-login':
                    login_browser(c)
                    return 0
                store = Store(c.database)
                if args.command == 'browser-once':
                    store.configure_delivery(c.destination)
                    report = collect_report(c, headed=args.headed)
                    ingest(report, c, store)
                    path = save_report(report, c, store)
                    print(json.dumps(summary(report), ensure_ascii=False, indent=2))
                    print(f'报告：{path}；本次未发送通知。')
                    return 0 if report['posts'] else 1
                return BrowserMonitor(c, store).run()
        c.validate_live()
        if args.command == 'check':
            print(f'本地配置通过。资源月度保守上限：{c.resource_limit}；未验证 X 权限、账号身份或邮箱送达。')
            return 0
        c.database.parent.mkdir(parents=True, exist_ok=True)
        with single_instance(str(c.database) + '.lock'):
            store = Store(c.database)
            monitor = Monitor(c, store)
            result = monitor.run()
            if monitor.fatal:
                print('监控停止：' + monitor.fatal, file=sys.stderr)
            return result
    except (ConfigError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, ConfigError) else type(exc).__name__
        print('配置/运行错误：' + message + '；请查看 README.md 和所选配置文件。', file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
