param([string]$Archive = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$fixture = Join-Path $root ('data/desktop/release-update-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture -Force | Out-Null
$sources = @((Join-Path $root 'desktop/ReleaseUpdates.cs'), (Join-Path $root 'desktop/UpdateInstaller.cs'))
foreach ($source in $sources) { if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'FAIL release update engine is not implemented yet' } }
$harness = @'
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Web.Script.Serialization;
using CodexQuotaDesktop;
class ReleaseUpdateTests {
    static string fixtures; static int count;
    static string Fresh() { string p=Path.Combine(fixtures, Guid.NewGuid().ToString("N")); Directory.CreateDirectory(p); Write(p,"package.json","{\"version\":\"0.1.0\"}"); Write(p,"CodexQuotaMonitor.exe","old-exe"); return p; }
    static void Write(string root,string name,string value) { string p=Path.Combine(root,name.Replace('/',Path.DirectorySeparatorChar)); Directory.CreateDirectory(Path.GetDirectoryName(p)); File.WriteAllText(p,value,new UTF8Encoding(false)); }
    static string Read(string root,string name) { return File.ReadAllText(Path.Combine(root,name.Replace('/',Path.DirectorySeparatorChar))); }
    static void Equal(object expected,object actual,string why) { if(!Object.Equals(expected,actual)) throw new Exception(why+": expected "+expected+", actual "+actual); }
    static void Reject(Action action,string why) { bool rejected=false; try {action();} catch(InvalidDataException) {rejected=true;} if(!rejected) throw new Exception("Accepted "+why); }
    static void Test(string name,Action action) { action(); count++; Console.WriteLine("PASS "+name); }
    static string Metadata(string version,string digest,string name,string url,bool draft,bool pre,long size) {
        return new JavaScriptSerializer().Serialize(new { tag_name="v"+version, draft=draft, prerelease=pre,
            html_url=ReleaseUpdates.RepositoryUrl+"/releases/tag/v"+version,
            assets=new[]{new {name=name,browser_download_url=url,digest=digest,size=size}} });
    }
    static ReleaseUpdateInfo Info(string root,string archive) {
        string name="CodexQuotaMonitor-v0.2.0-windows-x64.zip";
        return ReleaseUpdates.ParseMetadata(root,Metadata("0.2.0","sha256:"+ReleaseUpdates.FileSha256(archive),name,ReleaseUpdates.RepositoryUrl+"/releases/download/v0.2.0/"+name,false,false,new FileInfo(archive).Length));
    }
    static Dictionary<string,string> Files() { return new Dictionary<string,string> {
        {"CodexQuotaMonitor.exe","new-exe"},{"package.json","{\"version\":\"0.2.0\"}"},
        {"runtime-provenance.json","{\"application_version\":\"0.2.0\",\"target\":\"windows-x64\"}"},
        {"quota_monitor/__main__.py","new-module"}, {"runtime/python/python.exe","python-fixture"},
        {"runtime/node/node.exe","node-fixture"}, {"node_modules/playwright/package.json","{}"},
        {"node_modules/playwright-core/package.json","{}"}, {"assets/quota-monitor.ico","icon-fixture"},
        {"README.md","readme-fixture"} }; }
    static string Zip(Dictionary<string,string> files,string extra,bool symlink) {
        string p=Path.Combine(fixtures,Guid.NewGuid().ToString("N")+".zip");
        using(FileStream f=new FileStream(p,FileMode.CreateNew)) using(ZipArchive z=new ZipArchive(f,ZipArchiveMode.Create)) {
            foreach(KeyValuePair<string,string> pair in files) Add(z,"CodexQuotaMonitor-v0.2.0-windows-x64/"+pair.Key,pair.Value,false);
            if(extra!=null) Add(z,extra,"bad",symlink);
        } return p;
    }
    static void Add(ZipArchive z,string name,string text,bool symlink) { ZipArchiveEntry e=z.CreateEntry(name); if(symlink) e.ExternalAttributes=(0xA1FF<<16); using(StreamWriter w=new StreamWriter(e.Open(),new UTF8Encoding(false))) w.Write(text); }
    static string Plan(string root,string archive) { return ReleaseUpdates.PrepareArchive(root,Info(root,archive),archive,null); }
    static string[] PersonalFiles() { return new[]{"config.browser.local.toml","config.other.local.toml","data/desktop/config.browser.local.toml.notifications.json","data/history.db","data/history.db-wal","data/history.db-shm","data/recipients.json","logs/private.log","browser-profile/Cookies",".git/config","github-export/private.txt","my-notes.txt","quota_monitor/my-analysis.py"}; }
    static void Preserved(string root) { foreach(string p in PersonalFiles()) Equal("personal",Read(root,p),"preserved "+p); }
    static void SeedPersonal(string root) { foreach(string p in PersonalFiles()) Write(root,p,"personal"); }
    public static int Main(string[] args) { if(args[0]=="--apply-update") return UpdateInstaller.Run(args[1]); fixtures=args[0]; try {
        Test("numeric stable semantic version comparison",delegate {
            Equal(1,ReleaseUpdates.CompareVersions("0.10.0","0.9.9"),"numeric minor"); Equal(0,ReleaseUpdates.CompareVersions("1.2.3","1.2.3"),"same"); Equal(-1,ReleaseUpdates.CompareVersions("1.2.3","2.0.0"),"major");
            foreach(string v in new[]{"1.2","01.2.3","1.2.3-beta","1.2.3+build","-1.2.3","1.2.3.4"}) Reject(delegate {ReleaseUpdates.CompareVersions(v,"1.0.0");},"invalid stable version "+v);
        });
        Test("portable version wins over source version and downgrades are unavailable",delegate {
            string r=Fresh(); Write(r,"runtime-provenance.json","{\"application_version\":\"0.10.0\"}"); string n="CodexQuotaMonitor-v0.2.0-windows-x64.zip";
            ReleaseUpdateInfo i=ReleaseUpdates.ParseMetadata(r,Metadata("0.2.0","sha256:"+new string('a',64),n,ReleaseUpdates.RepositoryUrl+"/releases/download/v0.2.0/"+n,false,false,20)); Equal("0.10.0",i.CurrentVersion,"provenance"); Equal(false,i.Available,"downgrade");
        });
        Test("release metadata fails closed",delegate {
            string r=Fresh(); string n="CodexQuotaMonitor-v0.2.0-windows-x64.zip",u=ReleaseUpdates.RepositoryUrl+"/releases/download/v0.2.0/"+n,d="sha256:"+new string('a',64);
            Reject(delegate{ReleaseUpdates.ParseMetadata(r,Metadata("0.2.0",d,n,u,true,false,20));},"draft");
            Reject(delegate{ReleaseUpdates.ParseMetadata(r,Metadata("0.2.0",d,n,u,false,true,20));},"prerelease");
            Reject(delegate{ReleaseUpdates.ParseMetadata(r,Metadata("0.2.0",null,n,u,false,false,20));},"missing digest");
            Reject(delegate{ReleaseUpdates.ParseMetadata(r,Metadata("0.2.0",d,"other.zip",u,false,false,20));},"wrong asset");
            Reject(delegate{ReleaseUpdates.ParseMetadata(r,Metadata("0.2.0",d,n,"https://github.com.evil.test/asset",false,false,20));},"untrusted URL");
            Reject(delegate{ReleaseUpdates.ParseMetadata(r,Metadata("0.2.0",d,n,u,false,false,ReleaseUpdates.MaxDownloadBytes+1));},"oversized release");
            Reject(delegate{ReleaseUpdates.ParseMetadata(r,new string('x',ReleaseUpdates.MaxMetadataBytes+1));},"oversized metadata");
        });
        Test("HTTPS redirect destinations must remain exact trusted GitHub hosts",delegate {
            foreach(string u in new[]{"http://github.com/a","https://github.com.evil.test/a","https://user:pass@github.com/a","https://github.com:444/a","https://release-assets.githubusercontent.com.evil.test/a"}) Reject(delegate {ReleaseUpdates.RequireTrusted(new Uri(u));},"untrusted redirect "+u);
            ReleaseUpdates.RequireTrusted(new Uri("https://release-assets.githubusercontent.com/github-production-release-asset/fixture"));
        });
        Test("verified archive installs only application files and keeps all personal state",delegate {
            string r=Fresh(); SeedPersonal(r); string p=Plan(r,Zip(Files(),null,false)); UpdateInstaller.Apply(p,null);
            Equal("new-exe",Read(r,"CodexQuotaMonitor.exe"),"EXE updated"); Equal("new-module",Read(r,"quota_monitor/__main__.py"),"module updated"); Equal("python-fixture",Read(r,"runtime/python/python.exe"),"source checkout gains runtime"); Preserved(r);
            Reject(delegate{UpdateInstaller.Apply(p,null);},"replayed completed plan");
        });
        Test("future direct application modules and image assets remain upgradable",delegate {
            string r=Fresh(); Dictionary<string,string> f=Files(); f.Add("quota_monitor/future_feature.py","future-module"); f.Add("assets/future-feature.png","future-image");
            UpdateInstaller.Apply(Plan(r,Zip(f,null,false)),null); Equal("future-module",Read(r,"quota_monitor/future_feature.py"),"new module"); Equal("future-image",Read(r,"assets/future-feature.png"),"new image");
        });
        Test("SHA256 and exact byte count required before any application mutation",delegate {
            string r=Fresh(),a=Zip(Files(),null,false); ReleaseUpdateInfo i=Info(r,a); i.Sha256=new string('0',64);
            Reject(delegate{ReleaseUpdates.PrepareArchive(r,i,a,null);},"digest mismatch"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"digest retained old app"); i=Info(r,a); i.Size++; Reject(delegate{ReleaseUpdates.PrepareArchive(r,i,a,null);},"size mismatch");
        });
        Test("ZIP paths, aliases, symlinks and private payloads rejected",delegate {
            foreach(string e in new[]{"../outside.txt","C:/outside.txt","CodexQuotaMonitor-v0.2.0-windows-x64/../outside.txt","CodexQuotaMonitor-v0.2.0-windows-x64/data/history.db","CodexQuotaMonitor-v0.2.0-windows-x64/config.browser.local.toml","CodexQuotaMonitor-v0.2.0-windows-x64/quota_monitor/private-notes.txt","CodexQuotaMonitor-v0.2.0-windows-x64/README.md","CodexQuotaMonitor-v0.2.0-windows-x64/readme.MD","CodexQuotaMonitor-v0.2.0-windows-x64/runtime/python/CON.txt","CodexQuotaMonitor-v0.2.0-windows-x64/runtime/python/notes. ","CodexQuotaMonitor-v0.2.0-windows-x64/runtime/python/config.foo.local.toml","CodexQuotaMonitor-v0.2.0-windows-x64/runtime/python/browser-profile/Cookies"}) {
                string r=Fresh(),a=Zip(Files(),e,false); Reject(delegate{Plan(r,a);},"unsafe ZIP entry "+e); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"unsafe retained old app");
            }
            string sr=Fresh(),sa=Zip(Files(),"CodexQuotaMonitor-v0.2.0-windows-x64/runtime/python/linked.dll",true); Reject(delegate{Plan(sr,sa);},"symlink");
            Dictionary<string,string> partial=Files(); partial.Remove("CodexQuotaMonitor.exe"); string pr=Fresh(),pa=Zip(partial,null,false); Reject(delegate{Plan(pr,pa);},"incomplete portable archive");
        });
        Test("ZIP entry cap prevents excessive extraction work",delegate {
            Dictionary<string,string> f=Files(); for(int n=0;n<ReleaseUpdates.MaxEntries;n++) f.Add("quota_monitor/a"+n+".py",""); string r=Fresh(),a=Zip(f,null,false); Reject(delegate{Plan(r,a);},"excessive ZIP entries"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"entry cap retained old app");
        });
        Test("destination ancestor junction cannot redirect application writes",delegate {
            string r=Fresh(),p=Plan(r,Zip(Files(),null,false)),target=Path.Combine(fixtures,"outside-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(target); Write(target,"__main__.py","sentinel");
            System.Diagnostics.ProcessStartInfo start=new System.Diagnostics.ProcessStartInfo(Environment.GetEnvironmentVariable("COMSPEC"),"/c mklink /J \""+Path.Combine(r,"quota_monitor")+"\" \""+target+"\""); start.UseShellExecute=false; start.CreateNoWindow=true; start.RedirectStandardOutput=true; start.RedirectStandardError=true;
            int code; using(System.Diagnostics.Process child=System.Diagnostics.Process.Start(start)) {child.StandardOutput.ReadToEnd(); child.StandardError.ReadToEnd(); child.WaitForExit(); code=child.ExitCode;}
            if(code==0) { Reject(delegate{UpdateInstaller.Apply(p,null);},"destination ancestor junction"); Equal("sentinel",Read(target,"__main__.py"),"junction target unchanged"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"junction retained old app"); }
            else {
                // Some managed desktops prohibit creating junctions. Exercise the same ancestor
                // validator against an existing Windows junction without reading its contents.
                string junction=Path.Combine(Path.GetPathRoot(Environment.SystemDirectory),"Users","All Users");
                if((File.GetAttributes(junction)&FileAttributes.ReparsePoint)==0) throw new Exception("No read-only junction fixture is available");
                Reject(delegate{ReleaseUpdates.RejectReparse(Path.Combine(junction,"update-fixture-sentinel.txt"));},"existing ancestor junction"); Console.WriteLine("INFO junction creation is restricted; existing Windows junction validated read-only.");
            }
        });
        Test("locked application file aborts before any replacement",delegate {
            string r=Fresh(),p=Plan(r,Zip(Files(),null,false)); bool failed=false;
            using(FileStream held=new FileStream(Path.Combine(r,"CodexQuotaMonitor.exe"),FileMode.Open,FileAccess.Read,FileShare.None)) try{UpdateInstaller.Apply(p,null);} catch(IOException){failed=true;}
            Equal(true,failed,"exclusive lock rejected"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"lock retained old app"); Equal(false,File.Exists(Path.Combine(r,"data","desktop","update-recovery-required")),"no mutation marker before locks pass");
        });
        Test("tampered staging rejected before installation",delegate {
            string r=Fresh(),p=Plan(r,Zip(Files(),null,false)); string staged=Path.Combine(Path.GetDirectoryName(p),"staged","CodexQuotaMonitor.exe"); File.WriteAllText(staged,"tampered"); Reject(delegate{UpdateInstaller.Apply(p,null);},"changed stage"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"tamper retained old app");
        });
        Test("stage and plan hashes must still derive from the verified ZIP",delegate {
            string r=Fresh(),p=Plan(r,Zip(Files(),null,false)); UpdatePlan plan=UpdateInstaller.ReadPlan(p); string staged=Path.Combine(plan.Work,"staged","CodexQuotaMonitor.exe"); File.WriteAllText(staged,"coordinated-tamper");
            foreach(UpdateFile f in plan.Files) if(f.Relative=="CodexQuotaMonitor.exe") { f.Length=new FileInfo(staged).Length; f.Sha256=ReleaseUpdates.FileSha256(staged); }
            ReleaseUpdates.WritePlan(p,plan); Reject(delegate{UpdateInstaller.Apply(p,null);},"staging and plan differ from verified archive"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"coordinated tamper retained old app");
        });
        Test("failed swap restores replaced bytes and removes newly added files",delegate {
            string r=Fresh(); SeedPersonal(r); string oldPackage=Read(r,"package.json"),p=Plan(r,Zip(Files(),null,false)); bool failed=false;
            try { UpdateInstaller.Apply(p,delegate(int n) {if(n==4) throw new IOException("Injected local fixture failure");}); } catch(IOException) {failed=true;}
            Equal(true,failed,"injected failure"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"EXE rollback"); Equal(oldPackage,Read(r,"package.json"),"package rollback"); Equal(false,File.Exists(Path.Combine(r,"README.md")),"new file rollback"); Equal(false,File.Exists(Path.Combine(r,"assets","quota-monitor.ico")),"new asset rollback"); Preserved(r);
        });
        Test("native helper acknowledges readiness and cancels while exact parent remains alive",delegate {
            string r=Fresh(),p=Plan(r,Zip(Files(),null,false)); ReleaseUpdates.LaunchInstaller(r,p,false); UpdatePlan plan=UpdateInstaller.ReadPlan(p);
            Equal(false,plan.ResumeMonitoring,"stopped monitor retained"); Equal(true,File.Exists(Path.Combine(plan.Work,"helper-ready-"+plan.HelperToken)),"ready handshake"); Equal(true,UpdateInstaller.IsInstalling(r),"helper root lock");
            File.WriteAllText(Path.Combine(plan.Work,"cancel-"+plan.HelperToken),plan.HelperToken); System.Diagnostics.Stopwatch c=System.Diagnostics.Stopwatch.StartNew();
            while(UpdateInstaller.IsInstalling(r)&&c.ElapsedMilliseconds<5000) System.Threading.Thread.Sleep(50);
            Equal(false,UpdateInstaller.IsInstalling(r),"cancel releases lock"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"active parent prevents application swap"); Equal(false,File.Exists(Path.Combine(plan.Work,"completed")),"cancel never installs");
        });
        Test("incomplete rollback blocks subsequent startup and keeps recovery diagnostic",delegate {
            string r=Fresh(),p=Plan(r,Zip(Files(),null,false)); FileStream held=null; bool failed=false;
            try { UpdateInstaller.Apply(p,delegate(int n) {if(n==4) {held=new FileStream(Path.Combine(r,"CodexQuotaMonitor.exe"),FileMode.Open,FileAccess.Read,FileShare.Read); throw new IOException("Injected rollback failure");}}); }
            catch(IOException) {failed=true;} finally {if(held!=null) held.Dispose();}
            Equal(true,failed,"rollback failure reported"); Equal(true,UpdateInstaller.IsInstalling(r),"mixed application startup blocked");
            Dictionary<string,object> result=ReleaseUpdates.JsonObject(File.ReadAllText(Path.Combine(Path.GetDirectoryName(p),"result.json")),1048576); Equal("rollback-incomplete",result["state"],"recovery state preserved");
        });
        if(args.Length>2 && args[1].Length>0) Test("actual published portable ZIP passes allowlist and integrity preparation",delegate {
            string r=args[2]; Directory.CreateDirectory(r); Write(r,"package.json","{\"version\":\"0.1.0\"}"); Write(r,"CodexQuotaMonitor.exe","old-exe");
            string a=args[1],name=Path.GetFileName(a),version=name.Substring("CodexQuotaMonitor-v".Length); version=version.Substring(0,version.IndexOf("-windows-x64.zip",StringComparison.Ordinal));
            ReleaseUpdateInfo info=ReleaseUpdates.ParseMetadata(r,Metadata(version,"sha256:"+ReleaseUpdates.FileSha256(a),name,ReleaseUpdates.RepositoryUrl+"/releases/download/v"+version+"/"+name,false,false,new FileInfo(a).Length));
            string p=ReleaseUpdates.PrepareArchive(r,info,a,null); UpdatePlan plan=UpdateInstaller.ReadPlan(p); if(plan.Files.Count<100) throw new Exception("Real portable file inventory unexpectedly small"); Equal("old-exe",Read(r,"CodexQuotaMonitor.exe"),"real archive preparation does not install");
        });
        Console.WriteLine("PASS "+count+" release update scenarios; isolated fixtures only, no network, mail, production config or history touched."); return 0;
    } catch(Exception e) {Console.Error.WriteLine("FAIL "+e); return 1;} }
}
'@
$harnessPath = Join-Path $fixture 'ReleaseUpdateTests.cs'
[IO.File]::WriteAllText($harnessPath, $harness, [Text.UTF8Encoding]::new($false))
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$exe = Join-Path $fixture 'ReleaseUpdateTests.exe'
& $compiler /nologo /target:exe /platform:x64 /codepage:65001 /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll ("/out:" + $exe) $sources $harnessPath
if ($LASTEXITCODE -ne 0) { throw 'Release update test harness failed to compile' }
$realFixture = Join-Path $root ('data/desktop/u-' + [Guid]::NewGuid().ToString('N').Substring(0,6))
& $exe $fixture $Archive $realFixture
if ($LASTEXITCODE -ne 0) { throw 'Release update tests failed' }
