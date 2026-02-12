"""
Test that the local model (Qwen3VL-8B via Forge) loads and generates text.
Run from repo root. Uses LOCAL_MODEL_PATH from .env or default models/ path.
"""

import os
import sys
import time
from pathlib import Path

# Ensure project root is on path (for backends/, utils/)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

# Load .env from repo root
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass


def _ensure_cuda_on_path() -> None:
    """Prepend CUDA bin and bin/x64 to PATH so llama-cpp-python GPU DLLs load (Windows)."""
    if os.name != "nt":
        return
    cuda_path = os.environ.get("CUDA_PATH", "").strip()
    if not cuda_path or not os.path.isdir(cuda_path):
        # Fallback: default CUDA install
        for v in ["v13.1", "v12.8", "v12.2"]:
            _default = rf"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\{v}"
            if os.path.isdir(_default):
                cuda_path = _default
                break
        else:
            return
    bin_path = os.path.join(cuda_path, "bin")
    bin_x64 = os.path.join(cuda_path, "bin", "x64")
    prepend = os.pathsep.join(p for p in (bin_x64, bin_path) if os.path.isdir(p))
    if prepend:
        os.environ["PATH"] = prepend + os.pathsep + os.environ.get("PATH", "")
    if sys.version_info >= (3, 8):
        for d in (bin_x64, bin_path):
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass


def main() -> None:
    _ensure_cuda_on_path()

    # Determine model path
    model_path = os.environ.get("LOCAL_MODEL_PATH")
    if not model_path or not os.path.isfile(model_path):
        default = _root / "models" / "Qwen3VL-8B-Instruct-Q4_K_M.gguf"
        if default.is_file():
            model_path = str(default)
        else:
            print("LOCAL_MODEL_PATH not set or file missing.")
            print("Place Qwen3VL-8B-Instruct-Q4_K_M.gguf + mmproj in models/ or set .env")
            sys.exit(1)

    print(f"Model path: {model_path}")

    print("Loading model via Forge (this may take a minute)...")
    try:
        from src.models.local_model import LocalModel
        model = LocalModel(model_path=model_path)
    except Exception as e:
        print(f"Error: {e}")
        print("For Qwen3-VL vision, install JamePeng fork: pip install llama-cpp-python @ git+https://github.com/jamepeng/llama-cpp-python")
        sys.exit(1)

    print(f"Model loaded. Vision: {model.has_vision}")

    # Test 1: Simple text
    print("\n=== Test 1: Simple text ===")
    r = model.generate("What is 2+2? Answer with just the number.", max_tokens=10, temperature=0.1)
    print("Response:", r.get("text", "").strip())
    print("Success:", r.get("success"))

    # Test 2: Short task + speed
    print("\n=== Test 2: Short task ===")
    start = time.time()
    r = model.generate("Write a haiku about AI.", max_tokens=50, temperature=0.7)
    elapsed = time.time() - start
    text = r.get("text", "").strip()
    print("Response:", text)
    print(f"Time: {elapsed:.2f}s  (~{len(text.split()) / elapsed:.1f} tokens/sec)")

    print("\nDone.")


if __name__ == "__main__":
    main()
