"""Experimental rendered-page collection. Never uses the paid X client."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from .classify import classify
from .conversation import context_posts, context_text
from .config import ConfigError
from .service import Monitor, parse_time, process_post, recover_ignored_posts
from .notify import ConsoleNotifier, SMTPNotifier
import threading
from contextlib import ExitStack


def background_browser(profile, channel, stop=None, timeout=20):
    from .background_browser import BackgroundBrowser
    return BackgroundBrowser(profile, channel, stop=stop, timeout=timeout)


def ingest(report, config, store, now=None, on_accepted=None):
    accepted = 0
    alerts = []
    for post in report.get('posts', []):
        account = next((a for a in config.accounts if a['username'].lower() == str(post.get('username', '')).lower()), None)
        if (account is None or post.get('complete') is not True or not parse_time(post.get('created_at'))
                or not str(post.get('id', '')).isdigit() or not isinstance(post.get('text'), str)):
            continue
        payload = {'data': {'id': post['id'], 'author_id': str(account['id']),
                           'created_at': post['created_at'], 'text': post['text']},
                   'collection': {'source': 'browser_dom', 'coverage': 'partial_unverified',
                                  'timestamp_source': post.get('timestamp_source')}}
        if post.get('discovery_source') == 'https://codex-reset.com/':
            payload['collection'].update({key:post.get(key) for key in
                ('discovery_source','feed_fetched_at','parent_id','parent_username','context_status')})
        if config.section('browser').get('context_enabled', True):
            payload['collection']['context'] = context_posts(post)
        alert = process_post(payload, config, store, now)
        if alert is not None:
            alerts.append(alert)
        accepted += 1
        if on_accepted is not None:
            # process_post has committed before this callback. Publish each
            # accepted post promptly so a later failure cannot lose its alert.
            on_accepted(alert)
    return {'accepted': accepted, 'alerts': alerts}


def emit_event(event_type, **fields):
    """Dedicated single-line protocol; never send config or raw exceptions here."""
    print('@quota-ui ' + json.dumps({'type': event_type, **fields}, ensure_ascii=False), flush=True)


def browser_runtime(config):
    b = config.section('browser')
    app_root = Path(__file__).resolve().parent.parent
    portable_node = app_root / 'runtime' / 'node' / 'node.exe'
    node = str(portable_node) if portable_node.is_file() else shutil.which('node')
    if not node:
        raise ConfigError('浏览器模式需要 Node.js 和 Playwright，请查看 README')
    env = os.environ.copy()
    for key in ('X_BEARER_TOKEN', 'QQ_SMTP_AUTH_CODE'):
        env.pop(key, None)
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
    app_modules = app_root / 'node_modules'
    modules = b.get('node_modules')
    if modules:
        env['NODE_PATH'] = str(config.base / modules)
    elif app_modules.is_dir():
        env['NODE_PATH'] = str(app_modules)
    elif bundled.exists():
        env['NODE_PATH'] = os.pathsep.join(filter(None, [env.get('NODE_PATH'), str(bundled)]))
    return node, env


def login_browser(config):
    b = config.section('browser')
    node, env = browser_runtime(config)
    options = dict(profile=str(config.base / b.get('profile', 'data/browser-profile')),
                   channel=b.get('channel', 'chrome'), username=config.accounts[0]['username'])
    print('请在打开的专用浏览器中手动登录 X，并确认能看到 Replies。完成后关闭全部专用浏览器窗口。', flush=True)
    print('此入口不会采集帖子或发送邮件；关窗仅表示登录操作结束，不代表已验证登录成功。', flush=True)
    process = subprocess.Popen([node, str(Path(__file__).with_name('browser_login.cjs'))],
                               stdin=subprocess.PIPE, text=True, encoding='utf-8', env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        process.communicate(json.dumps(options))
        if process.returncode:
            raise ConfigError('登录浏览器未正常启动或退出；请先停止监控并检查浏览器配置占用')
        print('专用浏览器已关闭。若已完成登录，现在可回到监控界面点击启动监控。', flush=True)
    finally:
        if process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
            else:
                process.terminate()
            process.communicate(timeout=15)


def collect_report(config, stop=None, headed=None, profile_only=False):
    b = config.section('browser')
    start = time.monotonic()
    feed = {'status':'disabled', 'candidates':[]}
    if not profile_only and b.get('reset_feed_enabled', False):
        from .reset_feed import collect_candidates
        feed = collect_candidates(config)
    if stop and stop.is_set():
        raise ConfigError('浏览器采集已停止')
    node, env = browser_runtime(config)
    options = dict(accounts=[{'username':a['username']} for a in config.accounts],
                   profile=str(config.base / b.get('profile', 'data/browser-profile')),
                   channel=b.get('channel', 'chrome'),
                   headed=not b.get('headless', False) if headed is None else headed,
                   max_posts=b.get('max_posts', 5), max_scrolls=b.get('max_scrolls', 1),
                   profile_only=profile_only, context_enabled=b.get('context_enabled', True),
                   conversation_urls=[] if profile_only else b.get('conversation_urls', []), reset_feed=feed)
    deadline = start + b.get('timeout_seconds', 300)
    with ExitStack() as cleanup:
        if options['headed'] and headed is None and b.get('background', os.name == 'nt'):
            browser = cleanup.enter_context(background_browser(Path(options['profile']), options['channel'], stop=stop))
            options['cdp_endpoint'] = browser.endpoint
            options['background'] = True
        process = subprocess.Popen([node, str(Path(__file__).with_name('browser_collect.cjs'))],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            payload = json.dumps(options)
            while True:
                if (stop and stop.is_set()) or time.monotonic() >= deadline:
                    raise ConfigError('浏览器采集已停止或超时；本轮不视为成功')
                try:
                    output, error = process.communicate(payload, timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            if process.returncode:
                raise ConfigError('浏览器启动或采集失败；检查 Node/Playwright、浏览器安装及独立配置目录占用')
            report = json.loads(output)
            if not isinstance(report, dict) or not isinstance(report.get('accounts'), list) or not isinstance(report.get('posts'), list):
                raise ConfigError('浏览器采集报告格式错误')
            report['reset_feed'] = {k:v for k,v in feed.items() if k != 'candidates'}
            report['duration_seconds'] = round(time.monotonic()-start, 2)
            return report
        finally:
            if process.poll() is None:
                # Kill only this task's spawned process tree, so browser profile locks are released.
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
                else:
                    process.terminate()
                process.communicate(timeout=15)


def save_report(report, config, store):
    target = config.database.parent / 'last-browser-report.json'
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    store.set('last_browser_attempt_at', report.get('collected_at', ''))
    store.set('browser_coverage', json.dumps(report['accounts'], ensure_ascii=False))
    return target


def summary(report):
    return {'collected_at':report.get('collected_at'), 'browser_mode':report.get('browser_mode'),
            'duration_seconds':report.get('duration_seconds'), 'pages_visited':report.get('pages_visited'),
            'reset_feed':report.get('reset_feed'),
            'coverage':'partial_unverified' if report.get('posts') else 'unavailable',
            'accounts':report.get('accounts'), 'posts':[{
                'id':p['id'], 'username':p['username'], 'created_at':p.get('created_at'),
                'kind':classify(p.get('text', ''), context=context_text(context_posts(p))).kind,
                'reason':classify(p.get('text', ''), context=context_text(context_posts(p))).reason,
                'context_count':len(context_posts(p)),
                'context_status':p.get('context_status'), 'discovery_source':p.get('discovery_source'),
                'url':f"https://x.com/{p['username']}/status/{p['id']}"} for p in report.get('posts', [])]}


def empty_reason(report):
    timestamps = sum(item.get('reason') == 'timestamp_unavailable'
                     for account in report.get('accounts', []) for item in account.get('skipped', []))
    if timestamps:
        return f'读取到帖子，但 {timestamps} 条因发布时间无法识别被跳过'
    return '本轮未取得完整帖子；请查看日志中的访问或详情跳过原因'


class BrowserMonitor(Monitor):
    def __init__(self, config, store):
        # Do not call the API Monitor initializer: it creates a paid XClient.
        self.config, self.store = config, store
        self.notifier = SMTPNotifier(config) if config.section('notifications').get('channel') == 'smtp' else ConsoleNotifier()
        self.stop = threading.Event()
        self.fatal = None

    def collect(self):
        round_number = 0
        try:
            while not self.stop.is_set():
                start = time.monotonic()
                started_at = time.time()
                delay = self.config.section('browser').get('interval_minutes', 30) * 60
                round_number += 1
                accepted = 0
                outcome, message = 'error', ''
                emit_event('round_start', round=round_number, started_at=started_at, interval_seconds=delay)

                def on_accepted(alert):
                    nonlocal accepted
                    accepted += 1
                    if alert is not None:
                        emit_event('alert', **alert)

                try:
                    for recovered in recover_ignored_posts(self.config, self.store):
                        emit_event('alert', **recovered)
                    report = collect_report(self.config, self.stop)
                    ingest(report, self.config, self.store, on_accepted=on_accepted)
                    save_report(report, self.config, self.store)
                    print(json.dumps(summary(report), ensure_ascii=False), flush=True)
                    message = '' if accepted else empty_reason(report)
                    self.store.set('last_browser_error', message)
                    outcome = 'partial' if accepted else 'empty'
                except (ConfigError, OSError, ValueError) as exc:
                    message = str(exc) if isinstance(exc, ConfigError) else type(exc).__name__
                    if self.stop.is_set():
                        return
                    try:
                        self.store.set('last_browser_error', message)
                        print('浏览器本轮失败：' + message, flush=True)
                    except Exception as record_error:
                        message = type(record_error).__name__
                        self.fatal = message
                        self.stop.set()
                except Exception as exc:
                    message = type(exc).__name__
                    self.fatal = message
                    self.stop.set()
                finally:
                    remaining = max(1, delay - (time.monotonic() - start))
                    ended_at = time.time()
                    emit_event('round_end', round=round_number, started_at=started_at,
                               ended_at=ended_at, next_at=0 if self.stop.is_set() else ended_at + remaining,
                               outcome=outcome, posts=accepted, error=message)
                if not self.stop.is_set():
                    self.stop.wait(remaining)
        except Exception as exc:
            self.fatal = type(exc).__name__
            self.stop.set()
