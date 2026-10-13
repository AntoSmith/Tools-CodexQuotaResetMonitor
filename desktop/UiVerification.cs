using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Threading;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal static class UiVerification
    {
        static void Check(bool value, string message) { if (!value) throw new Exception(message); }
        static void Snapshot(Form form, string path)
        {
            form.ShowInTaskbar = false; form.StartPosition = FormStartPosition.Manual;
            form.Location = new Point(-16000, -16000); form.Show(); Application.DoEvents();
            using (Bitmap bitmap = new Bitmap(form.Width, form.Height)) { form.DrawToBitmap(bitmap, new Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(path, ImageFormat.Png); }
            form.Hide();
        }
        public static int Run(string root)
        {
            string output = Path.Combine(root, "data", "desktop");
            try {
                string fixture = Path.Combine(output, "settings-fixture-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(Path.Combine(fixture, "data", "desktop"));
                Directory.CreateDirectory(Path.Combine(fixture, "quota_monitor"));
                using (MonitorProcess runtime = new MonitorProcess(root))
                    File.WriteAllText(Path.Combine(fixture, "data", "desktop", "python-path.txt"), runtime.Runtime("python"), new System.Text.UTF8Encoding(false));
                foreach (string file in Directory.GetFiles(Path.Combine(root, "quota_monitor"), "*.py"))
                    File.Copy(file, Path.Combine(fixture, "quota_monitor", Path.GetFileName(file)));
                string fixtureConfig = "[notifications]\nchannel=\"smtp\"\nto=\"old@example.com\"\nusername=\"sender@example.test\"\nQQ_SMTP_AUTH_CODE=\"base-fixture-only\"\n"
                    + "[browser]\nchannel=\"chrome\"\n[[accounts]]\nusername=\"demo\"\nid=\"123\"\nkind=\"personal\"\nverified=false\n";
                File.WriteAllText(Path.Combine(fixture, "config.browser.local.toml"), fixtureConfig, new System.Text.UTF8Encoding(false));
                RecipientSettings bridge = new RecipientSettings(fixture);
                Check(bridge.Read().Recipients == "old@example.com", "Bridge did not read base configuration");
                RecipientPreferences persisted = bridge.Save("a@example.com；b@example.com;c@example.com;a@example.com");
                Check(persisted.Count == 3 && bridge.Read().Recipients == "a@example.com;b@example.com;c@example.com", "Bridge Unicode/dedup/persistence failed");
                persisted = bridge.Save(new RecipientContact[] {
                    new RecipientContact { Name = "我的主邮箱", Email = "a@example.com", Enabled = false, AcceptUncertain = false,
                        StartAt = "2026-09-01T08:00:00+08:00", EndAt = "2026-10-01T08:00:00+08:00", DeliveryMode = "quiet_night" },
                    new RecipientContact { Name = "工作通知", Email = "b@example.com" },
                    new RecipientContact { Name = "", Email = "c@example.com" } });
                Check(bridge.Read().Contacts[0].Name == "我的主邮箱" && persisted.Contacts[1].Name == "工作通知", "Named recipient mapping lost");
                Check(!bridge.Read().Contacts[0].Enabled && !persisted.Contacts[0].AcceptUncertain && persisted.Contacts[0].DeliveryMode == "quiet_night", "Subscription policy lost in native bridge");
                bool rejected = false;
                try { bridge.Save("invalid"); } catch (InvalidOperationException) { rejected = true; }
                Check(rejected && bridge.Read().Recipients == "a@example.com;b@example.com;c@example.com", "Bridge invalid save changed settings");
                Check(File.ReadAllText(Path.Combine(fixture, "config.browser.local.toml")) == fixtureConfig, "Bridge modified original TOML");
                SetupPreferences initialSetup = bridge.ReadSetup();
                Check(initialSetup.Transport.AuthCodePresent && initialSetup.Transport.AuthCode == "", "Setup bridge returned a saved authorization code");
                SetupPreferences configured = bridge.SaveSetup(new SetupPreferences { Transport = new MailTransport {
                    Channel = "smtp", Host = "smtp.qq.com", Port = 465, Username = "sender@example.test", AuthCode = "new-fixture-only" } });
                Check(configured.Transport.AuthCodePresent && configured.Transport.AuthCode == "" && bridge.Read().Contacts.Length == 3, "Transport save lost contacts or exposed code");
                configured = bridge.SaveSetup(new SetupPreferences { Collection = new CollectionPreferences {
                    BrowserChannel = "msedge", Accounts = new MonitoredAccount[] { new MonitoredAccount { Username = "demo", Enabled = true } } } });
                Check(configured.Collection.BrowserChannel == "msedge" && configured.Collection.Accounts[0].Verified && configured.Ready, "Collection save did not confirm mapped account or setup readiness");
                Check(File.ReadAllText(Path.Combine(fixture, "config.browser.local.toml")) == fixtureConfig, "Setup bridge modified original TOML");
                // A stalled helper must not block a large stdin write indefinitely.
                File.WriteAllText(Path.Combine(fixture, "quota_monitor", "desktop_settings.py"), "import time\ntime.sleep(30)\n");
                var timeoutWatch = System.Diagnostics.Stopwatch.StartNew();
                bool timedOut = false;
                try { bridge.Save(new string('a', 20000)); }
                catch (InvalidOperationException ex) { timedOut = ex.Message.Contains("超时"); }
                Check(timedOut && timeoutWatch.Elapsed.TotalSeconds < 15, "Stalled settings helper exceeded its deadline");
                bool startup = false; bool failWrite = false; bool failRecipients = false; int testMails = 0;
                RecipientContact[] recipients = new RecipientContact[] {
                    new RecipientContact { Name = "我的主邮箱", Email = "first@example.com" },
                    new RecipientContact { Name = "工作通知", Email = "second@example.com" },
                    new RecipientContact { Name = "家人", Email = "third@example.com" } };
                using (SettingsWindow settings = new SettingsWindow(delegate { return startup; },
                    delegate(bool value) { if (failWrite) throw new UnauthorizedAccessException("test"); startup = value; },
                    delegate { }, delegate { },
                    delegate { return new RecipientPreferences { Contacts = recipients, Channel = "smtp" }; },
                    delegate(RecipientContact[] value) {
                        if (failRecipients) throw new InvalidOperationException("邮箱地址无效");
                        recipients = value;
                        return new RecipientPreferences { Contacts = value, Channel = "smtp", Count = value.Length, AppliedMessage = "监控已重新启动。" };
                    }, delegate(string email) { testMails++; Check(email == "first@example.com", "Wrong test recipient"); return "SMTP 服务器已接受测试邮件，请确认收件。"; })) {
                    Check(settings.RecipientsGrid.Rows.Count == 3 && Convert.ToString(settings.RecipientsGrid.Rows[0].Cells[0].Value) == "我的主邮箱", "Recipient settings not loaded");
                    Check(settings.Controls.Find("smtpAuthCodeInput", true).Length == 1, "SMTP setup is missing from settings UI");
                    Check(((TextBox)settings.Controls.Find("smtpAuthCodeInput", true)[0]).UseSystemPasswordChar, "Authorization code is not masked by default");
                    settings.AddRecipient();
                    Check(settings.RecipientsGrid.Rows.Count == 4, "Add recipient failed");
                    settings.RemoveRecipient();
                    Check(settings.RecipientsGrid.Rows.Count == 3, "Remove recipient failed");
                    settings.RecipientsGrid.CurrentCell = settings.RecipientsGrid.Rows[0].Cells[0];
                    settings.NameInput.Text = "演示订阅者";
                    settings.EnabledSwitch.Checked = false;
                    settings.UncertainSwitch.Checked = false;
                    settings.LimitedSwitch.Checked = true;
                    settings.DeliveryChoice.SelectedIndex = 2;
                    settings.TestSelected().GetAwaiter().GetResult();
                    Check(testMails == 0, "Unsaved edits sent a test email");
                    settings.SaveRecipients();
                    Check(recipients[0].Name == "演示订阅者" && !recipients[0].Enabled && !recipients[0].AcceptUncertain && recipients[0].DeliveryMode == "digest_daily" && recipients[0].EndAt != "", "Policy edit not submitted: " + recipients[0].Name + "/" + recipients[0].Enabled + "/" + recipients[0].AcceptUncertain + "/" + recipients[0].DeliveryMode + "/" + recipients[0].EndAt);
                    Check(settings.RecipientStatus.Text.Contains("3") && settings.RecipientStatus.Text.Contains("重新启动"), "Recipient save not shown");
                    Check(!startup, "Recipient save changed startup registration");
                    failRecipients = true; settings.SaveRecipients();
                    Check(settings.RecipientStatus.Text.Contains("未保存"), "Recipient failure shown as success");
                    failRecipients = false; settings.SaveRecipients();
                    settings.RecipientsGrid.CurrentCell = settings.RecipientsGrid.Rows[1].Cells[0];
                    settings.RenewSubscription(); settings.SaveRecipients();
                    DateTimeOffset renewedEnd = DateTimeOffset.Parse(recipients[1].EndAt);
                    Check(Math.Abs((renewedEnd - DateTimeOffset.UtcNow.AddMonths(1)).TotalMinutes) < 2, "Unlimited subscription renewal exceeded one month");
                    settings.RecipientsGrid.CurrentCell = settings.RecipientsGrid.Rows[0].Cells[0];
                    Check(!settings.StartupCheck.Checked, "Initial startup setting incorrect");
                    settings.Save(true); Check(startup && settings.StartupCheck.Checked, "Enable startup did not persist");
                    settings.Save(false); Check(!startup && !settings.StartupCheck.Checked, "Disable startup did not persist");
                    failWrite = true; settings.Save(true);
                    Check(!startup && settings.Status.Text.Contains("未保存"), "Failed write was reported as saved");
                    failWrite = false; settings.Save(true);
                    var testTask = settings.TestSelected();
                    while (!testTask.IsCompleted) { Application.DoEvents(); Thread.Sleep(10); }
                    testTask.GetAwaiter().GetResult();
                    Check(testMails == 1 && settings.RecipientStatus.Text.Contains("接受"), "Single-recipient test status missing");
                    settings.EnabledSwitch.Checked = true; settings.SaveRecipients();
                    Snapshot(settings, Path.Combine(output, "settings-preview.png"));
                }
                bool failMail = false; SetupPreferences submitted = null;
                SetupPreferences currentSetup = new SetupPreferences { Ready = true,
                    Transport = new MailTransport { Channel = "smtp", Host = "smtp.qq.com", Port = 465, Username = "sender@example.test", AuthCodePresent = true },
                    Collection = new CollectionPreferences { BrowserChannel = "chrome", Accounts = new MonitoredAccount[] {
                        new MonitoredAccount { Username = "demo", Id = "123", Kind = "personal", Enabled = true, Verified = true } } } };
                using (SettingsWindow settings = new SettingsWindow(delegate { return false; }, delegate(bool value) { }, delegate { }, delegate { },
                    delegate { return new RecipientPreferences { Contacts = recipients, Channel = "smtp" }; },
                    delegate(RecipientContact[] value) { return new RecipientPreferences { Contacts = value, Channel = "smtp", Count = value.Length }; },
                    delegate(string email) { testMails++; return "SMTP 服务器已接受"; }, delegate { return currentSetup; }, delegate(SetupPreferences value) {
                        if (failMail) throw new InvalidOperationException("授权码格式无效");
                        submitted = value;
                        if (value.Transport != null) currentSetup.Transport = new MailTransport { Channel = value.Transport.Channel, Host = value.Transport.Host,
                            Port = value.Transport.Port, Username = value.Transport.Username, AuthCodePresent = !value.Transport.ClearAuthCode };
                        if (value.Collection != null) currentSetup.Collection = value.Collection;
                        return currentSetup;
                    }, "mail")) {
                    Check(settings.Setup.AuthCode.Text == "" && settings.Setup.AuthCode.UseSystemPasswordChar, "Saved code displayed on opening UI");
                    settings.Setup.ShowCode.Checked = true; Check(!settings.Setup.AuthCode.UseSystemPasswordChar, "Show-code switch did not change masking");
                    settings.Setup.AuthCode.Text = "ui-fixture-only";
                    settings.TestSelected().GetAwaiter().GetResult();
                    Check(testMails == 1 && settings.RecipientStatus.Text.Contains("邮件发送"), "Unsaved SMTP settings allowed a test send");
                    settings.Setup.SaveMail();
                    Check(submitted.Transport.AuthCode == "ui-fixture-only" && submitted.Collection == null, "SMTP save leaked across independent settings sections");
                    Check(settings.Setup.AuthCode.Text == "" && settings.Setup.AuthCode.UseSystemPasswordChar && !settings.Setup.MailDirty, "Successful save did not clear secret and restore masking");
                    settings.Setup.Sender.Text = "replacement@example.test"; failMail = true; settings.Setup.SaveMail();
                    Check(settings.Setup.MailDirty && settings.Setup.MailStatus.Text.Contains("未保存"), "Failed save discarded unsaved input or reported success");
                    failMail = false; settings.Setup.SaveMail();
                    settings.Setup.Browser.SelectedIndex = 1; settings.Setup.SaveCollection();
                    Check(submitted.Transport == null && submitted.Collection.BrowserChannel == "msedge", "Collection save overwrote transport settings");
                    Check(!settings.Setup.Dirty, "Saved settings still marked dirty");
                    settings.Setup.AuthCode.Text = "discard-fixture-only"; settings.Setup.ClearCode.Checked = true; settings.Setup.SaveMail();
                    Check(submitted.Transport.ClearAuthCode && submitted.Transport.AuthCode == "", "Clear-code choice submitted a conflicting new authorization code");
                    settings.Setup.AuthCode.Text = "preview-fixture-only"; settings.Setup.SaveMail();
                    Snapshot(settings, Path.Combine(output, "settings-smtp-preview.png"));
                }
                using (MonitorProcess runner = new MonitorProcess(root)) {
                    int previous = runner.Generation; runner.Stop();
                    runner.Output.Enqueue(new MonitorProcess.OutputLine { Generation = previous, Text = "stale" });
                    runner.Output.Enqueue(new MonitorProcess.OutputLine { Generation = runner.Generation, Text = "current" });
                    string[] lines = runner.DrainOutput().ToArray();
                    Check(lines.Length == 1 && lines[0] == "current", "Previous-session output leaked after stop/restart");
                }
                using (MonitorProcess runner = new MonitorProcess(root, "desktop-self-test.log")) {
                    runner.Start("[print('@quota-ui '+str(i)) for i in range(200)]");
                    for (int i = 0; i < 100 && runner.Running; i++) Thread.Sleep(50);
                    Check(!runner.Running, "Natural-exit fixture did not finish");
                    string[] lines = runner.ReapExitedOutput().ToArray();
                    Check(lines.Length == 200 && lines[199] == "@quota-ui 199", "Final stdout was lost on natural exit");
                }
                DashboardState state = new DashboardState(); state.Started();
                double now = (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
                state.Apply("@quota-ui {\"type\":\"round_start\",\"round\":1,\"started_at\":" + now.ToString(System.Globalization.CultureInfo.InvariantCulture) + "}");
                using (DashboardWindow dashboard = new DashboardWindow()) {
                    dashboard.RefreshState(state);
                    Check(dashboard.StartButton.Text.Contains("监控中") && dashboard.StartButton.BackColor == Palette.Ink && dashboard.StopButton.Enabled,
                        "Running button feedback was not preserved");
                    state.Apply("@quota-ui {\"type\":\"round_end\",\"round\":1,\"ended_at\":" + now.ToString(System.Globalization.CultureInfo.InvariantCulture)
                        + ",\"next_at\":" + (now + 1714).ToString(System.Globalization.CultureInfo.InvariantCulture) + ",\"posts\":15,\"outcome\":\"partial\"}");
                    dashboard.RefreshState(state);
                    Check(dashboard.StartButton.BackColor == Palette.Ink && state.Active, "Round end cleared active button");
                    Snapshot(dashboard, Path.Combine(output, "dashboard-preview.png"));
                    state.Stopped(""); dashboard.RefreshState(state);
                    Check(!dashboard.StopButton.Enabled && dashboard.StartButton.Text == "启动监控", "Stop button did not restore state");
                }
                state.Started(); state.Apply("@quota-ui {\"type\":\"round_start\",\"round\":1,\"started_at\":1788870000}");
                state.Stopped("process exited");
                Check(state.LastOutcome == "interrupted" && state.RoundsCompleted == 0 && state.NextAt == 0, "Interrupted round counted as completed");
                using (AlertWindow alert = new AlertWindow(DesktopAlert.Preview())) {
                    alert.PreviewFrame(); Snapshot(alert, Path.Combine(output, "alert-preview.png"));
                }
                File.WriteAllText(Path.Combine(output, "ui-test.txt"), "PASS recipient bridge Unicode/persistence/validation, UI buttons, generation isolation, interruption and rendered previews"); return 0;
            } catch (Exception ex) { File.WriteAllText(Path.Combine(output, "ui-test.txt"), "FAIL " + ex); return 1; }
        }
    }
}
