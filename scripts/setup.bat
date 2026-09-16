@echo off
setlocal
cd /d "%~dp0\.."
python -m pip install -e .
python -m pytest -q
pause
