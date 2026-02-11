"""
Download Qwen2.5-14B-Instruct Q4_K_M GGUF (single file) from Hugging Face.
Uses bartowski's single-file version so llama-cpp-python 0.2.x loads it without multi-file issues.
Saves to ARCHI_ROOT/models/ or repo models/. Updates .env with LOCAL_MODEL_PATH.
Requires: pip install huggingface_hub
"""

import os
import re
import sys
from pathlib import Path

# Single-file Q4_K_M from bartowski (~8.99 GB) — works with older llama-cpp-python
REPO_ID = "bartowski/Qwen2.5-14B-Instruct-GGUF"
FILENAME = "Qwen2.5-14B-Instruct-Q4_K_M.gguf"


def main() -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Install huggingface_hub first: .\\venv\\Scripts\\python.exe -m pip install huggingface_hub")
        sys.exit(1)

    # Model dir: ARCHI_ROOT or repo root or C:/Archi
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
    out_path = os.path.join(models_dir, FILENAME)
    print(f"Downloading to {out_path} (~8.99 GB)...")

    hf_hub_download(
        repo_id=REPO_ID,
        filename=FILENAME,
        local_dir=models_dir,
    )
    if not os.path.isfile(out_path):
        print("Download finished but file not at", out_path)
        sys.exit(1)
    path_norm = Path(out_path).resolve().as_posix()

    # Update .env
    env_path = os.path.join(root, ".env")
    env_content = ""
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            env_content = f.read()
    if "LOCAL_MODEL_PATH=" in env_content:
        env_content = re.sub(
            r"LOCAL_MODEL_PATH=.*",
            f"LOCAL_MODEL_PATH={path_norm}",
            env_content,
        )
    else:
        env_content = env_content.rstrip() + "\nLOCAL_MODEL_PATH=" + path_norm + "\n"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env_content)
    print(f"Wrote LOCAL_MODEL_PATH={path_norm} to {env_path}")
    print("Done. Run: .\\venv\\Scripts\\python.exe test_local_model.py")


if __name__ == "__main__":
    main()
