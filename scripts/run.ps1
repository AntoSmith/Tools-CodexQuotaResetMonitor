param([string]$Config = 'config.browser.local.toml', [string]$Python = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = 'utf-8'
if ([String]::IsNullOrEmpty($Python)) {
    $bundledPython = Join-Path $projectRoot 'runtime/python/python.exe'
    $Python = if (Test-Path -LiteralPath $bundledPython -PathType Leaf) { $bundledPython } else { 'python' }
}
$previousPath = $env:PATH
try {
    $bundledNode = Join-Path $projectRoot 'runtime/node/node.exe'
    if (Test-Path -LiteralPath $bundledNode -PathType Leaf) { $env:PATH = (Split-Path -Parent $bundledNode) + ';' + $previousPath }
    & $Python -m quota_monitor browser-run --config $Config
    $runExitCode = $LASTEXITCODE
} finally { $env:PATH = $previousPath }
exit $runExitCode
