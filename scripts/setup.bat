@echo off
setlocal
cd /d "%~dp0\.."

echo === KerMP setup ===
python -m pip install -e .
if errorlevel 1 goto :fail

echo.
echo === Preflight ===
python -m kermp.preflight
echo.

echo === Build / install Sims mod (packaged) ===
python sims_mod_src\build.py
if errorlevel 1 (
  echo.
  echo NOTE: The Sims mod build needs Python 3.7 for the .ts4script. If you already
  echo installed KerMP.ts4script this is fine. To rebuild, set PYTHON37 to a 3.7 exe.
)

echo.
echo Setup complete. Run scripts\gui.bat to start Host/Join.
pause
exit /b 0

:fail
echo.
echo Setup failed. See the error above.
pause
exit /b 1
