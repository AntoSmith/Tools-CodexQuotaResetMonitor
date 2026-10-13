using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
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
            using (Font title = new Font("Microsoft YaHei UI", 16F, FontStyle.Bold)) {
                g.DrawString("CODEX  /  QUOTA WATCH", small, Brushes.LightSteelBlue, 25, 9);
                g.DrawString(celebrate ? "发现新的重置线索" : "有一条新线索待核对", title, Brushes.White, 23, 32);
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
            Text = alert.Demo ? "提醒效果预览（演示）" : AppBrand.Name + " · 新线索";
            ClientSize = new Size(680, reviewable ? 800 : 684); AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            MinimumSize = new Size(600, 600); FormBorderStyle = FormBorderStyle.Sizable; MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.White; TopMost = true; StartPosition = FormStartPosition.CenterScreen;
            Icon = AppBrand.Icon; Font = new Font("Microsoft YaHei UI", 9F);
            art = new Fireworks(alert.Celebrate) { Left = 0, Top = 0, Width = 680, Height = 70, Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right }; Controls.Add(art);
            int footerHeight = reviewable ? 186 : 60;
            Panel content = new Panel { Left = 0, Top = 70, Width = 680, Height = ClientSize.Height - 70 - footerHeight,
                AutoScroll = true, Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right };
            Controls.Add(content);
            const int x = 24, width = 632;
            Label badge = Palette.Text(content, (alert.Demo ? "演示 · " : "自动筛选 · ") + HistoryDisplay.Kind(alert.Kind), x, 11, width, 32, 14, Palette.Blue, true);
            badge.Name = "alert-machine-kind"; badge.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Label author = Palette.Text(content, "@" + alert.Username + "  ·  原帖作者", x, 46, width, 24, 10, Palette.Ink, true);
            author.Name = "alert-author"; author.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Label when = Palette.Text(content, "发布 " + BeijingTime(alert.CreatedAt) + "   ·   采集 " + BeijingTime(alert.CollectedAt) + "（北京时间）",
                x, 73, width, 23, 9, Palette.Muted, false); when.Name = "alert-time"; when.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            Label originalLabel = Palette.Text(content, "作者原文", x, 104, width, 22, 10, Palette.Ink, true);
            originalLabel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            string legacyPrefix;
            SourceAssessment legacyAssessment;
            string original = alert.OriginalText;
            if (original == null) original = SplitLegacyPrefix(alert.Text ?? "", out legacyPrefix, out legacyAssessment);
            else { legacyPrefix = ""; legacyAssessment = null; }
            SourceAssessment assessment = alert.Assessment != null && alert.Assessment.IsDivin ? alert.Assessment : legacyAssessment;
            TextBox body = new TextBox { Name = "alert-original", Left = x, Top = 130, Width = width, Height = 230, Text = Lines(original),
                Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.None,
                BackColor = Palette.Canvas, ForeColor = Palette.Ink, Font = new Font("Microsoft YaHei UI", 12F),
                Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
            content.Controls.Add(body);
            int toggleTop = 372;
            Label summary = null;
            if (assessment != null) {
                summary = Palette.Text(content, Summary(assessment), x, 370, width, 70, 9, Palette.Muted, false);
                summary.Name = "alert-third-party-summary"; summary.AutoEllipsis = true; summary.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
                toggleTop = 450;
            }
            Panel details = new Panel { Name = "alert-details-panel", Left = x, Top = toggleTop + 42, Width = width, Height = 0,
                Visible = false, Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
            TextBox detailText = new TextBox { Name = "alert-details-text", Dock = DockStyle.Fill, Multiline = true, ReadOnly = true,
                ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.FixedSingle, BackColor = Palette.Canvas, ForeColor = Palette.Muted,
                Font = new Font("Microsoft YaHei UI", 9.5F), Text = Lines(Details(alert, assessment, legacyPrefix)) };
            details.Controls.Add(detailText); content.Controls.Add(details);
            Button toggle = Palette.Button(content, "展开分析 / 证据 / 对话上文", x, toggleTop, assessment == null ? width : 426);
            toggle.Name = "alert-details-toggle"; toggle.Height = 34; toggle.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            if (assessment != null) {
                Button source = Palette.Button(content, "divin.cc 来源 ↗", 464, toggleTop, 192); source.Name = "alert-source-link"; source.Height = 34;
                source.Anchor = AnchorStyles.Top | AnchorStyles.Right;
                source.Click += delegate { OpenUrl("https://divin.cc/", true); };
            }
            Label disclaimer = Palette.Text(content, alert.Demo ? "合成数据仅用于预览。不会发送邮件或改变监控记录。" :
                "这是候选线索。请核对原帖、订阅范围与账户状态；账户额度恢复须自行确认。", x, toggleTop + 45, width, 40, 9, Palette.Amber, false);
            disclaimer.Name = "alert-disclaimer"; disclaimer.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            bool expanded = false; int originalHeight = body.Height;
            toggle.Click += delegate {
                content.AutoScrollPosition = Point.Empty;
                if (!expanded) originalHeight = body.Height;
                int gap = Math.Max(8, (int)Math.Round(originalHeight / 23F));
                expanded = !expanded;
                body.Height = expanded ? (int)Math.Round(originalHeight * .35F) : originalHeight;
                if (summary != null) summary.Top = body.Bottom + gap;
                toggle.Top = summary == null ? body.Bottom + gap + 2 : summary.Bottom + gap;
                Button source = FindSource(content); if (source != null) source.Top = toggle.Top;
                details.Top = toggle.Bottom + gap;
                details.Visible = expanded;
                details.Height = expanded ? Math.Max(100, Math.Min(originalHeight, content.ClientSize.Height - details.Top - disclaimer.Height - gap * 2)) : 0;
                disclaimer.Top = details.Bottom + (expanded ? gap : 3);
                toggle.Text = expanded ? "收起分析 / 证据 / 对话上文" : "展开分析 / 证据 / 对话上文";
                content.AutoScrollMinSize = new Size(0, disclaimer.Bottom + gap);
                content.PerformLayout();
                if (expanded) content.ScrollControlIntoView(details);
            };
            if (reviewable) {
                ReviewPanel review = new ReviewPanel(history) { Left = x, Top = ClientSize.Height - footerHeight, Width = width, Height = 126,
                    Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right };
                Controls.Add(review);
                review.Saved += delegate(HistoryItem item) { Action<HistoryItem> handler = ReviewSaved; if (handler != null) handler(item); };
                Shown += async delegate { await review.LoadAsync(alert.Id); };
            }
            Button open = Palette.Button(this, "查看 X 原帖 ↗", x, ClientSize.Height - 48, 390);
            open.Name = "alert-original-link"; open.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            open.BackColor = Palette.Blue; open.ForeColor = Color.White;
            open.Enabled = !alert.Demo && DesktopAlert.SafeUrl(alert.Url);
            if (!open.Enabled) { open.BackColor = Palette.Canvas; open.ForeColor = Palette.Muted; }
            open.Click += delegate { OpenUrl(alert.Url, false); };
            Button close = Palette.Button(this, reviewable ? "稍后再看 / 关闭" : "知道了", 428, ClientSize.Height - 48, 228);
            close.Name = "alert-close";
            close.Anchor = AnchorStyles.Bottom | AnchorStyles.Right; close.Click += delegate { Close(); };
            AcceptButton = close; CancelButton = close;
            Shown += delegate { body.SelectionLength = 0; ActiveControl = close; };
        }
        public void PreviewFrame() { art.PreviewFrame(); }
        static Button FindSource(Control parent)
        { foreach (Control control in parent.Controls) if (control.Name == "alert-source-link") return control as Button; return null; }
        static string Lines(string value) { return (value ?? "").Replace("\r\n", "\n").Replace("\r", "\n").Replace("\n", "\r\n"); }
        static string BeijingTime(string value)
        {
            DateTimeOffset stamp;
            return DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out stamp)
                ? stamp.ToOffset(TimeSpan.FromHours(8)).ToString("MM-dd HH:mm", CultureInfo.InvariantCulture) : "时间未提供";
        }
        static string Brief(string value, int limit)
        {
            string clean = Regex.Replace(value ?? "", @"\s+", " ").Trim();
            return clean.Length <= limit ? clean : clean.Substring(0, limit - 1) + "…";
        }
        static string EventType(string value)
        {
            switch (value) { case "usage_reset": return "额度重置"; case "reset_card": return "重置卡发放"; case "credit_grant": return "额外额度发放"; default: return value; }
        }
        static string Stage(string value)
        {
            switch (value) { case "scheduled": return "预告"; case "rolling_out": return "推送中"; case "completed": return "已完成"; default: return value; }
        }
        static string Summary(SourceAssessment value)
        {
            return "divin.cc 第三方整理 · 待核对\n类型：" + Brief(EventType(value.event_type), 28) + "   范围：" + Brief(value.scope, 48)
                + "\n原因：" + Brief(value.reason, 72) + "  ·  完整分析见下方";
        }
        static string Details(DesktopAlert alert, SourceAssessment assessment, string legacyPrefix)
        {
            StringBuilder text = new StringBuilder("自动筛选依据（仅用于发现线索）：\n" + alert.Reason);
            if (assessment != null) {
                text.Append("\n\n第三方补充（divin.cc，不代表官方确认）：\n类型：").Append(EventType(assessment.event_type))
                    .Append("\n范围：").Append(assessment.scope).Append("\n原因：").Append(assessment.reason)
                    .Append("\n第三方阶段：").Append(Stage(assessment.stage)).Append("\n分析依据：").Append(assessment.analysis_basis)
                    .Append("\n第三方采集时间：").Append(BeijingTime(assessment.fetched_at)).Append("（北京时间）\n参考来源：https://divin.cc/");
                if (assessment.source_urls != null) foreach (string url in assessment.source_urls)
                    if (DesktopAlert.SafeUrl(url)) text.Append("\n证据链接：").Append(url);
            }
            if (!String.IsNullOrEmpty(alert.ContextText)) text.Append("\n\n").Append(alert.ContextText.Trim());
            if (!String.IsNullOrEmpty(legacyPrefix)) text.Append("\n\n兼容记录中的第三方补充：\n").Append(legacyPrefix.Trim());
            if (alert.OriginalText != null && !String.IsNullOrEmpty(alert.Text) && alert.Text != alert.OriginalText)
                text.Append("\n\n完整捕获记录（保留来源说明与原始格式）：\n").Append(alert.Text);
            return text.ToString();
        }
        static string SplitLegacyPrefix(string text, out string prefix, out SourceAssessment assessment)
        {
            prefix = ""; assessment = null;
            string normalized = text.Replace("\r\n", "\n");
            const string header = "事件补充（divin.cc 第三方整理，未作为官方确认或自动分类依据）：\n";
            if (!normalized.StartsWith(header, StringComparison.Ordinal)) return text;
            int split = normalized.IndexOf("\n\n", StringComparison.Ordinal);
            if (split < header.Length || split > 5000) return text;
            string[] labels = { "第三方事件类型", "第三方覆盖范围", "第三方重置原因", "第三方事件阶段", "第三方判断依据", "第三方采集时间", "第三方参考来源", "第三方证据链接" };
            string[] values = new string[labels.Length]; int previous = -1;
            foreach (string line in normalized.Substring(header.Length, split - header.Length).Split('\n')) {
                int colon = line.IndexOf('：'); if (colon <= 0 || colon == line.Length - 1) return text;
                int index = Array.IndexOf(labels, line.Substring(0, colon));
                if (index <= previous) return text; previous = index; values[index] = line.Substring(colon + 1);
                if (index == 5 && !Regex.IsMatch(values[index], @"\A\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}（北京时间）\z")) return text;
                if (index == 7) foreach (string link in values[index].Split(' ')) if (!DesktopAlert.SafeUrl(link)) return text;
            }
            if (values[1] == null || values[2] == null || values[6] != "https://divin.cc/") return text;
            assessment = new SourceAssessment { provider = "divin.cc", source_url = values[6], event_type = values[0], scope = values[1], reason = values[2],
                stage = values[3], analysis_basis = values[4], source_urls = values[7] == null ? null : values[7].Split(' ') };
            prefix = normalized.Substring(0, split); return normalized.Substring(split + 2);
        }
        static void OpenUrl(string value, bool source)
        {
            try {
                if (source ? value == "https://divin.cc/" : DesktopAlert.SafeUrl(value)) Process.Start(new ProcessStartInfo(value) { UseShellExecute = true });
            } catch (Exception) { MessageBox.Show("无法打开浏览器，请从详情中复制链接。", "打开失败"); }
        }
    }
}
