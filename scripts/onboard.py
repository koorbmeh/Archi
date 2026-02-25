#!/usr/bin/env python3
r"""
Archi Onboarding — guided first-run experience for new users.

Walks through: Python check → venv creation → dependency install →
config file setup → API key entry → Discord bot guidance → optional
features (voice, image gen) → connectivity test → first start.

Usage:
    python scripts/onboard.py          (interactive walkthrough)
    python scripts/onboard.py --check  (verify setup without changes)
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, ENV_PATH, header, run, set_env

# Minimum supported Python
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
        # Determine the python executable
        if sys.platform == "win32":
            py = venv_dir / "Scripts" / "python.exe"
        else:
            py = venv_dir / "bin" / "python"
        if py.exists():
            print("  OK")
            return str(py)
        print("  [WARNING] venv exists but python not found — recreating...")
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


def _install_deps(python: str) -> bool:
    """Install core dependencies from requirements.txt."""
    header("Step 3: Install Dependencies")
    req_file = ROOT / "requirements.txt"
    if not req_file.exists():
        print("  [ERROR] requirements.txt not found!")
        return False

    print("  Installing core dependencies (this may take a few minutes)...\n")
    result = subprocess.run(
        [python, "-m", "pip", "install", "-r", str(req_file)],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print("\n  [ERROR] Dependency installation failed.")
        print("  Try running manually: pip install -r requirements.txt")
        return False

    print("\n  Dependencies installed.")
    print("  OK")
    return True


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


def _optional_features(python: str) -> None:
    """Offer optional feature installation."""
    header("Step 7: Optional Features")

    # Voice
    print("  [1] Voice (text-to-speech via Piper + whisper STT)")
    print("  [2] Image generation (local SDXL, requires NVIDIA GPU)")
    print("  [3] Both")
    print("  [S] Skip optional features\n")
    choice = _input("Select", "S").upper()

    if choice in ("1", "3"):
        print("\n  Installing voice dependencies...")
        run(f'"{python}" -m pip install faster-whisper piper-tts sounddevice numpy')

    if choice in ("2", "3"):
        print("\n  Installing image generation dependencies...")
        run(f'"{python}" -m pip install diffusers transformers accelerate safetensors')
        print("\n  Note: For GPU acceleration, you may need PyTorch with CUDA.")
        print("  Run: python scripts/install.py imagegen  (for guided CUDA setup)")

    if choice == "S":
        print("  Skipped. Run 'python scripts/install.py' later to add features.")
    print("  OK")


def _connectivity_test(python: str) -> bool:
    """Quick connectivity test — try importing key modules."""
    header("Step 8: Connectivity Check")

    # Check key imports
    checks = [
        ("yaml", "PyYAML"),
        ("dotenv", "python-dotenv"),
        ("aiohttp", "aiohttp"),
    ]
    all_ok = True
    for module, name in checks:
        try:
            result = subprocess.run(
                [python, "-c", f"import {module}"],
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
    from _common import load_env
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


def _print_summary() -> None:
    """Print next steps."""
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
    print("  4. Build your profile (so Archi knows who you are):")
    print("     python scripts/profile_setup.py\n")
    print("  5. DM the bot on Discord to start chatting!\n")
    print("  For troubleshooting: python scripts/fix.py diagnose")
    print("  For more options:    python scripts/install.py")
    print()


def _check_only() -> None:
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
        print("\n  Run 'python scripts/onboard.py' to fix.")
    else:
        print("  All checks passed!")


def main() -> None:
    os.chdir(str(ROOT))

    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        _check_only()
        return

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

    # Step 3: Dependencies
    if not _install_deps(python):
        print("\n  You can retry with: python scripts/install.py deps")
        if not _yes_no("Continue setup anyway?"):
            return

    # Step 4: Config files
    _setup_configs()

    # Step 5: .env
    _setup_env()

    # Step 6: Runtime dirs
    _setup_dirs()

    # Step 7: Optional features
    _optional_features(python)

    # Step 8: Connectivity
    _connectivity_test(python)

    # Summary
    _print_summary()


if __name__ == "__main__":
    main()
