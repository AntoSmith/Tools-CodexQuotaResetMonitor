import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import unittest
import zipfile
import subprocess
from pathlib import Path
from unittest.mock import patch


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT = WORKSPACE / 'scripts' / 'build-installer.py'
INSTALLER_SCRIPT = WORKSPACE / 'installer' / 'CodexQuotaMonitor.iss'
TEST_TMP_ROOT = WORKSPACE / 'build' / 'installer-test-fixtures'


def load_builder():
    spec = importlib.util.spec_from_file_location('installer_builder', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerBuildTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'installer build entry point has not been implemented')
        self.builder = load_builder()
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    def make_archive(self, root, additions=None, version='0.2.3', package_version=None):
        root = Path(root)
        destination = root / 'dist'
        destination.mkdir(exist_ok=True)
        prefix = 'CodexQuotaMonitor-v' + version + '-windows-x64'
        files = {name: b'public fixture' for name in self.builder.portable.APP_SOURCE_FILES}
        files.update({
            'CodexQuotaMonitor.exe': b'MZ application fixture',
            'package.json': json.dumps({'name': 'codex-quota-reset-monitor',
                                        'version': package_version or version}).encode(),
            'runtime-provenance.json': json.dumps({'application_version': version,
                                                  'target': 'windows-x64'}).encode(),
            'THIRD_PARTY_NOTICES.txt': b'notices',
            'runtime/python/python.exe': b'MZ python fixture',
            'runtime/node/node.exe': b'MZ node fixture',
            'node_modules/playwright/package.json': b'{"name":"playwright"}',
            'node_modules/playwright-core/package.json': b'{"name":"playwright-core"}',
        })
        files.update(additions or {})
        archive = destination / (prefix + '.zip')
        with zipfile.ZipFile(archive, 'w') as zipped:
            for name, data in files.items():
                zipped.writestr(prefix + '/' + name, data)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (destination / 'SHA256SUMS.txt').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
        return archive

    def make_compiler(self, root):
        compiler = Path(root) / 'tools' / 'ISCC.exe'
        compiler.parent.mkdir(exist_ok=True)
        compiler.write_bytes(b'MZ compiler fixture')
        language = compiler.parent / 'Languages' / 'ChineseSimplified.isl'
        language.parent.mkdir()
        language.write_text('; verified language fixture', encoding='utf-8')
        return compiler

    def test_version_and_output_name_are_bound_to_archive(self):
        self.assertEqual(self.builder.archive_version(Path('CodexQuotaMonitor-v0.2.3-windows-x64.zip')), '0.2.3')
        self.assertEqual(self.builder.setup_name('0.2.3'), 'CodexQuotaMonitor-Setup-v0.2.3-windows-x64.exe')
        for name in ['other.zip', 'CodexQuotaMonitor-v1.2.70000-windows-x64.zip',
                     'CodexQuotaMonitor-v1.2.3+bad-windows-x64.zip']:
            with self.subTest(name=name), self.assertRaises(self.builder.BuildError):
                self.builder.archive_version(Path(name))

    def test_stage_comes_only_from_allowlisted_archive_and_has_matching_version(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            (root / 'config.browser.local.toml').write_text('SMTP_SECRET_DO_NOT_COPY')
            stage = root / 'stage'
            version = self.builder.prepare_archive(archive, stage)
            self.assertEqual(version, '0.2.3')
            self.assertTrue((stage / 'CodexQuotaMonitor.exe').is_file())
            self.assertFalse((stage / 'config.browser.local.toml').exists())
            self.assertEqual(json.loads((stage / 'package.json').read_text())['version'], version)

    def test_private_unreviewed_and_unsafe_members_are_rejected_before_extraction(self):
        for name in ['config.browser.local.toml', 'data/history.db', 'logs/monitor.log',
                     'profiles/Default/Cookies', 'quota_monitor/private.py', '../escaped.txt',
                     'runtime/python/.npmrc', 'runtime/node/Cookies', 'assets/CON.txt']:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
                root = Path(temporary)
                archive = self.make_archive(root, {name: b'SMTP_SECRET_DO_NOT_COPY'})
                stage = root / 'stage'
                with self.assertRaises(self.builder.BuildError):
                    self.builder.prepare_archive(archive, stage)
                self.assertFalse(stage.exists(), 'all archive validation must finish before stage creation')
                self.assertFalse((root / 'escaped.txt').exists())

    def test_zip_links_and_case_collisions_are_rejected_before_extraction(self):
        for kind in ['link', 'collision']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
                root = Path(temporary)
                archive = self.make_archive(root)
                prefix = archive.stem + '/'
                with zipfile.ZipFile(archive, 'a') as zipped:
                    if kind == 'link':
                        member = zipfile.ZipInfo(prefix + 'runtime/python/link')
                        member.create_system = 3
                        member.external_attr = (stat.S_IFLNK | 0o777) << 16
                        zipped.writestr(member, '../private')
                    else:
                        zipped.writestr(prefix + 'README.MD', 'duplicate on Windows')
                self.write_archive_sum(archive)
                with self.assertRaises(self.builder.BuildError):
                    self.builder.prepare_archive(archive, root / 'stage')
                self.assertFalse((root / 'stage').exists())

    def test_file_directory_conflicts_are_rejected_before_any_extraction(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root, {'runtime/python/conflict': b'file',
                                                'runtime/python/conflict/child': b'child'})
            with self.assertRaises((self.builder.BuildError, OSError)):
                self.builder.prepare_archive(archive, root / 'stage')
            self.assertFalse((root / 'stage').exists())

    def write_archive_sum(self, archive):
        (archive.parent / 'SHA256SUMS.txt').write_text(
            hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n', encoding='ascii')

    def test_missing_or_tampered_archive_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            sums = archive.parent / 'SHA256SUMS.txt'
            sums.unlink()
            with self.assertRaisesRegex(self.builder.BuildError, 'SHA256SUMS'):
                self.builder.prepare_archive(archive, root / 'stage')
            sums.write_text('0' * 64 + '  ' + archive.name + '\n')
            with self.assertRaisesRegex(self.builder.BuildError, 'SHA256'):
                self.builder.prepare_archive(archive, root / 'stage')
            self.assertFalse((root / 'stage').exists())

    def test_mismatched_embedded_version_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root, package_version='0.2.2')
            with self.assertRaisesRegex(self.builder.BuildError, 'version'):
                self.builder.prepare_archive(archive, root / 'stage')
            self.assertFalse((root / 'stage').exists())

    def test_incomplete_archive_is_rejected_even_with_a_valid_checksum(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            with zipfile.ZipFile(archive) as zipped:
                members = [(info.filename, zipped.read(info)) for info in zipped.infolist()
                           if not info.filename.endswith('/runtime/python/python.exe')]
            with zipfile.ZipFile(archive, 'w') as zipped:
                for name, data in members:
                    zipped.writestr(name, data)
            self.write_archive_sum(archive)
            with self.assertRaisesRegex(self.builder.BuildError, 'missing required'):
                self.builder.prepare_archive(archive, root / 'stage')
            self.assertFalse((root / 'stage').exists())

    def test_archive_size_limits_fail_before_stage_creation(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            with patch.object(self.builder, 'MAX_EXPANDED_BYTES', 1):
                with self.assertRaisesRegex(self.builder.BuildError, 'size limits'):
                    self.builder.prepare_archive(archive, root / 'stage')
            self.assertFalse((root / 'stage').exists())

    def test_checksum_update_retains_zip_and_other_release_hashes(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            sums = archive.parent / 'SHA256SUMS.txt'
            original = sums.read_text()
            sums.write_text(original + 'a' * 64 + '  older.zip\n')
            setup = archive.parent / self.builder.setup_name('0.2.3')
            setup.write_bytes(b'MZ setup fixture')
            self.builder.update_checksums(sums, [archive, setup])
            text = sums.read_text()
            self.assertIn(original.strip(), text)
            self.assertIn('a' * 64 + '  older.zip', text)
            self.assertIn(hashlib.sha256(setup.read_bytes()).hexdigest() + '  ' + setup.name, text)
            self.assertEqual(len(text.splitlines()), 3)

    def test_missing_explicit_compiler_or_language_fails_without_downloading(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(self.builder.BuildError, 'ISCC'):
                self.builder.discover_iscc(root / 'missing.exe', workspace=root)
            compiler = self.make_compiler(root)
            self.assertEqual(self.builder.discover_iscc(compiler, workspace=root), compiler.resolve())
            (compiler.parent / 'Languages' / 'ChineseSimplified.isl').unlink()
            with self.assertRaisesRegex(self.builder.BuildError, 'ChineseSimplified'):
                self.builder.compiler_language(compiler)

    def test_compiler_receives_only_fresh_stage_and_artifact_is_atomically_published(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            compiler = self.make_compiler(root)
            destination = archive.parent / self.builder.setup_name('0.2.3')
            destination.write_bytes(b'old setup')
            commands = []

            def compile_fixture(command, **options):
                commands.append(command)
                defines = {value[2:].split('=', 1)[0]: value.split('=', 1)[1]
                           for value in command if value.startswith('/D') and '=' in value}
                stage = Path(defines['AppSourceDir'])
                self.assertTrue(stage.is_relative_to(root / 'build' / 'installer'))
                self.assertNotEqual(stage, root)
                self.assertTrue((stage / 'CodexQuotaMonitor.exe').is_file())
                self.assertEqual(defines['AppVersion'], '0.2.3')
                self.assertEqual(destination.read_bytes(), b'old setup')
                output = Path(defines['SetupOutputDir']) / (defines['SetupOutputName'] + '.exe')
                output.write_bytes(b'MZ compiled installer fixture')

            with patch.object(self.builder.subprocess, 'run', side_effect=compile_fixture):
                setup, sums = self.builder.build_installer(root, archive, compiler, script=INSTALLER_SCRIPT)
            self.assertEqual(len(commands), 1)
            self.assertEqual(setup, destination)
            self.assertEqual(setup.read_bytes(), b'MZ compiled installer fixture')
            self.assertIn(archive.name, sums.read_text())
            self.assertIn(setup.name, sums.read_text())
            self.assertFalse(Path(next(value.split('=', 1)[1] for value in commands[0]
                                       if value.startswith('/DAppSourceDir='))).exists())

    def test_compiler_failure_retains_previous_setup_and_checksums(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            compiler = self.make_compiler(root)
            setup = archive.parent / self.builder.setup_name('0.2.3')
            setup.write_bytes(b'MZ previous installer')
            sums = archive.parent / 'SHA256SUMS.txt'
            original_sums = sums.read_bytes()
            with patch.object(self.builder.subprocess, 'run',
                              side_effect=subprocess.CalledProcessError(1, 'ISCC')):
                with self.assertRaises(subprocess.CalledProcessError):
                    self.builder.build_installer(root, archive, compiler, script=INSTALLER_SCRIPT)
            self.assertEqual(setup.read_bytes(), b'MZ previous installer')
            self.assertEqual(sums.read_bytes(), original_sums)
            self.assertEqual(list((root / 'build' / 'installer').iterdir()), [])

    def test_installer_checks_updater_and_removes_only_its_owned_startup_value(self):
        text = INSTALLER_SCRIPT.read_text(encoding='utf-8-sig')
        self.assertIn('function PrepareToInstall', text)
        self.assertIn('update-recovery-required', text)
        self.assertIn('update.lock', text)
        self.assertIn('fmShareExclusive', text)
        self.assertIn('TransactionLock: TFileStream', text)
        self.assertIn('procedure ReleaseTransactionLock', text)
        self.assertIn('procedure CurStepChanged', text)
        self.assertIn('ssPostInstall', text)
        self.assertIn('procedure DeinitializeSetup', text)
        self.assertIn('procedure DeinitializeUninstall', text)
        self.assertIn('SuppressibleMsgBox(Reason', text)
        self.assertIn('OPEN_ALWAYS', text)
        self.assertNotIn('fmCreate', text)
        self.assertIn('procedure CurUninstallStepChanged', text)
        self.assertIn('usPostUninstall', text)
        self.assertIn('RegQueryStringValue(HKCU', text)
        self.assertIn('CompareText(CurrentCommand, InstalledCommand) = 0', text)
        self.assertIn('RegDeleteValue(HKCU', text)
        self.assertIn("' --autostart'", text)
        self.assertNotIn('RegDeleteKey', text)

    def test_fixture_compile_identity_is_separate_and_unexposed_by_production_builder(self):
        text = INSTALLER_SCRIPT.read_text(encoding='utf-8-sig')
        self.assertIn('#ifndef TestIdentity', text)
        self.assertIn('TestAppId', text)
        self.assertIn('TestAppMutex', text)
        self.assertIn('CodexQuotaMonitor-" + TestIdentity', text)
        source = SCRIPT.read_text(encoding='utf-8-sig')
        self.assertNotIn('/DTestIdentity', source)
        self.assertNotIn('/DTestAppId', source)

    def test_installer_contract_is_per_user_and_keeps_user_state(self):
        self.assertTrue(INSTALLER_SCRIPT.is_file())
        text = INSTALLER_SCRIPT.read_text(encoding='utf-8-sig')
        for required in ['PrivilegesRequired=lowest',
                         'DefaultDirName={localappdata}\\Programs\\CodexQuotaMonitor',
                         '#define InstallerMutex "Local\\CodexQuotaMonitorDesktop"', 'WizardStyle=modern',
                         'ChineseSimplified', 'skipifsilent', '--after-install',
                         '[Icons]', '[Tasks]', 'UninstallDisplayIcon={app}\\CodexQuotaMonitor.exe']:
            self.assertIn(required, text)
        self.assertNotIn('[UninstallDelete]', text)
        self.assertNotIn('[InstallDelete]', text)
        self.assertNotIn('config.browser.local.toml', text)
        self.assertNotIn('runascurrentuser', text)
        self.assertNotIn('restartreplace', text)
        task = next(line for line in text.splitlines() if line.startswith('Name: "desktopicon"'))
        self.assertNotIn('unchecked', task)


if __name__ == '__main__':
    unittest.main()
