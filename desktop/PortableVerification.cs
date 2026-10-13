using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal static class PortableVerification
    {
        const string Template = "# synthetic fixture only\n[browser]\nchannel = \"chrome\" # browser\nprofile = \"data/browser/profile\"\n[notifications]\nchannel = \"console\"\nusername = \"sender@example.com\"\n";
        static void Check(bool value, string message) { if (!value) throw new Exception(message); }
        static void Stub(string path) { Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path, "synthetic, never executed"); }
        static void BuildSettingsRuntime(string root)
        {
            // GUI setup invokes its runtime. Use a valid PE which only responds to read commands.
            string read = "{\"ok\":true,\"contacts\":[],\"recipients\":\"\",\"channel\":\"console\",\"count\":0}";
            string setup = "{\"ok\":true,\"transport\":{\"channel\":\"console\",\"host\":\"smtp.qq.com\",\"port\":465,\"username\":\"\",\"auth_code_present\":false,\"auth_code_source\":\"none\"},\"collection\":{\"browser_channel\":\"chrome\",\"accounts\":[]},\"source\":\"synthetic fixture\",\"repair_needed\":false,\"ready\":false,\"missing_steps\":[\"邮件设置\",\"监控账号\",\"订阅者\"]}";
            string source = "using System; using System.Text; internal static class FixtureRuntime { static int Main(string[] args) { "
                + "Console.OutputEncoding = new UTF8Encoding(false); if (args.Length != 3 || args[0] != \"-m\" || args[1] != \"quota_monitor.desktop_settings\") return 7; "
                + "if (args[2] == \"read\") Console.WriteLine(\"" + read.Replace("\"", "\\\"") + "\"); "
                + "else if (args[2] == \"read-setup\") Console.WriteLine(\"" + setup.Replace("\"", "\\\"") + "\"); else return 8; return 0; } }";
            string sourcePath = Path.Combine(root, "data", "desktop", "FixtureRuntime.cs");
            File.WriteAllText(sourcePath, source, new UTF8Encoding(false));
            string compiler = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe");
            string executable = Path.Combine(root, "runtime", "python", "python.exe");
            ProcessStartInfo info = new ProcessStartInfo(compiler, "/nologo /target:exe /platform:x64 /codepage:65001 /out:\"" + executable + "\" \"" + sourcePath + "\"");
            info.UseShellExecute = false; info.CreateNoWindow = true; info.RedirectStandardOutput = true; info.RedirectStandardError = true;
            using (Process process = Process.Start(info)) {
                var stdout = process.StandardOutput.ReadToEndAsync(); var stderr = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(10000)) { process.Kill(); throw new Exception("Synthetic settings runtime compilation timed out"); }
                Check(process.ExitCode == 0, "Synthetic settings runtime compilation failed");
                Check(stdout.Wait(2000) && stderr.Wait(2000), "Synthetic settings runtime compiler output did not complete");
            }
        }
        static void Offscreen(Form form)
        {
            form.StartPosition = FormStartPosition.Manual; form.Location = new Point(-16000, -16000); form.ShowInTaskbar = false;
        }
        static object Setup(string method, params object[] args)
        {
            Type type = typeof(Program).Assembly.GetType("CodexQuotaDesktop.PortableConfiguration");
            Check(type != null, "First-run configuration initialization is missing");
            MethodInfo call = type.GetMethod(method, BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            Check(call != null, "First-run configuration method is missing: " + method);
            try { return call.Invoke(null, args); }
            catch (TargetInvocationException ex) { throw ex.InnerException; }
        }
        static void VerifySetupDialog(DashboardState state, MonitorProcess monitor, Action open)
        {
            bool shown = false;
            string failure = null;
            DateTime deadline = DateTime.UtcNow.AddSeconds(5);
            using (System.Windows.Forms.Timer close = new System.Windows.Forms.Timer { Interval = 20 })
            {
                close.Tick += delegate {
                    SettingsWindow settings = Application.OpenForms.OfType<SettingsWindow>().FirstOrDefault(form => form.Visible);
                    if (settings == null) return;
                    shown = true;
                    TabControl tabs = settings.Controls.OfType<TabControl>().FirstOrDefault();
                    if (tabs == null || tabs.SelectedTab.Text != "邮件发送") failure = "First-run settings must select the GUI mail sender tab";
                    else if (settings.Bounds.Right >= 0 || settings.Bounds.Bottom >= 0 || settings.ShowInTaskbar) failure = "Diagnostic settings must remain offscreen";
                    else if (state.Active || monitor.Running) failure = "Opening first-run settings started monitoring";
                    close.Stop(); settings.Dispose();
                };
                close.Start();
                open();
                while (!shown && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(10); }
            }
            Check(shown, "First-run launch must automatically open GUI settings");
            Check(failure == null, failure);
        }
        public static int Run(string output)
        {
            output = Path.GetFullPath(output);
            Directory.CreateDirectory(output);
            string fixture = Path.Combine(output, "portable-fixture-" + Guid.NewGuid().ToString("N"));
            string report = Path.Combine(output, "portable-test.txt");
            try
            {
                string root = Path.Combine(fixture, "原始 中文 目录");
                string python = Path.Combine(root, "runtime", "python", "python.exe");
                string node = Path.Combine(root, "runtime", "node", "node.exe");
                string devPython = Path.Combine(root, "开发 Python.exe");
                string devNode = Path.Combine(root, "开发 Node.exe");
                Stub(python); Stub(node); Stub(devPython); Stub(devNode);
                string paths = Path.Combine(root, "data", "desktop"); Directory.CreateDirectory(paths);
                File.WriteAllText(Path.Combine(paths, "python-path.txt"), devPython);
                File.WriteAllText(Path.Combine(paths, "node-path.txt"), devNode);
                using (MonitorProcess monitor = new MonitorProcess(root))
                {
                    Check(monitor.Runtime("python") == python, "Bundled Python must take priority over development path");
                    Check(monitor.Runtime("node") == node, "Bundled Node must take priority over development path");
                    File.Delete(python); File.Delete(node);
                    Check(monitor.Runtime("python") == devPython && monitor.Runtime("node") == devNode, "Source checkout runtime fallback changed");
                }
                Stub(python); Stub(node);
                string moved = Path.Combine(fixture, "搬移后 中文 与 空格");
                Directory.Move(root, moved); root = moved;
                using (MonitorProcess monitor = new MonitorProcess(root))
                {
                    Check(monitor.Runtime("python") == Path.Combine(root, "runtime", "python", "python.exe"), "Python runtime did not relocate");
                    Check(monitor.Runtime("node") == Path.Combine(root, "runtime", "node", "node.exe"), "Node runtime did not relocate");
                }
                Check((string)Setup("SelectBrowserChannel", true, true) == "chrome", "Installed Chrome must take priority");
                Check((string)Setup("SelectBrowserChannel", false, true) == "msedge", "Installed Edge fallback is missing");
                string[] browserEnvironment = new string[] { "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA" };
                string[] previousEnvironment = browserEnvironment.Select(name => Environment.GetEnvironmentVariable(name)).ToArray();
                string installation = Path.Combine(root, "模拟 标准安装目录");
                try
                {
                    foreach (string name in browserEnvironment) Environment.SetEnvironmentVariable(name, installation);
                    Stub(Path.Combine(root, "自定义目录", "Chrome", "chrome.exe"));
                    Stub(Path.Combine(installation, "Microsoft", "Edge", "Application", "msedge.exe"));
                    Check((string)Setup("PreferredBrowserChannel") == "msedge", "Browser selection must use locations supported by the background collector");
                    Stub(Path.Combine(installation, "Google", "Chrome", "Application", "chrome.exe"));
                    Check((string)Setup("PreferredBrowserChannel") == "chrome", "Standard-directory Chrome must take priority over Edge");
                }
                finally
                {
                    for (int i = 0; i < browserEnvironment.Length; i++) Environment.SetEnvironmentVariable(browserEnvironment[i], previousEnvironment[i]);
                }
                string template = Path.Combine(root, "config.browser.example.toml");
                string config = Path.Combine(root, "config.browser.local.toml");
                File.WriteAllText(template, Template, new UTF8Encoding(false));
                Check((bool)Setup("EnsureLocalConfig", root, "msedge"), "Missing local configuration was not created");
                string first = File.ReadAllText(config);
                Check(first.Contains("channel = \"msedge\" # browser") && first.Contains("channel = \"console\""), "Browser fallback changed another configuration section");
                byte[] original = new UTF8Encoding(true).GetPreamble().Concat(Encoding.UTF8.GetBytes("# 用户已有配置\r\n[browser]\r\nchannel='custom'\r\n")).ToArray();
                File.WriteAllBytes(config, original); File.Delete(template);
                Check(!(bool)Setup("EnsureLocalConfig", root, "chrome"), "Existing configuration was treated as first run");
                Check(File.ReadAllBytes(config).SequenceEqual(original), "Existing configuration bytes were overwritten");
                File.Delete(config);
                bool missing = false;
                try { Setup("EnsureLocalConfig", root, "chrome"); }
                catch (FileNotFoundException ex) { missing = ex.Message.Contains("config.browser.example.toml"); }
                Check(missing && !File.Exists(config), "Missing template must give a clear error without creating config");
                File.WriteAllText(template, Template, new UTF8Encoding(false));
                Check((bool)Setup("EnsureLocalConfig", root, "chrome") && File.ReadAllText(config) == Template, "Chrome first run must preserve clean template");
                File.Delete(config);
                bool[] created = new bool[2]; Exception[] errors = new Exception[2];
                using (ManualResetEvent gate = new ManualResetEvent(false))
                {
                    Thread[] threads = new Thread[2];
                    for (int i = 0; i < threads.Length; i++)
                    {
                        int index = i;
                        threads[i] = new Thread(delegate() {
                            try { gate.WaitOne(); created[index] = (bool)Setup("EnsureLocalConfig", root, "chrome"); }
                            catch (Exception ex) { errors[index] = ex; }
                        });
                        threads[i].Start();
                    }
                    gate.Set(); foreach (Thread thread in threads) Check(thread.Join(5000), "Exclusive configuration creation timed out");
                }
                Check(errors.All(error => error == null) && created.Count(value => value) == 1 && File.ReadAllText(config) == Template,
                    "Concurrent first run must exclusively create one complete configuration");
                Check(!(bool)Setup("IsSetupPending", root), "Existing users must not be marked as incomplete setup");
                byte[] configuredBytes = File.ReadAllBytes(config);
                Setup("MarkSetupPending", root);
                Check((bool)Setup("IsSetupPending", root), "First-run setup must stay pending after the local config exists");
                Check(!(bool)Setup("EnsureLocalConfig", root, "chrome") && (bool)Setup("IsSetupPending", root), "Restarting an incomplete setup must retain the pending marker");
                using (FileStream locked = new FileStream(Path.Combine(root, "data", "desktop", "setup-pending"), FileMode.Open, FileAccess.Read, FileShare.None))
                {
                    bool blocked = false;
                    try { Setup("CompleteSetup", root); }
                    catch (InvalidOperationException ex) { blocked = ex.Message == "无法完成首次设置，请重试保存后再启动监控。"; }
                    Check(blocked && (bool)Setup("IsSetupPending", root), "Unavailable setup marker must keep setup pending and report a safe error");
                }
                Setup("CompleteSetup", root);
                Setup("CompleteSetup", root);
                Check(!(bool)Setup("IsSetupPending", root) && File.ReadAllBytes(config).SequenceEqual(configuredBytes), "Completing setup must clear only its marker and preserve config bytes");
                Setup("MarkSetupPending", root);
                ConstructorInfo constructor = typeof(TrayApplication).GetConstructor(new Type[] { typeof(string), typeof(bool), typeof(bool), typeof(bool) });
                Check(constructor != null, "Stopped setup launch option is missing");
                BuildSettingsRuntime(root);
                DashboardWindow dashboard = new DashboardWindow(); Offscreen(dashboard);
                TrayApplication app = new TrayApplication(root, true, true, false, dashboard, delegate(SettingsWindow settings) { Offscreen(settings); });
                try
                {
                    DashboardState state = (DashboardState)typeof(TrayApplication).GetField("state", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(app);
                    MonitorProcess monitor = (MonitorProcess)typeof(TrayApplication).GetField("monitor", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(app);
                    DashboardWindow window = (DashboardWindow)typeof(TrayApplication).GetField("window", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(app);
                    Check(!state.Active && state.Phase == "stopped" && !monitor.Running, "First-run setup started or attempted monitoring");
                    Check(window.Visible && window.StartButton.Enabled && !window.StopButton.Enabled, "Autostart first run must show a stopped setup window");
                    VerifySetupDialog(state, monitor, delegate { Application.DoEvents(); });
                    Check(window.Controls.Find("portableSetupDescription", true).Any(control => control.Text.Contains("邮件发送") && control.Text.Contains("监控账号") && control.Text.Contains("浏览器") && !control.Text.Contains("LoginX")), "First-run setup instructions must explain GUI setup without requiring login");
                    VerifySetupDialog(state, monitor, delegate { window.StartButton.PerformClick(); });
                    Check(!state.Active && !monitor.Running, "Unsaved first-run setup must stay stopped when Start is clicked");
                    Check(!File.Exists(Path.Combine(root, "logs", "desktop-monitor.log")) && !Directory.Exists(Path.Combine(root, "data", "browser")), "First-run setup created monitoring data or logs");
                }
                finally { app.Quit(); }
                File.WriteAllText(report, "PASS bundled runtime priority, development fallback, relocation with Chinese/spaces, exclusive template-only setup, existing bytes, browser selection, missing-template error, persistent pending marker, safe marker failure, offscreen automatic GUI mail setup and unsaved first-run start guard", new UTF8Encoding(false));
                return 0;
            }
            catch (Exception ex) { File.WriteAllText(report, "FAIL " + ex, new UTF8Encoding(false)); return 1; }
            finally
            {
                // This GUID fixture is the only recursive deletion target and is bounded by output.
                string prefix = output.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                if (Path.GetFullPath(fixture).StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && Directory.Exists(fixture)) Directory.Delete(fixture, true);
            }
        }
    }
}
