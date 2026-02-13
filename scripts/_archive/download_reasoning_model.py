"""
Download DeepSeek-R1-Distill-Llama-8B GGUF (reasoning model) for dual-model architecture.

This model handles goal decomposition, planning, and structured JSON output.
The vision model (Qwen3-VL-8B) continues to handle image/screen analysis.

Source: bartowski/DeepSeek-R1-Distill-Llama-8B-GGUF (public HuggingFace repo)
Downloads Q4_K_M quant (~4.9 GB).
Saves to ARCHI_ROOT/models/ and writes REASONING_MODEL_PATH to .env.

Requires: pip install huggingface_hub

Note on VRAM: Loading both models simultaneously requires ~10-11 GB VRAM.
  - Vision (Qwen3-VL-8B Q4_K_M): ~5 GB
  - Reasoning (DeepSeek-R1-Distill-8B Q4_K_M): ~5 GB
If you only have 8 GB VRAM, set ARCHI_DUAL_MODEL=off in .env and Archi
will use the vision model for everything (still works, just no specialization).
"""

import os
import re
import sys
from pathlib import Path

REPO_ID = "bartowski/DeepSeek-R1-Distill-Llama-8B-GGUF"
FILENAME = "DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf"


def main() -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Install huggingface_hub first:")
        print("  .\\venv\\Scripts\\python.exe -m pip install huggingface_hub")
        sys.exit(1)

    root = os.environ.get("ARCHI_ROOT")
    if not root:
        root = Path(__file__).resolve().parent.parent
        if (root / "config").is_dir():
            root = str(root)
        else:
            root = "C:/Archi"
    else:
        root = os.path.normpath(root)
    models_dir = os.path.join(root, "models")
    os.makedirs(models_dir, exist_ok=True)

    dest = os.path.join(models_dir, FILENAME)
    print(f"Downloading reasoning model to {dest} (~4.9 GB)...")
    print(f"Source: {REPO_ID}")
    hf_hub_download(
        repo_id=REPO_ID,
        filename=FILENAME,
        local_dir=models_dir,
    )
    if not os.path.isfile(dest):
        print("ERROR: Download finished but file not found at", dest)
        sys.exit(1)
    print(f"Done. Size: {os.path.getsize(dest) / (1024**3):.2f} GB")

    # Write REASONING_MODEL_PATH to .env
    model_norm = Path(dest).resolve().as_posix()
    env_path = os.path.join(root, ".env")
    env_content = ""
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            env_content = f.read()

    if "REASONING_MODEL_PATH=" in env_content:
        env_content = re.sub(
            r"REASONING_MODEL_PATH=.*",
            f"REASONING_MODEL_PATH={model_norm}",
            env_content,
        )
    else:
        env_content = env_content.rstrip() + f"\nREASONING_MODEL_PATH={model_norm}\n"

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env_content)
    print(f"Wrote REASONING_MODEL_PATH={model_norm} to {env_path}")
    print()
    print("Dual-model architecture is now available:")
    print("  Vision model: Qwen3-VL-8B (images, screen reading)")
    print("  Reasoning model: DeepSeek-R1-Distill-8B (goals, planning, text)")
    print()
    print("To disable dual-model mode: set ARCHI_DUAL_MODEL=off in .env")
    print("To adjust reasoning context window: set ARCHI_REASONING_CONTEXT_SIZE in .env (default: 8192)")


if __name__ == "__main__":
    main()
