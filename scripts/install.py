#!/usr/bin/env python3
r"""
Archi Installer — consolidated setup script.

Handles installation, voice setup, image generation, and optional features.

Usage:
    .\venv\Scripts\python.exe scripts\install.py              (interactive menu)
    .\venv\Scripts\python.exe scripts\install.py deps          (install core deps)
    .\venv\Scripts\python.exe scripts\install.py voice         (install voice deps)
    .\venv\Scripts\python.exe scripts\install.py imagegen      (install image gen deps)
    .\venv\Scripts\python.exe scripts\install.py autostart     (Windows auto-start)
    .\venv\Scripts\python.exe scripts\install.py all           (everything)
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, PYTHON, PIP, header, run, set_env

MODELS_DIR = ROOT / "models"


# ── 1. Core Dependencies ──────────────────────────────────────

def install_deps() -> None:
    header("Installing Core Dependencies")
    req_file = ROOT / "requirements.txt"
    if not req_file.exists():
        print("  [ERROR] requirements.txt not found!")
        return
    run(f'"{PYTHON}" -m pip install -r "{req_file}"')
    print("\n  Core dependencies installed.")


# ── 2. Voice Dependencies ─────────────────────────────────────

def install_voice() -> None:
    header("Installing Voice Dependencies")
    print("  STT: faster-whisper (CTranslate2-based Whisper)")
    print("  TTS: piper-tts (lightweight ONNX)")
    print("  Audio: sounddevice + numpy (bundles PortAudio, no C compiler needed)\n")

    run(f'"{PYTHON}" -m pip install faster-whisper piper-tts sounddevice numpy')

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


# ── 3. Image Generation Dependencies ─────────────────────────

def install_imagegen() -> None:
    header("Installing Image Generation Dependencies")

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
            run(f'"{PYTHON}" -m pip install torch torchvision --force-reinstall --index-url https://download.pytorch.org/whl/cu128')
        elif torch_choice == "2":
            print("\n  Installing PyTorch with CUDA 12.6 (force-reinstall to replace CPU-only build)...")
            run(f'"{PYTHON}" -m pip install torch torchvision --force-reinstall --index-url https://download.pytorch.org/whl/cu126')
        else:
            print("  Skipping PyTorch CUDA install. Image gen will use CPU.")
    else:
        print("  PyTorch CUDA: OK")

    print()
    print("  Pipeline:  diffusers (Stable Diffusion XL)")
    print("  Tokeniser: transformers (CLIPTextModel)")
    print("  Accel:     accelerate (GPU inference)")
    print("  Loader:    safetensors (safe model loading)\n")

    run(f'"{PYTHON}" -m pip install diffusers transformers accelerate safetensors')

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


# ── 4. Windows Auto-Start ─────────────────────────────────────

def setup_autostart(auto: bool = False) -> None:
    header("Windows Auto-Start Setup")
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
            '@echo off\n'
            'REM Archi startup script (generated by install.py)\n'
            'set "ARCHI_ROOT=%~dp0.."\n'
            'cd /d "%ARCHI_ROOT%"\n'
            '"%ARCHI_ROOT%\\venv\\Scripts\\python.exe" "%ARCHI_ROOT%\\scripts\\start.py"'
            ' watchdog >> "%ARCHI_ROOT%\\logs\\startup.log" 2>&1\n',
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

        result = run(
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
        run(
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
    header("Archi Installer")
    print("  [1] Install core dependencies (requirements.txt)")
    print("  [2] Install voice dependencies (STT + TTS)")
    print("  [3] Install image generation dependencies (SDXL)")
    print("  [4] Windows auto-start setup")
    print("  [A] All of the above")
    print("  [Q] Quit\n")

    choice = input("Select: ").strip().upper()

    if choice == "1":
        install_deps()
    elif choice == "2":
        install_voice()
    elif choice == "3":
        install_imagegen()
    elif choice == "4":
        setup_autostart()
    elif choice == "A":
        install_deps()
        install_voice()
        install_imagegen()
        setup_autostart(auto=True)
    elif choice != "Q":
        print("  Unknown option.")
        main_menu()


def main() -> None:
    os.chdir(str(ROOT))

    # Support direct subcommand: scripts/install.py deps|voice|imagegen|autostart|all
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        dispatch = {
            "deps": install_deps,
            "voice": install_voice,
            "imagegen": install_imagegen,
            "autostart": setup_autostart,
        }
        if cmd == "all":
            install_deps()
            install_voice()
            install_imagegen()
            setup_autostart(auto=True)
        elif cmd in dispatch:
            dispatch[cmd]()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: deps, voice, imagegen, autostart, all")
            sys.exit(1)
    else:
        main_menu()


if __name__ == "__main__":
    main()
