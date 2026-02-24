"""Time-awareness utilities for Archi.

Reads timezone and working_hours from config/archi_identity.yaml and
provides helpers to determine whether the user is likely awake, whether
it's quiet hours, etc.  Used by ask_user, heartbeat, and initiative
systems.

Activity override (session 101): if the user sent a message within
activity_override_minutes, quiet hours are suppressed — Archi stays
responsive while the user is actively chatting.

Created in session 36 (companion personality overhaul).
Updated in session 101 (activity-based quiet hours override).
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ── Cached config ────────────────────────────────────────────────
_tz_name: str = "America/Chicago"
_work_start: int = 6    # hour (24h) — quiet hours end
_work_end: int = 23     # hour (24h) — quiet hours begin
_activity_override_minutes: int = 30
_loaded: bool = False

# ── Activity tracking ────────────────────────────────────────────
_last_user_activity: Optional[datetime] = None
_activity_lock = threading.Lock()


def _load_config() -> None:
    """Load timezone and working hours from archi_identity.yaml (once)."""
    global _tz_name, _work_start, _work_end, _activity_override_minutes, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from src.utils.paths import project_root
        path = project_root() / "config" / "archi_identity.yaml"
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        uc = cfg.get("user_context", {})
        _tz_name = uc.get("timezone", _tz_name)
        wh = uc.get("working_hours", "6 AM - 11 PM")
        _work_start, _work_end = _parse_working_hours(wh)
        _activity_override_minutes = int(
            uc.get("activity_override_minutes", 30)
        )
    except Exception as e:
        logger.warning("time_awareness: failed to load config, using defaults: %s", e)


def _parse_working_hours(wh: str) -> Tuple[int, int]:
    """Parse '6 AM - 11 PM' → (6, 23).  Returns defaults on failure."""
    try:
        parts = wh.split("-")
        if len(parts) != 2:
            return 6, 23
        start_str = parts[0].strip()
        end_str = parts[1].strip()
        start = _parse_hour(start_str)
        end = _parse_hour(end_str)
        return start, end
    except Exception:
        return 6, 23


def _parse_hour(s: str) -> int:
    """Parse '9 AM' or '11 PM' → 24h integer."""
    s = s.upper().strip()
    is_pm = "PM" in s
    is_am = "AM" in s
    num = int(s.replace("AM", "").replace("PM", "").strip())
    if is_pm and num != 12:
        num += 12
    elif is_am and num == 12:
        num = 0
    return num


# ── Timezone helper ──────────────────────────────────────────────

def _now_in_user_tz() -> datetime:
    """Return current datetime in the user's configured timezone."""
    _load_config()
    try:
        # Python 3.9+ zoneinfo
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(_tz_name))
    except Exception:
        # Fallback: try pytz
        try:
            import pytz
            return datetime.now(pytz.timezone(_tz_name))
        except Exception:
            # Last resort: assume UTC-6 (Chicago standard)
            return datetime.now(timezone(timedelta(hours=-6)))


# ── Activity tracking — public API ───────────────────────────────

def record_user_activity() -> None:
    """Record that the user just sent a message.

    Called from discord_bot on every inbound message.  While there's
    been activity within activity_override_minutes, quiet hours are
    suppressed so Archi stays responsive during a conversation.
    """
    global _last_user_activity
    with _activity_lock:
        _last_user_activity = _now_in_user_tz()


def _is_user_recently_active() -> bool:
    """True if the user messaged within the activity override window."""
    _load_config()
    with _activity_lock:
        if _last_user_activity is None:
            return False
        now = _now_in_user_tz()
        # Compare naive datetimes if tzinfo doesn't match
        try:
            delta = now - _last_user_activity
        except TypeError:
            # Timezone-aware vs naive mismatch — strip tzinfo for comparison
            delta = now.replace(tzinfo=None) - _last_user_activity.replace(tzinfo=None)
        return delta < timedelta(minutes=_activity_override_minutes)


# ── Public API ───────────────────────────────────────────────────

def is_quiet_hours() -> bool:
    """True if it's outside working hours AND the user hasn't been active recently.

    Activity override: if the user sent a message within
    activity_override_minutes, this returns False regardless of the
    clock — Archi stays responsive during an active conversation.
    """
    _load_config()

    # Activity override: user is chatting → never quiet
    if _is_user_recently_active():
        return False

    hour = _now_in_user_tz().hour
    if _work_start <= _work_end:
        # Normal range, e.g. 6–23
        return hour < _work_start or hour >= _work_end
    else:
        # Wraps midnight, e.g. 22–8
        return hour >= _work_end and hour < _work_start


def is_user_awake() -> bool:
    """True if within working hours or recently active."""
    return not is_quiet_hours()


def time_until_awake() -> timedelta:
    """How long until the start of the next working-hours window.

    Returns timedelta(0) if user is currently awake.
    """
    if is_user_awake():
        return timedelta(0)
    now = _now_in_user_tz()
    hour = now.hour
    # Calculate hours until _work_start
    if hour < _work_start:
        delta_hours = _work_start - hour
    else:
        # Past work_end, need to wait until next day's work_start
        delta_hours = (24 - hour) + _work_start
    # Rough: ignore minutes for simplicity, round up
    wake_time = now.replace(hour=_work_start, minute=0, second=0, microsecond=0)
    if wake_time <= now:
        wake_time += timedelta(days=1)
    return wake_time - now


def get_user_hour() -> int:
    """Return the current hour (0-23) in the user's timezone."""
    _load_config()
    return _now_in_user_tz().hour


def _reset_for_testing() -> None:
    """Reset module state for test isolation."""
    global _loaded, _last_user_activity
    global _tz_name, _work_start, _work_end, _activity_override_minutes
    _loaded = False
    _last_user_activity = None
    _tz_name = "America/Chicago"
    _work_start = 6
    _work_end = 23
    _activity_override_minutes = 30
