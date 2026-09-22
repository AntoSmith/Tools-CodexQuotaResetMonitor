@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -m quota_monitor browser-login --config config.browser.local.toml
pause
