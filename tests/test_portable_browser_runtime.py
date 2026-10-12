import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quota_monitor import browser
from quota_monitor.config import Config, ConfigError


WORKSPACE = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = WORKSPACE / 'build' / 'portable-browser-test-fixtures'


class PortableBrowserRuntimeTests(unittest.TestCase):
    def setUp(self):
        FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=FIXTURE_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / '便携应用 with spaces'
        self.home = self.root / 'synthetic-home'
        self.config_dir = self.root / 'separate-config'
        source = self.app / 'quota_monitor' / 'browser.py'
        source.parent.mkdir(parents=True)
        source.write_text('# application root fixture\n', encoding='utf-8')
        self.enterContext(patch.object(browser, '__file__', str(source)))
        self.enterContext(patch.object(browser.Path, 'home', return_value=self.home))
        self.enterContext(patch.dict(os.environ, {'PATH': ''}, clear=True))
        self.config = Config({'browser': {}}, self.config_dir)

    def create_portable_node(self):
        node = self.app / 'runtime' / 'node' / 'node.exe'
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_bytes(b'synthetic runtime fixture')
        return node

    def create_codex_modules(self):
        bundled = self.home / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
        bundled.mkdir(parents=True)
        return bundled

    def test_portable_node_and_modules_work_without_node_in_path(self):
        expected_node = self.create_portable_node()
        modules = self.app / 'node_modules'
        modules.mkdir()
        os.environ['NODE_PATH'] = 'D:/unrelated/modules'
        with patch.object(browser.shutil, 'which', return_value=None):
            try:
                node, environment = browser.browser_runtime(self.config)
            except ConfigError as error:
                self.fail('Existing portable runtime must work without system Node: ' + str(error))
        self.assertEqual(node, str(expected_node))
        self.assertEqual(environment['NODE_PATH'], str(modules))
        self.assertNotEqual(self.config.base, self.app)

    def test_portable_node_is_preferred_to_a_system_installation(self):
        portable = self.create_portable_node()
        with patch.object(browser.shutil, 'which', return_value='D:/system/node.exe'):
            node, _ = browser.browser_runtime(self.config)
        self.assertEqual(node, str(portable))

    def test_application_modules_take_priority_over_codex_fallback(self):
        modules = self.app / 'node_modules'
        modules.mkdir()
        self.create_codex_modules()
        with patch.object(browser.shutil, 'which', return_value='D:/system/node.exe'):
            _, environment = browser.browser_runtime(self.config)
        self.assertEqual(environment['NODE_PATH'], str(modules))

    def test_source_checkout_keeps_system_node_and_codex_modules_fallback(self):
        bundled = self.create_codex_modules()
        os.environ['NODE_PATH'] = 'D:/existing/modules'
        with patch.object(browser.shutil, 'which', return_value='D:/system/node.exe'):
            node, environment = browser.browser_runtime(self.config)
        self.assertEqual(node, 'D:/system/node.exe')
        self.assertEqual(environment['NODE_PATH'], os.pathsep.join(['D:/existing/modules', str(bundled)]))

    def test_explicit_modules_remain_relative_to_configuration_and_take_priority(self):
        self.create_portable_node()
        (self.app / 'node_modules').mkdir()
        self.create_codex_modules()
        self.config.data['browser']['node_modules'] = 'custom-modules'
        with patch.object(browser.shutil, 'which', return_value='D:/system/node.exe'):
            _, environment = browser.browser_runtime(self.config)
        self.assertEqual(environment['NODE_PATH'], str(self.config.base / 'custom-modules'))

    def test_directory_named_node_exe_does_not_override_system_runtime(self):
        (self.app / 'runtime/node/node.exe').mkdir(parents=True)
        with patch.object(browser.shutil, 'which', return_value='D:/system/node.exe'):
            node, _ = browser.browser_runtime(self.config)
        self.assertEqual(node, 'D:/system/node.exe')

    def test_regular_file_named_node_modules_does_not_override_codex_dependencies(self):
        (self.app / 'node_modules').write_bytes(b'not a module directory')
        bundled = self.create_codex_modules()
        with patch.object(browser.shutil, 'which', return_value='D:/system/node.exe'):
            _, environment = browser.browser_runtime(self.config)
        self.assertEqual(environment['NODE_PATH'], str(bundled))

    def test_no_runtime_keeps_existing_configuration_error(self):
        with patch.object(browser.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ConfigError, 'Node.js 和 Playwright'):
                browser.browser_runtime(self.config)

    def test_both_runtime_routes_remove_sensitive_environment_values(self):
        os.environ.update(X_BEARER_TOKEN='synthetic bearer', QQ_SMTP_AUTH_CODE='synthetic smtp',
                          QUOTA_RUNTIME_FIXTURE='keep')
        for portable in (False, True):
            with self.subTest(portable=portable):
                if portable:
                    self.create_portable_node()
                with patch.object(browser.shutil, 'which', return_value='D:/system/node.exe'):
                    _, environment = browser.browser_runtime(self.config)
                self.assertNotIn('X_BEARER_TOKEN', environment)
                self.assertNotIn('QQ_SMTP_AUTH_CODE', environment)
                self.assertEqual(environment['QUOTA_RUNTIME_FIXTURE'], 'keep')
                self.assertEqual(os.environ['X_BEARER_TOKEN'], 'synthetic bearer')


if __name__ == '__main__':
    unittest.main()
