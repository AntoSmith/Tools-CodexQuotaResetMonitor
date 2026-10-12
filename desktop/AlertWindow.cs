using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class Fireworks : Panel
    {
        readonly Timer animation = new Timer { Interval = 33 };
        readonly bool celebrate;
        int frame;
        public Fireworks(bool celebrate)
        {
            this.celebrate = celebrate; DoubleBuffered = true;
            animation.Tick += delegate { frame++; Invalidate(); if (frame >= 90) animation.Stop(); };
            if (celebrate) animation.Start();
        }
        public void PreviewFrame() { animation.Stop(); frame = 29; Invalidate(); }
        protected override void Dispose(bool disposing) { if (disposing) animation.Dispose(); base.Dispose(disposing); }
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e); Graphics g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
            using (LinearGradientBrush b = new LinearGradientBrush(ClientRectangle, Palette.Ink, Color.FromArgb(53, 65, 114), 20F)) g.FillRectangle(b, ClientRectangle);
            using (Pen ring = new Pen(Color.FromArgb(65, 148, 180, 223), 1.5F)) {
                g.DrawEllipse(ring, Width - 140, -80, 235, 235); g.DrawEllipse(ring, -75, 75, 160, 160);
            }
            if (celebrate && frame < 90) {
                Color[] colors = { Color.FromArgb(255, 215, 118), Color.FromArgb(112, 228, 206), Color.FromArgb(158, 177, 255) };
                for (int burst = 0; burst < 3; burst++) {
                    double age = (frame - burst * 7) / 36.0;
                    if (age <= 0 || age >= 2) continue;
                    float cx = 90 + burst * 170, cy = burst == 1 ? 58 : 85;
                    int alpha = Math.Max(0, (int)(220 * (1 - age / 2)));
                    using (Pen particle = new Pen(Color.FromArgb(alpha, colors[burst]), 2F)) {
                        for (int i = 0; i < 22; i++) {
                            double angle = i * Math.PI * 2 / 22 + burst;
                            float radius = (float)(age * 57), fall = (float)(age * age * 12);
                            float x = cx + (float)Math.Cos(angle) * radius, y = cy + (float)Math.Sin(angle) * radius + fall;
                            g.DrawLine(particle, x, y, x - (float)Math.Cos(angle) * 6, y - (float)Math.Sin(angle) * 6);
                        }
                    }
                }
            }
            using (Font small = new Font("Segoe UI", 9F, FontStyle.Bold))
            using (Font title = new Font("Microsoft YaHei UI", 21F, FontStyle.Bold)) {
                g.DrawString("CODEX  /  QUOTA WATCH", small, Brushes.LightSteelBlue, 25, 21);
                g.DrawString(celebrate ? "发现新的重置线索" : "有一条新线索待核对", title, Brushes.White, 23, 65);
            }
        }
    }

    internal sealed class AlertWindow : Form
    {
        readonly Fireworks art;
        public event Action<HistoryItem> ReviewSaved;
        public AlertWindow(DesktopAlert alert) : this(alert, null) { }
        public AlertWindow(DesktopAlert alert, IHistoryRepository history)
        {
            bool reviewable = !alert.Demo && history != null;
            int shift = reviewable ? -34 : 0;
            Text = alert.Demo ? "提醒效果预览（演示）" : AppBrand.Name + " · 新线索";
            ClientSize = new Size(558, reviewable ? 684 : 590); AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.White; TopMost = true; StartPosition = FormStartPosition.CenterScreen;
            Icon = AppBrand.Icon; Font = new Font("Microsoft YaHei UI", 9F);
            art = new Fireworks(alert.Celebrate) { Left = 0, Top = 0, Width = 558, Height = 140 + shift }; Controls.Add(art);
            Palette.Text(this, (alert.Demo ? "演示 · " : "") + alert.Title, 24, 160 + shift, 510, 31, 16, Palette.Blue, true);
            string when = ""; DateTimeOffset stamp;
            if (DateTimeOffset.TryParse(alert.CreatedAt, out stamp)) when = "   ·   " + stamp.ToLocalTime().ToString("MM-dd HH:mm");
            Palette.Text(this, "@" + alert.Username + when, 26, 204 + shift, 506, 24, 10, Palette.Muted, false);
            TextBox body = new TextBox { Left = 25, Top = 242 + shift, Width = 508, Height = 164, Text = alert.Text.Replace("\n", "\r\n"),
                Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.None,
                BackColor = Palette.Canvas, ForeColor = Palette.Ink, Font = new Font("Microsoft YaHei UI", 10F) };
            Controls.Add(body);
            Palette.Text(this, alert.Reason, 25, 419 + shift, 508, 47, 9, Palette.Muted, false);
            Palette.Text(this, alert.Demo ? "仅演示效果，不会发送邮件或改变监控记录。" : "这是候选线索，不代表你的账户额度已恢复。\n请核对原帖、订阅范围和账户状态；邮件投递结果以日志为准。",
                25, 468 + shift, 508, 43, 9, Palette.Amber, false);
            if (reviewable) {
                ReviewPanel review = new ReviewPanel(history) { Left = 25, Top = 484, Width = 508, Height = 126 };
                Controls.Add(review);
                review.Saved += delegate(HistoryItem item) { Action<HistoryItem> handler = ReviewSaved; if (handler != null) handler(item); };
                Shown += async delegate { await review.LoadAsync(alert.Id); };
            }
            Button open = Palette.Button(this, "查看原帖 ↗", 25, reviewable ? 624 : 529, 310);
            open.BackColor = Palette.Blue; open.ForeColor = Color.White;
            open.Enabled = !alert.Demo && DesktopAlert.SafeUrl(alert.Url);
            if (!open.Enabled) { open.BackColor = Palette.Canvas; open.ForeColor = Palette.Muted; }
            open.Click += delegate {
                try { if (DesktopAlert.SafeUrl(alert.Url)) Process.Start(new ProcessStartInfo(alert.Url) { UseShellExecute = true }); }
                catch (Exception) { MessageBox.Show("无法打开浏览器，请从日志查看原帖链接。", "打开失败"); }
            };
            Button close = Palette.Button(this, reviewable ? "稍后再看 / 关闭" : "知道了", 348, reviewable ? 624 : 529, 185); close.Click += delegate { Close(); };
            AcceptButton = close; CancelButton = close;
            Shown += delegate { body.SelectionLength = 0; ActiveControl = close; };
        }
        public void PreviewFrame() { art.PreviewFrame(); }
    }
}
