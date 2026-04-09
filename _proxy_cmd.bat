@echo off
cd /d "%~dp0"

rem start.bat: 127.0.0.1 | start_vps.bat: set TBANKMITM_PROXY_LISTEN_HOST=0.0.0.0 before call
if not defined TBANKMITM_PROXY_LISTEN_HOST set TBANKMITM_PROXY_LISTEN_HOST=127.0.0.1

rem Single addon chain for local and VPS-style Windows runs (same as start_vps.sh)
set "MITMARGS=-s transfer.py -s controller.py -s balance.py -s history.py -s operation_detail.py -s name.py -s reki.py -s panel_bridge.py -s browser_ops_injector.py -s tbank_sbp_debit_injector.py --listen-host %TBANKMITM_PROXY_LISTEN_HOST% -p 8082 --set block_global=false --set ssl_insecure=true --set http2=false"

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
"venv\Scripts\python.exe" "%~dp0mitm_run_dump.py" %MITMARGS%
set MITM_EXIT=%ERRORLEVEL%
if not %MITM_EXIT%==0 (
    echo.
    echo mitmproxy exited with code %MITM_EXIT%
)
exit /b %MITM_EXIT%
