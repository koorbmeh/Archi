@echo off
REM Run test_local_model.py with CUDA DLLs on PATH (same env as build).
call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=amd64
set "PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin;%PATH%"
set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1"
cd /d "C:\Repos\Archi"
"%~dp0..\venv\Scripts\python.exe" test_local_model.py
echo.
echo --- Optional: run router test (local + Grok) in this same window ---
"%~dp0..\venv\Scripts\python.exe" test_router.py
pause
