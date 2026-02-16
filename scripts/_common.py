"""Shared utilities for Archi scripts."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Cross-platform venv python
if sys.platform == "win32":
    VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
    VENV_PIP = ROOT / "venv" / "Scripts" / "pip.exe"
else:
    VENV_PYTHON = ROOT / "venv" / "bin" / "python"
    VENV_PIP = ROOT / "venv" / "bin" / "pip"

PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
PIP = str(VENV_PIP) if VENV_PIP.exists() else f"{sys.executable} -m pip"

ENV_PATH = ROOT / ".env"


def header(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def run(cmd: str, check: bool = True) -> int:
    """Run a shell command and return exit code."""
    print(f"  > {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=str(ROOT))
    if check and result.returncode != 0:
        print(f"  [WARNING] Command exited with code {result.returncode}")
    return result.returncode


def load_env() -> None:
    """Load .env into os.environ."""
    try:
        from dotenv import load_dotenv
        if ENV_PATH.exists():
            load_dotenv(ENV_PATH, override=True)
    except ImportError:
        pass


def set_env(name: str, value: str) -> None:
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
