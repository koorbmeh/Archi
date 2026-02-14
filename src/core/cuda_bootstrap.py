"""
Prepend CUDA runtime to PATH so llama_cpp can load llama.dll when not run from a CUDA batch.
Must be imported before any code that loads the local model (e.g. before importing ModelRouter).
"""

import os
from typing import List, Optional

# Prefer newer versions; we discover what's actually installed
_PREFERRED_VERSIONS = ["v13.1", "v12.4", "v12.2", "v12.1"]

# Common CUDA install locations (used as last-resort fallbacks)
_COMMON_CUDA_BASES = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "NVIDIA GPU Computing Toolkit", "CUDA"),
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "NVIDIA", "CUDA"),
]


def _cuda_root_has_bin(root: str) -> bool:
    """True if this toolkit root has bin/ and bin/x64/. llama-cpp-python uses CUDA_PATH + 'bin/x64' for add_dll_directory."""
    bin_dir = os.path.join(root, "bin")
    x64_dir = os.path.join(bin_dir, "x64")
    return os.path.isdir(bin_dir) and os.path.isdir(x64_dir)


def _cuda_bases_from_registry() -> List[str]:
    """Try to read CUDA install directories from the Windows registry."""
    bases: List[str] = []
    if os.name != "nt":
        return bases
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\NVIDIA Corporation\GPU Computing Toolkit\CUDA")
        i = 0
        while True:
            try:
                ver = winreg.EnumKey(key, i)
                sub = winreg.OpenKey(key, ver)
                path, _ = winreg.QueryValueEx(sub, "InstallDir")
                if path and os.path.isdir(path):
                    parent = os.path.dirname(path.rstrip(os.sep))
                    if parent not in bases:
                        bases.append(parent)
                winreg.CloseKey(sub)
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except OSError:
        pass
    return bases


def _discover_cuda_root() -> Optional[str]:
    """Use CUDA_PATH if valid; else search registry and common locations for an installed version."""
    env_path = os.environ.get("CUDA_PATH")
    if env_path and os.path.isdir(env_path) and _cuda_root_has_bin(env_path):
        return env_path

    # Build search list: registry paths first, then common fallback locations
    search_bases = _cuda_bases_from_registry() + [
        b for b in _COMMON_CUDA_BASES if b not in _cuda_bases_from_registry()
    ]

    for base in search_bases:
        if not os.path.isdir(base):
            continue
        for ver in _PREFERRED_VERSIONS:
            candidate = os.path.join(base, ver)
            if os.path.isdir(candidate) and _cuda_root_has_bin(candidate):
                return candidate
    return None


# Resolve toolkit root then choose bin or bin/x64 for PATH
_CUDA_BIN = None
_cuda_root = _discover_cuda_root()
if _cuda_root:
    _bin = os.path.join(_cuda_root, "bin")
    _bin_x64 = os.path.join(_cuda_root, "bin", "x64")
    if os.path.isdir(_bin):
        _CUDA_BIN = _bin_x64 if os.path.isdir(_bin_x64) else _bin

if _CUDA_BIN:
    _prev = os.environ.get("PATH", "")
    if _CUDA_BIN not in _prev.split(os.pathsep):
        os.environ["PATH"] = _CUDA_BIN + os.pathsep + _prev
    # Always set CUDA_PATH to the toolkit we're using. Loaders (e.g. llama-cpp) often use
    # CUDA_PATH + "\\bin\\x64", so this must match what we put on PATH or they look in the wrong place.
    if _cuda_root:
        os.environ["CUDA_PATH"] = _cuda_root
