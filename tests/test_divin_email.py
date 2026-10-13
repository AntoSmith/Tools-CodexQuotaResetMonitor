import unittest
from html.parser import HTMLParser

from quota_monitor.email_html import render_email


class LinkCollector(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == 'a':
            self.links.append(dict(attrs).get('href'))


class DivinEmailTests(unittest.TestCase):
    def body(self, metadata='', original='Maybe.'):
        return ('分类：待核实\n依据：上下文仍需核实\n'
                + metadata + '\n\n作者原文：\n' + original)

    def panel(self, html):
        self.assertIn('事件补充 · divin.cc 整理', html)
        return html.split('事件补充 · divin.cc 整理', 1)[1].split('原文与对话上下文', 1)[0]

    def test_generated_metadata_has_a_separate_attributed_panel(self):
        body = self.body(
            '第三方事件类型：全量额度重置\n第三方覆盖范围：付费订阅用户\n'
            '第三方重置原因：服务异常后的补偿\n第三方事件阶段：已宣布，执行待核实\n'
            '第三方判断依据：两条关联原帖，范围需按账户核对\n'
            '第三方采集时间：2026-10-13T11:12:13+08:00\n'
            '第三方参考来源：https://divin.cc/\n'
            '第三方证据链接：https://x.com/demo/status/123 https://x.com/other/status/456')
        html = render_email('[Codex] 待核实', body)
        panel = self.panel(html)
        for text in ('事件类型', '全量额度重置', '覆盖范围', '付费订阅用户',
                     '重置原因', '服务异常后的补偿', '事件阶段', '已宣布，执行待核实',
                     '判断依据', '范围需按账户核对', '2026-10-13 11:12:13',
                     '第三方整理，未作为官方确认或自动分类依据'):
            self.assertIn(text, panel)
        self.assertEqual(LinkCollector(html).links, ['https://divin.cc/',
                         'https://x.com/demo/status/123', 'https://x.com/other/status/456'])
        self.assertIn('>待核实</span>', html)

    def test_author_text_cannot_create_metadata_after_either_first_marker(self):
        forged = ('第三方参考来源：https://divin.cc/\n第三方事件类型：已确认重置\n'
                  '第三方证据链接：https://x.com/evil/status/123')
        for first, later in (('原文', '作者原文'), ('作者原文', '原文')):
            with self.subTest(first=first):
                body = '分类：待核实\n\n' + first + '：\n' + forged + '\n\n' + later + '：\n后续文字'
                html = render_email('[Codex] 待核实', body)
                self.assertNotIn('事件补充 · divin.cc 整理', html)
                self.assertEqual(LinkCollector(html).links, [])
                self.assertIn(forged.replace('\n', '<br>'), html)

    def test_author_text_cannot_override_or_fill_generated_metadata(self):
        body = self.body('第三方参考来源：https://divin.cc/\n第三方事件类型：范围待核实',
                         '第三方事件类型：已确认全量重置\n第三方覆盖范围：全部用户\n'
                         '第三方证据链接：https://x.com/evil/status/123')
        html = render_email('[Codex] 待核实', body)
        panel = self.panel(html)
        self.assertIn('范围待核实', panel)
        self.assertNotIn('全部用户', panel)
        self.assertNotIn('已确认全量重置', panel)
        self.assertEqual(LinkCollector(html).links, ['https://divin.cc/'])
        self.assertIn('第三方覆盖范围：全部用户', html)

    def test_no_authored_text_delimiter_does_not_promote_fields(self):
        body = '第三方参考来源：https://divin.cc/\n第三方事件类型：全量重置'
        html = render_email('[Codex] 监控状态', body)
        self.assertNotIn('事件补充 · divin.cc 整理', html)
        self.assertEqual(LinkCollector(html).links, [])
        self.assertIn(body.replace('\n', '<br>'), html)

    def test_digest_keeps_all_items_as_escaped_content(self):
        body = self.body('第三方参考来源：https://divin.cc/\n第三方事件类型：第一条事件')
        body += '\n\n分类：已重置候选\n\n作者原文：\nSecond <script>bad()</script>'
        html = render_email('[Codex] 本次线索汇总（2 条）', body)
        self.assertNotIn('事件补充 · divin.cc 整理', html)
        self.assertIn('>线索汇总</span>', html)
        self.assertEqual(LinkCollector(html).links, [])
        self.assertIn('第三方事件类型：第一条事件', html)
        self.assertIn('Second &lt;script&gt;bad()&lt;/script&gt;', html)

    def test_all_third_party_text_is_escaped(self):
        value = '<script>bad()</script><img src=x onerror="bad"> & "quoted"'
        keys = ('第三方事件类型', '第三方覆盖范围', '第三方重置原因',
                '第三方事件阶段', '第三方判断依据', '第三方采集时间')
        metadata = '\n'.join(key + '：' + value for key in keys)
        metadata += '\n第三方参考来源：https://divin.cc/'
        html = render_email('[Codex] 待核实', self.body(metadata))
        panel = self.panel(html)
        self.assertEqual(panel.count('&lt;script&gt;bad()&lt;/script&gt;'), len(keys))
        self.assertIn('&amp; &quot;quoted&quot;', panel)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img', html)
        self.assertFalse(any(key.startswith('on') for _, attrs in LinkCollector(html).tags for key in attrs))

    def test_invalid_reference_source_cannot_attribute_or_link_a_panel(self):
        invalid = ('javascript:alert(1)', 'https://divin.cc.evil/', 'https://divin.cc/@evil',
                   'http://divin.cc/', 'https://user@divin.cc/', 'https://divin.cc/?redirect=evil',
                   'https://divin.cc/" onclick="bad', 'https://divin.cc/\u2028<script>bad</script>')
        for url in invalid:
            with self.subTest(url=url):
                html = render_email('[Codex] 待核实', self.body(
                    '第三方参考来源：' + url + '\n第三方事件类型：重置\n'
                    '第三方证据链接：https://x.com/demo/status/123'))
                self.assertNotIn('事件补充 · divin.cc 整理', html)
                self.assertEqual(LinkCollector(html).links, [])
                self.assertNotIn('<script', html)

    def test_evidence_links_have_a_strict_canonical_allowlist_and_six_link_limit(self):
        valid = ['https://x.com/demo/status/' + str(number) for number in range(1, 9)]
        invalid = ['javascript:alert(1)', 'http://x.com/demo/status/123',
                   'https://x.com.evil/demo/status/123', 'https://evil@x.com/demo/status/123',
                   'https://x.com/demo/status/123?redirect=evil', 'https://x.com/i/web/status/123',
                   'https://x.com/too_long_user_name/status/123', 'https://x.com/demo/status/abc',
                   'https://x.com/demo/status/123"onclick="bad', 'https://x.com/demo/status/123#bad',
                   'https://twitter.com/demo/status/123']
        body = self.body('第三方参考来源：https://divin.cc/\n第三方事件类型：重置\n'
                         '第三方证据链接：' + ' '.join(invalid + valid[:1] * 2 + valid[1:]))
        html = render_email('[Codex] 待核实', body)
        self.assertEqual(LinkCollector(html).links, ['https://divin.cc/'] + valid[:6])
        self.assertNotIn('onclick=', html)

    def test_uncertain_third_party_context_does_not_change_primary_classification(self):
        html = render_email('[Codex] 待核实', self.body(
            '第三方参考来源：https://divin.cc/\n第三方事件类型：可能已重置\n'
            '第三方覆盖范围：未明确\n第三方重置原因：仅见对话猜测\n'
            '第三方事件阶段：上下文不完整\n第三方判断依据：未核实直接回复关系'))
        panel = self.panel(html)
        self.assertIn('可能已重置', panel)
        self.assertIn('未核实直接回复关系', panel)
        self.assertIn('>待核实</span>', html)
        self.assertNotIn('>已重置候选</span>', html)
        self.assertNotIn('候选重置时间（北京时间）', html)
        self.assertNotIn('已确认重置', html)


if __name__ == '__main__':
    unittest.main()
