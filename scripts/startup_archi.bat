@echo off
REM Archi startup script
REM   Default: no web interfaces (no open ports)
REM   Add --web at the end to enable dashboard (5000) + web chat (5001)

REM Derive project root from this script's location (scripts\startup_archi.bat -> ..)
set "ARCHI_ROOT=%~dp0.."
cd /d "%ARCHI_ROOT%"
"%ARCHI_ROOT%\venv\Scripts\python.exe" "%ARCHI_ROOT%\scripts\start.py" watchdog %* >> "%ARCHI_ROOT%\logs\startup.log" 2>&1
