param([string]$Output = 'data/desktop/AlertLayoutVerification.exe')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$icon = Join-Path $taskRoot 'assets/quota-monitor.ico'
$sources = @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'desktop') -Filter '*.cs' | ForEach-Object { $_.FullName })
$sources += Join-Path $PSScriptRoot 'AlertLayoutVerification.cs'
$target = Join-Path $taskRoot $Output
New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
& $compiler /nologo /target:exe /platform:x64 /codepage:65001 /main:CodexQuotaDesktop.AlertLayoutVerification /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll ("/resource:" + $icon + ',QuotaMonitor.AppIcon.ico') ("/out:" + $target) $sources
if ($LASTEXITCODE -ne 0) { throw 'Alert layout verification build failed' }
& $target $taskRoot
if ($LASTEXITCODE -ne 0) { throw 'Alert layout verification failed' }
