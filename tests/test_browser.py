import unittest
import json
from unittest.mock import patch, MagicMock
from quota_monitor.config import Config, ConfigError
from quota_monitor.store import Store
from quota_monitor.browser import ingest, collect_report


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.c = Config({'accounts': [{'username': 'demo', 'id': '123', 'verified': True, 'kind': 'personal'}],
                         'notifications': {'channel': 'console'}, 'browser': {'background': False, 'divin_feed_enabled': False}})
        self.s = Store(':memory:')

    def tearDown(self):
        self.s.close()

    def post(self, **changes):
        return dict({'id': '2097043464538264003', 'username': 'demo',
                     'text': 'We will reset Codex usage soon.', 'complete': True,
                     'created_at': '2026-09-08T00:00:00Z', 'timestamp_source': 'time_datetime'}, **changes)

    def test_browser_needs_no_paid_budget_or_token(self):
        self.c.validate_browser()
        with self.assertRaises(ConfigError):
            self.c.validate_live()

    def test_reject_incomplete_foreign_and_missing_time(self):
        report = {'posts': [self.post(complete=False), self.post(username='stranger'), self.post(created_at=None)]}
        self.assertEqual(ingest(report, self.c, self.s, now=1788827400)['accepted'], 0)
        self.assertEqual(self.s.due(1788827400), [])

    def test_dedup_and_source_label(self):
        report = {'posts': [self.post()]}
        ingest(report, self.c, self.s, now=1788827400)
        ingest(report, self.c, self.s, now=1788827400)
        rows = self.s.due(1788827400)
        self.assertEqual(len(rows), 1)
        self.assertIn('浏览器', rows[0]['body'])

    def test_invalid_browser_bounds(self):
        self.c.data['browser'] = {'interval_minutes': 0}
        with self.assertRaises(ConfigError):
            self.c.validate_browser()

    def test_existing_node_path_keeps_bundled_fallback(self):
        process = MagicMock()
        process.communicate.return_value = ('{"accounts":[],"posts":[]}', '')
        process.returncode = 0
        process.poll.return_value = 0
        with patch.dict('os.environ', {'NODE_PATH': 'D:/other/modules'}), \
             patch('quota_monitor.browser.Path.exists', return_value=True), \
             patch('quota_monitor.browser.subprocess.Popen', return_value=process) as popen:
            collect_report(self.c)
        self.assertIn('codex-primary-runtime', popen.call_args.kwargs['env']['NODE_PATH'])
        self.assertIn('D:/other/modules', popen.call_args.kwargs['env']['NODE_PATH'])

    def test_browser_display_mode_used_by_default_and_explicit_override(self):
        for config_headless, override, expected in [(False, None, True), (True, None, False), (True, True, True)]:
            with self.subTest(config_headless=config_headless, override=override):
                self.c.data['browser'] = {'headless': config_headless, 'background': False, 'divin_feed_enabled': False}
                process = MagicMock()
                process.communicate.return_value = ('{"accounts":[],"posts":[]}', '')
                process.returncode = 0
                process.poll.return_value = 0
                with patch('quota_monitor.browser.subprocess.Popen', return_value=process):
                    if override is None:
                        collect_report(self.c)
                    else:
                        collect_report(self.c, headed=override)
                options = json.loads(process.communicate.call_args.args[0])
                self.assertEqual(options['headed'], expected)

    def test_headless_must_be_boolean(self):
        self.c.data['browser'] = {'headless': 'false'}
        with self.assertRaises(ConfigError):
            self.c.validate_browser()

    def test_context_options_forwarded_and_mail_test_skips_threads(self):
        self.c.data['browser'].update(context_enabled=False,
                                     conversation_urls=['https://x.com/other/status/10'])
        for profile_only in (False, True):
            process = MagicMock()
            process.communicate.return_value = ('{"accounts":[],"posts":[]}', '')
            process.returncode = 0
            process.poll.return_value = 0
            with patch('quota_monitor.browser.subprocess.Popen', return_value=process):
                collect_report(self.c, profile_only=profile_only)
            options = json.loads(process.communicate.call_args.args[0])
            self.assertIs(options['context_enabled'], False)
            self.assertEqual(options['conversation_urls'], [] if profile_only else ['https://x.com/other/status/10'])

    def test_feed_only_used_for_regular_collection(self):
        self.c.data['browser']['reset_feed_enabled']=True
        for profile_only in (False, True):
            process = MagicMock()
            process.communicate.return_value = ('{"accounts":[],"posts":[]}', '')
            process.returncode = 0
            process.poll.return_value = 0
            feed={'status':'ok','candidates':[{'id':'20','username':'thsottiaux','url':'https://x.com/thsottiaux/status/20'}]}
            with patch('quota_monitor.reset_feed.collect_candidates',return_value=feed) as fetch, \
                 patch('quota_monitor.browser.subprocess.Popen',return_value=process):
                result=collect_report(self.c,profile_only=profile_only)
            options=json.loads(process.communicate.call_args.args[0])
            self.assertEqual(fetch.call_count,0 if profile_only else 1)
            self.assertEqual(options['reset_feed']['status'],'disabled' if profile_only else 'ok')
            self.assertNotIn('candidates',result['reset_feed'])
