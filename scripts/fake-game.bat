@echo off
setlocal
cd /d "%~dp0\.."
python tools\fake_game.py
pause
