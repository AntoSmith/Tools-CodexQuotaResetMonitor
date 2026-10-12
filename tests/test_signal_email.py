import copy
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from quota_monitor.config import Config, ConfigError


class SignalEmailTests(unittest.TestCase):
    def setUp(self):
        self.config = Config({'accounts':[{'username':'thsottiaux','id':'1953337039510003712','kind':'personal','verified':True}],
            'notifications':{'channel':'smtp','username':'me@example.com','to':'me@example.com;other@example.com'},
            'monitor':{'max_age_hours':24}})
        self.post = {'id':'2102254445082116335','username':'thsottiaux','complete':True,
                     'created_at':'2026-09-22T04:31:00Z', 'context':[],
                     'text':'Ladies and gentlemen... start... your... ENGINES. We are almost Tuesday and I promised a reset for Tuesday. Among some other things. See you soon.'}

    def test_selects_latest_related_complete_tibo_and_excludes_casual_newer(self):
        from quota_monitor.signal_email import prepare_signal
        other = {**self.post, 'id':'2102292452329820272','text':'Huge!!','created_at':'2026-09-22T07:02:00Z'}
        prepared = prepare_signal(self.config, {'collected_at':'2026-09-22T14:40:00Z','posts':[other,self.post]})
        self.assertEqual(prepared['post_id'],self.post['id'])
        self.assertEqual(prepared['recipient'],'me@example.com')
        self.assertIn('待核实',prepared['row']['title'])
        self.assertNotIn('测试',prepared['row']['title'])
        self.assertIn('尚未宣布执行完成',prepared['row']['body'])
        self.assertIn('周二',prepared['row']['body'])
        self.assertIn('原文节选',prepared['row']['body'])
        self.assertNotIn('other@example.com',str(prepared))

    def test_missing_self_recipient_and_incomplete_signal_are_rejected(self):
        from quota_monitor.signal_email import prepare_signal
        config=copy.deepcopy(self.config)
        config.data['notifications']['to']='other@example.com'
        with self.assertRaises(ConfigError): prepare_signal(config, {'posts':[self.post]})
        with self.assertRaises(ConfigError): prepare_signal(self.config, {'posts':[{**self.post,'complete':False}]})

    def test_sender_validation_does_not_change_subscriber_policy(self):
        from quota_monitor.signal_email import prepare_signal
        original=copy.deepcopy(self.config.data)
        prepare_signal(self.config, {'collected_at':'2026-09-22T14:40:00Z','posts':[self.post]})
        self.assertEqual(self.config.data,original)

    def test_newer_correction_is_selected_before_old_celebration(self):
        from quota_monitor.signal_email import prepare_signal
        from quota_monitor.email_message import mood_for_message
        old={**self.post,'text':'We have already reset Codex usage limits.'}
        new={**self.post,'id':'2102292452329820272','created_at':'2026-09-22T07:02:00Z',
             'text':'The Codex usage reset has been delayed.'}
        prepared=prepare_signal(self.config,{'collected_at':'2026-09-22T14:40:00Z','posts':[old,new]})
        self.assertEqual(prepared['post_id'],new['id'])
        self.assertEqual(mood_for_message(prepared['row']['title'],prepared['row']['body']),'neutral')

    def test_cli_preview_and_single_self_send_never_touch_production_outbox(self):
        from quota_monitor.signal_email import main
        from quota_monitor.desktop_settings import save_settings
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.browser.local.toml'
            path.write_text('[monitor]\ndatabase="data/production.db"\n[notifications]\nchannel="smtp"\n'
                            'username="me@example.com"\nto="me@example.com;other@example.com"\nQQ_SMTP_AUTH_CODE="test-only"\n'
                            '[[accounts]]\nusername="thsottiaux"\nid="1953337039510003712"\nkind="personal"\nverified=true\n',encoding='utf-8')
            save_settings(path,[{'email':'me@example.com','enabled':False,'accept_uncertain':False,'delivery_mode':'digest_daily'},
                                {'email':'other@example.com'}])
            report={'collected_at':'2026-09-22T14:40:00Z','posts':[self.post]}
            with patch('quota_monitor.browser.collect_report',return_value=report), patch('quota_monitor.notify.SMTPNotifier') as smtp, redirect_stdout(StringIO()):
                self.assertEqual(main(['--config',str(path)]),0)
                smtp.assert_not_called()
                self.assertEqual(main(['--config',str(path),'--send-to-self']),0)
                smtp.return_value.send_to.assert_called_once()
                row,address=smtp.return_value.send_to.call_args.args
                self.assertEqual(address,'me@example.com')
                self.assertIn('来源：X 公开详情页',row['body'])
            self.assertFalse((Path(temp)/'data/production.db').exists())
            html=(Path(temp)/'data/email-preview/latest-signal.html').read_text(encoding='utf-8')
            self.assertIn('每日 20:00 汇总',html)
            self.assertIn('不接收',html)
            self.assertNotIn('me@example.com',html)
            self.assertNotIn('other@example.com',html)
            for internal_label in ('演示', '测试', '绕过日常筛选'):
                self.assertNotIn(internal_label,html)
            self.assertIn('待核实',html)
            self.assertIn('尚未宣布执行完成',html)
            self.assertIn('可选发送方式',html)
