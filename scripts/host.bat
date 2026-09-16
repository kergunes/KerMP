@echo off
setlocal
cd /d "%~dp0\.."
python -m kermp.cli host --name %~1
pause
