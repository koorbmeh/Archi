@echo off
 REM Run web chat without needing venv activation (works when PowerShell scripts are disabled)
 cd /d "%~dp0.."
 if exist venv\Scripts\python.exe (
     venv\Scripts\python.exe scripts\run_web_chat.py
 ) else (
     python scripts\run_web_chat.py
 )
