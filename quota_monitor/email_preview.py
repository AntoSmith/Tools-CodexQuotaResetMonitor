"""Create an offline synthetic email preview; never read config or send mail."""
from pathlib import Path

from .email_message import build_message, preview_html
from .config import Config


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
    config = Config({'notifications': {'username':'preview@example.com', 'recipient_contacts':[
        {'email':'demo@example.com', 'delivery_mode':'quiet_night','accept_uncertain':True,
         'start_at':'2026-09-22T10:00:00+08:00','end_at':'2026-10-22T10:00:00+08:00'}]}})
    message = build_message(config, {'title': title + ' · 待核实','body':body,'event_key':'offline-demo'}, 'demo@example.com')
    (output / 'email-demo.html').write_text(preview_html(message), encoding='utf-8')
    (output / 'email-demo.eml').write_bytes(message.as_bytes())
    print(str((output / 'email-demo.html').resolve()))
    print('离线合成演示；未读取个人配置，未联网，未发送邮件。')


if __name__ == '__main__':
    main()
