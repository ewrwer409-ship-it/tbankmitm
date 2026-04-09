@echo off
rem VPS-style on Windows: all interfaces :8082, panel without host filter (like start_vps.sh).
rem Same mitm addon chain as start.bat via _proxy_cmd.bat.
cd /d "%~dp0"
title Mitmproxy 0.0.0.0:8082 + panel /admin (VPS-style)

taskkill /F /IM mitmdump.exe >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8082" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)

echo.
echo ============================================================
echo   Listen: 0.0.0.0:8082   phone / LAN can use this PC IP
echo   Panel:  http://THIS_PC_IP:8082/admin
echo   TBANKMITM_PANEL_ALLOW_ANY=1 (no IP lock in panel_bridge)
echo   Keep this window open. Ctrl+C = stop.
echo ============================================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo ERROR: No venv. Run setup.bat once in this folder.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo ERROR: Broken venv. Delete folder venv, run setup.bat again.
    pause
    exit /b 1
)

venv\Scripts\python.exe -c "import mitmproxy" 2>nul
if errorlevel 1 (
    echo ERROR: mitmproxy missing. Run setup.bat or install_mitm.bat
    pause
    exit /b 1
)

set TBANKMITM_PANEL_ALLOW_ANY=1
set TBANKMITM_PROXY_LISTEN_HOST=0.0.0.0

call "%~dp0_proxy_cmd.bat"

echo.
echo Proxy stopped.
pause
