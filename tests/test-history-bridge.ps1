param([string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'CodexQuotaMonitor.exe'))
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $Exe
$fixtureRoot = Join-Path $root 'data/desktop'
$fixture = Join-Path $fixtureRoot ('history-bridge-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $fixture 'data/desktop') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $fixture 'quota_monitor') -Force | Out-Null
$bundledPython = Join-Path $root 'runtime/python/python.exe'
if (Test-Path -LiteralPath $bundledPython -PathType Leaf) {
    [IO.File]::WriteAllText((Join-Path $fixture 'data/desktop/python-path.txt'), $bundledPython, [Text.UTF8Encoding]::new($false))
} else {
    Copy-Item -LiteralPath (Join-Path $root 'data/desktop/python-path.txt') -Destination (Join-Path $fixture 'data/desktop/python-path.txt')
}
Get-ChildItem -LiteralPath (Join-Path $root 'quota_monitor') -Filter '*.py' | Copy-Item -Destination (Join-Path $fixture 'quota_monitor')
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $fixture 'config.browser.local.toml'), "[monitor]`ndatabase=`"data/history.db`"`n", $utf8)
$seed = @'
import json
from pathlib import Path
from quota_monitor.store import Store
root=Path(__file__).resolve().parent
store=Store(root/'data/history.db')
text='Codex usage reset is planned. This is a fixture.'
body='分类：待核实\n账号：@example / ID 123 / 个人账号\n\n作者原文：\n'+text
with store.transaction():
    store.record_post('123', 'a'*20, 'uncertain', json.dumps({'data':{'id':'123','text':text,'created_at':'2026-09-30T00:00:00Z'}}))
    store.enqueue('post:123:'+ 'a'*20, '[Codex] 待核实', body, now=1790726400, destination='console:')
store.close()
'@
[IO.File]::WriteAllText((Join-Path $fixture 'seed.py'), $seed, $utf8)
$python = [IO.File]::ReadAllText((Join-Path $fixture 'data/desktop/python-path.txt')).Trim()
& $python (Join-Path $fixture 'seed.py')
if ($LASTEXITCODE -ne 0) { throw 'History bridge fixture initialization failed' }
$assembly = [Reflection.Assembly]::LoadFrom($Exe)
$type = $assembly.GetType('CodexQuotaDesktop.HistoryClient')
$constructor = $type.GetConstructor([Type[]]@([string]))
$bridge = $constructor.Invoke([object[]]@([string]$fixture))
$page = $bridge.List('all', 0, 50)
if ($page.total -ne 1 -or $page.items[0].label -ne 'unreviewed') { throw 'Native bridge did not load historical record' }
$id = $page.items[0].id
$saved = $bridge.Set($id, 'confirmed')
if ($saved.label -ne 'confirmed') { throw 'Native save did not confirm persisted label' }
$again = $constructor.Invoke([object[]]@([string]$fixture))
if ($again.Get($id).label -ne 'confirmed' -or $again.List('confirmed', 0, 50).total -ne 1) { throw 'Native round trip lost saved classification' }
if ($again.List('unreviewed', 0, 50).total -ne 0) { throw 'Filtered counts were not refreshed' }
$null = $again.Set($id, 'unreviewed')
if ($bridge.Get($id).label -ne 'unreviewed') { throw 'Native bridge failed to clear classification' }
Write-Output 'PASS native history bridge: UTF-8 protocol, real SQLite list/save/reload/filter/clear using isolated fixture; no mail or production DB touched.'
