import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from quota_monitor.browser import login_browser
from quota_monitor.cli import main
from quota_monitor.config import Config, ConfigError


class LoginTests(unittest.TestCase):
    def test_only_profile_and_public_target_passed_to_node(self):
        c=Config({'browser':{'profile':'data/login-profile','background':True,'headless':True},
                  'accounts':[{'username':'demo'}],
                  'notifications':{'QQ_SMTP_AUTH_CODE':'secret'}})
        process=MagicMock();process.returncode=0;process.poll.return_value=0
        with patch('quota_monitor.browser.subprocess.Popen',return_value=process) as spawn:
            login_browser(c)
        options=json.loads(process.communicate.call_args.args[0])
        self.assertEqual(set(options),{'profile','channel','username'})
        self.assertEqual(options['profile'],str(c.base/'data/login-profile'))
        self.assertNotIn('QQ_SMTP_AUTH_CODE',spawn.call_args.kwargs['env'])
        self.assertIn('browser_login.cjs',spawn.call_args.args[0][1])

    def test_cli_login_uses_monitor_lock_but_no_database_or_mail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.toml'
            p.write_text('[monitor]\ndatabase="data/state.db"\n[[accounts]]\nusername="demo"\nid="123"\nkind="personal"\nverified=true\n',encoding='utf-8')
            with patch('quota_monitor.browser.login_browser') as login,patch('quota_monitor.cli.Store') as store:
                self.assertEqual(main(['browser-login','--config',str(p)]),0)
                login.assert_called_once();store.assert_not_called()
            self.assertFalse((Path(tmp)/'data/state.db').exists())

    def test_login_refused_while_monitor_owns_database_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.toml'
            p.write_text('[monitor]\ndatabase="data/state.db"\n[[accounts]]\nusername="demo"\nid="123"\nkind="personal"\nverified=true\n',encoding='utf-8')
            with patch('quota_monitor.cli.single_instance',side_effect=ConfigError('此状态库已有监控进程运行')),patch('quota_monitor.browser.login_browser') as login:
                self.assertEqual(main(['browser-login','--config',str(p)]),2)
                login.assert_not_called()
