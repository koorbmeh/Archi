@echo off
REM Restart Archi - Kill everything and start fresh
cd /d "%~dp0.."
powershell -ExecutionPolicy Bypass -File "%~dp0restart_archi.ps1"
