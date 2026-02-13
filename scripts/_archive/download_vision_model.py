"""
Download Qwen3-VL-8B-Instruct GGUF (vision model) for Gate C local vision.
Source: Qwen/Qwen3-VL-8B-Instruct-GGUF (official, public).
Downloads two files: LLM (Q4_K_M, ~5.03 GB) + vision projector mmproj (F16, ~1.16 GB).
Saves to ARCHI_ROOT/models/ or repo models/. Writes LOCAL_VISION_MODEL_PATH and
LOCAL_VISION_MMPROJ_PATH to .env.

Requires: pip install huggingface_hub
"""

import os
import re
import sys
from pathlib import Path

# Official Qwen repo (public; bartowski repo returned 401)
REPO_ID = "Qwen/Qwen3-VL-8B-Instruct-GGUF"
LLM_FILENAME = "Qwen3VL-8B-Instruct-Q4_K_M.gguf"
MMPROJ_FILENAME = "mmproj-Qwen3VL-8B-Instruct-F16.gguf"


def main() -> None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Install huggingface_hub first: .\\venv\\Scripts\\python.exe -m pip install huggingface_hub")
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

    # 1. LLM
    llm_path = os.path.join(models_dir, LLM_FILENAME)
    print(f"Downloading LLM to {llm_path} (~5.03 GB)...")
    hf_hub_download(
        repo_id=REPO_ID,
        filename=LLM_FILENAME,
        local_dir=models_dir,
    )
    if not os.path.isfile(llm_path):
        print("Download finished but file not at", llm_path)
        sys.exit(1)
    print(f"LLM done. Size: {os.path.getsize(llm_path) / (1024**3):.2f} GB")

    # 2. Vision projector (mmproj)
    mmproj_path = os.path.join(models_dir, MMPROJ_FILENAME)
    print(f"Downloading mmproj to {mmproj_path} (~1.16 GB)...")
    hf_hub_download(
        repo_id=REPO_ID,
        filename=MMPROJ_FILENAME,
        local_dir=models_dir,
    )
    if not os.path.isfile(mmproj_path):
        print("Download finished but file not at", mmproj_path)
        sys.exit(1)
    print(f"mmproj done. Size: {os.path.getsize(mmproj_path) / (1024**3):.2f} GB")

    llm_norm = Path(llm_path).resolve().as_posix()
    mmproj_norm = Path(mmproj_path).resolve().as_posix()
    env_path = os.path.join(root, ".env")
    env_content = ""
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            env_content = f.read()

    def set_env(name: str, value: str) -> None:
        nonlocal env_content
        if f"{name}=" in env_content:
            env_content = re.sub(rf"{re.escape(name)}=.*", f"{name}={value}", env_content)
        else:
            env_content = env_content.rstrip() + "\n" + name + "=" + value + "\n"

    set_env("LOCAL_VISION_MODEL_PATH", llm_norm)
    set_env("LOCAL_VISION_MMPROJ_PATH", mmproj_norm)
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env_content)
    print(f"Wrote LOCAL_VISION_MODEL_PATH and LOCAL_VISION_MMPROJ_PATH to {env_path}")
    print("Next: implement local_vision_handler.py to load LLM + mmproj for screenshot/UI grounding.")


if __name__ == "__main__":
    main()
