import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ('X_BEARER_TOKEN', 'QQ_SMTP_AUTH_CODE')}
        env['PYTHONIOENCODING'] = 'utf-8'
        return subprocess.run([sys.executable, '-m', 'quota_monitor', *args], capture_output=True,
                              encoding='utf-8', env=env, timeout=15)

    def test_demo_is_offline_and_prints_candidates(self):
        r = self.run_cli('demo')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('即将重置候选', r.stdout)
        self.assertIn('重置券线索', r.stdout)
        self.assertIn('合成样例', r.stdout)

    def test_example_cannot_run_without_setup(self):
        r = self.run_cli('run', '--config', 'config.example.toml')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn('Traceback', r.stderr)

    def test_missing_config_is_actionable(self):
        r = self.run_cli('check', '--config', 'missing.toml')
        self.assertEqual(r.returncode, 2)
        self.assertIn('配置', r.stderr)

    def test_status_does_not_create_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'config.toml'
            p.write_text('[monitor]\ndatabase="data/state.db"\n', encoding='utf-8')
            r = self.run_cli('status', '--config', str(p))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((Path(tmp) / 'data').exists())

    def test_old_destination_does_not_block_cli_startup(self):
        from unittest.mock import patch
        from quota_monitor.cli import main
        from quota_monitor.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.toml'
            p.write_text('[monitor]\ndatabase="state.db"\n[[accounts]]\nusername="demo"\nid="123"\nkind="personal"\nverified=true\n',encoding='utf-8')
            store=Store(Path(tmp)/'state.db')
            try: store.set('destination','console:previous')
            finally: store.close()
            with patch('quota_monitor.browser.BrowserMonitor.collect', lambda self: self.stop.set()):
                self.assertEqual(main(['browser-run','--config',str(p)]), 0)

    def test_preview_reconciles_targets_without_sending(self):
        from unittest.mock import patch
        from quota_monitor.cli import main
        from quota_monitor.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'config.toml'
            p.write_text('[monitor]\ndatabase="state.db"\n[[accounts]]\nusername="demo"\nid="123"\nkind="personal"\nverified=true\n[notifications]\nchannel="smtp"\nusername="sender@example.com"\nto="a@example.com"\n', encoding='utf-8')
            s = Store(Path(tmp) / 'state.db')
            s.set('destination', 'smtp:a@example.com;b@example.com')
            s.enqueue('old', 'title', 'body', due=9999999999)
            s.close()
            with patch('quota_monitor.browser.collect_report', return_value={'posts': []}), \
                 patch('quota_monitor.browser.ingest'), patch('quota_monitor.browser.save_report'), \
                 patch('quota_monitor.browser.summary', return_value={}), \
                 patch('quota_monitor.notify.SMTPNotifier.send_to') as send:
                self.assertEqual(main(['browser-once', '--config', str(p)]), 1)
                send.assert_not_called()
            s = Store(Path(tmp) / 'state.db')
            try:
                s.configure_delivery('smtp:a@example.com;b@example.com')
                self.assertEqual(s.db.execute('SELECT destination FROM outbox').fetchone()[0], 'smtp:a@example.com')
            finally: s.close()


if __name__ == '__main__':
    unittest.main()
