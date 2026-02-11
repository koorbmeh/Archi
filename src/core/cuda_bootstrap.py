"""
Prepend CUDA runtime to PATH so llama_cpp can load llama.dll when not run from a CUDA batch.
Must be imported before any code that loads the local model (e.g. before importing ModelRouter).
"""

import os

_CUDA_BASE = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
# Prefer newer versions; we discover what's actually installed
_PREFERRED_VERSIONS = ["v13.1", "v12.4", "v12.2", "v12.1"]


def _cuda_root_has_bin(root: str) -> bool:
    """True if this toolkit root has bin/ and bin/x64/. llama-cpp-python uses CUDA_PATH + 'bin/x64' for add_dll_directory."""
    bin_dir = os.path.join(root, "bin")
    x64_dir = os.path.join(bin_dir, "x64")
    return os.path.isdir(bin_dir) and os.path.isdir(x64_dir)


def _discover_cuda_root() -> str | None:
    """Use CUDA_PATH only if it has a valid bin; else first installed version under CUDA base."""
    env_path = os.environ.get("CUDA_PATH")
    if env_path and os.path.isdir(env_path) and _cuda_root_has_bin(env_path):
        return env_path
    if not os.path.isdir(_CUDA_BASE):
        return None
    for ver in _PREFERRED_VERSIONS:
        candidate = os.path.join(_CUDA_BASE, ver)
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
