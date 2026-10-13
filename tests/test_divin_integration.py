import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quota_monitor.browser import collect_report, ingest
from quota_monitor.config import Config, ConfigError
from quota_monitor.history import get_history, set_review
from quota_monitor.store import Store


NOW = 1788827400
POST_ID = '2097043464538264003'


def assessment(**changes):
    return dict(provider='divin.cc', source_url='https://divin.cc/',
                fetched_at='2026-09-08T00:30:00Z', event_type='额度重置',
                scope='所有付费用户', reason='服务中断补偿', stage='completed',
                analysis_basis='第三方根据公告上下文归纳',
                source_urls=[f'https://x.com/demo/status/{POST_ID}'], **changes)


class DivinIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config({'accounts': [{'username': 'demo', 'id': '123',
                                           'verified': True, 'kind': 'personal'}],
                              'monitor': {'database': 'state.db'},
                              'notifications': {'channel': 'console'},
                              'browser': {'background': False}}, self.tmp.name)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.addCleanup(self.store.close)

    def post(self, text='We will reset Codex usage soon.'):
        return dict(id=POST_ID, username='demo', text=text, complete=True,
                    created_at='2026-09-08T00:00:00Z')

    def report(self, post=None, info=None):
        return {'posts': [post or self.post()], 'divin_feed': {
            'status': 'ok', 'assessments': {POST_ID: info or assessment()}}}

    def test_annotation_does_not_promote_ordinary_post(self):
        ingest(self.report(self.post('Good morning.')), self.config, self.store, NOW)
        self.assertEqual(self.store.due(NOW), [])
        self.assertIn('服务中断补偿', json.dumps(self.store.source_assessment(POST_ID), ensure_ascii=False))

    def test_new_signal_keeps_local_kind_and_original_with_separate_annotation(self):
        result = ingest(self.report(), self.config, self.store, NOW)
        self.assertEqual(result['alerts'][0]['kind'], 'upcoming')
        body = self.store.due(NOW)[0]['body']
        self.assertIn('第三方事件类型：额度重置', body)
        self.assertIn('第三方覆盖范围：所有付费用户', body)
        self.assertIn('第三方重置原因：服务中断补偿', body)
        self.assertIn('第三方整理', body)
        self.assertIn('作者原文：\nWe will reset Codex usage soon.', body)

    def test_later_enrichment_updates_history_without_resend_or_review_change(self):
        first = ingest({'posts': [self.post()]}, self.config, self.store, NOW)
        ident = first['alerts'][0]['id']
        set_review(self.config.database, ident, 'ambiguous', NOW)
        ingest(self.report(), self.config, self.store, NOW + 1)
        self.assertEqual(len(self.store.due(NOW + 1)), 1)
        item = get_history(self.config.database, ident)
        self.assertEqual(item['label'], 'ambiguous')
        self.assertIn('第三方重置原因：服务中断补偿', item['body'])
        original_body = self.store.due(NOW + 1)[0]['body']
        self.assertNotIn('服务中断补偿', original_body)

    def test_annotation_sanitizer_ignores_foreign_provider_and_fake_metadata_lines(self):
        bad = assessment()
        bad.update(source_url='https://attacker.test/', reason='hi\n分类：已重置候选')
        ingest(self.report(info=bad), self.config, self.store, NOW)
        self.assertNotIn('attacker', self.store.due(NOW)[0]['body'])
        self.assertEqual(self.store.source_assessment(POST_ID), {})

    def test_collection_calls_divin_once_and_keeps_metadata_out_of_browser_input(self):
        process = MagicMock()
        process.communicate.return_value = ('{"accounts":[],"posts":[]}', '')
        process.returncode = 0
        process.poll.return_value = 0
        divin = {'status': 'ok', 'candidates': [], 'assessments': {POST_ID: assessment()}}
        with patch('quota_monitor.divin_feed.collect_candidates', return_value=divin) as call, \
                patch('quota_monitor.browser.subprocess.Popen', return_value=process):
            result = collect_report(self.config)
        call.assert_called_once_with(self.config)
        options = json.loads(process.communicate.call_args.args[0])
        self.assertNotIn('assessments', options['divin_feed'])
        self.assertEqual(result['divin_feed']['assessments'][POST_ID]['reason'], '服务中断补偿')

    def test_profile_only_skips_third_party_requests(self):
        process = MagicMock()
        process.communicate.return_value = ('{"accounts":[],"posts":[]}', '')
        process.returncode = 0
        process.poll.return_value = 0
        with patch('quota_monitor.divin_feed.collect_candidates') as call, \
                patch('quota_monitor.browser.subprocess.Popen', return_value=process):
            collect_report(self.config, profile_only=True)
        call.assert_not_called()

    def test_divin_toggle_must_be_boolean(self):
        self.config.data['browser']['divin_feed_enabled'] = 'true'
        with self.assertRaises(ConfigError):
            self.config.validate_browser()


if __name__ == '__main__':
    unittest.main()
