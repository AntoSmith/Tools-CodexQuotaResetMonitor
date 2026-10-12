"""Local recipient preferences shared by the desktop UI and CLI worker."""
import argparse
import json
import os
import sys
import tempfile
import unicodedata
from pathlib import Path

from .config import Config, ConfigError, parse_recipients
from .subscription import normalize_policy


def settings_path(config_path):
    path = Path(config_path).resolve()
    return path.parent / 'data' / 'desktop' / (path.name + '.notifications.json')


def normalize_contacts(value):
    if isinstance(value, str):
        value = [{'name': '', 'email': email} for email in parse_recipients(value)]
    if not isinstance(value, list):
        raise ConfigError('收件人名单格式无效')
    result = {}
    for index, contact in enumerate(value, 1):
        if not isinstance(contact, dict):
            raise ConfigError(f'第 {index} 行收件人格式无效')
        name, email = contact.get('name', ''), contact.get('email')
        if not isinstance(name, str) or len(name) > 80 or any(unicodedata.category(c).startswith('C') for c in name):
            raise ConfigError(f'第 {index} 行备注应为最多 80 字的单行文字')
        if not isinstance(email, str) or any(c in email for c in ';；:：,，'):
            raise ConfigError(f'第 {index} 行请只填写一个邮箱地址')
        try:
            address = parse_recipients(email)[0]
        except ConfigError:
            raise ConfigError(f'第 {index} 行邮箱地址无效，请检查后再保存') from None
        name = name.strip()
        policy = normalize_policy(contact)
        if address in result:
            old = result[address]
            if (name and old['name'] and old['name'] != name) or normalize_policy(old) != policy:
                raise ConfigError(f'第 {index} 行邮箱已存在且备注或订阅设置不同，请合并为一行')
            name = name or old['name']
        result[address] = {'name': name, 'email': address, **policy}
    return list(result.values())


def apply_settings(data, config_path):
    path = settings_path(config_path)
    if not path.exists():
        return
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('version') not in (1, 2, 3):
            raise ValueError('schema')
        contacts = normalize_contacts(value.get('to') if value['version'] == 1 else value.get('contacts'))
    except (ValueError, OSError, UnicodeError):
        raise ConfigError('本机邮箱设置无法读取，请在“设置 → 收件人”重新保存') from None
    notification = data.setdefault('notifications', {})
    notification['to'] = ';'.join(c['email'] for c in contacts)
    notification['recipient_names'] = {c['email']: c['name'] for c in contacts}
    notification['recipient_contacts'] = contacts


def read_settings(config_path):
    config = Config.load(config_path)
    value = config.section('notifications').get('to', '')
    names = config.section('notifications').get('recipient_names', {})
    contacts = config.section('notifications').get('recipient_contacts')
    if contacts is None:
        contacts = normalize_contacts([{'name': names.get(email, ''), 'email': email} for email in parse_recipients(value)]) if value else []
    return {'recipients': value, 'contacts': contacts, 'channel': config.section('notifications').get('channel', 'console'),
            'source': 'settings' if settings_path(config_path).exists() else 'config'}


def save_settings(config_path, value):
    contacts = normalize_contacts(value)
    if isinstance(value, str):
        # An older desktop build may still submit a flat list. Keep existing names.
        try:
            previous = {c['email']: c for c in read_settings(config_path)['contacts']}
        except ConfigError:
            previous = {}
        contacts = [previous.get(c['email'], c) for c in contacts]
    # Only read base TOML: a corrupt override must remain repairable from the UI.
    config = Config.load(config_path, use_desktop_settings=False)
    path = settings_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.recipients-', suffix='.tmp', delete=False) as f:
            temporary = f.name
            json.dump({'version': 3, 'contacts': contacts}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    # Persist cancellations even while collection is stopped. No delivery or
    # database creation here; later enabling must not resurrect queued notices.
    if config.database.exists():
        try:
            from .delivery import reconcile_subscriptions
            from .store import Store
            effective = Config.load(config_path)
            store = Store(config.database)
            try:
                reconcile_subscriptions(store, effective)
            finally:
                store.close()
        except Exception:
            raise ConfigError('名单已保存，但待发队列更新未确认；请保持监控停止，重新保存成功后再启动') from None
    return {'recipients': '; '.join(c['email'] for c in contacts), 'contacts': contacts,
            'count': len(contacts), 'source': 'settings',
            'channel': config.section('notifications').get('channel', 'console')}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['read', 'save', 'test'])
    parser.add_argument('--config', default='config.browser.local.toml')
    args = parser.parse_args(argv)
    try:
        if args.action == 'read':
            result = read_settings(args.config)
        else:
            request = json.loads(sys.stdin.read(65536))
            if not isinstance(request, dict):
                raise ConfigError('邮箱设置请求格式无效')
            if args.action == 'test':
                from .subscription_test import send_test
                result = send_test(args.config, request.get('email'))
            else:
                result = save_settings(args.config, request['contacts'] if 'contacts' in request else request.get('recipients'))
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        # Never return config contents, secrets or raw filesystem exception details.
        error = str(exc) if isinstance(exc, ConfigError) else '无法读写邮箱设置，请检查本地配置文件和目录权限'
        print(json.dumps({'ok': False, 'error': error}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
