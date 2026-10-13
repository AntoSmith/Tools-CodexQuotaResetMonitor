param([string]$Output = 'data/desktop/update-ui-verification')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$folder = Join-Path $root $Output
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$runner = @'
using System;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using CodexQuotaDesktop;
class UpdateUiTest {
    static void Check(bool value, string why) { if (!value) throw new Exception(why); }
    static void Wait(Task task) { DateTime deadline=DateTime.UtcNow.AddSeconds(10); while(!task.IsCompleted && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(10); } task.GetAwaiter().GetResult(); }
    [STAThread] static int Main(string[] args) {
        try {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            int checks=0, preparations=0, launches=0, quits=0;
            string block="";
            var release=new ReleaseUpdateInfo {CurrentVersion="0.2.2",Version="0.2.3",Available=true, ReleaseUrl="https://github.com/AntoSmith/Tools-CodexQuotaResetMonitor/releases/tag/v0.2.3"};
            using(var form=new UpdateWindow(args[0],delegate {quits++;},delegate {return block;},
                delegate {checks++; return release;},delegate(ReleaseUpdateInfo info,Action<int> progress) {preparations++;progress(85);return "synthetic-plan";},
                delegate(string path) {Check(path=="synthetic-plan","Plan missing"); launches++;})) {
                form.ShowInTaskbar=false;form.StartPosition=FormStartPosition.Manual;form.Location=new Point(-16000,-16000);form.Show();
                Check(!form.InstallButton.Enabled,"Install before checked release");
                Wait(form.CheckAsync());
                Check(checks==1&&form.InstallButton.Enabled&&form.Status.Text.Contains("0.2.3"),"Available version absent");
                Check(preparations==0&&launches==0,"Check installed without choice");
                using(var bmp=new Bitmap(form.Width,form.Height)) {form.DrawToBitmap(bmp,new Rectangle(0,0,form.Width,form.Height));bmp.Save(Path.Combine(args[0],"update-window.png"));}
                block="请先保存设置";Wait(form.InstallAsync());
                Check(preparations==0&&launches==0&&quits==0&&form.Status.Text.Contains(block),"Dirty settings upgraded");
                block="";Wait(form.InstallAsync());
                Check(preparations==1&&launches==1&&quits==1,"Chosen upgrade was not launched once");
            }
            using(var failure=new UpdateWindow(args[0],delegate {quits++;},null,delegate {throw new IOException("offline");})) {
                failure.CreateControl();Wait(failure.CheckAsync());
                Check(!failure.InstallButton.Enabled&&failure.CheckButton.Enabled&&failure.Status.Text.Contains("失败"),"Network failure disabled retry");
            }
            bool savedStartup=false;
            using(var settings=new SettingsWindow(delegate {return savedStartup;},delegate(bool value) {savedStartup=value;},delegate {},delegate {},
                delegate {return new RecipientPreferences {Channel="smtp",Count=1,Contacts=new[]{new RecipientContact {Name="Fixture",Email="fixture@example.test"}}};},
                delegate(RecipientContact[] values) {return new RecipientPreferences {Contacts=values};},
                delegate(string email) {Thread.Sleep(180);return "synthetic SMTP result";})) {
                settings.CreateControl();Check(!settings.BlocksUpgrade,"Saved settings block update");
                settings.StartupCheck.Checked=true;Check(settings.BlocksUpgrade,"Unsaved autostart lost on upgrade");
                settings.Save(true);Check(!settings.BlocksUpgrade,"Saved autostart still blocks");
                Task sending=settings.TestSelected();Check(settings.BlocksUpgrade,"Active SMTP test permits upgrade");Wait(sending);
                Check(!settings.BlocksUpgrade,"Finished SMTP test still blocks");
                settings.Setup.AuthCode.Text="unsaved-fixture";Check(settings.BlocksUpgrade,"Unsaved mail setting permits upgrade");
            }
            File.WriteAllText(Path.Combine(args[0],"result.txt"),"PASS: manual check, available version, no implicit install, unsaved mail/autostart guards, active SMTP guard, progress, chosen upgrade, retry on error.");return 0;
        } catch(Exception ex) {File.WriteAllText(Path.Combine(args[0],"result.txt"),ex.ToString());return 1;}
    }
}
'@
$source = Join-Path $folder 'UpdateUiTest.cs'
[IO.File]::WriteAllText($source, $runner, [Text.UTF8Encoding]::new($true))
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$exe = Join-Path $folder 'UpdateUiTest.exe'
$sources = Get-ChildItem -LiteralPath (Join-Path $root 'desktop') -Filter '*.cs' | ForEach-Object { $_.FullName }
$icon = Join-Path $root 'assets/quota-monitor.ico'
& $compiler /nologo /target:exe /platform:x64 /codepage:65001 /main:UpdateUiTest /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll ("/resource:" + $icon + ',QuotaMonitor.AppIcon.ico') ("/out:" + $exe) $source $sources
if ($LASTEXITCODE -ne 0) { throw 'Update UI test compilation failed' }
$process = Start-Process -FilePath $exe -ArgumentList ('"' + $folder + '"') -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(20000)) { throw 'Update UI test timed out' }
if ($process.ExitCode -ne 0) { throw (Get-Content (Join-Path $folder 'result.txt') -Raw) }
Get-Content (Join-Path $folder 'result.txt')
