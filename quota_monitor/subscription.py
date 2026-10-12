"""Local subscriber policies. Collection cadence is deliberately unrelated."""
import re
from datetime import datetime, timedelta, timezone

from .config import ConfigError


BEIJING = timezone(timedelta(hours=8))
MODES = {'immediate', 'digest_30m', 'digest_daily', 'quiet_night'}


def normalize_policy(contact):
    if not isinstance(contact, dict):
        raise ConfigError('订阅设置必须是联系人对象')
    result = {}
    for field in ('enabled', 'accept_uncertain'):
        value = contact.get(field, True)
        if type(value) is not bool:
            raise ConfigError('订阅开关必须为 true/false：' + field)
        result[field] = value
    start, end = contact.get('start_at', ''), contact.get('end_at', '')
    if not isinstance(start, str) or not isinstance(end, str):
        raise ConfigError('订阅起止时间必须为字符串')
    if bool(start) != bool(end):
        raise ConfigError('请同时填写订阅开始和结束时间，或同时留空表示长期')
    if start:
        try:
            dates = [datetime.fromisoformat(value) for value in (start, end)]
            if any(value.tzinfo is None or value.utcoffset() is None for value in dates):
                raise ValueError()
            if dates[1] <= dates[0]:
                raise ValueError()
            start, end = [value.astimezone(BEIJING).isoformat(timespec='seconds') for value in dates]
        except (ValueError, OverflowError):
            raise ConfigError('订阅日期须为带时区的有效时间，结束时间必须晚于开始时间') from None
    result.update(start_at=start, end_at=end)
    mode = contact.get('delivery_mode', 'immediate')
    if not isinstance(mode, str) or mode not in MODES:
        raise ConfigError('不支持的邮件发送偏好')
    result['delivery_mode'] = mode
    return result


def active_reason(policy, now):
    """Empty reason means active; start inclusive and end exclusive."""
    if not policy['enabled']:
        return 'disabled'
    if policy['start_at']:
        if now < datetime.fromisoformat(policy['start_at']).timestamp():
            return 'not_started'
        if now >= datetime.fromisoformat(policy['end_at']).timestamp():
            return 'expired'
    return ''


def accepts(policy, row, now):
    if active_reason(policy, now):
        return False
    # Renewing a subscription must not make notices from before its start eligible.
    if policy['start_at'] and row.get('created') is not None:
        if row['created'] < datetime.fromisoformat(policy['start_at']).timestamp():
            return False
    return policy['accept_uncertain'] or not is_uncertain(row)


def is_uncertain(row):
    # Titles originate in service.py. For auxiliary titles, inspect only generated
    # metadata before the first authored-text marker, never a quoted post's text.
    if row['title'] == '[Codex] 待核实':
        return True
    body = row['body']
    markers = [body.find(marker) for marker in ('\n\n作者原文：\n', '\n\n原文：\n')]
    markers = [position for position in markers if position >= 0]
    metadata = body[:min(markers)] if markers else ''
    return re.search(r'^分类：\s*待核实\s*$', metadata, re.MULTILINE) is not None


def scheduled_at(mode, earliest):
    value = datetime.fromtimestamp(earliest, BEIJING)
    if mode == 'immediate':
        return earliest
    if mode == 'digest_30m':
        # A newly found event at the exact boundary belongs to the next bucket.
        return ((int(earliest) // 1800) + 1) * 1800
    if mode == 'digest_daily':
        target = value.replace(hour=20, minute=0, second=0, microsecond=0)
        if target.timestamp() <= earliest:
            target += timedelta(days=1)
        return target.timestamp()
    if mode == 'quiet_night':
        if value.hour < 8:
            return value.replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        if value.hour >= 22:
            return (value + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        return earliest
    raise ConfigError('不支持的邮件发送偏好')
