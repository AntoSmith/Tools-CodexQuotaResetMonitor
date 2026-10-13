"""Local desktop preferences shared by the settings UI and CLI worker."""
import argparse
import json
import os
import re
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


def _read_document(config_path):
    path = settings_path(config_path)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] not in (1, 2, 3, 4):
            raise ValueError('schema')
    except (ValueError, OSError, UnicodeError):
        raise ConfigError('本机邮箱设置无法读取，请在“设置”中重新保存') from None
    return value


def _document_contacts(value):
    return normalize_contacts(value.get('to') if value['version'] == 1 else value.get('contacts'))


def _base_contacts(config):
    notification = config.section('notifications')
    if not isinstance(notification, dict):
        raise ConfigError('收件人名单格式无效')
    contacts = notification.get('recipient_contacts')
    if contacts is not None:
        return normalize_contacts(contacts)
    value = notification.get('to', '')
    if not isinstance(value, str):
        raise ConfigError('收件人名单格式无效')
    names = notification.get('recipient_names', {})
    if not isinstance(names, dict):
        names = {}
    # Hide only the shipped placeholder, and only before a contact is saved.
    if value.strip().lower() == 'recipient@example.com':
        return []
    return normalize_contacts([{'name': names.get(email, ''), 'email': email}
                               for email in parse_recipients(value)]) if value else []


def _valid_host(value):
    return (isinstance(value, str) and 1 <= len(value) <= 253 and all(
        re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
        for label in value.split('.')))


def _sender(value, *, optional=False):
    if optional and value == '':
        return ''
    if (not isinstance(value, str) or len(value) > 254 or any(c in value for c in ';；:：,，') or any(
            unicodedata.category(c).startswith('C') for c in value)):
        raise ConfigError('发件邮箱无效，请填写一个完整的邮箱地址')
    try:
        addresses = parse_recipients(value)
        if len(addresses) != 1:
            raise ConfigError('sender')
        return addresses[0]
    except ConfigError:
        raise ConfigError('发件邮箱无效，请填写一个完整的邮箱地址') from None


def _auth_code(value):
    if (not isinstance(value, str) or len(value) > 512 or any(
            unicodedata.category(c).startswith('C') for c in value)):
        raise ConfigError('授权码格式无效：最多 512 字符，不允许换行或控制字符')
    return value.strip()


def _secret_override(value):
    """Recover a valid secret even when other transport fields need repair."""
    if not isinstance(value, dict):
        return {}
    if value.get('clear_auth_code') is True:
        return {'clear_auth_code': True}
    try:
        code = _auth_code(value.get('auth_code', ''))
    except ConfigError:
        return {}
    return {'auth_code': code} if code else {}


def _normalize_transport(value, previous=None):
    if not isinstance(value, dict):
        raise ConfigError('邮件发送设置格式无效')
    channel = value.get('channel')
    if channel not in ('smtp', 'console'):
        raise ConfigError('邮件发送方式仅支持 smtp/console')
    host, port = value.get('host'), value.get('port')
    if not _valid_host(host):
        raise ConfigError('SMTP 主机无效，请填写单行 ASCII 主机名')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError('SMTP 端口必须是 1–65535 的整数')
    username = _sender(value.get('username'), optional=channel == 'console')
    clear = value.get('clear_auth_code', False)
    if type(clear) is not bool:
        raise ConfigError('清除授权码选项必须为 true/false')
    code = _auth_code(value.get('auth_code', ''))
    if clear and code:
        raise ConfigError('清除授权码时请将授权码输入留空')
    secret = {'clear_auth_code': True} if clear else ({'auth_code': code} if code else _secret_override(previous))
    return dict(channel=channel, host=host, port=port, username=username, **secret)


def _apply_transport(data, transport):
    if not isinstance(data.get('notifications'), dict):
        data['notifications'] = {}
    notification = data['notifications']
    notification.update({key: transport[key] for key in ('channel', 'host', 'port', 'username')})
    if transport.get('clear_auth_code') is True:
        # Empty overrides an old TOML value, while preserving Config's env fallback.
        notification['QQ_SMTP_AUTH_CODE'] = ''
    elif 'auth_code' in transport:
        notification['QQ_SMTP_AUTH_CODE'] = transport['auth_code']


def _known_accounts(config):
    values = config.accounts
    if not isinstance(values, list):
        raise ConfigError('基础配置的监控账号格式无效，请核对账号和数字 ID')
    result, ids = {}, set()
    for value in values:
        if (not isinstance(value, dict) or not isinstance(value.get('username'), str) or not
                re.fullmatch(r'[A-Za-z0-9_]{1,15}', value['username']) or not
                re.fullmatch(r'[0-9]+', str(value.get('id', ''))) or value.get('kind') not in ('personal', 'organization')):
            raise ConfigError('基础配置的监控账号格式无效，请核对账号和数字 ID')
        name, identity = value['username'].lower(), str(value['id'])
        if name in result or identity in ids:
            raise ConfigError('基础配置的监控账号重复，请核对账号和数字 ID')
        result[name] = value
        ids.add(identity)
    return result


def _normalize_collection(value, config, *, stored=False):
    if not isinstance(value, dict) or value.get('browser_channel') not in ('chrome', 'msedge'):
        raise ConfigError('采集浏览器仅支持 chrome/msedge')
    accounts = value.get('accounts')
    if not isinstance(accounts, list):
        raise ConfigError('采集账号设置必须是列表')
    known, enabled = _known_accounts(config), {}
    for account in accounts:
        if (not isinstance(account, dict) or not isinstance(account.get('username'), str)
                or type(account.get('enabled')) is not bool):
            raise ConfigError('采集账号须包含账号名称和 true/false 启用状态')
        name = account['username'].lower()
        if name not in known:
            raise ConfigError('只能选择基础配置中已列出的监控账号')
        if name in enabled:
            raise ConfigError('采集账号不能重复选择')
        if not stored and any(key not in ('username', 'enabled') for key in account):
            raise ConfigError('监控账号的数字 ID 和身份不能在此修改')
        if stored and (type(account.get('id')) is not str or account['id'] != str(known[name]['id'])):
            raise ConfigError('监控账号的数字 ID 已变化，请重新核对并保存采集设置')
        enabled[name] = account['enabled']
    selected = sum(enabled.values())
    if not selected:
        raise ConfigError('请至少选择并核对一个监控账号')
    if selected > 10:
        raise ConfigError('最多选择 10 个监控账号')
    return {'browser_channel': value['browser_channel'], 'accounts': [
        {'username': account['username'], 'id': str(account['id']), 'enabled': enabled.get(name, False)}
        for name, account in known.items()]}


def _apply_collection(data, collection):
    enabled = {account['username'].lower() for account in collection['accounts'] if account['enabled']}
    data['accounts'] = [{**account, 'verified': True} for account in data.get('accounts', [])
                        if account['username'].lower() in enabled]
    if not isinstance(data.get('browser'), dict):
        data['browser'] = {}
    data['browser']['channel'] = collection['browser_channel']


def apply_settings(data, config_path):
    value = _read_document(config_path)
    if value is None:
        return
    contacts = _document_contacts(value)
    if value['version'] == 4:
        if 'transport' in value:
            _apply_transport(data, _normalize_transport(value['transport']))
        if 'collection' in value:
            config = Config(data, Path(config_path).resolve().parent)
            _apply_collection(data, _normalize_collection(value['collection'], config, stored=True))
    if not isinstance(data.get('notifications'), dict):
        data['notifications'] = {}
    notification = data['notifications']
    notification['to'] = ';'.join(c['email'] for c in contacts)
    notification['recipient_names'] = {c['email']: c['name'] for c in contacts}
    notification['recipient_contacts'] = contacts


def read_settings(config_path):
    # The GUI must display the saved list while other setup sections are being
    # repaired. Loading a worker config here could hide valid contacts and let a
    # later save overwrite them. A corrupt contact section still raises an error.
    config = Config.load(config_path, use_desktop_settings=False)
    document = _read_document(config_path)
    contacts = _document_contacts(document) if document is not None else _base_contacts(config)
    return {'recipients': ';'.join(contact['email'] for contact in contacts), 'contacts': contacts,
            'channel': read_setup(config_path)['transport']['channel'],
            'source': 'settings' if document is not None else 'config'}


def _write_document(config_path, document):
    path = settings_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.settings-', suffix='.tmp', delete=False) as f:
            temporary = f.name
            json.dump(document, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _prepare_document(config_path, config):
    try:
        previous = _read_document(config_path)
    except ConfigError:
        previous = None
    try:
        contacts = _document_contacts(previous) if previous else _base_contacts(config)
    except ConfigError:
        try:
            contacts = _base_contacts(config)
        except ConfigError:
            contacts = []
    document = {'version': 4, 'contacts': contacts}
    if previous and previous['version'] == 4:
        for key in ('transport', 'collection'):
            if key in previous:
                document[key] = previous[key]
    return document


def _reconcile_saved_subscriptions(config_path, config):
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


def save_settings(config_path, value):
    contacts = normalize_contacts(value)
    # Only read base TOML: a corrupt override must remain repairable from the UI.
    config = Config.load(config_path, use_desktop_settings=False)
    document = _prepare_document(config_path, config)
    if isinstance(value, str):
        # An older desktop build may still submit a flat list. Keep names/policy.
        previous = {contact['email']: contact for contact in document['contacts']}
        contacts = [previous.get(contact['email'], contact) for contact in contacts]
    document['contacts'] = contacts
    _write_document(config_path, document)
    _reconcile_saved_subscriptions(config_path, config)
    return {'recipients': '; '.join(c['email'] for c in contacts), 'contacts': contacts,
            'count': len(contacts), 'source': 'settings', 'channel': read_setup(config_path)['transport']['channel']}


def _transport_view(config, override=None):
    notification = config.section('notifications')
    repair = not isinstance(notification, dict)
    if repair:
        notification = {}
    channel = notification.get('channel', 'console')
    host, port, username = notification.get('host', 'smtp.qq.com'), notification.get('port', 465), notification.get('username', '')
    if channel not in ('smtp', 'console'):
        channel, repair = 'console', True
    if not _valid_host(host):
        host, repair = 'smtp.qq.com', True
    if type(port) is not int or not 1 <= port <= 65535:
        port, repair = 465, True
    try:
        username = _sender(username, optional=True)
    except ConfigError:
        username, repair = '', True
    if username.lower() == 'sender@example.com':
        username = ''
    if override is not None:
        channel, host, port, username = (override[key] for key in ('channel', 'host', 'port', 'username'))
        repair = False
    try:
        base_code = _auth_code(notification.get('QQ_SMTP_AUTH_CODE', ''))
    except ConfigError:
        base_code = ''
        repair = override is None or not _secret_override(override)
    code, source = base_code, 'config' if base_code else 'none'
    if override is not None and override.get('clear_auth_code') is True:
        code, source = '', 'none'
    elif override is not None and override.get('auth_code'):
        code, source = override['auth_code'], 'settings'
    if not code:
        try:
            code = _auth_code(os.environ.get('QQ_SMTP_AUTH_CODE', ''))
        except ConfigError:
            code, repair = '', True
        source = 'environment' if code else 'none'
    return dict(channel=channel, host=host, port=port, username=username,
                auth_code_present=bool(code), auth_code_source=source), repair


def _collection_view(config, override=None):
    browser = config.section('browser')
    repair = not isinstance(browser, dict)
    channel = browser.get('channel', 'chrome') if isinstance(browser, dict) else 'chrome'
    repair = repair or channel not in ('chrome', 'msedge')
    if repair:
        channel = 'chrome'
    try:
        known = _known_accounts(config)
    except ConfigError:
        return {'browser_channel': channel, 'accounts': []}, True
    choices = None
    if override is not None:
        channel, repair = override['browser_channel'], False
        choices = {account['username'].lower(): account['enabled'] for account in override['accounts']}
    accounts = []
    for name, account in known.items():
        enabled = account.get('verified') is True if choices is None else choices.get(name, False)
        accounts.append(dict(username=account['username'], id=str(account['id']), kind=account['kind'],
                             enabled=enabled, verified=account.get('verified') is True or enabled))
    return {'browser_channel': channel, 'accounts': accounts}, repair


def _readiness(config_path, transport, repair):
    missing = []
    if repair:
        missing.append('本机设置修复')
    try:
        config = Config.load(config_path)
    except (ValueError, OSError, TypeError, AttributeError):
        if '本机设置修复' not in missing:
            missing.append('本机设置修复')
        return False, missing
    accounts = config.accounts
    if (not isinstance(accounts, list) or not accounts or any(
            not isinstance(account, dict) or account.get('verified') is not True for account in accounts)):
        missing.append('监控账号')
    notification = config.section('notifications')
    if isinstance(notification, dict) and notification.get('channel', 'console') == 'smtp':
        if not transport['username'] or transport['username'].lower() == 'sender@example.com':
            missing.append('发件邮箱')
        if not transport['auth_code_present']:
            missing.append('SMTP 授权码')
        try:
            recipients = config.recipients
        except (ValueError, TypeError, AttributeError):
            recipients = []
        if not any(address.lower() != 'recipient@example.com' for address in recipients):
            missing.append('收件人')
    try:
        # Keep the worker's existing validation as the final readiness check.
        config.validate_browser()
    except (ValueError, TypeError, AttributeError):
        missing.append('采集设置')
    return not missing, missing


def read_setup(config_path):
    """Read safe setup fields even when the worker must reject a bad override."""
    config = Config.load(config_path, use_desktop_settings=False)
    repair, transport, collection = False, None, None
    try:
        document = _read_document(config_path)
    except ConfigError:
        document, repair = None, True
    if document is not None:
        try:
            _document_contacts(document)
        except ConfigError:
            repair = True
        if document['version'] == 4:
            if 'transport' in document:
                try:
                    transport = _normalize_transport(document['transport'])
                except ConfigError:
                    repair = True
            if 'collection' in document:
                try:
                    collection = _normalize_collection(document['collection'], config, stored=True)
                except ConfigError:
                    repair = True
    transport, transport_repair = _transport_view(config, transport)
    collection, collection_repair = _collection_view(config, collection)
    repair = repair or transport_repair or collection_repair
    ready, missing = _readiness(config_path, transport, repair)
    return {'transport': transport, 'collection': collection,
            'source': 'settings' if settings_path(config_path).exists() else 'config',
            'repair_needed': repair, 'ready': ready, 'missing_steps': missing}


def save_setup(config_path, request):
    if not isinstance(request, dict) or not any(key in request for key in ('transport', 'collection')):
        raise ConfigError('请提供邮件发送设置或采集设置')
    config = Config.load(config_path, use_desktop_settings=False)
    document = _prepare_document(config_path, config)
    if 'transport' in request:
        document['transport'] = _normalize_transport(request['transport'], document.get('transport'))
    if 'collection' in request:
        document['collection'] = _normalize_collection(request['collection'], config)
    _write_document(config_path, document)
    if 'transport' in request:
        _reconcile_saved_subscriptions(config_path, config)
    return read_setup(config_path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['read', 'save', 'test', 'read-setup', 'save-setup'])
    parser.add_argument('--config', default='config.browser.local.toml')
    args = parser.parse_args(argv)
    try:
        if args.action in ('read', 'read-setup'):
            result = read_setup(args.config) if args.action == 'read-setup' else read_settings(args.config)
        else:
            request = json.loads(sys.stdin.read(65536))
            if not isinstance(request, dict):
                raise ConfigError('邮箱设置请求格式无效')
            if args.action == 'save-setup':
                result = save_setup(args.config, request)
            elif args.action == 'test':
                from .subscription_test import send_test
                result = send_test(args.config, request.get('email'))
            else:
                result = save_settings(args.config, request['contacts'] if 'contacts' in request else request.get('recipients'))
        print(json.dumps({'ok': True, **result}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        # Never return config contents, secrets or raw filesystem exception details.
        error = str(exc) if isinstance(exc, ConfigError) else '无法读写邮箱设置，请检查本地配置文件和目录权限'
        print(json.dumps({'ok': False, 'error': error}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
