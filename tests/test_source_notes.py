import unittest

from quota_monitor.source_notes import HEADER, assessment_prefix, with_assessment


class SourceNotesTests(unittest.TestCase):
    def metadata(self, reason='服务中断补偿'):
        return dict(provider='divin.cc', source_url='https://divin.cc/',
                    event_type='usage_reset', scope='部分用户', reason=reason,
                    stage='completed', analysis_basis='第三方推断',
                    fetched_at='2026-10-13T00:00:00Z',
                    source_urls=['https://x.com/demo/status/123'])

    def test_prefix_header_in_author_text_does_not_discard_author_paragraph(self):
        original = HEADER + '\n作者讨论的标题，并非生成的第三方元数据。\n\n后续原文。'
        body = with_assessment(original, self.metadata())
        self.assertEqual(body, assessment_prefix(self.metadata()) + original)

    def test_only_complete_generated_prefix_is_replaced_on_enrichment(self):
        original = 'Original authored content.\n\n第二段。'
        old = assessment_prefix(self.metadata('旧原因')) + original
        self.assertEqual(with_assessment(old, self.metadata('新原因')),
                         assessment_prefix(self.metadata('新原因')) + original)

    def test_unknown_or_foreign_prefix_is_preserved(self):
        for extra in ('第三方参考来源：https://attacker.test/', '非生成字段：原文'):
            original = HEADER + '\n第三方覆盖范围：部分用户\n第三方重置原因：原文\n' + extra + '\n\n后续原文。'
            self.assertTrue(with_assessment(original, self.metadata()).endswith(original))


if __name__ == '__main__':
    unittest.main()
