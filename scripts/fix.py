#!/usr/bin/env python3
r"""
Archi Fix — diagnostics, tests, cache clearing, and state repair.

Usage:
    python scripts/fix.py              (interactive menu)
    python scripts/fix.py diagnose      (run all diagnostics)
    python scripts/fix.py test          (run pytest suite)
    python scripts/fix.py clean         (clear caches & trash)
    python scripts/fix.py state         (repair state / create goals)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, PYTHON, VENV_PYTHON, header, run, load_env, set_env

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


# ── 1. Diagnostics ────────────────────────────────────────────

def run_diagnostics() -> None:
    header("Archi Diagnostics")
    issues = []       # (severity, message, fix_hint)
    auto_fixes = []   # (description, callable)

    # ── Python & venv ──
    print("── Python Environment ──")
    print(f"  Python: {sys.executable}")
    print(f"  Version: {sys.version}")
    venv_ok = VENV_PYTHON.exists()
    print(f"  Venv: {'Yes' if venv_ok else 'No'}")
    if not venv_ok:
        issues.append(("ERROR", "No virtual environment found",
                        "Run: python -m venv venv"))
    print()

    # ── .env check ──
    load_env()
    print("── Environment Variables ──")
    env_file = ROOT / ".env"
    if not env_file.is_file():
        issues.append(("ERROR", ".env file missing",
                        "Copy .env.example to .env and fill in your values"))

    # Check the key that actually matters
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    print(f"  OPENROUTER_API_KEY: {'set (' + str(len(openrouter_key)) + ' chars)' if openrouter_key else 'NOT SET'}")
    if not openrouter_key:
        issues.append(("ERROR", "OPENROUTER_API_KEY not set — Archi cannot make API calls",
                       "Add OPENROUTER_API_KEY=your_key to .env"))
    print(f"  DISCORD_BOT_TOKEN: {'set (' + str(len(discord_token)) + ' chars)' if discord_token else 'NOT SET'}")
    if not discord_token:
        issues.append(("WARN", "DISCORD_BOT_TOKEN not set — Discord interface disabled",
                       "Add DISCORD_BOT_TOKEN=your_token to .env"))

    # Optional env vars
    models_dir = ROOT / "models"
    optional_keys = [
        "IMAGE_MODEL_PATH", "ARCHI_VOICE_ENABLED",
    ]
    for key in optional_keys:
        val = os.environ.get(key, "")
        if val:
            if val.endswith((".onnx", ".safetensors")):
                fname = Path(val).name
                exists = Path(val).exists()
                display = f"{fname} ({'found' if exists else 'FILE MISSING'})"
                if not exists:
                    issues.append(("WARN", f"{key} points to missing file: {fname}",
                                   "Check the path in .env"))
            else:
                display = val
        else:
            display = "not set (optional)"
            # Auto-detect image model
            if key == "IMAGE_MODEL_PATH" and models_dir.is_dir():
                safetensors = [f for f in models_dir.glob("*.safetensors")
                               if "mmproj" not in f.name.lower()]
                if len(safetensors) == 1:
                    display = f"not set (found {safetensors[0].name} — can auto-fix)"
                    fix_val = safetensors[0].resolve().as_posix()
                    auto_fixes.append((
                        f"Set IMAGE_MODEL_PATH in .env",
                        lambda v=fix_val: set_env("IMAGE_MODEL_PATH", v),
                    ))
        print(f"  {key}: {display}")
    print()

    # ── Model files ──
    print("── Model Files ──")
    if models_dir.is_dir():
        safetensors_files = list(models_dir.glob("*.safetensors"))
        for f in safetensors_files:
            size_gb = f.stat().st_size / (1024**3)
            print(f"  {f.name}: {size_gb:.2f} GB (image gen)")
        if not safetensors_files:
            print("  No .safetensors files (image gen not available)")
    else:
        print("  models/ directory not found")
    print()

    # ── Image generation ──
    print("── Image Generation ──")
    try:
        import torch as _torch
        cuda_tag = "CUDA" if _torch.cuda.is_available() else "CPU-only"
        print(f"  PyTorch: {_torch.__version__} ({cuda_tag})")
        if not _torch.cuda.is_available():
            issues.append(("WARN",
                "PyTorch has NO CUDA support — image gen will be CPU-only (very slow)",
                "Run: scripts/install.py imagegen"))
    except ImportError:
        print("  PyTorch: NOT INSTALLED")

    for pkg_name, import_name in [("diffusers", "diffusers"), ("accelerate", "accelerate"),
                                   ("safetensors", "safetensors")]:
        try:
            mod = __import__(import_name)
            print(f"  {pkg_name}: {mod.__version__}")
        except ImportError:
            print(f"  {pkg_name}: not installed")
    print()

    # ── Key imports ──
    print("── Module Imports ──")
    modules_to_check = [
        ("src.core.agent_loop", "Agent Loop"),
        ("src.models.router", "Model Router"),
        ("src.monitoring.health_check", "Health Check"),
        ("src.monitoring.cost_tracker", "Cost Tracker"),
        ("src.interfaces.discord_bot", "Discord Bot"),
        ("src.tools.image_gen", "Image Generator"),
    ]
    for module_name, label in modules_to_check:
        try:
            __import__(module_name)
            print(f"  {label}: OK")
        except Exception as e:
            err = str(e).split("\n")[0][:60]
            print(f"  {label}: FAILED ({err})")
            issues.append(("WARN", f"{label} import failed: {err}",
                           f"Check {module_name.replace('.', '/')}.py"))
    print()

    # ── API connectivity ──
    print("── API Connectivity ──")
    load_env()
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            from src.models.openrouter_client import OpenRouterClient
            client = OpenRouterClient()
            r = client.generate("Say OK", max_tokens=5)
            if r.get("success"):
                model = r.get("model", "unknown")
                print(f"  OpenRouter API: OK (model={model})")
            else:
                err = r.get("error", "unknown error")
                print(f"  OpenRouter API: FAILED ({err})")
                issues.append(("ERROR", f"OpenRouter API call failed: {err}",
                               "Check OPENROUTER_API_KEY and network connectivity"))
        except Exception as e:
            err = str(e).split("\n")[0][:80]
            print(f"  OpenRouter API: ERROR ({err})")
            issues.append(("ERROR", f"OpenRouter API error: {err}",
                           "Check OPENROUTER_API_KEY and network connectivity"))
    else:
        print("  OpenRouter API: SKIPPED (no API key)")
    print()

    # ── Router smoke test ──
    print("── Router Smoke Test ──")
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            from src.models.router import ModelRouter
            router = ModelRouter()
            stats = router.get_stats()
            print(f"  Router init: OK")
            print(f"  Cache entries: {stats.get('cached_entries', 0)}")
            info = router.get_active_model_info()
            print(f"  Active model: {info.get('display', 'unknown')}")
        except Exception as e:
            err = str(e).split("\n")[0][:80]
            print(f"  Router init: FAILED ({err})")
            issues.append(("WARN", f"Router init failed: {err}",
                           "Check src/models/router.py"))
    else:
        print("  Router: SKIPPED (no API key)")
    print()

    # ── Cost tracker ──
    print("── Cost Tracker ──")
    try:
        from src.monitoring.cost_tracker import CostTracker
        ct = CostTracker()
        summary = ct.get_summary()
        today_data = summary.get("today", {})
        month_data = summary.get("month", {})
        today_cost = today_data.get("total_cost", 0.0) if isinstance(today_data, dict) else 0.0
        month_cost = month_data.get("total_cost", 0.0) if isinstance(month_data, dict) else 0.0
        print(f"  Today: ${today_cost:.4f}")
        print(f"  This month: ${month_cost:.4f}")
        print(f"  All-time calls: {summary.get('total_calls', 0)}")
    except Exception as e:
        err = str(e).split("\n")[0][:60]
        print(f"  Cost tracker: FAILED ({err})")
        issues.append(("WARN", f"Cost tracker failed: {err}",
                       "Check src/monitoring/cost_tracker.py"))
    print()

    # ── Data directories ──
    print("── Data Directories ──")
    required_dirs = ["data", "logs", "config", "workspace"]
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

    # ── Summary ──
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
        print("\n  ERRORS:")
        for _, msg, fix in errors:
            print(f"    [!!] {msg}")
            print(f"         Fix: {fix}")

    if warnings:
        print("\n  WARNINGS:")
        for _, msg, fix in warnings:
            print(f"    [!]  {msg}")
            print(f"         Fix: {fix}")

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


def _auto_create_dirs(dirs: list) -> None:
    for d in dirs:
        (ROOT / d).mkdir(parents=True, exist_ok=True)


# ── 2. Tests ──────────────────────────────────────────────────

def run_tests() -> None:
    header("Running Tests")

    print("  [1] Full test suite (pytest)")
    print("  [2] Quick smoke test (imports + basic checks)")
    print("  [S] Skip\n")

    choice = input("Select [1]: ").strip() or "1"

    if choice == "1":
        run(f'"{PYTHON}" -m pytest tests/ -v --tb=short', check=False)
    elif choice == "2":
        print("\n  Running smoke test...\n")
        failures = 0
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

        for d in ["data", "logs", "config"]:
            path = ROOT / d
            status = "PASS" if path.is_dir() else "WARN"
            print(f"  [{status}] {d}/ {'exists' if path.is_dir() else 'missing'}")

        env_path = ROOT / ".env"
        status = "PASS" if env_path.is_file() else "WARN"
        print(f"  [{status}] .env {'exists' if env_path.is_file() else 'missing'}")

        print(f"\n  {'All smoke tests passed!' if failures == 0 else f'{failures} test(s) failed.'}")

    elif choice.upper() != "S":
        print("  Unknown option.")


# ── 3. Clean ──────────────────────────────────────────────────

def run_clean() -> None:
    header("Clean — Cache & Artifact Removal")

    print("  [1] Clear __pycache__ directories")
    print("  [2] Clear temp files (screenshots, debug artifacts)")
    print("  [3] Clear all logs")
    print("  [A] All of the above")
    print("  [S] Skip\n")

    choice = input("Select: ").strip().upper()
    if choice == "S":
        return

    items = set()
    if choice in ("1", "A"):
        items.add("pycache")
    if choice in ("2", "A"):
        items.add("temp")
    if choice in ("3", "A"):
        items.add("logs")

    if not items:
        print("  Unknown option.")
        return

    cleaned = 0

    if "pycache" in items:
        print("\n  Removing __pycache__ directories (skipping venv/)...")
        skip_dirs = {"venv", ".venv", "node_modules", ".git"}
        for dirpath, dirnames, _ in os.walk(str(ROOT)):
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
        for pattern in ["data/debug_*.png", "data/temp_*.png"]:
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
    header("State Repair & Goal Management")
    load_env()

    print("  [1] Reset goal state (clear all goals)")
    print("  [2] Verify & repair data directories")
    print("  [3] Reset memory databases")
    print("  [A] Full repair (2 + 3)")
    print("  [S] Skip\n")

    choice = input("Select: ").strip().upper()
    if choice == "S":
        return

    if choice == "1":
        print("\n  Resetting goals...")
        goals_file = ROOT / "data" / "goals_state.json"
        if goals_file.exists():
            goals_file.write_text('{"goals": []}\n', encoding="utf-8")
            print("    Reset goals_state.json")
        else:
            print("    No goals_state.json found")

    if choice in ("2", "A"):
        print("\n  Verifying data directories...")
        dirs = [
            "data", "data/vectors", "logs", "config", "workspace",
        ]
        for d in dirs:
            path = ROOT / d
            if not path.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                print(f"    Created: {d}/")
            else:
                print(f"    OK: {d}/")

    if choice in ("3", "A"):
        print("\n  Resetting memory databases...")
        db_files = list((ROOT / "data").glob("*.db"))
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


# ── Main ─────────────────────────────────────────────────────

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
        header("Archi Fix — Diagnostics & Repair")
        print("  [1] Run diagnostics (env, models, imports)")
        print("  [2] Run tests (pytest)")
        print("  [3] Clean (temp files, __pycache__)")
        print("  [4] State repair (goals, directories, databases)")
        print("  [A] Full diagnostic + clean")
        print("  [Q] Quit\n")

        choice = input("Select: ").strip().upper()
        dispatch = {
            "1": run_diagnostics, "2": run_tests,
            "3": run_clean, "4": repair_state,
        }
        if choice == "A":
            run_diagnostics()
            run_clean()
        elif choice != "Q":
            func = dispatch.get(choice)
            if func:
                func()
            else:
                print("  Unknown option.")


if __name__ == "__main__":
    main()
