@echo off
setlocal
cd /d "%~dp0\.."
if "%~1"=="" set /p KERMP_HOST=Host IP: 
if not "%~1"=="" set KERMP_HOST=%~1
python -m kermp.cli join %KERMP_HOST% --name %~2
pause
