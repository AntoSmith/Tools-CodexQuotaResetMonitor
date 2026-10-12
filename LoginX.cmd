@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
if exist "%~dp0runtime\node\node.exe" set "PATH=%~dp0runtime\node;%PATH%"
if exist "%~dp0runtime\python\python.exe" (
    "%~dp0runtime\python\python.exe" -m quota_monitor browser-login --config config.browser.local.toml
) else (
    python -m quota_monitor browser-login --config config.browser.local.toml
)
pause
endlocal
