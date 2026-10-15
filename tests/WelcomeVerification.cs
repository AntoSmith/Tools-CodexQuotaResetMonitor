using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal static class WelcomeVerification
    {
        static void Check(bool value, string message) { if (!value) throw new Exception(message); }
        static Type Guide { get { return typeof(WelcomeVerification).Assembly.GetType("CodexQuotaDesktop.WelcomeGuide"); } }
        static bool ShouldShow(string root, bool firstSetup)
        { return (bool)Guide.GetMethod("ShouldShow").Invoke(null, new object[] { root, firstSetup }); }
        static bool MarkSeen(string root)
        { return (bool)Guide.GetMethod("MarkSeen").Invoke(null, new object[] { root }); }
        static Form Window(bool configured)
        {
            Type type = typeof(WelcomeVerification).Assembly.GetType("CodexQuotaDesktop.WelcomeWindow");
            Check(type != null, "Welcome window is missing");
            return (Form)Activator.CreateInstance(type, new object[] { configured });
        }
        static T Find<T>(Control parent, string name) where T : Control
        { return parent.Controls.Find(name, true).OfType<T>().SingleOrDefault(); }
        static void Click(Button button)
        { typeof(Button).GetMethod("OnClick", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(button, new object[] { EventArgs.Empty }); }
        static void Handles(Control control)
        { IntPtr handle = control.Handle; foreach (Control child in control.Controls) Handles(child); }
        static string TextOf(Control control)
        { return control.Text + string.Join("", control.Controls.Cast<Control>().Select(TextOf).ToArray()); }
        static void Snapshot(Form form, string path)
        {
            form.ShowInTaskbar = false; form.StartPosition = FormStartPosition.Manual;
            form.Location = new Point(-16000, -16000); Handles(form); form.PerformLayout();
            using (Bitmap bitmap = new Bitmap(form.Width, form.Height)) {
                form.DrawToBitmap(bitmap, new Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(path, ImageFormat.Png);
            }
        }
        static string Hash(string path)
        { using (SHA256 hash = SHA256.Create()) return Convert.ToBase64String(hash.ComputeHash(File.ReadAllBytes(path))); }
        static void AssertVisibleActions(Form window)
        {
            Button configure = Find<Button>(window, "welcome-configure"), skip = Find<Button>(window, "welcome-skip");
            Check(configure != null && skip != null, "Guide actions are missing");
            Check(configure.Bottom <= configure.Parent.ClientSize.Height && skip.Bottom <= skip.Parent.ClientSize.Height, "Guide actions are clipped");
            Check(configure.Left >= skip.Right + 8 && configure.Right <= configure.Parent.ClientSize.Width - 12, "Guide actions overlap");
            foreach (string name in new[] { "welcome-step-mail", "welcome-step-recipients", "welcome-step-collection" }) {
                Control step = Find<Control>(window, name); Check(step != null, "A configuration step is missing");
                foreach (Label text in step.Controls.OfType<Label>()) {
                    Size measured = TextRenderer.MeasureText(text.Text, text.Font, new Size(text.ClientSize.Width, int.MaxValue), TextFormatFlags.WordBreak | TextFormatFlags.NoPadding);
                    Check(measured.Height <= text.ClientSize.Height + 2, "Step text is clipped: " + text.Text);
                }
            }
        }
        [STAThread]
        static int Main(string[] args)
        {
            string fixtureRoot = Path.GetFullPath(Path.Combine(args[0], "data", "desktop"));
            string fixture = Path.Combine(fixtureRoot, "welcome-test-" + Guid.NewGuid().ToString("N"));
            try {
                Application.EnableVisualStyles();
                Check(Guide != null, "Welcome guide state helper is missing");
                Directory.CreateDirectory(fixture);
                string config = Path.Combine(fixture, "config.browser.local.toml");
                File.WriteAllText(config, "# untouched sample\n[mail]\nchannel = 'console'\n");
                string original = Hash(config);
                Check(ShouldShow(fixture, true), "Fresh setup did not request the guide");
                Check(!ShouldShow(fixture, false), "Existing users were prompted during an ordinary update");
                using (Form window = Window(false)) {
                    string content = TextOf(window);
                    Check(content.Contains("SMTP 授权码") && content.Contains("本机"), "Mail credential guidance is missing");
                    Check(content.Contains("X / ChatGPT") && content.Contains("密码"), "Account password guidance is missing");
                    Check(content.Contains("测试邮件") && content.Contains("浏览器") && content.Contains("启动监控"), "Configuration sequence is incomplete");
                    Check(content.Contains("托盘") && content.Contains("退出并停止监控"), "Background and quit guidance is missing");
                    Check(content.Contains("原帖") && content.Contains("额度"), "Reminder verification guidance is missing");
                    Check(content.Contains("跳过") && content.Contains("停止") && content.Contains("不发送"), "Skipping consequences are unclear");
                    Handles(window); window.PerformLayout(); AssertVisibleActions(window);
                    Snapshot(window, Path.Combine(args[0], "data", "desktop", "welcome-preview.png"));
                    window.Scale(new SizeF(1.25F, 1.25F)); window.PerformLayout(); AssertVisibleActions(window);
                    Check(window.Width <= 1000 && window.Height <= 744, "125% guide cannot fit a 1024x768 working area");
                    Snapshot(window, Path.Combine(args[0], "data", "desktop", "welcome-preview-125.png"));
                    window.GetType().GetMethod("FitToWorkingArea", BindingFlags.Instance | BindingFlags.NonPublic)
                        .Invoke(window, new object[] { new Rectangle(0, 0, 800, 600) });
                    window.PerformLayout(); AssertVisibleActions(window);
                    Check(window.Height <= 576 && window.Width <= 776, "Guide was not constrained to a smaller working area");
                }
                using (Form window = Window(false)) {
                    int clicked = 0; Button skip = Find<Button>(window, "welcome-skip"); skip.Click += delegate { clicked++; };
                    Check(window.CancelButton == skip, "Escape does not use Skip"); Click(skip);
                    Check(window.DialogResult == DialogResult.Cancel && clicked == 1, "Skip did not finish the guide as cancel");
                }
                using (Form window = Window(false)) {
                    Check(window.AcceptButton == Find<Button>(window, "welcome-configure"), "Enter does not select Configure");
                    Click(Find<Button>(window, "welcome-configure")); Check(window.DialogResult == DialogResult.OK, "Configure did not request settings");
                }
                using (Form window = Window(false)) {
                    Handles(window); window.Close(); Check(window.DialogResult == DialogResult.Cancel, "Window X did not count as Skip");
                }
                using (Form window = Window(true)) {
                    string content = TextOf(window);
                    Check(!content.Contains("保持停止") && !content.Contains("不发送邮件"), "Returning guide falsely says existing monitoring will stop");
                }
                Check(ShouldShow(fixture, true), "Reading or skipping the window touched persisted state without the host action");
                Check(Hash(config) == original, "Guide interaction changed configuration");
                Check(MarkSeen(fixture), "Guide dismissal did not persist");
                Check(File.Exists(Path.Combine(fixture, "data", "desktop", "welcome-seen-v1")), "Guide marker is not in the user data sidecar");
                Check(!ShouldShow(fixture, true) && !ShouldShow(fixture, false), "Dismissed guide is shown again");
                Check(MarkSeen(fixture), "Repeated dismissal failed");
                Check(Directory.GetFiles(Path.Combine(fixture, "data", "desktop")).Length == 1, "Atomic marker write left temporary files");
                Check(Hash(config) == original, "Persisting guide state changed configuration");
                string blockedRoot = Path.Combine(fixture, "blocked"); Directory.CreateDirectory(blockedRoot);
                File.WriteAllText(Path.Combine(blockedRoot, "data"), "file blocks directory");
                Check(!MarkSeen(blockedRoot), "Unwritable guide state was falsely reported as saved");
                Check(ShouldShow(blockedRoot, true), "A failed marker write prevents showing the guide");
                Console.WriteLine("PASS welcome configure/skip/X behavior, existing-user gating, safe atomic dismissal marker, configuration isolation, and offscreen layout at 100% / 125%; previews rendered.");
                return 0;
            } catch (Exception ex) { Console.Error.WriteLine("FAIL " + ex); return 1; }
            finally {
                string resolved = Path.GetFullPath(fixture);
                if (resolved.StartsWith(fixtureRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) && Directory.Exists(resolved))
                    Directory.Delete(resolved, true);
            }
        }
    }
}
