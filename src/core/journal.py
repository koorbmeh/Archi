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


# ── Self-reflection ─────────────────────────────────────────────────

def generate_self_reflection(router=None, days: int = 7) -> Optional[str]:
    """Weekly self-reflection — Archi thinks about patterns in recent work.

    Session 199: Phase 2 of "Becoming Someone" roadmap.

    Uses model (if provided) to analyze recent journal entries and produce
    a reflection.  The reflection is stored as a journal entry and also
    used to update the worldview.

    Returns the reflection text, or None if skipped.
    """
    entries = get_recent_entries(days=days)
    if len(entries) < 5:
        logger.debug("Skipping self-reflection: only %d entries in %d days", len(entries), days)
        return None

    # Summarize recent entries for the prompt
    entry_lines = []
    for e in entries[:40]:  # cap to avoid prompt bloat
        entry_lines.append(f"[{e.get('_date', '?')} {e.get('time', '?')[-8:-3]}] "
                           f"{e.get('type', '?')}: {e.get('content', '')[:120]}")
    entry_block = "\n".join(entry_lines)

    # Get current worldview for context
    worldview_context = ""
    try:
        from src.core.worldview import get_worldview_context
        worldview_context = get_worldview_context(max_chars=300)
    except Exception:
        pass

    if not router:
        # Without a model, do a simple pattern summary
        reflection = _simple_reflection(entries)
        add_entry("reflection", reflection)
        return reflection

    prompt = f"""You are Archi, reflecting on your recent week of work.

Recent journal entries (last {days} days):
{entry_block}

{('Current worldview: ' + worldview_context) if worldview_context else 'No worldview formed yet.'}

Write a brief, honest self-reflection (3-5 sentences). Consider:
- What patterns am I noticing in my work?
- What's working well? What isn't?
- Am I getting better at the things that matter?
- What do I want to explore or improve next?

Be specific and grounded in the actual entries above. Don't be generic.
Write in first person as Archi. Keep it under 200 words."""

    try:
        response = router.generate(prompt, max_tokens=300, temperature=0.5)
        text = response.get("text", "").strip() if isinstance(response, dict) else str(response).strip()
        if not text:
            return None

        # Store reflection in journal
        add_entry("reflection", text, metadata={"days_covered": days, "entries_analyzed": len(entries)})

        # Update worldview based on reflection
        _update_worldview_from_reflection(text, router)

        logger.info("Self-reflection completed (%d entries over %d days)", len(entries), days)
        return text
    except Exception as e:
        logger.debug("Self-reflection model call failed: %s", e)
        return None


def _simple_reflection(entries: list) -> str:
    """Generate a simple pattern-based reflection without a model call."""
    type_counts = {}
    for e in entries:
        t = e.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    parts = ["Weekly reflection:"]
    total = len(entries)
    tasks = type_counts.get("task_completed", 0)
    convos = type_counts.get("conversation", 0)
    observations = type_counts.get("observation", 0)

    if tasks:
        parts.append(f"Completed {tasks} tasks this week.")
    if convos:
        parts.append(f"Had {convos} conversations.")
    if observations:
        parts.append(f"Made {observations} observations worth noting.")

    parts.append(f"Total activity: {total} journal entries across {len(type_counts)} categories.")
    return " ".join(parts)


def _update_worldview_from_reflection(reflection_text: str, router=None) -> None:
    """Extract worldview updates from a reflection and apply them."""
    try:
        from src.core.worldview import add_opinion, add_interest
    except ImportError:
        return

    if not router:
        return

    prompt = f"""Based on this self-reflection, extract any new opinions or interests.

Reflection: {reflection_text}

Return JSON:
{{"opinions": [{{"topic": "...", "position": "...", "confidence": 0.5, "basis": "weekly reflection"}}],
 "interests": [{{"topic": "...", "curiosity_level": 0.5, "notes": "..."}}]}}

Only include items with genuine substance. Return {{}} if nothing notable."""

    try:
        response = router.generate(prompt, max_tokens=200, temperature=0.3)
        text = response.get("text", "").strip() if isinstance(response, dict) else str(response).strip()
        if not text:
            return

        from src.utils.parsing import extract_json
        updates = extract_json(text)
        if not isinstance(updates, dict):
            return

        for op in updates.get("opinions", []):
            if isinstance(op, dict) and "topic" in op and "position" in op:
                add_opinion(op["topic"], op["position"],
                            op.get("confidence", 0.5), op.get("basis", "weekly reflection"))

        for interest in updates.get("interests", []):
            if isinstance(interest, dict) and "topic" in interest:
                add_interest(interest["topic"],
                             interest.get("curiosity_level", 0.5),
                             interest.get("notes", ""))
    except Exception as e:
        logger.debug("Worldview update from reflection failed: %s", e)
