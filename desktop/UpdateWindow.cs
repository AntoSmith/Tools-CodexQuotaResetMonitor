using System;
using System.Drawing;
using System.Diagnostics;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class UpdateWindow : Form
    {
        public readonly Button CheckButton, InstallButton;
        public readonly Label Status;
        readonly ProgressBar progress;
        readonly LinkLabel releasePage;
        readonly Func<ReleaseUpdateInfo> check;
        readonly Func<ReleaseUpdateInfo, Action<int>, string> prepare;
        readonly Action<string> install;
        readonly Action quit;
        readonly Func<string> blocked;
        ReleaseUpdateInfo release;
        bool busy;

        public UpdateWindow(string root, Action quit, Func<string> blocked = null,
            Func<ReleaseUpdateInfo> check = null,
            Func<ReleaseUpdateInfo, Action<int>, string> prepare = null,
            Action<string> install = null)
        {
            this.quit = quit; this.blocked = blocked;
            this.check = check ?? delegate { return ReleaseUpdates.Check(root); };
            this.prepare = prepare ?? delegate(ReleaseUpdateInfo value, Action<int> report) { return ReleaseUpdates.Prepare(root, value, report); };
            this.install = install ?? delegate(string plan) { ReleaseUpdates.LaunchInstaller(root, plan); };
            Text = "检查更新 · " + AppBrand.Name;
            ClientSize = new Size(610, 416); BackColor = Palette.Canvas; Icon = AppBrand.Icon;
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            Palette.Text(this, "让提醒工具保持最新", 28, 24, 548, 40, 20, Palette.Ink, true);
            Palette.Text(this, "从项目 GitHub Releases 检查 Windows 稳定版本。", 28, 72, 548, 32, 10, Palette.Muted, false);
            Panel card = new Panel { Left = 28, Top = 115, Width = 554, Height = 123, BackColor = Color.White, BorderStyle = BorderStyle.FixedSingle };
            Controls.Add(card);
            Status = Palette.Text(card, "点击检查，查看是否有新版本。", 18, 16, 514, 70, 11, Palette.Ink, false);
            progress = new ProgressBar { Left = 18, Top = 92, Width = 514, Height = 10, Visible = false };
            card.Controls.Add(progress);
            releasePage = new LinkLabel { Left = 28, Top = 246, Width = 200, Height = 25, Text = "查看 GitHub 版本说明", Enabled = false, Font = new Font("Microsoft YaHei UI", 10F), LinkColor = Palette.Blue };
            Controls.Add(releasePage);
            releasePage.LinkClicked += delegate {
                if (release != null && Regex.IsMatch(release.ReleaseUrl ?? "", @"\Ahttps://github\.com/AntoSmith/Tools-CodexQuotaResetMonitor/releases/tag/v[0-9]+\.[0-9]+\.[0-9]+\z"))
                    try { Process.Start(new ProcessStartInfo(release.ReleaseUrl) { UseShellExecute = true }); } catch { Status.Text = "无法打开浏览器，请稍后重试。"; }
            };
            Palette.Text(this, "升级后沿用当前配置、收件人、历史线索和浏览器资料。\n下载期间继续监控；安装时短暂退出，完成后自动重新打开。", 28, 278, 548, 52, 10, Palette.Muted, false);
            CheckButton = Palette.Button(this, "检查新版本", 28, 349, 166);
            InstallButton = Palette.Button(this, "下载并升级", 209, 349, 181); InstallButton.Enabled = false;
            InstallButton.BackColor = Palette.Blue; InstallButton.ForeColor = Color.White;
            Palette.Button(this, "关闭", 433, 349, 149).Click += delegate { Close(); };
            CheckButton.Click += async delegate { await CheckAsync(); };
            InstallButton.Click += async delegate { await InstallAsync(); };
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) { e.Cancel = true; Status.Text = "正在处理更新，请等待结果后关闭。"; } };
        }
        public async Task CheckAsync()
        {
            if (busy) return;
            busy = true; release = null; CheckButton.Enabled = InstallButton.Enabled = false;
            releasePage.Enabled = false;
            progress.Visible = false; Status.ForeColor = Palette.Muted; Status.Text = "正在连接 GitHub Releases…";
            try {
                ReleaseUpdateInfo result = await Task.Run(check);
                if (IsDisposed) return;
                release = result; Status.ForeColor = result.Available ? Palette.Blue : Palette.Green;
                releasePage.Enabled = !String.IsNullOrEmpty(result.ReleaseUrl);
                Status.Text = result.Available ? "发现新版本 v" + result.Version + "\n当前 v" + result.CurrentVersion + " · 可下载并升级"
                    : "当前 v" + result.CurrentVersion + "，已是最新稳定版本。";
            } catch (Exception) {
                if (!IsDisposed) { Status.Text = "检查失败：暂时无法获取有效的更新信息。\n请检查网络，稍后重试。"; Status.ForeColor = Palette.Amber; }
            } finally { busy = false; if (!IsDisposed) { CheckButton.Enabled = true; InstallButton.Enabled = release != null && release.Available; } }
        }
        public async Task InstallAsync()
        {
            if (busy || release == null || !release.Available) return;
            string reason = blocked == null ? "" : blocked();
            if (!String.IsNullOrEmpty(reason)) { Status.Text = reason; Status.ForeColor = Palette.Amber; return; }
            busy = true; CheckButton.Enabled = InstallButton.Enabled = false;
            progress.Visible = true; progress.Value = 0; Status.Text = "正在下载 v" + release.Version + "… 校验后安装。";
            Status.ForeColor = Palette.Blue;
            try {
                string plan = await Task.Run(delegate { return prepare(release, ReportProgress); });
                if (IsDisposed) return;
                reason = blocked == null ? "" : blocked();
                if (!String.IsNullOrEmpty(reason)) { Status.Text = reason; Status.ForeColor = Palette.Amber; return; }
                install(plan);
                busy = false; Close(); if (quit != null) quit();
            } catch (Exception) {
                if (!IsDisposed) { Status.Text = "更新未完成：下载、校验或准备安装失败。\n当前版本可继续使用，请稍后重试。"; Status.ForeColor = Palette.Amber; }
            } finally { busy = false; if (!IsDisposed) { CheckButton.Enabled = true; InstallButton.Enabled = release != null && release.Available; } }
        }
        void ReportProgress(int value)
        {
            if (!IsHandleCreated || IsDisposed) return;
            try { BeginInvoke(new MethodInvoker(delegate { if (!IsDisposed) progress.Value = Math.Max(0, Math.Min(100, value)); })); }
            catch (InvalidOperationException) { }
        }
    }
}
