#!/usr/bin/env python3
"""
Archi Factory Reset
===================
Clears all runtime state, logs, caches, and generated content while
preserving source code, configuration (prime directive, identity, rules),
and the Health_Optimization project files.

Before any destructive operation, a timestamped backup is created in
backup/resets/ (unless --no-backup is passed).

Usage:
    python scripts/reset.py                    # interactive confirmation
    python scripts/reset.py --yes              # skip confirmation (for automation)
    python scripts/reset.py --keep-user-model  # preserve learned preferences
    python scripts/reset.py --no-backup        # skip pre-reset backup
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, BACKUP_ROOT

DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
WORKSPACE_DIR = ROOT / "workspace"
RESET_BACKUP_DIR = BACKUP_ROOT / "resets"
MAX_BACKUPS = 3

# Track files that couldn't be cleared (locked by running process, etc.)
_skipped: list = []


# ── Backup ──────────────────────────────────────────────────────────────

def _create_backup() -> Path | None:
    """Create a timestamped backup of data/ and logs/ before reset.

    Stored in backup/resets/<timestamp>/, mirroring the original structure.
    Skips data/vectors/ (large, regenerated).
    Returns the backup directory path, or None on failure.
    """
    ts = time.strftime("%Y-%m-%d_%H%M%S")
    backup_path = RESET_BACKUP_DIR / f"reset_{ts}"

    try:
        backup_path.mkdir(parents=True, exist_ok=True)

        # Back up data/ (minus vectors and __pycache__)
        if DATA_DIR.exists():
            data_backup = backup_path / "data"
            shutil.copytree(
                str(DATA_DIR), str(data_backup),
                ignore=shutil.ignore_patterns("vectors", "__pycache__"),
                dirs_exist_ok=True,
            )

        # Back up logs/
        if LOGS_DIR.exists():
            logs_backup = backup_path / "logs"
            shutil.copytree(str(LOGS_DIR), str(logs_backup), dirs_exist_ok=True)

        print(f"  Backup created: backup/resets/reset_{ts}/")
        return backup_path
    except Exception as e:
        print(f"  [WARNING] Backup failed: {e}")
        print("  Continuing with reset (data may be lost).")
        return None


def _prune_old_backups() -> None:
    """Keep only the newest MAX_BACKUPS reset snapshots."""
    if not RESET_BACKUP_DIR.exists():
        return
    backups = sorted(
        [d for d in RESET_BACKUP_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        try:
            shutil.rmtree(str(oldest))
            print(f"  Pruned old backup: {oldest.name}")
        except OSError:
            pass


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


def clear_data_runtime(clear_project_context: bool = False,
                       keep_user_model: bool = False) -> int:
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
        "file_manifest.json":              {"files": {}},
        "cost_usage.json":                 None,  # special handling below
        "initiative_state.json":            {},
        "idea_history.json":                {"version": 1, "last_updated": None, "ideas": []},
    }

    # User model: only wipe if not explicitly kept
    if keep_user_model:
        um_path = DATA_DIR / "user_model.json"
        if um_path.exists():
            _banner("user_model.json preserved (learned preferences, facts, corrections)")
    else:
        json_resets["user_model.json"] = {"version": 2, "last_updated": None, "facts": [], "preferences": [], "corrections": [], "patterns": [], "style": [], "interests": []}

    # Project context: only clear if explicitly requested
    if clear_project_context:
        json_resets["project_context.json"] = {}
        _banner("project_context.json will be cleared")
    else:
        ctx_path = DATA_DIR / "project_context.json"
        if ctx_path.exists():
            _banner("project_context.json preserved (projects, interests, focus areas)")
    for filename, default in json_resets.items():
        fpath = DATA_DIR / filename
        if default is None:
            continue  # handled separately below
        try:
            fpath.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")
            count += 1
        except Exception:
            _skipped.append(str(fpath))

    # cost_usage.json: reset daily usage but PRESERVE monthly totals
    # so a reset mid-month doesn't hide accumulated spend from budget enforcement
    cost_path = DATA_DIR / "cost_usage.json"
    try:
        monthly = {}
        if cost_path.exists():
            old = json.loads(cost_path.read_text(encoding="utf-8"))
            monthly = old.get("monthly_usage", {})
        reset_cost = {"usage": {}, "daily_usage": {}, "monthly_usage": monthly}
        cost_path.write_text(json.dumps(reset_cost, indent=2) + "\n", encoding="utf-8")
        count += 1
        if monthly:
            total = sum(monthly.values())
            _banner(f"cost_usage.json reset (monthly total ${total:.4f} preserved)")
        else:
            _banner("cost_usage.json reset")
    except Exception:
        _skipped.append(str(cost_path))

    _banner(f"JSON state files reset to defaults ({len(json_resets)} files)")

    # JSONL files → truncate
    for fpath in DATA_DIR.glob("*.jsonl"):
        try:
            fpath.write_text("", encoding="utf-8")
            count += 1
        except Exception:
            _skipped.append(str(fpath))
    _banner("JSONL logs truncated (dream_log, etc.)")

    # Chat history (JSON)
    chat_hist = DATA_DIR / "chat_history.json"
    if chat_hist.exists():
        try:
            chat_hist.write_text("[]", encoding="utf-8")
            count += 1
            _banner("chat_history.json reset")
        except Exception:
            _skipped.append(str(chat_hist))

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

    # __pycache__ and .pytest_cache directories (stale compiled bytecode)
    cache_dir_count = 0
    for pattern in ("__pycache__", ".pytest_cache"):
        for d in ROOT.rglob(pattern):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                cache_dir_count += 1
    if cache_dir_count:
        _banner(f"Python cache directories removed ({cache_dir_count})")
    count += cache_dir_count

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
    print("    • Monthly cost totals (budget enforcement)")
    print()
    print("  Archi is ready for a fresh start.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Archi Factory Reset")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt")
    parser.add_argument("--clear-context", action="store_true",
                        help="Also clear project context (projects, interests, focus areas)")
    parser.add_argument("--keep-user-model", action="store_true",
                        help="Preserve user_model.json (learned preferences, facts, corrections)")
    parser.add_argument("--wipe-user-model", action="store_true",
                        help="Force wipe user_model.json even with --yes")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip pre-reset backup (for CI/automation)")
    args = parser.parse_args()

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║       ARCHI — FACTORY RESET          ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print("  This will clear:")
    print("    • All logs (conversations, errors, traces, action logs)")
    print("    • All runtime state (goals, experiences, idea backlog, initiative state)")
    print("    • Interesting findings queue, file manifest, daily cost usage")
    print("    • Dream cycle history, synthesis log & overnight results")
    print("    • Memory databases (memory.db, metrics.db, ui_memory.db)")
    print("    • Vector memory store")
    print("    • All caches (query cache, plan state, source backups)")
    print("    • Generated workspace content (images, videos, reports)")
    print("    • Chat history & user preferences")
    print("    • User model (unless you choose to keep it)")
    print("    • All __pycache__ and .pytest_cache directories")
    print()
    print("  This will KEEP:")
    print("    • All source code & tests")
    print("    • Configuration (prime directive, identity, rules)")
    print("    • .env & environment settings")
    print("    • Monthly cost totals (budget enforcement)")
    print("    • Project context (unless you choose to clear it)")
    print("    • User project files (workspace/projects/)")
    print()

    if not args.yes:
        answer = input("  Proceed with reset? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)

    # Decide whether to clear project context
    clear_project_ctx = args.clear_context
    ctx_path = DATA_DIR / "project_context.json"
    if ctx_path.exists() and not args.yes and not clear_project_ctx:
        print()
        print("  Project context (data/project_context.json) stores your active")
        print("  projects, interests, and focus areas. Clearing it means Archi")
        print("  will fall back to defaults from archi_identity.yaml.")
        print()
        ctx_answer = input("  Also clear project context? [y/N] ").strip().lower()
        clear_project_ctx = ctx_answer in ("y", "yes")

    # Decide whether to keep user model.
    # Default: keep it with --yes (safe for automation), ask interactively otherwise.
    # --wipe-user-model overrides everything for explicit full resets.
    keep_user_model = (args.keep_user_model or args.yes) and not args.wipe_user_model
    um_path = DATA_DIR / "user_model.json"
    if um_path.exists() and not args.yes and not keep_user_model:
        print()
        print("  User model (data/user_model.json) stores learned preferences,")
        print("  facts, corrections, and tone feedback accumulated over many")
        print("  sessions. Wiping it means Archi starts with no memory of you.")
        print()
        um_answer = input("  Keep user model? [Y/n] ").strip().lower()
        keep_user_model = um_answer not in ("n", "no")

    # Pre-reset backup
    if not args.no_backup:
        print()
        _create_backup()

    print()
    print("  Resetting...")
    print()

    total = 0
    total += clear_logs()
    total += clear_data_runtime(clear_project_context=clear_project_ctx,
                                keep_user_model=keep_user_model)
    total += clear_workspace_generated()

    # Clear profile-setup-declined marker so start.py offers it again
    declined_marker = DATA_DIR / ".profile_setup_declined"
    if declined_marker.exists():
        try:
            declined_marker.unlink()
        except OSError:
            pass

    # Prune old backups (keep MAX_BACKUPS)
    if not args.no_backup:
        _prune_old_backups()

    print_summary(total)


if __name__ == "__main__":
    main()
