using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal sealed class TrayApplication : ApplicationContext
    {
        readonly MonitorProcess monitor;
        readonly DashboardState state = new DashboardState();
        readonly DashboardWindow window;
        readonly NotifyIcon tray;
        readonly ToolStripMenuItem startupItem, startItem, stopItem;
        readonly Timer timer;
        readonly string root;
        bool closing, setupPending;
        AlertWindow popup;
        HistoryWindow historyWindow;
        readonly IHistoryRepository history;
        public TrayApplication(string root, bool hidden, bool launch) : this(root, hidden, launch, true) { }
        public TrayApplication(string root, bool hidden, bool launch, bool startMonitoring)
        {
            setupPending = !startMonitoring;
            this.root = root; monitor = new MonitorProcess(root); window = new DashboardWindow(); history = new HistoryClient(root);
            window.StartButton.Click += delegate { StartMonitor(); };
            window.StopButton.Click += delegate { StopMonitor(); };
            window.ConfigButton.Click += delegate { OpenConfig(); };
            window.SettingsButton.Click += delegate { OpenSettings(); };
            window.LogButton.Click += delegate { OpenLog(); };
            window.PreviewButton.Click += delegate { ShowAlert(DesktopAlert.Preview()); };
            window.LatestButton.Click += delegate { if (state.LastAlert != null) ShowAlert(state.LastAlert); };
            window.HistoryButton.Click += delegate { OpenHistory(); };
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("显示状态", null, delegate { ShowWindow(); });
            startItem = new ToolStripMenuItem("启动监控", null, delegate { StartMonitor(); }); menu.Items.Add(startItem);
            stopItem = new ToolStripMenuItem("停止监控", null, delegate { StopMonitor(); }); menu.Items.Add(stopItem);
            menu.Items.Add("预览提醒效果", null, delegate { ShowAlert(DesktopAlert.Preview()); });
            menu.Items.Add("历史线索", null, delegate { OpenHistory(); });
            menu.Items.Add("打开配置", null, delegate { OpenConfig(); });
            menu.Items.Add("查看日志", null, delegate { OpenLog(); });
            menu.Items.Add("设置", null, delegate { OpenSettings(); });
            startupItem = new ToolStripMenuItem("登录 Windows 后自动启动"); RefreshStartup();
            startupItem.Click += delegate {
                try { Startup.Set(!Startup.Enabled()); startupItem.Checked = Startup.Enabled(); }
                catch (Exception ex) { MessageBox.Show(ex.Message, "启动设置未更改"); }
            };
            menu.Items.Add(startupItem); menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("退出并停止监控", null, delegate { Quit(); });
            tray = new NotifyIcon { Icon = AppBrand.Icon, Text = AppBrand.Name, ContextMenuStrip = menu, Visible = launch };
            tray.DoubleClick += delegate { ShowWindow(); };
            window.FormClosing += delegate(object sender, FormClosingEventArgs e) {
                if (e.CloseReason == CloseReason.WindowsShutDown) { Quit(); return; }
                if (!closing) { e.Cancel = true; window.Hide(); }
            };
            timer = new Timer { Interval = 500 }; timer.Tick += delegate { Tick(); };
            Refresh();
            if (launch) { timer.Start(); if (startMonitoring) StartMonitor(); if (!hidden || setupPending) ShowWindow(); }
            else window.CreateControl();
        }
        void Tick()
        {
            bool exited = state.Active && !monitor.Running;
            foreach (string line in exited ? monitor.ReapExitedOutput() : monitor.DrainOutput()) state.Apply(line);
            if (exited) {
                int code = monitor.ExitCode; monitor.Stop();
                state.Stopped("采集进程退出（代码 " + code + "），请查看日志后重新启动。");
                tray.ShowBalloonTip(5000, "Codex 监控已停止", "请打开状态窗口或查看日志。", ToolTipIcon.Warning);
            }
            if (popup == null && state.Alerts.Count > 0) ShowAlert(state.Alerts.Dequeue());
            Refresh();
        }
        void Refresh()
        {
            window.RefreshState(state); startItem.Enabled = !state.Active; stopItem.Enabled = state.Active;
            if (setupPending && !state.Active) window.ShowSetupHint();
            tray.Text = AppBrand.Name + (state.Active ? (state.Phase == "collecting" ? "：正在采集" : "：运行中") : "：已停止");
        }
        void ShowWindow() { window.Show(); window.WindowState = FormWindowState.Normal; window.Activate(); }
        void StartMonitor()
        {
            if (state.Active) return;
            setupPending = false;
            try { monitor.Start(MonitorProcess.MonitorCode); state.Started(); Program.LaunchLog(root, "monitor_started"); }
            catch (Exception ex) {
                Program.LaunchLog(root, "monitor_start_failed " + ex.GetType().Name);
                state.Stopped("启动失败：" + ex.Message);
                tray.ShowBalloonTip(5000, "Codex 监控启动失败", "请打开状态窗口查看原因。", ToolTipIcon.Error);
            }
            Refresh();
        }
        void StopMonitor() { state.Stopped(""); state.Alerts.Clear(); monitor.Stop(); Refresh(); }
        void ShowAlert(DesktopAlert alert)
        {
            if (popup != null) { popup.Activate(); return; }
            popup = new AlertWindow(alert, history);
            popup.ReviewSaved += async delegate(HistoryItem item) { if (historyWindow != null) await historyWindow.RefreshAsync(); };
            popup.FormClosed += delegate { popup.Dispose(); popup = null; };
            popup.Show();
        }
        async void OpenHistory()
        {
            if (historyWindow != null) { historyWindow.Show(); historyWindow.WindowState = FormWindowState.Normal; historyWindow.Activate(); await historyWindow.RefreshAsync(); return; }
            historyWindow = new HistoryWindow(history);
            historyWindow.FormClosed += delegate { historyWindow.Dispose(); historyWindow = null; };
            historyWindow.Show();
        }
        void OpenConfig() { OpenText(Path.Combine(root, "config.browser.local.toml")); }
        void OpenSettings()
        {
            RecipientSettings recipients = new RecipientSettings(root);
            using (SettingsWindow settings = new SettingsWindow(Startup.Enabled, Startup.Set,
                delegate { Process.Start(new ProcessStartInfo("ms-settings:startupapps") { UseShellExecute = true }); },
                delegate { OpenText(Path.Combine(root, "logs", "desktop-startup.log")); },
                recipients.Read, delegate(RecipientContact[] value) {
                    bool running = state.Active && monitor.Running;
                    if (running) StopMonitor();
                    // Stop the old worker before publishing a new policy. A
                    // failed queue reconciliation must leave delivery stopped.
                    RecipientPreferences saved = recipients.Save(value);
                    if (running) {
                        StartMonitor();
                        saved.AppliedMessage = state.Active ? "监控已重新启动，新名单已提交加载。" : "监控重启失败，请返回主窗口查看原因；名单已保存。";
                    } else saved.AppliedMessage = "下次启动监控时生效。";
                    return saved;
                }, recipients.Test)) settings.ShowDialog(window);
            RefreshStartup();
        }
        void RefreshStartup()
        {
            try { startupItem.Checked = Startup.Enabled(); }
            catch (Exception ex) { Program.LaunchLog(root, "startup_status_read_failed " + ex.GetType().Name); }
        }
        void OpenLog() { OpenText(monitor.LogPath); }
        void OpenText(string path)
        {
            try { Process.Start("notepad.exe", "\"" + path + "\""); }
            catch (Exception ex) { MessageBox.Show(ex.Message, "无法打开文件"); }
        }
        public void Quit()
        {
            if (closing) return; closing = true;
            timer.Stop(); state.Stopped(""); monitor.Dispose();
            if (popup != null) popup.Close();
            if (historyWindow != null) historyWindow.Close();
            tray.Visible = false; tray.Dispose(); window.Dispose(); timer.Dispose(); ExitThread();
        }
    }
}
