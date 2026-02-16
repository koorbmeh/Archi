@echo off
REM Archi startup script — runs watchdog mode (auto-restart on crash)
REM Derive project root from this script's location (scripts\startup_archi.bat -> ..)
set "ARCHI_ROOT=%~dp0.."
cd /d "%ARCHI_ROOT%"
"%ARCHI_ROOT%\venv\Scripts\python.exe" "%ARCHI_ROOT%\scripts\start.py" watchdog %* >> "%ARCHI_ROOT%\logs\startup.log" 2>&1
