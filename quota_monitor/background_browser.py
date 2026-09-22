"""Full Chromium on a private Windows desktop, owned by a kill-on-close job.

The desktop is never made the input desktop. Windows 10's JOB_LIST process
attribute assigns the job atomically at creation, avoiding even a suspended
orphan if this Python process dies before ResumeThread.
"""
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid

from .config import ConfigError


def _find_browser(channel):
    relative = {'chrome': Path('Google/Chrome/Application/chrome.exe'),
                'msedge': Path('Microsoft/Edge/Application/msedge.exe')}
    if channel not in relative:
        raise ConfigError('后台浏览器仅支持 chrome 或 msedge')
    for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = os.environ.get(name)
        if root:
            candidate = Path(root) / relative[channel]
            if candidate.is_file():
                return candidate
    raise ConfigError('后台浏览器未找到已安装的 Chrome 或 Edge')


def _read_endpoint(path):
    if path.is_symlink() or path.resolve().parent != path.parent:
        raise ConfigError('后台浏览器端口文件路径不安全')
    try:
        with path.open('r', encoding='ascii') as handle:
            text = handle.read(1024)
    except (FileNotFoundError, UnicodeError):
        return None
    match = re.fullmatch(r'([0-9]{1,5})\r?\n(/devtools/browser/[A-Za-z0-9-]+)\s*', text)
    if not match or not 1 <= int(match[1]) <= 65535:
        return None
    return int(match[1]), 'ws://127.0.0.1:' + str(int(match[1])) + match[2]


def _port_is_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            return True
    except OSError:
        return False


class BackgroundBrowser:
    def __init__(self, profile: Path, channel: str, stop=None, timeout=20):
        self.profile = Path(profile).resolve()
        self.channel, self.stop, self.timeout = channel, stop, timeout
        self.endpoint = None
        self.pid = None
        self.desktop_name = 'QuotaBrowser_' + uuid.uuid4().hex
        self._native = None
        self._desktop = self._job = self._process = self._thread = None
        self._entered = False

    def _check_stop(self):
        if self.stop is not None and self.stop.is_set():
            raise ConfigError('后台浏览器采集已停止')

    def __enter__(self):
        if self._entered:
            raise ConfigError('后台浏览器上下文不能重复使用')
        self._entered = True
        try:
            self._check_stop()
            self._native = _WindowsNative()
            executable = _find_browser(self.channel)
            self.profile.mkdir(parents=True, exist_ok=True)
            self._native.check_profile_free(self.profile)
            port_file = self.profile / 'DevToolsActivePort'
            previous = _read_endpoint(port_file)
            if previous and _port_is_open(previous[0]):
                raise ConfigError('后台浏览器独立配置目录已被占用')
            # Only this exact file inside the supplied isolated profile is removed.
            port_file.unlink(missing_ok=True)
            self._desktop = self._native.create_desktop(self.desktop_name)
            self._job = self._native.create_job()
            args = [str(executable), '--remote-debugging-port=0',
                    '--remote-debugging-address=127.0.0.1',
                    '--user-data-dir=' + str(self.profile), '--no-first-run',
                    '--no-default-browser-check', '--lang=en-US', '--window-size=1280,900',
                    '--disable-background-networking', '--disable-component-update',
                    '--disable-session-crashed-bubble', '--disable-extensions',
                    '--disable-features=Translate,MediaRouter', '--password-store=basic',
                    '--mute-audio', 'about:blank']
            environment = {key: value for key, value in os.environ.items()
                           if key.upper() not in ('X_BEARER_TOKEN', 'QQ_SMTP_AUTH_CODE')}
            self._process, self._thread, self.pid = self._native.create_process(
                executable, args, self.desktop_name, self._job, environment)
            self._check_stop()
            self._native.resume(self._thread)
            self._native.close_handle(self._thread)
            self._thread = None
            deadline = time.monotonic() + self.timeout
            while True:
                self._check_stop()
                if not self._native.is_running(self._process):
                    raise ConfigError('后台浏览器提前退出；请检查独立配置目录是否被占用')
                endpoint = _read_endpoint(port_file)
                if endpoint:
                    self.endpoint = endpoint[1]
                    return self
                if time.monotonic() >= deadline:
                    raise ConfigError('后台浏览器启动超时；未取得本机调试端口')
                if self.stop is not None:
                    self.stop.wait(0.05)
                else:
                    time.sleep(0.05)
        except BaseException as error:
            self.close()
            if isinstance(error, OSError):
                raise ConfigError('后台浏览器启动失败；请检查 Windows 桌面权限及浏览器安装') from error
            raise

    def close(self):
        if self._native is None:
            return
        # Closing the sole noninherited job handle terminates the whole tree,
        # including Chrome children; close the desktop only after the root exits.
        errors = []
        for attribute, operation in (('_job', self._native.close_handle),
                                     ('_thread', self._native.close_handle)):
            handle = getattr(self, attribute)
            if handle is not None:
                try:
                    operation(handle)
                except OSError as error:
                    errors.append(error)
                finally:
                    setattr(self, attribute, None)
        if self._process is not None:
            try:
                self._native.wait_process(self._process)
            except OSError as error:
                errors.append(error)
            finally:
                try:
                    self._native.close_handle(self._process)
                except OSError as error:
                    errors.append(error)
                self._process = None
        if self._desktop is not None:
            try:
                self._native.close_desktop(self._desktop)
            except OSError as error:
                errors.append(error)
            finally:
                self._desktop = None
        if errors:
            raise ConfigError('后台浏览器资源回收失败') from errors[0]

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class _WindowsNative:
    """ctypes is loaded lazily so importing this module is portable."""
    def __init__(self):
        if os.name != 'nt':
            raise ConfigError('后台浏览器需要 Windows 10 或更新版本')
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.u = c.WinDLL('user32', use_last_error=True)

        class STARTUPINFO(c.Structure):
            _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR),
                        ('lpTitle', w.LPWSTR), ('dwX', w.DWORD), ('dwY', w.DWORD),
                        ('dwXSize', w.DWORD), ('dwYSize', w.DWORD), ('dwXCountChars', w.DWORD),
                        ('dwYCountChars', w.DWORD), ('dwFillAttribute', w.DWORD),
                        ('dwFlags', w.DWORD), ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                        ('lpReserved2', c.POINTER(c.c_byte)), ('hStdInput', w.HANDLE),
                        ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE)]

        class STARTUPINFOEX(c.Structure):
            _fields_ = [('StartupInfo', STARTUPINFO), ('lpAttributeList', c.c_void_p)]

        class PROCESS_INFORMATION(c.Structure):
            _fields_ = [('hProcess', w.HANDLE), ('hThread', w.HANDLE),
                        ('dwProcessId', w.DWORD), ('dwThreadId', w.DWORD)]

        class BASIC_LIMIT(c.Structure):
            _fields_ = [('PerProcessUserTimeLimit', c.c_int64), ('PerJobUserTimeLimit', c.c_int64),
                        ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                        ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD),
                        ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD),
                        ('SchedulingClass', w.DWORD)]

        class IO_COUNTERS(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
                         'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount',
                         'OtherTransferCount')]

        class EXTENDED_LIMIT(c.Structure):
            _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
                        ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                        ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]

        self.STARTUPINFOEX, self.PROCESS_INFORMATION = STARTUPINFOEX, PROCESS_INFORMATION
        self.EXTENDED_LIMIT = EXTENDED_LIMIT
        declarations = [
            (self.u.CreateDesktopW, [w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, w.DWORD, c.c_void_p], w.HANDLE),
            (self.u.CloseDesktop, [w.HANDLE], w.BOOL),
            (self.k.CreateJobObjectW, [c.c_void_p, w.LPCWSTR], w.HANDLE),
            (self.k.SetInformationJobObject, [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            (self.k.InitializeProcThreadAttributeList, [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)], w.BOOL),
            (self.k.UpdateProcThreadAttribute, [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p], w.BOOL),
            (self.k.DeleteProcThreadAttributeList, [c.c_void_p], None),
            (self.k.CreateProcessW, [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL,
                                   w.DWORD, c.c_void_p, w.LPCWSTR, c.c_void_p,
                                   c.POINTER(PROCESS_INFORMATION)], w.BOOL),
            (self.k.ResumeThread, [w.HANDLE], w.DWORD),
            (self.k.WaitForSingleObject, [w.HANDLE, w.DWORD], w.DWORD),
            (self.k.CloseHandle, [w.HANDLE], w.BOOL),
            (self.k.CreateFileW, [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p,
                                 w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
        ]
        for function, arguments, result in declarations:
            function.argtypes, function.restype = arguments, result

    def _check(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())
        return result

    def create_desktop(self, name):
        # Desktop access, excluding DESKTOP_SWITCHDESKTOP. No SwitchDesktop API is used.
        return self._check(self.u.CreateDesktopW(name, None, None, 0, 0x00FF, None))

    def check_profile_free(self, profile):
        # Chromium holds lockfile with GENERIC_WRITE and FILE_SHARE_READ.
        # OPEN_EXISTING never creates/truncates it; only test sharing compatibility.
        handle = self.k.CreateFileW(str(profile / 'lockfile'), 0x40000000, 1,
                                    None, 3, 0x80, None)
        if handle == self.c.c_void_p(-1).value:
            error = self.c.get_last_error()
            if error in (2, 3):  # absent file/path
                return
            raise ConfigError('后台浏览器独立配置目录已被占用或无法核验锁状态')
        self.close_handle(handle)

    def create_job(self):
        job = self._check(self.k.CreateJobObjectW(None, None))
        limits = self.EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        try:
            self._check(self.k.SetInformationJobObject(job, 9, self.c.byref(limits), self.c.sizeof(limits)))
        except BaseException:
            self.close_handle(job)
            raise
        return job

    def create_process(self, executable, arguments, desktop, job, environment):
        c, w = self.c, self.w
        size = c.c_size_t()
        self.k.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        self._check(self.k.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)))
        try:
            jobs = (w.HANDLE * 1)(job)
            self._check(self.k.UpdateProcThreadAttribute(
                attributes, 0, 0x0002000D, jobs, c.sizeof(jobs), None, None))
            startup = self.STARTUPINFOEX()
            startup.StartupInfo.cb = c.sizeof(startup)
            startup.StartupInfo.lpDesktop = desktop
            startup.lpAttributeList = c.cast(attributes, c.c_void_p)
            command = c.create_unicode_buffer(subprocess.list2cmdline(arguments))
            env = c.create_unicode_buffer('\0'.join(
                key + '=' + value for key, value in sorted(environment.items(), key=lambda item: item[0].upper())) + '\0')
            info = self.PROCESS_INFORMATION()
            self._check(self.k.CreateProcessW(
                str(executable), command, None, None, False,
                0x00000004 | 0x00000400 | 0x00080000,  # SUSPENDED, UNICODE_ENV, EXTENDED_STARTUPINFO
                env, None, c.byref(startup), c.byref(info)))
            return info.hProcess, info.hThread, info.dwProcessId
        finally:
            self.k.DeleteProcThreadAttributeList(attributes)

    def resume(self, thread):
        if self.k.ResumeThread(thread) == 0xFFFFFFFF:
            raise self.c.WinError(self.c.get_last_error())

    def is_running(self, process):
        result = self.k.WaitForSingleObject(process, 0)
        if result == 0xFFFFFFFF:
            raise self.c.WinError(self.c.get_last_error())
        return result == 258

    def wait_process(self, process):
        result = self.k.WaitForSingleObject(process, 10000)
        if result != 0:
            raise OSError('Browser process did not exit after job close')

    def close_handle(self, handle):
        self._check(self.k.CloseHandle(handle))

    def close_desktop(self, desktop):
        self._check(self.u.CloseDesktop(desktop))
