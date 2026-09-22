import os
import unittest
from unittest.mock import MagicMock, patch

from quota_monitor.config import Config, ConfigError
from quota_monitor.notify import SMTPNotifier


class MailConfigTests(unittest.TestCase):
    def config(self, **extra):
        return Config({'notifications': {'channel': 'smtp', 'username': 'sender@example.com',
                                         'to': 'receiver@example.com', **extra}})

    def test_config_takes_precedence_and_is_used_for_smtp_login(self):
        c = self.config(QQ_SMTP_AUTH_CODE=' local-test-code ')
        factory = MagicMock()
        smtp = factory.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        with patch.dict(os.environ, {'QQ_SMTP_AUTH_CODE': 'environment-test-code'}):
            c.validate_smtp_auth()
            SMTPNotifier(c, factory).send({'event_key': 'test', 'title': 'test', 'body': 'test'})
        smtp.login.assert_called_once_with('sender@example.com', 'local-test-code')
        self.assertNotIn('local-test-code', str(smtp.send_message.call_args))

    def test_blank_config_falls_back_to_environment(self):
        with patch.dict(os.environ, {'QQ_SMTP_AUTH_CODE': 'environment-test-code'}):
            self.assertEqual(self.config(QQ_SMTP_AUTH_CODE=' ').smtp_auth_code, 'environment-test-code')

    def test_missing_auth_fails_before_network(self):
        factory = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            c = self.config()
            with self.assertRaises(ConfigError):
                c.validate_smtp_auth()
            with self.assertRaises(ConfigError):
                SMTPNotifier(c, factory).send({'event_key': 'test', 'title': 'test', 'body': 'test'})
        factory.assert_not_called()

    def test_invalid_type_is_rejected_without_echoing_value(self):
        with self.assertRaises(ConfigError) as error:
            _ = self.config(QQ_SMTP_AUTH_CODE=987654).smtp_auth_code
        self.assertNotIn('987654', str(error.exception))
