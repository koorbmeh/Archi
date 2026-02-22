@echo off
REM Archi headless startup (runs via Task Scheduler at boot, before user login).
REM Output goes to logs/startup.log since there is no visible terminal.
set "ARCHI_ROOT=%~dp0.."
cd /d "%ARCHI_ROOT%"

if not exist "%ARCHI_ROOT%\venv\Scripts\python.exe" exit /b 1
if not exist "%ARCHI_ROOT%\scripts\start.py" exit /b 1
if not exist "%ARCHI_ROOT%\logs" mkdir "%ARCHI_ROOT%\logs"

"%ARCHI_ROOT%\venv\Scripts\python.exe" "%ARCHI_ROOT%\scripts\start.py" watchdog >> "%ARCHI_ROOT%\logs\startup.log" 2>&1
exit /b %ERRORLEVEL%
