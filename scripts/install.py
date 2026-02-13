#!/usr/bin/env python3
r"""
Archi Installer — consolidated setup script.

Handles all installation, model downloads, CUDA setup, auto-start,
and optional component installation in one place.

Usage:
    .\venv\Scripts\python.exe scripts\install.py              (interactive menu)
    .\venv\Scripts\python.exe scripts\install.py deps          (install core deps)
    .\venv\Scripts\python.exe scripts\install.py models        (download AI models)
    .\venv\Scripts\python.exe scripts\install.py voice         (install voice deps)
    .\venv\Scripts\python.exe scripts\install.py imagegen      (install image gen deps)
    .\venv\Scripts\python.exe scripts\install.py videogen      (install video gen deps)
    .\venv\Scripts\python.exe scripts\install.py cuda          (diagnose / build CUDA)
    .\venv\Scripts\python.exe scripts\install.py autostart     (Windows auto-start)
    .\venv\Scripts\python.exe scripts\install.py all           (everything)
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
VENV_PIP = ROOT / "venv" / "Scripts" / "pip.exe"
MODELS_DIR = ROOT / "models"
ENV_PATH = ROOT / ".env"

# Use venv python/pip if available, else system
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
PIP = str(VENV_PIP) if VENV_PIP.exists() else f"{sys.executable} -m pip"


def _header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def _run(cmd: str, check: bool = True) -> int:
    """Run a shell command and return exit code."""
    print(f"  > {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=str(ROOT))
    if check and result.returncode != 0:
        print(f"  [WARNING] Command exited with code {result.returncode}")
    return result.returncode


def _set_env(name: str, value: str) -> None:
    """Set a key=value in .env (create or update)."""
    env_content = ""
    if ENV_PATH.is_file():
        env_content = ENV_PATH.read_text(encoding="utf-8")
    if f"{name}=" in env_content:
        env_content = re.sub(rf"{re.escape(name)}=.*", f"{name}={value}", env_content)
    else:
        env_content = env_content.rstrip() + f"\n{name}={value}\n"
    ENV_PATH.write_text(env_content, encoding="utf-8")
    print(f"  Set {name}={value} in .env")


# ── 1. Core Dependencies ──────────────────────────────────────

def install_deps() -> None:
    _header("Installing Core Dependencies")
    req_file = ROOT / "requirements.txt"
    if not req_file.exists():
        print("  [ERROR] requirements.txt not found!")
        return
    _run(f'"{PYTHON}" -m pip install -r "{req_file}"')
    print("\n  Core dependencies installed.")


# ── 2. Model Downloads ────────────────────────────────────────

def download_models() -> None:
    _header("Model Downloads")
    MODELS_DIR.mkdir(exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("  huggingface_hub not installed. Installing...")
        _run(f'"{PYTHON}" -m pip install huggingface_hub')
        from huggingface_hub import hf_hub_download

    models = {
        "1": {
            "name": "Vision Model (Qwen3-VL-8B, ~5 GB + 1.2 GB projector)",
            "repo": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
            "files": [
                ("Qwen3VL-8B-Instruct-Q4_K_M.gguf", "LOCAL_VISION_MODEL_PATH"),
                ("mmproj-Qwen3VL-8B-Instruct-F16.gguf", "LOCAL_VISION_MMPROJ_PATH"),
            ],
        },
        "2": {
            "name": "Reasoning Model (DeepSeek-R1-Distill-8B, ~4.9 GB)",
            "repo": "bartowski/DeepSeek-R1-Distill-Llama-8B-GGUF",
            "files": [
                ("DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf", "REASONING_MODEL_PATH"),
            ],
        },
        "3": {
            "name": "Legacy Text Model (Qwen2.5-14B, ~9 GB)",
            "repo": "bartowski/Qwen2.5-14B-Instruct-GGUF",
            "files": [
                ("Qwen2.5-14B-Instruct-Q4_K_M.gguf", "LOCAL_MODEL_PATH"),
            ],
        },
    }

    print("Available models:\n")
    for key, info in models.items():
        print(f"  [{key}] {info['name']}")
    print(f"  [A] All models")
    print(f"  [S] Skip\n")

    choice = input("Select (1/2/3/A/S): ").strip().upper()
    if choice == "S":
        return

    selected = list(models.keys()) if choice == "A" else [choice]

    for key in selected:
        if key not in models:
            print(f"  Unknown option: {key}")
            continue
        info = models[key]
        print(f"\n  Downloading: {info['name']}")
        print(f"  Source: {info['repo']}\n")
        for filename, env_var in info["files"]:
            dest = MODELS_DIR / filename
            if dest.exists():
                size_gb = dest.stat().st_size / (1024**3)
                print(f"  Already exists: {filename} ({size_gb:.2f} GB)")
            else:
                print(f"  Downloading {filename}...")
                hf_hub_download(
                    repo_id=info["repo"],
                    filename=filename,
                    local_dir=str(MODELS_DIR),
                )
                if not dest.exists():
                    print(f"  [ERROR] Download finished but {filename} not found!")
                    continue
                size_gb = dest.stat().st_size / (1024**3)
                print(f"  Done: {filename} ({size_gb:.2f} GB)")
            _set_env(env_var, dest.resolve().as_posix())

    print("\n  Model download complete.")
    print("\n  Dual-model architecture:")
    print("    Vision model:    Qwen3-VL-8B (images, screen reading)")
    print("    Reasoning model: DeepSeek-R1-Distill-8B (goals, planning, text)")
    print("    Set ARCHI_DUAL_MODEL=off in .env to disable dual-model mode.")


# ── 3. Voice Dependencies ─────────────────────────────────────

def install_voice() -> None:
    _header("Installing Voice Dependencies")
    print("  STT: faster-whisper (CTranslate2-based Whisper)")
    print("  TTS: piper-tts (lightweight ONNX)")
    print("  Audio: sounddevice + numpy (bundles PortAudio, no C compiler needed)\n")

    _run(f'"{PYTHON}" -m pip install faster-whisper piper-tts sounddevice numpy')

    # Piper voice model
    piper_dir = MODELS_DIR / "piper"
    piper_dir.mkdir(parents=True, exist_ok=True)
    voice_name = "en_US-lessac-medium"
    onnx_file = piper_dir / f"{voice_name}.onnx"
    json_file = piper_dir / f"{voice_name}.onnx.json"

    if onnx_file.exists() and json_file.exists():
        print(f"\n  Piper voice already downloaded: {voice_name}")
    else:
        print(f"\n  Downloading Piper voice: {voice_name} from Hugging Face...")
        # Piper voices are hosted on HF: rhasspy/piper-voices
        hf_repo = "rhasspy/piper-voices"
        hf_revision = "v1.0.0"
        # Files live under: en/en_US/lessac/medium/
        hf_onnx = f"en/en_US/lessac/medium/{voice_name}.onnx"
        hf_json = f"en/en_US/lessac/medium/{voice_name}.onnx.json"
        try:
            from huggingface_hub import hf_hub_download

            print(f"  Downloading {voice_name}.onnx...")
            hf_hub_download(
                repo_id=hf_repo, filename=hf_onnx, revision=hf_revision,
                local_dir=str(piper_dir), local_dir_use_symlinks=False,
            )
            # hf_hub_download preserves the subfolder structure, so move files up
            nested_onnx = piper_dir / hf_onnx
            if nested_onnx.exists() and not onnx_file.exists():
                import shutil
                shutil.move(str(nested_onnx), str(onnx_file))

            print(f"  Downloading {voice_name}.onnx.json...")
            hf_hub_download(
                repo_id=hf_repo, filename=hf_json, revision=hf_revision,
                local_dir=str(piper_dir), local_dir_use_symlinks=False,
            )
            nested_json = piper_dir / hf_json
            if nested_json.exists() and not json_file.exists():
                import shutil
                shutil.move(str(nested_json), str(json_file))

            # Clean up nested directories left by hf_hub_download
            nested_dir = piper_dir / "en"
            if nested_dir.is_dir():
                import shutil
                shutil.rmtree(str(nested_dir), ignore_errors=True)

        except Exception as e:
            print(f"  Download error: {e}")

        if onnx_file.exists() and json_file.exists():
            size_mb = onnx_file.stat().st_size / (1024**2)
            print(f"  Piper voice downloaded: {voice_name} ({size_mb:.1f} MB)")
        else:
            print(
                f"  [NOTE] Auto-download may have failed. Download manually:\n"
                f"    https://huggingface.co/rhasspy/piper-voices/tree/v1.0.0/en/en_US/lessac/medium\n"
                f"    Place {voice_name}.onnx and {voice_name}.onnx.json in:\n"
                f"    {piper_dir}"
            )

    print("\n  To enable voice, set ARCHI_VOICE_ENABLED=true in .env")
    print("  Voice dependencies installed.")


# ── 4. Image Generation Dependencies ─────────────────────────

def install_imagegen() -> None:
    _header("Installing Image Generation Dependencies")

    # Step 1: Check if PyTorch has CUDA support
    print("  Checking PyTorch CUDA support...")
    torch_cuda_ok = False
    try:
        check = subprocess.run(
            f'"{PYTHON}" -c "import torch; print(torch.cuda.is_available())"',
            shell=True, capture_output=True, text=True, cwd=str(ROOT),
        )
        torch_cuda_ok = "True" in check.stdout
    except Exception:
        pass

    if not torch_cuda_ok:
        print("  PyTorch does NOT have CUDA support — image gen will be CPU-only (very slow).")
        print()
        print("  To fix this, install PyTorch with CUDA. Your options:")
        print()
        print("  [1] Install PyTorch + CUDA 12.8 (recommended for most NVIDIA GPUs)")
        print("  [2] Install PyTorch + CUDA 12.6")
        print("  [S] Skip (keep CPU-only)\n")

        torch_choice = input("Select [1]: ").strip() or "1"
        if torch_choice == "1":
            print("\n  Installing PyTorch with CUDA 12.8 (force-reinstall to replace CPU-only build)...")
            _run(f'"{PYTHON}" -m pip install torch torchvision --force-reinstall --index-url https://download.pytorch.org/whl/cu128')
        elif torch_choice == "2":
            print("\n  Installing PyTorch with CUDA 12.6 (force-reinstall to replace CPU-only build)...")
            _run(f'"{PYTHON}" -m pip install torch torchvision --force-reinstall --index-url https://download.pytorch.org/whl/cu126')
        else:
            print("  Skipping PyTorch CUDA install. Image gen will use CPU.")
    else:
        print("  PyTorch CUDA: OK")

    print()
    print("  Pipeline:  diffusers (Stable Diffusion XL)")
    print("  Tokeniser: transformers (CLIPTextModel)")
    print("  Accel:     accelerate (GPU inference)")
    print("  Loader:    safetensors (safe model loading)\n")

    _run(f'"{PYTHON}" -m pip install diffusers transformers accelerate safetensors')

    print("\n  Image generation dependencies installed.")
    print()
    print("  Next step: download an SDXL-compatible .safetensors model")
    print("  and place it in the models/ directory, or set IMAGE_MODEL_PATH")
    print("  in your .env file.")
    print()
    print("  Recommended sources:")
    print("    - CivitAI:     https://civitai.com  (search for SDXL)")
    print("    - Hugging Face: stabilityai/stable-diffusion-xl-base-1.0")
    print()
    print("  The image generator will auto-detect .safetensors files in models/")
    print("  whose names contain 'sdxl', 'stable', 'pony', or 'juggernaut'.")


# ── 5. Video Generation Dependencies ─────────────────────────

def install_videogen() -> None:
    _header("Installing Video Generation Dependencies (WAN 2.1)")

    print("  Video generation requires diffusers (shared with image gen)")
    print("  plus video I/O libraries for MP4 export.\n")

    # Check if diffusers is already installed (from image gen setup)
    diffusers_ok = False
    try:
        check = subprocess.run(
            f'"{PYTHON}" -c "import diffusers; print(diffusers.__version__)"',
            shell=True, capture_output=True, text=True, cwd=str(ROOT),
        )
        if check.returncode == 0:
            diffusers_ok = True
            print(f"  diffusers: {check.stdout.strip()} (already installed)")
    except Exception:
        pass

    if not diffusers_ok:
        print("  diffusers not found — installing core deps...")
        _run(f'"{PYTHON}" -m pip install diffusers transformers accelerate safetensors')

    print()
    print("  Video I/O:    imageio + imageio-ffmpeg (MP4 encoding)")
    print("  Tokeniser:    sentencepiece (WAN text encoder)")
    print("  Text clean:   ftfy (prompt cleaning for WAN pipeline)\n")

    _run(f'"{PYTHON}" -m pip install imageio imageio-ffmpeg sentencepiece ftfy')

    print("\n  Video generation dependencies installed.\n")

    # ── Model pre-download ──────────────────────────────────────
    # WAN models are full HuggingFace pipeline directories (not single files).
    # We use snapshot_download() to pre-cache them so first video gen is fast.

    video_models = {
        "1": {
            "name": "T2V: Wan2.1-T2V-1.3B  (~29 GB, 8 GB VRAM w/ CPU offload)",
            "repo": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
            "env_var": "VIDEO_T2V_MODEL_PATH",
        },
        "2": {
            "name": "I2V: Wan2.1-I2V-14B-480P  (~50 GB, heavy CPU offload)",
            "repo": "Wan-AI/Wan2.1-I2V-14B-480P-Diffusers",
            "env_var": "VIDEO_I2V_MODEL_PATH",
        },
    }

    print("  Download video models now?\n")
    print("  WAN models are large HuggingFace pipelines (includes UMT5-XXL text encoder).")
    print("  Pre-downloading avoids a surprise download on first use.\n")
    for key, info in video_models.items():
        print(f"  [{key}] {info['name']}")
    print("  [A] Both models")
    print("  [S] Skip (models will auto-download on first use)\n")

    choice = input("Select (1/2/A/S): ").strip().upper()
    if choice != "S":
        selected = list(video_models.keys()) if choice == "A" else [choice]

        for key in selected:
            if key not in video_models:
                print(f"  Unknown option: {key}")
                continue
            info = video_models[key]
            repo_id = info["repo"]
            env_var = info["env_var"]

            # Check if already set via env var (user has a custom path)
            existing = os.environ.get(env_var, "").strip()
            if existing and os.path.isdir(existing):
                print(f"\n  {env_var} already points to: {existing}")
                print(f"  Skipping download for {repo_id}")
                continue

            print(f"\n  Downloading: {info['name']}")
            print(f"  Source: {repo_id}")
            print(f"  This may take a while for large models...\n")

            try:
                from huggingface_hub import snapshot_download

                # Download to HF cache (default behaviour).
                # snapshot_download returns the local directory path.
                local_dir = snapshot_download(
                    repo_id=repo_id,
                    repo_type="model",
                )
                print(f"\n  Downloaded to: {local_dir}")
                _set_env(env_var, local_dir)
            except ImportError:
                print("  [ERROR] huggingface_hub not installed. Run: pip install huggingface_hub")
                print(f"  Model will auto-download on first use instead.")
            except Exception as e:
                print(f"  [ERROR] Download failed: {e}")
                print(f"  Model will auto-download on first use instead.")

    print()
    print("  Output: 480p (832x480), 49 frames @ 16 FPS = ~3 second MP4 videos")
    print()
    print("  Usage:")
    print('    Discord:  "generate a video of a dog running"  (text-to-video)')
    print('    Discord:  [attach image] "animate this"        (image-to-video)')
    print()
    print("  Override model paths with env vars:")
    print("    VIDEO_T2V_MODEL_PATH — any HF repo ID or local directory")
    print("    VIDEO_I2V_MODEL_PATH — any HF repo ID or local directory")


# ── 6. CUDA Diagnostics & Build ───────────────────────────────

def _run_cuda_diagnostics() -> None:
    """Run CUDA diagnostics inline (always called first)."""
    print("  Loading CUDA bootstrap...")
    _run(f'"{PYTHON}" -c "import src.core.cuda_bootstrap"')
    print()

    print("  CUDA Diagnostic Report")
    print("  " + "=" * 56)

    cuda_path = os.environ.get("CUDA_PATH", "")
    print(f"\n  CUDA_PATH: {cuda_path or 'NOT SET'}")

    # Check PATH for CUDA entries
    path_entries = os.environ.get("PATH", "").split(";")
    cuda_in_path = [p for p in path_entries if "CUDA" in p.upper()]
    if cuda_in_path:
        print("\n  CUDA in PATH:")
        for p in cuda_in_path:
            exists = "Y" if os.path.exists(p) else "N"
            print(f"    [{exists}] {p}")
    else:
        print("  No CUDA entries in PATH")

    # Check common install locations
    print("\n  Checking common CUDA locations:")
    found = False
    for base in [
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA",
        r"C:\Program Files\NVIDIA\CUDA",
    ]:
        if os.path.exists(base):
            try:
                versions = [d for d in os.listdir(base) if d.startswith("v")]
                if versions:
                    for v in sorted(versions):
                        vpath = os.path.join(base, v)
                        has_bin = os.path.isdir(os.path.join(vpath, "bin"))
                        print(f"    Found: {v} at {vpath} (bin: {'Y' if has_bin else 'N'})")
                        found = True
            except OSError:
                pass
    if not found:
        print("    No CUDA installations found")

    # Check llama-cpp-python and test actual GPU support
    # Write a temp script to avoid nested-quote issues on Windows
    print("\n  llama-cpp-python:")
    gpu_check_script = ROOT / "scripts" / "_gpu_check.py"
    gpu_check_script.write_text(
        "import sys\n"
        "try:\n"
        "    import llama_cpp\n"
        "    print(f'version={llama_cpp.__version__}')\n"
        "except ImportError:\n"
        "    print('not_installed=true')\n"
        "    sys.exit(0)\n"
        "\n"
        "from pathlib import Path\n"
        "pkg_dir = Path(llama_cpp.__file__).parent\n"
        "cuda_dlls = [f.name for f in pkg_dir.glob('*.dll') if 'cuda' in f.name.lower()]\n"
        "if cuda_dlls:\n"
        "    print(f'cuda_dlls={\",\".join(cuda_dlls)}')\n"
        "\n"
        "try:\n"
        "    from llama_cpp import llama_supports_gpu_offload\n"
        "    print(f'gpu_offload={llama_supports_gpu_offload()}')\n"
        "except Exception as e:\n"
        "    print(f'gpu_error={e}')\n",
        encoding="utf-8",
    )

    gpu_result = subprocess.run(
        f'"{PYTHON}" "{gpu_check_script}"',
        shell=True, capture_output=True, text=True, cwd=str(ROOT),
    )
    try:
        gpu_check_script.unlink()
    except OSError:
        pass

    # Parse results
    info = {}
    for line in gpu_result.stdout.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()

    if "not_installed" in info:
        print("    NOT INSTALLED")
    else:
        print(f"    Installed: {info.get('version', 'unknown')}")
        if "cuda_dlls" in info:
            print(f"    CUDA DLLs bundled: {info['cuda_dlls']}")

        # GPU offload test result
        print("\n  GPU acceleration test:")
        gpu_offload = info.get("gpu_offload", "")
        if gpu_offload == "True":
            print("    [OK] GPU offloading IS supported — models can use your GPU")
        elif gpu_offload == "False":
            print("    [!!] GPU offloading NOT available — running CPU only")
            print("         To fix: choose option [1] below to rebuild with CUDA, or")
            print("                 choose option [3] for the Qwen3-VL fork")
        elif "gpu_error" in info:
            print(f"    [??] Could not test GPU support: {info['gpu_error'][:100]}")
        else:
            print("    [??] Could not determine GPU support")

    # Summary
    print("\n  " + "=" * 56)
    gpu_ok = info.get("gpu_offload") == "True"
    if not gpu_ok and cuda_in_path and "not_installed" not in info:
        print("  NOTE: CUDA is installed on your system but llama-cpp-python")
        print("  does not have GPU support enabled. Rebuilding with CUDA will")
        print("  significantly speed up model inference (10-20x faster).")


def cuda_setup(auto: bool = False) -> None:
    _header("CUDA Diagnostics & Build")

    # Always run diagnostics first
    _run_cuda_diagnostics()

    # Then offer build/install options
    print("\n  What would you like to do?\n")
    print("  [1] Build llama-cpp-python with CUDA (requires Visual Studio)")
    print("  [2] Install llama-cpp-python CPU-only (pre-built)")
    print("  [3] Install Qwen3-VL fork (vision support)")
    print("  [S] Skip (no changes needed)\n")

    if auto:
        print("  (Auto mode: skipping build — run 'install.py cuda' to build manually)")
        return

    choice = input("Select (1/2/3/S): ").strip().upper()

    if choice == "1":
        print("\n  Building llama-cpp-python with CUDA support...")
        print("  Requires: Visual Studio 2019+ with C++ workload, CUDA Toolkit 12.x\n")
        cuda_path = os.environ.get("CUDA_PATH", "")
        if not cuda_path:
            print("  [WARNING] CUDA_PATH not set. Attempting to find CUDA...")
            for candidate in [
                r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4",
                r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.2",
                r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1",
            ]:
                if os.path.isdir(candidate):
                    cuda_path = candidate
                    break
            if not cuda_path:
                print("  [ERROR] No CUDA installation found. Install CUDA Toolkit first.")
                return

        os.environ["CUDA_PATH"] = cuda_path
        os.environ["CMAKE_ARGS"] = "-DGGML_CUDA=ON"
        os.environ["FORCE_CMAKE"] = "1"
        print(f"  Using CUDA_PATH: {cuda_path}")
        _run(
            f'"{PYTHON}" -m pip install llama-cpp-python --no-cache-dir --force-reinstall'
        )

    elif choice == "2":
        print("\n  Installing llama-cpp-python (CPU-only, pre-built)...")
        _run(
            f'"{PYTHON}" -m pip install llama-cpp-python '
            f"--prefer-binary "
            f"--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
        )

    elif choice == "3":
        print("\n  Installing Qwen3-VL fork (vision support via llama-cpp-python)...")
        _run(
            f'"{PYTHON}" -m pip install '
            f"llama-cpp-python@git+https://github.com/jamepeng/llama-cpp-python"
        )

    elif choice != "S":
        print("  Unknown option.")


# ── 5. Windows Auto-Start ─────────────────────────────────────

def setup_autostart(auto: bool = False) -> None:
    _header("Windows Auto-Start Setup")
    if sys.platform != "win32":
        print("  Auto-start setup is Windows-only.")
        return

    if auto:
        # In "All" mode, set up auto-start automatically
        choice = "1"
    else:
        print("  [1] Setup auto-start on boot (scheduled task)")
        print("  [2] Remove auto-start")
        print("  [3] Install as Windows Service (requires NSSM)")
        print("  [S] Skip\n")

        choice = input("Select (1/2/3/S): ").strip().upper()

    if choice == "1":
        # Create startup batch that uses the new consolidated start.py watchdog
        log_path = ROOT / "logs" / "startup.log"
        (ROOT / "logs").mkdir(exist_ok=True)
        start_script = ROOT / "scripts" / "start.py"

        startup_bat = ROOT / "scripts" / "startup_archi.bat"
        startup_bat.write_text(
            f'@echo off\ncd /d "{ROOT}"\n'
            f'"{VENV_PYTHON}" "{start_script}" watchdog >> "{log_path}" 2>&1\n',
            encoding="ascii",
        )
        print(f"  Created startup batch: {startup_bat}")

        # Write a temp .ps1 script to avoid nested-quote issues
        task_name = "ArchiAutoStart"
        temp_ps1 = ROOT / "scripts" / "_setup_autostart.ps1"
        temp_ps1.write_text(
            f'$ErrorActionPreference = "Stop"\n'
            f'$action = New-ScheduledTaskAction -Execute "{startup_bat}" -WorkingDirectory "{ROOT}"\n'
            f'$trigger = New-ScheduledTaskTrigger -AtStartup\n'
            f'$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest\n'
            f'$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable\n'
            f'Unregister-ScheduledTask -TaskName "{task_name}" -Confirm:$false -ErrorAction SilentlyContinue\n'
            f'Register-ScheduledTask -TaskName "{task_name}" -Action $action -Trigger $trigger '
            f'-Principal $principal -Settings $settings -Description "Auto-start Archi on boot"\n'
            f'Write-Host "Scheduled task {task_name} registered successfully." -ForegroundColor Green\n',
            encoding="utf-8",
        )

        result = _run(
            f'powershell -ExecutionPolicy Bypass -File "{temp_ps1}"',
            check=False,
        )

        # Clean up temp script
        try:
            temp_ps1.unlink()
        except OSError:
            pass

        if result == 0:
            print(f"\n  Auto-start enabled! Archi will start on boot.")
            print(f"  To disable: run this script and choose option 2.")
        else:
            print(f"\n  [NOTE] This likely requires Administrator privileges.")
            print(f"  Try: Right-click PowerShell > Run as Administrator > run this script")

    elif choice == "2":
        temp_ps1 = ROOT / "scripts" / "_remove_autostart.ps1"
        temp_ps1.write_text(
            'Unregister-ScheduledTask -TaskName "ArchiAutoStart" -Confirm:$false\n'
            'Write-Host "ArchiAutoStart task removed." -ForegroundColor Green\n',
            encoding="utf-8",
        )
        _run(
            f'powershell -ExecutionPolicy Bypass -File "{temp_ps1}"',
            check=False,
        )
        try:
            temp_ps1.unlink()
        except OSError:
            pass
        print("  Auto-start removed.")

    elif choice == "3":
        start_script = ROOT / "scripts" / "start.py"
        print("\n  To install as a Windows Service, you need NSSM:")
        print("  Download: https://nssm.cc/download")
        print(f'  Then run:  nssm install Archi "{PYTHON}" "{start_script}" service')

    elif choice != "S":
        print("  Unknown option.")


# ── Main Menu ─────────────────────────────────────────────────

def main_menu() -> None:
    _header("Archi Installer")
    print("  [1] Install core dependencies (requirements.txt)")
    print("  [2] Download AI models (vision, reasoning)")
    print("  [3] Install voice dependencies (STT + TTS)")
    print("  [4] Install image generation dependencies (SDXL)")
    print("  [5] Install video generation dependencies (WAN 2.1)")
    print("  [6] CUDA diagnostics & llama-cpp-python build")
    print("  [7] Windows auto-start setup")
    print("  [A] All of the above")
    print("  [Q] Quit\n")

    choice = input("Select: ").strip().upper()

    if choice == "1":
        install_deps()
    elif choice == "2":
        download_models()
    elif choice == "3":
        install_voice()
    elif choice == "4":
        install_imagegen()
    elif choice == "5":
        install_videogen()
    elif choice == "6":
        cuda_setup()
    elif choice == "7":
        setup_autostart()
    elif choice == "A":
        install_deps()
        download_models()
        install_voice()
        install_imagegen()
        install_videogen()
        cuda_setup(auto=True)
        setup_autostart(auto=True)
    elif choice != "Q":
        print("  Unknown option.")
        main_menu()


def main() -> None:
    os.chdir(str(ROOT))

    # Support direct subcommand: scripts/install.py deps|models|voice|imagegen|videogen|cuda|autostart|all
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        dispatch = {
            "deps": install_deps,
            "models": download_models,
            "voice": install_voice,
            "imagegen": install_imagegen,
            "videogen": install_videogen,
            "cuda": cuda_setup,
            "autostart": setup_autostart,
        }
        if cmd == "all":
            install_deps()
            download_models()
            install_voice()
            install_imagegen()
            install_videogen()
            cuda_setup(auto=True)
            setup_autostart(auto=True)
        elif cmd in dispatch:
            dispatch[cmd]()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: deps, models, voice, imagegen, videogen, cuda, autostart, all")
            sys.exit(1)
    else:
        main_menu()


if __name__ == "__main__":
    main()
