using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace CodexQuotaDesktop
{
    internal sealed class RecipientContact
    {
        public string Name, Email;
        public bool Enabled = true, AcceptUncertain = true;
        public string StartAt = "", EndAt = "", DeliveryMode = "immediate";
        public RecipientContact Copy() { return (RecipientContact)MemberwiseClone(); }
    }
    internal sealed class RecipientPreferences
    {
        public string Recipients, Channel, AppliedMessage;
        public int Count;
        public RecipientContact[] Contacts;
        public string Message;
    }

    internal sealed class RecipientSettings
    {
        readonly string root;
        public RecipientSettings(string root) { this.root = root; }
        public RecipientPreferences Read() { return Call("read", null); }
        public string Test(string email) { return Call("test", new Dictionary<string, object> { { "email", email } }).Message; }
        public RecipientPreferences Save(string recipients) { return Call("save", new Dictionary<string, object> { { "recipients", recipients } }); }
        public RecipientPreferences Save(RecipientContact[] contacts)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            foreach (RecipientContact contact in contacts)
                rows.Add(new Dictionary<string, object> { { "name", contact.Name }, { "email", contact.Email },
                    { "enabled", contact.Enabled }, { "accept_uncertain", contact.AcceptUncertain },
                    { "start_at", contact.StartAt }, { "end_at", contact.EndAt }, { "delivery_mode", contact.DeliveryMode } });
            return Call("save", new Dictionary<string, object> { { "contacts", rows } });
        }
        RecipientPreferences Call(string action, object payload)
        {
            string python;
            using (MonitorProcess runtime = new MonitorProcess(root)) python = runtime.Runtime("python");
            ProcessStartInfo info = new ProcessStartInfo(python, "-m quota_monitor.desktop_settings " + action);
            info.WorkingDirectory = root; info.UseShellExecute = false; info.CreateNoWindow = true;
            info.RedirectStandardInput = true; info.RedirectStandardOutput = true; info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            JavaScriptSerializer json = new JavaScriptSerializer();
            using (Process process = Process.Start(info)) {
                Stopwatch elapsed = Stopwatch.StartNew();
                int deadline = action == "test" ? 90000 : 10000;
                var output = process.StandardOutput.ReadToEndAsync();
                var errors = process.StandardError.ReadToEndAsync();
                Task input = Task.Factory.StartNew(delegate {
                    try {
                        if (payload != null) {
                            byte[] request = new UTF8Encoding(false).GetBytes(json.Serialize(payload));
                            process.StandardInput.BaseStream.Write(request, 0, request.Length);
                            process.StandardInput.BaseStream.Flush();
                        }
                    } finally { process.StandardInput.Close(); }
                });
                try {
                    if (!process.WaitForExit(Math.Max(0, deadline - (int)elapsed.ElapsedMilliseconds)) ||
                        !Task.WaitAll(new Task[] { input, output, errors }, Math.Max(0, deadline - (int)elapsed.ElapsedMilliseconds))) {
                        if (!process.HasExited) process.Kill();
                        process.WaitForExit(2000);
                        throw new InvalidOperationException(action == "test" ? "发送超时，结果未确认；请先检查收件箱，未自动重试。" : "邮箱设置操作超时，请重新打开设置确认是否已保存。");
                    }
                } catch (AggregateException) {
                    if (!process.HasExited) process.Kill();
                    throw new InvalidOperationException("无法完成邮箱设置通信，请重新打开设置确认结果。");
                }
                // Raw stderr may include private paths; show only the safe protocol error.
                Dictionary<string, object> result;
                try { result = json.Deserialize<Dictionary<string, object>>(output.Result); }
                catch { throw new InvalidOperationException("无法读取邮箱设置结果，请检查 Python 和本地配置。"); }
                if (result == null || !result.ContainsKey("ok")) throw new InvalidOperationException("邮箱设置返回格式无效。");
                if (!Convert.ToBoolean(result["ok"])) throw new InvalidOperationException(Convert.ToString(result["error"]));
                if (process.ExitCode != 0) throw new InvalidOperationException("邮箱设置进程异常退出。");
                if (action == "test") return new RecipientPreferences { Message = Convert.ToString(result["message"]) };
                List<RecipientContact> contacts = new List<RecipientContact>();
                foreach (object row in (System.Collections.IEnumerable)result["contacts"]) {
                    var contact = (Dictionary<string, object>)row;
                    contacts.Add(new RecipientContact { Name = Convert.ToString(contact["name"]), Email = Convert.ToString(contact["email"]),
                        Enabled = Convert.ToBoolean(contact["enabled"]), AcceptUncertain = Convert.ToBoolean(contact["accept_uncertain"]),
                        StartAt = Convert.ToString(contact["start_at"]), EndAt = Convert.ToString(contact["end_at"]), DeliveryMode = Convert.ToString(contact["delivery_mode"]) });
                }
                return new RecipientPreferences { Recipients = Convert.ToString(result["recipients"]), Contacts = contacts.ToArray(),
                    Channel = Convert.ToString(result["channel"]), Count = result.ContainsKey("count") ? Convert.ToInt32(result["count"]) : 0 };
            }
        }
    }
}
