using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace CodexQuotaDesktop
{
    public sealed class DesktopAlert
    {
        public string Id, Kind, Title, Username, Text, Url, Reason, CreatedAt;
        public bool Demo;
        public bool Celebrate { get { return Kind == "completed" || Kind == "upcoming" || Kind == "credit"; } }
        public static bool SafeUrl(string value)
        {
            return value != null && Regex.IsMatch(value, @"\Ahttps://x\.com/[A-Za-z0-9_]+/status/[0-9]+\z", RegexOptions.CultureInvariant);
        }
        public static DesktopAlert Preview()
        {
            return new DesktopAlert { Id = "demo", Kind = "completed", Title = "已重置候选", Username = "演示账号",
                Text = "这是一条合成的提醒，展示捕获额度重置线索后的弹窗效果。\n真实提醒会在这里显示作者的原文。",
                Reason = "仅预览界面，没有抓取推文、发送邮件或改变监控记录。", Demo = true };
        }
    }

    public sealed class WireEvent
    {
        public string type, outcome, error, id, kind, title, username, text, url, reason, created_at;
        public int round, posts;
        public double started_at, ended_at, next_at, interval_seconds;
    }

    public sealed class DashboardState
    {
        public bool Active { get; private set; }
        public string Phase { get; private set; }
        public string LastOutcome { get; private set; }
        public string Error { get; private set; }
        public int CurrentRound { get; private set; }
        public int RoundsCompleted { get; private set; }
        public int Failures { get; private set; }
        public int Posts { get; private set; }
        public double StartedAt { get; private set; }
        public double EndedAt { get; private set; }
        public double NextAt { get; private set; }
        public DesktopAlert LastAlert { get; private set; }
        public readonly Queue<DesktopAlert> Alerts = new Queue<DesktopAlert>();
        readonly HashSet<string> seen = new HashSet<string>();
        readonly JavaScriptSerializer json = new JavaScriptSerializer();
        public DashboardState() { Phase = "stopped"; LastOutcome = ""; Error = ""; }
        public void Started()
        {
            Active = true; Phase = "starting"; Error = ""; LastOutcome = "";
            CurrentRound = RoundsCompleted = Failures = Posts = 0;
            StartedAt = EndedAt = NextAt = 0; Alerts.Clear(); seen.Clear();
        }
        public void Stopped(string error)
        {
            if (Phase == "collecting") LastOutcome = "interrupted";
            Active = false; Phase = String.IsNullOrEmpty(error) ? "stopped" : "error";
            NextAt = 0; Error = error ?? "";
        }
        public bool Apply(string line)
        {
            if (!Active || line == null || !line.StartsWith("@quota-ui ", StringComparison.Ordinal)) return false;
            WireEvent e;
            try { e = json.Deserialize<WireEvent>(line.Substring(10)); }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
            catch (FormatException) { return false; }
            catch (OverflowException) { return false; }
            catch (Exception ex) {
                // Framework type converters wrap invalid numeric input in System.Exception.
                if (ex.InnerException is FormatException || ex.InnerException is OverflowException || ex.InnerException is ArgumentException) return false;
                throw;
            }
            if (e == null) return false;
            if (e.type == "round_start" && e.round == CurrentRound + 1 && Phase != "collecting" && ValidTime(e.started_at))
            {
                CurrentRound = e.round; StartedAt = e.started_at; EndedAt = NextAt = 0;
                Posts = 0; LastOutcome = ""; Error = "";
                Phase = "collecting"; return true;
            }
            if (e.type == "round_end" && e.round == CurrentRound && Phase == "collecting"
                && ValidTime(e.ended_at) && (ValidTime(e.next_at) || e.next_at == 0)
                && (e.outcome == "partial" || e.outcome == "empty" || e.outcome == "error"))
            {
                EndedAt = e.ended_at; NextAt = e.next_at; Posts = Math.Max(0, e.posts);
                LastOutcome = e.outcome == "partial" && Posts == 0 ? "empty" : e.outcome;
                if (LastOutcome != "partial") Failures++;
                RoundsCompleted++; Phase = "waiting"; Error = e.error ?? ""; return true;
            }
            if (e.type == "alert" && !String.IsNullOrEmpty(e.id) && e.id.Length < 200
                && DesktopAlert.SafeUrl(e.url) && SupportedKind(e.kind) && !seen.Contains(e.id))
            {
                DesktopAlert alert = new DesktopAlert { Id = e.id, Kind = e.kind, Title = Label(e.kind),
                    Username = e.username ?? "", Text = e.text ?? "", Url = e.url, Reason = e.reason ?? "", CreatedAt = e.created_at };
                seen.Add(e.id); LastAlert = alert; Alerts.Enqueue(alert); return true;
            }
            return false;
        }
        static bool ValidTime(double value) { return value > 0 && value < 253402300799 && !Double.IsNaN(value); }
        static bool SupportedKind(string kind)
        { return kind == "completed" || kind == "upcoming" || kind == "credit" || kind == "uncertain" || kind == "correction" || kind == "limits"; }
        static string Label(string kind)
        {
            switch (kind) {
                case "completed": return "已重置候选"; case "upcoming": return "即将重置候选";
                case "credit": return "重置券线索"; case "correction": return "延期 / 撤回线索";
                case "limits": return "额度变化线索"; default: return "待核实线索";
            }
        }
        public string ResultText
        {
            get {
                if (Phase == "collecting") return "第" + CurrentRound + "轮采集中，等待本轮结果";
                string round = "第" + CurrentRound + "轮 · ";
                if (LastOutcome == "partial") return round + "取得 " + Posts + " 条帖子 · 公开页面覆盖有限";
                if (LastOutcome == "empty") return round + "未取得可用帖子 · " + (String.IsNullOrWhiteSpace(Error) ? "页面可能受限" : Error);
                if (LastOutcome == "error") return round + "采集失败 · " + Error;
                if (LastOutcome == "interrupted") return "本轮已中断，未计入已结束轮数";
                return "等待首轮采集结果";
            }
        }
        public static string LocalTime(double value)
        { return value <= 0 ? "—" : new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddSeconds(value).ToLocalTime().ToString("MM-dd HH:mm:ss"); }
        public string Countdown
        {
            get {
                if (Phase == "collecting") return "采集中";
                if (!Active || NextAt <= 0) return "—";
                double seconds = NextAt - (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
                if (seconds <= 0) return "即将开始";
                return TimeSpan.FromSeconds(seconds).ToString(@"hh\:mm\:ss");
            }
        }
    }
}
