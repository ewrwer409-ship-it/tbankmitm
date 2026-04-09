@echo off
cd /d "%~dp0"

taskkill /F /IM mitmdump.exe >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8082" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)

if not exist "venv\Scripts\python.exe" (
    echo ERROR: No venv. Run setup.bat
    pause
    exit /b 1
)

venv\Scripts\python.exe -c "import mitmproxy" 2>nul
if errorlevel 1 (
    echo ERROR: Run setup.bat to install mitmproxy
    pause
    exit /b 1
)

echo New window: mitmproxy. Proxy 127.0.0.1:8082  Panel: http://127.0.0.1:8082/admin
rem start /D = working dir for child cmd (must be project folder).
start "mitmdump" /D "%~dp0" cmd.exe /k "_proxy_cmd.bat & echo. & pause"
