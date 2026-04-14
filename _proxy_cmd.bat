@echo off
cd /d "%~dp0"

rem start.bat: 127.0.0.1 | start_vps.bat: set TBANKMITM_PROXY_LISTEN_HOST=0.0.0.0 before call
if not defined TBANKMITM_PROXY_LISTEN_HOST set TBANKMITM_PROXY_LISTEN_HOST=127.0.0.1

rem Цепочка аддонов задаётся в mitm_addon_chain.py (один источник с start_vps.sh).

rem mitmproxy 12: "python -m mitmproxy.tools.dump" только импортирует модуль и сразу выходит (нет __main__). Локальный venv + mitm_run_dump.py.
if not exist "venv\Scripts\python.exe" (
    echo ERROR: No venv\Scripts\python.exe - run setup.bat
    pause
    exit /b 1
)
if not exist "%~dp0mitm_run_dump.py" (
    echo ERROR: mitm_run_dump.py missing in %~dp0
    pause
    exit /b 1
)
"venv\Scripts\python.exe" "%~dp0mitm_run_dump.py"
set MITM_EXIT=%ERRORLEVEL%
if not %MITM_EXIT%==0 (
    echo.
    echo mitmproxy exited with code %MITM_EXIT%
)
exit /b %MITM_EXIT%
