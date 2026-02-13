@echo off
cd /d "%~dp0.."
powershell -ExecutionPolicy Bypass -File "%~dp0run_archi_watchdog.ps1" >> "%~dp0..\logs\startup.log" 2>&1
