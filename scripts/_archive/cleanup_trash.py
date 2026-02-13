#!/usr/bin/env python3
"""
One-time cleanup: remove trash/debug/test artifacts from the repo.
Run from the project root:  python scripts/cleanup_trash.py
"""

import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files to delete (relative to project root)
TRASH_FILES = [
    "structure.txt",                        # 4.7 MB Windows tree dump
    "data/debug_vision_detection.png",      # debug screenshot
    "data/temp_screenshot.png",             # temp artifact
    "data/temp_screenshot_original.png",    # temp artifact
    "workspace/a.txt",                      # empty test file
    "workspace/test.txt",                   # test artifact
    "logs/conversations.jsonl",             # empty log
    "logs/startup.log",                     # empty log
    "logs/errors/2026-02-11.log",           # empty log
]

deleted = 0
for rel in TRASH_FILES:
    path = os.path.join(ROOT, rel)
    if os.path.isfile(path):
        size = os.path.getsize(path)
        os.remove(path)
        print(f"  Deleted: {rel} ({size:,} bytes)")
        deleted += 1
    else:
        print(f"  Skipped (not found): {rel}")

# Remove all __pycache__ directories
for dirpath, dirnames, _ in os.walk(ROOT):
    for d in dirnames:
        if d == "__pycache__":
            full = os.path.join(dirpath, d)
            shutil.rmtree(full)
            print(f"  Removed: {os.path.relpath(full, ROOT)}/")
            deleted += 1

print(f"\nDone. Removed {deleted} items.")
