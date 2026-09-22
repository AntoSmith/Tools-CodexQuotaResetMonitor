param([string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'CodexQuotaMonitor.exe'))
$ErrorActionPreference = 'Stop'
$assembly = [Reflection.Assembly]::LoadFrom($Exe)
$type = $assembly.GetType('CodexQuotaDesktop.DashboardState')
if ($null -eq $type) { throw 'DashboardState is missing: round feedback has not been implemented' }
$processType = $assembly.GetType('CodexQuotaDesktop.MonitorProcess')
if ($null -eq $processType.GetMethod('ReapExitedOutput')) { throw 'Natural exit must flush final stdout before discarding its session' }
$state = [Activator]::CreateInstance($type)
$state.Started()
if (-not $state.Active) { throw 'Started state must remain active' }
$null = $state.Apply('@quota-ui {"type":"round_start","round":1,"started_at":1788870000,"interval_seconds":1800}')
if ($state.Phase -ne 'collecting') { throw 'Round start must show collection in progress' }
$null = $state.Apply('@quota-ui {"type":"round_end","round":1,"started_at":1788870000,"ended_at":1788870100,"next_at":1788871800,"outcome":"partial","posts":15,"error":""}')
if (-not $state.Active -or $state.Phase -ne 'waiting' -or $state.RoundsCompleted -ne 1 -or $state.Posts -ne 15) { throw 'Completing a round must keep monitor active and preserve real counts' }
$null = $state.Apply('@quota-ui {"type":"round_end","round":1,"outcome":"partial","posts":15}')
if ($state.RoundsCompleted -ne 1) { throw 'Duplicate completion changed round count' }
$null = $state.Apply('@quota-ui {"type":"round_start","round":2,"started_at":1788871800,"interval_seconds":1800}')
$null = $state.Apply('@quota-ui {"type":"round_end","round":2,"ended_at":1788871810,"next_at":1788873600,"outcome":"empty","posts":0}')
if ($state.Failures -ne 1 -or $state.RoundsCompleted -ne 2 -or $state.LastOutcome -ne 'empty') { throw 'Empty collection must show a failed/incomplete attempt, not success' }
$event = '@quota-ui {"type":"alert","id":"post:123:version","kind":"completed","title":"candidate","username":"demo","text":"Synthetic reset candidate","url":"https://x.com/demo/status/123","reason":"test"}'
$null = $state.Apply($event)
$null = $state.Apply($event)
if ($state.Alerts.Count -ne 1) { throw 'Duplicate event produced multiple popups' }
$null = $state.Apply('@quota-ui {"type":"alert","id":"bad","kind":"completed","url":"file:///C:/private"}')
if ($state.Alerts.Count -ne 1) { throw 'Unsafe alert URL was accepted' }
$null = $state.Apply('@quota-ui invalid json')
$null = $state.Apply('@quota-ui {"type":"round_start","round":999999999999999999999999}')
$null = $state.Apply('@quota-ui {"type":"round_start","round":"bad"}')
$null = $state.Apply('@quota-ui {"type":"round_start","started_at":"bad"}')
$state.Stopped('')
if ($state.Active -or $state.Phase -ne 'stopped') { throw 'Stop must restore inactive button state' }
$null = $state.Apply($event)
if ($state.Alerts.Count -ne 1) { throw 'Stopped state consumed stale process output' }
$state.Started()
if ($state.RoundsCompleted -ne 0 -or $state.Failures -ne 0) { throw 'Session counts must reset at explicit restart' }

# Refresh a real, unshown dashboard across an empty round and recovery. No monitor is launched.
$windowType = $assembly.GetType('CodexQuotaDesktop.DashboardWindow')
$window = [Activator]::CreateInstance($windowType)
$fields = [Reflection.BindingFlags]'Instance, NonPublic'
$resultLabel = $windowType.GetField('result', $fields).GetValue($window)
$endedLabel = $windowType.GetField('ended', $fields).GetValue($window)
$palette = $assembly.GetType('CodexQuotaDesktop.Palette')
$amber = $palette.GetField('Amber').GetValue($null)
$muted = $palette.GetField('Muted').GetValue($null)
$roundFailures = [Collections.Generic.List[string]]::new()
try {
    $null = $state.Apply('@quota-ui {"type":"round_start","round":1,"started_at":1788870000}')
    $null = $state.Apply('@quota-ui {"type":"round_end","round":1,"ended_at":1788870100,"next_at":1788871800,"outcome":"empty","posts":0,"error":"timestamp_unavailable"}')
    $window.RefreshState($state)
    if ($resultLabel.ForeColor -ne $amber) { $roundFailures.Add('Current empty result must retain its warning color') }
    $null = $state.Apply('@quota-ui {"type":"round_start","round":2,"started_at":1788871800}')
    $window.RefreshState($state)
    if ($state.LastOutcome -ne '' -or $state.Posts -ne 0 -or $state.EndedAt -ne 0 -or $state.Error -ne '') { $roundFailures.Add('New round must clear the previous outcome, posts, end time and diagnostic') }
    if ($resultLabel.Text -ne '第2轮采集中，等待本轮结果') { $roundFailures.Add('New round must replace the previous warning with its own collecting text') }
    if ($resultLabel.ForeColor -ne $muted) { $roundFailures.Add('Collecting must clear the stale warning color') }
    if ($endedLabel.Text -ne '结束：—') { $roundFailures.Add('Collecting must not show the previous end time') }
    if ($state.Failures -ne 1 -or $state.RoundsCompleted -ne 1) { $roundFailures.Add('New round must preserve cumulative completed and failed counts') }
    $null = $state.Apply('@quota-ui {"type":"round_end","round":2,"ended_at":1788871810,"next_at":1788873600,"outcome":"partial","posts":7}')
    $window.RefreshState($state)
    if ($state.LastOutcome -ne 'partial' -or $state.Error -ne '' -or $resultLabel.Text -notmatch '取得 7 条帖子' -or $resultLabel.ForeColor -ne $muted) { $roundFailures.Add('Recovered collection must replace the old error with its real post count') }
    $null = $state.Apply('@quota-ui {"type":"round_start","round":3,"started_at":1788873600}')
    if ($state.Posts -ne 0 -or $state.EndedAt -ne 0) { $roundFailures.Add('New round must also clear previous successful post count and end time') }
    $null = $state.Apply('@quota-ui {"type":"round_end","round":3,"ended_at":1788873610,"next_at":1788875400,"outcome":"empty","posts":0,"error":"timestamp_unavailable"}')
    $window.RefreshState($state)
    if ($resultLabel.Text -notmatch '第3轮' -or $resultLabel.Text -notmatch 'timestamp_unavailable') { $roundFailures.Add('Empty result must identify its round and show the supplied diagnostic') }
    if ($resultLabel.ForeColor -ne $amber -or $state.Failures -ne 2 -or $state.RoundsCompleted -ne 3) { $roundFailures.Add('Current empty result must remain visible while failure counts stay cumulative') }
} finally { $window.Dispose() }
if ($roundFailures.Count) { throw ($roundFailures -join '; ') }
Write-Output 'Dashboard lifecycle, round-result recovery, count, empty-result, malformed-event, dedup and safe-link tests passed.'
