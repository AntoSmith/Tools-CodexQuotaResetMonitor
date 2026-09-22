import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch


class NativeFake:
    def __init__(self, profile, failure=None, port_text='43123\n/devtools/browser/test-id\n'):
        self.profile, self.failure, self.port_text = profile, failure, port_text
        self.events = []
        self.running = True

    def create_desktop(self, name):
        self.events.append(('desktop', name))
        return 1

    def check_profile_free(self, profile):
        if self.failure == 'profile_locked':
            raise OSError('profile locked')

    def create_job(self):
        self.events.append(('job',))
        return 2

    def create_process(self, executable, arguments, desktop, job, environment):
        self.events.append(('create', arguments, desktop, job, environment))
        if self.failure == 'create':
            raise OSError('create failed')
        return 3, 4, 1234

    def resume(self, thread):
        self.events.append(('resume', thread))
        if self.failure == 'resume':
            raise OSError('resume failed')
        if self.port_text is not None:
            (self.profile / 'DevToolsActivePort').write_text(self.port_text)

    def is_running(self, process):
        return self.running

    def close_handle(self, handle):
        self.events.append(('close', handle))

    def wait_process(self, process):
        self.events.append(('wait', process))

    def close_desktop(self, desktop):
        self.events.append(('close_desktop', desktop))


class BackgroundBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)

    def module(self):
        spec = importlib.util.find_spec('quota_monitor.background_browser')
        self.assertIsNotNone(spec, 'BackgroundBrowser module must exist')
        return importlib.import_module('quota_monitor.background_browser')

    def start(self, fake, **kwargs):
        module = self.module()
        self.enterContext(patch.object(module, '_WindowsNative', return_value=fake))
        self.enterContext(patch.object(module, '_find_browser', return_value=Path('chrome.exe')))
        return module.BackgroundBrowser(self.profile, 'chrome', **kwargs)

    def test_hidden_launch_endpoint_environment_and_cleanup(self):
        fake = NativeFake(self.profile)
        browser = self.start(fake)
        with patch.dict(os.environ, {'X_BEARER_TOKEN': 'secret', 'QQ_SMTP_AUTH_CODE': 'secret'}):
            with browser as result:
                self.assertEqual(result.endpoint, 'ws://127.0.0.1:43123/devtools/browser/test-id')
                self.assertEqual(result.pid, 1234)
                launch = next(e for e in fake.events if e[0] == 'create')
                self.assertEqual(launch[2], result.desktop_name)
                self.assertNotEqual(result.desktop_name.lower(), 'default')
                self.assertEqual(launch[3], 2)
                self.assertNotIn('X_BEARER_TOKEN', launch[4])
                self.assertNotIn('QQ_SMTP_AUTH_CODE', launch[4])
                self.assertIn('--remote-debugging-address=127.0.0.1', launch[1])
                self.assertIn('--remote-debugging-port=0', launch[1])
                self.assertNotIn('--no-sandbox', launch[1])
                self.assertNotIn('--headless', launch[1])
        self.assertLess(fake.events.index(('close', 2)), fake.events.index(('wait', 3)))
        self.assertIn(('close', 3), fake.events)
        self.assertIn(('close', 4), fake.events)
        self.assertEqual(fake.events[-1], ('close_desktop', 1))

    def test_launch_failures_close_every_acquired_resource(self):
        for failure in ('create', 'resume'):
            with self.subTest(failure=failure):
                fake = NativeFake(self.profile, failure=failure)
                browser = self.start(fake)
                with self.assertRaisesRegex(Exception, '后台浏览器'):
                    browser.__enter__()
                self.assertIn(('close', 2), fake.events)
                self.assertIn(('close_desktop', 1), fake.events)
                if failure == 'resume':
                    self.assertIn(('close', 3), fake.events)
                    self.assertIn(('close', 4), fake.events)

    def test_cancel_before_launch_creates_nothing(self):
        stop = threading.Event()
        stop.set()
        fake = NativeFake(self.profile)
        with self.assertRaisesRegex(Exception, '停止'):
            self.start(fake, stop=stop).__enter__()
        self.assertEqual(fake.events, [])

    def test_timeout_and_browser_exit_clean_up(self):
        for running in (True, False):
            with self.subTest(running=running):
                fake = NativeFake(self.profile, port_text=None)
                fake.running = running
                with self.assertRaisesRegex(Exception, '后台浏览器'):
                    self.start(fake, timeout=0.02).__enter__()
                self.assertIn(('close', 2), fake.events)
                self.assertIn(('close_desktop', 1), fake.events)

    def test_invalid_endpoint_is_never_exposed(self):
        for contents in ('65536\n/devtools/browser/a', '1234\nhttp://evil.test',
                         '1234\n/devtools/browser/a?x=1', '-1\n/devtools/browser/a'):
            with self.subTest(contents=contents):
                fake = NativeFake(self.profile, port_text=contents)
                with self.assertRaisesRegex(Exception, '后台浏览器'):
                    self.start(fake, timeout=0.02).__enter__()
                (self.profile / 'DevToolsActivePort').unlink(missing_ok=True)

    def test_stale_port_file_is_removed_before_create(self):
        module = self.module()
        port_file = self.profile / 'DevToolsActivePort'
        port_file.write_text('1234\n/devtools/browser/old')
        fake = NativeFake(self.profile)
        original = fake.create_process
        def create(*args):
            self.assertFalse(port_file.exists())
            return original(*args)
        fake.create_process = create
        with patch.object(module, '_port_is_open', return_value=False):
            with self.start(fake):
                pass

    def test_active_profile_port_is_not_deleted_or_launched(self):
        module = self.module()
        port_file = self.profile / 'DevToolsActivePort'
        port_file.write_text('1234\n/devtools/browser/active')
        fake = NativeFake(self.profile)
        with patch.object(module, '_port_is_open', return_value=True):
            with self.assertRaisesRegex(Exception, '占用'):
                self.start(fake).__enter__()
        self.assertTrue(port_file.exists())
        self.assertEqual(fake.events, [])

    def test_cancellation_after_create_reaps_suspended_process(self):
        stop = threading.Event()
        fake = NativeFake(self.profile)
        original = fake.create_process
        def create(*args):
            result = original(*args)
            stop.set()
            return result
        fake.create_process = create
        with self.assertRaisesRegex(Exception, '停止'):
            self.start(fake, stop=stop).__enter__()
        self.assertNotIn(('resume', 4), fake.events)
        self.assertIn(('close', 2), fake.events)
        self.assertIn(('close', 3), fake.events)

    def test_occupied_profile_without_cdp_file_is_not_launched(self):
        fake = NativeFake(self.profile, failure='profile_locked')
        with self.assertRaisesRegex(Exception, '后台浏览器'):
            self.start(fake).__enter__()
        self.assertEqual(fake.events, [])


@unittest.skipUnless(os.name == 'nt' and os.environ.get('QUOTA_TEST_BACKGROUND_NATIVE') == '1',
                     'Explicit opt-in Windows local blank-page integration test')
class BackgroundBrowserNativeTests(unittest.TestCase):
    def setUp(self):
        from quota_monitor.background_browser import BackgroundBrowser
        self.browser_class = BackgroundBrowser
        self.temp = tempfile.TemporaryDirectory(prefix='background-test-', dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)

    def test_native_window_is_on_noninput_desktop_and_cleanup(self):
        import ctypes as c
        from ctypes import wintypes as w
        with self.browser_class(self.profile, 'chrome', timeout=25) as browser:
            native = browser._native
            with self.assertRaisesRegex(Exception, '占用'):
                native.check_profile_free(self.profile)
            port = browser.endpoint.split(':')[2].split('/')[0]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:' + port + '/json/version', timeout=5) as response:
                self.assertEqual(json.load(response)['webSocketDebuggerUrl'], browser.endpoint)
            callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
            native.u.EnumDesktopWindows.argtypes = [w.HANDLE, callback_type, w.LPARAM]
            native.u.EnumDesktopWindows.restype = w.BOOL
            native.u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
            native.u.GetWindowThreadProcessId.restype = w.DWORD
            native.u.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            native.u.OpenInputDesktop.restype = w.HANDLE
            native.u.GetUserObjectInformationW.argtypes = [w.HANDLE, c.c_int, c.c_void_p,
                                                          w.DWORD, c.POINTER(w.DWORD)]
            native.u.GetUserObjectInformationW.restype = w.BOOL
            input_desktop = native.u.OpenInputDesktop(0, False, 1)
            self.assertTrue(input_desktop)
            try:
                name = c.create_unicode_buffer(256)
                needed = w.DWORD()
                self.assertTrue(native.u.GetUserObjectInformationW(
                    input_desktop, 2, name, c.sizeof(name), c.byref(needed)))
                self.assertNotEqual(name.value, browser.desktop_name)
            finally:
                native.close_desktop(input_desktop)
            pids = []
            @callback_type
            def window(hwnd, lparam):
                pid = w.DWORD()
                native.u.GetWindowThreadProcessId(hwnd, c.byref(pid))
                pids.append(pid.value)
                return True
            deadline = time.monotonic() + 5
            while browser.pid not in pids and time.monotonic() < deadline:
                self.assertTrue(native.u.EnumDesktopWindows(browser._desktop, window, 0))
                time.sleep(0.05)
            self.assertIn(browser.pid, pids)
            native.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            native.k.OpenProcess.restype = w.HANDLE
            process = native.k.OpenProcess(0x00100000, False, browser.pid)
            self.assertTrue(process)
        try:
            self.assertFalse(native.is_running(process))
        finally:
            native.close_handle(process)

    def test_killing_owner_kills_browser_without_cleanup_callback(self):
        from quota_monitor.background_browser import _WindowsNative
        native = _WindowsNative()
        c, w = native.c, native.w
        native.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        native.k.OpenProcess.restype = w.HANDLE
        script = ("from pathlib import Path; import sys,time; "
                  "from quota_monitor.background_browser import BackgroundBrowser; "
                  "b=BackgroundBrowser(Path(sys.argv[1]),'chrome',timeout=25); "
                  "b.__enter__(); print(b.pid,flush=True); time.sleep(60)")
        owner = subprocess.Popen([sys.executable, '-c', script, str(self.profile)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        process = None
        try:
            # Child has its own bounded 25-second startup timeout.
            line = owner.stdout.readline().strip()
            self.assertTrue(line.isdigit(), 'Native owner failed to launch browser')
            process = native.k.OpenProcess(0x00100000, False, int(line))
            self.assertTrue(process)
            owner.kill()
            owner.communicate(timeout=5)
            native.wait_process(process)
            self.assertFalse(native.is_running(process))
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.communicate(timeout=5)
            if process:
                native.close_handle(process)


if __name__ == '__main__':
    unittest.main()
