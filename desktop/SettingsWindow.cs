using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class SettingsWindow : Form
    {
        public readonly CheckBox StartupCheck;
        public readonly Label Status, RecipientStatus;
        public readonly DataGridView RecipientsGrid;
        readonly Func<bool> read;
        readonly Action<bool> write;
        readonly Func<RecipientContact[], RecipientPreferences> saveRecipients;
        public SettingsWindow(Func<bool> read, Action<bool> write, Action systemSettings, Action logs,
                              Func<RecipientPreferences> readRecipients, Func<RecipientContact[], RecipientPreferences> saveRecipients)
        {
            this.read = read; this.write = write; this.saveRecipients = saveRecipients;
            Text = "设置 · Codex 额度监控"; ClientSize = new Size(710, 610);
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            BackColor = Palette.Canvas; Icon = AppBrand.Icon;
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            TabControl tabs = new TabControl { Left = 20, Top = 18, Width = 670, Height = 574,
                Font = new Font("Microsoft YaHei UI", 10F) };
            TabPage mail = new TabPage("收件人") { BackColor = Palette.Canvas };
            TabPage startup = new TabPage("启动设置") { BackColor = Palette.Canvas };
            tabs.TabPages.Add(mail); tabs.TabPages.Add(startup); Controls.Add(tabs);

            Palette.Text(mail, "管理收件人", 18, 18, 610, 34, 18, Palette.Ink, true);
            Palette.Text(mail, "给邮箱起个好认的名字。每行一个地址，备注可留空，支持中文。", 18, 57, 620, 27, 10, Palette.Muted, false);
            RecipientsGrid = new DataGridView { Left = 18, Top = 94, Width = 620, Height = 270,
                BackgroundColor = Color.White, BorderStyle = BorderStyle.FixedSingle,
                AllowUserToAddRows = false, AllowUserToDeleteRows = false, AllowUserToResizeRows = false,
                RowHeadersVisible = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
                SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false,
                EditMode = DataGridViewEditMode.EditOnEnter, EnableHeadersVisualStyles = false,
                ColumnHeadersHeight = 38, ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing,
                GridColor = Color.FromArgb(228, 234, 243) };
            RecipientsGrid.ColumnHeadersDefaultCellStyle = new DataGridViewCellStyle { BackColor = Palette.Canvas, ForeColor = Palette.Ink, Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Bold) };
            RecipientsGrid.DefaultCellStyle = new DataGridViewCellStyle { BackColor = Color.White, ForeColor = Palette.Ink,
                SelectionBackColor = Color.FromArgb(226, 237, 254), SelectionForeColor = Palette.Ink,
                Font = new Font("Microsoft YaHei UI", 10F), Padding = new Padding(6, 0, 2, 0) };
            RecipientsGrid.RowTemplate.Height = 37;
            RecipientsGrid.Columns.Add(new DataGridViewTextBoxColumn { Name = "name", HeaderText = "备注名称（可选）", FillWeight = 34, MaxInputLength = 80, SortMode = DataGridViewColumnSortMode.NotSortable });
            RecipientsGrid.Columns.Add(new DataGridViewTextBoxColumn { Name = "email", HeaderText = "邮箱地址", FillWeight = 66, MaxInputLength = 320, SortMode = DataGridViewColumnSortMode.NotSortable });
            mail.Controls.Add(RecipientsGrid);
            Palette.Button(mail, "+ 添加收件人", 18, 378, 142).Click += delegate { AddRecipient(); };
            Palette.Button(mail, "删除选中行", 172, 378, 126).Click += delegate { RemoveRecipient(); };
            Palette.Text(mail, "重复邮箱仅发一份；不同备注的重复项需合并。", 312, 389, 326, 32, 9, Palette.Muted, false);
            Button save = Palette.Button(mail, "保存并应用", 18, 436, 160);
            save.BackColor = Palette.Blue; save.ForeColor = Color.White;
            save.Click += delegate { SaveRecipients(); };
            Palette.Text(mail, "运行中会重启监控；已停止则下次启动生效。", 192, 448, 446, 26, 10, Palette.Muted, false);
            RecipientStatus = Palette.Text(mail, "", 18, 490, 620, 46, 9, Palette.Muted, false);

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
                RecipientStatus.Text = current.Channel == "smtp" ? "已载入当前名单。保存后自动同步到程序配置；修改备注不会补发旧邮件。" : "当前为仅日志模式：保存后仍不会发邮件，需在配置中启用 SMTP。";
            } catch (Exception ex) { RecipientStatus.Text = "读取失败，可重新填写并保存：" + ex.Message; RecipientStatus.ForeColor = Palette.Amber; }
        }
        void LoadContacts(RecipientContact[] contacts)
        {
            RecipientsGrid.Rows.Clear();
            if (contacts != null) foreach (RecipientContact contact in contacts) RecipientsGrid.Rows.Add(contact.Name, contact.Email);
        }
        public void AddRecipient()
        {
            RecipientsGrid.EndEdit();
            int index = RecipientsGrid.Rows.Add("", "");
            RecipientsGrid.CurrentCell = RecipientsGrid.Rows[index].Cells[0];
            RecipientsGrid.Focus(); RecipientsGrid.BeginEdit(true);
        }
        public void RemoveRecipient()
        {
            RecipientsGrid.CancelEdit();
            if (RecipientsGrid.CurrentRow != null) RecipientsGrid.Rows.RemoveAt(RecipientsGrid.CurrentRow.Index);
        }
        public void SaveRecipients()
        {
            try {
                if (!RecipientsGrid.EndEdit()) throw new InvalidOperationException("请先完成当前行编辑。");
                List<RecipientContact> contacts = new List<RecipientContact>();
                foreach (DataGridViewRow row in RecipientsGrid.Rows)
                    contacts.Add(new RecipientContact { Name = Convert.ToString(row.Cells[0].Value), Email = Convert.ToString(row.Cells[1].Value) });
                RecipientPreferences saved = saveRecipients(contacts.ToArray()); LoadContacts(saved.Contacts);
                RecipientStatus.ForeColor = Palette.Green;
                RecipientStatus.Text = "已保存 " + saved.Count + " 位收件人并同步配置。" + saved.AppliedMessage
                    + (saved.Channel == "smtp" ? "" : " 当前为仅日志模式，尚未启用邮件发送。");
            } catch (Exception ex) { RecipientStatus.Text = "未保存 / 未确认：" + ex.Message; RecipientStatus.ForeColor = Palette.Amber; }
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
    }
}
