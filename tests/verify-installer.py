"""Run isolated Windows installer/upgrade/uninstall verification with a test identity.

Example: python tests/verify-installer.py --archive dist/CodexQuotaMonitor-v0.2.3-windows-x64.zip --iscc build/tools/inno-7.1.0/ISCC.exe
Never uses the production AppId, mutex, startup value, shortcuts or live data.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    spec = importlib.util.spec_from_file_location('installer_verification_builder', ROOT / 'scripts/build-installer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, expected=0):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    result = subprocess.run([str(item) for item in command], cwd=ROOT, capture_output=True,
                            timeout=100, startupinfo=startup)
    if expected is not None and result.returncode != expected:
        raise RuntimeError('Installer command returned unexpected status: ' + str(result.returncode))
    return result.returncode


def main():
    if os.name != 'nt':
        raise SystemExit('This verification requires Windows.')
    import winreg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--iscc', type=Path, required=True)
    args = parser.parse_args()
    builder = load_builder()
    token = uuid.uuid4().hex[:10]
    fixture = ROOT / 'build' / ('iv-' + token)
    fixture.mkdir()
    stage, app = fixture / 'payload', fixture / 'app'
    version = builder.prepare_archive(args.archive.resolve(), stage)
    identity = 'fixture-' + token
    app_id = str(uuid.uuid4()).upper()
    startup_name = 'CodexQuotaMonitor-' + identity
    assert startup_name != 'CodexQuotaMonitor'
    reg_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    uninstall_path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{' + app_id + '}_is1'
    view = winreg.KEY_WOW64_64KEY
    installer = fixture / 'fixture-setup.exe'
    observations = []
    reg = None
    try:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_path, 0, winreg.KEY_READ | view):
                raise RuntimeError('Fixture installation identity already exists.')
        except FileNotFoundError:
            pass
        reg = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0,
                                 winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE | view)
        try:
            winreg.QueryValueEx(reg, startup_name)
            raise RuntimeError('Fixture startup identity already exists.')
        except FileNotFoundError:
            pass
        run([args.iscc.resolve(), '/Qp', '/DAppVersion=' + version,
             '/DAppVersionInfo=' + version.split('-', 1)[0] + '.0', '/DAppSourceDir=' + str(stage),
             '/DSetupOutputDir=' + str(fixture), '/DSetupOutputName=fixture-setup',
             '/DChineseLanguageFile=' + str(builder.compiler_language(args.iscc.resolve())),
             '/DTestIdentity=' + identity, '/DTestAppId={{' + app_id + '}',
             '/DTestAppMutex=Local\\CodexQuotaMonitorFixture-' + token,
             ROOT / 'installer/CodexQuotaMonitor.iss'])

        def install(label, expected=0):
            return run([installer, '/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                        '/CURRENTUSER', '/NOICONS', '/TASKS=', '/DIR=' + str(app),
                        '/LOG=' + str(fixture / (label + '.log'))], expected)

        def uninstall(label):
            candidates = list(app.glob('unins*.exe'))
            if len(candidates) != 1:
                raise RuntimeError('Missing or ambiguous fixture uninstaller.')
            log = fixture / (label + '.log')
            run([candidates[0], '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                 '/LOG=' + str(log)])
            # Inno's launcher can return while its second phase is still running.
            # Final callbacks (including exact-owned startup cleanup) precede this marker.
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if log.exists():
                    raw = log.read_bytes()
                    text = raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8-sig', errors='replace')
                    if 'Log closed.' in text:
                        break
                time.sleep(0.05)
            else:
                raise RuntimeError('Fixture uninstaller did not finish its final callback.')

        install('fresh')
        assert (app / 'CodexQuotaMonitor.exe').is_file()
        assert not (app / 'config.browser.local.toml').exists(), 'Silent installer launched the application'
        assert not (app / 'data/desktop/welcome-seen-v1').exists(), 'Silent installer dismissed user guidance'
        observations.append('fresh silent installation; application and SMTP never launched')
        sentinels = {
            'config.browser.local.toml': b'# synthetic private config sentinel\n',
            'data/desktop/config.browser.local.toml.notifications.json': b'{"fixture":"contacts-and-mail-settings"}',
            'data/browser/state.db': b'synthetic history database',
            'data/browser/state.db-wal': b'synthetic WAL',
            'data/browser/state.db-shm': b'synthetic SHM',
            'data/browser/profile/Default/Cookies': b'synthetic browser cookie state',
            'logs/monitor.log': b'synthetic log',
            'custom-notes.txt': b'synthetic unrelated personal file',
        }
        for relative, content in sentinels.items():
            path = app / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        before = {relative: sha(app / relative) for relative in sentinels}
        install('overinstall')
        assert all(sha(app / relative) == digest for relative, digest in before.items())
        observations.append('over-installation preserved every private sentinel byte')

        marker = app / 'data/desktop/update-recovery-required'
        marker.write_text('fixture recovery required', encoding='utf-8')
        executable_hash = sha(app / 'CodexQuotaMonitor.exe')
        assert install('blocked-recovery', expected=None) != 0
        assert sha(app / 'CodexQuotaMonitor.exe') == executable_hash
        marker.unlink()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        lock = kernel.CreateFileW(str(app / 'data/desktop/update.lock'), 0xC0000000, 0, None, 3, 0x80, None)
        assert lock not in (None, ctypes.c_void_p(-1).value), 'Cannot acquire fixture update lock'
        try:
            assert install('blocked-lock', expected=None) != 0
            assert sha(app / 'CodexQuotaMonitor.exe') == executable_hash
        finally:
            kernel.CloseHandle(lock)
        observations.append('recovery marker and held updater lock rejected before replacement')

        own_command = '"' + str(app / 'CodexQuotaMonitor.exe') + '" --autostart'
        winreg.SetValueEx(reg, startup_name, 0, winreg.REG_SZ, own_command)
        uninstall('uninstall-owned')
        assert not (app / 'CodexQuotaMonitor.exe').exists()
        assert all(sha(app / relative) == digest for relative, digest in before.items())
        try:
            winreg.QueryValueEx(reg, startup_name)
            raise AssertionError('Exact-owned startup entry was not removed')
        except FileNotFoundError:
            pass
        observations.append('uninstall preserved all private bytes and removed only owned startup entry')

        install('reinstall')
        unrelated_command = '"' + str(fixture / 'other-portable/CodexQuotaMonitor.exe') + '" --autostart'
        winreg.SetValueEx(reg, startup_name, 0, winreg.REG_SZ, unrelated_command)
        uninstall('uninstall-unrelated')
        assert winreg.QueryValueEx(reg, startup_name)[0] == unrelated_command
        assert all(sha(app / relative) == digest for relative, digest in before.items())
        observations.append('uninstall retained other-portable startup entry and user data')
        with_uninstall_record = False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_path, 0, winreg.KEY_READ | view):
                with_uninstall_record = True
        except FileNotFoundError:
            pass
        assert not with_uninstall_record, 'Fixture uninstall registration left behind'
        report = {'passed': True, 'version': version, 'observations': observations,
                  'production_identity_used': False, 'private_files_verified': len(before)}
        (ROOT / 'data/desktop/installer-e2e-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        for observation in observations:
            print('PASS ' + observation, flush=True)
        print('PASS isolated installer lifecycle; production app, registry identity and data untouched.', flush=True)
    finally:
        if reg is not None:
            try:
                winreg.DeleteValue(reg, startup_name)
            except FileNotFoundError:
                pass
            winreg.CloseKey(reg)


if __name__ == '__main__':
    main()
