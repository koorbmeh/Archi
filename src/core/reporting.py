"""
Reporting — Morning reports and hourly summaries for heartbeat cycles.

Handles overnight result persistence, morning report compilation,
and hourly summary notifications via Discord.
Split from dream_cycle.py (now heartbeat.py) in session 11.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Max display length for task/goal descriptions in notifications
_MAX_TASK_LEN = 60
_MAX_GOAL_LEN = 80


def _humanize_task(raw: str) -> str:
    """Turn a raw PlanExecutor task description into a short human-readable line.

    Examples:
        "web_search('2024 studies optimal supplements...')" → "Researched optimal supplements"
        "create_file('workspace/.../diet.md', ...)" → "Created diet.md"
        "list_files('workspace/...'); read_file(...)" → "Reviewed project files"
    """
    if not raw:
        return "Background task"

    # If it already looks human-readable (no parens/quotes in first 40 chars), just truncate
    head = raw[:40]
    if "(" not in head and "'" not in head:
        return raw[:_MAX_TASK_LEN] + ("…" if len(raw) > _MAX_TASK_LEN else "")

    # Extract the dominant action from compound task strings
    parts = raw.split(";")
    actions = []
    for part in parts:
        p = part.strip()
        if p.startswith("web_search("):
            # Pull out the query topic
            topic = p.split("'", 2)[1] if "'" in p else ""
            topic = " ".join(topic.split()[:5])  # first 5 words
            actions.append(f"Researched {topic}")
        elif p.startswith("create_file("):
            fname = os.path.basename(p.split("'", 2)[1]) if "'" in p else "file"
            actions.append(f"Created {fname}")
        elif p.startswith("append_file("):
            fname = os.path.basename(p.split("'", 2)[1]) if "'" in p else "file"
            actions.append(f"Updated {fname}")
        elif p.startswith("write_source("):
            fname = os.path.basename(p.split("'", 2)[1]) if "'" in p else "file"
            actions.append(f"Wrote {fname}")
        elif p.startswith("read_file(") or p.startswith("list_files("):
            actions.append("Reviewed project files")
        elif p.startswith("fetch_webpage("):
            actions.append("Fetched web content")
        elif p.startswith("edit_file("):
            fname = os.path.basename(p.split("'", 2)[1]) if "'" in p else "file"
            actions.append(f"Edited {fname}")
        else:
            actions.append(p[:_MAX_TASK_LEN])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for a in actions:
        if a not in seen:
            seen.add(a)
            unique.append(a)

    summary = "; ".join(unique[:3])
    if len(summary) > _MAX_TASK_LEN:
        summary = summary[:_MAX_TASK_LEN - 1] + "…"
    return summary or "Background task"


def load_overnight_results(path: Path) -> List[Dict[str, Any]]:
    """Restore overnight results from disk (survives restarts)."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                logger.info("Loaded %d overnight results from disk", len(data))
                return data
    except Exception as e:
        logger.debug("Could not load overnight results: %s", e)
    return []


def save_overnight_results(results: List[Dict[str, Any]], path: Path) -> None:
    """Persist overnight results to disk."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
    except Exception as e:
        logger.debug("Could not save overnight results: %s", e)


# Rate-limit proactive finding notifications: max 1 per 30 minutes
_FINDING_NOTIFY_COOLDOWN = 1800  # seconds
_last_finding_notify: float = 0.0


def _notify(text: str) -> None:
    """Send a Discord DM notification (best-effort, never raises)."""
    try:
        from src.interfaces.discord_bot import send_notification
        send_notification(text)
    except Exception as e:
        logger.debug("Discord notification skipped: %s", e)


def _pop_next_finding() -> Optional[str]:
    """Retrieve and mark-delivered the next undelivered interesting finding.

    Returns the finding summary string, or None if no findings available.
    """
    try:
        from src.core.interesting_findings import get_findings_queue
        ifq = get_findings_queue()
        finding = ifq.get_next_undelivered()
        if finding:
            ifq.mark_delivered(finding["id"])
            return finding["summary"]
    except Exception as e:
        logger.debug("Could not retrieve finding: %s", e)
    return None


def send_finding_notification(
    goal_desc: str,
    finding_summary: str,
    files_created: List[str],
    router: Any = None,
) -> bool:
    """Send a proactive Discord notification about an interesting finding.

    Uses the Notification Formatter (Phase 3) for natural messages.
    Rate-limited to at most 1 notification per 30 minutes to avoid spam.

    Args:
        goal_desc: Description of the goal that produced the finding.
        finding_summary: Conversational summary of the interesting finding.
        files_created: List of file paths created by the task.
        router: Model router for the Formatter call. Optional.

    Returns:
        True if notification was sent, False if skipped (cooldown, empty, etc.).
    """
    global _last_finding_notify

    if not finding_summary or len(finding_summary.strip()) < 15:
        return False

    now = time.time()
    if now - _last_finding_notify < _FINDING_NOTIFY_COOLDOWN:
        logger.debug("Finding notification skipped (cooldown): %s", finding_summary[:60])
        return False

    from src.core.notification_formatter import format_finding
    fmt = format_finding(
        goal_description=goal_desc,
        finding_summary=finding_summary,
        files_created=files_created,
        router=router,
    )

    _notify(fmt["message"])
    _last_finding_notify = now
    logger.info("Proactive finding notification sent: %s", finding_summary[:60])
    return True


def _get_user_goal_progress() -> List[str]:
    """Get progress lines for user-requested goals (deferred requests + /goal).

    Returns formatted lines like:
      ⏳ Look into protein powder brands (50%, 2/4 tasks)
      ✓ Check server logs (complete)

    Only includes goals with user_intent starting with "User ".
    Returns empty list if no user goals exist.
    """
    try:
        from src.core.goal_manager import GoalManager
        gm = GoalManager()
        lines = []
        for goal in gm.goals.values():
            _intent = (goal.user_intent or "").lower()
            if not _intent.startswith("user "):
                continue
            desc = goal.description[:70]
            if goal.is_complete():
                lines.append(f"- {desc} (done)")
            else:
                total = len(goal.tasks)
                done = sum(1 for t in goal.tasks if t.status.value == "completed")
                pct = goal.completion_percentage
                if total > 0:
                    lines.append(f"- {desc} ({pct:.0f}%, {done}/{total} tasks)")
                else:
                    lines.append(f"- {desc} (queued)")
        return lines
    except Exception as e:
        logger.debug("User goal progress lookup failed: %s", e)
        return []


def send_user_goal_completion(
    goal_description: str,
    task_results: List[Dict[str, Any]],
    files_created: List[str],
) -> bool:
    """Send a proactive Discord notification when a user-requested goal completes.

    Unlike regular goal completion notifications (which just say "Goal complete"),
    this sends a richer message with a summary of findings and files — because the
    user specifically asked for this work and is waiting for a follow-up.

    Args:
        goal_description: The goal's description (what the user asked for).
        task_results: List of task result dicts from overnight_results.
        files_created: All files created across the goal's tasks.

    Returns:
        True if notification was sent.
    """
    # Short goal label (first sentence or truncated)
    goal_label = goal_description.split(".")[0].split(":")[0].strip()
    if len(goal_label) > _MAX_GOAL_LEN:
        goal_label = goal_label[:_MAX_GOAL_LEN - 1] + "…"

    # Extract the PlanExecutor's "done" summary — this contains the actual
    # answer/findings the user is waiting for.  The summary field in each
    # task result looks like "Done: [user name], I researched X. Key findings..."
    findings = []
    for r in task_results:
        summary = r.get("summary", "")
        if "Done: " in summary:
            # Pull out the "Done:" portion (the model's completion summary)
            done_text = summary.split("Done: ", 1)[1].split(";")[0].strip()
            if len(done_text) > 20:
                findings.append(done_text)

    if findings:
        # Show up to 3 task summaries — each explains what was built
        trimmed = []
        for f in findings[:3]:
            trimmed.append(f[:200] + "…" if len(f) > 200 else f)
        msg = f"Done with {goal_label}.\n" + "\n".join(trimmed)
    else:
        file_names = [os.path.basename(f) for f in files_created[:5]]
        file_note = f"\nFiles: {', '.join(file_names)}" if file_names else ""
        msg = f"Done with {goal_label}.{file_note}"

    _notify(msg)
    logger.info("User goal completion notification sent: %s", goal_description[:60])
    return True


def send_morning_report(
    overnight_results: List[Dict[str, Any]],
    overnight_results_path: Path,
    router: Any = None,
) -> None:
    """Compile and send a summary of overnight work via Discord DM.

    Runs once per morning (6-9 AM). Uses the Notification Formatter (Phase 3)
    for natural, varied messages. Falls back to deterministic formatting
    if the model call fails.

    Args:
        overnight_results: Task result dicts accumulated overnight.
        overnight_results_path: Path to the overnight results JSON file.
        router: Model router for the Formatter call. Optional — if None,
            uses deterministic fallback formatting.
    """
    if not overnight_results:
        logger.info("Morning report: nothing to report (no overnight work)")
        return

    logger.info("Compiling morning report (%d results)", len(overnight_results))

    successes = [r for r in overnight_results if r.get("success")]
    failures = [r for r in overnight_results if not r.get("success")]
    total_cost = sum(r.get("cost", 0) for r in overnight_results)

    # User goal progress
    _user_goal_lines = _get_user_goal_progress()

    finding_summary = _pop_next_finding()

    from src.core.notification_formatter import format_morning_report
    fmt = format_morning_report(
        successes=successes,
        failures=failures,
        total_cost=total_cost,
        user_goal_lines=_user_goal_lines,
        finding_summary=finding_summary,
        router=router,
    )

    _notify(fmt["message"])
    logger.info("Morning report sent (%d chars)", len(fmt["message"]))

    # Reset overnight results (memory + disk)
    overnight_results.clear()
    try:
        if overnight_results_path.exists():
            overnight_results_path.unlink()
    except Exception as e:
        logger.debug("Could not delete overnight results file: %s", e)


def send_hourly_summary(
    hourly_task_results: List[Dict[str, Any]],
    router: Any = None,
) -> None:
    """Send a concise summary of accumulated dream-cycle work (hourly).

    Uses the Notification Formatter (Phase 3) for natural messages.

    Args:
        hourly_task_results: Task result dicts accumulated since last summary.
        router: Model router for the Formatter call. Optional.
    """
    results = hourly_task_results
    if not results:
        return

    successes = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]

    # Collect files
    all_files = []
    for r in results:
        for f in r.get("files_created", []):
            name = os.path.basename(f)
            if name not in all_files:
                all_files.append(name)

    # User goal progress
    _user_goal_lines = _get_user_goal_progress()

    finding_summary = _pop_next_finding()

    from src.core.notification_formatter import format_hourly_summary
    fmt = format_hourly_summary(
        successes=successes,
        failures=failures,
        files_created=all_files,
        user_goal_lines=_user_goal_lines,
        finding_summary=finding_summary,
        router=router,
    )

    _notify(fmt["message"])
    logger.info("Hourly summary sent (%d tasks)", len(results))

    hourly_task_results.clear()
