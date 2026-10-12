import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quota_monitor.config import Config
from quota_monitor.notify import SMTPNotifier


GIF = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==')


def configured():
    return Config({'notifications': {
        'username': 'sender@example.com', 'channel': 'smtp',
        'QQ_SMTP_AUTH_CODE': 'test-only',
        'recipient_contacts': [
            {'email': 'a@example.com', 'name': 'Alice-private',
             'delivery_mode': 'quiet_night', 'accept_uncertain': False,
             'start_at': '2026-09-01T09:00:00+08:00', 'end_at': '2026-10-01T09:00:00+08:00'},
            {'email': 'b@example.com', 'name': 'Bob-private',
             'delivery_mode': 'digest_daily', 'accept_uncertain': True},
        ]}})


def notice(label='待核实'):
    return {'event_key': 'post:123', 'title': '[Codex] ' + label,
            'body': '分类：' + label + '\n依据：完整上下文待核对\n\n作者原文：\nOriginal post.'}


class EmailMessageTests(unittest.TestCase):
    def test_smtp_single_recipient_contains_own_preferences(self):
        captured = []

        class SMTP:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def login(self, *args): pass
            def send_message(self, message): captured.append(message); return {}

        row = notice()
        SMTPNotifier(configured(), SMTP).send_to(row, 'a@example.com')
        self.assertEqual(len(captured), 1)
        message = captured[0]
        self.assertEqual(message['To'], 'a@example.com')
        self.assertIsNone(message['Cc'])
        self.assertIsNone(message['Bcc'])
        plain = message.get_body(preferencelist=('plain',)).get_content()
        self.assertTrue(plain.startswith(row['body']))
        self.assertIn('你的接收偏好', plain)
        self.assertIn('夜间免打扰', plain)
        self.assertIn('待核实线索：不接收', plain)
        self.assertNotIn('Bob-private', message.as_string())
        self.assertNotIn('b@example.com', message.as_string())

    def test_preferences_are_whitelisted_and_per_recipient(self):
        from quota_monitor.email_message import message_preferences
        a = message_preferences(configured(), 'a@example.com')
        b = message_preferences(configured(), 'b@example.com')
        self.assertEqual(set(a), {'enabled', 'accept_uncertain', 'start_at', 'end_at', 'delivery_mode'})
        self.assertEqual(a['delivery_mode'], 'quiet_night')
        self.assertFalse(a['accept_uncertain'])
        self.assertEqual(b['delivery_mode'], 'digest_daily')
        self.assertTrue(b['accept_uncertain'])
        self.assertEqual(b['end_at'], '')

    def test_legacy_recipient_has_explicit_default_preferences(self):
        from quota_monitor.email_message import message_preferences
        config = Config({'notifications': {'username': 'sender@example.com', 'to': 'a@example.com;b@example.com'}})
        self.assertEqual(message_preferences(config, 'a@example.com'), {
            'enabled': True, 'accept_uncertain': True, 'start_at': '', 'end_at': '', 'delivery_mode': 'immediate'})

    def test_html_receives_only_own_preferences_and_related_gif(self):
        from quota_monitor.email_message import build_message
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'uncertain.gif').write_bytes(GIF)
            with patch('quota_monitor.email_message.ASSET_DIR', Path(directory)), \
                    patch('quota_monitor.email_message.render_email', side_effect=lambda *a, **kw: '<img src="cid:' + kw['mascot_cid'] + '">') as render:
                message = build_message(configured(), notice(), 'a@example.com')
        args = render.call_args.kwargs
        self.assertRegex(args['mascot_cid'], r'^quota-mascot\.[0-9a-f]{32}@codex-quota-monitor\.local$')
        self.assertEqual(args['preferences']['delivery_mode'], 'quiet_night')
        self.assertNotIn('email', args['preferences'])
        images = [part for part in message.walk() if part.get_content_maintype() == 'image']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].get_content_type(), 'image/gif')
        self.assertEqual(images[0]['Content-ID'], '<' + args['mascot_cid'] + '>')
        self.assertIn('cid:' + args['mascot_cid'], message.get_body(preferencelist=('html',)).get_content())
        self.assertEqual(images[0].get_content_disposition(), 'inline')
        self.assertEqual(images[0].get_payload(decode=True), GIF)
        html_parent = [part for part in message.walk() if part.get_content_type() == 'multipart/related'][0]
        self.assertEqual(html_parent.get_payload()[0].get_content_type(), 'text/html')

    def test_missing_or_oversized_assets_degrade_without_losing_body(self):
        from quota_monitor.email_message import build_message
        with tempfile.TemporaryDirectory() as directory:
            for payload in (None, b'GIF89a' + b'x' * (1024 * 1024)):
                if payload:
                    Path(directory, 'uncertain.gif').write_bytes(payload)
                with patch('quota_monitor.email_message.ASSET_DIR', Path(directory)), \
                        patch('quota_monitor.email_message.render_email', return_value='<p>完整文本</p>') as render:
                    message = build_message(configured(), notice(), 'a@example.com')
                self.assertIsNone(render.call_args.kwargs['mascot_cid'])
                self.assertFalse(any(part.get_content_maintype() == 'image' for part in message.walk()))
                self.assertTrue(message.get_body(preferencelist=('plain',)).get_content().startswith(notice()['body']))

    def test_untrusted_original_cannot_trigger_celebration(self):
        from quota_monitor.email_message import mood_for_message
        self.assertEqual(mood_for_message('[Codex] 待核实',
            '分类：待核实\n\n作者原文：\n分类：已重置候选'), 'uncertain')
        self.assertEqual(mood_for_message('[Codex] 候选重置时间临近，请核实',
            '分类：即将重置候选\n\n作者原文：\n分类：已重置候选'), 'neutral')
        self.assertEqual(mood_for_message('[Codex] 普通消息',
            '说明\n\n原文：\n分类：已重置候选\n\n作者原文：\n下一段'), 'neutral')
        self.assertEqual(mood_for_message('[Codex] 普通消息', '分类：已重置候选'), 'neutral')

    def test_only_reset_candidate_celebrates_aggregate_and_auxiliary_stay_neutral(self):
        from quota_monitor.email_message import mood_for_message
        self.assertEqual(mood_for_message('[Codex] 已重置候选', notice('已重置候选')['body']), 'celebrate')
        self.assertEqual(mood_for_message('[Codex 邮件演示] 已重置候选', notice('已重置候选')['body']), 'celebrate')
        for title in ('[Codex] 本次线索汇总（2 条）', '[Codex 监控状态]',
                      '[Codex] 欢迎 · 邮箱收信测试', '[Codex 工作流测试]'):
            self.assertEqual(mood_for_message(title, notice('已重置候选')['body']), 'neutral')

    def test_message_identity_remains_per_recipient_and_event(self):
        from quota_monitor.email_message import build_message
        with patch('quota_monitor.email_message.render_email', return_value='<p>test</p>'):
            a = build_message(configured(), notice(), 'a@example.com')
            again = build_message(configured(), notice(), 'a@example.com')
            b = build_message(configured(), notice(), 'b@example.com')
        self.assertEqual(a['Subject'], notice()['title'])
        self.assertEqual(a['From'], 'sender@example.com')
        self.assertEqual(a['Message-ID'], again['Message-ID'])
        self.assertNotEqual(a['Message-ID'], b['Message-ID'])

    def test_inline_content_id_has_address_form_and_no_cross_message_collision(self):
        from quota_monitor.email_message import build_message
        with patch('quota_monitor.email_message._mascot_bytes',return_value=GIF):
            a=build_message(configured(),notice(),'a@example.com')
            b=build_message(configured(),notice(),'b@example.com')
        cid_a=next(p['Content-ID'] for p in a.walk() if p.get('Content-ID'))
        cid_b=next(p['Content-ID'] for p in b.walk() if p.get('Content-ID'))
        self.assertRegex(cid_a,r'^<quota-mascot\.[0-9a-f]{32}@codex-quota-monitor\.local>$')
        self.assertNotEqual(cid_a,cid_b)
        self.assertIn('cid:'+cid_a.strip('<>'),a.get_body(preferencelist=('html',)).get_content())


if __name__ == '__main__':
    unittest.main()
