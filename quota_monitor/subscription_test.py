"""Explicit, single-recipient welcome test; never touches collection or its outbox."""
import re
import uuid

from .config import Config, ConfigError, parse_recipients
from .desktop_settings import read_settings
from .notify import SMTPNotifier


def send_test(config_path, email, *, notifier=None):
    addresses = parse_recipients(email)
    if len(addresses) != 1:
        raise ConfigError('测试只能选择一位已保存的收件人')
    email = addresses[0]
    contacts = read_settings(config_path)['contacts']
    contact = next((c for c in contacts if c['email'] == email), None)
    if contact is None:
        raise ConfigError('请先保存此收件人，再发送测试邮件')
    config = Config.load(config_path)
    settings = config.section('notifications')
    settings['channel'] = 'smtp'
    config.validate_smtp_auth()
    sender = settings.get('username', '')
    if len(parse_recipients(sender)) != 1 or any(c in sender for c in ';；:：,，'):
        raise ConfigError('发件邮箱配置无效')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', settings.get('host', 'smtp.qq.com')):
        raise ConfigError('SMTP host 无效')
    port = settings.get('port', 465)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError('SMTP port 无效')
    modes = {'immediate': '发现即发', 'digest_30m': '每半小时汇总',
             'digest_daily': '每日 20:00 汇总', 'quiet_night': '夜间免打扰（22:00–08:00）'}
    period = '未设到期日' if not contact['end_at'] else contact['start_at'] + ' 至 ' + contact['end_at']
    body = (f"你好，{contact['name'] or '订阅者'}！\n\n"
            '这是一封收信测试，非重置公告。\n'
            '如果你收到此邮件，请回复确认；也可以检查垃圾邮件文件夹并将发件人加入通讯录。\n\n'
            f"当前订阅开关：{'启用' if contact['enabled'] else '停用'}\n"
            f"订阅时间（北京时间）：{period}\n"
            f"待核实线索：{'接收' if contact['accept_uncertain'] else '不接收'}\n"
            f"发送偏好：{modes[contact['delivery_mode']]}\n\n"
            '本次测试不会开通、续期或修改你的订阅。\n'
            '本服务为非官方信息提醒，不提供额度充值，不保证重置发生。需要调整或停止提醒，请回复联系发送方。')
    row = {'event_key': 'welcome-test:' + uuid.uuid4().hex,
           'title': '[Codex] 欢迎 · 邮箱收信测试', 'body': body}
    try:
        (notifier or SMTPNotifier(config)).send_to(row, email)
    except Exception:
        raise ConfigError('发送结果未确认，请先检查收件箱和垃圾邮件，再决定是否重试；未自动重试') from None
    return {'status': 'smtp_accepted', 'message': 'SMTP 服务器已接受测试邮件，请让对方确认收件（含垃圾邮件）。'}
