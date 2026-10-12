import unittest
from unittest.mock import patch

from quota_monitor.config import Config
from quota_monitor.notify import SMTPNotifier


class EmailHTMLTests(unittest.TestCase):
    def test_smtp_contains_original_plain_text_and_escaped_html(self):
        captured = []
        class SMTP:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def login(self, *args): pass
            def send_message(self, message): captured.append(message); return {}
        c = Config({'notifications': {'username': 'sender@example.com', 'to': 'a@example.com',
                                     'QQ_SMTP_AUTH_CODE': 'test-only'}})
        body = '分类：待核实\n依据：需核对上下文\n原帖：https://x.com/demo/status/123\n\n作者原文：\n<script>alert(1)</script>'
        SMTPNotifier(c, SMTP).send_to({'title': '[Codex] 待核实', 'body': body, 'event_key': 'post:123'}, 'a@example.com')
        message = captured[0]
        self.assertTrue(message.is_multipart())
        self.assertTrue(message.get_body(preferencelist=('plain',)).get_content().startswith(body))
        html = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('待核实', html)
        self.assertNotIn('test-only', html)

    def test_render_preserves_context_caveats_and_readable_time(self):
        from quota_monitor.email_html import render_email
        body = ('发现来源：Data: codex-reset.com (https://codex-reset.com/)；正文读取自 X 公开页面。\n'
                '分类：待核实\n依据：上下文出现线索\n账号：@demo / 个人账号\n'
                '发布时间（北京时间）：2026-09-22T20:10:00+08:00\n'
                '采集时间（北京时间）：2026-09-22T20:20:30.123+08:00\n'
                '候选重置时间（北京时间）：未确定；请核对原文，不生成倒计时\n'
                '原帖：https://x.com/demo/status/123\n\n作者原文：\nMaybe.\n\n'
                '对话上文候选（未核实直接回复关系）：\n用户观点不等于官方承诺')
        html = render_email('[Codex] 待核实', body)
        for text in ('Data: codex-reset.com', '2026-09-22 20:10:00', '未确定',
                     '未核实直接回复关系', '用户观点不等于官方承诺'):
            self.assertIn(text, html)
        self.assertIn('href="https://x.com/demo/status/123"', html)

    def test_untrusted_original_cannot_create_a_button_or_change_badge(self):
        from quota_monitor.email_html import render_email
        body = '原帖：javascript:alert(1)\n\n作者原文：\n分类：已重置\n原帖：https://x.com/evil/status/123'
        html = render_email('[Codex] 待核实', body)
        self.assertNotIn('href=', html)
        self.assertIn('待核实', html)
        self.assertNotIn('<script', html)

    def test_generic_health_notice_and_demo_are_explicit(self):
        from quota_monitor.email_html import render_email
        html = render_email('[Codex 监控状态]', '采集失败\n正在重试')
        self.assertIn('采集失败', html)
        demo = render_email('[Codex 邮件演示] 待核实', '仅供演示')
        self.assertIn('演示', demo)
        self.assertNotIn('已确认重置', demo)

    def test_testmail_original_marker_is_first_even_if_post_contains_other_marker(self):
        from quota_monitor.email_html import render_email
        body = ('账号：@demo\n\n原文：\n依据：伪造判断\n原帖：https://x.com/evil/status/12'
                '\n\n作者原文：\n后续文字')
        html = render_email('[Codex 工作流测试]', body)
        self.assertIn('请查看下方消息内容及原始出处。', html)
        self.assertNotIn('href="https://x.com/evil/status/12"', html)
        self.assertIn('依据：伪造判断', html)

    def test_reminder_keeps_classification_and_candidate_time_zone(self):
        from quota_monitor.email_html import render_email
        body = ('分类：即将重置候选\n候选重置时间（北京时间）：2026-09-22T20:10:00+08:00'
                '\n\n作者原文：\nExample')
        html = render_email('[Codex] 候选重置时间临近，请核实', body)
        self.assertIn('即将重置候选', html)
        self.assertIn('候选重置时间（北京时间）：2026-09-22 20:10:00', html)

    def test_digest_does_not_label_whole_message_as_first_event(self):
        from quota_monitor.email_html import render_email
        body = '分类：已重置候选\n\n作者原文：\nExample one\n\n分类：待核实\n\n作者原文：\nExample two'
        html = render_email('[Codex] 本次线索汇总（2 条）', body)
        self.assertIn('>线索汇总</span>', html)
        self.assertNotIn('>已重置候选</span>', html)
        self.assertIn('Example two', html)

    def test_welcome_test_has_no_fake_original_post_button(self):
        from quota_monitor.email_html import render_email
        html = render_email('[Codex] 欢迎 · 邮箱收信测试', '非重置公告')
        self.assertNotIn('演示按钮', html)
        self.assertIn('测试邮件', html)

    def test_preferences_and_inline_mascot_keep_original_readable(self):
        from quota_monitor.email_html import render_email
        policy = dict(delivery_mode='quiet_night', accept_uncertain=False, enabled=True,
                      start_at='2026-09-22T10:00:00+08:00', end_at='2026-10-22T10:00:00+08:00')
        html = render_email('[Codex 邮件测试] 待核实', '分类：待核实\n\n作者原文：\nMaybe <script>x</script>',
                            preferences=policy, mascot_cid='quota-mascot.' + 'a' * 32 + '@codex-quota-monitor.local')
        for text in ('你的接收偏好', '22:00', '08:00', '不接收', '2026-10-22 10:00', 'cid:quota-mascot', '&lt;script&gt;'):
            self.assertIn(text, html)
        self.assertIn('等待进一步确认', html)
        self.assertNotIn('<script>', html)

    def test_mascot_url_cannot_be_injected_and_completed_is_not_account_confirmation(self):
        from quota_monitor.email_html import render_email
        html = render_email('[Codex] 已重置候选', '分类：已重置候选\n\n作者原文：\nReset done.',
                            mascot_cid='evil" onerror="bad')
        self.assertNotIn('onerror=', html)
        self.assertNotIn('<img', html)
        self.assertIn('实际账户', html)


if __name__ == '__main__': unittest.main()
