#!/usr/bin/env python3
"""Wrap a verified portable ZIP in a per-user Windows Setup executable.

Requires Inno Setup 6+ (ISCC.exe plus Languages/ChineseSimplified.isl). No tools
are downloaded and no running workspace application, settings, or data is copied.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path


_spec = importlib.util.spec_from_file_location('installer_portable_builder', Path(__file__).with_name('build-portable.py'))
portable = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(portable)
BuildError = portable.BuildError
MAX_ARCHIVE_FILES = 20000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


def _version(version):
    portable.release_name(version)
    if len(version) > 64 or any(int(part) > 65535 for part in version.split('-', 1)[0].split('.')):
        raise BuildError('Version components must fit Windows version fields (0..65535)')
    return version


def archive_version(archive):
    match = re.fullmatch(r'CodexQuotaMonitor-v([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?)-windows-x64\.zip',
                         Path(archive).name)
    if not match:
        raise BuildError('Expected a versioned CodexQuotaMonitor Windows x64 portable ZIP')
    return _version(match.group(1))


def setup_name(version):
    return 'CodexQuotaMonitor-Setup-v' + _version(version) + '-windows-x64.exe'


def _read_checksums(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise BuildError('A verified adjacent SHA256SUMS.txt is required: ' + str(path))
    entries = {}
    for line in path.read_text(encoding='ascii').splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([a-fA-F0-9]{64}) [ *]([^/\\]+)', line)
        if not match:
            raise BuildError('Malformed SHA256SUMS.txt entry')
        name = match.group(2)
        portable.safe_archive_path(name)
        if name.casefold() in {entry.casefold() for entry in entries}:
            raise BuildError('Duplicate SHA256SUMS.txt entry: ' + name)
        entries[name] = match.group(1).lower()
    return entries


def _allowed_member(relative):
    name = relative.as_posix()
    parts = name.casefold().split('/')
    if any(part in portable.FORBIDDEN_COMPONENTS for part in parts):
        raise BuildError('Private/generated directory in portable ZIP: ' + name)
    if (relative.name.casefold() in portable.FORBIDDEN_FILENAMES
            or relative.suffix.casefold() in {'.pyc', '.log', '.db'}):
        raise BuildError('Private/generated file in portable ZIP: ' + name)
    allowed = set(portable.APP_SOURCE_FILES) | {'CodexQuotaMonitor.exe', 'runtime-provenance.json',
                                               'THIRD_PARTY_NOTICES.txt'}
    if name in allowed:
        return
    if len(parts) > 2 and parts[0] == 'runtime' and parts[1] in {'python', 'node'}:
        return
    if len(parts) > 2 and parts[0] == 'node_modules' and parts[1] in portable.NPM_INTEGRITIES:
        return
    if len(parts) == 2 and parts[0] == 'licenses':
        return
    raise BuildError('File is outside the portable release allowlist: ' + name)


def prepare_archive(archive, stage):
    """Validate the complete ZIP before creating a fresh extraction directory."""
    original = Path(archive).absolute()
    archive = original.resolve()
    if original != archive or not archive.is_file():
        raise BuildError('Portable ZIP must be an existing regular file without links')
    version = archive_version(archive)
    prefix = portable.release_name(version)
    sums = _read_checksums(archive.parent / 'SHA256SUMS.txt')
    if archive.name not in sums:
        raise BuildError('Portable ZIP is missing from SHA256SUMS.txt')
    portable.verify_sha256(archive, sums[archive.name])
    stage = Path(stage)
    if stage.exists() or stage.is_symlink():
        raise BuildError('Installer extraction stage must be fresh')
    with zipfile.ZipFile(archive) as zipped:
        entries = [(info, portable.safe_archive_path(info.filename)) for info in zipped.infolist()]
        portable._check_duplicates(path for _, path in entries)
        if len(entries) > MAX_ARCHIVE_FILES or sum(info.file_size for info, _ in entries) > MAX_EXPANDED_BYTES:
            raise BuildError('Portable ZIP exceeds expected release size limits')
        files = {}
        for info, path in entries:
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise BuildError('Portable ZIP contains a link or special file')
            if path.parts[0] != prefix:
                raise BuildError('Portable ZIP has an unexpected root directory')
            if len(path.parts) == 1:
                if not info.is_dir():
                    raise BuildError('Portable ZIP root must be a directory')
                continue
            relative = portable.PurePosixPath(*path.parts[1:])
            if info.is_dir():
                if any(part.casefold() in portable.FORBIDDEN_COMPONENTS for part in relative.parts):
                    raise BuildError('Private/generated directory in portable ZIP')
                continue
            if info.flag_bits & 1:
                raise BuildError('Encrypted portable ZIP entries are not supported')
            _allowed_member(relative)
            files[relative.as_posix()] = info
        required = set(portable.APP_SOURCE_FILES) | {
            'CodexQuotaMonitor.exe', 'package.json', 'runtime-provenance.json', 'THIRD_PARTY_NOTICES.txt',
            'runtime/python/python.exe', 'runtime/node/node.exe',
            'node_modules/playwright/package.json', 'node_modules/playwright-core/package.json',
        }
        file_paths = {name.casefold() for name in files}
        for _, path in entries:
            parts = [part.casefold() for part in path.parts[1:]]
            if any('/'.join(parts[:index]) in file_paths for index in range(1, len(parts))):
                raise BuildError('Portable ZIP uses a file as a parent directory: ' + path.as_posix())
        missing = required - files.keys()
        if missing:
            raise BuildError('Portable ZIP is missing required application/runtime files: ' + ', '.join(sorted(missing)))
        for name in ['package.json', 'runtime-provenance.json']:
            if files[name].file_size > 1024 * 1024:
                raise BuildError('Portable version metadata is too large')
        package = json.loads(zipped.read(files['package.json']))
        provenance = json.loads(zipped.read(files['runtime-provenance.json']))
        if (package.get('version') != version or provenance.get('application_version') != version
                or provenance.get('target') != 'windows-x64'):
            raise BuildError('Portable ZIP metadata version/target does not match its filename')
        if zipped.testzip() is not None:
            raise BuildError('Portable ZIP integrity test failed')
    # The portable extractor independently rechecks every path and entry type.
    stage.mkdir(parents=True, exist_ok=False)
    portable.extract_zip(archive, stage, prefix=prefix)
    portable.assert_release_clean(stage)
    return version


def discover_iscc(explicit=None, workspace=None):
    """Use only an explicitly supplied, installed, or known local cached compiler."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise BuildError('ISCC compiler was not found at --iscc: ' + str(path))
        return path
    candidates = []
    configured = os.environ.get('INNO_SETUP_ISCC')
    if configured:
        return discover_iscc(configured, workspace)
    on_path = shutil.which('ISCC.exe')
    if on_path:
        candidates.append(Path(on_path))
    for variable in ['ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA']:
        value = os.environ.get(variable)
        if value:
            base = Path(value) / 'Programs' if variable == 'LOCALAPPDATA' else Path(value)
            candidates.extend(base / ('Inno Setup ' + version) / 'ISCC.exe' for version in ['7', '6'])
    if workspace:
        tool_root = Path(workspace) / 'build' / 'tools'
        candidates.extend([tool_root / 'inno-7.1.0' / 'ISCC.exe', tool_root / 'inno-setup' / 'ISCC.exe'])
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise BuildError('ISCC.exe was not found. Install official Inno Setup or pass --iscc PATH/ISCC.exe '
                     '(with Languages/ChineseSimplified.isl). No compiler is downloaded automatically.')


def compiler_language(iscc):
    language = Path(iscc).parent / 'Languages' / 'ChineseSimplified.isl'
    if not language.is_file():
        raise BuildError('The compiler needs Languages/ChineseSimplified.isl from the official Inno Setup '
                         'language repository or distribution: ' + str(language))
    return language.resolve()


def update_checksums(path, artifacts):
    """Atomically add current artifact hashes while retaining other release entries."""
    path = Path(path)
    entries = _read_checksums(path)
    for artifact in artifacts:
        artifact = Path(artifact)
        portable.safe_archive_path(artifact.name)
        entries[artifact.name] = portable.file_hash(artifact)
    partial = path.with_name(path.name + '.' + uuid.uuid4().hex + '.part')
    try:
        partial.write_text(''.join(digest + '  ' + name + '\n' for name, digest in entries.items()), encoding='ascii')
        partial.replace(path)
    finally:
        if partial.exists():
            partial.unlink()


def build_installer(workspace, archive, iscc=None, script=None):
    workspace = Path(workspace).resolve()
    archive = Path(archive).resolve()
    portable.require_within(archive, workspace)
    version = archive_version(archive)
    compiler = discover_iscc(iscc, workspace)
    language = compiler_language(compiler)
    script = Path(script or workspace / 'installer' / 'CodexQuotaMonitor.iss').resolve()
    if not script.is_file():
        raise BuildError('Installer script is missing: ' + str(script))
    staging_parent = portable.require_within(workspace / 'build' / 'installer', workspace)
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='s-', dir=staging_parent) as temporary:
        temporary = portable.require_within(temporary, staging_parent)
        stage = temporary / 'app'
        prepare_archive(archive, stage)
        output = temporary / 'output'
        output.mkdir()
        name = setup_name(version)
        command = [str(compiler), '/Qp', '/DAppVersion=' + version,
                   '/DAppVersionInfo=' + version.split('-', 1)[0] + '.0',
                   '/DAppSourceDir=' + str(stage), '/DSetupOutputDir=' + str(output),
                   '/DSetupOutputName=' + Path(name).stem, '/DChineseLanguageFile=' + str(language), str(script)]
        print('Compiling verified portable archive ' + archive.name, flush=True)
        subprocess.run(command, cwd=workspace, check=True)
        compiled = portable.require_within(output / name, output)
        if not compiled.is_file() or compiled.stat().st_size < 2:
            raise BuildError('Inno Setup did not produce the expected executable')
        with compiled.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise BuildError('Inno Setup output is not a Windows executable')
        destination = portable.require_within(archive.parent / name, workspace)
        sums = archive.parent / 'SHA256SUMS.txt'
        # Ensure concurrent or accidental changes cannot silently replace ZIP evidence.
        recorded = _read_checksums(sums)
        if archive.name not in recorded:
            raise BuildError('Portable ZIP disappeared from SHA256SUMS.txt during compilation')
        portable.verify_sha256(archive, recorded[archive.name])
        compiled.replace(destination)
        update_checksums(sums, [archive, destination])
    return destination, sums


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True, help='Verified versioned portable ZIP in this workspace')
    parser.add_argument('--iscc', type=Path, help='Official Inno Setup compiler path (no automatic download)')
    arguments = parser.parse_args(argv)
    workspace = Path(__file__).resolve().parents[1]
    setup, sums = build_installer(workspace, arguments.archive, arguments.iscc)
    print(str(setup), flush=True)
    print(str(sums), flush=True)
    print('SHA256 ' + portable.file_hash(setup), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (BuildError, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print('Installer build failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
