"""A manually requested, single-address sample from recently read Tibo evidence."""
import argparse
import copy
import json
import re
import uuid
from pathlib import Path

from .classify import classify
from .config import Config, ConfigError
from .conversation import context_posts, context_text
from .email_message import build_message, preview_html
from .service import BEIJING, LABELS, parse_time


def prepare_signal(config, report):
    recipient = config.section('notifications').get('username', '')
    if recipient not in config.recipients:
        raise ConfigError('发件邮箱不在已保存的收件名单中，无法确定本人邮箱；本次未发送')
    collected = parse_time(report.get('collected_at'))
    candidates = []
    for post in report.get('posts', []):
        stamp = parse_time(post.get('created_at'))
        if (post.get('username', '').lower() != 'thsottiaux' or post.get('complete') is not True
                or not re.fullmatch(r'[0-9]{1,25}', str(post.get('id', '')))
                or stamp is None or not isinstance(post.get('text'), str) or not post['text'].strip()):
            continue
        if collected and not -300 <= (collected - stamp).total_seconds() <= config.section('monitor').get('max_age_hours', 24) * 3600:
            continue
        decision = classify(post['text'], context=context_text(context_posts(post)))
        if decision.kind in ('uncertain', 'completed', 'upcoming', 'credit', 'correction'):
            candidates.append((stamp, int(post['id']), post, decision))
    if not candidates:
        raise ConfigError('本次未取得近期重置相关推文的完整正文；未发送，不以第三方摘要代替原帖')
    stamp, _, post, decision = max(candidates, key=lambda value: value[:2])
    url = f"https://x.com/thsottiaux/status/{post['id']}"
    takeaway = decision.reason
    if re.search(r'promised a reset for Tuesday', post['text'], re.I):
        takeaway = 'Tibo 提到曾承诺周二重置；这是一条预告，尚未宣布执行完成，也未给出确切时刻和适用订阅。'
    quote = ' '.join(post['text'].split()[:24])
    if len(post['text'].split()) > 24:
        quote += ' …'
    context_note = ('页面可读取到对话上文，回复关系仍未核实；请在原帖查看完整对话。'
                    if context_posts(post) else '本次未读取到可核验的对话上文。')
    body = ('来源：X 公开详情页；覆盖有限，发布时间精度为分钟。\n'
            + ('发现来源：Data: codex-reset.com (https://codex-reset.com/)，仅用于定位原帖。\n'
               if post.get('discovery_source') == 'https://codex-reset.com/' else '')
            + f"分类：{LABELS[decision.kind]}\n依据：{decision.reason}\n要点：{takeaway}\n"
              f"账号：Tibo · @thsottiaux（个人账号）\n原帖：{url}\n"
              f"发布时间（北京时间）：{stamp.astimezone(BEIJING).isoformat()}\n"
            + (f"采集时间（北京时间）：{collected.astimezone(BEIJING).isoformat()}\n" if collected else '')
            + f"候选重置时间（北京时间）：未确定；不据此生成倒计时\n原文模式：节选\n"
              f"\n作者原文：\n{quote}\n\n原文节选，完整内容请打开原帖。\n{context_note}")
    return {'post_id':post['id'], 'post_url':url, 'recipient':recipient,
            'row':{'event_key':'signal-test:' + uuid.uuid4().hex,
                   'title':'[Codex] ' + LABELS[decision.kind], 'body':body}, 'status':'preview'}


def write_preview(config, prepared, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    message = build_message(config, prepared['row'], prepared['recipient'])
    html = preview_html(message)
    (directory / 'latest-signal.html').write_text(html, encoding='utf-8')
    (directory / 'latest-signal.eml').write_bytes(message.as_bytes())
    (directory / 'latest-signal.json').write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='最新可读重置线索样例；默认仅预览，显式选项才发送给本人')
    parser.add_argument('--config', default='config.browser.local.toml')
    parser.add_argument('--send-to-self', action='store_true')
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        data = copy.deepcopy(config.data)
        data['accounts'] = [a for a in config.accounts if a['username'].lower() == 'thsottiaux']
        data.setdefault('monitor', {})['database'] = 'data/email-preview/collection/state.db'
        data.setdefault('browser', {}).update(profile='data/email-preview/collection/profile', max_posts=5, conversation_urls=[])
        sample_config = Config(data, config.base)
        sample_config.validate_browser()
        from .browser import collect_report
        from .cli import single_instance
        sample_config.database.parent.mkdir(parents=True, exist_ok=True)
        with single_instance(str(sample_config.database) + '.lock'):
            report = collect_report(sample_config)
        output = config.base / 'data/email-preview'
        output.mkdir(parents=True, exist_ok=True)
        (output / 'latest-signal-source.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        prepared = prepare_signal(config, report)
        write_preview(config, prepared, output)
        print('原帖：' + prepared['post_url'])
        print('预览：' + str(output / 'latest-signal.html'))
        if args.send_to_self:
            from .notify import SMTPNotifier
            send_config = Config(copy.deepcopy(config.data), config.base)
            send_config.data['notifications']['channel'] = 'smtp'
            send_config.validate_smtp_auth()
            prepared['status'] = 'sending'
            write_preview(config, prepared, output)
            try:
                SMTPNotifier(send_config).send_to(prepared['row'], prepared['recipient'])
            except Exception:
                prepared['status'] = 'failed_or_uncertain'
                write_preview(config, prepared, output)
                raise ConfigError('发送结果未确认；未自动重试，请先检查邮箱') from None
            prepared['status'] = 'smtp_accepted'
            write_preview(config, prepared, output)
            print('SMTP 已接受给本人邮箱的一封重置线索邮件；未向其他订阅者发送。请确认收件。')
        else:
            print('仅预览，未发送邮件。')
        return 0
    except (ConfigError, OSError, ValueError) as exc:
        print(str(exc) if isinstance(exc, ConfigError) else '样例生成失败：' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
