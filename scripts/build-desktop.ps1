param([string]$OutputName = 'CodexQuotaMonitor.exe')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework C# compiler is missing' }
$runtime = Join-Path $root 'data/desktop'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
foreach ($name in @('python', 'node')) {
    $command = Get-Command $name -ErrorAction Stop
    [IO.File]::WriteAllText((Join-Path $runtime ($name + '-path.txt')), $command.Source, [Text.UTF8Encoding]::new($false))
}
if ([IO.Path]::GetFileName($OutputName) -ne $OutputName -or -not $OutputName.EndsWith('.exe')) { throw 'OutputName must be an EXE filename in the project root' }
$output = Join-Path $root $OutputName
$sources = Get-ChildItem -LiteralPath (Join-Path $root 'desktop') -Filter '*.cs' | ForEach-Object { $_.FullName }
$icon = Join-Path $root 'assets/quota-monitor.ico'
if (-not (Test-Path -LiteralPath $icon)) { throw 'Application icon is missing from assets/' }
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll ("/win32icon:" + $icon) ("/resource:" + $icon + ',QuotaMonitor.AppIcon.ico') ("/out:" + $output) $sources
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
Write-Output $output
