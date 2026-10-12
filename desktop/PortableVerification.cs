using System;
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
        static object Setup(string method, params object[] args)
        {
            Type type = typeof(Program).Assembly.GetType("CodexQuotaDesktop.PortableConfiguration");
            Check(type != null, "First-run configuration initialization is missing");
            MethodInfo call = type.GetMethod(method, BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            Check(call != null, "First-run configuration method is missing: " + method);
            try { return call.Invoke(null, args); }
            catch (TargetInvocationException ex) { throw ex.InnerException; }
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
                ConstructorInfo constructor = typeof(TrayApplication).GetConstructor(new Type[] { typeof(string), typeof(bool), typeof(bool), typeof(bool) });
                Check(constructor != null, "Stopped setup launch option is missing");
                TrayApplication app = (TrayApplication)constructor.Invoke(new object[] { root, true, true, false });
                try
                {
                    DashboardState state = (DashboardState)typeof(TrayApplication).GetField("state", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(app);
                    MonitorProcess monitor = (MonitorProcess)typeof(TrayApplication).GetField("monitor", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(app);
                    DashboardWindow window = (DashboardWindow)typeof(TrayApplication).GetField("window", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(app);
                    Check(!state.Active && state.Phase == "stopped" && !monitor.Running, "First-run setup started or attempted monitoring");
                    Check(window.Visible && window.StartButton.Enabled && !window.StopButton.Enabled, "Autostart first run must show a stopped setup window");
                    Check(window.Controls.Find("portableSetupDescription", true).Any(control => control.Text.Contains("打开配置")), "First-run setup instructions are missing");
                    Check(!File.Exists(Path.Combine(root, "logs", "desktop-monitor.log")) && !Directory.Exists(Path.Combine(root, "data", "browser")), "First-run setup created monitoring data or logs");
                }
                finally { app.Quit(); }
                File.WriteAllText(report, "PASS bundled runtime priority, development fallback, relocation with Chinese/spaces, exclusive template-only setup, existing bytes, browser selection, missing-template error and stopped setup UI", new UTF8Encoding(false));
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
