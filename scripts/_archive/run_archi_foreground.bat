@echo off
REM Run Archi in a visible terminal window (for when it was started in background)
REM Double-click or run: scripts\run_archi_foreground.bat

cd /d "%~dp0.."
echo Starting Archi in foreground (Ctrl+C to stop)...
echo.
venv\Scripts\python.exe scripts\start_archi.py
pause
