"""Create an offline synthetic email preview; never read config or send mail."""
from email.message import EmailMessage
from pathlib import Path

from .email_html import render_email


def main():
    title = '[Codex 邮件演示] 发现额度重置线索'
    body = ('分类：待核实\n依据：回复中出现额度重置线索，但尚未明确适用订阅和执行时间。\n'
            '账号：@demo / 演示账号（非真实公告）\n'
            '发布时间（北京时间）：2026-09-22T20:10:00+08:00\n'
            '采集时间（北京时间）：2026-09-22T20:30:00+08:00\n'
            '候选重置时间（北京时间）：未确定，需核对原文\n'
            '原帖：演示样例，无真实链接\n\n作者原文：\n'
            '“A reset-related update may arrive on Tuesday.”\n'
            '以上英文为合成示例，不是任何人的真实推文。\n\n'
            '演示上下文：\n用户询问额度重置时间，回复未明确订阅范围。\n'
            '仅作为待核实线索，不据此承诺重置或生成倒计时。')
    output = Path('data/email-preview')
    output.mkdir(parents=True, exist_ok=True)
    html = render_email(title + ' · 待核实', body)
    (output / 'email-demo.html').write_text(html, encoding='utf-8')
    message = EmailMessage()
    message['Subject'] = title
    message['From'] = 'preview@example.com'
    message['To'] = 'demo@example.com'
    message.set_content(body)
    message.add_alternative(html, subtype='html')
    (output / 'email-demo.eml').write_bytes(message.as_bytes())
    print(str((output / 'email-demo.html').resolve()))
    print('离线合成演示；未读取个人配置，未联网，未发送邮件。')


if __name__ == '__main__':
    main()
