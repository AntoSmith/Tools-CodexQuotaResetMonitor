using System;
using System.Drawing;
using System.IO;
using System.Security;
using System.Text;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal static class WelcomeGuide
    {
        static string Marker(string root) { return Path.Combine(root, "data", "desktop", "welcome-seen-v1"); }

        public static bool ShouldShow(string root, bool firstSetup)
        {
            if (!firstSetup) return false;
            try { return !File.Exists(Marker(root)); }
            catch (ArgumentException) { return true; }
            catch (NotSupportedException) { return true; }
            catch (SecurityException) { return true; }
        }

        // Failure to save this optional preference must not block configuration.
        public static bool MarkSeen(string root)
        {
            string temporary = null;
            try {
                string marker = Marker(root);
                if (File.Exists(marker)) return true;
                Directory.CreateDirectory(Path.GetDirectoryName(marker));
                temporary = marker + "." + Guid.NewGuid().ToString("N") + ".tmp";
                File.WriteAllText(temporary, "welcome-v1\n" + DateTime.UtcNow.ToString("o") + "\n", new UTF8Encoding(false));
                try { File.Move(temporary, marker); }
                catch (IOException) { if (!File.Exists(marker)) throw; }
                return true;
            }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
            catch (ArgumentException) { return false; }
            catch (NotSupportedException) { return false; }
            catch (SecurityException) { return false; }
            finally {
                if (temporary != null) {
                    try { if (File.Exists(temporary)) File.Delete(temporary); }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                    catch (SecurityException) { }
                }
            }
        }
    }

    internal sealed class WelcomeWindow : Form
    {
        public readonly Button ConfigureButton, SkipButton;
        readonly Panel footer;

        public WelcomeWindow(bool configured = false)
        {
            Text = "使用引导 · " + AppBrand.Name;
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            ClientSize = new Size(600, 550); MinimumSize = new Size(480, 460);
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
            BackColor = Palette.Canvas; ForeColor = Palette.Ink;
            Font = new Font("Microsoft YaHei UI", 9F); Icon = AppBrand.Icon;

            Panel body = new Panel { Name = "welcome-content", Dock = DockStyle.Fill,
                AutoScroll = true, AutoScrollMinSize = new Size(0, 442), BackColor = Palette.Canvas };
            footer = new Panel { Name = "welcome-footer", Dock = DockStyle.Bottom, Height = 108,
                BackColor = Color.White };
            Controls.Add(body); Controls.Add(footer);
            Panel logo = new Panel { Left = 24, Top = 23, Width = 36, Height = 36 };
            logo.Paint += delegate(object sender, PaintEventArgs args) { args.Graphics.DrawIcon(AppBrand.Icon, logo.ClientRectangle); };
            body.Controls.Add(logo);
            Label title = Palette.Text(body, configured ? "随时查看，按需调整" : "欢迎使用，先花一分钟了解", 72, 18, 504, 32, 17, Palette.Ink, true);
            title.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Label introduction = Palette.Text(body, "完成下面三步，即可在本机接收公开重置线索。", 72, 55, 504, 28, 9.5F, Palette.Muted, false);
            introduction.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Step(body, "welcome-step-mail", "1", "设置发件邮箱",
                "在“邮件发送”填写发件邮箱和 SMTP 授权码并保存。\nQQ 邮箱需先在邮箱网页设置中开启 SMTP 服务。", 97);
            Step(body, "welcome-step-recipients", "2", "添加收件人并测试",
                "到“订阅管理”添加收件邮箱，保存后发送测试邮件。\n收到测试邮件后，再设置提醒偏好和订阅时间。", 177);
            Step(body, "welcome-step-collection", "3", "选择采集浏览器，启动监控",
                "在“采集设置”选择 Chrome / Edge 和要监控的公开账号。\n保存后回到主窗口，点击“启动监控”。", 257);
            Label privacy = Palette.Text(body,
                "授权码仅保存在本机设置中，请妥善保管。\n本软件不会要求填写 X / ChatGPT 账号密码。", 26, 344, 548, 38, 9, Palette.Muted, false);
            privacy.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Label background = Palette.Text(body,
                "关闭主窗口会留在右下角托盘；彻底退出请选择“退出并停止监控”。\n提醒仅为公开线索，请结合原帖与自己的额度核实。", 26, 391, 548, 40, 9, Palette.Muted, false);
            background.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Label later = Palette.Text(footer, configured ? "随时可从“设置”重新查看本引导。" : "跳过后保持停止，不发送邮件；稍后可从“设置”继续。",
                24, 10, 552, 28, 9, Palette.Muted, false);
            later.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            SkipButton = Palette.Button(footer, configured ? "关闭引导" : "稍后再说 / 跳过", 280, 46, 142);
            SkipButton.Name = "welcome-skip"; SkipButton.DialogResult = DialogResult.Cancel;
            ConfigureButton = Palette.Button(footer, "立即配置", 434, 46, 142);
            ConfigureButton.Name = "welcome-configure"; ConfigureButton.DialogResult = DialogResult.OK;
            ConfigureButton.BackColor = Palette.Blue; ConfigureButton.ForeColor = Color.White;
            ConfigureButton.FlatAppearance.BorderColor = Palette.Blue;
            ConfigureButton.FlatAppearance.MouseOverBackColor = Color.FromArgb(32, 65, 134);
            SkipButton.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };
            ConfigureButton.Click += delegate { DialogResult = DialogResult.OK; Close(); };
            AcceptButton = ConfigureButton; CancelButton = SkipButton;
            FitToWorkingArea(Screen.PrimaryScreen.WorkingArea);
        }

        static void Step(Control parent, string name, string number, string title, string description, int top)
        {
            Panel card = new Panel { Name = name, Left = 24, Top = top, Width = 552, Height = 74,
                BackColor = Color.White, Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
            parent.Controls.Add(card);
            Label badge = Palette.Text(card, number, 14, 20, 30, 30, 14, Palette.Blue, true);
            badge.TextAlign = ContentAlignment.MiddleCenter;
            Label heading = Palette.Text(card, title, 58, 8, 478, 25, 11, Palette.Ink, true);
            Label detail = Palette.Text(card, description, 58, 35, 478, 34, 9, Palette.Muted, false);
            heading.Anchor = detail.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        }

        protected override void OnLayout(LayoutEventArgs e)
        {
            base.OnLayout(e);
            if (ConfigureButton == null || SkipButton == null || footer == null) return;
            int margin = Math.Max(18, (int)Math.Round(24F * ConfigureButton.Height / 42F));
            ConfigureButton.Left = footer.ClientSize.Width - margin - ConfigureButton.Width;
            SkipButton.Left = ConfigureButton.Left - Math.Max(10, margin / 2) - SkipButton.Width;
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e); FitToWorkingArea(Screen.FromControl(this).WorkingArea);
        }

        internal void FitToWorkingArea(Rectangle workingArea)
        {
            int width = Math.Max(1, workingArea.Width - 24), height = Math.Max(1, workingArea.Height - 24);
            // The footer remains visible while the introduction scrolls on short displays.
            MinimumSize = new Size(Math.Min(MinimumSize.Width, width), Math.Min(MinimumSize.Height, height));
            Size = new Size(Math.Min(Width, width), Math.Min(Height, height));
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (DialogResult == DialogResult.None) DialogResult = DialogResult.Cancel;
            base.OnFormClosing(e);
        }
    }
}
