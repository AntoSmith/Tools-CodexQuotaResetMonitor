using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace CodexQuotaDesktop
{
    internal static class UpdateInstaller
    {
        sealed class RollbackIncompleteException : IOException
        { public RollbackIncompleteException(string message, Exception inner) : base(message, inner) { } }
        sealed class SavedFile { public string Relative, Backup; public bool Existed; }
        const string ApplicationMutex = @"Local\CodexQuotaMonitorDesktop";
        internal static UpdatePlan ReadPlan(string path)
        {
            path = Path.GetFullPath(path); ReleaseUpdates.RejectReparse(path);
            if (!File.Exists(path) || new FileInfo(path).Length > 8 * 1024 * 1024) throw new InvalidDataException("更新安装计划不存在或过大。");
            UpdatePlan plan;
            try { plan = ReleaseUpdates.Serializer(8 * 1024 * 1024).Deserialize<UpdatePlan>(File.ReadAllText(path, Encoding.UTF8)); }
            catch (ArgumentException ex) { throw new InvalidDataException("更新安装计划格式无效。", ex); }
            if (plan == null || plan.SchemaVersion != 1) throw new InvalidDataException("更新安装计划版本无效。");
            plan.Root = ReleaseUpdates.CanonicalRoot(plan.Root); plan.Work = ReleaseUpdates.CanonicalRoot(plan.Work);
            string updates = Path.Combine(plan.Root, "data", "desktop", "updates");
            if (!String.Equals(Path.GetDirectoryName(plan.Work), updates, StringComparison.OrdinalIgnoreCase)
                || !Regex.IsMatch(Path.GetFileName(plan.Work), @"^update-[a-f0-9]{32}$", RegexOptions.CultureInvariant)
                || !String.Equals(path, Path.Combine(plan.Work, "plan.json"), StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("更新安装计划离开了当前程序的更新目录。");
            ReleaseUpdates.StableVersion(plan.Version);
            if (plan.ArchiveName != "CodexQuotaMonitor-v" + plan.Version + "-windows-x64.zip" || plan.ArchiveSize <= 0 || plan.ArchiveSize > ReleaseUpdates.MaxDownloadBytes
                || plan.ArchiveSha256 == null || !Regex.IsMatch(plan.ArchiveSha256, @"^[a-fA-F0-9]{64}$", RegexOptions.CultureInvariant)
                || plan.ParentPid <= 0 || plan.ParentStartTicks <= 0 || (plan.HelperToken != null && !Regex.IsMatch(plan.HelperToken, @"^[a-f0-9]{32}$", RegexOptions.CultureInvariant)))
                throw new InvalidDataException("更新安装计划校验信息无效。");
            if (plan.Files == null || plan.Files.Count == 0 || plan.Files.Count > ReleaseUpdates.MaxEntries) throw new InvalidDataException("更新文件清单无效。");
            HashSet<string> names = new HashSet<string>(StringComparer.OrdinalIgnoreCase); long total = 0;
            foreach (UpdateFile file in plan.Files) {
                if (file == null || !ReleaseUpdates.Allowed(file.Relative, false) || !names.Add(file.Relative) || file.Length < 0 || file.Length > ReleaseUpdates.MaxEntryBytes
                    || file.Sha256 == null || !Regex.IsMatch(file.Sha256, @"^[a-fA-F0-9]{64}$", RegexOptions.CultureInvariant)) throw new InvalidDataException("更新文件清单包含不安全的条目。");
                total = checked(total + file.Length); if (total > ReleaseUpdates.MaxExpandedBytes) throw new InvalidDataException("更新文件清单大小超过限制。");
            }
            foreach (string required in new string[] { "CodexQuotaMonitor.exe", "package.json", "runtime-provenance.json", "quota_monitor/__main__.py", "runtime/python/python.exe", "runtime/node/node.exe", "node_modules/playwright/package.json", "node_modules/playwright-core/package.json" })
                if (!names.Contains(required)) throw new InvalidDataException("更新文件清单不是完整便携包。");
            return plan;
        }

        static string LockPath(string root) { return Path.Combine(root, "data", "desktop", "update.lock"); }
        static string RecoveryMarker(string root) { return Path.Combine(root, "data", "desktop", "update-recovery-required"); }
        public static bool IsInstalling(string root)
        {
            if (File.Exists(RecoveryMarker(root))) return true;
            string path = LockPath(root);
            if (!File.Exists(path)) return false;
            try {
                ReleaseUpdates.RejectReparse(path);
                using (FileStream check = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite)) { return false; }
            } catch (IOException) { return true; }
            catch (InvalidDataException) { return true; }
            catch (UnauthorizedAccessException) { return true; }
        }

        static FileStream AcquireRootLock(string root)
        {
            string path = LockPath(root); ReleaseUpdates.RejectReparse(path); Directory.CreateDirectory(Path.GetDirectoryName(path));
            return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        }

        static bool Cancelled(UpdatePlan plan)
        { return plan.HelperToken != null && File.Exists(Path.Combine(plan.Work, "cancel-" + plan.HelperToken)); }

        static void WaitForParent(UpdatePlan plan)
        {
            // A reused PID is never treated as the launching app, and no process is killed.
            Process parent;
            try { parent = Process.GetProcessById(plan.ParentPid); }
            catch (ArgumentException) { return; }
            using (parent) {
                try {
                    if (parent.StartTime.ToUniversalTime().Ticks != plan.ParentStartTicks) return;
                    Stopwatch clock = Stopwatch.StartNew();
                    while (!parent.WaitForExit(100)) {
                        if (Cancelled(plan)) throw new IOException("更新已取消，当前程序继续运行。");
                        if (clock.ElapsedMilliseconds >= 60000) throw new IOException("等待程序退出超时，已取消更新。");
                    }
                } catch (InvalidOperationException) { return; }
            }
        }

        static void Status(UpdatePlan plan, string state, string detail)
        {
            // Diagnostics contain only paths, exception types and installer state; never config contents.
            string path = Path.Combine(plan.Work, "result.json"); ReleaseUpdates.RejectReparse(path);
            File.WriteAllText(path, ReleaseUpdates.Serializer(1024 * 1024).Serialize(new { schema_version = 1, state = state, version = plan.Version,
                checked_at = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture), detail = detail }), new UTF8Encoding(false));
        }

        public static int Run(string planPath)
        {
            UpdatePlan plan = null; bool parentExited = false, installed = false, restartAllowed = true;
            try {
                plan = ReadPlan(planPath);
                if (plan.HelperToken == null) throw new InvalidDataException("更新助手启动令牌缺失。");
                using (FileStream rootLock = AcquireRootLock(plan.Root)) {
                    if (Cancelled(plan)) return 1;
                    string ready = Path.Combine(plan.Work, "helper-ready-" + plan.HelperToken); ReleaseUpdates.RejectReparse(ready);
                    File.WriteAllText(ready, plan.HelperToken, new UTF8Encoding(false));
                    WaitForParent(plan); parentExited = true;
                    if (Cancelled(plan)) return 1;
                    using (Mutex mutex = new Mutex(false, ApplicationMutex)) {
                        bool acquired = false;
                        try {
                            try { acquired = mutex.WaitOne(30000); }
                            catch (AbandonedMutexException) { acquired = true; }
                            if (!acquired) throw new IOException("其他程序实例仍在运行，已停止更新。");
                            ApplyCore(planPath, null, true); installed = true;
                        } finally { if (acquired) mutex.ReleaseMutex(); }
                    }
                }
            } catch (Exception ex) {
                restartAllowed = !(ex is RollbackIncompleteException);
                if (plan != null) {
                    // Preserve the transaction's rolled-back/rollback-incomplete result.
                    if (!File.Exists(Path.Combine(plan.Work, "result.json"))) try { Status(plan, "failed", ex.GetType().Name + ": " + ex.Message); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                    if (File.Exists(RecoveryMarker(plan.Root))) restartAllowed = false;
                    if (parentExited && !Cancelled(plan)) {
                        string relative = "data/desktop/updates/" + Path.GetFileName(plan.Work);
                        MessageBox.Show(restartAllowed
                            ? "更新未完成，程序文件已保留或恢复。配置、邮箱名单和历史数据已保留。\n请查看 " + relative + "/result.json 后重试。"
                            : "更新未完成，程序文件需要恢复，已阻止自动启动。配置、邮箱名单和历史数据已保留。\n请按 " + relative + "/recovery.md 恢复程序文件。", "更新未完成", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                }
            }
            // Both the root lock and application mutex are released before restart.
            if (parentExited && restartAllowed && plan != null && !Cancelled(plan)) {
                try {
                    string executable = Path.Combine(plan.Root, "CodexQuotaMonitor.exe"); ReleaseUpdates.RejectReparse(executable);
                    ProcessStartInfo restart = new ProcessStartInfo(executable, plan.ResumeMonitoring ? "" : "--updated-stopped");
                    restart.UseShellExecute = false; restart.WorkingDirectory = plan.Root; restart.CreateNoWindow = true;
                    using (Process process = Process.Start(restart)) { }
                } catch (Exception ex) {
                    try { Status(plan, installed ? "installed-restart-failed" : "failed-restart-failed", ex.GetType().Name); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                    return 1;
                }
            }
            return installed ? 0 : 1;
        }

        internal static void Apply(string planPath, Action<int> afterFile)
        {
            UpdatePlan plan = ReadPlan(planPath);
            using (FileStream rootLock = AcquireRootLock(plan.Root)) ApplyCore(planPath, afterFile, false);
        }

        static void ValidateStage(UpdatePlan plan)
        {
            ReleaseUpdates.VerifyFile(Path.Combine(plan.Work, plan.ArchiveName), plan.ArchiveSize, plan.ArchiveSha256);
            // The downloaded ZIP also binds the complete manifest. Editing both the stage and
            // plan hashes must not detach installed bytes from the verified release archive.
            // Keep this component short for the .NET Framework/Windows path limit.
            string verified;
            do { verified = Path.Combine(plan.Work, "v" + Guid.NewGuid().ToString("N").Substring(0, 4)); } while (Directory.Exists(verified) || File.Exists(verified));
            ReleaseUpdates.RejectReparse(verified); Directory.CreateDirectory(verified);
            List<UpdateFile> archiveFiles = ReleaseUpdates.ExtractArchive(Path.Combine(plan.Work, plan.ArchiveName), verified, plan.Version);
            if (archiveFiles.Count != plan.Files.Count) throw new InvalidDataException("更新文件清单与已验证的压缩包不一致。");
            for (int i = 0; i < archiveFiles.Count; i++) {
                UpdateFile original = archiveFiles[i], planned = plan.Files[i];
                if (original.Relative != planned.Relative || original.Length != planned.Length || !String.Equals(original.Sha256, planned.Sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("更新文件清单与已验证的压缩包不一致。");
            }
            string stage = Path.Combine(plan.Work, "staged"); ReleaseUpdates.RejectReparse(stage);
            foreach (UpdateFile file in plan.Files) {
                ReleaseUpdates.VerifyFile(ReleaseUpdates.Child(stage, file.Relative), file.Length, file.Sha256);
                string target = ReleaseUpdates.Child(plan.Root, file.Relative); ReleaseUpdates.RejectReparse(target);
                if (Directory.Exists(target)) throw new InvalidDataException("程序目标路径与现有目录冲突：" + file.Relative);
            }
            string stagedVersion = ReleaseUpdates.Text(ReleaseUpdates.JsonObject(File.ReadAllText(ReleaseUpdates.Child(stage, "runtime-provenance.json"), Encoding.UTF8), ReleaseUpdates.MaxMetadataBytes), "application_version");
            string packageVersion = ReleaseUpdates.Text(ReleaseUpdates.JsonObject(File.ReadAllText(ReleaseUpdates.Child(stage, "package.json"), Encoding.UTF8), ReleaseUpdates.MaxMetadataBytes), "version");
            if (stagedVersion != plan.Version || packageVersion != plan.Version) throw new InvalidDataException("更新文件版本不匹配安装计划。");
        }

        static void PreflightLocks(UpdatePlan plan, bool wait)
        {
            Stopwatch clock = Stopwatch.StartNew();
            while (true) {
                try {
                    foreach (UpdateFile file in plan.Files) {
                        string path = ReleaseUpdates.Child(plan.Root, file.Relative); ReleaseUpdates.RejectReparse(path);
                        if (File.Exists(path)) using (FileStream check = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                    }
                    return;
                } catch (IOException) {
                    if (!wait || clock.ElapsedMilliseconds >= 30000) throw new IOException("部分程序文件仍在使用中，已停止更新。请关闭采集程序后重试。");
                    Thread.Sleep(200);
                }
            }
        }

        static void AppendJournal(UpdatePlan plan, string state, SavedFile file)
        {
            string journal = Path.Combine(plan.Work, "journal.jsonl"); ReleaseUpdates.RejectReparse(journal);
            File.AppendAllText(journal, ReleaseUpdates.Serializer(1024 * 1024).Serialize(new { state = state, relative = file.Relative, existed = file.Existed,
                backup = file.Backup, time = DateTimeOffset.UtcNow.ToString("o", CultureInfo.InvariantCulture) }) + Environment.NewLine, new UTF8Encoding(false));
        }

        static void ReplaceFrom(string source, string destination, bool existed)
        {
            ReleaseUpdates.RejectReparse(source); ReleaseUpdates.RejectReparse(destination); Directory.CreateDirectory(Path.GetDirectoryName(destination));
            string temporary = Path.Combine(Path.GetDirectoryName(destination), ".quota-update-" + Guid.NewGuid().ToString("N") + ".tmp");
            ReleaseUpdates.RejectReparse(temporary);
            try {
                File.Copy(source, temporary, false);
                if (existed) File.Replace(temporary, destination, null);
                else File.Move(temporary, destination);
            } finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        static void MarkRecovery(UpdatePlan plan)
        {
            string marker = RecoveryMarker(plan.Root); ReleaseUpdates.RejectReparse(marker);
            string recovery = Path.Combine(plan.Work, "recovery.md"); ReleaseUpdates.RejectReparse(recovery);
            File.WriteAllText(recovery, "# 程序文件恢复说明\n\n此更新只替换程序文件，未改写个人配置、邮箱名单、浏览器资料和历史数据库。\n\n"
                + "1. 关闭托盘程序、采集程序和更新助手。\n2. 保留本目录中的 plan.json、journal.jsonl、result.json 和 backup。\n"
                + "3. 将 backup 目录中的文件按相对路径复制回程序原目录；请勿把整个 backup 目录覆盖到 data 或配置目录。\n"
                + "4. journal.jsonl 中 existed=false 的条目是此轮新增程序文件。核对 plan.json 后，只移除此轮已写入且仍与计划哈希一致的新增文件。\n"
                + "5. 核对已恢复的程序版本与文件后，移除 data/desktop/update-recovery-required 标记，再启动程序。\n\n"
                + "如果无法确认恢复状态，请保留全部证据和备份并请求维护者协助；不要删除配置、邮箱名单或历史数据库。\n", new UTF8Encoding(false));
            using (FileStream output = new FileStream(marker, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                byte[] bytes = Encoding.UTF8.GetBytes(ReleaseUpdates.Serializer(1024 * 1024).Serialize(new {
                    schema_version = 1, plan = "updates/" + Path.GetFileName(plan.Work) + "/plan.json", recovery = "updates/" + Path.GetFileName(plan.Work) + "/recovery.md", version = plan.Version }));
                output.Write(bytes, 0, bytes.Length); output.Flush(true);
            }
        }

        static void ClearRecovery(UpdatePlan plan)
        { string marker = RecoveryMarker(plan.Root); ReleaseUpdates.RejectReparse(marker); File.Delete(marker); }

        static void ApplyCore(string planPath, Action<int> afterFile, bool waitForLocks)
        {
            UpdatePlan plan = ReadPlan(planPath);
            string complete = Path.Combine(plan.Work, "completed"), journal = Path.Combine(plan.Work, "journal.jsonl");
            ReleaseUpdates.RejectReparse(complete); ReleaseUpdates.RejectReparse(journal);
            if (File.Exists(complete) || File.Exists(journal)) throw new InvalidDataException("更新计划已执行或中断，请重新准备更新。");
            if (File.Exists(RecoveryMarker(plan.Root))) throw new InvalidDataException("程序存在未完成的更新，请先按备份说明恢复。");
            if (ReleaseUpdates.CompareVersions(plan.Version, ReleaseUpdates.CurrentVersion(plan.Root)) <= 0) throw new InvalidDataException("安装计划不再对应更高版本，请重新检查更新。");
            ValidateStage(plan); PreflightLocks(plan, waitForLocks);
            List<SavedFile> saved = new List<SavedFile>(), changed = new List<SavedFile>();
            string backupRoot = Path.Combine(plan.Work, "backup"); ReleaseUpdates.RejectReparse(backupRoot); Directory.CreateDirectory(backupRoot);
            // Back up every replaced file before the first application file changes.
            foreach (UpdateFile file in plan.Files) {
                string destination = ReleaseUpdates.Child(plan.Root, file.Relative), backup = ReleaseUpdates.Child(backupRoot, file.Relative);
                ReleaseUpdates.RejectReparse(destination); ReleaseUpdates.RejectReparse(backup);
                SavedFile item = new SavedFile { Relative = file.Relative, Backup = backup, Existed = File.Exists(destination) };
                if (item.Existed) {
                    Directory.CreateDirectory(Path.GetDirectoryName(backup)); File.Copy(destination, backup, false);
                    ReleaseUpdates.VerifyFile(backup, new FileInfo(destination).Length, ReleaseUpdates.FileSha256(destination));
                }
                saved.Add(item);
            }
            Status(plan, "installing", "Application file replacement only; personal files are retained.");
            MarkRecovery(plan);
            try {
                for (int i = 0; i < saved.Count; i++) {
                    SavedFile file = saved[i]; AppendJournal(plan, "before", file);
                    string staged = ReleaseUpdates.Child(Path.Combine(plan.Work, "staged"), file.Relative), target = ReleaseUpdates.Child(plan.Root, file.Relative);
                    ReleaseUpdates.VerifyFile(staged, plan.Files[i].Length, plan.Files[i].Sha256);
                    ReplaceFrom(staged, target, file.Existed); changed.Add(file);
                    AppendJournal(plan, "applied", file);
                    if (afterFile != null) afterFile(i + 1);
                }
                foreach (UpdateFile file in plan.Files) ReleaseUpdates.VerifyFile(ReleaseUpdates.Child(plan.Root, file.Relative), file.Length, file.Sha256);
                File.WriteAllText(complete, plan.Version, new UTF8Encoding(false)); Status(plan, "installed", "Application files updated; personal configuration and history retained."); ClearRecovery(plan);
            } catch (Exception original) {
                List<string> failures = new List<string>();
                for (int i = changed.Count - 1; i >= 0; i--) {
                    SavedFile file = changed[i]; string target = ReleaseUpdates.Child(plan.Root, file.Relative);
                    try {
                        ReleaseUpdates.RejectReparse(target);
                        if (file.Existed) ReplaceFrom(file.Backup, target, File.Exists(target));
                        else if (File.Exists(target)) File.Delete(target);
                        AppendJournal(plan, "restored", file);
                    } catch (Exception ex) { failures.Add(file.Relative + " (" + ex.GetType().Name + ")"); }
                }
                if (File.Exists(complete)) File.Delete(complete);
                if (failures.Count > 0) {
                    Status(plan, "rollback-incomplete", String.Join(", ", failures.ToArray()));
                    throw new RollbackIncompleteException("更新失败，部分程序文件未能恢复。备份保留在 " + backupRoot + "。", original);
                }
                Status(plan, "rolled-back", original.GetType().Name); ClearRecovery(plan); throw;
            }
        }
    }
}
