"""Presentation of attributed source assessments, independent of classification."""
from .divin_feed import normalize_assessment
from datetime import datetime, timedelta, timezone
import re


HEADER = '事件补充（divin.cc 第三方整理，未作为官方确认或自动分类依据）：'
FIELDS = [('第三方事件类型', 'event_type'), ('第三方覆盖范围', 'scope'),
          ('第三方重置原因', 'reason'), ('第三方事件阶段', 'stage'),
          ('第三方判断依据', 'analysis_basis'), ('第三方采集时间', 'fetched_at'),
          ('第三方参考来源', 'source_url')]


def _generated_prefix_end(body):
    """Recognize only our complete initial metadata format, never arbitrary prose."""
    if not body.startswith(HEADER + '\n'):
        return 0
    end = body.find('\n\n')
    if end < 0 or end > 5000:
        return 0
    labels = [label for label, _ in FIELDS] + ['第三方证据链接']
    values, previous = {}, -1
    for line in body[len(HEADER) + 1:end].split('\n'):
        label, sep, value = line.partition('：')
        if not sep or label not in labels or not value or value != value.strip():
            return 0
        position = labels.index(label)
        if position <= previous:
            return 0
        previous = position
        values[label] = value
        if label == '第三方采集时间':
            try:
                datetime.strptime(value, '%Y-%m-%d %H:%M:%S（北京时间）')
            except ValueError:
                return 0
        if label == '第三方证据链接':
            links = value.split(' ')
            if len(links) > 6 or any(not re.fullmatch(r'https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,25}', link)
                                     for link in links):
                return 0
    if (not values.get('第三方覆盖范围') or not values.get('第三方重置原因')
            or values.get('第三方参考来源') != 'https://divin.cc/'):
        return 0
    return end + 2


def assessment_prefix(value):
    value = normalize_assessment(value)
    if not value:
        return ''
    value['event_type'] = {'usage_reset': '额度重置', 'reset_card': '重置卡发放',
                           'credit_grant': '额外额度发放'}.get(value['event_type'], value['event_type'])
    value['stage'] = {'scheduled': '预告', 'rolling_out': '推送中',
                      'completed': '已完成'}.get(value['stage'], value['stage'])
    value['scope'] = value['scope'] or '范围未说明'
    value['reason'] = value['reason'] or '原因待确认'
    if value.get('fetched_at'):
        value['fetched_at'] = datetime.fromisoformat(value['fetched_at'].replace('Z', '+00:00')).astimezone(
            timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）'
    lines = [HEADER]
    lines += [f'{label}：{value[key]}' for label, key in FIELDS if value.get(key)]
    if value.get('source_urls'):
        lines.append('第三方证据链接：' + ' '.join(value['source_urls']))
    return '\n'.join(lines) + '\n\n'


def with_assessment(body, value):
    prefix = assessment_prefix(value)
    if not prefix:
        return body
    end = _generated_prefix_end(body)
    if end:
        # Replace only our generated initial block, never anything in author text.
        body = body[end:]
    return prefix + body
