using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class SetupPanels
    {
        public readonly TabPage Mail = new TabPage("邮件发送") { BackColor = Palette.Canvas };
        public readonly TabPage Collection = new TabPage("采集设置") { BackColor = Palette.Canvas };
        public readonly TextBox Sender, Host, Port, AuthCode;
        public readonly ComboBox Mode, Provider, Browser;
        public readonly CheckBox ShowCode, ClearCode;
        public readonly CheckedListBox Accounts;
        public readonly Label MailStatus, CollectionStatus, CodeStatus;
        public readonly Button SaveMailButton, SaveCollectionButton;
        readonly Func<SetupPreferences> read;
        readonly Func<SetupPreferences, SetupPreferences> save;
        MonitoredAccount[] accountChoices = new MonitoredAccount[0];
        bool loading;
        public bool MailDirty { get; private set; }
        public bool CollectionDirty { get; private set; }
        public bool Dirty { get { return MailDirty || CollectionDirty; } }
        public string Channel { get; private set; }
        public event Action Updated;

        public SetupPanels(Func<SetupPreferences> read, Func<SetupPreferences, SetupPreferences> save, Action recipients)
        {
            this.read = read; this.save = save;
            Palette.Text(Mail, "配置你的邮件提醒", 24, 20, 880, 35, 19, Palette.Ink, true);
            Palette.Text(Mail, "填好发件邮箱和授权码，再到“订阅管理”添加收件人并发送测试邮件。", 24, 62, 880, 30, 10, Palette.Muted, false);
            Panel card = new Panel { Left = 24, Top = 108, Width = 892, Height = 364, BackColor = Color.White, BorderStyle = BorderStyle.FixedSingle };
            Mail.Controls.Add(card);
            LabelAt(card, "提醒方式", 22, 24); Mode = Choice(card, "smtpModeChoice", 142, 20, 230);
            Mode.Items.AddRange(new object[] { "邮件提醒", "仅记录日志" }); Mode.SelectedIndex = 1;
            LabelAt(card, "邮箱服务", 418, 24); Provider = Choice(card, "smtpProviderChoice", 530, 20, 330);
            Provider.Items.AddRange(new object[] { "QQ 邮箱（推荐）", "自定义 SMTP（SSL）" }); Provider.SelectedIndex = 0;
            LabelAt(card, "发件邮箱", 22, 79); Sender = Input(card, "smtpSenderInput", 142, 76, 718, 320);
            LabelAt(card, "SMTP 服务器", 22, 135); Host = Input(card, "smtpHostInput", 142, 132, 430, 253); Host.Text = "smtp.qq.com";
            LabelAt(card, "SSL 端口", 604, 135); Port = Input(card, "smtpPortInput", 698, 132, 162, 5); Port.Text = "465";
            LabelAt(card, "SMTP 授权码", 22, 192); AuthCode = Input(card, "smtpAuthCodeInput", 142, 189, 558, 512);
            AuthCode.UseSystemPasswordChar = true;
            ShowCode = new CheckBox { Text = "显示", Left = 722, Top = 189, Width = 120, Height = 30, ForeColor = Palette.Muted };
            card.Controls.Add(ShowCode);
            CodeStatus = Palette.Text(card, "尚未设置授权码", 142, 227, 690, 26, 9, Palette.Muted, false);
            ClearCode = new CheckBox { Text = "清除已保存的授权码", Left = 142, Top = 258, Width = 350, Height = 30, ForeColor = Palette.Muted };
            card.Controls.Add(ClearCode);
            Palette.Text(card, "QQ 邮箱：网页端设置 → 账号 / 账号与安全 → 开启 SMTP 服务并获取授权码。\n授权码不是邮箱登录密码；已有授权码时，输入框留空即可保留。", 142, 299, 708, 49, 9, Palette.Muted, false);
            SaveMailButton = Palette.Button(Mail, "保存邮件设置", 24, 490, 170); SaveMailButton.BackColor = Palette.Blue; SaveMailButton.ForeColor = Color.White;
            SaveMailButton.Click += delegate { SaveMail(); };
            Palette.Button(Mail, "添加收件人 / 发送测试", 210, 490, 230).Click += delegate { recipients(); };
            MailStatus = Palette.Text(Mail, "", 24, 548, 886, 65, 10, Palette.Muted, false);
            Palette.Text(Collection, "浏览器与监控账号", 24, 20, 880, 35, 19, Palette.Ink, true);
            Palette.Text(Collection, "选择本机已安装的浏览器，勾选要监控的公开账号。默认每半小时检查一次。", 24, 62, 880, 30, 10, Palette.Muted, false);
            LabelAt(Collection, "采集浏览器", 24, 119); Browser = Choice(Collection, "collectionBrowserChoice", 152, 116, 330);
            Browser.Items.AddRange(new object[] { "Google Chrome", "Microsoft Edge" }); Browser.SelectedIndex = 0;
            Palette.Text(Collection, "采集在后台运行。公开信息可读时无需登录 X。", 24, 165, 870, 30, 10, Palette.Muted, false);
            Accounts = new CheckedListBox { Name = "monitoredAccounts", Left = 24, Top = 218, Width = 892, Height = 185,
                CheckOnClick = true, Font = new Font("Microsoft YaHei UI", 11F), BorderStyle = BorderStyle.FixedSingle, IntegralHeight = false };
            Collection.Controls.Add(Accounts);
            Palette.Text(Collection, "勾选并保存表示你已核对这些公开账号；数字 ID 由模板提供。", 24, 423, 888, 30, 10, Palette.Muted, false);
            SaveCollectionButton = Palette.Button(Collection, "保存采集设置", 24, 490, 170); SaveCollectionButton.BackColor = Palette.Blue; SaveCollectionButton.ForeColor = Color.White;
            SaveCollectionButton.Click += delegate { SaveCollection(); };
            CollectionStatus = Palette.Text(Collection, "", 24, 548, 886, 65, 10, Palette.Muted, false);
            ShowCode.CheckedChanged += delegate { AuthCode.UseSystemPasswordChar = !ShowCode.Checked; };
            foreach (TextBox input in new TextBox[] { Sender, Host, Port, AuthCode }) input.TextChanged += delegate { MailChanged(); };
            Mode.SelectedIndexChanged += delegate { MailChanged(); };
            Provider.SelectedIndexChanged += delegate {
                if (!loading && Provider.SelectedIndex == 0) { Host.Text = "smtp.qq.com"; Port.Text = "465"; }
                Host.ReadOnly = Port.ReadOnly = Provider.SelectedIndex == 0; MailChanged();
            };
            ClearCode.CheckedChanged += delegate { AuthCode.Enabled = !ClearCode.Checked; MailChanged(); };
            Browser.SelectedIndexChanged += delegate { if (!loading) CollectionDirty = true; };
            Accounts.ItemCheck += delegate { if (!loading) CollectionDirty = true; };
            SaveMailButton.Enabled = SaveCollectionButton.Enabled = save != null;
            if (read != null) {
                try { SetupPreferences value = read(); LoadMail(value.Transport); LoadCollection(value.Collection);
                    string hint = value.RepairNeeded ? "原设置无法读取，请重新填写并保存。" : Readiness(value);
                    MailStatus.Text = CollectionStatus.Text = hint;
                } catch { MailStatus.Text = CollectionStatus.Text = "设置读取失败，请检查运行环境，或重新填写并保存。"; }
            }
            Host.ReadOnly = Port.ReadOnly = Provider.SelectedIndex == 0;
        }
        static void LabelAt(Control parent, string text, int x, int y) { Palette.Text(parent, text, x, y, 119, 27, 10, Palette.Muted, false); }
        static TextBox Input(Control parent, string name, int x, int y, int width, int max)
        {
            TextBox input = new TextBox { Name = name, Left = x, Top = y, Width = width, MaxLength = max, Font = new Font("Microsoft YaHei UI", 10F) };
            parent.Controls.Add(input); return input;
        }
        static ComboBox Choice(Control parent, string name, int x, int y, int width)
        {
            ComboBox choice = new ComboBox { Name = name, Left = x, Top = y, Width = width, DropDownStyle = ComboBoxStyle.DropDownList, Font = new Font("Microsoft YaHei UI", 10F) };
            parent.Controls.Add(choice); return choice;
        }
        void MailChanged() { if (!loading) { MailDirty = true; MailStatus.ForeColor = Palette.Muted; MailStatus.Text = "邮件设置尚未保存；保存后再发送测试邮件。"; } }
        static string Readiness(SetupPreferences value)
        {
            return value.Ready ? "配置已就绪。可以发送测试邮件，或回到主窗口启动监控。" : "还需完成：" + string.Join("、", value.MissingSteps) + "。请在相应设置页完成并保存。";
        }
        void LoadMail(MailTransport value)
        {
            if (value == null) return;
            loading = true;
            Channel = value.Channel; Mode.SelectedIndex = value.Channel == "smtp" ? 0 : 1;
            Sender.Text = value.Username == "sender@example.com" ? "" : value.Username;
            Host.Text = value.Host; Port.Text = value.Port.ToString(); Provider.SelectedIndex = value.Host == "smtp.qq.com" && value.Port == 465 ? 0 : 1;
            AuthCode.Text = ""; ShowCode.Checked = false; ClearCode.Checked = false; AuthCode.Enabled = true;
            CodeStatus.Text = value.AuthCodePresent ? "已配置授权码 · 留空保留，填写新值替换" : "尚未设置授权码 · 请填写邮箱提供的 SMTP 授权码";
            CodeStatus.ForeColor = value.AuthCodePresent ? Palette.Green : Palette.Muted;
            loading = false; MailDirty = false;
        }
        void LoadCollection(CollectionPreferences value)
        {
            if (value == null) return;
            loading = true; Browser.SelectedIndex = value.BrowserChannel == "msedge" ? 1 : 0;
            accountChoices = value.Accounts; Accounts.Items.Clear();
            foreach (MonitoredAccount account in accountChoices)
                Accounts.Items.Add("@" + account.Username + "  ·  " + (account.Kind == "organization" ? "机构账号" : "个人账号") + "  ·  ID " + account.Id, account.Enabled);
            loading = false; CollectionDirty = false;
        }
        public void SaveMail()
        {
            if (save == null) return;
            try {
                int port;
                if (!int.TryParse(Port.Text, out port)) throw new InvalidOperationException("SSL 端口应为 1–65535 的整数。");
                SetupPreferences saved = save(new SetupPreferences { Transport = new MailTransport {
                    Channel = Mode.SelectedIndex == 0 ? "smtp" : "console", Host = Host.Text.Trim(), Port = port,
                    Username = Sender.Text.Trim(), AuthCode = ClearCode.Checked ? "" : AuthCode.Text, ClearAuthCode = ClearCode.Checked } });
                LoadMail(saved.Transport); MailStatus.ForeColor = Palette.Green;
                MailStatus.Text = "邮件设置已保存。" + Readiness(saved) + " " + saved.AppliedMessage;
                if (Updated != null) Updated();
            } catch (Exception ex) { MailStatus.Text = "邮件设置未保存 / 未确认：" + ex.Message; MailStatus.ForeColor = Palette.Amber; }
        }
        public void SaveCollection()
        {
            if (save == null) return;
            try {
                List<MonitoredAccount> choices = new List<MonitoredAccount>();
                for (int i = 0; i < accountChoices.Length; i++) choices.Add(new MonitoredAccount { Username = accountChoices[i].Username, Enabled = Accounts.GetItemChecked(i) });
                SetupPreferences saved = save(new SetupPreferences { Collection = new CollectionPreferences {
                    BrowserChannel = Browser.SelectedIndex == 1 ? "msedge" : "chrome", Accounts = choices.ToArray() } });
                LoadCollection(saved.Collection); CollectionStatus.ForeColor = Palette.Green;
                CollectionStatus.Text = "采集设置已保存。" + Readiness(saved) + " " + saved.AppliedMessage;
            } catch (Exception ex) { CollectionStatus.Text = "采集设置未保存 / 未确认：" + ex.Message; CollectionStatus.ForeColor = Palette.Amber; }
        }
        public void SetSending(bool sending) { Mail.Enabled = Collection.Enabled = !sending; }
        public void PreferMailForNewUser()
        {
            if (Sender.Text.Length != 0 || Channel == "smtp") return;
            loading = true; Mode.SelectedIndex = 0; loading = false;
            MailStatus.Text = "填写发件邮箱和 SMTP 授权码后保存，再添加收件人并测试。";
        }
    }
}
