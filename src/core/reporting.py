"""
Reporting — Morning reports and hourly summaries for dream cycles.

Handles overnight result persistence, morning report compilation,
and hourly summary notifications via Discord.
Split from dream_cycle.py in session 11.
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


def send_finding_notification(
    goal_desc: str,
    finding_summary: str,
    files_created: List[str],
) -> bool:
    """Send a proactive Discord notification about an interesting finding.

    Rate-limited to at most 1 notification per 30 minutes to avoid spam.
    Only sends if the finding is substantive (non-empty summary).

    Args:
        goal_desc: Description of the goal that produced the finding.
        finding_summary: Conversational summary of the interesting finding.
        files_created: List of file paths created by the task.

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

    # Build a concise conversational message
    file_names = [os.path.basename(f) for f in files_created[:3]]
    file_note = ""
    if file_names:
        file_note = f"\n📄 Updated: {', '.join(file_names)}"

    msg = (
        f"💡 {finding_summary}"
        f"{file_note}"
    )

    _notify(msg)
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
                lines.append(f"  ✓ {desc} (complete)")
            else:
                total = len(goal.tasks)
                done = sum(1 for t in goal.tasks if t.status.value == "completed")
                pct = goal.completion_percentage
                if total > 0:
                    lines.append(f"  ⏳ {desc} ({pct:.0f}%, {done}/{total} tasks)")
                else:
                    lines.append(f"  ⏳ {desc} (queued)")
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
    # task result looks like "Done: Jesse, I researched X. Key findings..."
    findings = []
    for r in task_results:
        summary = r.get("summary", "")
        if "Done: " in summary:
            # Pull out the "Done:" portion (the model's completion summary)
            done_text = summary.split("Done: ", 1)[1].split(";")[0].strip()
            if len(done_text) > 20:
                findings.append(done_text)

    file_names = [os.path.basename(f) for f in files_created[:5]]
    file_note = ""
    if file_names:
        file_note = f"\n📄 Files: {', '.join(file_names)}"

    # Build the message: lead with findings if available
    if findings:
        # Use the longest/most detailed finding as the main summary
        best_finding = max(findings, key=len)
        # Cap at 300 chars for Discord readability
        if len(best_finding) > 300:
            best_finding = best_finding[:297] + "…"
        msg = (
            f"✅ **Done:** {goal_label}\n\n"
            f"{best_finding}"
            f"{file_note}"
        )
    else:
        msg = (
            f"✅ **Done:** {goal_label}"
            f"{file_note}"
        )

    _notify(msg)
    logger.info("User goal completion notification sent: %s", goal_description[:60])
    return True


def send_morning_report(
    overnight_results: List[Dict[str, Any]],
    overnight_results_path: Path,
) -> None:
    """Compile and send a summary of overnight work via Discord DM.

    Runs once per morning (6-9 AM). Collects all task results from
    the overnight session and formats them into a readable report.
    """
    if not overnight_results:
        logger.info("Morning report: nothing to report (no overnight work)")
        return

    logger.info("Compiling morning report (%d results)", len(overnight_results))

    lines = ["\U0001f305 **Good morning, Jesse! Here's what I worked on overnight:**\n"]

    # Lead with progress on user-requested goals
    _user_goal_lines = _get_user_goal_progress()
    if _user_goal_lines:
        lines.append("📋 **Your requests:**")
        lines.extend(_user_goal_lines)
        lines.append("")

    successes = [r for r in overnight_results if r.get("success")]
    failures = [r for r in overnight_results if not r.get("success")]
    total_cost = sum(r.get("cost", 0) for r in overnight_results)

    if successes:
        lines.append(f"\u2705 **Completed ({len(successes)}):**")
        for r in successes:
            verified_tag = " \u2714\ufe0f" if r.get("verified") else ""
            task_label = _humanize_task(r.get("task", ""))
            lines.append(f"  \u2022 {task_label}{verified_tag}")
            files = r.get("files_created", [])
            if files:
                filenames = [os.path.basename(f) for f in files[:3]]
                lines.append(f"    \U0001f4c4 {', '.join(filenames)}")

    if failures:
        lines.append(f"\n\u26a0\ufe0f **Needs attention ({len(failures)}):**")
        for r in failures:
            lines.append(f"  \u2022 {_humanize_task(r.get('task', ''))}")

    lines.append(f"\n\U0001f4b0 Cost: ${total_cost:.4f}")

    # Check idea backlog
    try:
        backlog_path = _base_path() / "data" / "idea_backlog.json"
        if backlog_path.exists():
            with open(backlog_path, "r", encoding="utf-8") as f:
                backlog = json.load(f)
            pending = [i for i in backlog.get("ideas", []) if i.get("status") == "pending"]
            if pending:
                lines.append(f"\n\U0001f4a1 **Ideas in backlog:** {len(pending)}")
                top3 = sorted(pending, key=lambda x: x.get("score", 0), reverse=True)[:3]
                for idea in top3:
                    cat = idea.get("category", "?")
                    desc = idea.get("description", "")[:80]
                    lines.append(f"  \u2022 [{cat}] {desc}")
    except Exception:
        pass

    report = "\n".join(lines)

    # Append one interesting finding if available
    try:
        from src.core.interesting_findings import get_findings_queue
        ifq = get_findings_queue()
        finding = ifq.get_next_undelivered()
        if finding:
            report += f"\n\n\U0001f4a1 **Something interesting:** {finding['summary']}"
            ifq.mark_delivered(finding["id"])
    except Exception:
        pass

    _notify(report)
    logger.info("Morning report sent (%d chars)", len(report))

    # Reset overnight results (memory + disk)
    overnight_results.clear()
    try:
        if overnight_results_path.exists():
            overnight_results_path.unlink()
    except Exception:
        pass


def send_hourly_summary(
    hourly_task_results: List[Dict[str, Any]],
) -> None:
    """Send a concise summary of accumulated dream-cycle work (hourly).

    Keeps the message short: headline count + top 3 notable items + files.
    """
    results = hourly_task_results
    if not results:
        return

    successes = [r for r in results if r.get("success")]
    failures = [r for r in results if not r.get("success")]

    # Collect all files
    all_files = []
    for r in results:
        for f in r.get("files_created", []):
            name = os.path.basename(f)
            if name not in all_files:
                all_files.append(name)

    lines = []
    lines.append(
        f"\U0001f4cb **Hourly update** — {len(successes)} completed"
        + (f", {len(failures)} failed" if failures else "")
    )

    # Lead with user-requested goal progress
    _user_goal_lines = _get_user_goal_progress()
    if _user_goal_lines:
        lines.append("\n📋 **Your requests:**")
        lines.extend(_user_goal_lines)

    # Lead with key findings from the interesting findings queue
    try:
        from src.core.interesting_findings import get_findings_queue
        ifq = get_findings_queue()
        _delivered_count = 0
        while _delivered_count < 3:
            finding = ifq.get_next_undelivered()
            if not finding:
                break
            if _delivered_count == 0:
                lines.append("\n\U0001f4a1 **Key findings:**")
            lines.append(f"  • {finding['summary']}")
            ifq.mark_delivered(finding["id"])
            _delivered_count += 1
    except Exception:
        pass

    # Show top 3 tasks (prioritize failures, then most recent successes)
    notable = failures[:2] + successes[-3:]
    for r in notable[:3]:
        icon = "\u2705" if r.get("success") else "\u274c"
        task_desc = _humanize_task(r.get("task", "Unknown task"))
        lines.append(f"  {icon} {task_desc}")

    remaining = len(results) - min(3, len(notable))
    if remaining > 0:
        lines.append(f"  + {remaining} other tasks")

    if all_files:
        file_list = ", ".join(all_files[:5])
        if len(all_files) > 5:
            file_list += f" +{len(all_files) - 5} more"
        lines.append(f"  \U0001f4c4 Files: {file_list}")

    _notify("\n".join(lines))
    logger.info("Hourly summary sent (%d tasks)", len(results))

    hourly_task_results.clear()
