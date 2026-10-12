param([string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'data/desktop/portable-launcher-test.exe'))
$ErrorActionPreference = 'Stop'
$Exe = [IO.Path]::GetFullPath($Exe)
if (-not (Test-Path -LiteralPath $Exe)) { throw 'Expected test EXE has not been built' }
$output = Join-Path (Split-Path -Parent $PSScriptRoot) 'data/desktop/portable-launcher-verification'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$assembly = [Reflection.Assembly]::LoadFrom($Exe)
$native = $assembly.GetType('CodexQuotaDesktop.PortableVerification')
if ($null -eq $native) { throw 'Native portable verification is missing' }
$result = $native.GetMethod('Run').Invoke($null, [object[]]@([string]$output))
$report = Join-Path $output 'portable-test.txt'
if ($result -ne 0) { throw (Get-Content -LiteralPath $report -Raw) }
$relocated = Join-Path $output ('搬移 应用 ' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $relocated | Out-Null
try {
    $copy = Join-Path $relocated 'CodexQuotaMonitor.exe'
    Copy-Item -LiteralPath $Exe -Destination $copy
    $relocationReport = Join-Path $relocated '验证 报告'
    $process = Start-Process -FilePath $copy -ArgumentList ('--portable-test "' + $relocationReport + '"') -WorkingDirectory $relocated -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(20000)) { $process.Kill(); throw 'Portable diagnostic timed out' }
    if ($process.ExitCode -ne 0) { throw 'Relocated portable diagnostic failed' }
    if ((Get-Content -LiteralPath (Join-Path $relocationReport 'portable-test.txt') -Raw) -notmatch '^PASS') { throw 'Missing relocated diagnostic success report' }
    if (Test-Path -LiteralPath (Join-Path $relocated 'config.browser.local.toml')) { throw 'Diagnostic initialized the executable root configuration' }
    $loginScript = Join-Path $relocated 'LoginX.cmd'
    Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'LoginX.cmd') -Destination $loginScript
    $fakeSource = Join-Path $relocated 'FakeRuntime.cs'
    [IO.File]::WriteAllText($fakeSource, 'using System; using System.IO; internal static class FakeRuntime { static int Main(string[] args) { File.WriteAllLines("login-runtime-probe.txt", new string[] { Environment.GetEnvironmentVariable("PATH"), Environment.GetEnvironmentVariable("PYTHONIOENCODING"), String.Join("|", args), Directory.GetCurrentDirectory(), System.Reflection.Assembly.GetExecutingAssembly().Location }); int code; return Int32.TryParse(Environment.GetEnvironmentVariable("FAKE_RUNTIME_EXIT_CODE"), out code) ? code : 0; } }')
    $bundledPython = Join-Path $relocated 'runtime/python/python.exe'
    $bundledNode = Join-Path $relocated 'runtime/node/node.exe'
    New-Item -ItemType Directory -Path (Split-Path -Parent $bundledPython), (Split-Path -Parent $bundledNode) -Force | Out-Null
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    & $compiler /nologo /target:exe /platform:x64 ("/out:" + $bundledPython) $fakeSource
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic login runtime compilation failed' }
    [IO.File]::WriteAllText($bundledNode, 'synthetic, never executed')
    $systemRuntime = Join-Path $relocated 'system runtime'
    New-Item -ItemType Directory -Path $systemRuntime | Out-Null
    Copy-Item -LiteralPath $bundledPython -Destination (Join-Path $systemRuntime 'python.exe')
    $explicitPython = Join-Path $systemRuntime 'explicit python.exe'
    Copy-Item -LiteralPath $bundledPython -Destination $explicitPython
    $scriptDirectory = Join-Path $relocated 'scripts'
    New-Item -ItemType Directory -Path $scriptDirectory | Out-Null
    $runScript = Join-Path $scriptDirectory 'run.ps1'
    Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/run.ps1') -Destination $runScript
    $runWrapper = Join-Path $relocated 'Invoke-SyntheticRun.ps1'
    $wrapperSource = @'
param([string]$Entry, [string]$Fixture, [string]$Python)
$pathBefore = $env:PATH
$code = 0
try {
    if ([String]::IsNullOrEmpty($Python)) { & $Entry -Config 'config 脚本.toml' }
    else { & $Entry -Config 'config 脚本.toml' -Python $Python }
    $code = $LASTEXITCODE
} catch { $code = 99 }
finally {
    if ($env:PATH -ne $pathBefore) { $code = 98 }
    [IO.File]::WriteAllText((Join-Path $Fixture 'run-restored.txt'), ('exit=' + $code))
}
exit $code
'@
    [IO.File]::WriteAllText($runWrapper, $wrapperSource, [Text.UTF8Encoding]::new($true))
    function Invoke-SyntheticRun {
        param([string]$RuntimePath, [string]$ExpectedExecutable, [string]$Python = '', [int]$ExpectedExit = 0, [switch]$ExpectNoProbe)
        $probe = Join-Path $relocated 'login-runtime-probe.txt'
        $restored = Join-Path $relocated 'run-restored.txt'
        foreach ($path in @($probe, $restored)) { if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force } }
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $runWrapper + '" -Entry "' + $runScript + '" -Fixture "' + $relocated + '"'
        if (-not [String]::IsNullOrEmpty($Python)) { $arguments += ' -Python "' + $Python + '"' }
        $shell = Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
        $info = [Diagnostics.ProcessStartInfo]::new($shell, $arguments)
        $info.WorkingDirectory = $output
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.EnvironmentVariables['PATH'] = $RuntimePath
        $info.EnvironmentVariables['FAKE_RUNTIME_EXIT_CODE'] = [string]$(if ($ExpectedExit -eq 99) { 0 } else { $ExpectedExit })
        $run = [Diagnostics.Process]::Start($info)
        try {
            $stdout = $run.StandardOutput.ReadToEndAsync()
            $stderr = $run.StandardError.ReadToEndAsync()
            if (-not $run.WaitForExit(15000)) { $run.Kill(); throw 'Synthetic run.ps1 test timed out' }
            if ($run.ExitCode -ne $ExpectedExit) { throw ('run.ps1 failed to select a runtime, preserve its exit code, or restore PATH; expected exit ' + $ExpectedExit + ', actual ' + $run.ExitCode) }
            if (-not (Test-Path -LiteralPath $restored) -or [IO.File]::ReadAllText($restored) -ne ('exit=' + $ExpectedExit)) { throw 'run.ps1 did not restore PATH before returning to its caller' }
            if ($ExpectNoProbe) {
                if (Test-Path -LiteralPath $probe) { throw 'Invalid explicit Python was replaced by another runtime' }
                return
            }
            if (-not (Test-Path -LiteralPath $probe)) { throw 'run.ps1 did not invoke the synthetic Python runtime' }
            $lines = [IO.File]::ReadAllLines($probe)
            if ($lines[1] -ne 'utf-8' -or $lines[2] -ne '-m|quota_monitor|browser-run|--config|config 脚本.toml') { throw 'run.ps1 arguments or Unicode environment changed' }
            if ($lines[3] -ne $relocated -or $lines[4] -ne $ExpectedExecutable) { throw 'run.ps1 used the wrong runtime or working directory' }
            if (Test-Path -LiteralPath $bundledNode) {
                if ($lines[0] -ne ((Split-Path -Parent $bundledNode) + ';' + $RuntimePath)) { throw 'run.ps1 did not temporarily prepend bundled Node' }
            } elseif ($lines[0] -ne $RuntimePath) { throw 'run.ps1 changed PATH when no bundled Node exists' }
        } finally { $run.Dispose() }
    }
    foreach ($sourceMode in @($false, $true)) {
        if ($sourceMode) { Remove-Item -LiteralPath $bundledPython, $bundledNode -Force }
        $probe = Join-Path $relocated 'login-runtime-probe.txt'
        if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force }
        $info = [Diagnostics.ProcessStartInfo]::new('cmd.exe', ('/d /c ""' + $loginScript + '""'))
        $info.WorkingDirectory = $output
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.EnvironmentVariables['PATH'] = if ($sourceMode) { $systemRuntime + ';' + (Join-Path $env:WINDIR 'System32') } else { Join-Path $env:WINDIR 'System32' }
        $login = [Diagnostics.Process]::Start($info)
        try {
            $stdout = $login.StandardOutput.ReadToEndAsync()
            $stderr = $login.StandardError.ReadToEndAsync()
            $login.StandardInput.WriteLine('')
            $login.StandardInput.Close()
            if (-not $login.WaitForExit(10000)) { $login.Kill(); throw 'Synthetic login test timed out' }
            if ($login.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $probe)) { throw ('LoginX did not use the expected ' + $(if ($sourceMode) { 'source fallback' } else { 'bundled Python runtime' })) }
            $lines = [IO.File]::ReadAllLines($probe)
            if ($lines[1] -ne 'utf-8' -or $lines[2] -ne '-m|quota_monitor|browser-login|--config|config.browser.local.toml') { throw 'LoginX arguments or Unicode environment changed' }
            $expectedLogin = if ($sourceMode) { Join-Path $systemRuntime 'python.exe' } else { $bundledPython }
            if ($lines[3] -ne $relocated -or $lines[4] -ne $expectedLogin) { throw 'LoginX used the wrong runtime or working directory' }
            if (-not $sourceMode -and -not $lines[0].StartsWith((Split-Path -Parent $bundledNode) + ';', [StringComparison]::OrdinalIgnoreCase)) { throw 'LoginX did not add bundled Node to its temporary PATH' }
        } finally { $login.Dispose() }
        $isolatedPath = Join-Path $env:WINDIR 'System32'
        $sourcePath = $systemRuntime + ';' + $isolatedPath
        if ($sourceMode) { Invoke-SyntheticRun -RuntimePath $sourcePath -ExpectedExecutable (Join-Path $systemRuntime 'python.exe') }
        else {
            Invoke-SyntheticRun -RuntimePath $isolatedPath -ExpectedExecutable $bundledPython
            Invoke-SyntheticRun -RuntimePath $sourcePath -ExpectedExecutable $bundledPython
            Invoke-SyntheticRun -RuntimePath $isolatedPath -ExpectedExecutable $explicitPython -Python $explicitPython
            Invoke-SyntheticRun -RuntimePath $isolatedPath -ExpectedExecutable $bundledPython -ExpectedExit 23
            Invoke-SyntheticRun -RuntimePath $isolatedPath -ExpectedExecutable '' -Python (Join-Path $relocated 'missing-python.exe') -ExpectedExit 99 -ExpectNoProbe
        }
    }
    Write-Output (Get-Content -LiteralPath $report -Raw)
    Write-Output 'PASS LoginX bundled Python/Node without system runtimes, source fallback, Unicode environment and relative working directory'
    Write-Output 'PASS run.ps1 bundled priority without system runtimes, source fallback, explicit Python, Unicode config/working directory, nonzero exit and exact PATH restoration'
} finally {
    $resolved = [IO.Path]::GetFullPath($relocated)
    $boundary = [IO.Path]::GetFullPath($output).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
