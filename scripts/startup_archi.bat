@echo off
REM Archi startup script
REM   Default: no web interfaces (no open ports)
REM   Add --web at the end to enable dashboard (5000) + web chat (5001)
cd /d "C:\Users\koorb\.cursor\projects\Archi"
"C:\Users\koorb\.cursor\projects\Archi\venv\Scripts\python.exe" "C:\Users\koorb\.cursor\projects\Archi\scripts\start.py" watchdog %* >> "C:\Users\koorb\.cursor\projects\Archi\logs\startup.log" 2>&1
