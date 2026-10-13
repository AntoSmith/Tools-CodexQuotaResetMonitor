#!/usr/bin/env python3
"""Build the template-only Windows x64 distribution from an explicit allowlist.

Run with Python 3.11+ on Windows. Downloads use public official HTTPS endpoints
directly; neither npm configuration nor locally installed runtimes are copied.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath


PYTHON_VERSION = '3.14.8'
NODE_VERSION = '24.21.0'
PLAYWRIGHT_VERSION = '1.62.1'
PYTHON_FILENAME = 'python-3.14.8-embed-amd64.zip'
NODE_FILENAME = 'node-v24.21.0-win-x64.zip'
PYTHON_SHA256 = 'a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310'
NODE_SHA256 = '158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541'
PYTHON_URL = 'https://www.python.org/ftp/python/3.14.8/' + PYTHON_FILENAME
PYTHON_CHECKSUM_URL = 'https://www.python.org/downloads/release/python-3148/'
NODE_URL = 'https://nodejs.org/dist/v24.21.0/' + NODE_FILENAME
NODE_CHECKSUM_URL = 'https://nodejs.org/dist/v24.21.0/SHASUMS256.txt'
NPM_INTEGRITIES = {
    'playwright': 'sha512-0M+L3LAD8/nm554LOla9Ayx0j0tmFZ0FBcoQ7F1VuVHpM/XpiC8RcDzBQB8W5+hA8L22THxELzeF+2WcUzvcLg==',
    'playwright-core': 'sha512-wPYSwEBJY9GHraISXqyqtx0na0LpO3XEX7jNDhntbex7tzUS7kLnZsOlFruFJB4Hi/rhDMjXGqHewDZ68nYZVw==',
}
OFFICIAL_HOSTS = {'www.python.org', 'python.org', 'nodejs.org', 'registry.npmjs.org'}

# Review new application files before adding them here. Never replace this with
# recursive copying, Git working-tree export, or directory-wide wildcards.
APP_SOURCE_FILES = (
    'README.md', 'docs/USAGE.md', 'docs/PUBLISH.md', 'config.browser.example.toml', 'LoginX.cmd', 'package.json',
    'scripts/run.ps1',
    'quota_monitor/__init__.py', 'quota_monitor/__main__.py',
    'quota_monitor/background_browser.py', 'quota_monitor/browser.py',
    'quota_monitor/classify.py', 'quota_monitor/cli.py', 'quota_monitor/config.py',
    'quota_monitor/conversation.py', 'quota_monitor/delivery.py',
    'quota_monitor/desktop_settings.py', 'quota_monitor/email_html.py',
    'quota_monitor/email_message.py', 'quota_monitor/email_preview.py',
    'quota_monitor/email_test.py', 'quota_monitor/history.py', 'quota_monitor/notify.py',
    'quota_monitor/reset_feed.py', 'quota_monitor/divin_feed.py',
    'quota_monitor/source_notes.py', 'quota_monitor/service.py',
    'quota_monitor/signal_email.py', 'quota_monitor/store.py',
    'quota_monitor/subscription.py', 'quota_monitor/subscription_test.py',
    'quota_monitor/xapi.py', 'quota_monitor/browser_collect.cjs',
    'quota_monitor/browser_dom.cjs', 'quota_monitor/browser_environment.cjs',
    'quota_monitor/browser_errors.cjs', 'quota_monitor/browser_login.cjs',
    'assets/quota-monitor.ico', 'assets/quota-monitor.png', 'assets/quota-monitor.svg',
    'assets/feature-overview.svg', 'assets/screenshots/dashboard.png',
    'assets/screenshots/alert.png', 'assets/screenshots/history.png',
    'assets/screenshots/subscribers.png', 'assets/screenshots/email-demo.png',
    'assets/screenshots/mail-settings.png',
    'assets/email/celebrate.gif', 'assets/email/celebrate.png',
    'assets/email/neutral.gif', 'assets/email/neutral.png',
    'assets/email/uncertain.gif', 'assets/email/uncertain.png', 'assets/email/README.md',
)
DESKTOP_SOURCE_FILES = (
    'AlertWindow.cs', 'AppBrand.cs', 'DashboardState.cs', 'DashboardWindow.cs',
    'HistoryClient.cs', 'HistoryVerification.cs', 'HistoryWindow.cs', 'Launcher.cs',
    'PortableVerification.cs', 'RecipientSettings.cs', 'ReviewPanel.cs', 'SettingsWindow.cs', 'SetupPanels.cs', 'TrayApplication.cs',
    'UiVerification.cs',
)
FORBIDDEN_COMPONENTS = {
    'data', 'logs', '.git', '.codex', '.agents', '__pycache__',
    'profiles', 'user_data', 'user-data', 'browser-profile', 'browser-profiles',
    'github-export',
}
FORBIDDEN_FILENAMES = {
    'config.browser.local.toml', '.npmrc', 'python-path.txt', 'node-path.txt',
    'cookies', 'cookies.json', 'storage-state.json', 'login-state.json',
}


class BuildError(RuntimeError):
    """Fail closed when a release input or output cannot be verified."""


def release_name(version):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?', version):
        raise BuildError('Version must be a plain semantic version, for example 0.2.0')
    return 'CodexQuotaMonitor-v' + version + '-windows-x64'


def require_within(path, root):
    resolved = Path(path).resolve()
    resolved_root = Path(root).resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise BuildError('Path escapes its intended workspace directory')
    return resolved


def collect_application_files(workspace):
    workspace = Path(workspace).resolve()
    result = []
    for name in APP_SOURCE_FILES:
        source = workspace / name
        if not source.is_file() or source.is_symlink():
            raise BuildError('Missing or linked allowlisted application file: ' + name)
        require_within(source, workspace)
        # Reject junctions and symlinked ancestor directories as well as files.
        if source.resolve() != source.absolute():
            raise BuildError('Allowlisted source resolves through a link: ' + name)
        result.append(source)
    return result


def safe_archive_path(name):
    """Validate both POSIX extraction paths and Windows filename semantics."""
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise BuildError('Unsafe archive path')
    parts = name.rstrip('/').split('/')
    for part in parts:
        if not part or part in {'.', '..'} or part.endswith(('.', ' ')):
            raise BuildError('Unsafe archive path: ' + name)
        if re.search(r'[<>:"|?*\x00-\x1f]', part):
            raise BuildError('Unsafe Windows archive path: ' + name)
        stem = part.split('.', 1)[0].upper()
        if stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', stem):
            raise BuildError('Reserved Windows filename in archive: ' + name)
    return PurePosixPath(*parts)


def _check_duplicates(paths):
    seen = set()
    for path in paths:
        folded = path.as_posix().casefold()
        if folded in seen:
            raise BuildError('Duplicate or case-colliding archive entry: ' + path.as_posix())
        seen.add(folded)


def _extraction_target(root, path):
    target = Path(root).joinpath(*path.parts)
    return require_within(target, root)


def extract_zip(archive, destination, prefix=None, selected=None):
    """Preflight all entries, then extract without ZipFile.extractall()."""
    destination = Path(destination)
    with zipfile.ZipFile(archive) as zipped:
        entries = [(info, safe_archive_path(info.filename)) for info in zipped.infolist()]
        _check_duplicates(path for _, path in entries)
        for info, path in entries:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise BuildError('Archive contains a symlink: ' + path.as_posix())
            if prefix and path.parts[0] != prefix:
                raise BuildError('Archive does not use the expected runtime directory')
        for info, path in entries:
            if prefix:
                if len(path.parts) == 1:
                    if not info.is_dir():
                        raise BuildError('Runtime archive prefix must be a directory')
                    continue
                path = PurePosixPath(*path.parts[1:])
            if selected is not None and path.as_posix() not in selected:
                continue
            target = _extraction_target(destination, path)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(info) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)


def extract_npm_tarball(archive, destination):
    """Reject links, devices, duplicates and paths outside npm's package/ root."""
    destination = Path(destination)
    with tarfile.open(archive, 'r:gz') as tar:
        entries = [(member, safe_archive_path(member.name)) for member in tar.getmembers()]
        _check_duplicates(path for _, path in entries)
        for member, path in entries:
            if not (member.isdir() or member.isfile()) or path.parts[0] != 'package':
                raise BuildError('Unsafe npm archive member: ' + member.name)
            if len(path.parts) == 1 and not member.isdir():
                raise BuildError('npm package prefix must be a directory')
        for member, path in entries:
            if len(path.parts) == 1:
                continue
            target = _extraction_target(destination, PurePosixPath(*path.parts[1:]))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)


def file_hash(path, algorithm='sha256'):
    digest = hashlib.new(algorithm)
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path, expected):
    if not re.fullmatch('[a-fA-F0-9]{64}', expected) or not hmac.compare_digest(file_hash(path), expected.lower()):
        raise BuildError('SHA256 mismatch for ' + Path(path).name)


def verify_integrity(path, expected):
    try:
        algorithm, encoded = expected.split('-', 1)
        if algorithm != 'sha512':
            raise ValueError('Only pinned SHA512 npm integrity is supported')
        expected_digest = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise BuildError('Invalid npm integrity') from error
    actual = bytes.fromhex(file_hash(path, 'sha512'))
    if len(expected_digest) != 64 or not hmac.compare_digest(actual, expected_digest):
        raise BuildError('npm integrity mismatch for ' + Path(path).name)


def create_fresh_stage(workspace, version):
    workspace = Path(workspace).resolve()
    name = release_name(version)
    parent = require_within(workspace / 'build' / 'portable', workspace)
    parent.mkdir(parents=True, exist_ok=True)
    stage = require_within(parent / (name + '-stage-' + uuid.uuid4().hex), parent)
    stage.mkdir(exist_ok=False)
    # Each invocation creates a new directory; no existing stage is removed.
    return stage


def configure_embedded_python(runtime):
    runtime = Path(runtime)
    if not (runtime / 'python314._pth').is_file():
        raise BuildError('Embedded Python path configuration is missing')
    (runtime / 'python314._pth').write_text('python314.zip\n.\n../..\nimport site\n', encoding='utf-8')
    (runtime / 'sitecustomize.py').write_text(
        '# Keep GUI bridge fixtures and relocated application imports independent of PATH.\n'
        'import os\nimport sys\nsys.path.insert(0, os.getcwd())\n', encoding='utf-8')


def assert_release_clean(stage):
    stage = Path(stage).resolve()
    allowed_app = set(APP_SOURCE_FILES) | {'CodexQuotaMonitor.exe', 'runtime-provenance.json', 'THIRD_PARTY_NOTICES.txt'}
    files = []
    for path in sorted(stage.rglob('*')):
        if path.is_symlink() or path.resolve() != path.absolute():
            raise BuildError('Release contains a linked path')
        if not path.is_file():
            continue
        require_within(path, stage)
        relative = path.relative_to(stage).as_posix()
        components = relative.casefold().split('/')
        if any(part in FORBIDDEN_COMPONENTS for part in components):
            raise BuildError('Private/generated directory in release: ' + relative)
        if path.name.casefold() in FORBIDDEN_FILENAMES or path.suffix.casefold() in {'.pyc', '.log', '.db'}:
            raise BuildError('Private/generated file in release: ' + relative)
        if relative not in allowed_app:
            if components[0] == 'runtime' and len(components) > 2 and components[1] in {'python', 'node'}:
                pass
            elif components[0] == 'node_modules' and len(components) > 2 and components[1] in NPM_INTEGRITIES:
                pass
            elif components[0] == 'licenses' and len(components) == 2:
                pass
            else:
                raise BuildError('File is outside the release allowlist: ' + relative)
        files.append(path)
    if not files:
        raise BuildError('Release stage is empty')
    return files


def write_release_zip(stage, destination, version):
    stage, destination = Path(stage).resolve(), Path(destination).resolve()
    files = assert_release_clean(stage)
    name = release_name(version)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / (name + '.zip')
    partial = destination / (archive.name + '.' + uuid.uuid4().hex + '.part')
    try:
        with zipfile.ZipFile(partial, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
            for path in files:
                member = zipfile.ZipInfo(name + '/' + path.relative_to(stage).as_posix(), (2026, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED
                member.create_system = 3
                member.external_attr = 0o100644 << 16
                with path.open('rb') as source, zipped.open(member, 'w', force_zip64=True) as output:
                    shutil.copyfileobj(source, output)
        with zipfile.ZipFile(partial) as zipped:
            if zipped.testzip() is not None:
                raise BuildError('Output ZIP integrity test failed')
        partial.replace(archive)
    finally:
        if partial.exists():
            partial.unlink()
    sums = destination / 'SHA256SUMS.txt'
    sums.write_text(file_hash(archive) + '  ' + archive.name + '\n', encoding='ascii')
    return archive, sums


def _official_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in OFFICIAL_HOSTS or parsed.username or parsed.password:
        raise BuildError('Download URL is not an approved official public endpoint')


def official_download_opener():
    # Preserve the platform network route. No npm authentication, cookies or
    # explicit HTTP authentication handlers are installed for public downloads.
    return urllib.request.build_opener()


def cached_download(cache, filename, url, offline=False, sha256=None, integrity=None, max_bytes=150 * 1024 * 1024):
    _official_url(url)
    safe_archive_path(filename)
    if '/' in filename:
        raise BuildError('Cache filename must be a plain filename')
    cache = Path(cache).resolve()
    path = require_within(cache / filename, cache)
    if path.is_symlink():
        raise BuildError('Download cache contains a symlink')
    if not path.exists():
        if offline:
            raise BuildError('Offline cache is missing ' + filename)
        cache.mkdir(parents=True, exist_ok=True)
        partial = require_within(cache / (filename + '.' + uuid.uuid4().hex + '.part'), cache)
        print('Downloading ' + filename, flush=True)
        try:
            # Do not load a user npmrc, npm token or cookies.
            opener = official_download_opener()
            request = urllib.request.Request(url, headers={'User-Agent': 'CodexQuotaMonitor-PortableBuilder/0.2'})
            with opener.open(request, timeout=90) as response, partial.open('xb') as output:
                _official_url(response.url)
                downloaded = 0
                while chunk := response.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise BuildError('Official download exceeded the expected size limit')
                    output.write(chunk)
            if sha256:
                verify_sha256(partial, sha256)
            if integrity:
                verify_integrity(partial, integrity)
            partial.replace(path)
        finally:
            if partial.exists():
                partial.unlink()
    if sha256:
        verify_sha256(path, sha256)
    if integrity:
        verify_integrity(path, integrity)
    if path.stat().st_size > max_bytes:
        raise BuildError('Cached download exceeded the expected size limit')
    return path


def download_runtime_inputs(cache, offline=False):
    node_sums = cached_download(cache, 'node-v' + NODE_VERSION + '-SHASUMS256.txt', NODE_CHECKSUM_URL,
                                offline, max_bytes=1024 * 1024)
    matching = [line.split() for line in node_sums.read_text(encoding='utf-8').splitlines()
                if len(line.split()) == 2 and line.split()[1] == NODE_FILENAME]
    if len(matching) != 1 or matching[0][0] != NODE_SHA256:
        raise BuildError('Official Node.js checksum does not match the pinned runtime')
    inputs = {
        'python': cached_download(cache, PYTHON_FILENAME, PYTHON_URL, offline, sha256=PYTHON_SHA256),
        'node': cached_download(cache, NODE_FILENAME, NODE_URL, offline, sha256=NODE_SHA256),
    }
    for package, expected_integrity in NPM_INTEGRITIES.items():
        metadata_url = 'https://registry.npmjs.org/' + package + '/' + PLAYWRIGHT_VERSION
        metadata_path = cached_download(cache, package + '-' + PLAYWRIGHT_VERSION + '-metadata.json',
                                        metadata_url, offline, max_bytes=4 * 1024 * 1024)
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        expected_url = 'https://registry.npmjs.org/' + package + '/-/' + package + '-' + PLAYWRIGHT_VERSION + '.tgz'
        dist = metadata.get('dist', {})
        if (metadata.get('name') != package or metadata.get('version') != PLAYWRIGHT_VERSION
                or dist.get('tarball') != expected_url or dist.get('integrity') != expected_integrity):
            raise BuildError('npm official metadata does not match the pinned package: ' + package)
        if package == 'playwright' and metadata.get('dependencies', {}).get('playwright-core') != PLAYWRIGHT_VERSION:
            raise BuildError('Playwright does not declare the pinned playwright-core version')
        inputs[package] = cached_download(cache, package + '-' + PLAYWRIGHT_VERSION + '.tgz', expected_url,
                                          offline, integrity=expected_integrity)
    return inputs


def copy_application(workspace, stage, version):
    for source in collect_application_files(workspace):
        destination = Path(stage) / source.relative_to(workspace)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    package_path = Path(stage) / 'package.json'
    package = json.loads(package_path.read_text(encoding='utf-8'))
    if package.get('dependencies', {}).get('playwright') != PLAYWRIGHT_VERSION:
        raise BuildError('Application package.json must use the pinned Playwright version')
    package['version'] = version
    package_path.write_text(json.dumps(package, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def compile_desktop(workspace, stage):
    compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise BuildError('Windows .NET Framework x64 C# compiler is required to build the release')
    sources = [Path(workspace) / 'desktop' / name for name in DESKTOP_SOURCE_FILES]
    for source in sources:
        require_within(source, workspace)
        if not source.is_file() or source.is_symlink():
            raise BuildError('Missing or linked C# source: ' + source.name)
    icon = Path(stage) / 'assets' / 'quota-monitor.ico'
    output = Path(stage) / 'CodexQuotaMonitor.exe'
    command = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/debug-',
               '/codepage:65001', '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
               '/reference:System.Web.Extensions.dll', '/win32icon:' + str(icon),
               '/resource:' + str(icon) + ',QuotaMonitor.AppIcon.ico', '/out:' + str(output)]
    subprocess.run(command + [str(source) for source in sources], check=True, cwd=workspace)
    if not output.is_file():
        raise BuildError('Desktop compiler did not produce the release EXE')


def install_runtimes(inputs, stage):
    stage = Path(stage)
    python = stage / 'runtime' / 'python'
    node = stage / 'runtime' / 'node'
    extract_zip(inputs['python'], python)
    configure_embedded_python(python)
    extract_zip(inputs['node'], node, prefix='node-v' + NODE_VERSION + '-win-x64', selected={'node.exe', 'LICENSE'})
    for package in NPM_INTEGRITIES:
        package_root = stage / 'node_modules' / package
        extract_npm_tarball(inputs[package], package_root)
        data = json.loads((package_root / 'package.json').read_text(encoding='utf-8'))
        if data.get('name') != package or data.get('version') != PLAYWRIGHT_VERSION:
            raise BuildError('Extracted npm package version mismatch: ' + package)
    licenses = stage / 'licenses'
    licenses.mkdir()
    license_sources = {
        'Python.txt': python / 'LICENSE.txt', 'Node.txt': node / 'LICENSE',
        'Playwright.txt': stage / 'node_modules' / 'playwright' / 'LICENSE',
        'Playwright-core.txt': stage / 'node_modules' / 'playwright-core' / 'LICENSE',
    }
    for name, source in license_sources.items():
        if not source.is_file():
            raise BuildError('Bundled runtime license is missing: ' + name)
        shutil.copyfile(source, licenses / name)
    notices = (
        'This distribution includes unmodified official runtime binaries and npm packages.\n'
        'Python: Python Software Foundation License; see Python.txt.\n'
        'Node.js: MIT and bundled third-party notices; see Node.txt.\n'
        'Playwright and playwright-core: Apache License 2.0; see their license files.\n'
        'These license files are in the licenses/ directory.\n'
        'Additional upstream notices remain in the runtime/package directories.\n'
        'Embedded Python adds a relative _pth and cwd-first sitecustomize configuration.\n')
    (licenses / 'README.txt').write_text(notices, encoding='utf-8')
    (stage / 'THIRD_PARTY_NOTICES.txt').write_text(notices, encoding='utf-8')


def write_provenance(inputs, stage, version):
    packages = {}
    for package, integrity in NPM_INTEGRITIES.items():
        packages[package] = {
            'version': PLAYWRIGHT_VERSION,
            'url': 'https://registry.npmjs.org/' + package + '/-/' + package + '-' + PLAYWRIGHT_VERSION + '.tgz',
            'metadata_url': 'https://registry.npmjs.org/' + package + '/' + PLAYWRIGHT_VERSION,
            'integrity': integrity, 'sha256': file_hash(inputs[package]),
            'license_file': 'licenses/' + ('Playwright.txt' if package == 'playwright' else 'Playwright-core.txt'),
        }
    provenance = {
        'schema_version': 1, 'application_version': version, 'target': 'windows-x64',
        'python': {'version': PYTHON_VERSION, 'url': PYTHON_URL, 'sha256': PYTHON_SHA256,
                   'checksum_source': PYTHON_CHECKSUM_URL, 'license_file': 'licenses/Python.txt',
                   'configuration': 'relative _pth with import site and cwd-first sitecustomize'},
        'node': {'version': NODE_VERSION, 'url': NODE_URL, 'sha256': NODE_SHA256,
                 'checksum_source': NODE_CHECKSUM_URL, 'license_file': 'licenses/Node.txt'},
        'npm_packages': packages,
        'browsers': 'Use the installed Chrome or Microsoft Edge; browser profiles are not bundled',
    }
    (Path(stage) / 'runtime-provenance.json').write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def smoke_test(stage):
    stage = Path(stage).resolve()
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD='1')
    # Keep imports independent of development-machine Python/Node configuration.
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    environment.pop('NODE_PATH', None)
    subprocess.run([str(stage / 'runtime/python/python.exe'), '-B', '-c',
                    'import ctypes, sqlite3, tomllib, quota_monitor.cli; print("Python portable imports OK")'],
                   cwd=stage, env=environment, check=True, timeout=60)
    subprocess.run([str(stage / 'runtime/node/node.exe'), '-e',
                    'require("playwright"); require("playwright-core"); console.log("Node / Playwright portable imports OK")'],
                   cwd=stage, env=environment, check=True, timeout=60)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='0.2.0', help='Application release version (default: 0.2.0)')
    parser.add_argument('--offline', action='store_true', help='Use already verified download-cache files only')
    parser.add_argument('--download-only', action='store_true', help='Fill and verify the official runtime download cache')
    parser.add_argument('--skip-smoke-tests', action='store_true', help='Skip bundled runtime import checks')
    arguments = parser.parse_args(argv)
    release_name(arguments.version)
    workspace = Path(__file__).resolve().parents[1]
    cache = require_within(workspace / 'build' / 'download-cache', workspace)
    print('Pinned runtimes: Python ' + PYTHON_VERSION + ', Node ' + NODE_VERSION + ', Playwright ' + PLAYWRIGHT_VERSION,
          flush=True)
    inputs = download_runtime_inputs(cache, arguments.offline)
    if arguments.download_only:
        print('Official runtime download cache verified', flush=True)
        return 0
    stage = create_fresh_stage(workspace, arguments.version)
    print('Fresh release stage: ' + str(stage), flush=True)
    copy_application(workspace, stage, arguments.version)
    compile_desktop(workspace, stage)
    install_runtimes(inputs, stage)
    write_provenance(inputs, stage, arguments.version)
    if not arguments.skip_smoke_tests:
        smoke_test(stage)
    destination = require_within(workspace / 'dist', workspace)
    archive, sums = write_release_zip(stage, destination, arguments.version)
    print(str(archive), flush=True)
    print(str(sums), flush=True)
    print('SHA256 ' + file_hash(archive), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (BuildError, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError) as error:
        print('Portable build failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
