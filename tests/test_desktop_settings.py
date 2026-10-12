import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quota_monitor.config import Config, ConfigError


DEFAULT_POLICY = dict(enabled=True, accept_uncertain=True, start_at='', end_at='', delivery_mode='immediate')


def contacts_with_defaults(rows):
    return [{**DEFAULT_POLICY, **row} for row in rows]


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config.browser.local.toml'
        self.original = '[notifications]\nchannel="smtp"\nusername="sender@example.com"\nto="old@example.com"\nQQ_SMTP_AUTH_CODE="secret-test-only"\n'
        self.path.write_text(self.original, encoding='utf-8')

    def test_ui_saved_list_overrides_only_recipients_and_survives_reload(self):
        from quota_monitor.desktop_settings import read_settings, save_settings
        self.assertEqual(read_settings(self.path)['recipients'], 'old@example.com')
        result = save_settings(self.path, 'a@example.com； b@example.com; c@example.com;a@example.com')
        self.assertEqual(result['count'], 3)
        c = Config.load(self.path)
        self.assertEqual(c.recipients, ['a@example.com', 'b@example.com', 'c@example.com'])
        self.assertEqual(c.smtp_auth_code, 'secret-test-only')
        self.assertEqual(c.section('notifications')['channel'], 'smtp')
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.original)
        self.assertEqual(read_settings(self.path)['source'], 'settings')
        self.assertNotIn('secret-test-only', json.dumps(result))

    def test_legacy_flat_save_preserves_existing_contact_names(self):
        from quota_monitor.desktop_settings import read_settings, save_settings
        save_settings(self.path, [{'name': '主邮箱', 'email': 'a@example.com'}])
        save_settings(self.path, 'a@example.com;b@example.com')
        self.assertEqual(read_settings(self.path)['contacts'],
                         contacts_with_defaults([{'name': '主邮箱', 'email': 'a@example.com'}, {'name': '', 'email': 'b@example.com'}]))

    def test_invalid_input_and_failed_write_preserve_current_settings(self):
        from quota_monitor.desktop_settings import save_settings
        save_settings(self.path, 'good@example.com')
        for text in ('', 'bad', 'a@example.com;;b@example.com', 'a@example.com\nBcc:other@example.com'):
            with self.assertRaises(ConfigError): save_settings(self.path, text)
        with patch('quota_monitor.desktop_settings.os.replace', side_effect=OSError('private path')):
            with self.assertRaises(OSError): save_settings(self.path, 'new@example.com')
        self.assertEqual(Config.load(self.path).recipients, ['good@example.com'])

    def test_other_config_files_do_not_inherit_desktop_recipients(self):
        from quota_monitor.desktop_settings import save_settings
        other = self.path.with_name('other.toml')
        other.write_text(self.original, encoding='utf-8')
        save_settings(self.path, 'new@example.com')
        self.assertEqual(Config.load(other).recipients, ['old@example.com'])

    def test_corrupt_override_is_reported_and_can_be_repaired_from_ui(self):
        from quota_monitor.desktop_settings import settings_path, save_settings
        p = settings_path(self.path)
        p.parent.mkdir(parents=True)
        p.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ConfigError, '邮箱设置'): Config.load(self.path)
        save_settings(self.path, 'repaired@example.com')
        self.assertEqual(Config.load(self.path).recipients, ['repaired@example.com'])

    def test_named_contacts_round_trip_and_derive_runtime_configuration(self):
        from quota_monitor.desktop_settings import read_settings, save_settings
        contacts = [{'name': '我的主邮箱', 'email': ' A@EXAMPLE.COM '},
                    {'name': '小王', 'email': 'b@example.com'}, {'name': '', 'email': 'c@example.com'}]
        result = save_settings(self.path, contacts)
        expected = [{'name': '我的主邮箱', 'email': 'A@example.com'},
                    {'name': '小王', 'email': 'b@example.com'}, {'name': '', 'email': 'c@example.com'}]
        expected = contacts_with_defaults(expected)
        self.assertEqual(result['contacts'], expected)
        self.assertEqual(read_settings(self.path)['contacts'], expected)
        config = Config.load(self.path)
        self.assertEqual(config.recipients, ['A@example.com', 'b@example.com', 'c@example.com'])
        self.assertEqual(config.section('notifications')['recipient_names'],
                         {'A@example.com': '我的主邮箱', 'b@example.com': '小王', 'c@example.com': ''})
        destination = config.destination
        contacts[0]['name'] = '工作邮箱'
        save_settings(self.path, contacts)
        self.assertEqual(Config.load(self.path).destination, destination)
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.original)

    def test_legacy_settings_get_unnamed_contacts_without_changing_addresses(self):
        from quota_monitor.desktop_settings import read_settings, settings_path, save_settings
        self.assertEqual(read_settings(self.path)['contacts'], contacts_with_defaults([{'name': '', 'email': 'old@example.com'}]))
        p = settings_path(self.path)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'version': 1, 'to': 'a@example.com;b@example.com'}), encoding='utf-8')
        contacts = read_settings(self.path)['contacts']
        self.assertEqual(contacts, contacts_with_defaults([{'name': '', 'email': 'a@example.com'}, {'name': '', 'email': 'b@example.com'}]))
        save_settings(self.path, contacts)
        self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['version'], 3)

    def test_duplicate_email_names_are_unambiguous_and_invalid_rows_do_not_save(self):
        from quota_monitor.desktop_settings import save_settings
        save_settings(self.path, [{'name': '', 'email': 'a@example.com'},
                                  {'name': '主邮箱', 'email': 'a@example.com'}])
        c = Config.load(self.path)
        self.assertEqual(c.recipients, ['a@example.com'])
        self.assertEqual(c.section('notifications')['recipient_names'], {'a@example.com': '主邮箱'})
        invalid = [[{'name': '备注', 'email': ''}], [{'name': '备注', 'email': 'a@example.com;b@example.com'}],
                   [{'name': 'x\ny', 'email': 'a@example.com'}], [{'name': 'x'*81, 'email': 'a@example.com'}],
                   [{'name': 123, 'email': 'a@example.com'}], [None],
                   [{'name': '甲', 'email': 'a@example.com'}, {'name': '乙', 'email': 'a@example.com'}]]
        for contacts in invalid:
            with self.subTest(contacts=contacts), self.assertRaises(ConfigError): save_settings(self.path, contacts)
            self.assertEqual(Config.load(self.path).section('notifications')['recipient_names'], {'a@example.com': '主邮箱'})


if __name__ == '__main__': unittest.main()
