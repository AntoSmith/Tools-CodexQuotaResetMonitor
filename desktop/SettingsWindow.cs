using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class SettingsWindow : Form
    {
        public readonly CheckBox StartupCheck;
        public readonly Label Status, RecipientStatus;
        public readonly DataGridView RecipientsGrid;
        public readonly TextBox NameInput, EmailInput;
        public readonly CheckBox EnabledSwitch, UncertainSwitch, LimitedSwitch;
        public readonly ComboBox DeliveryChoice;
        readonly DateTimePicker start, end;
        readonly Label remaining;
        readonly Panel editor;
        readonly TabPage mail;
        public readonly SetupPanels Setup;
        readonly Timer countdown = new Timer { Interval = 1000 };
        readonly Func<string, string> testRecipient;
        DataGridViewRow selected;
        bool loading, dirty, sending;
        static readonly string[] modes = { "immediate", "digest_30m", "digest_daily", "quiet_night" };
        static DateTime Now { get { return DateTime.UtcNow.AddHours(8); } }
        readonly Func<bool> read;
        readonly Action<bool> write;
        readonly Func<RecipientContact[], RecipientPreferences> saveRecipients;
        public SettingsWindow(Func<bool> read, Action<bool> write, Action systemSettings, Action logs,
                              Func<RecipientPreferences> readRecipients, Func<RecipientContact[], RecipientPreferences> saveRecipients,
                              Func<string, string> testRecipient = null,
                              Func<SetupPreferences> readSetup = null, Func<SetupPreferences, SetupPreferences> saveSetup = null,
                              string initialTab = null)
        {
            this.read = read; this.write = write; this.saveRecipients = saveRecipients; this.testRecipient = testRecipient;
            Text = "设置 · " + AppBrand.Name; ClientSize = new Size(1000, 690);
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            BackColor = Palette.Canvas; Icon = AppBrand.Icon;
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            TabControl tabs = new TabControl { Left = 20, Top = 18, Width = 960, Height = 654,
                Font = new Font("Microsoft YaHei UI", 10F) };
            mail = new TabPage("订阅管理") { BackColor = Palette.Canvas };
            TabPage startup = new TabPage("启动设置") { BackColor = Palette.Canvas };
            Setup = new SetupPanels(readSetup, saveSetup, delegate { tabs.SelectedTab = mail; });
            tabs.TabPages.Add(Setup.Mail); tabs.TabPages.Add(mail); tabs.TabPages.Add(Setup.Collection); tabs.TabPages.Add(startup); Controls.Add(tabs);
            tabs.SelectedTab = initialTab == "mail" ? Setup.Mail : mail;
            if (initialTab == "mail") Setup.PreferMailForNewUser();

            Palette.Text(mail, "订阅者管理", 20, 18, 880, 34, 19, Palette.Ink, true);
            Palette.Text(mail, "在本机管理名单、订阅时间和发送偏好。选择一位订阅者开始编辑。", 20, 58, 880, 27, 10, Palette.Muted, false);
            RecipientsGrid = new DataGridView { Left = 20, Top = 100, Width = 416, Height = 382,
                BackgroundColor = Color.White, BorderStyle = BorderStyle.FixedSingle,
                AllowUserToAddRows = false, AllowUserToDeleteRows = false, AllowUserToResizeRows = false,
                RowHeadersVisible = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
                SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false,
                ReadOnly = true, EnableHeadersVisualStyles = false,
                ColumnHeadersHeight = 38, ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing,
                GridColor = Color.FromArgb(228, 234, 243) };
            RecipientsGrid.ColumnHeadersDefaultCellStyle = new DataGridViewCellStyle { BackColor = Palette.Canvas, ForeColor = Palette.Ink, Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Bold) };
            RecipientsGrid.DefaultCellStyle = new DataGridViewCellStyle { BackColor = Color.White, ForeColor = Palette.Ink,
                SelectionBackColor = Color.FromArgb(226, 237, 254), SelectionForeColor = Palette.Ink,
                Font = new Font("Microsoft YaHei UI", 10F), Padding = new Padding(6, 0, 2, 0) };
            RecipientsGrid.RowTemplate.Height = 45;
            RecipientsGrid.Columns.Add(new DataGridViewTextBoxColumn { Name = "name", HeaderText = "备注", FillWeight = 27, SortMode = DataGridViewColumnSortMode.NotSortable });
            RecipientsGrid.Columns.Add(new DataGridViewTextBoxColumn { Name = "email", HeaderText = "邮箱", FillWeight = 45, SortMode = DataGridViewColumnSortMode.NotSortable });
            RecipientsGrid.Columns.Add(new DataGridViewTextBoxColumn { Name = "state", HeaderText = "状态", FillWeight = 28, SortMode = DataGridViewColumnSortMode.NotSortable });
            mail.Controls.Add(RecipientsGrid);
            editor = new Panel { Left = 452, Top = 100, Width = 478, Height = 382, BackColor = Color.White, BorderStyle = BorderStyle.FixedSingle };
            mail.Controls.Add(editor);
            Palette.Text(editor, "订阅详情", 18, 12, 230, 30, 13, Palette.Ink, true);
            EnabledSwitch = Toggle(editor, "启用提醒", 337, 14, 120);
            Palette.Text(editor, "备注名称", 18, 56, 84, 24, 10, Palette.Muted, false);
            NameInput = Input(editor, 108, 53, 344, 80);
            Palette.Text(editor, "目标邮箱", 18, 95, 84, 24, 10, Palette.Muted, false);
            EmailInput = Input(editor, 108, 92, 344, 320);
            UncertainSwitch = Toggle(editor, "接收待核实线索", 18, 136, 207);
            LimitedSwitch = Toggle(editor, "设置订阅起止时间", 239, 136, 219);
            Palette.Text(editor, "开始（北京时间）", 18, 177, 206, 23, 9, Palette.Muted, false);
            Palette.Text(editor, "到期（北京时间）", 246, 177, 207, 23, 9, Palette.Muted, false);
            start = DateInput(editor, 18, 201); end = DateInput(editor, 246, 201); end.Value = Now.AddMonths(1);
            remaining = Palette.Text(editor, "", 18, 242, 285, 28, 11, Palette.Blue, true);
            Button renew = Palette.Button(editor, "续期一个月", 316, 239, 138); renew.Height = 32;
            renew.Click += delegate { RenewSubscription(); };
            Palette.Text(editor, "发送偏好", 18, 292, 84, 24, 10, Palette.Muted, false);
            DeliveryChoice = new ComboBox { Left = 108, Top = 288, Width = 345, DropDownStyle = ComboBoxStyle.DropDownList };
            DeliveryChoice.Items.AddRange(new object[] { "发现即发", "每半小时汇总", "每日 20:00 汇总", "夜间免打扰（22:00–08:00）" });
            DeliveryChoice.SelectedIndex = 0; editor.Controls.Add(DeliveryChoice);
            Palette.Text(editor, "偏好仅控制发信时间；待核实线索不会当作已确认重置。", 18, 337, 440, 28, 9, Palette.Muted, false);
            Palette.Button(mail, "+ 添加订阅者", 20, 496, 150).Click += delegate { AddRecipient(); };
            Palette.Button(mail, "删除选中项", 184, 496, 133).Click += delegate { RemoveRecipient(); };
            Button save = Palette.Button(mail, "保存并应用", 452, 496, 160);
            save.BackColor = Palette.Blue; save.ForeColor = Color.White;
            save.Click += delegate { SaveRecipients(); };
            Button test = Palette.Button(mail, "发送初始测试邮件", 626, 496, 190);
            test.Click += async delegate { await TestSelected(); };
            RecipientStatus = Palette.Text(mail, "", 20, 549, 910, 56, 9, Palette.Muted, false);
            RecipientsGrid.CurrentCellChanged += delegate { SelectContact(); };
            foreach (TextBox input in new TextBox[] { NameInput, EmailInput }) input.TextChanged += delegate { Changed(); };
            foreach (CheckBox toggle in new CheckBox[] { EnabledSwitch, UncertainSwitch, LimitedSwitch }) toggle.CheckedChanged += delegate { Changed(); };
            start.ValueChanged += delegate { Changed(); }; end.ValueChanged += delegate { Changed(); };
            DeliveryChoice.SelectedIndexChanged += delegate { Changed(); };
            countdown.Tick += delegate { RefreshCountdown(); }; countdown.Start();
            FormClosing += delegate(object sender, FormClosingEventArgs args) {
                if (sending) { args.Cancel = true; RecipientStatus.Text = "测试正在发送，请等待结果后关闭；不会自动重试。"; }
                else if ((dirty || Setup.Dirty) && MessageBox.Show(this, "还有未保存的设置修改，确定放弃并关闭？", "未保存的修改", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) args.Cancel = true;
            };

            Palette.Text(startup, "启动与后台运行", 24, 20, 590, 35, 18, Palette.Ink, true);
            StartupCheck = new CheckBox { Text = "登录 Windows 后自动启动监控", Left = 26, Top = 72,
                Width = 570, Height = 30, Font = new Font("Microsoft YaHei UI", 11F) };
            startup.Controls.Add(StartupCheck);
            Status = Palette.Text(startup, "", 26, 114, 590, 56, 10, Palette.Muted, false);
            Palette.Text(startup, "登录后在托盘运行，不自动弹出主窗口。\n电脑关机或休眠时无法采集；系统启动应用中也需保持允许。", 26, 180, 590, 52, 10, Palette.Muted, false);
            Palette.Button(startup, "保存启动设置", 24, 250, 124).Click += delegate { Save(StartupCheck.Checked); };
            Palette.Button(startup, "修复启动项", 158, 250, 124).Click += delegate { Save(true); };
            Palette.Button(startup, "Windows 启动应用", 292, 250, 170).Click += delegate { Run(systemSettings); };
            Palette.Button(startup, "启动日志", 472, 250, 114).Click += delegate { Run(logs); };
            Palette.Text(startup, "修复会重新登记当前程序；系统禁用或安全软件拦截需在对应设置中解除。", 26, 310, 590, 48, 9, Palette.Muted, false);
            Reload();
            try {
                RecipientPreferences current = readRecipients(); LoadContacts(current.Contacts);
                RecipientStatus.Text = current.Channel == "smtp" ? "已载入名单。修改后请保存；测试邮件仅发给选中的已保存邮箱。" : "当前为仅日志模式；日常提醒需启用 SMTP。手动测试仍会尝试发送邮件。";
            } catch (Exception ex) { RecipientStatus.Text = "读取失败，可重新填写并保存：" + ex.Message; RecipientStatus.ForeColor = Palette.Amber; }
            Setup.Updated += delegate { RecipientStatus.Text = Setup.Channel == "smtp" ? "邮件发送已启用；保存收件人后可发送初始测试邮件。" : "当前仅记录日志。开启邮件提醒请到“邮件发送”页。"; };
        }
        static CheckBox Toggle(Control parent, string text, int x, int y, int width)
        {
            CheckBox control = new CheckBox { Text = text, Left = x, Top = y, Width = width, Height = 30, ForeColor = Palette.Ink, Font = new Font("Microsoft YaHei UI", 10F) };
            parent.Controls.Add(control); return control;
        }
        static TextBox Input(Control parent, int x, int y, int width, int max)
        {
            TextBox control = new TextBox { Left = x, Top = y, Width = width, MaxLength = max, Font = new Font("Microsoft YaHei UI", 10F) };
            parent.Controls.Add(control); return control;
        }
        static DateTimePicker DateInput(Control parent, int x, int y)
        {
            DateTimePicker control = new DateTimePicker { Left = x, Top = y, Width = 207, Format = DateTimePickerFormat.Custom, CustomFormat = "yyyy-MM-dd HH:mm", Value = Now };
            parent.Controls.Add(control); return control;
        }
        static DateTime ReadDate(string text, DateTime fallback)
        {
            DateTimeOffset value;
            return DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.None, out value) ? value.ToOffset(TimeSpan.FromHours(8)).DateTime : fallback;
        }
        static string EncodeDate(DateTime value) { return value.ToString("yyyy-MM-dd'T'HH:mm:00", CultureInfo.InvariantCulture) + "+08:00"; }
        void FlushContact()
        {
            if (loading || selected == null || selected.Tag == null) return;
            RecipientContact contact = (RecipientContact)selected.Tag;
            contact.Name = NameInput.Text; contact.Email = EmailInput.Text;
            contact.Enabled = EnabledSwitch.Checked; contact.AcceptUncertain = UncertainSwitch.Checked;
            contact.StartAt = LimitedSwitch.Checked ? EncodeDate(start.Value) : "";
            contact.EndAt = LimitedSwitch.Checked ? EncodeDate(end.Value) : "";
            contact.DeliveryMode = modes[Math.Max(0, DeliveryChoice.SelectedIndex)];
            selected.Cells[0].Value = contact.Name; selected.Cells[1].Value = contact.Email;
        }
        void SelectContact()
        {
            if (loading) return;
            FlushContact(); selected = RecipientsGrid.CurrentRow;
            loading = true;
            try {
                editor.Enabled = selected != null;
                if (selected == null || selected.Tag == null) return;
                RecipientContact contact = (RecipientContact)selected.Tag;
                NameInput.Text = contact.Name; EmailInput.Text = contact.Email;
                EnabledSwitch.Checked = contact.Enabled; UncertainSwitch.Checked = contact.AcceptUncertain;
                LimitedSwitch.Checked = !string.IsNullOrEmpty(contact.EndAt);
                start.Value = ReadDate(contact.StartAt, Now); end.Value = ReadDate(contact.EndAt, Now.AddMonths(1));
                DeliveryChoice.SelectedIndex = Math.Max(0, Array.IndexOf(modes, contact.DeliveryMode));
            } finally { loading = false; RefreshCountdown(); }
        }
        void LoadContacts(RecipientContact[] contacts)
        {
            loading = true; selected = null; RecipientsGrid.Rows.Clear();
            if (contacts != null) foreach (RecipientContact contact in contacts) {
                int index = RecipientsGrid.Rows.Add(contact.Name, contact.Email, ""); RecipientsGrid.Rows[index].Tag = contact.Copy();
            }
            loading = false; dirty = false; SelectContact();
        }
        void Changed()
        {
            if (loading) return;
            dirty = true; FlushContact(); RefreshCountdown();
            RecipientStatus.ForeColor = Palette.Amber; RecipientStatus.Text = "有未保存的修改。保存后生效；请先保存再发送测试邮件。";
        }
        static string Countdown(RecipientContact contact)
        {
            if (!contact.Enabled) return "已停用";
            if (string.IsNullOrEmpty(contact.EndAt)) return "长期有效";
            DateTime from = ReadDate(contact.StartAt, Now), until = ReadDate(contact.EndAt, Now);
            if (until <= Now) return "已到期 · 停发";
            TimeSpan span = (from > Now ? from : until) - Now;
            return (from > Now ? "距开始 " : "剩余 ") + span.Days + "天 " + span.Hours.ToString("00") + ":" + span.Minutes.ToString("00") + ":" + span.Seconds.ToString("00");
        }
        void RefreshCountdown()
        {
            start.Enabled = end.Enabled = LimitedSwitch.Checked;
            foreach (DataGridViewRow row in RecipientsGrid.Rows) {
                RecipientContact contact = row.Tag as RecipientContact;
                if (contact != null) {
                    string label = Countdown(contact);
                    row.Cells[2].Value = label.Contains("天") ? label.Split(' ')[0] + (label.StartsWith("剩余") ? " " + (ReadDate(contact.EndAt, Now) - Now).Days + "天" : "") : label;
                    row.Cells[2].ToolTipText = label;
                    if (row == selected) { remaining.Text = label; remaining.ForeColor = label.Contains("停") ? Palette.Amber : Palette.Blue; }
                }
            }
            if (selected == null) remaining.Text = "请添加或选择订阅者";
        }
        public void AddRecipient()
        {
            FlushContact(); loading = true;
            RecipientContact contact = new RecipientContact { Name = "", Email = "", StartAt = EncodeDate(Now), EndAt = EncodeDate(Now.AddMonths(1)) };
            int index = RecipientsGrid.Rows.Add("", "", ""); RecipientsGrid.Rows[index].Tag = contact;
            loading = false; RecipientsGrid.CurrentCell = RecipientsGrid.Rows[index].Cells[0]; SelectContact(); Changed(); NameInput.Focus();
        }
        public void RenewSubscription()
        {
            bool continuing = LimitedSwitch.Checked && end.Value > Now;
            DateTime baseline = continuing ? end.Value : Now;
            LimitedSwitch.Checked = true;
            if (!continuing) start.Value = Now;
            end.Value = baseline.AddMonths(1);
        }
        public void RemoveRecipient()
        {
            if (RecipientsGrid.CurrentRow == null) return;
            loading = true; int index = RecipientsGrid.CurrentRow.Index; selected = null;
            RecipientsGrid.Rows.RemoveAt(index); loading = false; SelectContact(); Changed();
        }
        public void SaveRecipients()
        {
            if (sending) return;
            try {
                FlushContact(); string email = EmailInput.Text;
                List<RecipientContact> contacts = new List<RecipientContact>();
                foreach (DataGridViewRow row in RecipientsGrid.Rows) contacts.Add(((RecipientContact)row.Tag).Copy());
                RecipientPreferences saved = saveRecipients(contacts.ToArray()); LoadContacts(saved.Contacts);
                foreach (DataGridViewRow row in RecipientsGrid.Rows) if (((RecipientContact)row.Tag).Email == email) RecipientsGrid.CurrentCell = row.Cells[0];
                RecipientStatus.ForeColor = Palette.Green;
                RecipientStatus.Text = "已保存 " + saved.Count + " 位订阅者并同步配置。" + saved.AppliedMessage
                    + (saved.Channel == "smtp" ? "" : " 当前为仅日志模式，尚未启用邮件发送。");
            } catch (Exception ex) { RecipientStatus.Text = "未保存 / 未确认：" + ex.Message; RecipientStatus.ForeColor = Palette.Amber; }
        }
        public async Task TestSelected()
        {
            if (sending) return;
            if (Setup.MailDirty) { RecipientStatus.Text = "请先在“邮件发送”页保存发件邮箱和授权码，再测试收信。"; RecipientStatus.ForeColor = Palette.Amber; return; }
            if (dirty || selected == null || testRecipient == null) {
                RecipientStatus.Text = "请先保存修改并选中一位订阅者，再发送测试邮件。"; RecipientStatus.ForeColor = Palette.Amber; return;
            }
            string email = ((RecipientContact)selected.Tag).Email;
            sending = true; mail.Enabled = false; Setup.SetSending(true);
            RecipientStatus.Text = "正在向选中邮箱发送测试邮件… 请勿重复操作。";
            try { string result = await Task.Run(delegate { return testRecipient(email); }); RecipientStatus.Text = result; RecipientStatus.ForeColor = Palette.Green; }
            catch (Exception ex) { RecipientStatus.Text = "发送结果未确认：" + ex.Message; RecipientStatus.ForeColor = Palette.Amber; }
            finally { sending = false; mail.Enabled = true; Setup.SetSending(false); }
        }
        void Run(Action action) { try { action(); } catch (Exception ex) { Status.Text = "操作失败：" + ex.Message; Status.ForeColor = Palette.Amber; } }
        public void Reload()
        {
            try {
                StartupCheck.Checked = read(); Status.ForeColor = Palette.Muted;
                Status.Text = StartupCheck.Checked ? "启动项已登记。是否成功自启动，请查看启动日志；\n如被系统禁用，请打开 Windows 启动应用检查。" : "未登记当前程序的自启动项。勾选后点击保存即可启用。";
            } catch (Exception ex) { Status.Text = "无法读取启动设置：" + ex.Message; Status.ForeColor = Palette.Amber; }
        }
        public void Save(bool enabled)
        {
            try {
                write(enabled);
                if (read() != enabled) throw new InvalidOperationException("写入后校验不一致，请检查系统权限或安全软件。");
                Reload(); Status.Text = "已保存。" + Status.Text;
            } catch (Exception ex) { Status.Text = "设置未保存：" + ex.Message; Status.ForeColor = Palette.Amber; }
        }
        protected override void Dispose(bool disposing) { if (disposing) countdown.Dispose(); base.Dispose(disposing); }
    }
}
