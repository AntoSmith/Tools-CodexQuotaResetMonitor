using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace CodexQuotaDesktop
{
    internal sealed class ReleaseUpdateInfo
    {
        public string CurrentVersion, Version, ReleaseUrl, AssetUrl, AssetName, Sha256;
        public long Size;
        public bool Available;
    }

    internal sealed class UpdateFile
    {
        public string Relative, Sha256;
        public long Length;
    }

    internal sealed class UpdatePlan
    {
        public int SchemaVersion, ParentPid;
        public long ParentStartTicks;
        public string Root, Work, Version, ArchiveName, ArchiveSha256, HelperToken;
        public long ArchiveSize;
        public bool ResumeMonitoring;
        public List<UpdateFile> Files;
    }

    internal static class ReleaseUpdates
    {
        public const string RepositoryUrl = "https://github.com/AntoSmith/Tools-CodexQuotaResetMonitor";
        public const string LatestEndpoint = "https://api.github.com/repos/AntoSmith/Tools-CodexQuotaResetMonitor/releases/latest";
        public const long MaxDownloadBytes = 160L * 1024 * 1024;
        public const int MaxMetadataBytes = 2 * 1024 * 1024;
        internal const long MaxExpandedBytes = 700L * 1024 * 1024;
        internal const long MaxEntryBytes = 160L * 1024 * 1024;
        internal const int MaxEntries = 12000;
        static readonly Regex VersionPattern = new Regex(@"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", RegexOptions.CultureInvariant);
        static readonly Regex DigestPattern = new Regex(@"^[a-fA-F0-9]{64}$", RegexOptions.CultureInvariant);
        static readonly HashSet<string> ApplicationFiles = new HashSet<string>(new string[] {
            "CodexQuotaMonitor.exe", "README.md", "docs/USAGE.md", "docs/PUBLISH.md", "config.browser.example.toml", "LoginX.cmd", "package.json", "scripts/run.ps1",
            "runtime-provenance.json", "THIRD_PARTY_NOTICES.txt",
            "quota_monitor/__init__.py", "quota_monitor/__main__.py", "quota_monitor/background_browser.py", "quota_monitor/browser.py", "quota_monitor/classify.py", "quota_monitor/cli.py", "quota_monitor/config.py",
            "quota_monitor/conversation.py", "quota_monitor/delivery.py", "quota_monitor/desktop_settings.py", "quota_monitor/email_html.py", "quota_monitor/email_message.py", "quota_monitor/email_preview.py",
            "quota_monitor/email_test.py", "quota_monitor/history.py", "quota_monitor/notify.py", "quota_monitor/reset_feed.py", "quota_monitor/divin_feed.py", "quota_monitor/source_notes.py", "quota_monitor/service.py",
            "quota_monitor/signal_email.py", "quota_monitor/store.py", "quota_monitor/subscription.py", "quota_monitor/subscription_test.py", "quota_monitor/xapi.py", "quota_monitor/browser_collect.cjs", "quota_monitor/browser_dom.cjs",
            "quota_monitor/browser_environment.cjs", "quota_monitor/browser_errors.cjs", "quota_monitor/browser_login.cjs",
            "assets/quota-monitor.ico", "assets/quota-monitor.png", "assets/quota-monitor.svg", "assets/feature-overview.svg", "assets/screenshots/dashboard.png", "assets/screenshots/alert.png", "assets/screenshots/history.png",
            "assets/screenshots/subscribers.png", "assets/screenshots/email-demo.png", "assets/screenshots/mail-settings.png", "assets/email/celebrate.gif", "assets/email/celebrate.png", "assets/email/neutral.gif",
            "assets/email/neutral.png", "assets/email/uncertain.gif", "assets/email/uncertain.png", "assets/email/README.md", "licenses/Python.txt", "licenses/Node.txt", "licenses/Playwright.txt", "licenses/Playwright-core.txt", "licenses/README.txt"
        }, StringComparer.Ordinal);
        static readonly HashSet<string> PrivateParts = new HashSet<string>(new string[] {
            "data", "logs", ".git", ".codex", ".agents", "__pycache__", "profiles", "profile", "user_data", "user-data", "browser-profile", "browser-profiles", "browserprofile", "github-export"
        }, StringComparer.OrdinalIgnoreCase);
        static readonly HashSet<string> PrivateNames = new HashSet<string>(new string[] {
            ".npmrc", "python-path.txt", "node-path.txt", "cookies", "cookies.json", "storage-state.json", "login-state.json"
        }, StringComparer.OrdinalIgnoreCase);

        internal static JavaScriptSerializer Serializer(int limit)
        { return new JavaScriptSerializer { MaxJsonLength = limit, RecursionLimit = 64 }; }

        internal static string StableVersion(string version)
        {
            if (version == null || version.Length > 100 || !VersionPattern.IsMatch(version)) throw new InvalidDataException("版本号不是正式稳定版本。");
            return version;
        }

        public static int CompareVersions(string left, string right)
        {
            string[] a = StableVersion(left).Split('.'), b = StableVersion(right).Split('.');
            for (int i = 0; i < a.Length; i++) {
                int result = a[i].Length.CompareTo(b[i].Length);
                if (result == 0) result = String.CompareOrdinal(a[i], b[i]);
                if (result != 0) return Math.Sign(result);
            }
            return 0;
        }

        internal static Dictionary<string, object> JsonObject(string text, int limit)
        {
            if (text == null || Encoding.UTF8.GetByteCount(text) > limit) throw new InvalidDataException("更新元数据超过限制。");
            try {
                Dictionary<string, object> value = Serializer(limit).DeserializeObject(text) as Dictionary<string, object>;
                if (value == null) throw new InvalidDataException("更新元数据格式无效。");
                return value;
            } catch (ArgumentException ex) { throw new InvalidDataException("更新元数据格式无效。", ex); }
        }

        internal static string Text(Dictionary<string, object> data, string key)
        { object value; if (!data.TryGetValue(key, out value) || !(value is string)) throw new InvalidDataException("更新元数据缺少 " + key + "。"); return (string)value; }
        static bool Boolean(Dictionary<string, object> data, string key)
        { object value; if (!data.TryGetValue(key, out value) || !(value is bool)) throw new InvalidDataException("更新元数据缺少 " + key + "。"); return (bool)value; }
        static long Number(Dictionary<string, object> data, string key)
        {
            object value; if (!data.TryGetValue(key, out value) || (!(value is int) && !(value is long))) throw new InvalidDataException("更新元数据中的文件大小无效。");
            return Convert.ToInt64(value, CultureInfo.InvariantCulture);
        }

        public static string CurrentVersion(string root)
        {
            root = CanonicalRoot(root);
            string provenance = Path.Combine(root, "runtime-provenance.json"), package = Path.Combine(root, "package.json");
            string path = File.Exists(provenance) ? provenance : package;
            RejectReparse(path);
            if (!File.Exists(path) || new FileInfo(path).Length > MaxMetadataBytes) throw new InvalidDataException("无法读取当前程序版本，请保留完整程序目录。");
            return StableVersion(Text(JsonObject(File.ReadAllText(path, Encoding.UTF8), MaxMetadataBytes), path == provenance ? "application_version" : "version"));
        }

        public static ReleaseUpdateInfo Check(string root)
        {
            string current = CurrentVersion(root);
            Stopwatch clock = new StopwatchStarted();
            using (HttpWebResponse response = OpenTrusted(new Uri(LatestEndpoint), clock, 40))
            using (Stream stream = response.GetResponseStream()) {
                byte[] bytes = ReadBounded(stream, MaxMetadataBytes, clock, 40);
                ReleaseUpdateInfo info = ParseMetadata(root, new UTF8Encoding(false, true).GetString(bytes));
                if (info.CurrentVersion != current) throw new InvalidDataException("检查期间程序版本已变化，请重新检查。");
                return info;
            }
        }

        internal static ReleaseUpdateInfo ParseMetadata(string root, string json)
        {
            Dictionary<string, object> data = JsonObject(json, MaxMetadataBytes);
            if (Boolean(data, "draft") || Boolean(data, "prerelease")) throw new InvalidDataException("最新发布不是正式稳定版本。");
            string tag = Text(data, "tag_name");
            if (!tag.StartsWith("v", StringComparison.Ordinal)) throw new InvalidDataException("发布标签格式无效。");
            string version = StableVersion(tag.Substring(1));
            string assetName = AssetName(version), releaseUrl = Text(data, "html_url");
            if (releaseUrl != RepositoryUrl + "/releases/tag/" + tag) throw new InvalidDataException("发布页面不属于指定项目。");
            RequireTrusted(new Uri(releaseUrl));
            object assetsValue;
            if (!data.TryGetValue("assets", out assetsValue) || !(assetsValue is object[])) throw new InvalidDataException("发布未包含可下载的便携包。");
            Dictionary<string, object> match = null;
            foreach (object item in (object[])assetsValue) {
                Dictionary<string, object> asset = item as Dictionary<string, object>;
                if (asset == null) throw new InvalidDataException("发布附件格式无效。");
                if (Text(asset, "name") != assetName) continue;
                if (match != null) throw new InvalidDataException("发布包含重复便携包。");
                match = asset;
            }
            if (match == null) throw new InvalidDataException("正式发布缺少 Windows x64 便携包。");
            string url = Text(match, "browser_download_url"), digest = Text(match, "digest");
            if (url != RepositoryUrl + "/releases/download/" + tag + "/" + assetName) throw new InvalidDataException("便携包下载地址不属于指定项目。");
            RequireTrusted(new Uri(url));
            if (!digest.StartsWith("sha256:", StringComparison.Ordinal) || !DigestPattern.IsMatch(digest.Substring(7))) throw new InvalidDataException("正式发布未提供有效 SHA256，无法安全升级。");
            long size = Number(match, "size");
            if (size <= 0 || size > MaxDownloadBytes) throw new InvalidDataException("便携包大小超过限制或无效。");
            string current = CurrentVersion(root);
            return new ReleaseUpdateInfo { CurrentVersion = current, Version = version, ReleaseUrl = releaseUrl, AssetUrl = url, AssetName = assetName,
                Sha256 = digest.Substring(7).ToLowerInvariant(), Size = size, Available = CompareVersions(version, current) > 0 };
        }

        static string AssetName(string version) { return "CodexQuotaMonitor-v" + StableVersion(version) + "-windows-x64.zip"; }
        internal static void RequireTrusted(Uri uri)
        {
            if (uri == null || !uri.IsAbsoluteUri || uri.Scheme != Uri.UriSchemeHttps || !uri.IsDefaultPort || uri.UserInfo.Length != 0 || uri.Fragment.Length != 0)
                throw new InvalidDataException("更新地址必须使用可信的 HTTPS 服务。");
            string host = uri.DnsSafeHost;
            if (host != "github.com" && host != "api.github.com" && host != "release-assets.githubusercontent.com" && host != "objects.githubusercontent.com")
                throw new InvalidDataException("更新地址或跳转不属于可信的 GitHub 服务。");
        }

        sealed class StopwatchStarted : Stopwatch { public StopwatchStarted() { Start(); } }
        static void Deadline(Stopwatch clock, int seconds)
        { if (clock.Elapsed.TotalSeconds >= seconds) throw new IOException("更新请求超时，请稍后重试。"); }
        static HttpWebResponse OpenTrusted(Uri uri, Stopwatch clock, int seconds)
        {
            // Follow redirects ourselves so an intermediate hop never receives a request on an untrusted host.
            ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072; // TLS 1.2 on .NET Framework.
            for (int hop = 0; hop <= 5; hop++) {
                RequireTrusted(uri); Deadline(clock, seconds);
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(uri);
                request.Method = "GET"; request.AllowAutoRedirect = false; request.Timeout = 20000; request.ReadWriteTimeout = 20000;
                request.UserAgent = "CodexQuotaMonitor-ReleaseUpdater/1"; request.Accept = "application/vnd.github+json";
                request.Credentials = null; request.UseDefaultCredentials = false; request.CookieContainer = null;
                request.AutomaticDecompression = DecompressionMethods.None;
                HttpWebResponse response;
                try { response = (HttpWebResponse)request.GetResponse(); }
                catch (WebException ex) {
                    HttpWebResponse error = ex.Response as HttpWebResponse;
                    if (error != null) { int status = (int)error.StatusCode; error.Dispose(); throw new IOException(status == 403 || status == 429 ? "GitHub 请求受到限制，请稍后重试。" : "GitHub 更新请求失败（HTTP " + status + "）。", ex); }
                    throw new IOException("无法连接 GitHub 更新服务，请检查网络后重试。", ex);
                }
                int code = (int)response.StatusCode;
                if (code == 301 || code == 302 || code == 303 || code == 307 || code == 308) {
                    string next = response.Headers[HttpResponseHeader.Location]; response.Dispose();
                    if (String.IsNullOrEmpty(next)) throw new InvalidDataException("更新下载跳转地址缺失。");
                    uri = new Uri(uri, next); continue;
                }
                if (code != 200) { response.Dispose(); throw new IOException("GitHub 返回了无效下载状态。"); }
                return response;
            }
            throw new InvalidDataException("更新下载跳转次数超过限制。");
        }

        static byte[] ReadBounded(Stream source, long maximum, Stopwatch clock, int seconds)
        {
            using (MemoryStream target = new MemoryStream()) {
                CopyBounded(source, target, maximum, clock, seconds, null, maximum);
                return target.ToArray();
            }
        }
        static long CopyBounded(Stream source, Stream target, long maximum, Stopwatch clock, int seconds, Action<int> progress, long total)
        {
            byte[] buffer = new byte[65536]; long written = 0; int last = -1, length;
            while ((length = source.Read(buffer, 0, buffer.Length)) > 0) {
                if (clock != null) Deadline(clock, seconds);
                written += length; if (written > maximum) throw new InvalidDataException("更新文件超过大小限制。");
                target.Write(buffer, 0, length);
                int value = (int)Math.Min(95, written * 95 / Math.Max(1, total));
                if (progress != null && value != last) { progress(value); last = value; }
            }
            if (clock != null) Deadline(clock, seconds);
            return written;
        }

        static void ValidateInfo(string root, ReleaseUpdateInfo info)
        {
            if (info == null || !info.Available || CompareVersions(info.Version, CurrentVersion(root)) <= 0 || info.CurrentVersion != CurrentVersion(root)) throw new InvalidDataException("此更新已不适用于当前版本，请重新检查。");
            if (info.AssetName != AssetName(info.Version) || info.ReleaseUrl != RepositoryUrl + "/releases/tag/v" + info.Version || info.AssetUrl != RepositoryUrl + "/releases/download/v" + info.Version + "/" + info.AssetName)
                throw new InvalidDataException("更新元数据不匹配指定项目。");
            RequireTrusted(new Uri(info.AssetUrl));
            if (info.Sha256 == null || !DigestPattern.IsMatch(info.Sha256) || info.Size <= 0 || info.Size > MaxDownloadBytes) throw new InvalidDataException("更新校验信息无效。");
        }

        static string NewWork(string root)
        {
            root = CanonicalRoot(root);
            string parent = Path.Combine(root, "data", "desktop", "updates"); RejectReparse(parent); Directory.CreateDirectory(parent);
            string work = Path.Combine(parent, "update-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(work); return work;
        }

        public static string Prepare(string root, ReleaseUpdateInfo info, Action<int> progress)
        {
            root = CanonicalRoot(root); ValidateInfo(root, info);
            string work = NewWork(root), archive = Path.Combine(work, info.AssetName);
            Stopwatch clock = new StopwatchStarted();
            using (HttpWebResponse response = OpenTrusted(new Uri(info.AssetUrl), clock, 300)) {
                if (response.ContentLength > 0 && response.ContentLength != info.Size) throw new InvalidDataException("下载文件大小与发布信息不符。");
                using (Stream source = response.GetResponseStream())
                using (FileStream target = new FileStream(archive, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    if (CopyBounded(source, target, info.Size, clock, 300, progress, info.Size) != info.Size) throw new InvalidDataException("下载未完成或文件大小不符。");
            }
            return BuildPlan(root, info, archive, work, progress);
        }

        internal static string PrepareArchive(string root, ReleaseUpdateInfo info, string archive, Action<int> progress)
        {
            root = CanonicalRoot(root); ValidateInfo(root, info); RejectReparse(archive); VerifyFile(archive, info.Size, info.Sha256);
            string work = NewWork(root), copy = Path.Combine(work, info.AssetName); File.Copy(archive, copy, false);
            return BuildPlan(root, info, copy, work, progress);
        }

        static string BuildPlan(string root, ReleaseUpdateInfo info, string archive, string work, Action<int> progress)
        {
            VerifyFile(archive, info.Size, info.Sha256);
            string stage = Path.Combine(work, "staged"); Directory.CreateDirectory(stage);
            List<UpdateFile> files = ExtractArchive(archive, stage, info.Version);
            using (Process self = Process.GetCurrentProcess()) {
                UpdatePlan plan = new UpdatePlan { SchemaVersion = 1, Root = root, Work = work, Version = info.Version, ArchiveName = info.AssetName,
                    ArchiveSha256 = info.Sha256, ArchiveSize = info.Size, ParentPid = self.Id, ParentStartTicks = self.StartTime.ToUniversalTime().Ticks,
                    ResumeMonitoring = true, Files = files };
                string path = Path.Combine(work, "plan.json"); WritePlan(path, plan);
                if (progress != null) progress(100);
                return path;
            }
        }

        internal static List<UpdateFile> ExtractArchive(string archive, string stage, string version)
        {
            string prefix = Path.GetFileNameWithoutExtension(AssetName(version));
            List<KeyValuePair<ZipArchiveEntry, string>> selected = new List<KeyValuePair<ZipArchiveEntry, string>>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            HashSet<string> filePaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            List<UpdateFile> files = new List<UpdateFile>(); long total = 0;
            using (FileStream source = new FileStream(archive, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (ZipArchive zip = new ZipArchive(source, ZipArchiveMode.Read)) {
                if (zip.Entries.Count > MaxEntries) throw new InvalidDataException("更新压缩包条目数量超过限制。");
                foreach (ZipArchiveEntry entry in zip.Entries) {
                    bool directory = entry.FullName.EndsWith("/", StringComparison.Ordinal);
                    string path = SafeRelative(directory ? entry.FullName.Substring(0, entry.FullName.Length - 1) : entry.FullName);
                    if (!seen.Add(path)) throw new InvalidDataException("更新压缩包存在重复或大小写冲突路径。");
                    int mode = (entry.ExternalAttributes >> 16) & 0xF000;
                    if ((mode != 0 && mode != 0x8000 && mode != 0x4000) || (entry.ExternalAttributes & 0x400) != 0) throw new InvalidDataException("更新压缩包包含链接或重解析路径。");
                    if (path == prefix && directory) continue;
                    if (!path.StartsWith(prefix + "/", StringComparison.Ordinal)) throw new InvalidDataException("更新压缩包目录与发布版本不符。");
                    string relative = path.Substring(prefix.Length + 1);
                    if (!Allowed(relative, directory)) throw new InvalidDataException("更新压缩包包含未批准的程序文件：" + relative);
                    if (directory) { if (entry.Length != 0) throw new InvalidDataException("更新压缩包目录条目无效。"); continue; }
                    if (entry.Length < 0 || entry.Length > MaxEntryBytes) throw new InvalidDataException("更新压缩包单个文件过大。");
                    total = checked(total + entry.Length); if (total > MaxExpandedBytes) throw new InvalidDataException("更新压缩包解压大小超过限制。");
                    filePaths.Add(relative); selected.Add(new KeyValuePair<ZipArchiveEntry, string>(entry, relative));
                }
                // Also reject file/directory aliases such as runtime/python and runtime/python/python.exe.
                foreach (string name in filePaths) {
                    int slash = name.LastIndexOf('/');
                    while (slash >= 0) { if (filePaths.Contains(name.Substring(0, slash))) throw new InvalidDataException("更新压缩包存在文件与目录冲突。"); slash = name.LastIndexOf('/', slash - 1); }
                }
                foreach (string required in new string[] { "CodexQuotaMonitor.exe", "package.json", "runtime-provenance.json", "quota_monitor/__main__.py", "runtime/python/python.exe", "runtime/node/node.exe", "node_modules/playwright/package.json", "node_modules/playwright-core/package.json" })
                    if (!filePaths.Contains(required)) throw new InvalidDataException("更新压缩包不是完整便携包：缺少 " + required);
                selected.Sort(delegate(KeyValuePair<ZipArchiveEntry, string> a, KeyValuePair<ZipArchiveEntry, string> b) { return String.CompareOrdinal(a.Value, b.Value); });
                foreach (KeyValuePair<ZipArchiveEntry, string> pair in selected) {
                    string target = Child(stage, pair.Value); RejectReparse(target); Directory.CreateDirectory(Path.GetDirectoryName(target));
                    using (Stream input = pair.Key.Open())
                    using (FileStream output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        if (CopyBounded(input, output, pair.Key.Length, null, 0, null, 1) != pair.Key.Length) throw new InvalidDataException("更新压缩包内容长度不符。");
                    files.Add(new UpdateFile { Relative = pair.Value, Length = pair.Key.Length, Sha256 = FileSha256(target) });
                }
            }
            string stagedVersion = StableVersion(Text(JsonObject(File.ReadAllText(Child(stage, "runtime-provenance.json"), Encoding.UTF8), MaxMetadataBytes), "application_version"));
            string packageVersion = StableVersion(Text(JsonObject(File.ReadAllText(Child(stage, "package.json"), Encoding.UTF8), MaxMetadataBytes), "version"));
            if (stagedVersion != version || packageVersion != version) throw new InvalidDataException("便携包内部版本与发布版本不符。");
            return files;
        }

        internal static string SafeRelative(string path)
        {
            if (String.IsNullOrEmpty(path) || path.Length > 220 || path.IndexOf('\\') >= 0 || Path.IsPathRooted(path)) throw new InvalidDataException("更新压缩包路径无效。");
            foreach (string part in path.Split('/')) {
                if (String.IsNullOrEmpty(part) || part == "." || part == ".." || part.EndsWith(".", StringComparison.Ordinal) || part.EndsWith(" ", StringComparison.Ordinal)
                    || Regex.IsMatch(part, "[<>:\"|?*\\x00-\\x1f]", RegexOptions.CultureInvariant)
                    || Regex.IsMatch(part.Split('.')[0], @"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
                    throw new InvalidDataException("更新压缩包包含不安全的 Windows 路径。");
            }
            return path;
        }

        internal static bool Allowed(string relative, bool directory)
        {
            SafeRelative(relative);
            string[] parts = relative.Split('/');
            foreach (string part in parts) {
                if (PrivateParts.Contains(part) || PrivateNames.Contains(part) || Regex.IsMatch(part, @"^config.*\.local\.toml(?:\.|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)
                    || Regex.IsMatch(part, @"\.(db|sqlite|sqlite3|log|pyc)(?:-(wal|shm))?$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)) return false;
            }
            if (directory) {
                foreach (string known in ApplicationFiles) if (known.StartsWith(relative + "/", StringComparison.Ordinal)) return true;
                return relative == "quota_monitor" || relative == "assets" || relative.StartsWith("assets/", StringComparison.Ordinal)
                    || relative == "runtime" || relative == "runtime/python" || relative == "runtime/node" || relative == "node_modules"
                    || relative == "node_modules/playwright" || relative == "node_modules/playwright-core"
                    || relative.StartsWith("node_modules/playwright/", StringComparison.Ordinal) || relative.StartsWith("node_modules/playwright-core/", StringComparison.Ordinal);
            }
            if (ApplicationFiles.Contains(relative)) return true;
            if (parts.Length == 2 && parts[0] == "quota_monitor" && Regex.IsMatch(parts[1], @"^[A-Za-z_][A-Za-z0-9_]*\.(py|cjs)$", RegexOptions.CultureInvariant)) return true;
            if (parts.Length >= 2 && parts[0] == "assets" && Regex.IsMatch(parts[parts.Length - 1], @"\.(png|ico|svg|gif)$", RegexOptions.CultureInvariant)) return true;
            if (parts.Length == 3 && parts[0] == "runtime" && parts[1] == "node") return parts[2] == "node.exe" || parts[2] == "LICENSE";
            if (parts.Length == 3 && parts[0] == "runtime" && parts[1] == "python") {
                string name = parts[2];
                return name == "python.exe" || name == "pythonw.exe" || name == "python.cat" || name == "LICENSE.txt" || name == "sitecustomize.py"
                    || Regex.IsMatch(name, @"^[A-Za-z0-9_\-]+\.(dll|pyd|zip)$", RegexOptions.CultureInvariant)
                    || Regex.IsMatch(name, @"^python[0-9]+\._pth$", RegexOptions.CultureInvariant);
            }
            return parts.Length > 2 && parts[0] == "node_modules" && (parts[1] == "playwright" || parts[1] == "playwright-core");
        }

        internal static string CanonicalRoot(string root)
        {
            if (String.IsNullOrEmpty(root) || !Path.IsPathRooted(root)) throw new InvalidDataException("程序目录必须是绝对路径。");
            root = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (!Directory.Exists(root) || root == Path.GetPathRoot(root).TrimEnd('\\', '/')) throw new InvalidDataException("程序目录不存在或无效。");
            RejectReparse(root); return root;
        }
        internal static string Child(string root, string relative)
        {
            relative = SafeRelative(relative);
            string full = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)));
            if (!full.StartsWith(Path.GetFullPath(root).TrimEnd('\\', '/') + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("更新路径离开程序目录。");
            return full;
        }
        internal static void RejectReparse(string path)
        {
            string current = Path.GetFullPath(path);
            while (!String.IsNullOrEmpty(current)) {
                try { if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException("更新路径包含链接或重解析目录，已停止更新。"); }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                string parent = Path.GetDirectoryName(current); if (parent == current) break; current = parent;
            }
        }

        public static string FileSha256(string path)
        {
            using (SHA256 sha = SHA256.Create())
            using (FileStream input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
        }
        internal static void VerifyFile(string path, long length, string digest)
        {
            RejectReparse(path);
            if (digest == null || !DigestPattern.IsMatch(digest) || !File.Exists(path) || new FileInfo(path).Length != length || !String.Equals(FileSha256(path), digest, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("更新文件大小或 SHA256 校验失败，已停止更新。");
        }

        internal static void WritePlan(string path, UpdatePlan plan)
        { RejectReparse(path); File.WriteAllText(path, Serializer(8 * 1024 * 1024).Serialize(plan), new UTF8Encoding(false)); }

        public static void LaunchInstaller(string root, string planPath)
        { LaunchInstaller(root, planPath, true); }
        public static void LaunchInstaller(string root, string planPath, bool resumeMonitoring)
        {
            root = CanonicalRoot(root); UpdatePlan plan = UpdateInstaller.ReadPlan(planPath);
            if (!String.Equals(plan.Root, root, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("安装计划不属于当前程序目录。");
            using (Process parent = Process.GetCurrentProcess()) { plan.ParentPid = parent.Id; plan.ParentStartTicks = parent.StartTime.ToUniversalTime().Ticks; }
            plan.ResumeMonitoring = resumeMonitoring; plan.HelperToken = Guid.NewGuid().ToString("N"); WritePlan(planPath, plan);
            string source = Assembly.GetExecutingAssembly().Location, helper = Path.Combine(plan.Work, "update-helper-" + plan.HelperToken + ".exe");
            RejectReparse(source); RejectReparse(helper); File.Copy(source, helper, false);
            string ready = Path.Combine(plan.Work, "helper-ready-" + plan.HelperToken);
            ProcessStartInfo start = new ProcessStartInfo(helper, "--apply-update \"" + Path.GetFullPath(planPath) + "\"");
            start.UseShellExecute = false; start.CreateNoWindow = true; start.WorkingDirectory = root;
            using (Process process = Process.Start(start)) {
                Stopwatch clock = new StopwatchStarted();
                while (clock.ElapsedMilliseconds < 5000) {
                    if (process.HasExited) throw new IOException("更新助手启动失败，程序保持运行，请重试。");
                    if (File.Exists(ready)) { RejectReparse(ready); if (File.ReadAllText(ready) == plan.HelperToken) return; }
                    Thread.Sleep(50);
                }
                // Cancel only our newly launched helper; it has not been allowed to change the application yet.
                File.WriteAllText(Path.Combine(plan.Work, "cancel-" + plan.HelperToken), plan.HelperToken, new UTF8Encoding(false));
                throw new IOException("更新助手未及时就绪，程序保持运行，请重试。");
            }
        }
    }
}
