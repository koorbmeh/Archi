#!/usr/bin/env python3
"""
Archi Factory Reset
===================
Clears all runtime state, logs, caches, and generated content while
preserving source code, configuration (prime directive, identity, rules),
and the Health_Optimization project files.

Usage:
    python scripts/reset.py          # interactive confirmation
    python scripts/reset.py --yes    # skip confirmation (for automation)
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"

# Track files that couldn't be cleared (locked by running process, etc.)
_skipped: list = []


def _banner(msg: str) -> None:
    print(f"  {'✓':>3}  {msg}")


def _safe_delete(path: Path) -> bool:
    """Try to delete a file; if locked, truncate it instead."""
    try:
        path.unlink()
        return True
    except (PermissionError, OSError):
        try:
            path.write_text("", encoding="utf-8")
            return True
        except Exception:
            _skipped.append(str(path))
            return False


def _safe_rmtree(path: Path) -> bool:
    """Try shutil.rmtree; fall back to clearing files individually."""
    try:
        shutil.rmtree(path)
        return True
    except (PermissionError, OSError):
        # Fall back: clear files one by one
        for item in sorted(path.rglob("*"), reverse=True):
            if item.is_file():
                _safe_delete(item)
            elif item.is_dir() and item != path:
                try:
                    item.rmdir()
                except OSError:
                    pass
        return False


# ── Clear functions ──────────────────────────────────────────────────────

def clear_logs() -> int:
    """Delete all log files and subdirectories under logs/."""
    count = 0
    if not LOGS_DIR.exists():
        return count

    for item in sorted(LOGS_DIR.rglob("*"), reverse=True):
        if item.is_file():
            if _safe_delete(item):
                count += 1
        elif item.is_dir() and item != LOGS_DIR:
            try:
                item.rmdir()
            except OSError:
                pass
    _banner(f"Logs cleared ({count} files)")
    return count


def clear_data_runtime() -> int:
    """Clear runtime data files while preserving directory structure."""
    count = 0
    if not DATA_DIR.exists():
        return count

    # JSON state files → reset to empty defaults
    json_resets = {
        "goals_state.json":                 {"goals": []},
        "experiences.json":                 {"experiences": []},
        "idea_backlog.json":                {"ideas": []},
        "overnight_results.json":           {},
        "interesting_findings_queue.json":  [],
        "user_preferences.json":            {},
        "web_chat_history.json":            [],
    }
    for filename, default in json_resets.items():
        fpath = DATA_DIR / filename
        try:
            fpath.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")
            count += 1
        except Exception:
            _skipped.append(str(fpath))
    _banner(f"JSON state files reset to defaults ({len(json_resets)} files)")

    # JSONL files → truncate
    for fpath in DATA_DIR.glob("*.jsonl"):
        try:
            fpath.write_text("", encoding="utf-8")
            count += 1
        except Exception:
            _skipped.append(str(fpath))
    _banner("JSONL logs truncated (dream_log, etc.)")

    # Plain text state
    txt_state = DATA_DIR / "chat_history.txt"
    if txt_state.exists():
        try:
            txt_state.write_text("", encoding="utf-8")
            count += 1
            _banner("chat_history.txt cleared")
        except Exception:
            _skipped.append(str(txt_state))

    # Backup files (*.backup)
    backup_count = 0
    for fpath in DATA_DIR.glob("*.backup"):
        if _safe_delete(fpath):
            backup_count += 1
    if backup_count:
        _banner(f"Backup files removed ({backup_count})")
    count += backup_count

    # SQLite databases → clear all rows, keep schema
    for db_file in DATA_DIR.glob("*.db"):
        _clear_sqlite(db_file)
        count += 1

    # Cache directory
    cache_dir = DATA_DIR / "cache"
    if cache_dir.exists():
        _safe_rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        count += 1
        _banner("Cache directory cleared")

    # Plan state directory
    plan_dir = DATA_DIR / "plan_state"
    if plan_dir.exists():
        _safe_rmtree(plan_dir)
        plan_dir.mkdir(parents=True, exist_ok=True)
        count += 1
        _banner("Plan state cleared")

    # Source backups directory
    backups_dir = DATA_DIR / "source_backups"
    if backups_dir.exists():
        bak_count = sum(1 for f in backups_dir.iterdir() if f.is_file())
        _safe_rmtree(backups_dir)
        backups_dir.mkdir(parents=True, exist_ok=True)
        count += bak_count
        _banner(f"Source backups cleared ({bak_count} files)")

    # Vector store (LanceDB)
    vectors_dir = DATA_DIR / "vectors"
    if vectors_dir.exists():
        _safe_rmtree(vectors_dir)
        vectors_dir.mkdir(parents=True, exist_ok=True)
        count += 1
        _banner("Vector memory store cleared")

    # Uploaded files
    uploads_dir = DATA_DIR / "uploads"
    if uploads_dir.exists():
        upload_count = sum(1 for f in uploads_dir.iterdir() if f.is_file())
        _safe_rmtree(uploads_dir)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        count += upload_count
        if upload_count:
            _banner(f"Uploads cleared ({upload_count} files)")

    # Tool manifest (regenerated at runtime)
    manifest = DATA_DIR / "tool_manifest.yaml"
    if manifest.exists():
        if _safe_delete(manifest):
            count += 1
            _banner("Tool manifest removed (will regenerate)")

    return count


def _clear_sqlite(db_path: Path) -> None:
    """Drop all rows from user-data tables in a SQLite DB, keep schema."""
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()
                  if not row[0].startswith("sqlite_")]
        for table in tables:
            cursor.execute(f"DELETE FROM [{table}]")
        conn.commit()
        conn.execute("VACUUM")
        conn.close()
        _banner(f"Database {db_path.name} cleared ({len(tables)} tables)")
    except Exception as e:
        print(f"  ⚠  Could not clear {db_path.name}: {e}")


def clear_workspace_generated() -> int:
    """Clear dream-cycle-generated workspace content, but KEEP user projects."""
    count = 0
    if not WORKSPACE_DIR.exists():
        return count

    # Directories to completely clear (generated content)
    clear_dirs = ["images", "videos", "reports", "research", "scripts", "logs"]
    for dirname in clear_dirs:
        dirpath = WORKSPACE_DIR / dirname
        if dirpath.exists():
            file_count = sum(1 for _ in dirpath.rglob("*") if _.is_file())
            _safe_rmtree(dirpath)
            dirpath.mkdir(parents=True, exist_ok=True)
            count += file_count
            if file_count:
                _banner(f"workspace/{dirname}/ cleared ({file_count} files)")

    # Remove loose generated files at workspace root
    loose_count = 0
    for fpath in WORKSPACE_DIR.iterdir():
        if fpath.is_file():
            if _safe_delete(fpath):
                loose_count += 1
    if loose_count:
        _banner(f"Loose workspace files removed ({loose_count})")
    count += loose_count

    # KEEP workspace/projects/ entirely (user's Health_Optimization, etc.)
    _banner("workspace/projects/ preserved (user project files kept)")

    return count


def print_summary(total: int) -> None:
    print()
    print(f"  {'─' * 50}")
    print(f"  Reset complete. {total} items cleared.")
    if _skipped:
        print()
        print(f"  ⚠  {len(_skipped)} file(s) could not be cleared (locked?):")
        for s in _skipped[:10]:
            print(f"      {s}")
        if len(_skipped) > 10:
            print(f"      ... and {len(_skipped) - 10} more")
        print("      Tip: stop Archi first, then re-run this script.")
    print()
    print("  Preserved:")
    print("    • Source code (src/, tests/, scripts/)")
    print("    • Configuration (config/, .env)")
    print("    • Prime directive & identity")
    print("    • User project files (workspace/projects/)")
    print()
    print("  Archi is ready for a fresh start.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Archi Factory Reset")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt")
    args = parser.parse_args()

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║       ARCHI — FACTORY RESET          ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print("  This will clear:")
    print("    • All logs (conversations, errors, traces, action logs)")
    print("    • All runtime state (goals, experiences, idea backlog)")
    print("    • Interesting findings queue")
    print("    • Dream cycle history & overnight results")
    print("    • Memory databases (memory.db, metrics.db, ui_memory.db)")
    print("    • Vector memory store")
    print("    • All caches (query cache, plan state, source backups)")
    print("    • Generated workspace content (images, videos, reports)")
    print("    • Chat history & user preferences")
    print()
    print("  This will KEEP:")
    print("    • All source code & tests")
    print("    • Configuration (prime directive, identity, rules)")
    print("    • .env & environment settings")
    print("    • User project files (workspace/projects/)")
    print()

    if not args.yes:
        answer = input("  Proceed with reset? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)

    print()
    print("  Resetting...")
    print()

    total = 0
    total += clear_logs()
    total += clear_data_runtime()
    total += clear_workspace_generated()

    print_summary(total)


if __name__ == "__main__":
    main()
