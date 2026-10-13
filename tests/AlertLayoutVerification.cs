using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace CodexQuotaDesktop
{
    internal static class AlertLayoutVerification
    {
        sealed class ReviewFixture : IHistoryRepository
        {
            public HistoryPage List(string label, int offset, int limit) { throw new NotSupportedException(); }
            public HistoryItem Get(string id) { return new HistoryItem { id = id, kind = "uncertain", label = "ambiguous" }; }
            public HistoryItem Set(string id, string label) { return new HistoryItem { id = id, kind = "uncertain", label = label }; }
        }
        static void Check(bool value, string message) { if (!value) throw new Exception(message); }
        static T Find<T>(Control parent, string name) where T : Control
        { return parent.Controls.Find(name, true).OfType<T>().SingleOrDefault(); }
        static object Field(object value, string name)
        { var field = value.GetType().GetField(name); return field == null ? null : field.GetValue(value); }
        static void Handles(Control control)
        { IntPtr handle = control.Handle; foreach (Control child in control.Controls) Handles(child); }
        static void Snapshot(Form form, string path)
        {
            form.TopMost = false; form.ShowInTaskbar = false; form.StartPosition = FormStartPosition.Manual;
            form.Location = new Point(-16000, -16000); Handles(form); form.PerformLayout();
            using (Bitmap bitmap = new Bitmap(form.Width, form.Height)) {
                form.DrawToBitmap(bitmap, new Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(path, ImageFormat.Png);
            }
        }
        static DesktopAlert Alert(string text, string original, Dictionary<string, object> assessment)
        {
            DashboardState state = new DashboardState(); state.Started();
            Dictionary<string, object> wire = new Dictionary<string, object> {
                { "type", "alert" }, { "id", "fixture:short-original" }, { "kind", "uncertain" },
                { "title", "official completed" }, { "username", "demo_updates" }, { "text", text },
                { "url", "https://x.com/demo_updates/status/2097043464538264003" },
                { "created_at", "2026-10-13T00:10:00Z" }, { "collected_at", "2026-10-13T00:30:00Z" },
                { "reason", "简短回复中的对象和范围无法独立确定，请结合原帖核对。" }
            };
            if (original != null) wire["original_text"] = original;
            if (assessment != null) wire["source_assessment"] = assessment;
            wire["context_text"] = "对话上文候选：\n用户：Does this cover all subscriptions?\n回复关系未经官方接口核实。";
            Check(state.Apply("@quota-ui " + new JavaScriptSerializer().Serialize(wire)), "Structured event was rejected");
            Check(state.LastAlert.Kind == "uncertain" && !state.LastAlert.Celebrate, "Third-party completed stage promoted machine classification/celebration");
            Check(state.LastAlert.Title == "待核实线索", "Untrusted wire title displaced machine title");
            return state.LastAlert;
        }
        [STAThread]
        public static int Main(string[] args)
        {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            try {
                string original = "Reset all propagated. Sweet dreams.";
                string longBasis = String.Concat(Enumerable.Repeat("事件判断：第三方根据上下文推断；分析来源：自动归纳；范围来源：历史记录；站点合并标记：已完成。", 16));
                var assessment = new Dictionary<string, object> {
                    { "provider", "divin.cc" }, { "source_url", "https://divin.cc/" }, { "event_type", "usage_reset" },
                    { "scope", "所有付费用户" }, { "reason", "服务中断补偿" }, { "stage", "completed" },
                    { "analysis_basis", longBasis }, { "fetched_at", "2026-10-13T00:25:00Z" },
                    { "source_urls", new [] { "https://x.com/demo/status/123", "file:///C:/private", "https://divin.cc.attacker.test/" } }
                };
                string full = "事件补充（divin.cc 第三方整理，未作为官方确认或自动分类依据）：\n第三方事件类型：额度重置\n第三方覆盖范围：所有付费用户\n第三方重置原因：服务中断补偿\n第三方事件阶段：已完成\n第三方判断依据：" + longBasis + "\n第三方参考来源：https://divin.cc/\n\n" + original;
                DesktopAlert dto = Alert(full, original, assessment);
                Check(Convert.ToString(Field(dto, "OriginalText")) == original, "Structured original_text was discarded");
                Check(Convert.ToString(Field(dto, "CollectedAt")) == "2026-10-13T00:30:00Z", "Collection time was discarded");
                using (AlertWindow window = new AlertWindow(dto, new ReviewFixture())) {
                    TextBox body = Find<TextBox>(window, "alert-original");
                    Check(body != null && body.Text == original, "The author original is not the primary body");
                    Check(body.Height >= 230 && body.Font.Size >= 11, "Original text remains cramped");
                    Label summary = Find<Label>(window, "alert-third-party-summary");
                    Check(summary != null && summary.Top > body.Bottom && !summary.Text.Contains(longBasis) && summary.Text.Length < 220, "Third-party summary displaced/flooded original text");
                    Control detail = Find<Control>(window, "alert-details-panel");
                    Check(detail != null && !detail.Visible && detail.Height == 0, "Long third-party details should be collapsed initially");
                    Label when = Find<Label>(window, "alert-time");
                    Check(when != null && when.Text.Contains("08:10") && when.Text.Contains("08:30") && when.Text.Contains("北京时间"), "Header did not explicitly convert both timestamps to Beijing time");
                    ReviewPanel review = window.Controls.OfType<ReviewPanel>().Single();
                    review.Bind(new ReviewFixture().Get(dto.Id));
                    Check(review.Top > body.Parent.Top + body.Bottom && review.Bottom < Find<Button>(window, "alert-original-link").Top, "Manual review is not kept below the original and above footer actions");
                    Check(Find<Button>(review, "review-confirmed").ForeColor == Palette.Green && Find<Button>(review, "review-ambiguous").BackColor == Palette.Amber, "Manual labels lost their independent colors");
                    Snapshot(window, Path.Combine(args[0], "data", "desktop", "alert-structured-preview.png"));
                    typeof(Button).GetMethod("OnClick", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                        .Invoke(Find<Button>(window, "alert-details-toggle"), new object[] { EventArgs.Empty });
                    TextBox details = Find<TextBox>(window, "alert-details-text");
                    Check(details != null && details.Text.Contains(longBasis) && details.Text.Contains("用户：Does"), "Collapsed evidence/context became inaccessible");
                    Check(detail.Height > 100, "Details button did not expand details");
                    Check(detail.Top >= 0 && detail.Bottom <= detail.Parent.ClientSize.Height, "Expanded details were not scrolled into the visible content area: top=" + detail.Top + ", bottom=" + detail.Bottom + ", parentHeight=" + detail.Parent.ClientSize.Height + ", scroll=" + ((Panel)detail.Parent).AutoScrollPosition);
                    Check(Find<Button>(window, "alert-source-link") != null, "Safe divin source link is missing");
                    Snapshot(window, Path.Combine(args[0], "data", "desktop", "alert-details-preview.png"));
                    typeof(Button).GetMethod("OnClick", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                        .Invoke(Find<Button>(window, "alert-details-toggle"), new object[] { EventArgs.Empty });
                    window.Scale(new SizeF(1.25F, 1.25F)); window.PerformLayout();
                    Check(body.Bottom < summary.Top && review.Bottom < Find<Button>(window, "alert-original-link").Top, "125% scale overlapped original/summary/review controls");
                    Snapshot(window, Path.Combine(args[0], "data", "desktop", "alert-structured-preview-125.png"));
                    window.Size = window.MinimumSize; window.PerformLayout();
                    Check(Find<Button>(window, "alert-original-link").Right < Find<Button>(window, "alert-close").Left, "Small window footer actions overlap");
                    Check(review.Bottom <= window.ClientSize.Height && review.Top >= 70, "Small window loses manual review/footer");
                    Snapshot(window, Path.Combine(args[0], "data", "desktop", "alert-structured-preview-small.png"));
                }
                using (AlertWindow legacy = new AlertWindow(Alert(full, null, null))) {
                    Check(Find<TextBox>(legacy, "alert-original").Text == original, "Known generated legacy prefix was not separated");
                    Check(Find<TextBox>(legacy, "alert-details-text").Text.Contains(longBasis), "Legacy prefix details were lost");
                }
                string authored = "事件补充（divin.cc 第三方整理，未作为官方确认或自动分类依据）：\n这是作者谈到的一句话，并非生成的元数据。\n\n" + original;
                using (AlertWindow legacy = new AlertWindow(Alert(authored, null, null))) {
                    Check(Find<TextBox>(legacy, "alert-original").Text == authored.Replace("\n", "\r\n"), "Author content was misparsed as generated metadata");
                }
                string emptyPrefix = "事件补充（divin.cc 第三方整理，未作为官方确认或自动分类依据）：\n\n" + original;
                using (AlertWindow legacy = new AlertWindow(Alert(emptyPrefix, null, null))) {
                    Check(Find<TextBox>(legacy, "alert-original").Text == emptyPrefix.Replace("\n", "\r\n"), "An empty metadata-looking author heading was stripped");
                }
                using (AlertWindow structured = new AlertWindow(Alert(full, full, null))) {
                    Check(Find<TextBox>(structured, "alert-original").Text == full.Replace("\n", "\r\n"), "Structured author text was incorrectly parsed as legacy metadata");
                }
                assessment["source_url"] = "https://divin.cc.attacker.test/";
                using (AlertWindow unsafeSource = new AlertWindow(Alert(original, original, assessment))) {
                    Check(Find<Button>(unsafeSource, "alert-source-link") == null, "Foreign third-party URL is clickable");
                }
                Console.WriteLine("PASS alert original priority, wire separation, Beijing timestamps, collapsed evidence/context, legacy fidelity and untrusted source safety; offscreen previews rendered.");
                return 0;
            } catch (Exception ex) { Console.Error.WriteLine("FAIL " + ex); return 1; }
        }
    }
}
