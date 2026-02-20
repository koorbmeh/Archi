"""
Centralised path helpers for Archi.

Every module that previously defined its own ``_base_path()`` or ``_db_path()``
should import from here instead.  Single source of truth for:

* ``base_path()``  – project root (directory containing ``config/``).
* ``db_path()``    – SQLite database at ``data/memory.db``.
* ``data_dir()``   – ``data/`` directory (created lazily).
"""

import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

_cached_base: Optional[str] = None


def _is_windows_path_on_non_windows(path: str) -> bool:
    """Detect a Windows-style absolute path (e.g. 'C:/...') on a non-Windows OS.

    ARCHI_ROOT might be set to a Windows path in .env, but if Archi is
    running inside WSL, a Linux container, or another non-Windows context
    then that path is invalid and would cause os.makedirs to create a
    bogus ``C:`` directory under the current working directory.
    """
    if sys.platform == "win32":
        return False
    return len(path) >= 3 and path[0].isalpha() and path[1] == ":" and path[2] in ("/", "\\")


def base_path() -> str:
    """Return the project root (directory containing ``config/``).

    Resolution order:
    1. ``ARCHI_ROOT`` environment variable (normalised) — skipped if it
       contains a Windows drive path on a non-Windows OS.
    2. Walk up from *this* file (up to 6 levels) looking for ``config/``.
    3. Current working directory as last resort.

    The result is cached after the first call.
    """
    global _cached_base
    if _cached_base is not None:
        return _cached_base

    env = os.environ.get("ARCHI_ROOT")
    if env and not _is_windows_path_on_non_windows(env):
        _cached_base = os.path.normpath(env)
        return _cached_base

    cur = Path(__file__).resolve().parent
    for _ in range(6):
        if (cur / "config").is_dir():
            _cached_base = str(cur)
            return _cached_base
        cur = cur.parent

    _cached_base = os.getcwd()
    return _cached_base


def base_path_as_path() -> Path:
    """Same as :func:`base_path` but returns a :class:`pathlib.Path`."""
    return Path(base_path())


# Alias used by newer modules (initiative_tracker, time_awareness).
project_root = base_path_as_path


# ---------------------------------------------------------------------------
# Common data paths
# ---------------------------------------------------------------------------

def db_path() -> str:
    """Return path to the shared SQLite database (``data/memory.db``)."""
    return os.path.join(base_path(), "data", "memory.db")


def data_dir(subdir: str = "") -> str:
    """Return (and ensure existence of) ``data/<subdir>``."""
    d = os.path.join(base_path(), "data", subdir) if subdir else os.path.join(base_path(), "data")
    os.makedirs(d, exist_ok=True)
    return d
