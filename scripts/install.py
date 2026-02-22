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
        choice = "1"
    else:
        print("  Auto-start uses two layers:")
        print("    - Task Scheduler: runs Archi headless at boot (even without login)")
        print("    - Startup folder: opens a visible monitor terminal when you log in")
        print()
        print("  [1] Enable auto-start (both layers)")
        print("  [2] Remove auto-start (both layers)")
        print("  [S] Skip\n")

        choice = input("Select (1/2/S): ").strip().upper()

    if choice == "1":
        (ROOT / "logs").mkdir(exist_ok=True)
        headless_bat = ROOT / "scripts" / "startup_archi_headless.bat"
        monitor_bat = ROOT / "scripts" / "startup_archi_monitor.bat"

        if not headless_bat.exists():
            print(f"  [ERROR] {headless_bat} not found.")
            return
        if not monitor_bat.exists():
            print(f"  [ERROR] {monitor_bat} not found.")
            return

        # --- Layer 1: Task Scheduler (headless at boot) ---
        print("\n  Setting up Layer 1: Task Scheduler (headless at boot)...")
        task_name = "ArchiAutoStart"
        temp_ps1 = ROOT / "scripts" / "_setup_autostart.ps1"
        # Uses current user with "Run whether user is logged on or not".
        # -LogonType S4U = "Service for User" — runs as the current user
        # without needing a stored password, but without interactive desktop.
        temp_ps1.write_text(
            f'$ErrorActionPreference = "Stop"\n'
            f'$action = New-ScheduledTaskAction '
            f'-Execute "{headless_bat}" '
            f'-WorkingDirectory "{ROOT}"\n'
            f'$trigger = New-ScheduledTaskTrigger -AtStartup\n'
            f'$trigger.Delay = "PT30S"\n'
            f'$principal = New-ScheduledTaskPrincipal '
            f'-UserId "$env:USERDOMAIN\\$env:USERNAME" '
            f'-LogonType S4U '
            f'-RunLevel Limited\n'
            f'$settings = New-ScheduledTaskSettingsSet '
            f'-AllowStartIfOnBatteries '
            f'-DontStopIfGoingOnBatteries '
            f'-StartWhenAvailable '
            f'-ExecutionTimeLimit (New-TimeSpan -Days 365)\n'
            f'Unregister-ScheduledTask -TaskName "{task_name}" '
            f'-Confirm:$false -ErrorAction SilentlyContinue\n'
            f'Register-ScheduledTask -TaskName "{task_name}" '
            f'-Action $action -Trigger $trigger '
            f'-Principal $principal -Settings $settings '
            f'-Description "Start Archi AI agent headless at boot"\n'
            f'Write-Host "Task Scheduler: {task_name} registered." '
            f'-ForegroundColor Green\n',
            encoding="utf-8",
        )

        ts_result = run(
            f'powershell -ExecutionPolicy Bypass -File "{temp_ps1}"',
            check=False,
        )
        try:
            temp_ps1.unlink()
        except OSError:
            pass

        if ts_result != 0:
            print("  [WARNING] Task Scheduler setup failed (may need admin).")
            print("  You can retry from an elevated prompt, or Archi will still")
            print("  start via the Startup folder when you log in.")

        # --- Layer 2: Startup folder shortcut (visible monitor on login) ---
        print("\n  Setting up Layer 2: Startup folder (visible terminal on login)...")
        temp_ps1 = ROOT / "scripts" / "_setup_startup_shortcut.ps1"
        temp_ps1.write_text(
            f'$ws = New-Object -ComObject WScript.Shell\n'
            f'$startup = $ws.SpecialFolders("Startup")\n'
            f'$lnk = $ws.CreateShortcut("$startup\\Archi.lnk")\n'
            f'$lnk.TargetPath = "{monitor_bat}"\n'
            f'$lnk.WorkingDirectory = "{ROOT}"\n'
            f'$lnk.Description = "Archi AI Agent Monitor"\n'
            f'$lnk.WindowStyle = 1\n'
            f'$lnk.Save()\n'
            f'Write-Host "Startup folder: Archi.lnk created at $startup" '
            f'-ForegroundColor Green\n',
            encoding="utf-8",
        )

        sf_result = run(
            f'powershell -ExecutionPolicy Bypass -File "{temp_ps1}"',
            check=False,
        )
        try:
            temp_ps1.unlink()
        except OSError:
            pass

        if sf_result != 0:
            print("  [ERROR] Startup folder shortcut failed.")
            print("  Manual fix: Win+R → shell:startup → create shortcut to:")
            print(f"  {monitor_bat}")

        print()
        if ts_result == 0 and sf_result == 0:
            print("  Both layers enabled!")
            print("  - Boot (no login needed): Archi starts headless via Task Scheduler")
            print("  - Login: visible terminal opens to monitor Archi")
        elif sf_result == 0:
            print("  Startup folder enabled (visible terminal on login).")
            print("  Task Scheduler failed — Archi will only start when you log in.")
        print("  To disable: run this script and choose option 2.")

    elif choice == "2":
        temp_ps1 = ROOT / "scripts" / "_remove_autostart.ps1"
        temp_ps1.write_text(
            '# Remove Startup folder shortcut\n'
            '$ws = New-Object -ComObject WScript.Shell\n'
            '$startup = $ws.SpecialFolders("Startup")\n'
            '$lnk = "$startup\\Archi.lnk"\n'
            'if (Test-Path $lnk) {\n'
            '    Remove-Item $lnk -Force\n'
            '    Write-Host "Removed Archi shortcut from Startup folder." '
            '-ForegroundColor Green\n'
            '} else {\n'
            '    Write-Host "No Archi shortcut found in Startup folder." '
            '-ForegroundColor Yellow\n'
            '}\n'
            '# Remove Task Scheduler entry\n'
            'Unregister-ScheduledTask -TaskName "ArchiAutoStart" '
            '-Confirm:$false -ErrorAction SilentlyContinue\n'
            'Write-Host "Task Scheduler: ArchiAutoStart removed." '
            '-ForegroundColor Green\n',
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
        print("  Auto-start removed (both layers).")

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
