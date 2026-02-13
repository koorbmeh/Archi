#!/usr/bin/env python3
r"""
Archi Fix — diagnostics, tests, cache clearing, and state repair.

Consolidates: check_grok.py, diagnose_cuda.py, clear_cache.py,
              cleanup_trash.py, create_health_goal.py, verify_gate_a.ps1

Usage:
    .\venv\Scripts\python.exe scripts\fix.py              (interactive menu)
    .\venv\Scripts\python.exe scripts\fix.py diagnose      (run all diagnostics)
    .\venv\Scripts\python.exe scripts\fix.py test          (run pytest suite)
    .\venv\Scripts\python.exe scripts\fix.py clean         (clear caches & trash)
    .\venv\Scripts\python.exe scripts\fix.py state         (repair state / create goals)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def _header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def _run(cmd: str, check: bool = False) -> int:
    print(f"  > {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=str(ROOT))
    return result.returncode


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
    except ImportError:
        pass


# ── 1. Diagnostics ────────────────────────────────────────────

def run_diagnostics() -> None:
    _header("Archi Diagnostics")
    issues = []       # (severity, message, fix_hint)
    auto_fixes = []   # (description, callable)

    # ── 1a. Python & venv ──
    print("── Python Environment ──")
    print(f"  Python: {sys.executable}")
    print(f"  Version: {sys.version}")
    venv_ok = VENV_PYTHON.exists()
    print(f"  Venv: {'Yes' if venv_ok else 'No'}")
    if not venv_ok:
        issues.append(("ERROR", "No virtual environment found",
                        "Run: python -m venv venv"))
    print()

    # ── 1b. .env check ──
    _load_env()
    print("── Environment Variables ──")
    env_file = ROOT / ".env"
    if not env_file.is_file():
        issues.append(("ERROR", ".env file missing",
                        "Copy .env.example to .env and fill in your values"))

    env_keys = [
        "LOCAL_MODEL_PATH", "LOCAL_VISION_MODEL_PATH", "LOCAL_VISION_MMPROJ_PATH",
        "REASONING_MODEL_PATH", "GROK_API_KEY", "DISCORD_BOT_TOKEN",
        "ARCHI_DUAL_MODEL", "ARCHI_VOICE_ENABLED", "IMAGE_MODEL_PATH",
    ]
    dual_mode = os.environ.get("ARCHI_DUAL_MODEL", "").lower() in ("on", "true", "1")
    models_dir = ROOT / "models"

    for key in env_keys:
        val = os.environ.get(key, "")
        if key in ("GROK_API_KEY", "DISCORD_BOT_TOKEN"):
            display = f"set ({len(val)} chars)" if val else "NOT SET"
            if not val:
                sev = "WARN" if key == "DISCORD_BOT_TOKEN" else "WARN"
                issues.append((sev, f"{key} not set",
                               f"Add {key}=your_key to .env"))
        elif val:
            # For file paths, verify the file exists
            if val.endswith((".gguf", ".onnx", ".safetensors")):
                fname = Path(val).name
                exists = Path(val).exists()
                display = f"{fname} ({'found' if exists else 'FILE MISSING'})"
                if not exists:
                    hint = "Run: scripts\\install.py imagegen" if key == "IMAGE_MODEL_PATH" else "Run: scripts\\install.py models"
                    issues.append(("ERROR", f"{key} points to missing file: {fname}", hint))
            else:
                display = val
        else:
            # Missing env var — check if we can auto-fix
            if key == "LOCAL_MODEL_PATH" and dual_mode:
                display = "not needed (dual-model is on)"
            elif key == "LOCAL_VISION_MODEL_PATH":
                candidate = models_dir / "Qwen3VL-8B-Instruct-Q4_K_M.gguf"
                if candidate.exists():
                    display = "NOT SET (model file found — can auto-fix)"
                    fix_val = candidate.resolve().as_posix()
                    auto_fixes.append((
                        f"Set {key} in .env",
                        lambda k=key, v=fix_val: _auto_set_env(k, v),
                    ))
                else:
                    display = "NOT SET"
                    issues.append(("ERROR", f"{key} not set and model not downloaded",
                                   "Run: scripts\\install.py models"))
            elif key == "LOCAL_VISION_MMPROJ_PATH":
                candidate = models_dir / "mmproj-Qwen3VL-8B-Instruct-F16.gguf"
                if candidate.exists():
                    display = "NOT SET (model file found — can auto-fix)"
                    fix_val = candidate.resolve().as_posix()
                    auto_fixes.append((
                        f"Set {key} in .env",
                        lambda k=key, v=fix_val: _auto_set_env(k, v),
                    ))
                else:
                    display = "NOT SET"
                    issues.append(("ERROR", f"{key} not set and model not downloaded",
                                   "Run: scripts\\install.py models"))
            elif key == "REASONING_MODEL_PATH":
                candidate = models_dir / "DeepSeek-R1-Distill-Llama-8B-Q4_K_M.gguf"
                if candidate.exists():
                    display = "NOT SET (model file found — can auto-fix)"
                    fix_val = candidate.resolve().as_posix()
                    auto_fixes.append((
                        f"Set {key} in .env",
                        lambda k=key, v=fix_val: _auto_set_env(k, v),
                    ))
                else:
                    display = "NOT SET"
                    issues.append(("ERROR", f"{key} not set and model not downloaded",
                                   "Run: scripts\\install.py models"))
            elif key == "ARCHI_DUAL_MODEL":
                # Check if both vision + reasoning are available
                has_vision = os.environ.get("LOCAL_VISION_MODEL_PATH", "")
                has_reasoning = os.environ.get("REASONING_MODEL_PATH", "")
                if has_vision and has_reasoning:
                    display = "NOT SET (vision + reasoning available — can auto-fix)"
                    auto_fixes.append((
                        "Enable dual-model mode",
                        lambda: _auto_set_env("ARCHI_DUAL_MODEL", "on"),
                    ))
                else:
                    display = "off (need both vision + reasoning models)"
            elif key == "IMAGE_MODEL_PATH":
                # Check for .safetensors files in models/ that aren't LLM-related
                safetensors = [
                    f for f in models_dir.glob("*.safetensors")
                    if "mmproj" not in f.name.lower()
                ] if models_dir.is_dir() else []
                if len(safetensors) == 1:
                    display = f"NOT SET (found {safetensors[0].name} — can auto-fix)"
                    fix_val = safetensors[0].resolve().as_posix()
                    auto_fixes.append((
                        f"Set IMAGE_MODEL_PATH in .env",
                        lambda v=fix_val: _auto_set_env("IMAGE_MODEL_PATH", v),
                    ))
                elif len(safetensors) > 1:
                    names = ", ".join(f.name for f in safetensors[:3])
                    display = f"NOT SET ({len(safetensors)} .safetensors found: {names})"
                    issues.append(("WARN",
                        "Multiple .safetensors in models/ — set IMAGE_MODEL_PATH to pick one",
                        "Add IMAGE_MODEL_PATH=path/to/model.safetensors to .env"))
                else:
                    display = "NOT SET (no image model downloaded — optional)"
            else:
                display = "NOT SET"
        print(f"  {key}: {display}")
    print()

    # ── 1c. Model files ──
    print("── Model Files ──")
    if models_dir.is_dir():
        gguf_files = list(models_dir.glob("*.gguf"))
        if gguf_files:
            for f in gguf_files:
                size_gb = f.stat().st_size / (1024**3)
                print(f"  {f.name}: {size_gb:.2f} GB")
        else:
            print("  No .gguf files found in models/")
            issues.append(("ERROR", "No model files downloaded",
                           "Run: scripts\\install.py models"))
        # Piper voice
        piper_dir = models_dir / "piper"
        if piper_dir.is_dir():
            piper_files = list(piper_dir.glob("*.onnx"))
            if piper_files:
                for f in piper_files:
                    print(f"  piper/{f.name}: {f.stat().st_size / (1024**2):.1f} MB")
            else:
                print("  No Piper voice models in models/piper/")
                if os.environ.get("ARCHI_VOICE_ENABLED", "").lower() in ("true", "1"):
                    issues.append(("WARN", "Voice enabled but no Piper voice model found",
                                   "Run: scripts\\install.py voice"))
        else:
            if os.environ.get("ARCHI_VOICE_ENABLED", "").lower() in ("true", "1"):
                print("  models/piper/ not found (voice enabled but not installed)")
                issues.append(("WARN", "Voice enabled but piper directory missing",
                               "Run: scripts\\install.py voice"))
            else:
                print("  models/piper/ not found (voice not enabled)")
        # Image generation models
        safetensors_files = list(models_dir.glob("*.safetensors"))
        if safetensors_files:
            for f in safetensors_files:
                size_gb = f.stat().st_size / (1024**3)
                print(f"  {f.name}: {size_gb:.2f} GB (image gen)")
        else:
            print("  No .safetensors files found in models/ (image gen not available)")
    else:
        print("  models/ directory not found")
        issues.append(("ERROR", "models/ directory missing",
                       "Run: scripts\\install.py models"))
    print()

    # ── 1d. Image generation dependencies ──
    print("── Image Generation ──")
    # PyTorch CUDA check — critical for image gen performance
    try:
        import torch as _torch
        torch_ver = _torch.__version__
        torch_cuda = _torch.cuda.is_available()
        cuda_tag = "CUDA" if torch_cuda else "CPU-only"
        print(f"  PyTorch: {torch_ver} ({cuda_tag})")
        if not torch_cuda:
            issues.append(("WARN",
                "PyTorch has NO CUDA support — image gen will run on CPU (very slow, 6+ min per image)",
                "Run: scripts\\install.py imagegen  → it will offer to install PyTorch with CUDA"))
    except ImportError:
        print("  PyTorch: NOT INSTALLED")

    diffusers_ok = False
    try:
        import diffusers
        print(f"  diffusers: {diffusers.__version__}")
        diffusers_ok = True
    except ImportError:
        print("  diffusers: NOT INSTALLED (image gen disabled)")
    try:
        import transformers
        print(f"  transformers: {transformers.__version__}")
    except ImportError:
        if diffusers_ok:
            print("  transformers: NOT INSTALLED")
            issues.append(("WARN", "transformers missing — required for image gen",
                           "Run: scripts\\install.py imagegen"))
    try:
        import accelerate
        print(f"  accelerate: {accelerate.__version__}")
    except ImportError:
        if diffusers_ok:
            print("  accelerate: NOT INSTALLED")
            issues.append(("WARN", "accelerate missing — required for image gen",
                           "Run: scripts\\install.py imagegen"))
    try:
        import safetensors as st
        print(f"  safetensors: {st.__version__}")
    except ImportError:
        if diffusers_ok:
            print("  safetensors: NOT INSTALLED")
            issues.append(("WARN", "safetensors missing — required for image gen",
                           "Run: scripts\\install.py imagegen"))
    # Check if ImageGenerator can find a model
    if diffusers_ok:
        try:
            from src.tools.image_gen import ImageGenerator
            if ImageGenerator.is_available():
                model_path = ImageGenerator._resolve_model_path()
                fname = Path(model_path).name if model_path and os.path.exists(model_path) else model_path
                print(f"  Image model: {fname}")
            else:
                print("  Image model: NONE FOUND")
                issues.append(("WARN",
                    "diffusers installed but no image model found in models/",
                    "Download an SDXL .safetensors and place in models/ or set IMAGE_MODEL_PATH in .env"))
        except Exception as e:
            print(f"  ImageGenerator check: {e}")
    print()

    # ── 1e. Video generation dependencies ──
    print("── Video Generation (WAN 2.1) ──")
    imageio_ok = False
    try:
        import imageio
        print(f"  imageio: {imageio.__version__}")
        imageio_ok = True
    except ImportError:
        print("  imageio: NOT INSTALLED")
    try:
        import imageio_ffmpeg
        print(f"  imageio-ffmpeg: {imageio_ffmpeg.__version__}")
    except ImportError:
        print("  imageio-ffmpeg: NOT INSTALLED")
    try:
        import sentencepiece
        print(f"  sentencepiece: {sentencepiece.__version__}")
    except ImportError:
        print("  sentencepiece: NOT INSTALLED")
    # Check if VideoGenerator can be imported
    if diffusers_ok and imageio_ok:
        try:
            from src.tools.video_gen import VideoGenerator
            if VideoGenerator.is_available():
                t2v_id = VideoGenerator._resolve_model_id("t2v")
                i2v_id = VideoGenerator._resolve_model_id("i2v")
                print(f"  T2V model: {t2v_id}")
                print(f"  I2V model: {i2v_id}")
            else:
                print("  VideoGenerator: diffusers WAN pipeline not available")
                issues.append(("WARN",
                    "diffusers installed but WAN pipeline not available",
                    "Run: scripts\\install.py videogen  or upgrade diffusers: pip install --upgrade diffusers"))
        except Exception as e:
            print(f"  VideoGenerator check: {e}")
    elif not imageio_ok and diffusers_ok:
        issues.append(("WARN", "imageio not installed — video gen MP4 export won't work",
                       "Run: scripts\\install.py videogen"))
    # Check env vars
    t2v_env = os.environ.get("VIDEO_T2V_MODEL_PATH", "")
    i2v_env = os.environ.get("VIDEO_I2V_MODEL_PATH", "")
    if t2v_env:
        print(f"  VIDEO_T2V_MODEL_PATH: {t2v_env}")
    if i2v_env:
        print(f"  VIDEO_I2V_MODEL_PATH: {i2v_env}")
    print()

    # ── 1f. CUDA check ──
    print("── CUDA ──")
    try:
        import src.core.cuda_bootstrap  # noqa: F401
        print("  CUDA bootstrap loaded")
    except Exception as e:
        print(f"  CUDA bootstrap: {e}")
        issues.append(("WARN", f"CUDA bootstrap failed: {e}",
                       "Check src/core/cuda_bootstrap.py"))

    cuda_path = os.environ.get("CUDA_PATH", "")
    print(f"  CUDA_PATH: {cuda_path or 'NOT SET'}")

    llama_installed = False
    try:
        import llama_cpp
        print(f"  llama-cpp-python: {llama_cpp.__version__}")
        llama_installed = True
    except ImportError:
        print("  llama-cpp-python: NOT INSTALLED")
        issues.append(("ERROR", "llama-cpp-python not installed — local models won't work",
                       "Run: scripts\\install.py cuda"))

    # GPU offload check (uses temp script to avoid quote issues)
    if llama_installed:
        gpu_check_script = ROOT / "scripts" / "_gpu_check_diag.py"
        gpu_check_script.write_text(
            "try:\n"
            "    from llama_cpp import llama_supports_gpu_offload\n"
            "    print(f'gpu_offload={llama_supports_gpu_offload()}')\n"
            "except Exception as e:\n"
            "    print(f'gpu_error={e}')\n",
            encoding="utf-8",
        )
        gpu_result = subprocess.run(
            f'"{PYTHON}" "{gpu_check_script}"',
            shell=True, capture_output=True, text=True, cwd=str(ROOT),
        )
        try:
            gpu_check_script.unlink()
        except OSError:
            pass

        gpu_out = gpu_result.stdout.strip()
        if "gpu_offload=True" in gpu_out:
            print("  GPU offloading: YES")
        elif "gpu_offload=False" in gpu_out:
            print("  GPU offloading: NO (CPU only)")
            if cuda_path:
                issues.append(("WARN",
                    "CUDA installed but llama-cpp-python is CPU-only (10-20x slower)",
                    "Run: scripts\\install.py cuda  → choose option [1] to rebuild with CUDA"))
        else:
            print(f"  GPU offloading: could not determine")
    print()

    # ── 1g. Grok API check ──
    print("── Grok API ──")
    grok_key = os.environ.get("GROK_API_KEY", "")
    if grok_key:
        try:
            from src.models.grok_client import GrokClient
            client = GrokClient()
            has_key = hasattr(client, "_api_key") and bool(client._api_key)
            print(f"  GrokClient: OK (key present: {has_key})")
        except Exception as e:
            print(f"  GrokClient error: {e}")
            issues.append(("WARN", f"GrokClient failed to initialize: {e}",
                           "Check GROK_API_KEY in .env"))
    else:
        print("  GROK_API_KEY not set (Grok fallback disabled)")
    print()

    # ── 1h. Key imports ──
    print("── Module Imports ──")
    modules_to_check = [
        ("src.core.agent_loop", "Agent Loop"),
        ("src.models.router", "Model Router"),
        ("src.monitoring.health_check", "Health Check"),
        ("src.monitoring.cost_tracker", "Cost Tracker"),
        ("src.interfaces.web_chat", "Web Chat"),
        ("src.interfaces.discord_bot", "Discord Bot"),
        ("src.interfaces.voice_interface", "Voice Interface"),
        ("src.tools.image_gen", "Image Generator"),
        ("src.tools.video_gen", "Video Generator"),
    ]
    for module_name, label in modules_to_check:
        try:
            __import__(module_name)
            print(f"  {label}: OK")
        except Exception as e:
            err = str(e).split("\n")[0][:60]
            print(f"  {label}: FAILED ({err})")
            issues.append(("ERROR", f"{label} import failed: {err}",
                           f"Check {module_name.replace('.', '/')}.py for errors"))
    print()

    # ── 1i. Ports check ──
    print("── Service Ports ──")
    try:
        import urllib.request
        for port, name in [(5000, "Dashboard"), (5001, "Web Chat")]:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
                print(f"  Port {port} ({name}): RUNNING")
            except Exception:
                print(f"  Port {port} ({name}): not running")
    except Exception:
        print("  Could not check ports")

    # ── 1j. Data directories ──
    print("\n── Data Directories ──")
    required_dirs = ["data", "logs", "logs/errors", "config", "models", "workspace"]
    missing_dirs = []
    for d in required_dirs:
        p = ROOT / d
        if p.is_dir():
            print(f"  {d}/: OK")
        else:
            print(f"  {d}/: MISSING")
            missing_dirs.append(d)
    if missing_dirs:
        auto_fixes.append((
            f"Create missing directories: {', '.join(missing_dirs)}",
            lambda dirs=missing_dirs: _auto_create_dirs(dirs),
        ))

    # ══════════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════════
    errors = [i for i in issues if i[0] == "ERROR"]
    warnings = [i for i in issues if i[0] == "WARN"]

    if not issues and not auto_fixes:
        print(f"\n{'=' * 60}")
        print("  All checks passed — Archi looks healthy!")
        print(f"{'=' * 60}")
        return

    print(f"\n{'=' * 60}")
    print(f"  Issues Found: {len(errors)} error(s), {len(warnings)} warning(s)")
    print(f"{'=' * 60}")

    if errors:
        print("\n  ERRORS (will prevent Archi from working):")
        for _, msg, fix in errors:
            print(f"    [!!] {msg}")
            print(f"         Fix: {fix}")

    if warnings:
        print("\n  WARNINGS (Archi will work but with reduced capability):")
        for _, msg, fix in warnings:
            print(f"    [!]  {msg}")
            print(f"         Fix: {fix}")

    # Offer auto-fixes
    if auto_fixes:
        print(f"\n  AUTO-FIXABLE ({len(auto_fixes)} item(s)):")
        for desc, _ in auto_fixes:
            print(f"    [*] {desc}")

        answer = input("\n  Apply auto-fixes? (Y/n): ").strip().lower()
        if answer in ("", "y", "yes"):
            for desc, fix_fn in auto_fixes:
                try:
                    fix_fn()
                    print(f"    Fixed: {desc}")
                except Exception as e:
                    print(f"    Failed: {desc} — {e}")
            print("\n  Auto-fixes applied. Run diagnostics again to verify.")
        else:
            print("  Skipped auto-fixes.")

    print()


def _auto_set_env(name: str, value: str) -> None:
    """Set a key=value in .env (create or update)."""
    import re
    env_path = ROOT / ".env"
    env_content = ""
    if env_path.is_file():
        env_content = env_path.read_text(encoding="utf-8")
    if f"{name}=" in env_content:
        env_content = re.sub(rf"{re.escape(name)}=.*", f"{name}={value}", env_content)
    else:
        env_content = env_content.rstrip() + f"\n{name}={value}\n"
    env_path.write_text(env_content, encoding="utf-8")


def _auto_create_dirs(dirs: list) -> None:
    """Create missing directories."""
    for d in dirs:
        (ROOT / d).mkdir(parents=True, exist_ok=True)


# ── 2. Tests ──────────────────────────────────────────────────

def run_tests() -> None:
    _header("Running Tests")

    print("  [1] Full test suite (pytest)")
    print("  [2] Quick smoke test (imports + basic checks)")
    print("  [3] CUDA model test")
    print("  [S] Skip\n")

    choice = input("Select [1]: ").strip() or "1"

    if choice == "1":
        _run(f'"{PYTHON}" -m pytest tests/ -v --tb=short')
    elif choice == "2":
        print("\n  Running smoke test...\n")
        failures = 0

        # Test core imports
        test_imports = [
            "src.core.agent_loop",
            "src.core.goal_manager",
            "src.models.router",
            "src.monitoring.health_check",
        ]
        for mod in test_imports:
            try:
                __import__(mod)
                print(f"  [PASS] import {mod}")
            except Exception as e:
                print(f"  [FAIL] import {mod}: {e}")
                failures += 1

        # Test data directories
        for d in ["data", "logs", "config"]:
            path = ROOT / d
            if path.is_dir():
                print(f"  [PASS] {d}/ exists")
            else:
                print(f"  [WARN] {d}/ missing (will be created on first run)")

        # Test .env
        env_path = ROOT / ".env"
        if env_path.is_file():
            print(f"  [PASS] .env exists")
        else:
            print(f"  [WARN] .env missing (some features may not work)")

        if failures == 0:
            print(f"\n  All smoke tests passed!")
        else:
            print(f"\n  {failures} test(s) failed.")

    elif choice == "3":
        print("\n  Running CUDA model test...")
        _run(f'"{PYTHON}" -m pytest tests/ -v -k "test_local_model or test_router" --tb=short')

    elif choice.upper() != "S":
        print("  Unknown option.")


# ── 3. Clean ──────────────────────────────────────────────────

def run_clean() -> None:
    _header("Clean — Cache & Artifact Removal")

    print("  [1] Clear web chat cache (while Archi is running)")
    print("  [2] Clear __pycache__ directories")
    print("  [3] Clear temp files (screenshots, debug artifacts)")
    print("  [4] Clear all logs")
    print("  [A] All of the above")
    print("  [S] Skip\n")

    choice = input("Select: ").strip().upper()
    if choice == "S":
        return

    items = set()
    if choice in ("1", "A"):
        items.add("cache")
    if choice in ("2", "A"):
        items.add("pycache")
    if choice in ("3", "A"):
        items.add("temp")
    if choice in ("4", "A"):
        items.add("logs")

    if not items:
        print("  Unknown option.")
        return

    cleaned = 0

    if "cache" in items:
        print("\n  Clearing web chat cache...")
        try:
            import urllib.request
            req = urllib.request.urlopen("http://127.0.0.1:5001/clear-cache", timeout=5)
            data = req.read().decode()
            print(f"    Cache cleared: {data}")
            cleaned += 1
        except Exception:
            print("    Archi web chat not running (skipped)")

    if "pycache" in items:
        print("\n  Removing __pycache__ directories (skipping venv/)...")
        skip_dirs = {"venv", ".venv", "node_modules", ".git", "_archive"}
        for dirpath, dirnames, _ in os.walk(str(ROOT)):
            # Don't descend into venv or other heavy dirs
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for d in dirnames:
                if d == "__pycache__":
                    full = os.path.join(dirpath, d)
                    shutil.rmtree(full, ignore_errors=True)
                    rel = os.path.relpath(full, str(ROOT))
                    print(f"    Removed: {rel}/")
                    cleaned += 1

    if "temp" in items:
        print("\n  Removing temp/debug files...")
        temp_patterns = [
            "data/debug_*.png",
            "data/temp_*.png",
            "workspace/a.txt",
            "workspace/test.txt",
            "structure.txt",
        ]
        for pattern in temp_patterns:
            for f in ROOT.glob(pattern):
                f.unlink()
                print(f"    Deleted: {f.relative_to(ROOT)}")
                cleaned += 1

    if "logs" in items:
        print("\n  Clearing logs...")
        logs_dir = ROOT / "logs"
        if logs_dir.is_dir():
            for f in logs_dir.rglob("*"):
                if f.is_file():
                    f.unlink()
                    print(f"    Deleted: {f.relative_to(ROOT)}")
                    cleaned += 1

    print(f"\n  Cleaned {cleaned} items.")


# ── 4. State Repair ───────────────────────────────────────────

def repair_state() -> None:
    _header("State Repair & Goal Management")
    _load_env()

    print("  [1] Reset goal state (clear all goals)")
    print("  [2] Create health optimization goal")
    print("  [3] Verify & repair data directories")
    print("  [4] Reset memory databases")
    print("  [A] Full repair (3 + 4)")
    print("  [S] Skip\n")

    choice = input("Select: ").strip().upper()
    if choice == "S":
        return

    if choice in ("1",):
        print("\n  Resetting goals...")
        goals_file = ROOT / "data" / "goals.json"
        if goals_file.exists():
            goals_file.unlink()
            print("    Removed goals.json")
        else:
            print("    No goals.json found")

    if choice in ("2",):
        print("\n  Creating health optimization goal...")
        try:
            from src.core.goal_manager import GoalManager
            gm = GoalManager()
            goal = gm.create_goal(
                description=(
                    "Analyze Health Optimization project and create synthesis:\n"
                    "1. Read all files from AI contributors\n"
                    "2. Identify common themes and consensus\n"
                    "3. Note contradictions between AIs\n"
                    "4. Create synthesized document\n"
                    "5. Create prioritized action plan\n"
                    "Location: workspace/projects/Health_Optimization/"
                ),
                user_intent="Health Optimization project analysis",
                priority=8,
            )
            gm.save_state()
            print(f"    Goal created: {goal.goal_id}")
        except Exception as e:
            print(f"    Failed: {e}")

    if choice in ("3", "A"):
        print("\n  Verifying data directories...")
        dirs = [
            "data", "data/uploads", "data/memory", "data/vectors",
            "logs", "logs/errors", "config", "models", "workspace",
        ]
        for d in dirs:
            path = ROOT / d
            if not path.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                print(f"    Created: {d}/")
            else:
                print(f"    OK: {d}/")

    if choice in ("4", "A"):
        print("\n  Resetting memory databases...")
        db_files = list((ROOT / "data").glob("*.db")) + list((ROOT / "data" / "memory").glob("*.db"))
        if db_files:
            for db_file in db_files:
                confirm = input(f"    Delete {db_file.relative_to(ROOT)}? (y/N): ").strip().lower()
                if confirm == "y":
                    db_file.unlink()
                    print(f"    Deleted: {db_file.relative_to(ROOT)}")
                else:
                    print(f"    Skipped: {db_file.relative_to(ROOT)}")
        else:
            print("    No database files found")

    print("\n  State repair complete.")


# ── Main Menu ─────────────────────────────────────────────────

def main_menu() -> None:
    _header("Archi Fix — Diagnostics & Repair")
    print("  [1] Run diagnostics (env, models, CUDA, API, ports)")
    print("  [2] Run tests (pytest)")
    print("  [3] Clean (caches, temp files, __pycache__)")
    print("  [4] State repair (goals, directories, databases)")
    print("  [A] Full diagnostic + clean")
    print("  [Q] Quit\n")

    choice = input("Select: ").strip().upper()

    if choice == "1":
        run_diagnostics()
    elif choice == "2":
        run_tests()
    elif choice == "3":
        run_clean()
    elif choice == "4":
        repair_state()
    elif choice == "A":
        run_diagnostics()
        run_clean()
    elif choice != "Q":
        print("  Unknown option.")
        main_menu()


def main() -> None:
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        dispatch = {
            "diagnose": run_diagnostics,
            "test": run_tests,
            "clean": run_clean,
            "state": repair_state,
        }
        if cmd in dispatch:
            dispatch[cmd]()
        else:
            print(f"Unknown command: {cmd}")
            print("Available: diagnose, test, clean, state")
            sys.exit(1)
    else:
        main_menu()


if __name__ == "__main__":
    main()
