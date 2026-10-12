using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace CodexQuotaDesktop
{
    internal sealed class HistoryItem
    {
        public string id { get; set; }
        public string post_id { get; set; }
        public string kind { get; set; }
        public string title { get; set; }
        public string username { get; set; }
        public string text { get; set; }
        public string body { get; set; }
        public string url { get; set; }
        public string created_at { get; set; }
        public string detected_at { get; set; }
        public string label { get; set; }
        public string reviewed_at { get; set; }
        public HistoryItem Copy() { return (HistoryItem)MemberwiseClone(); }
    }
    internal sealed class HistoryPage
    {
        public List<HistoryItem> items { get; set; }
        public Dictionary<string, int> counts { get; set; }
        public int total { get; set; }
        public int offset { get; set; }
        public int limit { get; set; }
    }
    internal interface IHistoryRepository
    {
        HistoryPage List(string label, int offset, int limit);
        HistoryItem Get(string id);
        HistoryItem Set(string id, string label);
    }
    internal static class HistoryDisplay
    {
        public static readonly string[] Labels = { "all", "unreviewed", "confirmed", "ambiguous", "uncertain" };
        public static string Label(string value)
        {
            switch (value) {
                case "all": return "全部";
                case "confirmed": return "确定";
                case "ambiguous": return "模糊";
                case "uncertain": return "不确定";
                default: return "未标记";
            }
        }
        public static Color Color(string value)
        {
            switch (value) {
                case "confirmed": return Palette.Green;
                case "ambiguous": return Palette.Amber;
                case "uncertain": return System.Drawing.Color.FromArgb(117, 77, 164);
                default: return Palette.Muted;
            }
        }
        public static string Kind(string value)
        {
            switch (value) {
                case "completed": return "已重置候选";
                case "upcoming": return "即将重置候选";
                case "credit": return "重置券线索";
                case "uncertain": return "待核实线索";
                case "correction": return "延期 / 撤回线索";
                case "limits": return "额度变化线索";
                default: return String.IsNullOrEmpty(value) ? "—" : value;
            }
        }
        public static string BeijingTime(string value)
        {
            DateTimeOffset stamp;
            return DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out stamp)
                ? stamp.ToOffset(TimeSpan.FromHours(8)).ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture) : "—";
        }
        public static bool SafeUrl(string value)
        {
            return value != null && Regex.IsMatch(value, @"\Ahttps://x\.com/(?:[A-Za-z0-9_]+|i/web)/status/[0-9]+\z", RegexOptions.CultureInvariant);
        }
    }
    internal sealed class HistoryClient : IHistoryRepository
    {
        readonly string root;
        public HistoryClient(string root) { this.root = root; }
        public HistoryPage List(string label, int offset, int limit)
        {
            if (Array.IndexOf(HistoryDisplay.Labels, label) < 0 || offset < 0 || limit < 1 || limit > 200)
                throw new InvalidOperationException("历史筛选或分页参数无效。");
            Dictionary<string, object> result = Call("list", new { label = label, offset = offset, limit = limit });
            try {
                HistoryPage page = Serializer().ConvertToType<HistoryPage>(result);
                if (page.items == null || page.counts == null || page.total < 0 || page.offset != offset || page.limit != limit)
                    throw new InvalidOperationException();
                foreach (HistoryItem item in page.items) Validate(item);
                return page;
            } catch { throw new InvalidOperationException("历史列表返回格式无效，请刷新重试。"); }
        }
        public HistoryItem Get(string id) { return Item(Call("get", new { id = RequireId(id) })); }
        public HistoryItem Set(string id, string label)
        {
            if (Array.IndexOf(HistoryDisplay.Labels, label) < 1) throw new InvalidOperationException("人工标记无效。");
            return Item(Call("set", new { id = RequireId(id), label = label }));
        }
        static string RequireId(string id)
        {
            if (String.IsNullOrWhiteSpace(id)) throw new InvalidOperationException("缺少历史记录标识。");
            return id;
        }
        static void Validate(HistoryItem item)
        {
            if (item == null || String.IsNullOrEmpty(item.id) || Array.IndexOf(HistoryDisplay.Labels, item.label) < 1)
                throw new InvalidOperationException("历史记录返回格式无效。");
        }
        static JavaScriptSerializer Serializer() { return new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 }; }
        static HistoryItem Item(Dictionary<string, object> result)
        {
            try {
                HistoryItem item = Serializer().ConvertToType<HistoryItem>(result["item"]); Validate(item); return item;
            } catch { throw new InvalidOperationException("历史记录返回格式无效，请重新加载。"); }
        }
        Dictionary<string, object> Call(string action, object payload)
        {
            string python;
            try { using (MonitorProcess runtime = new MonitorProcess(root)) python = runtime.Runtime("python"); }
            catch { throw new InvalidOperationException("找不到历史记录运行环境，请重新运行构建脚本。"); }
            ProcessStartInfo info = new ProcessStartInfo(python, "-m quota_monitor.history " + action);
            info.WorkingDirectory = root; info.UseShellExecute = false; info.CreateNoWindow = true;
            info.RedirectStandardInput = true; info.RedirectStandardOutput = true; info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            try {
                using (Process process = Process.Start(info)) {
                    Stopwatch watch = Stopwatch.StartNew();
                    Task<string> output = process.StandardOutput.ReadToEndAsync();
                    Task<string> errors = process.StandardError.ReadToEndAsync();
                    Task input = Task.Factory.StartNew(delegate {
                        try {
                            byte[] request = new UTF8Encoding(false).GetBytes(Serializer().Serialize(payload));
                            process.StandardInput.BaseStream.Write(request, 0, request.Length); process.StandardInput.BaseStream.Flush();
                        } finally { process.StandardInput.Close(); }
                    });
                    try {
                        if (!process.WaitForExit(Math.Max(0, 10000 - (int)watch.ElapsedMilliseconds)) ||
                            !Task.WaitAll(new Task[] { input, output, errors }, Math.Max(0, 10000 - (int)watch.ElapsedMilliseconds))) {
                            if (!process.HasExited) process.Kill(); process.WaitForExit(2000);
                            throw new InvalidOperationException(action == "set" ? "保存超时，结果未确认；请刷新记录检查标记后再操作。" : "读取历史记录超时，请刷新重试。");
                        }
                    } catch (AggregateException) {
                        if (!process.HasExited) process.Kill();
                        throw new InvalidOperationException("历史记录通信失败，请刷新确认结果。");
                    }
                    Dictionary<string, object> result;
                    try { result = Serializer().Deserialize<Dictionary<string, object>>(output.Result); }
                    catch { throw new InvalidOperationException("无法读取历史记录结果，请检查本地配置。"); }
                    // Stderr is drained to avoid deadlocks; never display it or configuration paths.
                    if (result == null || !result.ContainsKey("ok") || !(result["ok"] is bool))
                        throw new InvalidOperationException("历史记录返回格式无效。");
                    if (!(bool)result["ok"]) {
                        string message = result.ContainsKey("error") ? Convert.ToString(result["error"]) : "历史记录操作失败。";
                        throw new InvalidOperationException(String.IsNullOrWhiteSpace(message) ? "历史记录操作失败。" : message);
                    }
                    if (process.ExitCode != 0) throw new InvalidOperationException("历史记录进程异常退出，请刷新确认结果。");
                    return result;
                }
            } catch (InvalidOperationException) { throw; }
            catch { throw new InvalidOperationException("无法启动历史记录读取，请检查本地运行环境。"); }
        }
    }
}
