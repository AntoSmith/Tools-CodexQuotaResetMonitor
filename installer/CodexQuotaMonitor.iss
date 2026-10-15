; Build only through scripts/build-installer.py, which verifies and extracts the
; portable ZIP into a fresh stage. Never point AppSourceDir at a live application.
#ifndef AppVersion
  #error AppVersion must be supplied by the verified installer builder.
#endif
#ifndef AppVersionInfo
  #error AppVersionInfo must be supplied by the verified installer builder.
#endif
#ifndef AppSourceDir
  #error AppSourceDir must be supplied by the verified installer builder.
#endif
#ifndef SetupOutputDir
  #error SetupOutputDir must be supplied by the verified installer builder.
#endif
#ifndef SetupOutputName
  #error SetupOutputName must be supplied by the verified installer builder.
#endif
#ifndef ChineseLanguageFile
  #define ChineseLanguageFile "compiler:Languages\ChineseSimplified.isl"
#endif

; Fixture builds must use a separate registry identity, mutex, and startup value.
; The public build script never defines TestIdentity or accepts compiler defines.
#ifndef TestIdentity
  #define InstallerAppId "{{13E144B7-A89D-49D0-A614-B846F67F8B47}"
  #define InstallerMutex "Local\CodexQuotaMonitorDesktop"
  #define InstallerStartupValue "CodexQuotaMonitor"
#else
  #ifndef TestAppId
    #error Fixture builds require a unique TestAppId.
  #endif
  #ifndef TestAppMutex
    #error Fixture builds require a unique TestAppMutex.
  #endif
  #define InstallerAppId TestAppId
  #define InstallerMutex TestAppMutex
  #define InstallerStartupValue "CodexQuotaMonitor-" + TestIdentity
#endif

[Setup]
AppId={#InstallerAppId}
AppName=Codex 额度重置消息监控
AppVersion={#AppVersion}
AppVerName=Codex 额度重置消息监控 {#AppVersion}
AppPublisher=AntoSmith
VersionInfoVersion={#AppVersionInfo}
VersionInfoDescription=Codex 额度重置消息监控安装程序
DefaultDirName={localappdata}\Programs\CodexQuotaMonitor
DefaultGroupName=Codex 额度重置消息监控
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableProgramGroupPage=yes
AllowNoIcons=yes
DisableWelcomePage=no
WizardStyle=modern
WizardSizePercent=110
SetupIconFile={#AppSourceDir}\assets\quota-monitor.ico
UninstallDisplayIcon={app}\CodexQuotaMonitor.exe
AppMutex={#InstallerMutex}
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
Uninstallable=yes
OutputDir={#SetupOutputDir}
OutputBaseFilename={#SetupOutputName}
Compression=lzma2
SolidCompression=yes
DiskSpanning=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "{#ChineseLanguageFile}"

[Messages]
WelcomeLabel1=欢迎安装 Codex 额度重置消息监控
WelcomeLabel2=本程序帮助您查看 Codex 额度恢复动态，并按您的设置发送邮件提醒。%n%n安装完成后可直接在图形界面中配置邮箱和采集账号。您也可以跳过首次使用引导，稍后再设置。%n%n更新安装会保留已有设置、历史记录和浏览器登录信息。安装前请从托盘菜单退出正在运行的程序。
FinishedHeadingLabel=安装完成
FinishedLabel=Codex 额度重置消息监控已安装。%n%n打开程序后，您可以在设置中填写邮箱、选择采集账号，并在准备好后启动监控。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
; Only validated, clean portable files reach this directory. The uninstall log
; tracks these program files; there is deliberately no wildcard state cleanup.
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Codex 额度重置消息监控"; Filename: "{app}\CodexQuotaMonitor.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\quota-monitor.ico"
Name: "{autodesktop}\Codex 额度重置消息监控"; Filename: "{app}\CodexQuotaMonitor.exe"; WorkingDir: "{app}"; IconFilename: "{app}\assets\quota-monitor.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\CodexQuotaMonitor.exe"; Parameters: "--after-install"; WorkingDir: "{app}"; Description: "打开 Codex 额度重置消息监控"; Flags: nowait postinstall skipifsilent

[Code]
const
  StartupKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  StartupValue = '{#InstallerStartupValue}';
  LockReadWrite = $C0000000;
  LockOpenAlways = 4; { Win32 OPEN_ALWAYS: preserve every existing byte. }
  LockNormalAttribute = $80;

var
  TransactionLock: TFileStream;

function CreateLockFile(FileName: String; DesiredAccess, ShareMode: LongWord;
  SecurityAttributes: NativeUInt; CreationDisposition, Flags: LongWord;
  TemplateFile: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';

function CloseLockFile(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

procedure ReleaseTransactionLock;
begin
  if TransactionLock <> nil then
  begin
    TransactionLock.Free;
    TransactionLock := nil;
  end;
end;

function UpdateBlockReason: String;
var
  LockPath: String;
  CreationHandle: THandle;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\data\desktop\update-recovery-required')) then
  begin
    Result := '上次应用内更新需要恢复程序文件。请先按 data\desktop 中的恢复说明完成处理，再重新安装。';
    Exit;
  end;
  if TransactionLock <> nil then
    Exit;
  LockPath := ExpandConstant('{app}\data\desktop\update.lock');
  try
    if not ForceDirectories(ExtractFileDir(LockPath)) then
      RaiseException('Cannot create the application update lock directory.');
    if not FileExists(LockPath) then
    begin
      { OPEN_ALWAYS atomically creates a missing lock without truncating a lock
        another updater may have just created. }
      CreationHandle := CreateLockFile(LockPath, LockReadWrite, 0, 0,
        LockOpenAlways, LockNormalAttribute, 0);
      if CreationHandle = THandle(-1) then
        RaiseException('Cannot create or open the application update lock.');
      CloseLockFile(CreationHandle);
    end;
    { If an updater wins the handoff after creation, this exclusive open fails
      and installation stops before copying any program files. }
    TransactionLock := TFileStream.Create(LockPath, fmOpenReadWrite or fmShareExclusive);
    if FileExists(ExpandConstant('{app}\data\desktop\update-recovery-required')) then
    begin
      ReleaseTransactionLock;
      Result := '上次应用内更新需要恢复程序文件。请先完成恢复，再重新安装。';
    end;
  except
    ReleaseTransactionLock;
    Result := '应用内更新正在进行或更新锁无法访问。请等待更新结束并退出程序后重新安装。';
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := UpdateBlockReason;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    ReleaseTransactionLock;
end;

procedure DeinitializeSetup;
begin
  ReleaseTransactionLock;
end;

function InitializeUninstall: Boolean;
var
  Reason: String;
begin
  Reason := UpdateBlockReason;
  Result := Reason = '';
  if not Result then
    SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
end;

procedure DeinitializeUninstall;
begin
  ReleaseTransactionLock;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  CurrentCommand, InstalledCommand: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  InstalledCommand := '"' + ExpandConstant('{app}\CodexQuotaMonitor.exe') + '"' + ' --autostart';
  if RegQueryStringValue(HKCU, StartupKey, StartupValue, CurrentCommand) then
    if CompareText(CurrentCommand, InstalledCommand) = 0 then
      RegDeleteValue(HKCU, StartupKey, StartupValue);
end;
