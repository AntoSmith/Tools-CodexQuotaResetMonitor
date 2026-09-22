import json
import unittest
from unittest.mock import MagicMock, patch

from quota_monitor.browser import collect_report
from quota_monitor.config import Config, ConfigError


class BackgroundRoutingTests(unittest.TestCase):
    def config(self, **browser):
        return Config({'accounts': [{'username':'demo', 'id':'123', 'verified':True, 'kind':'personal'}],
                       'notifications':{'channel':'console'}, 'browser':browser})

    def test_background_requires_boolean(self):
        with self.assertRaisesRegex(ConfigError, 'background'):
            self.config(background='yes').validate_browser()

    def collect(self, config, headed=None, fails=False):
        process=MagicMock()
        process.communicate.return_value=('"invalid"' if fails else '{"posts":[],"accounts":[]}', '')
        process.returncode=0
        process.poll.return_value=0
        background=MagicMock()
        background.__enter__.return_value.endpoint='ws://127.0.0.1:43210/devtools/browser/test'
        with patch('quota_monitor.browser.background_browser', return_value=background) as factory, \
             patch('quota_monitor.browser.subprocess.Popen', return_value=process):
            if fails:
                with self.assertRaises(ConfigError):
                    collect_report(config, headed=headed)
            else:
                collect_report(config, headed=headed)
        return factory, background, json.loads(process.communicate.call_args.args[0])

    def test_background_passes_endpoint_and_cleans_up(self):
        factory, background, options=self.collect(self.config(background=True, headless=False))
        factory.assert_called_once()
        self.assertTrue(options['headed'])
        self.assertTrue(options['background'])
        self.assertTrue(options['cdp_endpoint'].startswith('ws://127.0.0.1:'))
        background.__exit__.assert_called_once()

    def test_explicit_headed_and_headless_do_not_launch_background_browser(self):
        for browser, override in [({'background':True}, True), ({'background':True,'headless':True}, None), ({'background':False}, None)]:
            factory, _, options=self.collect(self.config(**browser), headed=override)
            factory.assert_not_called()
            self.assertNotIn('cdp_endpoint', options)

    def test_cleanup_after_bad_collector_output(self):
        _, background, _=self.collect(self.config(background=True), fails=True)
        background.__exit__.assert_called_once()

    def test_failed_background_start_never_launches_foreground(self):
        with patch('quota_monitor.browser.background_browser', side_effect=ConfigError('unavailable')), \
             patch('quota_monitor.browser.subprocess.Popen') as launch:
            with self.assertRaises(ConfigError):
                collect_report(self.config(background=True))
            launch.assert_not_called()
