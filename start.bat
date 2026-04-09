@echo off
rem ASCII-only: UTF-8 Cyrillic in .bat breaks cmd.exe on many Windows setups.
cd /d "%~dp0"
title Mitmproxy 127.0.0.1:8082 + panel /admin

taskkill /F /IM mitmdump.exe >nul 2>&1
rem mitm is venv\python.exe mitm_run_dump.py - free TCP 8082 by PID
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8082" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)

echo.
echo ============================================================
echo   Listen: 127.0.0.1:8082   proxy + panel, same process
echo   Panel:  http://127.0.0.1:8082/admin
echo   Keep this window open. Close window = stop. Ctrl+C = stop.
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

call "%~dp0_proxy_cmd.bat"

echo.
echo Proxy stopped.
pause
