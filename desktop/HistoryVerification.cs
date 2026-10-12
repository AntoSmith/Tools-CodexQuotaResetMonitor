using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal static class HistoryVerification
    {
        static void Check(bool value, string message) { if (!value) throw new Exception(message); }
        static T Find<T>(Control parent, string name) where T : Control
        {
            return parent.Controls.Find(name, true).OfType<T>().Single();
        }
        static void Until(Func<bool> condition, string description)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(5);
            while (!condition() && DateTime.UtcNow < deadline) { Events(); Thread.Sleep(10); }
            Events();
            Check(condition(), "Timed out: " + description);
        }
        static void Events()
        {
            // DoEvents may restore a base context when no outer Application.Run loop exists.
            Application.DoEvents();
            if (!(SynchronizationContext.Current is WindowsFormsSynchronizationContext))
                SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
        }
        static void Pump(Task task)
        {
            Until(delegate { return task.IsCompleted; }, "asynchronous operation");
            task.GetAwaiter().GetResult(); Events();
        }
        sealed class Repository : IHistoryRepository
        {
            public readonly List<HistoryItem> Rows = new List<HistoryItem>();
            public bool FailSave;
            public int Reads, Writes;
            public ManualResetEvent SaveEntered, SaveRelease;
            public Repository()
            {
                string[] kinds = { "completed", "uncertain", "credit", "limits", "upcoming", "correction" };
                string[] titles = { "已重置候选", "待核实线索", "重置券线索", "额度变化线索", "即将重置候选", "延期 / 撤回线索" };
                string[] authors = { "demo_updates", "demo_reply", "demo_credit", "demo_limits", "demo_schedule", "demo_notice" };
                string[] replies = {
                    "Codex usage has been reset for the eligible accounts.",
                    "Okay, it is still coming on Tuesday. More details will follow.",
                    "An extra reset credit is now available to eligible Codex subscribers.",
                    "We have adjusted Codex usage limits for some subscription plans.",
                    "We plan to reset Codex usage after the maintenance window.",
                    "The previously discussed Codex reset has been postponed."
                };
                string[] questions = {
                    "Has the Codex usage reset finished for the affected subscriptions?",
                    "Are we getting a usage reset with the next update?",
                    "Will there be a reset credit that we can redeem ourselves?",
                    "Are these new usage limits a reset or a change to the plan?",
                    "When should we expect the next Codex usage reset?",
                    "Is the reset still scheduled for the original time?"
                };
                string[] reasons = {
                    "作者文字声明已重置；仍需核对适用订阅与实际账户状态。",
                    "对话上文涉及重置，回复仅给出星期信息，范围和时间尚不明确。",
                    "文字提及可兑换权益，不等同于所有账户自动重置。",
                    "文字描述额度变化，未据此认定已执行一次重置。",
                    "文字描述未来计划，未给出明确时区，不生成确定倒计时。",
                    "文字提及原计划延期，需结合前文判断所指事件。"
                };
                for (int i = 0; i < 53; i++) {
                    int sample = i % kinds.Length;
                    string body = "【合成对话示例 · 非真实公告】\r\n\r\n"
                        + "作者回复（@" + authors[sample] + "）：\r\n" + replies[sample] + "\r\n\r\n"
                        + "对话上文（@demo_user）：\r\n" + questions[sample] + "\r\n\r\n"
                        + "自动分类：" + titles[sample] + "\r\n判断依据：" + reasons[sample] + "\r\n\r\n"
                        + "上下文说明：这里展示可读取的上文候选。网页顺序或父帖链接只能提供关联线索，"
                        + "不表示已经核实直接回复关系；用户的提问也不等同于作者的承诺。\r\n\r\n"
                        + "保留完整内容：历史窗口可以回看作者正文、发现时间和关联上文，"
                        + "帮助判断这条消息是在预告、解释、延期，还是讨论一次已经完成的重置。"
                        + "如果缺少关键句子，应继续查看原帖，不用一句简短回应推断所有订阅的额度都会恢复。\r\n\r\n"
                        + "适用范围：即使文字提到重置，也需要核对具体产品、订阅类型、账户条件和生效时间。"
                        + "界面中的庆祝效果只用于展示提醒样式，不能替代实际账户状态的确认。\r\n\r\n"
                        + "复核说明：人工标记用于历史整理，可改为确定、模糊或不确定，也可以清除。"
                        + "它与自动分类分别保存，不改变邮件的投递规则。\r\n\r\n"
                        + "示例编号：" + (i + 1).ToString("00") + " / 53。本文和用户名均为合成数据，来源按钮仅演示界面。";
                    Rows.Add(new HistoryItem {
                        id = "fixture:" + i, post_id = i.ToString(), kind = kinds[sample],
                        title = titles[sample], username = authors[sample],
                        text = "合成示例：" + replies[sample], body = body,
                        url = "https://x.com/example/status/123", created_at = "2026-09-30T08:00:00+00:00",
                        detected_at = "2026-09-30T16:10:00+08:00", label = i == 1 ? "ambiguous" : "unreviewed", reviewed_at = ""
                    });
                }
            }
            static HistoryItem Copy(HistoryItem value) { return value.Copy(); }
            public HistoryPage List(string label, int offset, int limit)
            {
                lock (Rows) {
                    var selected = Rows.Where(row => label == "all" || row.label == label).ToList();
                    var counts = HistoryDisplay.Labels.ToDictionary(key => key, key => Rows.Count(row => key == "all" || row.label == key));
                    return new HistoryPage { items = selected.Skip(offset).Take(limit).Select(Copy).ToList(), total = selected.Count,
                        offset = offset, limit = limit, counts = counts };
                }
            }
            public HistoryItem Get(string id) { Interlocked.Increment(ref Reads); lock (Rows) return Copy(Rows.Single(row => row.id == id)); }
            public HistoryItem Set(string id, string label)
            {
                Interlocked.Increment(ref Writes);
                if (SaveEntered != null) SaveEntered.Set();
                if (SaveRelease != null && !SaveRelease.WaitOne(5000)) throw new InvalidOperationException("测试保存超时");
                if (FailSave) throw new InvalidOperationException("模拟写入失败");
                lock (Rows) {
                    HistoryItem row = Rows.Single(value => value.id == id); row.label = label;
                    row.reviewed_at = label == "unreviewed" ? "" : "2026-09-30T16:20:00+08:00"; return Copy(row);
                }
            }
        }
        static void Save(ReviewPanel panel, string label)
        {
            Button button = Find<Button>(panel, "review-" + label);
            Check(button.Enabled && button.Visible && button.CanSelect, "Review button is unavailable: " + label + " " + button.Bounds + " enabled=" + button.Enabled + " visible=" + button.Visible + " selectable=" + button.CanSelect + " context=" + (SynchronizationContext.Current == null ? "null" : SynchronizationContext.Current.GetType().Name));
            button.PerformClick();
            Until(delegate { return Find<Label>(panel, "review-status").Text != "正在保存…"; }, "review save");
        }
        public static int Run(string root)
        {
            string output = Path.Combine(root, "data", "desktop"); Directory.CreateDirectory(output);
            try {
                Control.CheckForIllegalCrossThreadCalls = true;
                SynchronizationContext.SetSynchronizationContext(new WindowsFormsSynchronizationContext());
                Check(HistoryDisplay.BeijingTime("2026-09-30T08:00:00+00:00") == "2026-09-30 16:00", "History time must use Beijing timezone");
                Check(!HistoryDisplay.SafeUrl("file:///C:/Windows/notepad.exe") && !HistoryDisplay.SafeUrl("https://x.com.evil.test/post"), "Unsafe URL accepted");
                Repository repo = new Repository();
                using (HistoryWindow window = new HistoryWindow(repo)) {
                    window.ShowInTaskbar = false; window.StartPosition = FormStartPosition.Manual; window.Location = new Point(-16000, -16000); window.Show();
                    DataGridView grid = Find<DataGridView>(window, "history-grid");
                    ComboBox filter = Find<ComboBox>(window, "history-filter");
                    ReviewPanel review = Find<ReviewPanel>(window, "history-review");
                    Until(delegate { return grid.Rows.Count == 50 && Find<Button>(review, "review-confirmed").Enabled; }, "initial history page");
                    Check(Find<TextBox>(window, "history-body").Text.Contains("复核说明：人工标记用于历史整理"), "Full body not displayed");
                    Check(review.Right <= review.Parent.ClientSize.Width - review.Parent.Padding.Right, "History review panel exceeds detail column bounds");
                    Check(filter.SelectedIndex == 0 && filter.Items[0].ToString().Contains("53"), "Default all filter/count missing");
                    Find<Button>(window, "history-next").PerformClick();
                    Until(delegate { return grid.Rows.Count == 3; }, "second page");
                    filter.SelectedIndex = 3;
                    Until(delegate { return grid.Rows.Count == 1 && Find<Button>(review, "review-confirmed").Enabled; }, "ambiguous filter");
                    Check(Convert.ToString(grid.Rows[0].Tag) != "", "History row identifier missing");
                    Save(review, "confirmed");
                    Until(delegate { return grid.Rows.Count == 0; }, "saved item removed from old filter");
                    filter.SelectedIndex = 2;
                    Until(delegate { return grid.Rows.Count == 1 && Find<Button>(review, "review-confirmed").Enabled; }, "confirmed filter");
                    Check(Find<Label>(review, "review-state").Text.Contains("确定"), "Confirmed status absent");
                    repo.FailSave = true; Save(review, "uncertain");
                    Check(Find<Label>(review, "review-status").Text.Contains("未确认") && Find<Label>(review, "review-status").Text.Contains("模拟写入失败") && repo.Get("fixture:1").label == "confirmed", "Failure was presented as saved: " + Find<Label>(review, "review-status").Text + " / " + repo.Get("fixture:1").label);
                    repo.FailSave = false;
                    repo.Set("fixture:2", "ambiguous"); repo.Set("fixture:3", "uncertain");
                    filter.SelectedIndex = 0;
                    Until(delegate { return grid.Rows.Count == 50 && Find<Button>(review, "review-confirmed").Enabled; }, "all page reloaded");
                    using (Bitmap bitmap = new Bitmap(window.Width, window.Height)) {
                        window.DrawToBitmap(bitmap, new Rectangle(0, 0, window.Width, window.Height)); bitmap.Save(Path.Combine(output, "history-preview.png"), ImageFormat.Png);
                    }
                    window.Scale(new SizeF(1.25F, 1.25F)); Events();
                    using (Bitmap bitmap = new Bitmap(window.Width, window.Height)) {
                        window.DrawToBitmap(bitmap, new Rectangle(0, 0, window.Width, window.Height)); bitmap.Save(Path.Combine(output, "history-preview-125.png"), ImageFormat.Png);
                    }
                }
                repo.Set("fixture:2", "unreviewed");
                using (Form host = new Form { ClientSize = new Size(558, 170), ShowInTaskbar = false, StartPosition = FormStartPosition.Manual, Location = new Point(-16000, -16000) })
                using (ReviewPanel panel = new ReviewPanel(repo) { Bounds = new Rectangle(25, 20, 508, 126) }) {
                    host.Controls.Add(panel); host.Show(); panel.Bind(repo.Get("fixture:0")); Events();
                    Save(panel, "confirmed"); Check(repo.Get("fixture:0").label == "confirmed", "Confirmed save failed");
                    Save(panel, "ambiguous"); Check(repo.Get("fixture:0").label == "ambiguous", "Changing review failed");
                    Save(panel, "uncertain"); Check(repo.Get("fixture:0").label == "uncertain", "Uncertain save failed");
                    Save(panel, "unreviewed"); Check(repo.Get("fixture:0").label == "unreviewed", "Clearing review failed");
                    repo.SaveEntered = new ManualResetEvent(false); repo.SaveRelease = new ManualResetEvent(false);
                    Find<Button>(panel, "review-confirmed").PerformClick();
                    Until(delegate { return repo.SaveEntered.WaitOne(0); }, "delayed save started");
                    panel.Bind(repo.Get("fixture:2")); repo.SaveRelease.Set();
                    Until(delegate { return Find<Button>(panel, "review-confirmed").Enabled; }, "delayed save finished");
                    Check(Find<Label>(panel, "review-state").Text.Contains("未标记") && repo.Get("fixture:2").label == "unreviewed", "Stale save changed selected item visuals");
                    Pump(panel.LoadAsync("fixture:1"));
                    Check(Find<Label>(panel, "review-state").Text.Contains("确定"), "Review reload failed");
                    foreach (Button button in panel.Controls.Find("review-actions", true).Single().Controls.OfType<Button>())
                        Check(button.Bottom <= button.Parent.ClientSize.Height && button.Width >= 85, "Review action clipped at native DPI");
                    using (Bitmap bitmap = new Bitmap(host.Width, host.Height)) {
                        host.DrawToBitmap(bitmap, new Rectangle(0, 0, host.Width, host.Height)); bitmap.Save(Path.Combine(output, "history-review-preview.png"), ImageFormat.Png);
                    }
                    repo.SaveEntered.Dispose(); repo.SaveRelease.Dispose(); repo.SaveEntered = repo.SaveRelease = null;
                }
                int reads = repo.Reads, writes = repo.Writes;
                using (AlertWindow demo = new AlertWindow(DesktopAlert.Preview(), repo)) {
                    demo.TopMost = false; demo.ShowInTaskbar = false; demo.StartPosition = FormStartPosition.Manual; demo.Location = new Point(-16000, -16000); demo.Show(); Events();
                    Check(demo.Controls.OfType<ReviewPanel>().Count() == 0 && repo.Reads == reads && repo.Writes == writes, "Demo alert touched history");
                }
                DesktopAlert real = new DesktopAlert { Id = "fixture:0", Kind = "completed", Title = "已重置候选", Username = "demo_updates",
                    Text = "【合成对话示例 · 非真实公告】\r\n\r\n作者回复：\r\nCodex usage has been reset for the eligible accounts.\r\n\r\n"
                        + "对话上文（@demo_user）：\r\nHas the Codex usage reset finished for the affected subscriptions?\r\n\r\n"
                        + "这是提醒样式示例；需要结合原文范围与实际账户状态判断。",
                    Url = "https://x.com/example/status/123", CreatedAt = "2026-09-30T16:00:00+08:00", Reason = "合成数据：文字声明已重置；不代表真实公告或当前账户额度状态。" };
                using (AlertWindow alert = new AlertWindow(real, repo)) {
                    alert.TopMost = false; alert.ShowInTaskbar = false; alert.StartPosition = FormStartPosition.Manual; alert.Location = new Point(-16000, -16000); alert.Show(); alert.PreviewFrame();
                    ReviewPanel panel = alert.Controls.OfType<ReviewPanel>().Single();
                    Until(delegate { return Find<Button>(panel, "review-confirmed").Enabled; }, "real alert history load");
                    Check(panel.Bottom <= alert.ClientSize.Height, "Alert review panel exceeds client bounds");
                    using (Bitmap bitmap = new Bitmap(alert.Width, alert.Height)) {
                        alert.DrawToBitmap(bitmap, new Rectangle(0, 0, alert.Width, alert.Height)); bitmap.Save(Path.Combine(output, "history-alert-preview.png"), ImageFormat.Png);
                    }
                }
                File.WriteAllText(Path.Combine(output, "history-ui-test.txt"), "PASS history paging/filter/counts/full body, labels/change/clear, failure and stale-save handling; native previews rendered. No real history or mail touched."); return 0;
            } catch (Exception ex) { File.WriteAllText(Path.Combine(output, "history-ui-test.txt"), "FAIL " + ex); return 1; }
        }
#if HISTORY_UI_TEST
        [STAThread] static int Main(string[] args)
        {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            return Run(args[0]);
        }
#endif
    }
}
