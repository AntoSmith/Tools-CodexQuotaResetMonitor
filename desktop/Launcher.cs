using System;
using System.ComponentModel;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace CodexQuotaDesktop
{
    internal sealed class ProcessJob : IDisposable
    {
        [StructLayout(LayoutKind.Sequential)] struct BasicLimits
        {
            public long ProcessTime, JobTime;
            public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet;
            public uint ActiveProcesses;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters
        {
            public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        }
        [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits
        {
            public BasicLimits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        IntPtr handle;

        public ProcessJob()
        {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) throw new Win32Exception();
            ExtendedLimits limits = new ExtendedLimits();
            limits.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE, no breakaway permission.
            if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits)))
            {
                int error = Marshal.GetLastWin32Error();
                Dispose();
                throw new Win32Exception(error);
            }
        }
        public void Assign(Process process)
        {
            if (!AssignProcessToJobObject(handle, process.Handle)) throw new Win32Exception();
        }
        public void Dispose()
        {
            if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
        }
    }

    internal sealed class MonitorProcess : IDisposable
    {
        internal sealed class OutputLine { public int Generation; public string Text; }
        internal readonly ConcurrentQueue<OutputLine> Output = new ConcurrentQueue<OutputLine>();
        internal int Generation;
        public const string MonitorCode = "sys.argv=['quota_monitor','browser-run','--config','config.browser.local.toml'];runpy.run_module('quota_monitor',run_name='__main__')";
        readonly string root;
        readonly string logName;
        readonly object logLock = new object();
        Process process;
        ProcessJob job;
        StreamWriter log;
        public string LogPath { get { return Path.Combine(root, "logs", logName); } }
        public bool Running { get { return process != null && !process.HasExited; } }
        public int ExitCode { get { return process == null ? 0 : process.ExitCode; } }

        public MonitorProcess(string root) : this(root, "desktop-monitor.log") { }
        internal MonitorProcess(string root, string logName) { this.root = root; this.logName = logName; }
        public IEnumerable<string> DrainOutput()
        {
            OutputLine line;
            while (Output.TryDequeue(out line)) if (line.Generation == Generation) yield return line.Text;
        }
        public IEnumerable<string> ReapExitedOutput()
        {
            if (process == null || !process.HasExited) throw new InvalidOperationException("Process has not exited");
            // Close any surviving descendants before waiting for redirected pipes to finish.
            // Keep the current generation until all final callbacks have been consumed.
            if (job != null) { job.Dispose(); job = null; }
            process.WaitForExit();
            return new List<string>(DrainOutput());
        }
        public static string GatedCode(string code) { return "import sys,runpy;sys.exit(0) if sys.stdin.readline().strip()!='start' else None;" + code; }
        public string Runtime(string name)
        {
            string path = File.ReadAllText(Path.Combine(root, "data", "desktop", name + "-path.txt")).Trim();
            if (!Path.IsPathRooted(path) || !File.Exists(path)) throw new FileNotFoundException("找不到 " + name + "，请重新运行构建脚本。 ");
            return path;
        }
        public void Preflight()
        {
            Runtime("python"); Runtime("node");
            if (!File.Exists(Path.Combine(root, "config.browser.local.toml"))) throw new FileNotFoundException("找不到 config.browser.local.toml，请把 EXE 放在项目目录中。");
            if (!File.Exists(Path.Combine(root, "quota_monitor", "__main__.py"))) throw new FileNotFoundException("找不到采集代码，请保留完整项目目录。");
        }
        void WriteLog(string line)
        {
            if (line == null) return;
            lock (logLock)
            {
                try { if (log != null) log.WriteLine(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + line); }
                catch (IOException) { }
            }
        }
        public void Start(string code)
        {
            if (Running) return;
            Stop(); Preflight();
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath));
            lock (logLock)
            {
                if (File.Exists(LogPath) && new FileInfo(LogPath).Length > 5 * 1024 * 1024)
                {
                    string previous = LogPath + ".previous";
                    if (File.Exists(previous)) File.Delete(previous);
                    File.Move(LogPath, previous);
                }
                log = new StreamWriter(LogPath, true, new UTF8Encoding(false)); log.AutoFlush = true;
            }
            // Python waits on stdin until it belongs to our job, before spawning any descendants.
            string gated = GatedCode(code);
            ProcessStartInfo info = new ProcessStartInfo(Runtime("python"), "-u -c \"" + gated.Replace("\"", "\\\"") + "\"");
            info.WorkingDirectory = root;
            info.UseShellExecute = false; info.CreateNoWindow = true;
            info.RedirectStandardInput = true; info.RedirectStandardOutput = true; info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            info.EnvironmentVariables["PATH"] = Path.GetDirectoryName(Runtime("node")) + ";" + info.EnvironmentVariables["PATH"];
            job = new ProcessJob();
            process = new Process(); process.StartInfo = info;
            int session = Generation;
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) {
                WriteLog(e.Data);
                if (e.Data != null && e.Data.StartsWith("@quota-ui ", StringComparison.Ordinal))
                    Output.Enqueue(new OutputLine { Generation = session, Text = e.Data });
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { WriteLog(e.Data); };
            try
            {
                process.Start();
                job.Assign(process);
                process.BeginOutputReadLine(); process.BeginErrorReadLine();
                WriteLog("采集进程已启动，PID=" + process.Id);
                process.StandardInput.WriteLine("start"); process.StandardInput.Flush(); process.StandardInput.Close();
            }
            catch
            {
                // Assignment may fail before job ownership; this process is still waiting at its gate.
                try { if (!process.HasExited) process.Kill(); } catch (InvalidOperationException) { }
                Stop(); throw;
            }
        }
        public void Stop()
        {
            Interlocked.Increment(ref Generation);
            OutputLine ignored; while (Output.TryDequeue(out ignored)) { }
            if (job != null) { job.Dispose(); job = null; }
            if (process != null)
            {
                try { if (process.WaitForExit(5000)) process.WaitForExit(); }
                catch (InvalidOperationException) { }
                process.Dispose(); process = null;
            }
            lock (logLock) { if (log != null) { log.Dispose(); log = null; } }
        }
        public void Dispose() { Stop(); }
    }

    internal static class Startup
    {
        public const string KeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
        public const string ValueName = "CodexQuotaMonitor";
        public static string Command { get { return "\"" + Application.ExecutablePath + "\" --autostart"; } }
        public static bool Enabled()
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(KeyPath))
                return key != null && String.Equals(key.GetValue(ValueName) as string, Command, StringComparison.OrdinalIgnoreCase);
        }
        public static void Set(bool enabled)
        {
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(KeyPath))
            {
                object existing = key.GetValue(ValueName);
                if (existing != null && (!(existing is string) || !String.Equals((string)existing, Command, StringComparison.OrdinalIgnoreCase)))
                    throw new InvalidOperationException("已存在不同目录的同名启动项，请先处理旧程序的启动设置。");
                if (enabled) key.SetValue(ValueName, Command, RegistryValueKind.String);
                else key.DeleteValue(ValueName, false);
            }
        }
    }

    internal static class Program
    {
        [STAThread] static int Main(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            bool diagnostic = args.Length == 0 || args[0] == "--autostart";
            if (diagnostic) LaunchLog(root, "launch mode=" + (args.Length == 0 ? "manual" : "autostart"));
            try { return MainCore(args); }
            catch (Exception ex) {
                LaunchLog(root, "fatal " + ex.GetType().Name);
                if (args.Length == 0) MessageBox.Show("程序启动失败，请查看 logs/desktop-startup.log。\n" + ex.Message, AppBrand.Name);
                return 1;
            }
            finally { if (diagnostic) LaunchLog(root, "exit"); }
        }
        public static void LaunchLog(string root, string message)
        {
            try {
                string folder = Path.Combine(root, "logs"); Directory.CreateDirectory(folder);
                File.AppendAllText(Path.Combine(folder, "desktop-startup.log"), DateTimeOffset.Now.ToString("o")
                    + " pid=" + Process.GetCurrentProcess().Id + " " + message + Environment.NewLine);
            } catch { /* Diagnostic failure must not prevent monitoring. */ }
        }
        static int MainCore(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            Directory.SetCurrentDirectory(root);
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            Directory.CreateDirectory(Path.Combine(root, "data", "desktop"));
            string mode = args.Length == 0 ? "" : args[0];
            if (mode == "--ui-test") return UiVerification.Run(root);
            if (mode == "--history-ui-test") return HistoryVerification.Run(root);
            if (mode == "--self-test" || mode == "--lifecycle-test") return SelfTest(root, mode);
            if (mode == "--enable-startup" || mode == "--disable-startup")
            {
                try { Startup.Set(mode == "--enable-startup"); return 0; }
                catch (Exception ex)
                {
                    File.WriteAllText(Path.Combine(root, "data", "desktop", "startup-error.txt"), ex.GetType().Name + ": " + ex.Message);
                    return 1;
                }
            }
            bool created;
            using (Mutex mutex = new Mutex(true, @"Local\CodexQuotaMonitorDesktop", out created))
            {
                if (!created)
                {
                    LaunchLog(root, "existing_instance");
                    if (mode != "--autostart") MessageBox.Show("程序已经运行，请查看右下角托盘图标。", AppBrand.Name);
                    return 0;
                }
                TrayApplication app = null;
                try { app = new TrayApplication(root, mode == "--autostart", true); Application.Run(app); }
                finally { if (app != null) app.Quit(); mutex.ReleaseMutex(); }
            }
            return 0;
        }
        static int SelfTest(string root, string mode)
        {
            string report = Path.Combine(root, "data", "desktop", mode == "--self-test" ? "self-test.txt" : "lifecycle-test.txt");
            try
            {
                if (mode == "--self-test")
                {
                    using (MonitorProcess runner = new MonitorProcess(root)) runner.Preflight();
                    TrayApplication ui = new TrayApplication(root, true, false); ui.Quit();
                }
                else
                {
                    string pidPath = Path.Combine(root, "data", "desktop", "child.pid");
                    if (File.Exists(pidPath)) File.Delete(pidPath);
                    using (MonitorProcess runner = new MonitorProcess(root, "desktop-self-test.log"))
                    {
                        string probe = Path.Combine(root, "data", "desktop", "unexpected-gate.txt");
                        if (File.Exists(probe)) File.Delete(probe);
                        ProcessStartInfo gateInfo = new ProcessStartInfo(runner.Runtime("python"), "-c \"" + MonitorProcess.GatedCode("open('data/desktop/unexpected-gate.txt','w').write('ran')") + "\"");
                        gateInfo.UseShellExecute = false; gateInfo.CreateNoWindow = true;
                        gateInfo.RedirectStandardInput = true; gateInfo.WorkingDirectory = root;
                        using (Process gate = Process.Start(gateInfo))
                        {
                            try
                            {
                                gate.StandardInput.Close(); // Launcher died before assigning job or releasing gate.
                                if (!gate.WaitForExit(5000)) throw new Exception("EOF gate process did not exit");
                                if (File.Exists(probe)) throw new Exception("EOF incorrectly released the process gate");
                            }
                            finally { if (!gate.HasExited) gate.Kill(); }
                        }
                        runner.Start("import subprocess,time;child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);open('data/desktop/child.pid','w').write(str(child.pid));time.sleep(120)");
                        for (int i = 0; i < 100 && !File.Exists(pidPath); i++) Thread.Sleep(100);
                        if (!File.Exists(pidPath)) throw new Exception("Synthetic child did not start");
                        using (Process child = Process.GetProcessById(Int32.Parse(File.ReadAllText(pidPath))))
                        {
                            try
                            {
                                if (!runner.Running) throw new Exception("Root process exited unexpectedly");
                                runner.Stop();
                                if (!child.WaitForExit(5000)) throw new Exception("Child survived stop");
                                if (runner.Running) throw new Exception("Root survived stop");
                            }
                            finally { if (!child.HasExited) child.Kill(); }
                        }
                    }
                }
                File.WriteAllText(report, "PASS " + mode + " " + DateTime.Now.ToString("o")); return 0;
            }
            catch (Exception ex) { File.WriteAllText(report, "FAIL " + ex.ToString()); return 1; }
        }
    }
}
