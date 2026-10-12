using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal static class Palette
    {
        public static readonly Color Ink = Color.FromArgb(25, 39, 67), Blue = Color.FromArgb(40, 78, 157),
            Muted = Color.FromArgb(106, 121, 144), Canvas = Color.FromArgb(242, 245, 250),
            Green = Color.FromArgb(24, 117, 97), Amber = Color.FromArgb(163, 100, 26);
        public static Label Text(Control parent, string value, int x, int y, int width, int height, float size, Color color, bool bold)
        {
            Label label = new Label { Text = value, Left = x, Top = y, Width = width, Height = height,
                Font = new Font("Microsoft YaHei UI", size, bold ? FontStyle.Bold : FontStyle.Regular),
                ForeColor = color, BackColor = Color.Transparent, AutoEllipsis = true };
            parent.Controls.Add(label); return label;
        }
        public static Button Button(Control parent, string text, int x, int y, int width)
        {
            Button button = new Button { Text = text, Left = x, Top = y, Width = width, Height = 42,
                Font = new Font("Microsoft YaHei UI", 9F), FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand,
                BackColor = Color.White, ForeColor = Ink, UseVisualStyleBackColor = false };
            button.FlatAppearance.BorderColor = Color.FromArgb(218, 225, 236);
            button.FlatAppearance.MouseOverBackColor = Color.FromArgb(229, 236, 249);
            parent.Controls.Add(button); return button;
        }
    }

    internal sealed class Card : Panel
    {
        public Card(int x, int y, int width, int height)
        { SetBounds(x, y, width, height); BackColor = Color.White; }
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using (Pen pen = new Pen(Color.FromArgb(224, 231, 240))) e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
        }
    }

    internal sealed class DashboardWindow : Form
    {
        public readonly Button StartButton, StopButton, ConfigButton, LogButton, PreviewButton, LatestButton, SettingsButton, HistoryButton;
        readonly Label badge, heading, description, rounds, failures, started, ended, next, nextTime, result, latest;
        public DashboardWindow()
        {
            Text = AppBrand.Name; ClientSize = new Size(820, 590);
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            BackColor = Palette.Canvas; Font = new Font("Microsoft YaHei UI", 9F);
            StartPosition = FormStartPosition.CenterScreen; FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false; Icon = AppBrand.Icon;
            Palette.Text(this, AppBrand.Name, 28, 23, 580, 36, 21, Palette.Ink, true);
            Palette.Text(this, "关注新线索，让等待有迹可循。", 30, 66, 510, 26, 10, Palette.Muted, false);
            badge = Palette.Text(this, "已停止", 634, 34, 154, 32, 10, Palette.Muted, true);
            badge.TextAlign = ContentAlignment.MiddleCenter;
            SettingsButton = Palette.Button(this, "设置", 650, 74, 142); SettingsButton.Height = 28;
            Card status = new Card(28, 111, 764, 103); Controls.Add(status);
            Panel stripe = new Panel { Left = 0, Top = 0, Width = 5, Height = 103, BackColor = Palette.Blue }; status.Controls.Add(stripe);
            heading = Palette.Text(status, "准备好后，开始监控", 22, 17, 718, 33, 16, Palette.Ink, true);
            description = Palette.Text(status, "启动后自动定期采集，关闭窗口也会在托盘继续运行。", 23, 60, 718, 30, 10, Palette.Muted, false);

            Card a = new Card(28, 230, 228, 116); Controls.Add(a);
            Palette.Text(a, "本次启动 · 已结束轮数", 17, 14, 198, 22, 9, Palette.Muted, false);
            rounds = Palette.Text(a, "0 轮", 16, 41, 190, 39, 23, Palette.Ink, true);
            failures = Palette.Text(a, "失败 / 空结果：0 轮", 17, 86, 197, 22, 9, Palette.Muted, false);
            Card b = new Card(268, 230, 258, 116); Controls.Add(b);
            Palette.Text(b, "本轮 / 最近一轮开始", 17, 14, 224, 22, 9, Palette.Muted, false);
            started = Palette.Text(b, "—", 16, 45, 228, 30, 15, Palette.Ink, true);
            ended = Palette.Text(b, "结束：—", 17, 86, 226, 22, 8.5F, Palette.Muted, false);
            Card c = new Card(538, 230, 254, 116); Controls.Add(c);
            Palette.Text(c, "距离下一轮", 17, 14, 222, 22, 9, Palette.Muted, false);
            next = Palette.Text(c, "—", 16, 41, 222, 39, 23, Palette.Blue, true);
            nextTime = Palette.Text(c, "计划开始：—", 17, 86, 228, 22, 8.5F, Palette.Muted, false);

            result = Palette.Text(this, "等待首轮采集结果", 30, 360, 760, 28, 10, Palette.Muted, false);
            StartButton = Palette.Button(this, "启动监控", 28, 400, 162);
            StopButton = Palette.Button(this, "停止监控", 202, 400, 126);
            PreviewButton = Palette.Button(this, "预览提醒", 340, 400, 144);
            ConfigButton = Palette.Button(this, "打开配置", 496, 400, 142);
            LogButton = Palette.Button(this, "查看日志", 650, 400, 142);

            Card recent = new Card(28, 462, 764, 77); Controls.Add(recent);
            Palette.Text(recent, "最近发现", 17, 11, 435, 22, 9, Palette.Muted, false);
            latest = Palette.Text(recent, "本次尚无新线索", 17, 38, 435, 26, 10, Palette.Ink, false);
            HistoryButton = Palette.Button(recent, "历史线索", 466, 18, 135);
            LatestButton = Palette.Button(recent, "查看线索", 610, 18, 135); LatestButton.Enabled = false;
            Palette.Text(this, "关闭窗口收回托盘 · 登录自启动可在右上角设置中查看 · 电脑休眠时暂停采集", 30, 554, 758, 25, 9, Palette.Muted, false);
        }
        public void RefreshState(DashboardState state)
        {
            StartButton.Text = state.Active ? "●  监控中" : "启动监控";
            StartButton.BackColor = state.Active ? Palette.Ink : Palette.Blue;
            StartButton.ForeColor = Color.White;
            StartButton.FlatAppearance.MouseOverBackColor = state.Active ? Palette.Ink : Color.FromArgb(31, 64, 134);
            StartButton.FlatAppearance.MouseDownBackColor = Palette.Ink;
            StopButton.Enabled = state.Active;
            badge.Text = state.Active ? "●  监控中" : (state.Phase == "error" ? "●  已停止 / 异常" : "●  已停止");
            badge.ForeColor = state.Active ? Palette.Green : Palette.Muted;
            badge.BackColor = state.Active ? Color.FromArgb(226, 242, 237) : Color.FromArgb(228, 233, 241);
            if (state.Phase == "collecting") {
                heading.Text = "正在采集第 " + state.CurrentRound + " 轮";
                description.Text = "正在读取公开页面。默认在后台采集，无需打开或操作浏览器。";
            } else if (state.Phase == "waiting") {
                heading.Text = "本轮已结束，等待下一轮";
                description.Text = "采集浏览器已关闭，监控仍在运行；到达计划时间后自动开始下一轮。";
            } else if (state.Phase == "starting") {
                heading.Text = "监控已启动，正在准备采集";
                description.Text = "正在读取配置并启动采集程序，稍后显示真实进度。";
            } else {
                heading.Text = state.Phase == "error" ? "监控已停止，请查看日志" : "监控已停止";
                description.Text = String.IsNullOrEmpty(state.Error) ? "点击启动监控可重新加载配置；本次启动的轮数将归零。" : state.Error;
            }
            rounds.Text = state.RoundsCompleted + " 轮";
            failures.Text = "失败 / 空结果：" + state.Failures + " 轮";
            started.Text = DashboardState.LocalTime(state.StartedAt);
            ended.Text = "结束：" + (state.Phase == "collecting" || state.LastOutcome == "interrupted" ? "—" : DashboardState.LocalTime(state.EndedAt));
            next.Text = state.Countdown; nextTime.Text = "计划开始：" + DashboardState.LocalTime(state.NextAt);
            result.Text = state.ResultText;
            result.ForeColor = state.Phase != "collecting" && (state.LastOutcome == "error" || state.LastOutcome == "empty") ? Palette.Amber : Palette.Muted;
            LatestButton.Enabled = state.LastAlert != null;
            latest.Text = state.LastAlert == null ? "本次尚无新线索" : state.LastAlert.Title + "  ·  @" + state.LastAlert.Username;
        }
    }
}
