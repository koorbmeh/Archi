"""Shared utilities for Archi scripts."""

import os
import re
import shutil
import subprocess
import sys
import time
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


BACKUP_ROOT = ROOT / "backup"


def backup_file(filepath: Path, quiet: bool = False) -> Path | None:
    """Create a timestamped backup of *filepath* before overwriting it.

    Backups are stored in a centralized ``backup/`` folder at the project
    root, mirroring the original directory structure with a timestamp
    suffix.  For example::

        config/archi_identity.yaml
          -> backup/config/archi_identity.20260225_153000.yaml

    Returns the backup path, or None if the source file didn't exist.
    """
    if not filepath.is_file():
        return None

    filepath = filepath.resolve()
    try:
        rel = filepath.relative_to(ROOT.resolve())
    except ValueError:
        # File lives outside the project — fall back to flat name
        rel = Path(filepath.name)

    ts = time.strftime("%Y%m%d_%H%M%S")
    stem = rel.stem
    suffix = rel.suffix  # e.g. ".yaml", ".json", ".env" ...
    bak_name = f"{stem}.{ts}{suffix}"
    bak = BACKUP_ROOT / rel.parent / bak_name

    try:
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(filepath), str(bak))
        if not quiet:
            print(f"  Backed up {rel} -> backup/{rel.parent / bak_name}")
        return bak
    except OSError as exc:
        if not quiet:
            print(f"  [WARNING] Could not back up {filepath.name}: {exc}")
        return None


def set_env(name: str, value: str) -> None:
    """Set a key=value in .env (create or update)."""
    env_content = ""
    if ENV_PATH.is_file():
        env_content = ENV_PATH.read_text(encoding="utf-8")
    old_content = env_content
    if f"{name}=" in env_content:
        env_content = re.sub(rf"{re.escape(name)}=.*", f"{name}={value}", env_content)
    else:
        env_content = env_content.rstrip() + f"\n{name}={value}\n"
    # Only back up if the file actually changed
    if env_content != old_content and old_content.strip():
        backup_file(ENV_PATH, quiet=True)
    ENV_PATH.write_text(env_content, encoding="utf-8")
    print(f"  Set {name}={value} in .env")
