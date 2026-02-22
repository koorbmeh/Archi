@echo off
title Archi
REM Archi login monitor — opens a visible terminal on user login.
REM If Archi is already running (started by Task Scheduler at boot),
REM tails the log file. If not running, launches Archi directly.
set "ARCHI_ROOT=%~dp0.."
cd /d "%ARCHI_ROOT%"

REM --- Check if Archi is already running via PID lock ---
if not exist "%ARCHI_ROOT%\data\archi.pid" goto :start_archi

set /p ARCHI_PID=<"%ARCHI_ROOT%\data\archi.pid"
tasklist /FI "PID eq %ARCHI_PID%" /FI "IMAGENAME eq python.exe" 2>nul | find "%ARCHI_PID%" >nul
if %ERRORLEVEL% equ 0 goto :tail_log

REM PID file exists but process is dead — stale lock
echo Stale PID lock found (PID %ARCHI_PID% not running). Starting Archi...
del "%ARCHI_ROOT%\data\archi.pid" 2>nul

:start_archi
echo Archi is not running. Starting in visible terminal...
echo.
"%ARCHI_ROOT%\venv\Scripts\python.exe" "%ARCHI_ROOT%\scripts\start.py" watchdog
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Archi exited with error code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
goto :eof

:tail_log
echo Archi is running (PID %ARCHI_PID%, started at boot).
echo Monitoring log output. Press Ctrl+C to close this window (Archi keeps running).
echo.
if not exist "%ARCHI_ROOT%\logs" mkdir "%ARCHI_ROOT%\logs"

REM Use PowerShell Get-Content -Wait to tail the log (like Unix tail -f)
powershell -Command "if (Test-Path '%ARCHI_ROOT%\logs\startup.log') { Get-Content '%ARCHI_ROOT%\logs\startup.log' -Tail 50 -Wait } else { Write-Host 'Waiting for log file...'; while (-not (Test-Path '%ARCHI_ROOT%\logs\startup.log')) { Start-Sleep 2 }; Get-Content '%ARCHI_ROOT%\logs\startup.log' -Tail 50 -Wait }"
