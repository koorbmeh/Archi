@echo off
REM Kill background Archi, then open a new visible terminal and run Archi.
REM Use when Archi was started by scheduled task or is running in background.
REM Double-click or run: scripts\run_archi_in_new_window.bat
REM The new window stays open so you can see output and press Ctrl+C to stop.

cd /d "%~dp0.."
echo Stopping any running Archi...
powershell -ExecutionPolicy Bypass -Command "& { Get-NetTCPConnection -LocalPort 5000,5001 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'start_archi|archi_service|run_dashboard' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"
timeout /t 2 /nobreak > nul
echo Starting Archi in new window...
start "Archi" cmd /k "cd /d \"%~dp0..\" && venv\Scripts\python.exe scripts\start_archi.py"
