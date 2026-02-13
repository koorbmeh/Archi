@echo off
REM Build llama-cpp-python from source with CUDA. Run from an elevated prompt or double-click.
REM Requires: VS with C++ tools, CUDA Toolkit 12.4+ (or 13.x; VS 2026 STL needs 12.4+), Python venv with pip.

REM Requires: Visual Studio with "Desktop development with C++" workload (adds nmake, cl, etc.)
call "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat" -arch=amd64
if errorlevel 1 (
  echo VsDevCmd.bat not found. Install Visual Studio with C++ workload.
  pause
  exit /b 1
)
where nmake >nul 2>&1
if errorlevel 1 (
  echo nmake not found. Install the "Desktop development with C++" workload in Visual Studio Installer.
  pause
  exit /b 1
)

REM VS 2026 STL requires CUDA 12.4+. Prefer v13.1; fallback v12.2 needs -allow-unsupported-compiler.
set "CUDA_VER=v13.1"
if not exist "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\nvcc.exe" set "CUDA_VER=v12.2"
set "CUDACXX=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\%CUDA_VER%\bin\nvcc.exe"
REM Both 12.2 and 13.1 nvcc only list VS 2019-2022; VS 2026 needs -allow-unsupported-compiler.
set "CMAKE_ARGS=-G Ninja -DCMAKE_BUILD_TYPE=Release -DLLAMA_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=89 -DCMAKE_CUDA_FLAGS=-allow-unsupported-compiler"
set FORCE_CMAKE=1
set "PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\%CUDA_VER%\bin;%PATH%"
set PIP_NO_BUILD_ISOLATION=1

cd /d "C:\Repos\Archi"
echo Building llama-cpp-python with CUDA (20-40 min)...
"C:\Repos\Archi\venv\Scripts\python.exe" -m pip install llama-cpp-python --no-binary llama-cpp-python --force-reinstall --no-cache-dir -v

echo.
if errorlevel 1 (echo Build failed.) else (echo Build succeeded. Run: venv\Scripts\python.exe test_local_model.py)
