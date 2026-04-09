@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo ERROR: No venv. Run setup.bat first.
    pause
    exit /b 1
)

echo Installing mitmproxy into venv - direct PyPI, no proxy / no pip.ini...
venv\Scripts\python.exe "%~dp0pip_install_mitm.py"
if errorlevel 1 (
    echo ERROR: pip failed.
    pause
    exit /b 1
)

echo Done. Run start.bat
pause
