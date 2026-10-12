"""Build one recipient's email without changing delivery eligibility or cadence."""
import hashlib
import base64
import re
import uuid
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from .config import parse_recipients
from .email_html import readable_time, render_email
from .subscription import normalize_policy


ASSET_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'email'
MAX_ASSET_BYTES = 1024 * 1024
DELIVERY_LABELS = {
    'immediate': '发现即发',
    'digest_30m': '每半小时汇总',
    'digest_daily': '每日 20:00 汇总',
    'quiet_night': '夜间免打扰（22:00–08:00）',
}


def message_preferences(config, recipient):
    """Never return addresses, names, or another subscriber's settings."""
    addresses = parse_recipients(recipient)
    if len(addresses) != 1:
        raise ValueError('每封邮件只能指定一个收件人')
    for contact in config.recipient_contacts or []:
        if contact['email'] == addresses[0]:
            return normalize_policy(contact)
    return normalize_policy({})


def mood_for_message(title, body):
    """Only trusted classification can pick a celebratory image.

    The first authored-text delimiter wins. Aggregate messages and auxiliary
    tests cannot inherit the first embedded post's status.
    """
    if any(value in title for value in ('本次线索汇总', '监控状态', '欢迎', '工作流测试')):
        return 'neutral'
    label = re.sub(r'^\[Codex[^\]]*\]\s*', '', title).strip()
    classification = label if label in ('待核实', '已重置候选', '即将重置候选',
                                       '重置券线索', '额度变化线索', '延期/撤回线索') else ''
    if not classification:
        offsets = [body.find(marker) for marker in ('\n\n作者原文：\n', '\n\n原文：\n')]
        offsets = [offset for offset in offsets if offset >= 0]
        metadata = body[:min(offsets)] if offsets else ''
        match = re.search(r'^分类：\s*([^\r\n]+?)\s*$', metadata, re.MULTILINE)
        classification = match.group(1) if match else ''
    if classification == '待核实':
        return 'uncertain'
    if classification == '已重置候选':
        return 'celebrate'
    return 'neutral'


def preferences_text(preferences):
    policy = normalize_policy(preferences)
    period = '未设到期日'
    if policy['start_at']:
        period = readable_time(policy['start_at']) + ' 至 ' + readable_time(policy['end_at'])
    return ('你的接收偏好\n'
            f"订阅开关：{'启用' if policy['enabled'] else '停用'}\n"
            f"发送偏好：{DELIVERY_LABELS[policy['delivery_mode']]}\n"
            f"待核实线索：{'接收' if policy['accept_uncertain'] else '不接收'}\n"
            f'订阅时间（北京时间）：{period}\n'
            '可选发送方式：' + ' / '.join(DELIVERY_LABELS.values()) + '\n'
            '待核实线索可开关；订阅期限可设置。\n'
            '上述为本次邮件生成时的设置；如需调整，请回复联系发送者。')


def _mascot_bytes(mood):
    # The only names are a closed internal set. Neither post text nor addresses
    # can supply a path. Missing optional assets must not prevent notifications.
    if mood not in ('uncertain', 'celebrate', 'neutral'):
        return None
    try:
        with (ASSET_DIR / (mood + '.gif')).open('rb') as source:
            payload = source.read(MAX_ASSET_BYTES + 1)
    except OSError:
        return None
    if len(payload) > MAX_ASSET_BYTES or not payload.startswith((b'GIF87a', b'GIF89a')):
        return None
    return payload


def build_message(config, row, recipient):
    """Assemble a plaintext + HTML alternative with one optional related GIF."""
    preferences = message_preferences(config, recipient)
    payload = _mascot_bytes(mood_for_message(row['title'], row['body']))
    message = EmailMessage()
    message['Subject'] = row['title']
    message['From'] = config.section('notifications')['username']
    message['To'] = recipient
    message['Date'] = formatdate(localtime=False)
    digest = hashlib.sha256((recipient + row['event_key']).encode()).hexdigest()
    message['Message-ID'] = f'<{digest}@codex-quota-monitor.local>'
    mascot_cid = f'quota-mascot.{uuid.uuid4().hex}@codex-quota-monitor.local' if payload is not None else None
    message.set_content(row['body'] + '\n\n' + preferences_text(preferences))
    message.add_alternative(render_email(
        row['title'], row['body'], preferences=preferences,
        mascot_cid=mascot_cid), subtype='html')
    if payload is not None:
        message.get_body(preferencelist=('html',)).add_related(
            payload, maintype='image', subtype='gif',
            cid=f'<{mascot_cid}>', disposition='inline')
    return message


def preview_html(message):
    """Render the exact MIME HTML locally, without requiring an external image host."""
    html = message.get_body(preferencelist=('html',)).get_content()
    for part in message.walk():
        if part.get('Content-ID'):
            cid = part['Content-ID'].strip('<>')
            data = base64.b64encode(part.get_payload(decode=True)).decode('ascii')
            html = html.replace('cid:' + cid, 'data:' + part.get_content_type() + ';base64,' + data)
    return html
