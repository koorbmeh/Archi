"""Daily journal — Archi's continuity-of-experience system.

Session 197: initial implementation (DESIGN_BECOMING_SOMEONE.md Phase 1b).

Archi maintains a running journal for each day: tasks completed,
conversations had, things learned, observations.  Not shown to Jesse
unless asked — it's for Archi's internal continuity.  Morning
orientation reads recent entries to re-establish context.

Data lives in ``data/journal/YYYY-MM-DD.json``.  Each file is a dict
with a ``entries`` list of timestamped items, plus aggregate counters
(tasks_completed, conversations, observations).

Pruning: entries older than 30 days are auto-removed on load.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

_JOURNAL_DIR = "data/journal"
_DT_FMT = "%Y-%m-%dT%H:%M:%S"
_DATE_FMT = "%Y-%m-%d"
_MAX_ENTRIES_PER_DAY = 200
_RETENTION_DAYS = 30


# ── Data helpers ─────────────────────────────────────────────────────

def _journal_dir() -> str:
    return str(_base_path() / _JOURNAL_DIR)


def _journal_path(day: date) -> str:
    return os.path.join(_journal_dir(), f"{day.strftime(_DATE_FMT)}.json")


def _empty_day() -> dict:
    """Skeleton for a new day's journal."""
    return {
        "date": date.today().strftime(_DATE_FMT),
        "entries": [],
        "summary": {
            "tasks_completed": 0,
            "conversations": 0,
            "observations": 0,
            "things_learned": 0,
        },
    }


# ── Persistence ──────────────────────────────────────────────────────

def load_day(day: Optional[date] = None) -> dict:
    """Load a single day's journal.  Returns empty skeleton if missing."""
    day = day or date.today()
    path = _journal_path(day)
    if not os.path.isfile(path):
        skeleton = _empty_day()
        skeleton["date"] = day.strftime(_DATE_FMT)
        return skeleton
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load journal for %s: %s", day, e)
        skeleton = _empty_day()
        skeleton["date"] = day.strftime(_DATE_FMT)
        return skeleton


def save_day(data: dict) -> None:
    """Atomically write a day's journal to disk."""
    day_str = data.get("date", date.today().strftime(_DATE_FMT))
    day = datetime.strptime(day_str, _DATE_FMT).date()
    path = _journal_path(day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error("Failed to save journal for %s: %s", day_str, e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Entry recording ──────────────────────────────────────────────────

def add_entry(
    entry_type: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    day: Optional[date] = None,
) -> None:
    """Add a journal entry to today's (or specified day's) journal.

    Entry types: task_completed, conversation, observation, thing_learned,
    dream_cycle, mood_signal, reflection.
    """
    day = day or date.today()
    data = load_day(day)
    entries = data.get("entries", [])

    if len(entries) >= _MAX_ENTRIES_PER_DAY:
        logger.warning("Journal entry cap reached for %s — skipping", day)
        return

    entry = {
        "time": datetime.now().strftime(_DT_FMT),
        "type": entry_type,
        "content": content,
    }
    if metadata:
        entry["metadata"] = metadata

    entries.append(entry)
    data["entries"] = entries

    # Update summary counters
    summary = data.get("summary", {})
    counter_map = {
        "task_completed": "tasks_completed",
        "conversation": "conversations",
        "observation": "observations",
        "thing_learned": "things_learned",
    }
    counter_key = counter_map.get(entry_type)
    if counter_key:
        summary[counter_key] = summary.get(counter_key, 0) + 1
        data["summary"] = summary

    save_day(data)
    logger.debug("Journal entry (%s): %s", entry_type, content[:80])


# ── Query helpers ────────────────────────────────────────────────────

def get_recent_entries(days: int = 3, entry_type: Optional[str] = None) -> List[dict]:
    """Get journal entries from the last N days.

    Returns a flat list of entries (newest first), optionally filtered
    by type.  Useful for morning orientation and self-reflection.
    """
    all_entries = []
    today = date.today()
    for offset in range(days):
        day = today - timedelta(days=offset)
        data = load_day(day)
        entries = data.get("entries", [])
        if entry_type:
            entries = [e for e in entries if e.get("type") == entry_type]
        # Tag with date for context
        for e in entries:
            e["_date"] = day.strftime(_DATE_FMT)
        all_entries.extend(entries)
    # Newest first
    all_entries.sort(key=lambda e: e.get("time", ""), reverse=True)
    return all_entries


def get_day_summary(day: Optional[date] = None) -> str:
    """Human-readable summary of a day's journal for orientation."""
    data = load_day(day)
    summary = data.get("summary", {})
    entries = data.get("entries", [])
    day_str = data.get("date", "today")

    if not entries:
        return f"No journal entries for {day_str}."

    parts = [f"Journal for {day_str}:"]
    tasks = summary.get("tasks_completed", 0)
    convs = summary.get("conversations", 0)
    obs = summary.get("observations", 0)
    learned = summary.get("things_learned", 0)

    counts = []
    if tasks:
        counts.append(f"{tasks} task{'s' if tasks != 1 else ''} completed")
    if convs:
        counts.append(f"{convs} conversation{'s' if convs != 1 else ''}")
    if obs:
        counts.append(f"{obs} observation{'s' if obs != 1 else ''}")
    if learned:
        counts.append(f"{learned} thing{'s' if learned != 1 else ''} learned")
    if counts:
        parts.append(", ".join(counts) + ".")

    # Include last few entries as context
    recent = entries[-5:]
    for e in recent:
        time_str = e.get("time", "?")[-8:-3]  # HH:MM
        parts.append(f"  [{time_str}] {e.get('type', '?')}: {e.get('content', '')[:120]}")

    return "\n".join(parts)


def get_orientation(days: int = 3) -> str:
    """Morning orientation — brief recap of recent days for context.

    Used when Archi starts a new day or recovers from being offline.
    Returns a compact multi-day summary.
    """
    today = date.today()
    parts = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        data = load_day(day)
        entries = data.get("entries", [])
        if not entries:
            continue
        summary = data.get("summary", {})
        label = "Today" if offset == 0 else ("Yesterday" if offset == 1 else day.strftime("%A %b %d"))
        counts = []
        for key, desc in [("tasks_completed", "tasks"), ("conversations", "convos"),
                          ("things_learned", "learned")]:
            val = summary.get(key, 0)
            if val:
                counts.append(f"{val} {desc}")
        count_str = f" ({', '.join(counts)})" if counts else ""
        parts.append(f"- {label}{count_str}: {len(entries)} journal entries")

        # Most recent notable entries
        notable = [e for e in entries if e.get("type") in
                   ("observation", "thing_learned", "reflection")][-3:]
        for e in notable:
            parts.append(f"  • {e.get('content', '')[:100]}")
    return "\n".join(parts) if parts else "No recent journal entries."


# ── Pruning ──────────────────────────────────────────────────────────

def prune_old_journals(retention_days: int = _RETENTION_DAYS) -> int:
    """Remove journal files older than retention_days.  Returns count removed."""
    journal_dir = _journal_dir()
    if not os.path.isdir(journal_dir):
        return 0
    cutoff = date.today() - timedelta(days=retention_days)
    removed = 0
    for fname in os.listdir(journal_dir):
        if not fname.endswith(".json"):
            continue
        try:
            file_date = datetime.strptime(fname.replace(".json", ""), _DATE_FMT).date()
            if file_date < cutoff:
                os.remove(os.path.join(journal_dir, fname))
                removed += 1
        except (ValueError, OSError):
            continue
    if removed:
        logger.info("Pruned %d old journal files (cutoff: %s)", removed, cutoff)
    return removed
