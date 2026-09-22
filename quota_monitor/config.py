import math
import os
import re
import tomllib
from decimal import Decimal
from pathlib import Path


class ConfigError(ValueError):
    pass


def parse_recipients(value):
    if not isinstance(value, str) or '\r' in value or '\n' in value:
        raise ConfigError('notifications.to 必须是邮箱字符串，多个地址用分号分隔，不允许换行')
    result = []
    for part in re.split(r'[;；:：,，]', value):
        address = part.strip()
        if not re.fullmatch(r'[^\s@<>:：,，;；]+@[^\s@<>:：,，;；]+\.[^\s@<>:：,，;；]+', address):
            raise ConfigError('邮箱地址无效：to；请检查每个地址，分隔符之间不能留空')
        local, domain = address.rsplit('@', 1)
        address = local + '@' + domain.lower()
        if address not in result:
            result.append(address)
    return result


def normalize_destination(value):
    if value.startswith('smtp:'):
        return 'smtp:' + ':'.join(sorted(parse_recipients(value[5:])))
    return value


class Config:
    def __init__(self, data, base=None):
        self.data = data
        self.base = Path(base or '.').resolve()

    @classmethod
    def load(cls, path, *, use_desktop_settings=True):
        path = Path(path).resolve()
        with path.open('rb') as f:
            data = tomllib.load(f)
        if use_desktop_settings:
            from .desktop_settings import apply_settings
            apply_settings(data, path)
        return cls(data, path.parent)

    def section(self, name):
        return self.data.get(name, {})

    @property
    def recipients(self):
        return parse_recipients(self.section('notifications').get('to', ''))

    @property
    def destination(self):
        n = self.section('notifications')
        return normalize_destination(n.get('channel', 'console') + ':' + n.get('to', ''))

    @property
    def smtp_auth_code(self):
        value = self.section('notifications').get('QQ_SMTP_AUTH_CODE', '')
        if not isinstance(value, str):
            raise ConfigError('notifications.QQ_SMTP_AUTH_CODE 必须是引号包围的字符串')
        return value.strip() or os.environ.get('QQ_SMTP_AUTH_CODE', '').strip()

    def validate_smtp_auth(self):
        if self.section('notifications').get('channel') == 'smtp' and not self.smtp_auth_code:
            raise ConfigError('缺少 QQ_SMTP_AUTH_CODE：请填写 notifications 配置或同名环境变量')

    @property
    def accounts(self):
        return self.data.get('accounts', [])

    @property
    def database(self):
        return self.base / self.section('monitor').get('database', 'data/state.db')

    @property
    def resource_limit(self):
        b = self.section('budget')
        return min(int(b['max_resources']), int(Decimal(str(b['monthly_usd'])) / Decimal(str(b['estimated_resource_usd']))))

    def validate(self, require_budget=True):
        if not self.accounts:
            raise ConfigError('请填写并人工核对至少一个监控账号')
        ids, names = set(), set()
        for a in self.accounts:
            if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', a.get('username', '')) or not re.fullmatch(r'[0-9]+', str(a.get('id', ''))):
                raise ConfigError('账号必须包含有效 username 和数字 ID')
            if a.get('verified') is not True or a.get('kind') not in ('personal', 'organization'):
                raise ConfigError('账号身份须人工核对 verified=true，并注明 personal/organization')
            if str(a['id']) in ids or a['username'].lower() in names:
                raise ConfigError('监控账号重复')
            ids.add(str(a['id']))
            names.add(a['username'].lower())
        if len(self.accounts) > 10:
            raise ConfigError('第一版最多监控 10 个账号')
        b = self.section('budget')
        for key in (('monthly_usd', 'estimated_resource_usd', 'max_resources') if require_budget else ()):
            v = b.get(key)
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0:
                raise ConfigError('预算字段必须为正数：' + key)
        if require_budget and (type(b['max_resources']) is not int or self.resource_limit < 1):
            raise ConfigError('资源上限必须为正整数，预算须足够读取至少一条')
        n = self.section('notifications')
        if n.get('channel', 'console') not in ('console', 'smtp'):
            raise ConfigError('notifications.channel 仅支持 console/smtp')
        if n.get('channel') == 'smtp':
            sender = n.get('username', '')
            if not isinstance(sender, str) or not re.fullmatch(r'[^\s@<>:：,，;；]+@[^\s@<>:：,，;；]+\.[^\s@<>:：,，;；]+', sender):
                raise ConfigError('邮箱地址无效：username；发件邮箱只能填写一个')
            self.recipients
            if not re.fullmatch(r'[A-Za-z0-9.-]+', n.get('host', 'smtp.qq.com')):
                raise ConfigError('SMTP host 无效')
            if type(n.get('port', 465)) is not int or not 1 <= n.get('port', 465) <= 65535:
                raise ConfigError('SMTP port 无效')
        m = self.section('monitor')
        for key, default, lo, hi in [('max_age_hours', 24, 1, 168), ('backfill_hours', 24, 1, 168),
                                      ('reminder_minutes', 15, 0, 1440)]:
            v = m.get(key, default)
            if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
                raise ConfigError('monitor 配置范围错误：' + key)

    def validate_browser(self):
        self.validate(require_budget=False)
        b = self.section('browser')
        if type(b.get('reset_feed_enabled', False)) is not bool:
            raise ConfigError('browser.reset_feed_enabled 必须为 true/false')
        ua = b.get('reset_feed_user_agent', 'CodexQuotaResetMonitor/0.1 (personal local monitor)')
        if not isinstance(ua, str) or not 10 <= len(ua) <= 250 or any(ord(c)<32 or ord(c)>126 for c in ua):
            raise ConfigError('browser.reset_feed_user_agent 必须为 10-250 个可打印 ASCII 字符，标识项目名称')
        if type(b.get('context_enabled', True)) is not bool:
            raise ConfigError('browser.context_enabled 必须为 true/false')
        urls = b.get('conversation_urls', [])
        if (not isinstance(urls, list) or len(urls) > 5 or any(not isinstance(u, str) or not
                re.fullmatch(r'https://(?:www\.)?x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]+/?', u) for u in urls)):
            raise ConfigError('browser.conversation_urls 最多填写 5 个 https://x.com/用户名/status/数字ID 对话链接')
        if type(b.get('headless', False)) is not bool:
            raise ConfigError('browser.headless 必须为 true/false')
        if type(b.get('background', os.name == 'nt')) is not bool:
            raise ConfigError('browser.background 必须为 true/false')
        for key, default, lo, hi in [('interval_minutes', 30, 5, 1440), ('max_posts', 5, 1, 20),
                                      ('max_scrolls', 1, 0, 10), ('timeout_seconds', 300, 30, 1800)]:
            v = b.get(key, default)
            if type(v) is not int or not lo <= v <= hi:
                raise ConfigError('browser 配置范围错误：' + key)
        if b.get('channel', 'chrome') not in ('chrome', 'msedge'):
            raise ConfigError('browser.channel 仅支持 chrome/msedge')

    def validate_live(self):
        self.validate()
        if self.section('budget').get('provider_cap_confirmed') is not True:
            raise ConfigError('请先在 X 后台设置费用限制，再设置 provider_cap_confirmed=true')
        if not os.environ.get('X_BEARER_TOKEN', '').strip():
            raise ConfigError('缺少环境变量 X_BEARER_TOKEN')
        self.validate_smtp_auth()
