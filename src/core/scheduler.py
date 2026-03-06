"""Scheduled task system — gives Archi time-awareness.

Session 196: initial implementation (DESIGN_SCHEDULED_TASKS.md).

Archi can create, modify, remove, and fire scheduled tasks. Tasks are
persisted in data/scheduled_tasks.json and checked every heartbeat tick.
Supports cron-based recurrence via ``croniter``, engagement tracking,
and adaptive retirement of ignored tasks.
"""

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Safety limits
MAX_TASKS = 50
MAX_FIRES_PER_HOUR = 10

_SCHEDULE_FILE = "data/scheduled_tasks.json"
_DT_FMT = "%Y-%m-%dT%H:%M:%S"

# Quiet hours: 11 PM – 6 AM (notify tasks deferred to morning)
_QUIET_START = 23
_QUIET_END = 6


# ── Data model ────────────────────────────────────────────────────────

@dataclass
class TaskStats:
    """Engagement statistics for a scheduled task."""
    times_fired: int = 0
    times_acknowledged: int = 0
    times_ignored: int = 0
    last_fired: Optional[str] = None
    last_acknowledged: Optional[str] = None


@dataclass
class ScheduledTask:
    """A single scheduled task entry."""
    id: str
    description: str
    cron: str
    next_run_at: str
    action: str = "notify"
    payload: Any = ""
    created_by: str = "user"
    enabled: bool = True
    on_miss: str = "skip"          # "skip" or "fire_once"
    created_at: str = ""
    stats: TaskStats = field(default_factory=TaskStats)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime(_DT_FMT)
        if isinstance(self.stats, dict):
            self.stats = TaskStats(**self.stats)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledTask":
        stats_raw = d.pop("stats", {})
        task = cls(**d)
        task.stats = TaskStats(**stats_raw) if isinstance(stats_raw, dict) else TaskStats()
        return task


# ── Persistence ───────────────────────────────────────────────────────

_lock = threading.Lock()


def _schedule_path() -> str:
    return str(_base_path() / _SCHEDULE_FILE)


def load_schedule() -> List[ScheduledTask]:
    """Load scheduled tasks from disk."""
    path = _schedule_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [ScheduledTask.from_dict(entry) for entry in data]
    except Exception as e:
        logger.error("Failed to load schedule: %s", e)
        return []


def save_schedule(tasks: List[ScheduledTask]) -> None:
    """Atomically write scheduled tasks to disk."""
    path = _schedule_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = [t.to_dict() for t in tasks]
    try:
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error("Failed to save schedule: %s", e)
        # Clean up temp file if replace failed
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Cron helpers ──────────────────────────────────────────────────────

def compute_next_run(cron_expr: str, after: Optional[datetime] = None) -> str:
    """Compute the next fire time from a cron expression.

    Returns ISO-format datetime string. Falls back to +1h on error.
    """
    after = after or datetime.now()
    try:
        from croniter import croniter
        cron = croniter(cron_expr, after)
        nxt = cron.get_next(datetime)
        return nxt.strftime(_DT_FMT)
    except Exception as e:
        logger.warning("Cron parse error for '%s': %s — falling back to +1h", cron_expr, e)
        return (after + timedelta(hours=1)).strftime(_DT_FMT)


def validate_cron(cron_expr: str) -> bool:
    """Check whether a cron expression is valid."""
    try:
        from croniter import croniter
        return croniter.is_valid(cron_expr)
    except Exception:
        return False


# ── Core operations ───────────────────────────────────────────────────

def check_due_tasks(now: Optional[datetime] = None) -> List[ScheduledTask]:
    """Return enabled tasks whose next_run_at <= now."""
    now = now or datetime.now()
    now_str = now.strftime(_DT_FMT)
    tasks = load_schedule()
    due = []
    for t in tasks:
        if not t.enabled:
            continue
        if t.next_run_at <= now_str:
            due.append(t)
    return due


def create_task(
    task_id: str,
    description: str,
    cron_expr: str,
    action: str = "notify",
    payload: Any = "",
    created_by: str = "user",
    on_miss: str = "skip",
    enabled: bool = True,
) -> Optional[ScheduledTask]:
    """Create a new scheduled task. Returns the task, or None on failure."""
    with _lock:
        tasks = load_schedule()
        if len(tasks) >= MAX_TASKS:
            logger.warning("Schedule full (%d tasks) — refusing create", len(tasks))
            return None
        # Deduplicate by id
        if any(t.id == task_id for t in tasks):
            logger.warning("Task '%s' already exists", task_id)
            return None
        if not validate_cron(cron_expr):
            logger.warning("Invalid cron expression: '%s'", cron_expr)
            return None

        next_run = compute_next_run(cron_expr)
        task = ScheduledTask(
            id=task_id,
            description=description,
            cron=cron_expr,
            next_run_at=next_run,
            action=action,
            payload=payload,
            created_by=created_by,
            on_miss=on_miss,
            enabled=enabled,
        )
        tasks.append(task)
        save_schedule(tasks)
        logger.info("Created scheduled task '%s' (next: %s)", task_id, next_run)
        return task


def modify_task(task_id: str, **updates) -> Optional[ScheduledTask]:
    """Modify an existing task. Returns the updated task, or None."""
    with _lock:
        tasks = load_schedule()
        target = next((t for t in tasks if t.id == task_id), None)
        if not target:
            return None

        for key, val in updates.items():
            if key == "cron":
                if not validate_cron(val):
                    logger.warning("Invalid cron for modify: '%s'", val)
                    return None
                target.cron = val
                target.next_run_at = compute_next_run(val)
            elif key == "stats":
                continue  # Don't allow direct stats overwrite via modify
            elif hasattr(target, key):
                setattr(target, key, val)

        save_schedule(tasks)
        logger.info("Modified scheduled task '%s': %s", task_id, list(updates.keys()))
        return target


def remove_task(task_id: str) -> bool:
    """Remove a task by id. Returns True if found and removed."""
    with _lock:
        tasks = load_schedule()
        before = len(tasks)
        tasks = [t for t in tasks if t.id != task_id]
        if len(tasks) == before:
            return False
        save_schedule(tasks)
        logger.info("Removed scheduled task '%s'", task_id)
        return True


def list_tasks(include_disabled: bool = True) -> List[ScheduledTask]:
    """List all scheduled tasks."""
    tasks = load_schedule()
    if not include_disabled:
        tasks = [t for t in tasks if t.enabled]
    return tasks


def get_task(task_id: str) -> Optional[ScheduledTask]:
    """Get a single task by id."""
    tasks = load_schedule()
    return next((t for t in tasks if t.id == task_id), None)


# ── Firing / advancement ─────────────────────────────────────────────

def advance_task(task_id: str) -> None:
    """After firing, update next_run_at and increment stats.times_fired."""
    with _lock:
        tasks = load_schedule()
        target = next((t for t in tasks if t.id == task_id), None)
        if not target:
            return
        now = datetime.now()
        target.stats.times_fired += 1
        target.stats.last_fired = now.strftime(_DT_FMT)
        target.next_run_at = compute_next_run(target.cron, after=now)
        save_schedule(tasks)


def is_quiet_hours(now: Optional[datetime] = None) -> bool:
    """Check if current time is in quiet hours (11 PM – 6 AM)."""
    hour = (now or datetime.now()).hour
    return hour >= _QUIET_START or hour < _QUIET_END


def check_fire_rate(tasks: List[ScheduledTask]) -> bool:
    """Return True if we're under the hourly fire rate limit."""
    one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime(_DT_FMT)
    recent_fires = sum(
        1 for t in tasks
        if t.stats.last_fired and t.stats.last_fired >= one_hour_ago
    )
    return recent_fires < MAX_FIRES_PER_HOUR


# ── Engagement tracking ──────────────────────────────────────────────

def record_engagement(task_id: str, acknowledged: bool) -> None:
    """Record whether a fired task was acknowledged or ignored."""
    with _lock:
        tasks = load_schedule()
        target = next((t for t in tasks if t.id == task_id), None)
        if not target:
            return
        if acknowledged:
            target.stats.times_acknowledged += 1
            target.stats.last_acknowledged = datetime.now().strftime(_DT_FMT)
        else:
            target.stats.times_ignored += 1
        save_schedule(tasks)


def get_ignored_tasks(
    threshold_days: int = 14,
    ignore_rate: float = 0.7,
    min_fires: int = 5,
) -> List[ScheduledTask]:
    """Find tasks that have been consistently ignored.

    Returns tasks where:
    - Created more than threshold_days ago
    - Fired at least min_fires times
    - Ignore rate exceeds the threshold
    """
    tasks = load_schedule()
    cutoff = (datetime.now() - timedelta(days=threshold_days)).strftime(_DT_FMT)
    ignored = []
    for t in tasks:
        if not t.enabled:
            continue
        if t.created_at > cutoff:
            continue
        total = t.stats.times_acknowledged + t.stats.times_ignored
        if total < min_fires:
            continue
        rate = t.stats.times_ignored / total if total > 0 else 0
        if rate >= ignore_rate:
            ignored.append(t)
    return ignored


# ── Formatting helpers ────────────────────────────────────────────────

def format_task_list(tasks: List[ScheduledTask]) -> str:
    """Format a list of tasks for conversational display."""
    if not tasks:
        return "No scheduled tasks."
    lines = []
    for t in tasks:
        status = "enabled" if t.enabled else "disabled"
        total = t.stats.times_fired
        ack_rate = ""
        if total > 0:
            rate = t.stats.times_acknowledged / total * 100
            ack_rate = f" ({rate:.0f}% ack)"
        lines.append(
            f"- **{t.id}**: {t.description} [{t.cron}] — {status}, "
            f"fired {total}x{ack_rate}, next: {t.next_run_at}"
        )
    return "\n".join(lines)


def slugify(text: str) -> str:
    """Convert text to a kebab-case task id."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60] or "task"
