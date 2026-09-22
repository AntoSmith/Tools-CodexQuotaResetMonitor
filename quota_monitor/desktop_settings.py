"""Local recipient preferences shared by the desktop UI and CLI worker."""
import argparse
import json
import os
import sys
import tempfile
import unicodedata
from pathlib import Path

from .config import Config, ConfigError, parse_recipients


def settings_path(config_path):
    path = Path(config_path).resolve()
    return path.parent / 'data' / 'desktop' / (path.name + '.notifications.json')


def normalize_contacts(value):
    if isinstance(value, str):
        return [{'name': '', 'email': email} for email in parse_recipients(value)]
    if not isinstance(value, list) or not value:
        raise ConfigError('请至少添加一个收件邮箱')
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
        if address in result and name and result[address] and result[address] != name:
            raise ConfigError(f'第 {index} 行邮箱已存在且备注不同，请合并为一行')
        result[address] = name or result.get(address, '')
    return [{'name': name, 'email': address} for address, name in result.items()]


def apply_settings(data, config_path):
    path = settings_path(config_path)
    if not path.exists():
        return
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('version') not in (1, 2):
            raise ValueError('schema')
        contacts = normalize_contacts(value.get('to') if value['version'] == 1 else value.get('contacts'))
    except (ValueError, OSError, UnicodeError):
        raise ConfigError('本机邮箱设置无法读取，请在“设置 → 收件人”重新保存') from None
    notification = data.setdefault('notifications', {})
    notification['to'] = ';'.join(c['email'] for c in contacts)
    notification['recipient_names'] = {c['email']: c['name'] for c in contacts}


def read_settings(config_path):
    config = Config.load(config_path)
    value = config.section('notifications').get('to', '')
    names = config.section('notifications').get('recipient_names', {})
    contacts = [{'name': names.get(email, ''), 'email': email} for email in parse_recipients(value)] if value else []
    return {'recipients': value, 'contacts': contacts, 'channel': config.section('notifications').get('channel', 'console'),
            'source': 'settings' if settings_path(config_path).exists() else 'config'}


def save_settings(config_path, value):
    contacts = normalize_contacts(value)
    if isinstance(value, str):
        # An older desktop build may still submit a flat list. Keep existing names.
        try:
            names = {c['email']: c['name'] for c in read_settings(config_path)['contacts']}
        except ConfigError:
            names = {}
        for contact in contacts:
            contact['name'] = names.get(contact['email'], '')
    # Only read base TOML: a corrupt override must remain repairable from the UI.
    config = Config.load(config_path, use_desktop_settings=False)
    path = settings_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.recipients-', suffix='.tmp', delete=False) as f:
            temporary = f.name
            json.dump({'version': 2, 'contacts': contacts}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return {'recipients': '; '.join(c['email'] for c in contacts), 'contacts': contacts,
            'count': len(contacts), 'source': 'settings',
            'channel': config.section('notifications').get('channel', 'console')}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['read', 'save'])
    parser.add_argument('--config', default='config.browser.local.toml')
    args = parser.parse_args(argv)
    try:
        if args.action == 'read':
            result = read_settings(args.config)
        else:
            request = json.loads(sys.stdin.read(65536))
            if not isinstance(request, dict):
                raise ConfigError('邮箱设置请求格式无效')
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
