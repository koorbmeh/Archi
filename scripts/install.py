#!/usr/bin/env python3
r"""
Archi Installer — consolidated setup and onboarding script.

Handles first-time setup (onboarding), dependency installation, voice setup,
image generation, optional features, and Windows auto-start.

Usage:
    python scripts/install.py                  (interactive menu)
    python scripts/install.py setup            (first-time guided setup)
    python scripts/install.py deps             (install core deps)
    python scripts/install.py voice            (install voice deps)
    python scripts/install.py imagegen         (install image gen deps)
    python scripts/install.py autostart        (Windows auto-start)
    python scripts/install.py all              (everything)
    python scripts/install.py --check          (verify setup without changes)
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, PYTHON, PIP, ENV_PATH, header, run, set_env, load_env

MODELS_DIR = ROOT / "models"

# Python version bounds
MIN_PY = (3, 10)
MAX_PY = (3, 12)

# Config templates: (example_file, target_file, description)
CONFIG_TEMPLATES = [
    ("archi_identity.example.yaml", "archi_identity.yaml", "Agent identity"),
    ("prime_directive.example.txt", "prime_directive.txt", "Operational guidelines"),
    ("mcp_servers.example.yaml", "mcp_servers.yaml", "MCP tool servers"),
    ("personality.yaml.example", "personality.yaml", "Personality framework"),
]

# Required runtime directories
RUNTIME_DIRS = ["data", "logs", "logs/errors", "logs/actions", "workspace"]


# ── I/O Helpers ──────────────────────────────────────────────

def _input(prompt: str, default: str = "") -> str:
    """Input with default value display."""
    if default:
        raw = input(f"  {prompt} [{default}]: ").strip()
        return raw or default
    return input(f"  {prompt}: ").strip()


def _yes_no(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt with default."""
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ── CUDA Detection ───────────────────────────────────────────

def _detect_cuda_tag() -> str | None:
    """Detect the best CUDA wheel tag for this machine.

    Priority:
    1. Existing torch CUDA install (e.g. "cu128") — preserve what works.
    2. nvidia-smi CUDA version — pick the right wheels for fresh installs.
    3. None — no NVIDIA GPU, use CPU builds from default PyPI.
    """
    # Check existing torch first
    try:
        result = subprocess.run(
            f'"{PYTHON}" -c "import torch; print(torch.__version__)"',
            shell=True, capture_output=True, text=True, cwd=str(ROOT),
        )
        version = result.stdout.strip()
        if "+cu" in version:
            tag = version.split("+")[1]
            print(f"  Detected existing torch CUDA build: {tag}")
            return tag
    except Exception:
        pass

    # No CUDA torch installed — probe the GPU via nvidia-smi
    try:
        result = subprocess.run(
            "nvidia-smi --query-gpu=driver_version --format=csv,noheader",
            shell=True, capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            driver = result.stdout.strip().split("\n")[0]
            major = int(driver.split(".")[0])
            # Driver >=570 supports CUDA 12.8, >=560 supports 12.6
            if major >= 570:
                tag = "cu128"
            elif major >= 560:
                tag = "cu126"
            else:
                print(f"  NVIDIA driver {driver} is too old for current CUDA wheels.")
                print("  Update your driver or torch will fall back to CPU.")
                return None
            print(f"  Detected NVIDIA GPU (driver {driver}) -> using {tag} wheels")
            return tag
    except Exception:
        pass

    return None


def _pip_extra_index() -> str:
    """Return '--extra-index-url ...' for CUDA wheels, or '' for CPU."""
    tag = _detect_cuda_tag()
    if tag:
        return f" --extra-index-url https://download.pytorch.org/whl/{tag}"
    return ""


# ── First-Time Setup (Onboarding) ────────────────────────────

def _check_python() -> bool:
    """Verify Python version is compatible."""
    header("Step 1: Python Version Check")
    v = sys.version_info
    print(f"  Python {v.major}.{v.minor}.{v.micro} ({platform.python_implementation()})")
    print(f"  Platform: {platform.system()} {platform.machine()}")

    if (v.major, v.minor) < MIN_PY:
        print(f"\n  [ERROR] Python {MIN_PY[0]}.{MIN_PY[1]}+ required.")
        print(f"  Download from https://www.python.org/downloads/")
        return False
    if (v.major, v.minor) > MAX_PY:
        print(f"\n  [WARNING] Python {v.major}.{v.minor} may have compatibility issues")
        print(f"  with ML dependencies. Python {MIN_PY[0]}.{MIN_PY[1]}-{MAX_PY[0]}.{MAX_PY[1]} recommended.")
        if not _yes_no("Continue anyway?"):
            return False
    print("  OK")
    return True


def _setup_venv() -> str:
    """Create venv if it doesn't exist. Returns python path."""
    header("Step 2: Virtual Environment")
    venv_dir = ROOT / "venv"

    if venv_dir.exists():
        print(f"  Virtual environment already exists at: venv/")
        if sys.platform == "win32":
            py = venv_dir / "Scripts" / "python.exe"
        else:
            py = venv_dir / "bin" / "python"
        if py.exists():
            print("  OK")
            return str(py)
        print("  [WARNING] venv exists but python not found -- recreating...")
        shutil.rmtree(str(venv_dir), ignore_errors=True)

    print("  Creating virtual environment...")
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  [ERROR] venv creation failed: {result.stderr.strip()}")
        print("  Try: python -m pip install --upgrade pip setuptools")
        return ""

    if sys.platform == "win32":
        py = venv_dir / "Scripts" / "python.exe"
    else:
        py = venv_dir / "bin" / "python"

    print(f"  Created: venv/")
    print("  OK")
    return str(py)


def _setup_configs() -> None:
    """Copy config templates that don't exist yet."""
    header("Step 4: Configuration Files")
    config_dir = ROOT / "config"
    any_copied = False

    for example, target, desc in CONFIG_TEMPLATES:
        target_path = config_dir / target
        example_path = config_dir / example
        if target_path.exists():
            print(f"  {target:<35} exists")
            continue
        if not example_path.exists():
            print(f"  {target:<35} [SKIP] no template found")
            continue
        shutil.copy2(str(example_path), str(target_path))
        print(f"  {target:<35} created from template")
        any_copied = True

    if any_copied:
        print("\n  You can customize these later in config/.")
    print("  OK")


def _setup_env() -> None:
    """Create .env and prompt for essential keys."""
    header("Step 5: Environment Variables (.env)")

    if ENV_PATH.exists():
        print("  .env file already exists.")
        if not _yes_no("Update API keys?", default=False):
            return
    else:
        example = ROOT / ".env.example"
        if example.exists():
            shutil.copy2(str(example), str(ENV_PATH))
            print("  Created .env from .env.example")
        else:
            ENV_PATH.write_text("# Archi environment\n", encoding="utf-8")
            print("  Created empty .env")

    # OpenRouter key (required)
    print("\n  --- OpenRouter API Key (required) ---")
    print("  Get one at: https://openrouter.ai/keys")
    print("  This powers all AI inference. Free tier available.\n")
    key = _input("OpenRouter API key (or press Enter to skip)")
    if key and key != "sk-or-replace-with-your-key":
        set_env("OPENROUTER_API_KEY", key)

    # xAI direct key (optional, recommended)
    print("\n  --- xAI Direct API Key (optional, recommended) ---")
    print("  Get one at: https://console.x.ai")
    print("  Enables direct routing to Grok (faster, avoids OpenRouter overhead).\n")
    key = _input("xAI API key (or press Enter to skip)")
    if key:
        set_env("XAI_API_KEY", key)

    # Discord bot token
    print("\n  --- Discord Bot Token (required for Discord interface) ---")
    print("  Create a bot at: https://discord.com/developers/applications")
    print("  Under Bot > Privileged Gateway Intents, enable: Message Content Intent")
    print("  Under OAuth2 > URL Generator: select 'bot' scope, then permissions:")
    print("    Send Messages, Embed Links, Attach Files, Read Message History")
    print("  Copy the bot token below.\n")
    token = _input("Discord bot token (or press Enter to skip)")
    if token and token != "your_bot_token":
        set_env("DISCORD_BOT_TOKEN", token)

    owner = _input("Your Discord user ID (or press Enter to skip)")
    if owner and owner != "your_discord_user_id":
        set_env("DISCORD_OWNER_ID", owner)

    print("\n  .env configured. You can edit it later at any time.")
    print("  OK")


def _setup_dirs() -> None:
    """Create runtime directories."""
    header("Step 6: Runtime Directories")
    for d in RUNTIME_DIRS:
        p = ROOT / d
        p.mkdir(parents=True, exist_ok=True)
        print(f"  {d + '/':<25} OK")
    print("  OK")


def _optional_features() -> None:
    """Offer optional feature installation."""
    header("Step 7: Optional Features")

    print("  [1] Voice (text-to-speech via Piper + whisper STT)")
    print("  [2] Image generation (local SDXL, requires NVIDIA GPU)")
    print("  [3] Both")
    print("  [S] Skip optional features\n")
    choice = _input("Select", "S").upper()

    if choice in ("1", "3"):
        install_voice()
    if choice in ("2", "3"):
        install_imagegen()
    if choice == "S":
        print("  Skipped. Run 'python scripts/install.py' later to add features.")
    print("  OK")


def _connectivity_test() -> bool:
    """Quick connectivity test -- try importing key modules."""
    header("Step 8: Connectivity Check")

    checks = [
        ("yaml", "PyYAML"),
        ("dotenv", "python-dotenv"),
        ("aiohttp", "aiohttp"),
    ]
    all_ok = True
    for module, name in checks:
        try:
            result = subprocess.run(
                [PYTHON, "-c", f"import {module}"],
                capture_output=True, text=True,
            )
            status = "OK" if result.returncode == 0 else "MISSING"
            if status == "MISSING":
                all_ok = False
        except Exception:
            status = "ERROR"
            all_ok = False
        print(f"  {name:<25} {status}")

    # Check .env has an API key
    load_env()
    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    xai_key = os.environ.get("XAI_API_KEY", "")
    has_key = (or_key and or_key != "sk-or-replace-with-your-key") or bool(xai_key)
    print(f"  {'API key configured':<25} {'OK' if has_key else 'NOT SET'}")
    if not has_key:
        print("  [WARNING] No API key found. Archi needs at least one to function.")
        print("  Edit .env and add OPENROUTER_API_KEY or XAI_API_KEY.")

    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    has_discord = discord_token and discord_token != "your_bot_token"
    print(f"  {'Discord token configured':<25} {'OK' if has_discord else 'NOT SET'}")
    if not has_discord:
        print("  [NOTE] Discord is the only interface. Set DISCORD_BOT_TOKEN to use Archi.")

    return all_ok


def _print_setup_summary() -> None:
    """Print next steps after setup."""
    header("Setup Complete!")
    print("  Next steps:\n")
    print("  1. Review and customize config files in config/")
    print("     - archi_identity.yaml  (name, role, focus areas)")
    print("     - personality.yaml     (voice, humor, values)")
    print("     - prime_directive.txt  (operational guidelines)")
    print("     - rules.yaml           (budgets, safety rules)\n")
    print("  2. Make sure .env has your API keys set\n")
    print("  3. Start Archi:")
    if sys.platform == "win32":
        print("     .\\venv\\Scripts\\python.exe scripts\\start.py\n")
    else:
        print("     python scripts/start.py\n")
    print("  4. On first start, Archi will offer to build your profile")
    print("     (so it knows who you are from day one).\n")
    print("  For troubleshooting: python scripts/fix.py diagnose")
    print("  For more options:    python scripts/install.py")
    print()


def first_time_setup() -> None:
    """Guided first-time setup (merged from onboard.py)."""
    header("Welcome to Archi!")
    print("  This script will walk you through the initial setup.")
    print("  It takes about 5 minutes. You can re-run it anytime.\n")

    if not _yes_no("Ready to begin?"):
        print("  No problem. Run this script again when you're ready.")
        return

    # Step 1: Python
    if not _check_python():
        return

    # Step 2: Venv
    python = _setup_venv()
    if not python:
        return

    # Step 3: Core dependencies (reuses install_deps with CUDA protection)
    install_deps()

    # Step 4: Config files
    _setup_configs()

    # Step 5: .env
    _setup_env()

    # Step 6: Runtime dirs
    _setup_dirs()

    # Step 7: Optional features (delegates to install_voice/install_imagegen)
    _optional_features()

    # Step 8: Connectivity
    _connectivity_test()

    # Summary
    _print_setup_summary()


def _is_first_run() -> bool:
    """Detect if this looks like a fresh install (no .env file)."""
    return not ENV_PATH.exists()


# ── Verification (--check) ───────────────────────────────────

def check_only() -> None:
    """Verify existing setup without making changes."""
    header("Archi Setup Verification")
    issues = []

    # Python
    v = sys.version_info
    py_ok = MIN_PY <= (v.major, v.minor) <= MAX_PY
    print(f"  Python {v.major}.{v.minor}.{v.micro:<12} {'OK' if py_ok else 'WARNING'}")
    if not py_ok:
        issues.append(f"Python {v.major}.{v.minor} outside recommended range")

    # Venv
    venv_exists = (ROOT / "venv").exists()
    print(f"  {'Virtual environment':<25} {'OK' if venv_exists else 'MISSING'}")
    if not venv_exists:
        issues.append("No virtual environment (run: python -m venv venv)")

    # Config files
    config_dir = ROOT / "config"
    for _, target, desc in CONFIG_TEMPLATES:
        exists = (config_dir / target).exists()
        print(f"  {target:<35} {'OK' if exists else 'MISSING'}")
        if not exists:
            issues.append(f"Missing config: {target}")

    # .env
    env_exists = ENV_PATH.exists()
    print(f"  {'.env':<35} {'OK' if env_exists else 'MISSING'}")
    if not env_exists:
        issues.append("No .env file")

    # Runtime dirs
    for d in RUNTIME_DIRS:
        exists = (ROOT / d).exists()
        print(f"  {d + '/':<25} {'OK' if exists else 'MISSING'}")
        if not exists:
            issues.append(f"Missing directory: {d}/")

    print()
    if issues:
        print(f"  {len(issues)} issue(s) found:")
        for issue in issues:
            print(f"    - {issue}")
        print("\n  Run 'python scripts/install.py setup' to fix.")
    else:
        print("  All checks passed!")


# ── Core Dependencies ────────────────────────────────────────

def install_deps() -> None:
    header("Installing Core Dependencies")
    req_file = ROOT / "requirements.txt"
    if not req_file.exists():
        print("  [ERROR] requirements.txt not found!")
        return

    # sentence-transformers depends on torch. Without the CUDA index,
    # pip pulls the CPU build from PyPI and silently clobbers any
    # existing CUDA install. Always pass the right index.
    extra = _pip_extra_index()
    if extra:
        print(f"  Using CUDA wheel index to prevent CPU-only torch install.\n")
    run(f'"{PYTHON}" -m pip install -r "{req_file}"{extra}')

    print("\n  Core dependencies installed.")


# ── Voice Dependencies ───────────────────────────────────────

def install_voice() -> None:
    header("Installing Voice Dependencies")
    print("  STT: faster-whisper (CTranslate2-based Whisper)")
    print("  TTS: piper-tts (lightweight ONNX)")
    print("  Audio: sounddevice + numpy (bundles PortAudio, no C compiler needed)\n")

    # Preserve CUDA torch (faster-whisper can pull torch transitively)
    extra = _pip_extra_index()
    run(f'"{PYTHON}" -m pip install faster-whisper piper-tts sounddevice numpy{extra}')

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
        hf_repo = "rhasspy/piper-voices"
        hf_revision = "v1.0.0"
        hf_onnx = f"en/en_US/lessac/medium/{voice_name}.onnx"
        hf_json = f"en/en_US/lessac/medium/{voice_name}.onnx.json"
        try:
            from huggingface_hub import hf_hub_download

            print(f"  Downloading {voice_name}.onnx...")
            hf_hub_download(
                repo_id=hf_repo, filename=hf_onnx, revision=hf_revision,
                local_dir=str(piper_dir), local_dir_use_symlinks=False,
            )
            nested_onnx = piper_dir / hf_onnx
            if nested_onnx.exists() and not onnx_file.exists():
                shutil.move(str(nested_onnx), str(onnx_file))

            print(f"  Downloading {voice_name}.onnx.json...")
            hf_hub_download(
                repo_id=hf_repo, filename=hf_json, revision=hf_revision,
                local_dir=str(piper_dir), local_dir_use_symlinks=False,
            )
            nested_json = piper_dir / hf_json
            if nested_json.exists() and not json_file.exists():
                shutil.move(str(nested_json), str(json_file))

            nested_dir = piper_dir / "en"
            if nested_dir.is_dir():
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


# ── Image Generation Dependencies ────────────────────────────

def install_imagegen() -> None:
    header("Installing Image Generation Dependencies")

    cuda_tag = _detect_cuda_tag()

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
        if cuda_tag:
            print(f"  NVIDIA GPU detected but torch lacks CUDA. Installing torch+{cuda_tag}...")
            run(f'"{PYTHON}" -m pip install torch torchvision --force-reinstall --index-url https://download.pytorch.org/whl/{cuda_tag}')
        else:
            print("  No NVIDIA GPU detected -- image gen will be CPU-only (very slow).")
            print("  If you do have an NVIDIA GPU, ensure drivers are installed and")
            print("  nvidia-smi is on your PATH, then re-run this installer.")
    else:
        print("  PyTorch CUDA: OK")

    print()
    print("  Pipeline:  diffusers (Stable Diffusion XL)")
    print("  Tokeniser: transformers (CLIPTextModel)")
    print("  Accel:     accelerate (GPU inference)")
    print("  Loader:    safetensors (safe model loading)\n")

    extra = _pip_extra_index()
    run(f'"{PYTHON}" -m pip install diffusers transformers accelerate safetensors{extra}')

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


# ── Windows Auto-Start ───────────────────────────────────────

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

        print("\n  Setting up Layer 1: Task Scheduler (headless at boot)...")
        task_name = "ArchiAutoStart"
        temp_ps1 = ROOT / "scripts" / "_setup_autostart.ps1"
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
            print("  Manual fix: Win+R -> shell:startup -> create shortcut to:")
            print(f"  {monitor_bat}")

        print()
        if ts_result == 0 and sf_result == 0:
            print("  Both layers enabled!")
            print("  - Boot (no login needed): Archi starts headless via Task Scheduler")
            print("  - Login: visible terminal opens to monitor Archi")
        elif sf_result == 0:
            print("  Startup folder enabled (visible terminal on login).")
            print("  Task Scheduler failed -- Archi will only start when you log in.")
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


# ── Main Menu ────────────────────────────────────────────────

def main_menu() -> None:
    # Auto-detect first run and suggest setup
    if _is_first_run():
        header("Archi Installer")
        print("  It looks like this is a fresh install (no .env found).\n")
        if _yes_no("Run first-time setup?"):
            first_time_setup()
            return
        print()

    header("Archi Installer")
    print("  [S] First-time setup (guided walkthrough)")
    print("  [1] Install core dependencies (requirements.txt)")
    print("  [2] Install voice dependencies (STT + TTS)")
    print("  [3] Install image generation dependencies (SDXL)")
    print("  [4] Windows auto-start setup")
    print("  [A] All of the above (deps + voice + imagegen + autostart)")
    print("  [Q] Quit\n")

    choice = input("Select: ").strip().upper()

    if choice == "S":
        first_time_setup()
    elif choice == "1":
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

    # Support direct subcommand
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "--check":
            check_only()
            return
        dispatch = {
            "setup": first_time_setup,
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
            print("Available: setup, deps, voice, imagegen, autostart, all, --check")
            sys.exit(1)
    else:
        main_menu()


if __name__ == "__main__":
    main()
