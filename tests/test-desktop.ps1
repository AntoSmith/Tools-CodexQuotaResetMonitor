param([string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'CodexQuotaMonitor.exe'))
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Exe)) { throw 'Expected tray EXE has not been built' }
$root = Split-Path -Parent $Exe
$report = Join-Path $root 'data/desktop/self-test.txt'
$process = Start-Process -FilePath $Exe -ArgumentList '--self-test' -WorkingDirectory $root -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(20000)) { throw 'Desktop self-test timed out' }
if ($process.ExitCode -ne 0) { throw 'Desktop self-test failed; read data/desktop/self-test.txt' }
if ((Get-Content -LiteralPath $report -Raw) -notmatch 'PASS') { throw 'Missing successful self-test report' }
$process = Start-Process -FilePath $Exe -ArgumentList '--lifecycle-test' -WorkingDirectory $root -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(25000)) { throw 'Lifecycle test timed out' }
if ($process.ExitCode -ne 0) { throw 'Process lifecycle test failed; read data/desktop/lifecycle-test.txt' }
$process = Start-Process -FilePath $Exe -ArgumentList '--ui-test' -WorkingDirectory $root -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(25000)) { throw 'UI feedback test timed out' }
if ($process.ExitCode -ne 0) { throw 'UI feedback test failed; read data/desktop/ui-test.txt' }
$process = Start-Process -FilePath $Exe -ArgumentList '--history-ui-test' -WorkingDirectory $root -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(25000)) { throw 'History UI test timed out' }
if ($process.ExitCode -ne 0) { throw 'History UI test failed; read data/desktop/history-ui-test.txt' }
$historyReport = Join-Path $root 'data/desktop/history-ui-test.txt'
if ((Get-Content -LiteralPath $historyReport -Raw) -notmatch '^PASS') { throw 'Missing successful history UI test report' }
Write-Output 'Desktop preflight, UI feedback, history review, final stdout, process start/stop and child cleanup passed.'
