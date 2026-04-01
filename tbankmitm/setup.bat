@echo off
cd /d "%~dp0"

echo Step 1: create venv
py -3 -m venv venv 2>nul
if errorlevel 1 (
    python -m venv venv 2>nul
)
if not exist "venv\Scripts\python.exe" (
    echo ERROR: Cannot create venv. Install Python from python.org and check "Add to PATH".
    pause
    exit /b 1
)

echo Step 2-4: pip + mitmproxy - direct PyPI, no proxy / no pip.ini
venv\Scripts\python.exe "%~dp0pip_install_mitm.py"
if errorlevel 1 (
    echo ERROR: pip or mitmproxy failed. Check internet, firewall, VPN.
    pause
    exit /b 1
)

echo.
echo OK. Now run start.bat
pause
