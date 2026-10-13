import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quota_monitor import desktop_settings
from quota_monitor.config import Config, ConfigError


class SetupSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'config.browser.local.toml'
        self.original = ('[notifications]\nchannel="smtp"\nhost="smtp.qq.com"\nport=465\n'
                         'username="base@example.com"\nto="old@example.com"\n'
                         'QQ_SMTP_AUTH_CODE="base-secret-only"\n[browser]\nchannel="chrome"\n'
                         '[[accounts]]\nusername="KnownUser"\nid="123"\nkind="personal"\nverified=false\n'
                         '[[accounts]]\nusername="Verified"\nid="456"\nkind="organization"\nverified=true\n')
        self.path.write_text(self.original, encoding='utf-8')
        self.transport = dict(channel='smtp', host='smtp.example.com', port=465,
                              username='new@example.com', auth_code='ui-secret-only')
        self.collection = dict(browser_channel='msedge', accounts=[
            dict(username='KnownUser', enabled=True), dict(username='Verified', enabled=False)])
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()

    def setup_api(self):
        self.assertTrue(callable(getattr(desktop_settings, 'read_setup', None)), 'setup read API is missing')
        self.assertTrue(callable(getattr(desktop_settings, 'save_setup', None)), 'setup save API is missing')
        return desktop_settings.read_setup, desktop_settings.save_setup

    def test_transport_round_trip_is_sanitized_and_does_not_change_toml(self):
        read, save = self.setup_api()
        initial = read(self.path)
        self.assertEqual(initial['transport']['auth_code_source'], 'config')
        result = save(self.path, {'transport': self.transport})
        self.assertEqual(result['transport'], dict(channel='smtp', host='smtp.example.com', port=465,
                          username='new@example.com', auth_code_present=True, auth_code_source='settings'))
        self.assertEqual(result, read(self.path))
        effective = Config.load(self.path)
        self.assertEqual(effective.smtp_auth_code, 'ui-secret-only')
        self.assertEqual(effective.recipients, ['old@example.com'])
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.original)
        self.assertNotIn('ui-secret-only', json.dumps(result))
        self.assertNotIn('base-secret-only', json.dumps(result))
        self.assertEqual(json.loads(desktop_settings.settings_path(self.path).read_text())['version'], 4)

    def test_blank_auth_keeps_secret_clear_tombstone_blocks_base_but_keeps_env_fallback(self):
        read, save = self.setup_api()
        save(self.path, {'transport': self.transport})
        save(self.path, {'transport': {**self.transport, 'auth_code': ''}})
        self.assertEqual(Config.load(self.path).smtp_auth_code, 'ui-secret-only')
        save(self.path, {'transport': {**self.transport, 'auth_code': '', 'clear_auth_code': True}})
        self.assertEqual(Config.load(self.path).smtp_auth_code, '')
        self.assertEqual(read(self.path)['transport']['auth_code_source'], 'none')
        with patch.dict(os.environ, {'QQ_SMTP_AUTH_CODE': 'environment-secret-only'}):
            self.assertEqual(Config.load(self.path).smtp_auth_code, 'environment-secret-only')
            self.assertEqual(read(self.path)['transport']['auth_code_source'], 'environment')
        save(self.path, {'transport': {**self.transport, 'auth_code': ''}})
        self.assertFalse(read(self.path)['transport']['auth_code_present'])

    def test_settings_secret_precedes_environment_and_console_can_be_saved_without_sender(self):
        read, save = self.setup_api()
        with patch.dict(os.environ, {'QQ_SMTP_AUTH_CODE': 'environment-secret-only'}):
            self.assertEqual(read(self.path)['transport']['auth_code_source'], 'config')
            save(self.path, {'transport': self.transport})
            self.assertEqual(Config.load(self.path).smtp_auth_code, 'ui-secret-only')
        save(self.path, {'transport': {**self.transport, 'channel': 'console', 'username': '', 'auth_code': ''}})
        self.assertEqual(read(self.path)['transport']['channel'], 'console')

    def test_collection_confirms_known_identity_and_filters_disabled_accounts(self):
        read, save = self.setup_api()
        initial = read(self.path)['collection']['accounts']
        self.assertFalse(initial[0]['enabled'])
        self.assertFalse(initial[0]['verified'])
        save(self.path, {'collection': self.collection})
        effective = Config.load(self.path)
        self.assertEqual(effective.section('browser')['channel'], 'msedge')
        self.assertEqual(effective.accounts, [dict(username='KnownUser', id='123', kind='personal', verified=True)])
        effective.validate_browser()
        rows = read(self.path)['collection']['accounts']
        self.assertEqual(rows[0], dict(username='KnownUser', id='123', kind='personal', verified=True, enabled=True))
        self.assertFalse(rows[1]['enabled'])
        saved = json.loads(desktop_settings.settings_path(self.path).read_text())
        self.assertEqual(saved['collection']['accounts'][0]['id'], '123')

    def test_collection_rejects_unknown_editable_identity_duplicate_and_empty_selection(self):
        _, save = self.setup_api()
        invalid = [dict(browser_channel='firefox', accounts=self.collection['accounts']),
                   dict(browser_channel='chrome', accounts=[dict(username='Other', enabled=True)]),
                   dict(browser_channel='chrome', accounts=[dict(username='KnownUser', id='999', enabled=True)]),
                   dict(browser_channel='chrome', accounts=[dict(username='KnownUser', enabled='true')]),
                   dict(browser_channel='chrome', accounts=[]),
                   dict(browser_channel='chrome', accounts=[dict(username='KnownUser', enabled=False)]),
                   dict(browser_channel='chrome', accounts=[dict(username='KnownUser', enabled=True),
                                                            dict(username='knownuser', enabled=True)])]
        for collection in invalid:
            with self.subTest(collection=collection), self.assertRaises(ConfigError):
                save(self.path, {'collection': collection})
        self.assertFalse(desktop_settings.settings_path(self.path).exists())

    def test_transport_rejects_injection_invalid_fields_and_never_reports_auth_code(self):
        _, save = self.setup_api()
        invalid = [{'host': 'smtp.example.com\r\nsecret'}, {'host': 'https://smtp.example.com'},
                   {'host': '-smtp.example.com'}, {'host': 'smtp..example.com'}, {'host': '邮件.example.com'},
                   {'port': True}, {'port': 0}, {'port': 65536}, {'port': '465'},
                   {'username': 'a@example.com;b@example.com'}, {'username': 'a@example.com;a@example.com'},
                   {'username': 'a@example.com\nBcc:x@example.com'},
                   {'auth_code': 'private-injected-secret\n'}, {'auth_code': 'private\x00secret'},
                   {'auth_code': 's'*513}, {'clear_auth_code': 'true'},
                   {'clear_auth_code': True}, {'channel': 'other'}]
        for change in invalid:
            with self.subTest(change=change):
                with self.assertRaises(ConfigError) as caught:
                    save(self.path, {'transport': {**self.transport, **change}})
                self.assertNotIn('private', str(caught.exception))
        self.assertFalse(desktop_settings.settings_path(self.path).exists())

    def test_saving_each_section_preserves_contacts_and_other_sections(self):
        read, save = self.setup_api()
        desktop_settings.save_settings(self.path, [{'name': '保留', 'email': 'one@example.com', 'enabled': False}])
        save(self.path, {'transport': self.transport})
        save(self.path, {'collection': self.collection})
        desktop_settings.save_settings(self.path, [{'name': '新名单', 'email': 'two@example.com'}])
        self.assertEqual(read(self.path)['transport']['auth_code_source'], 'settings')
        self.assertEqual(read(self.path)['collection']['browser_channel'], 'msedge')
        self.assertEqual(Config.load(self.path).recipients, ['two@example.com'])
        self.assertEqual(Config.load(self.path).smtp_auth_code, 'ui-secret-only')
        self.assertEqual(desktop_settings.read_settings(self.path)['channel'], 'smtp')

    def test_corrupt_json_and_transport_are_readable_for_repair_but_worker_rejects_them(self):
        read, save = self.setup_api()
        path = desktop_settings.settings_path(self.path)
        path.parent.mkdir(parents=True)
        for value in ('{broken', json.dumps({'version': 4, 'contacts': [], 'transport': {'channel': 'smtp'}})):
            path.write_text(value, encoding='utf-8')
            result = read(self.path)
            self.assertTrue(result['repair_needed'])
            self.assertEqual(result['transport']['host'], 'smtp.qq.com')
            with self.assertRaises(ConfigError): Config.load(self.path)
            save(self.path, {'transport': self.transport})
            self.assertFalse(read(self.path)['repair_needed'])
            self.assertEqual(Config.load(self.path).smtp_auth_code, 'ui-secret-only')

    def test_valid_contacts_survive_transport_repair_and_id_changes_require_new_confirmation(self):
        read, save = self.setup_api()
        desktop_settings.save_settings(self.path, [{'name': '保留', 'email': 'one@example.com'}])
        path = desktop_settings.settings_path(self.path)
        doc = json.loads(path.read_text())
        doc['transport'] = dict(channel='smtp', host='invalid\n', auth_code='ui-secret-only')
        path.write_text(json.dumps(doc), encoding='utf-8')
        save(self.path, {'transport': {**self.transport, 'auth_code': ''}})
        self.assertEqual(Config.load(self.path).recipients, ['one@example.com'])
        self.assertEqual(Config.load(self.path).smtp_auth_code, 'ui-secret-only')
        save(self.path, {'collection': self.collection})
        self.path.write_text(self.original.replace('id="123"', 'id="789"'), encoding='utf-8')
        self.assertTrue(read(self.path)['repair_needed'])
        with self.assertRaises(ConfigError): Config.load(self.path)

    def test_legacy_versions_migrate_without_losing_contact_policy(self):
        read, save = self.setup_api()
        path = desktop_settings.settings_path(self.path)
        path.parent.mkdir(parents=True)
        for version in (1, 2, 3):
            doc = dict(version=version, to='one@example.com') if version == 1 else dict(
                version=version, contacts=[dict(name='legacy', email='one@example.com', enabled=False)])
            path.write_text(json.dumps(doc), encoding='utf-8')
            self.assertFalse(read(self.path)['repair_needed'])
            save(self.path, {'transport': self.transport})
            self.assertEqual(Config.load(self.path).recipients, ['one@example.com'])
            self.assertEqual(json.loads(path.read_text())['version'], 4)
            if version > 1:
                self.assertFalse(desktop_settings.read_settings(self.path)['contacts'][0]['enabled'])

    def test_atomic_failure_preserves_all_previous_settings(self):
        _, save = self.setup_api()
        save(self.path, {'transport': self.transport, 'collection': self.collection})
        path = desktop_settings.settings_path(self.path)
        before = path.read_bytes()
        with patch('quota_monitor.desktop_settings.os.replace', side_effect=OSError('private secret path')):
            with self.assertRaises(OSError): save(self.path, {'transport': {**self.transport, 'host': 'smtp.other.com'}})
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_cli_read_save_and_errors_emit_only_sanitized_json(self):
        self.setup_api()
        for action, request, expected_code in [('save-setup', {'transport': self.transport}, 0),
                                               ('read-setup', None, 0),
                                               ('save-setup', {'transport': {**self.transport, 'auth_code': 'private-secret\n'}}, 1)]:
            output = io.StringIO()
            with patch('sys.stdin', io.StringIO(json.dumps(request))), redirect_stdout(output):
                code = desktop_settings.main([action, '--config', str(self.path)])
            result = json.loads(output.getvalue())
            self.assertEqual(code, expected_code)
            self.assertEqual(result['ok'], expected_code == 0)
            for secret in ('base-secret-only', 'ui-secret-only', 'private-secret'):
                self.assertNotIn(secret, output.getvalue())

    def test_placeholder_sender_and_recipient_are_hidden_only_when_not_saved(self):
        read, save = self.setup_api()
        self.path.write_text(self.original.replace('base@example.com', 'sender@example.com').replace(
            'old@example.com', 'recipient@example.com'), encoding='utf-8')
        self.assertEqual(read(self.path)['transport']['username'], '')
        self.assertEqual(desktop_settings.read_settings(self.path)['contacts'], [])
        desktop_settings.save_settings(self.path, [{'name': 'explicit', 'email': 'recipient@example.com'}])
        self.assertEqual(desktop_settings.read_settings(self.path)['contacts'][0]['email'], 'recipient@example.com')

    def test_readiness_requires_confirmed_collection_and_mail_configuration(self):
        read, save = self.setup_api()
        initial = read(self.path)
        self.assertIn('ready', initial)
        self.assertFalse(initial['ready'])
        self.assertIn('监控账号', initial['missing_steps'])
        self.assertTrue(save(self.path, {'collection': self.collection})['ready'])
        cleared = save(self.path, {'transport': {**self.transport, 'auth_code': '', 'clear_auth_code': True}})
        self.assertFalse(cleared['ready'])
        self.assertIn('SMTP 授权码', cleared['missing_steps'])
        with patch.dict(os.environ, {'QQ_SMTP_AUTH_CODE': 'environment-secret-only'}):
            self.assertTrue(read(self.path)['ready'])
        save(self.path, {'transport': self.transport})
        desktop_settings.save_settings(self.path, [])
        self.assertFalse(read(self.path)['ready'])
        self.assertIn('收件人', read(self.path)['missing_steps'])
        self.assertTrue(save(self.path, {'transport': {**self.transport, 'channel': 'console', 'auth_code': ''}})['ready'])

    def test_placeholder_and_malformed_worker_settings_report_safe_readiness_labels(self):
        read, save = self.setup_api()
        self.path.write_text(self.original.replace('base@example.com', 'sender@example.com').replace(
            'old@example.com', 'recipient@example.com'), encoding='utf-8')
        save(self.path, {'collection': self.collection})
        result = read(self.path)
        self.assertIn('ready', result)
        self.assertFalse(result['ready'])
        self.assertIn('发件邮箱', result['missing_steps'])
        self.assertIn('收件人', result['missing_steps'])
        self.path.write_text(self.original.replace('channel="chrome"', 'channel="firefox"'), encoding='utf-8')
        document = json.loads(desktop_settings.settings_path(self.path).read_text())
        document.pop('collection')
        desktop_settings.settings_path(self.path).write_text(json.dumps(document), encoding='utf-8')
        self.assertFalse(read(self.path)['ready'])
        self.assertIn('采集设置', read(self.path)['missing_steps'])
        desktop_settings.settings_path(self.path).write_text('{broken', encoding='utf-8')
        self.assertFalse(read(self.path)['ready'])

    def test_wrong_base_smtp_types_are_readable_and_can_be_repaired_with_gui_values(self):
        read, save = self.setup_api()
        malformed = ['host=123', 'port="bad"', 'username=123', 'QQ_SMTP_AUTH_CODE=123']
        originals = ['host="smtp.qq.com"', 'port=465', 'username="base@example.com"',
                     'QQ_SMTP_AUTH_CODE="base-secret-only"']
        for old, new in zip(originals, malformed):
            with self.subTest(field=old):
                desktop_settings.settings_path(self.path).unlink(missing_ok=True)
                self.path.write_text(self.original.replace(old, new), encoding='utf-8')
                self.assertTrue(read(self.path)['repair_needed'])
                save(self.path, {'transport': self.transport, 'collection': self.collection})
                self.assertTrue(read(self.path)['ready'])
        desktop_settings.settings_path(self.path).unlink(missing_ok=True)
        self.path.write_text(self.original.replace('to="old@example.com"', 'to=123'), encoding='utf-8')
        desktop_settings.save_settings(self.path, [{'name': 'repair', 'email': 'new@example.com'}])
        self.assertEqual(Config.load(self.path).recipients, ['new@example.com'])

    def test_malformed_base_sections_are_repairable_without_leaking_raw_details(self):
        read, save = self.setup_api()
        accounts = '[[accounts]]' + self.original.split('[[accounts]]', 1)[1]
        self.path.write_text('notifications="private-base-text"\nbrowser=123\n' + accounts, encoding='utf-8')
        output = io.StringIO()
        with redirect_stdout(output):
            code = desktop_settings.main(['read-setup', '--config', str(self.path)])
        self.assertEqual(code, 0)
        self.assertNotIn('private-base-text', output.getvalue())
        result = json.loads(output.getvalue())
        self.assertTrue(result['repair_needed'])
        self.assertFalse(result['ready'])
        save(self.path, {'transport': self.transport, 'collection': self.collection})
        self.assertFalse(read(self.path)['repair_needed'])
        self.assertEqual(Config.load(self.path).smtp_auth_code, 'ui-secret-only')


if __name__ == '__main__':
    unittest.main()
