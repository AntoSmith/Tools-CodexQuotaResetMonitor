using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class HistoryWindow : Form
    {
        sealed class FilterChoice
        {
            public string Key; public int Count;
            public override string ToString() { return HistoryDisplay.Label(Key) + "  (" + Count + ")"; }
        }
        const int PageSize = 50;
        readonly IHistoryRepository repository;
        readonly DataGridView grid;
        readonly ComboBox filter;
        readonly Label title, metadata, pageStatus;
        readonly TextBox body;
        readonly ReviewPanel review;
        readonly Button previous, next, open;
        int offset, total, pageGeneration, detailGeneration;
        bool filling, updatingFilter, listBusy;
        string selectedId;
        HistoryItem selected;
        public HistoryWindow(IHistoryRepository repository)
        {
            if (repository == null) throw new ArgumentNullException("repository");
            this.repository = repository;
            Text = AppBrand.Name + " · 历史线索";
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            ClientSize = new Size(1000, 660); MinimumSize = new Size(900, 600);
            StartPosition = FormStartPosition.CenterScreen; Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Palette.Canvas; Icon = AppBrand.Icon;
            TableLayoutPanel layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(20, 16, 20, 12), ColumnCount = 1, RowCount = 4 };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 66)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 44));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42)); Controls.Add(layout);
            Panel header = new Panel { Dock = DockStyle.Fill, Margin = Padding.Empty }; layout.Controls.Add(header, 0, 0);
            Palette.Text(header, "历史线索", 0, 0, 650, 34, 20, Palette.Ink, true);
            Palette.Text(header, "回看已发现的消息，记录自己的判断。所有时间均为北京时间。", 1, 39, 850, 23, 9, Palette.Muted, false);
            FlowLayoutPanel toolbar = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.LeftToRight, WrapContents = false, Margin = Padding.Empty };
            layout.Controls.Add(toolbar, 0, 1);
            toolbar.Controls.Add(new Label { Text = "人工标记", AutoSize = true, Margin = new Padding(0, 8, 14, 0), ForeColor = Palette.Ink });
            filter = new ComboBox { Name = "history-filter", Width = 210, DropDownStyle = ComboBoxStyle.DropDownList, Margin = new Padding(0, 4, 12, 0) };
            foreach (string key in HistoryDisplay.Labels) filter.Items.Add(new FilterChoice { Key = key });
            filter.SelectedIndex = 0; toolbar.Controls.Add(filter);
            Button refresh = Palette.Button(toolbar, "刷新", 0, 0, 88); refresh.Name = "history-refresh"; refresh.Height = 32; refresh.Margin = new Padding(0, 0, 12, 0);
            refresh.Click += async delegate { await RefreshAsync(); };
            filter.SelectedIndexChanged += async delegate { if (!updatingFilter) { offset = 0; await RefreshAsync(); } };

            SplitContainer split = new SplitContainer { Dock = DockStyle.Fill, Margin = Padding.Empty, Size = new Size(960, 470), SplitterWidth = 12, BackColor = Palette.Canvas };
            split.Panel1MinSize = 310; split.Panel2MinSize = 390; split.SplitterDistance = 435;
            layout.Controls.Add(split, 0, 2);
            grid = new DataGridView { Name = "history-grid", Dock = DockStyle.Fill, ReadOnly = true, AllowUserToAddRows = false, AllowUserToDeleteRows = false,
                AllowUserToResizeRows = false, AutoGenerateColumns = false, MultiSelect = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect,
                RowHeadersVisible = false, BackgroundColor = Color.White, BorderStyle = BorderStyle.None, CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal,
                GridColor = Palette.Canvas, EnableHeadersVisualStyles = false, ColumnHeadersHeight = 36, ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing };
            grid.ColumnHeadersDefaultCellStyle = new DataGridViewCellStyle { BackColor = Palette.Canvas, ForeColor = Palette.Muted, Font = Font, SelectionBackColor = Palette.Canvas };
            grid.DefaultCellStyle = new DataGridViewCellStyle { BackColor = Color.White, ForeColor = Palette.Ink, SelectionBackColor = Color.FromArgb(231, 238, 250),
                SelectionForeColor = Palette.Ink, Padding = new Padding(3), Font = Font };
            grid.RowTemplate.Height = 48;
            grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "when", HeaderText = "发现时间", Width = 126, SortMode = DataGridViewColumnSortMode.NotSortable });
            grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "label", HeaderText = "人工标记", Width = 80, SortMode = DataGridViewColumnSortMode.NotSortable });
            grid.Columns.Add(new DataGridViewTextBoxColumn { Name = "message", HeaderText = "线索 / 作者", AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill,
                MinimumWidth = 115, SortMode = DataGridViewColumnSortMode.NotSortable });
            grid.DefaultCellStyle.WrapMode = DataGridViewTriState.True;
            split.Panel1.Controls.Add(grid); grid.SelectionChanged += async delegate { if (!filling) await LoadSelectedAsync(); };

            TableLayoutPanel detail = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 5, Padding = new Padding(16, 10, 16, 8), BackColor = Color.White };
            detail.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 42)); detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 63));
            detail.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 42)); detail.RowStyles.Add(new RowStyle(SizeType.Absolute, 126));
            split.Panel2.Controls.Add(detail);
            title = new Label { Dock = DockStyle.Fill, Font = new Font("Microsoft YaHei UI", 13F, FontStyle.Bold), ForeColor = Palette.Ink, Text = "选择一条历史线索", AutoEllipsis = true, Margin = Padding.Empty };
            metadata = new Label { Dock = DockStyle.Fill, ForeColor = Palette.Muted, AutoEllipsis = true, Margin = Padding.Empty };
            body = new TextBox { Name = "history-body", Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, WordWrap = true,
                BorderStyle = BorderStyle.None, BackColor = Palette.Canvas, ForeColor = Palette.Ink, Margin = new Padding(0, 0, 0, 6), Font = new Font("Microsoft YaHei UI", 10F) };
            detail.Controls.Add(title, 0, 0); detail.Controls.Add(metadata, 0, 1); detail.Controls.Add(body, 0, 2);
            Panel links = new Panel { Dock = DockStyle.Fill, Margin = Padding.Empty }; detail.Controls.Add(links, 0, 3);
            open = Palette.Button(links, "查看原帖 ↗", 0, 2, 138); open.Height = 32; open.Enabled = false;
            open.Click += delegate { OpenSelected(); };
            review = new ReviewPanel(repository) { Name = "history-review", Dock = DockStyle.Fill, Margin = Padding.Empty };
            detail.Controls.Add(review, 0, 4);
            review.Saved += async delegate(HistoryItem item) { if (Alive) await RefreshAsync(); };

            TableLayoutPanel footer = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 1, ColumnCount = 3, Margin = Padding.Empty, Padding = new Padding(0, 8, 0, 0) };
            footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 94)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 94));
            layout.Controls.Add(footer, 0, 3);
            pageStatus = new Label { Name = "history-status", Dock = DockStyle.Fill, ForeColor = Palette.Muted, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true, Margin = Padding.Empty };
            footer.Controls.Add(pageStatus, 0, 0);
            previous = Palette.Button(footer, "上一页", 0, 0, 86); previous.Name = "history-previous"; previous.Dock = DockStyle.Fill; previous.Margin = new Padding(5, 0, 0, 0); footer.SetCellPosition(previous, new TableLayoutPanelCellPosition(1, 0));
            next = Palette.Button(footer, "下一页", 0, 0, 86); next.Name = "history-next"; next.Dock = DockStyle.Fill; next.Margin = new Padding(8, 0, 0, 0); footer.SetCellPosition(next, new TableLayoutPanelCellPosition(2, 0));
            previous.Click += async delegate { if (!listBusy && offset > 0) { offset = Math.Max(0, offset - PageSize); await RefreshAsync(); } };
            next.Click += async delegate { if (!listBusy && offset + PageSize < total) { offset += PageSize; await RefreshAsync(); } };
            UpdatePaging(); Shown += async delegate { await RefreshAsync(); };
        }
        bool Alive { get { return !IsDisposed && !Disposing; } }
        void UpdatePaging() { previous.Enabled = !listBusy && offset > 0; next.Enabled = !listBusy && offset + PageSize < total; }
        void ClearDetail(string message)
        {
            detailGeneration++; selected = null; selectedId = null;
            title.Text = message; metadata.Text = ""; body.Text = ""; open.Enabled = false; review.Bind(null);
        }
        public async Task RefreshAsync()
        {
            if (!Alive) return;
            string preserveId = selectedId; string label = ((FilterChoice)filter.SelectedItem).Key;
            int request = ++pageGeneration, requestedOffset = offset;
            listBusy = true; UpdatePaging(); pageStatus.Text = "正在读取历史线索…"; pageStatus.ForeColor = Palette.Muted;
            filling = true; grid.Rows.Clear(); filling = false; ClearDetail("正在读取历史线索…");
            try {
                HistoryPage page = await Task.Run(delegate { return repository.List(label, requestedOffset, PageSize); });
                if (!Alive || request != pageGeneration) return;
                total = page.total;
                if (requestedOffset > 0 && requestedOffset >= total) { offset = total == 0 ? 0 : ((total - 1) / PageSize) * PageSize; await RefreshAsync(); return; }
                updatingFilter = true;
                int filterIndex = filter.SelectedIndex;
                for (int i = 0; i < filter.Items.Count; i++) {
                    FilterChoice choice = (FilterChoice)filter.Items[i]; int count;
                    choice.Count = page.counts.TryGetValue(choice.Key, out count) ? count : 0; filter.Items[i] = choice;
                }
                filter.SelectedIndex = filterIndex; updatingFilter = false;
                filling = true; grid.Rows.Clear(); int selection = 0;
                foreach (HistoryItem item in page.items) {
                    int index = grid.Rows.Add(HistoryDisplay.BeijingTime(item.detected_at), "● " + HistoryDisplay.Label(item.label), item.title + " · @" + item.username);
                    DataGridViewRow row = grid.Rows[index]; row.Tag = item.id;
                    row.Cells[1].Style.ForeColor = HistoryDisplay.Color(item.label); row.Cells[1].Style.SelectionForeColor = HistoryDisplay.Color(item.label);
                    row.Cells[2].ToolTipText = item.title + " · @" + item.username;
                    if (item.id == preserveId) selection = index;
                }
                grid.ClearSelection();
                if (grid.Rows.Count > 0) { grid.CurrentCell = grid.Rows[selection].Cells[0]; grid.Rows[selection].Selected = true; }
                filling = false; listBusy = false; UpdatePaging();
                pageStatus.Text = total == 0 ? "当前筛选下暂无历史线索。" : "共 " + total + " 条 · 第 " + (offset + 1) + "–" + (offset + page.items.Count) + " 条 · 按发现时间从新到旧";
                if (grid.Rows.Count == 0) ClearDetail("当前筛选下暂无历史线索"); else await LoadSelectedAsync();
            } catch (Exception ex) {
                if (!Alive || request != pageGeneration) return;
                filling = false; updatingFilter = false; listBusy = false; UpdatePaging();
                pageStatus.Text = "读取失败：" + ex.Message; pageStatus.ForeColor = Palette.Amber; ClearDetail("读取失败，可点击刷新重试");
            }
        }
        async Task LoadSelectedAsync()
        {
            if (!Alive || filling || grid.SelectedRows.Count == 0) return;
            string id = Convert.ToString(grid.SelectedRows[0].Tag);
            ClearDetail("正在读取原文…"); selectedId = id; int request = detailGeneration;
            try {
                HistoryItem item = await Task.Run(delegate { return repository.Get(id); });
                if (!Alive || request != detailGeneration) return;
                selected = item; title.Text = item.title;
                metadata.Text = "@" + item.username + "  ·  自动分类：" + HistoryDisplay.Kind(item.kind) + "\n"
                    + "发布：" + HistoryDisplay.BeijingTime(item.created_at) + "   发现：" + HistoryDisplay.BeijingTime(item.detected_at);
                string fullText = String.IsNullOrEmpty(item.body) ? item.text : item.body;
                body.Text = (fullText ?? "").Replace("\r\n", "\n").Replace("\r", "\n").Replace("\n", "\r\n");
                body.SelectionStart = 0; body.SelectionLength = 0;
                open.Enabled = HistoryDisplay.SafeUrl(item.url); review.Bind(item);
            } catch (Exception ex) {
                if (!Alive || request != detailGeneration) return;
                title.Text = "原文读取失败"; metadata.Text = ex.Message; review.Bind(null);
            }
        }
        void OpenSelected()
        {
            if (selected == null || !HistoryDisplay.SafeUrl(selected.url)) return;
            try { Process.Start(new ProcessStartInfo(selected.url) { UseShellExecute = true }); }
            catch { MessageBox.Show(this, "无法打开浏览器，请稍后重试。", "打开失败", MessageBoxButtons.OK, MessageBoxIcon.Information); }
        }
        protected override void Dispose(bool disposing) { if (disposing) { pageGeneration++; detailGeneration++; } base.Dispose(disposing); }
    }
}
