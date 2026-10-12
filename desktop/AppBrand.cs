using System;
using System.Drawing;
using System.IO;
using System.Reflection;

namespace CodexQuotaDesktop
{
    internal static class AppBrand
    {
        public const string Name = "Codex 额度重置消息监控";
        // Shared for the process lifetime, just like the former SystemIcons instance.
        public static readonly Icon Icon = LoadIcon();
        static Icon LoadIcon()
        {
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("QuotaMonitor.AppIcon.ico"))
            {
                if (resource == null) throw new InvalidOperationException("应用图标未嵌入，请重新构建程序。");
                using (Icon source = new Icon(resource, new Size(32, 32))) return (Icon)source.Clone();
            }
        }
    }
}
