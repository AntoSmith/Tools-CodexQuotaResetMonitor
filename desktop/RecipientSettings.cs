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
        public SetupPreferences Setup;
    }

    internal sealed class MailTransport
    {
        public string Channel = "console", Host = "smtp.qq.com", Username = "", AuthCode = "", AuthCodeSource = "none";
        public int Port = 465;
        public bool AuthCodePresent, ClearAuthCode;
    }
    internal sealed class MonitoredAccount
    {
        public string Username, Id, Kind;
        public bool Enabled, Verified;
    }
    internal sealed class CollectionPreferences
    {
        public string BrowserChannel = "chrome";
        public MonitoredAccount[] Accounts = new MonitoredAccount[0];
    }
    internal sealed class SetupPreferences
    {
        public MailTransport Transport;
        public CollectionPreferences Collection;
        public string Source, AppliedMessage;
        public bool RepairNeeded, Ready;
        public string[] MissingSteps = new string[0];
    }

    internal sealed class RecipientSettings
    {
        readonly string root;
        public RecipientSettings(string root) { this.root = root; }
        public RecipientPreferences Read() { return Call("read", null); }
        public SetupPreferences ReadSetup() { return Call("read-setup", null).Setup; }
        public SetupPreferences SaveSetup(SetupPreferences value)
        {
            Dictionary<string, object> request = new Dictionary<string, object>();
            if (value.Transport != null) {
                MailTransport transport = value.Transport;
                request["transport"] = new Dictionary<string, object> { { "channel", transport.Channel },
                    { "host", transport.Host }, { "port", transport.Port }, { "username", transport.Username },
                    { "auth_code", transport.AuthCode }, { "clear_auth_code", transport.ClearAuthCode } };
            }
            if (value.Collection != null) {
                List<Dictionary<string, object>> accounts = new List<Dictionary<string, object>>();
                foreach (MonitoredAccount account in value.Collection.Accounts)
                    accounts.Add(new Dictionary<string, object> { { "username", account.Username }, { "enabled", account.Enabled } });
                request["collection"] = new Dictionary<string, object> { { "browser_channel", value.Collection.BrowserChannel }, { "accounts", accounts } };
            }
            return Call("save-setup", request).Setup;
        }
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
                if (action == "read-setup" || action == "save-setup") {
                    var transport = (Dictionary<string, object>)result["transport"];
                    var collection = (Dictionary<string, object>)result["collection"];
                    List<MonitoredAccount> accounts = new List<MonitoredAccount>();
                    foreach (object row in (System.Collections.IEnumerable)collection["accounts"]) {
                        var account = (Dictionary<string, object>)row;
                        accounts.Add(new MonitoredAccount { Username = Convert.ToString(account["username"]),
                            Id = Convert.ToString(account["id"]), Kind = Convert.ToString(account["kind"]),
                            Enabled = Convert.ToBoolean(account["enabled"]), Verified = Convert.ToBoolean(account["verified"]) });
                    }
                    return new RecipientPreferences { Setup = new SetupPreferences {
                        Transport = new MailTransport { Channel = Convert.ToString(transport["channel"]), Host = Convert.ToString(transport["host"]),
                            Port = Convert.ToInt32(transport["port"]), Username = Convert.ToString(transport["username"]),
                            AuthCodePresent = Convert.ToBoolean(transport["auth_code_present"]),
                            AuthCodeSource = transport.ContainsKey("auth_code_source") ? Convert.ToString(transport["auth_code_source"]) : "none" },
                        Collection = new CollectionPreferences { BrowserChannel = Convert.ToString(collection["browser_channel"]), Accounts = accounts.ToArray() },
                        Source = Convert.ToString(result["source"]), RepairNeeded = Convert.ToBoolean(result["repair_needed"]),
                        Ready = result.ContainsKey("ready") && Convert.ToBoolean(result["ready"]),
                        MissingSteps = result.ContainsKey("missing_steps") ? Strings(result["missing_steps"]) : new string[0] } };
                }
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
        static string[] Strings(object values)
        {
            List<string> result = new List<string>();
            foreach (object value in (System.Collections.IEnumerable)values) result.Add(Convert.ToString(value));
            return result.ToArray();
        }
    }
}
