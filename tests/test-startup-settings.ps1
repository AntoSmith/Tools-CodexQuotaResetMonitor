param([string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'CodexQuotaMonitor.exe'))
$ErrorActionPreference='Stop'
$a=[Reflection.Assembly]::LoadFrom($Exe)
if ($null -eq $a.GetType('CodexQuotaDesktop.SettingsWindow')) { throw 'Settings window is missing' }
$t=$a.GetType('CodexQuotaDesktop.DashboardWindow')
if ($null -eq $t.GetField('SettingsButton')) { throw 'Dashboard settings entry is missing' }
Write-Output 'Settings entry available'
