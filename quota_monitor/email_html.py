"""Email-safe presentation only; notification classification stays in service.py."""
import re
from datetime import datetime, timedelta, timezone
from html import escape


def readable_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return value
        return stamp.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, AttributeError):
        return value


def block(value):
    return escape(value).replace('\n', '<br>')


DIVIN_KEYS = {'第三方事件类型', '第三方覆盖范围', '第三方重置原因', '第三方事件阶段',
              '第三方判断依据', '第三方采集时间', '第三方参考来源', '第三方证据链接'}


def divin_card(fields):
    """Display third-party context without changing notification classification."""
    if fields.get('第三方参考来源') != 'https://divin.cc/':
        return ''
    rows = []
    for label, key in [('事件类型', '第三方事件类型'), ('覆盖范围', '第三方覆盖范围'),
                       ('重置原因', '第三方重置原因'), ('事件阶段', '第三方事件阶段'),
                       ('判断依据', '第三方判断依据'), ('采集于', '第三方采集时间')]:
        value = fields.get(key)
        if value:
            if key == '第三方采集时间':
                value = readable_time(value)
            rows.append(f'<tr><td valign="top" style="padding:5px 12px 5px 0;color:#7b7890;font-size:12px;width:72px;">{label}</td>'
                        f'<td style="padding:5px 0;color:#4b4662;font-size:13px;line-height:1.8;overflow-wrap:anywhere;">{escape(value)}</td></tr>')
    evidence_urls = []
    for url in fields.get('第三方证据链接', '').split():
        if re.fullmatch(r'https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]+', url) and url not in evidence_urls:
            evidence_urls.append(url)
            if len(evidence_urls) == 6:
                break
    links = ['<a href="https://divin.cc/" style="color:#665b8c;text-decoration:underline;">查看 divin.cc 整理 ↗</a>']
    links.extend(f'<a href="{escape(url, quote=True)}" style="color:#665b8c;text-decoration:underline;">证据原帖 {index} ↗</a>'
                 for index, url in enumerate(evidence_urls, 1))
    return ('<div style="margin-top:24px;border:1px solid #e3deed;border-radius:10px;padding:18px 20px;background:#f8f7fb;">'
            '<p style="margin:0 0 10px;color:#5b5277;font-size:13px;font-weight:bold;">事件补充 · divin.cc 整理</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table>'
            '<p style="margin:12px 0 0;color:#817b91;font-size:11px;line-height:1.8;">第三方整理，未作为官方确认或自动分类依据</p>'
            f'<p style="margin:10px 0 0;font-size:12px;line-height:2;">{" &nbsp; · &nbsp; ".join(links)}</p></div>')


def preference_card(preferences):
    if preferences is None:
        return ''
    from .email_message import DELIVERY_LABELS
    mode = DELIVERY_LABELS.get(preferences.get('delivery_mode'), '未设置')
    until = readable_time(preferences.get('end_at', '')) or '长期有效'
    status = '启用' if preferences.get('enabled', True) else '已停用'
    uncertain = '接收' if preferences.get('accept_uncertain', True) else '不接收'
    rows = [('发送方式', mode), ('待核实线索', uncertain), ('订阅状态', status), ('订阅到期', until)]
    options = ''.join('<span style="display:inline-block;margin:0 6px 7px 0;padding:5px 8px;border:1px solid #d2e5e1;'
                      f'border-radius:6px;background:#ffffff;color:#406c69;font-size:11px;">{escape(label)}</span>'
                      for label in DELIVERY_LABELS.values())
    content = ''.join(f'<tr><td style="padding:5px 12px 5px 0;color:#71858c;font-size:12px;width:84px;">{key}</td>'
                      f'<td style="padding:5px 0;color:#264e58;font-size:12px;">{escape(value)}</td></tr>' for key, value in rows)
    return ('<div style="margin-top:24px;border:1px solid #dfe9eb;border-radius:12px;padding:18px 20px;background:#f6faf9;">'
            '<p style="margin:0 0 10px;color:#254d56;font-weight:bold;font-size:13px;">你的接收偏好</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{content}</table>'
            '<div style="border-top:1px solid #dfe9eb;margin-top:14px;padding-top:14px;">'
            '<p style="margin:0 0 10px;color:#406c69;font-size:12px;font-weight:bold;">按你的习惯接收 · 可选发送方式</p>'
            f'{options}<p style="margin:2px 0 0;color:#637e85;font-size:11px;line-height:1.8;">待核实线索可开关 · 订阅期限可设置</p></div>'
            '<p style="margin:9px 0 0;color:#7d9095;font-size:11px;line-height:1.7;">时间均为北京时间。调整偏好或停止提醒，可回复此邮件联系发送者。</p></div>')


def render_email(title, body, *, preferences=None, mascot_cid=None):
    """Extract only generated metadata BEFORE the authored text delimiter.

    No HTML or URLs from original posts are interpreted as markup. Every claim,
    source limitation and context disclaimer remains visible in the HTML part.
    """
    prefix, original = body, None
    markers = [(body.index(marker), marker) for marker in ('\n\n作者原文：\n', '\n\n原文：\n') if marker in body]
    digest = title.startswith('[Codex] 本次线索汇总')
    if markers and not digest:
        _, marker = min(markers)
        prefix, original = body.split(marker, 1)
    fields, notes = {}, []
    keys = {'分类', '依据', '要点', '原文模式', '账号', '原帖', '发布时间（北京时间）', '采集时间（北京时间）',
            '发布至采集延迟', '候选重置时间（北京时间）'} | DIVIN_KEYS
    if original is not None:
        for line in prefix.split('\n'):
            key, separator, value = line.partition('：')
            if separator and key in keys and key not in fields:
                fields[key] = value.strip()
            elif line.strip():
                notes.append(line)
    third_party = divin_card(fields)
    if not third_party:
        notes.extend(f'{key}：{value}' for key, value in fields.items() if key in DIVIN_KEYS)
    demo = '演示' in title or '测试' in title
    label = '线索汇总' if digest else '监控消息'
    color, tint = '#255b74', '#eaf3f7'
    for candidate in ('待核实', '即将重置候选', '已重置候选', '重置券线索', '额度变化线索', '延期/撤回线索'):
        if candidate in title or fields.get('分类') == candidate:
            label = candidate
            break
    if label in ('待核实', '延期/撤回线索'):
        color, tint = '#94620d', '#fff5dd'
    elif label == '已重置候选':
        color, tint = '#127060', '#e8f6f0'
    headline = re.sub(r'^\[Codex[^\]]*\]\s*', '', title).strip() or '监控状态通知'
    if headline == label:
        headline = {'待核实':'有条重置线索，值得留意', '已重置候选':'重置消息来了',
                    '即将重置候选':'新的重置预告', '重置券线索':'发现重置券线索'}.get(label, headline)
    intro = fields.get('依据', '请查看下方消息内容及原始出处。')
    takeaway = fields.get('要点')
    url = fields.get('原帖', '')
    valid_url = re.fullmatch(r'https://x\.com/(?:[A-Za-z0-9_]{1,15}|i/web)/status/[0-9]+', url)
    button = (f'<a href="{escape(url, quote=True)}" style="display:inline-block;background:#193d4a;color:#ffffff;'
              'text-decoration:none;padding:14px 24px;border-radius:10px;font-size:15px;font-weight:bold;">查看 X 原帖 &nbsp; ↗</a>') if valid_url else ''
    if '演示' in title and not button:
        button = '<span style="display:inline-block;background:#193d4a;color:#ffffff;padding:14px 24px;border-radius:10px;font-size:15px;">查看原帖 · 演示按钮</span>'
    metadata = ''
    for name, key in [('发布于', '发布时间（北京时间）'), ('采集于', '采集时间（北京时间）')]:
        if fields.get(key):
            metadata += (f'<tr><td style="padding:8px 12px 8px 0;color:#71828a;width:72px;font-size:13px;">{name}</td>'
                         f'<td style="padding:8px 0;color:#213b45;font-size:14px;">{escape(readable_time(fields[key]))} <span style="font-size:11px;color:#71828a;">北京时间</span></td></tr>')
    timing = ''
    if metadata:
        timing = f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-top:1px solid #e3ebee;margin-top:20px;">{metadata}</table>'
    reset = fields.get('候选重置时间（北京时间）')
    if reset:
        timing += f'<p style="font-size:12px;line-height:1.8;color:#637980;margin:12px 0 0;">候选重置时间（北京时间）：{escape(readable_time(reset))}</p>'
    latency = fields.get('发布至采集延迟')
    if latency:
        timing += f'<p style="font-size:12px;color:#637980;margin:5px 0 0;">发布至采集延迟：{escape(latency)}</p>'
    author = fields.get('账号', '')
    content = original if original is not None else body
    source_notes = ('<div style="font-size:11px;line-height:1.8;color:#75888f;margin-top:20px;">'
                    + block('\n'.join(notes)) + '</div>') if notes else ''
    demo_banner = ('<div style="background:#fff2cf;color:#825817;padding:12px 20px;text-align:center;font-size:13px;font-weight:bold;">'
                   '演示 / 测试邮件 · 不代表当前发生额度重置</div>') if demo else ''
    companion = ''
    if isinstance(mascot_cid, str) and re.fullmatch(r'quota-mascot\.[0-9a-f]{32}@codex-quota-monitor\.local', mascot_cid):
        from .email_message import mood_for_message
        mood = mood_for_message(title, body)
        caption, description = {
            'uncertain': ('等待进一步确认', '先记下这条线索，具体范围与执行时间还要看后续。'),
            'celebrate': ('作者已声明重置', '有好消息！是否适用于你，仍请查看实际账户额度。'),
            'neutral': ('留意后续消息', '原文与来源都在这里，可以随时回去核对。'),
        }[mood]
        companion = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;border-top:1px dashed #d8e5e5;"><tr>'
                     f'<td valign="middle" style="padding:14px 8px 0 0;"><p style="font-size:13px;color:#2f5960;font-weight:bold;margin:0 0 7px;">{caption}</p>'
                     f'<p style="font-size:11px;line-height:1.8;color:#71898e;margin:0;">{description}</p></td>'
                     f'<td width="164" valign="bottom" style="padding-top:4px;"><img src="cid:{mascot_cid}" width="164" height="98" alt="额度小助手：{caption}" '
                     'style="display:block;width:164px;height:98px;border:0;"></td></tr></table>')
    original_label = '原文节选 · 完整内容见原帖' if fields.get('原文模式') == '节选' else '原文与对话上下文'
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>@media only screen and (max-width:600px){{.outer{{padding:16px 10px!important}}.inner{{padding:24px 20px!important}}.headline{{font-size:25px!important}}}}</style></head>
<body style="margin:0;background:#eef3f4;color:#213b45;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef3f4;"><tr><td class="outer" align="center" style="padding:36px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:660px;background:#ffffff;border:1px solid #dfe8eb;border-radius:18px;overflow:hidden;">
<tr><td style="background:#153b45;padding:24px 30px;color:#ffffff;">
<span style="display:inline-block;font-size:27px;vertical-align:middle;margin-right:10px;color:#85dbc2;">↻</span>
<span style="font-weight:bold;font-size:19px;vertical-align:middle;">Codex 额度提醒</span>
<div style="margin:7px 0 0 38px;font-size:12px;color:#b2ccd2;letter-spacing:1px;">关注额度变化 · 保留原文出处</div>
</td></tr><tr><td>{demo_banner}</td></tr>
<tr><td class="inner" style="padding:32px;">
<span style="display:inline-block;background:{tint};color:{color};padding:6px 11px;border-radius:6px;font-size:12px;font-weight:bold;">{escape(label)}</span>
<h1 class="headline" style="font-size:30px;line-height:1.4;letter-spacing:-0.5px;color:#193b46;margin:17px 0 12px;">{escape(headline)}</h1>
<p style="font-size:15px;line-height:1.9;color:#536e77;margin:0;">{escape(intro)}</p>
{f'<p style="font-size:15px;line-height:1.9;color:#254c56;margin:16px 0 0;padding:14px 16px;background:#f1f8f5;border-radius:10px;">{escape(takeaway)}</p>' if takeaway else ''}
{timing}
{third_party}
<div style="margin-top:26px;border:1px solid #dfe9ec;border-left:4px solid #81b6aa;border-radius:10px;padding:20px;background:#f8fbfb;">
<p style="font-size:12px;letter-spacing:1px;color:#637e85;font-weight:bold;margin:0 0 10px;">{original_label if original is not None else '消息内容'}</p>
{f'<p style="font-size:12px;color:#526f77;margin:0 0 14px;">{escape(author)}</p>' if author else ''}
<div style="font-size:14px;line-height:1.9;color:#25454e;overflow-wrap:anywhere;word-break:break-word;">{block(content)}</div>
{companion}
</div>
{f'<div style="margin-top:24px;">{button}</div>' if button else ''}
{preference_card(preferences)}
{source_notes}
</td></tr><tr><td class="inner" style="padding:22px 32px;background:#f5f8f9;border-top:1px solid #e3ebee;">
<p style="margin:0;font-size:12px;line-height:1.9;color:#657b82;">本服务提供消息线索，不提供额度充值或保证重置。请结合原文、订阅范围及实际账户状态判断。</p>
<p style="margin:8px 0 0;font-size:11px;line-height:1.8;color:#7b8e94;">独立消息提醒工具 · 非 OpenAI 官方服务<br>如需停止接收，请回复本邮件联系发送者。</p>
</td></tr></table>
<p style="font-size:11px;color:#7d939a;margin:20px 0 0;">CODEX QUOTA MONITOR &nbsp; / &nbsp; {'模板演示，非真实收件记录' if '演示' in title else '手动邮件测试，非重置公告' if demo else '原文可追溯，线索待核验'}</p>
</td></tr></table></body></html>'''
