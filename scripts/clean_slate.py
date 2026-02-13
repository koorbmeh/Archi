#!/usr/bin/env python3
"""
Clean Slate — Reset Archi's data files for a fresh start.

Creates .backup copies of each file before wiping.
Run from project root: python scripts/clean_slate.py
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Resolve project root (parent of scripts/)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"


def backup_and_write(filepath: Path, new_content: str) -> None:
    """Create a timestamped backup, then write new content."""
    if filepath.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = filepath.with_suffix(f".{ts}.backup")
        shutil.copy2(filepath, backup)
        print(f"  Backed up: {filepath.name} -> {backup.name}")
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(new_content, encoding="utf-8")
    print(f"  Reset:     {filepath.name}")


def clean_goals() -> None:
    """Remove all goals, reset counters."""
    path = DATA_DIR / "goals_state.json"
    new = {"next_goal_id": 1, "next_task_id": 1, "goals": []}
    backup_and_write(path, json.dumps(new, indent=2))


def clean_experiences() -> None:
    """Clear all experiences, patterns, metrics, and action_stats."""
    path = DATA_DIR / "experiences.json"
    new = {"experiences": [], "patterns": {}, "metrics": {}, "action_stats": {}}
    backup_and_write(path, json.dumps(new, indent=2))


def clean_idea_backlog() -> None:
    """Clear ideas, reset brainstorm timestamp."""
    path = DATA_DIR / "idea_backlog.json"
    new = {"ideas": [], "last_brainstorm": None}
    backup_and_write(path, json.dumps(new, indent=2))


def clean_overnight_results() -> None:
    """Empty overnight results array."""
    path = DATA_DIR / "overnight_results.json"
    backup_and_write(path, "[]")


def clean_dream_log() -> None:
    """Clear dream log JSONL (truncate to empty)."""
    path = DATA_DIR / "dream_log.jsonl"
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(f".{ts}.backup")
        shutil.copy2(path, backup)
        print(f"  Backed up: {path.name} -> {backup.name}")
        path.write_text("", encoding="utf-8")
        print(f"  Cleared:   {path.name}")
    else:
        print(f"  Skipped:   {path.name} (not found)")


def clean_synthesis_log() -> None:
    """Clear synthesis log if it exists."""
    path = DATA_DIR / "synthesis_log.json"
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(f".{ts}.backup")
        shutil.copy2(path, backup)
        path.write_text("", encoding="utf-8")
        print(f"  Cleared:   {path.name}")


def clean_plan_state() -> None:
    """Delete all crash-recovery state files."""
    plan_dir = DATA_DIR / "plan_state"
    if plan_dir.exists():
        count = 0
        for f in plan_dir.glob("*.json"):
            try:
                f.unlink()
            except PermissionError:
                f.write_text("{}", encoding="utf-8")
            count += 1
        print(f"  Cleared:   plan_state/ ({count} files)")
    else:
        print(f"  Skipped:   plan_state/ (not found)")


def main() -> None:
    print(f"Archi Clean Slate")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Data dir:     {DATA_DIR}")
    print()

    if not DATA_DIR.exists():
        print("ERROR: data/ directory not found. Run from project root.")
        sys.exit(1)

    print("Resetting data files...")
    clean_goals()
    clean_experiences()
    clean_idea_backlog()
    clean_overnight_results()
    clean_dream_log()
    clean_synthesis_log()
    clean_plan_state()

    print()
    print("Done. Archi has a clean slate.")
    print("Backups saved alongside originals with .backup suffix.")


if __name__ == "__main__":
    main()
