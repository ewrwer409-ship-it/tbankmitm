@echo off
rem Listen on all interfaces so iPhone / other devices on LAN can use proxy (same port 8082).
cd /d "%~dp0"
set TBANKMITM_PROXY_LISTEN_HOST=0.0.0.0
set TBANKMITM_TRAFFIC_STDOUT=1
call "%~dp0start.bat"
