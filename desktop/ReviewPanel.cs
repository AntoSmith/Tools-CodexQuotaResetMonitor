using System;
using System.Drawing;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class ReviewPanel : UserControl
    {
        readonly IHistoryRepository repository;
        readonly Label state, status;
        readonly Button[] buttons = new Button[4];
        HistoryItem current;
        int generation;
        bool saving, loading;
        public event Action<HistoryItem> Saved;
        public ReviewPanel(IHistoryRepository repository)
        {
            if (repository == null) throw new ArgumentNullException("repository");
            this.repository = repository;
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96, 96);
            Size = new Size(508, 126); BackColor = Color.White; Font = new Font("Microsoft YaHei UI", 9F);
            TableLayoutPanel layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1, Margin = Padding.Empty, Padding = Padding.Empty };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 25)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 40));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); Controls.Add(layout);
            state = new Label { Name = "review-state", Dock = DockStyle.Fill, Margin = Padding.Empty, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true, ForeColor = Palette.Muted };
            layout.Controls.Add(state, 0, 0);
            TableLayoutPanel actions = new TableLayoutPanel { Name = "review-actions", Dock = DockStyle.Fill, RowCount = 1, ColumnCount = 4, Margin = Padding.Empty };
            actions.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.Controls.Add(actions, 0, 1);
            string[] labels = { "confirmed", "ambiguous", "uncertain", "unreviewed" };
            for (int i = 0; i < labels.Length; i++) {
                actions.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25));
                string label = labels[i];
                Button button = Palette.Button(actions, label == "unreviewed" ? "清除标记" : HistoryDisplay.Label(label), 0, 0, 100);
                button.Name = "review-" + label; button.Tag = label; button.Dock = DockStyle.Fill;
                button.Margin = new Padding(i == 0 ? 0 : 4, 2, i == 3 ? 0 : 4, 2); buttons[i] = button;
                actions.SetCellPosition(button, new TableLayoutPanelCellPosition(i, 0));
                button.Click += async delegate { await SaveAsync(label); };
            }
            status = new Label { Name = "review-status", Dock = DockStyle.Fill, Margin = new Padding(0, 7, 0, 0), AutoEllipsis = true,
                ForeColor = Palette.Muted, Font = new Font("Microsoft YaHei UI", 8.5F) };
            layout.Controls.Add(status, 0, 2); Bind(null);
        }
        bool Alive { get { return !IsDisposed && !Disposing; } }
        public void Bind(HistoryItem item)
        {
            if (!Alive) return;
            generation++; loading = false; current = item == null ? null : item.Copy();
            status.Text = item == null ? "选择一条线索后可添加人工标记。" : "人工标记仅供整理线索；自动分类和通知状态保持独立。";
            status.ForeColor = Palette.Muted; Render();
        }
        void Render()
        {
            state.Text = current == null ? "人工标记：—" : "人工标记：" + HistoryDisplay.Label(current.label);
            state.ForeColor = current == null ? Palette.Muted : HistoryDisplay.Color(current.label);
            foreach (Button button in buttons) {
                string label = Convert.ToString(button.Tag);
                bool selected = current != null && current.label == label;
                button.Enabled = current != null && !saving && !loading;
                button.BackColor = selected ? HistoryDisplay.Color(label) : Color.White;
                button.ForeColor = selected ? Color.White : HistoryDisplay.Color(label);
            }
        }
        public async Task LoadAsync(string id)
        {
            if (!Alive) return;
            Bind(null); int request = generation; loading = true; status.Text = "正在读取人工标记…"; Render();
            try {
                HistoryItem item = await Task.Run(delegate { return repository.Get(id); });
                if (!Alive || request != generation) return;
                Bind(item);
            } catch (Exception ex) {
                if (!Alive || request != generation) return;
                loading = false; status.Text = "读取失败：" + ex.Message; status.ForeColor = Palette.Amber; Render();
            }
        }
        async Task SaveAsync(string label)
        {
            if (!Alive || current == null || saving || loading) return;
            string id = current.id; int request = generation;
            saving = true; status.Text = "正在保存…"; status.ForeColor = Palette.Muted; Render();
            try {
                HistoryItem saved = await Task.Run(delegate { return repository.Set(id, label); });
                if (!Alive) return;
                saving = false;
                if (request == generation && current != null && current.id == id) {
                    current = saved.Copy(); status.Text = label == "unreviewed" ? "已清除标记，恢复为未标记。" : "已保存为“" + HistoryDisplay.Label(saved.label) + "”。";
                    status.ForeColor = Palette.Green;
                }
                Render(); Action<HistoryItem> handler = Saved; if (handler != null) handler(saved);
            } catch (Exception ex) {
                if (!Alive) return;
                saving = false;
                if (request == generation && current != null && current.id == id) { status.Text = "保存结果未确认：" + ex.Message + " 请重新加载核对。"; status.ForeColor = Palette.Amber; }
                Render();
            }
        }
        protected override void Dispose(bool disposing) { if (disposing) { generation++; Saved = null; } base.Dispose(disposing); }
    }
}
