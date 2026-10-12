import base64
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT = WORKSPACE / 'scripts' / 'build-portable.py'
TEST_TMP_ROOT = WORKSPACE / 'build' / 'portable-test-fixtures'


def load_builder():
    spec = importlib.util.spec_from_file_location('portable_builder', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PortableBuildTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'portable build entry point has not been implemented')
        self.builder = load_builder()
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    def test_source_allowlist_excludes_private_and_unreviewed_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            for name in self.builder.APP_SOURCE_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('public fixture', encoding='utf-8')
            forbidden = [
                'data/desktop/python-path.txt', 'logs/monitor.log',
                'config.browser.local.toml', '.npmrc', '.git/config',
                'quota_monitor/__pycache__/cli.pyc', 'quota_monitor/private.py',
                'assets/email/private.png', 'docs/private-note.md',
            ]
            for name in forbidden:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('private fixture', encoding='utf-8')
            selected = self.builder.collect_application_files(root)
            names = {path.relative_to(root).as_posix() for path in selected}
            self.assertIn('quota_monitor/cli.py', names)
            self.assertIn('quota_monitor/browser_collect.cjs', names)
            self.assertIn('config.browser.example.toml', names)
            self.assertIn('docs/PUBLISH.md', names)
            self.assertTrue(names.isdisjoint(forbidden))
            self.assertNotIn('CodexQuotaMonitor.exe', names)

    def test_compiler_sources_include_portable_configuration(self):
        self.assertIn('PortableVerification.cs', self.builder.DESKTOP_SOURCE_FILES)

    def test_runtime_installation_keeps_runtime_layout_and_bundled_licenses(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            stage = root / 'stage'
            stage.mkdir()
            inputs = {'python': root / 'python.zip', 'node': root / 'node.zip'}
            with zipfile.ZipFile(inputs['python'], 'w') as zipped:
                zipped.writestr('python.exe', b'python fixture')
                zipped.writestr('python314._pth', 'python314.zip\n.\n#import site\n')
                zipped.writestr('LICENSE.txt', 'PSF fixture license')
            prefix = 'node-v' + self.builder.NODE_VERSION + '-win-x64/'
            with zipfile.ZipFile(inputs['node'], 'w') as zipped:
                zipped.writestr(prefix + 'node.exe', b'node fixture')
                zipped.writestr(prefix + 'LICENSE', 'MIT fixture license')
                zipped.writestr(prefix + 'npm.cmd', 'not needed')
            for package in ['playwright', 'playwright-core']:
                inputs[package] = root / (package + '.tgz')
                with tarfile.open(inputs[package], 'w:gz') as tar:
                    contents = {
                        'package/package.json': json.dumps({'name': package, 'version': '1.62.1'}).encode(),
                        'package/LICENSE': b'Apache fixture license',
                        'package/index.js': b'module.exports = {};',
                    }
                    for name, data in contents.items():
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        tar.addfile(member, io.BytesIO(data))
            self.builder.install_runtimes(inputs, stage)
            self.assertTrue((stage / 'runtime/python/python.exe').is_file())
            self.assertTrue((stage / 'runtime/node/node.exe').is_file())
            self.assertFalse((stage / 'runtime/node/npm.cmd').exists())
            self.assertTrue((stage / 'node_modules/playwright-core/LICENSE').is_file())
            self.assertTrue((stage / 'licenses/Python.txt').is_file())
            self.assertTrue((stage / 'licenses/Node.txt').is_file())
            self.assertTrue((stage / 'THIRD_PARTY_NOTICES.txt').is_file())

    def test_allowlist_cannot_follow_symlink_outside_workspace(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary) / 'workspace'
            root.mkdir()
            for name in self.builder.APP_SOURCE_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('public', encoding='utf-8')
            link = root / 'README.md'
            link.unlink()
            outside = Path(temporary) / 'private.txt'
            outside.write_text('private', encoding='utf-8')
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest('symlinks require Windows developer mode')
            with self.assertRaises(self.builder.BuildError):
                self.builder.collect_application_files(root)

    def test_archive_paths_reject_traversal_and_windows_aliases(self):
        for name in ['../private', '/absolute', 'C:/private', 'a/../../b',
                     'a\\private', 'a:stream', 'a//b', 'a/./b', 'NUL.txt',
                     'foo./bar', 'foo /bar', '//server/share']:
            with self.subTest(name=name), self.assertRaises(self.builder.BuildError):
                self.builder.safe_archive_path(name)
        self.assertEqual(self.builder.safe_archive_path('package/lib/main.js').as_posix(),
                         'package/lib/main.js')

    def test_zip_extraction_preflights_before_writing_any_member(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            archive = Path(temporary) / 'bad.zip'
            output = Path(temporary) / 'output'
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.writestr('valid.txt', 'public')
                zipped.writestr('../escaped.txt', 'private')
            with self.assertRaises(self.builder.BuildError):
                self.builder.extract_zip(archive, output)
            self.assertFalse((output / 'valid.txt').exists())
            self.assertFalse((Path(temporary) / 'escaped.txt').exists())

    def test_zip_extraction_rejects_symlink_and_case_collision(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            for kind in ['symlink', 'collision']:
                archive = Path(temporary) / (kind + '.zip')
                with zipfile.ZipFile(archive, 'w') as zipped:
                    if kind == 'symlink':
                        member = zipfile.ZipInfo('link')
                        member.create_system = 3
                        member.external_attr = 0o120777 << 16
                        zipped.writestr(member, '../private')
                    else:
                        zipped.writestr('LICENSE', 'one')
                        zipped.writestr('license', 'two')
                with self.subTest(kind=kind), self.assertRaises(self.builder.BuildError):
                    self.builder.extract_zip(archive, Path(temporary) / kind)

    def test_tar_extraction_rejects_links_and_wrong_package_prefix(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            for name, linked in [('package/link', True), ('other/main.js', False)]:
                archive = Path(temporary) / ('link.tgz' if linked else 'prefix.tgz')
                with tarfile.open(archive, 'w:gz') as tar:
                    member = tarfile.TarInfo(name)
                    if linked:
                        member.type = tarfile.SYMTYPE
                        member.linkname = '../../private'
                        tar.addfile(member)
                    else:
                        member.size = 4
                        tar.addfile(member, io.BytesIO(b'code'))
                with self.subTest(name=name), self.assertRaises(self.builder.BuildError):
                    self.builder.extract_npm_tarball(archive, Path(temporary) / 'module')

    def test_checksums_reject_tampered_download(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            path = Path(temporary) / 'download.zip'
            path.write_bytes(b'official fixture')
            sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            integrity = 'sha512-' + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
            self.builder.verify_sha256(path, sha256)
            self.builder.verify_integrity(path, integrity)
            path.write_bytes(b'tampered')
            with self.assertRaises(self.builder.BuildError):
                self.builder.verify_sha256(path, sha256)
            with self.assertRaises(self.builder.BuildError):
                self.builder.verify_integrity(path, integrity)

    def test_official_download_opener_preserves_platform_network_route(self):
        self.assertTrue(hasattr(self.builder, 'official_download_opener'),
                        'official downloads need a platform-route-aware opener')
        opener = self.builder.official_download_opener()
        handlers = [handler for handler in opener.handlers if isinstance(handler, urllib.request.ProxyHandler)]
        configured = urllib.request.getproxies()
        if configured:
            self.assertEqual(len(handlers), 1)
            self.assertEqual(handlers[0].proxies, configured)
        self.assertFalse(any(isinstance(handler, urllib.request.HTTPBasicAuthHandler) for handler in opener.handlers))

    def test_offline_cache_rejects_tampering_and_unapproved_urls(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            cache = Path(temporary)
            (cache / 'runtime.zip').write_bytes(b'tampered')
            with self.assertRaises(self.builder.BuildError):
                self.builder.cached_download(cache, 'runtime.zip', self.builder.PYTHON_URL,
                                             offline=True, sha256=self.builder.PYTHON_SHA256)
            with self.assertRaises(self.builder.BuildError):
                self.builder.cached_download(cache, 'runtime.zip', 'https://example.org/runtime.zip', offline=True)
            with self.assertRaises(self.builder.BuildError):
                self.builder.cached_download(cache, 'missing.zip', self.builder.PYTHON_URL, offline=True)

    def test_fresh_stage_never_reuses_existing_directory_or_leaves_workspace(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            first = self.builder.create_fresh_stage(root, '0.2.0')
            (first / 'sentinel.txt').write_text('retain', encoding='utf-8')
            second = self.builder.create_fresh_stage(root, '0.2.0')
            self.assertNotEqual(first, second)
            self.assertEqual((first / 'sentinel.txt').read_text(), 'retain')
            self.assertTrue(second.is_relative_to(root.resolve() / 'build' / 'portable'))
            with self.assertRaises(self.builder.BuildError):
                self.builder.create_fresh_stage(root, '../outside')

    def test_embedded_python_paths_use_relative_app_root_and_fixture_cwd(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            runtime = Path(temporary)
            (runtime / 'python314._pth').write_text('python314.zip\n.\n#import site\n')
            self.builder.configure_embedded_python(runtime)
            text = (runtime / 'python314._pth').read_text()
            self.assertIn('python314.zip\n.\n../..\nimport site\n', text)
            startup = (runtime / 'sitecustomize.py').read_text()
            self.assertIn('os.getcwd()', startup)
            self.assertIn('sys.path.insert(0,', startup)
            self.assertNotIn(str(runtime), startup)

    def test_release_zip_is_clean_deterministic_and_matches_checksum(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'config.browser.example.toml').write_text('[monitor]\n', encoding='utf-8')
            (stage / 'CodexQuotaMonitor.exe').write_bytes(b'compiled fixture')
            (stage / 'runtime-provenance.json').write_text(json.dumps({'python': {'version': '3.14.8'}}))
            archive, sums = self.builder.write_release_zip(stage, root / 'dist', '0.2.0')
            first = archive.read_bytes()
            checksum = sums.read_text().split()[0]
            self.assertEqual(checksum, hashlib.sha256(first).hexdigest())
            with zipfile.ZipFile(archive) as zipped:
                names = zipped.namelist()
                self.assertIn('CodexQuotaMonitor-v0.2.0-windows-x64/config.browser.example.toml', names)
                self.assertTrue(all('/data/' not in name and '/logs/' not in name for name in names))
            self.builder.write_release_zip(stage, root / 'dist', '0.2.0')
            self.assertEqual(first, archive.read_bytes())

    def test_release_zip_refuses_private_artifacts_even_if_stage_was_contaminated(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temporary:
            root = Path(temporary)
            for name in ['config.browser.local.toml', 'data/history.db', 'logs/session.log',
                         'runtime/python/python-path.txt', 'profiles/Default/Cookies']:
                stage = root / 'stage'
                stage.mkdir(exist_ok=True)
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'private')
                with self.subTest(name=name), self.assertRaises(self.builder.BuildError):
                    self.builder.write_release_zip(stage, root / 'dist', '0.2.0')
                path.unlink()


if __name__ == '__main__':
    unittest.main()
