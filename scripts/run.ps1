param([string]$Config = 'config.browser.local.toml', [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = 'utf-8'
& $Python -m quota_monitor browser-run --config $Config
exit $LASTEXITCODE
