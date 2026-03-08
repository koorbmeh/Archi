"""Content Calendar — plans, queues, and schedules content across platforms.

Session 240: Content Strategy Phase 4.

Uses brand config pillars for topic rotation.  Auto-publish via heartbeat
integration (publish_due()).  Discord commands: "content plan", "schedule a
post", "what's coming up".

Persistence: data/content_calendar.json — queue of ContentSlot entries.
"""

import json
import logging
import os
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_CALENDAR_PATH = os.path.join(base_path(), "data", "content_calendar.json")
_MAX_QUEUE_SIZE = 100   # Hard cap on queued items
_MIN_QUEUE_DAYS = 3     # Auto-generate when queue thinner than this
_lock = threading.Lock()

# Platform-specific cadence defaults (posts per week)
_DEFAULT_CADENCE = {
    "twitter":   {"per_week": 14, "formats": ["tweet", "tweet_thread"]},
    "instagram": {"per_week": 7,  "formats": ["instagram_post"]},
    "blog":      {"per_week": 3,  "formats": ["blog"]},
    "youtube":   {"per_week": 2,  "formats": ["video_script"]},
    "facebook":  {"per_week": 5,  "formats": ["facebook_post"]},
    "reddit":    {"per_week": 2,  "formats": ["reddit"]},
}

# Best posting times (hour in CST/local) per platform
_BEST_TIMES = {
    "twitter":   [8, 12, 18],
    "instagram": [7, 12, 19],
    "blog":      [9],
    "youtube":   [10],
    "facebook":  [8, 17],
    "reddit":    [10, 14],
}


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class ContentSlot:
    """A scheduled content item."""
    slot_id: str = ""
    pillar: str = ""           # ai_tech, finance, health_fitness, etc.
    platform: str = ""         # twitter, instagram, blog, etc.
    content_format: str = ""   # tweet, blog, video_script, etc.
    topic: str = ""            # Topic/title for generation
    status: str = "planned"    # planned / generated / published / failed
    scheduled_at: str = ""     # ISO datetime for publish
    generated_content: str = ""  # Stored generated text (for review)
    publish_result: str = ""   # URL or error message after publish
    image_path: str = ""       # Local path to companion image (if generated)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "pillar": self.pillar,
            "platform": self.platform,
            "content_format": self.content_format,
            "topic": self.topic,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "generated_content": self.generated_content,
            "publish_result": self.publish_result,
            "image_path": self.image_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ContentSlot":
        return cls(
            slot_id=d.get("slot_id", ""),
            pillar=d.get("pillar", ""),
            platform=d.get("platform", ""),
            content_format=d.get("content_format", ""),
            topic=d.get("topic", ""),
            status=d.get("status", "planned"),
            scheduled_at=d.get("scheduled_at", ""),
            generated_content=d.get("generated_content", ""),
            publish_result=d.get("publish_result", ""),
            image_path=d.get("image_path", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    @property
    def is_due(self) -> bool:
        """True if scheduled time has passed and content is ready."""
        if self.status != "generated":
            return False
        if not self.scheduled_at:
            return False
        try:
            sched = datetime.fromisoformat(self.scheduled_at)
            return datetime.now() >= sched
        except ValueError:
            return False


# ── Persistence ──────────────────────────────────────────────────────

def _load_calendar() -> Dict[str, Any]:
    """Load calendar data from disk."""
    if not os.path.isfile(_CALENDAR_PATH):
        return {"slots": [], "last_plan_date": ""}
    try:
        with open(_CALENDAR_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load content calendar: %s", e)
        return {"slots": [], "last_plan_date": ""}


def _save_calendar(data: Dict[str, Any]) -> None:
    """Atomically write calendar data to disk."""
    os.makedirs(os.path.dirname(_CALENDAR_PATH), exist_ok=True)
    # Trim old published/failed slots beyond limit
    slots = data.get("slots", [])
    active = [s for s in slots if s.get("status") in ("planned", "generated")]
    done = [s for s in slots if s.get("status") not in ("planned", "generated")]
    keep_done = done[-20:] if len(done) > 20 else done
    data["slots"] = active + keep_done
    tmp = _CALENDAR_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _CALENDAR_PATH)


# ── Helpers ──────────────────────────────────────────────────────────

def _generate_slot_id() -> str:
    """Generate a short unique ID for a content slot."""
    now = datetime.now()
    return f"cs_{now.strftime('%Y%m%d_%H%M%S')}_{random.randint(100, 999)}"


def _get_pillars() -> List[Dict[str, Any]]:
    """Load topic pillars from brand config."""
    try:
        from src.utils.config import get_brand_config
        brand = get_brand_config()
        return brand.get("topic_pillars", [])
    except Exception:
        return []


def _pick_pillar(recent_pillars: List[str], pillars: List[Dict]) -> Dict:
    """Pick a pillar ensuring diversity — avoid repeating the last 2."""
    if not pillars:
        return {"id": "general", "name": "General", "angles": [], "platforms": []}
    # Filter out recently used pillars
    available = [p for p in pillars if p.get("id") not in recent_pillars[-2:]]
    if not available:
        available = pillars
    return random.choice(available)


def _pick_topic_from_pillar(pillar: Dict) -> str:
    """Generate a topic hint from a pillar's angles."""
    angles = pillar.get("angles", [])
    if angles:
        angle = random.choice(angles)
        return f"{pillar.get('name', 'general')}: {angle}"
    return pillar.get("name", "general topic")


def _schedule_time(base_date: datetime, platform: str, slot_index: int) -> datetime:
    """Pick a time for a given platform on a given date."""
    times = _BEST_TIMES.get(platform, [10])
    hour = times[slot_index % len(times)]
    # Add small random jitter (0-15 min) to avoid exact-hour posts
    minute = random.randint(0, 15)
    return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ── Core class ───────────────────────────────────────────────────────

class ContentCalendar:
    """Plan, queue, and schedule content across platforms.

    Uses brand config pillars for topic rotation, respects platform cadence,
    and ensures format variety.
    """

    def plan_week(self, start_date: Optional[datetime] = None) -> List[ContentSlot]:
        """Generate a week's content plan.

        Distributes posts across platforms per cadence config, rotates pillars
        for topic diversity, picks appropriate formats.

        Args:
            start_date: When the planned week begins (default: tomorrow).

        Returns:
            List of ContentSlot entries (status="planned").
        """
        start = start_date or (datetime.now() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        pillars = _get_pillars()
        recent_pillars: List[str] = []
        new_slots: List[ContentSlot] = []

        for day_offset in range(7):
            day = start + timedelta(days=day_offset)

            for platform, cadence in _DEFAULT_CADENCE.items():
                per_week = cadence["per_week"]
                formats = cadence["formats"]

                # Distribute posts across the week
                # e.g., 14/week = 2/day, 3/week = ~every other day
                posts_today = per_week / 7.0
                # Deterministic rounding: post on this day if accumulated >= 1
                accumulated = posts_today * (day_offset + 1)
                previous = posts_today * day_offset
                slots_today = int(accumulated) - int(previous)

                for slot_idx in range(slots_today):
                    pillar = _pick_pillar(recent_pillars, pillars)
                    recent_pillars.append(pillar.get("id", ""))
                    fmt = formats[slot_idx % len(formats)]
                    topic = _pick_topic_from_pillar(pillar)
                    sched = _schedule_time(day, platform, slot_idx)

                    slot = ContentSlot(
                        slot_id=_generate_slot_id(),
                        pillar=pillar.get("id", "general"),
                        platform=platform,
                        content_format=fmt,
                        topic=topic,
                        status="planned",
                        scheduled_at=sched.isoformat(),
                        created_at=datetime.now().isoformat(),
                        updated_at=datetime.now().isoformat(),
                    )
                    new_slots.append(slot)

        # Persist
        with _lock:
            data = _load_calendar()
            data["last_plan_date"] = start.isoformat()
            for s in new_slots:
                data["slots"].append(s.to_dict())
            # Enforce max queue size
            active = [s for s in data["slots"]
                      if s.get("status") in ("planned", "generated")]
            if len(active) > _MAX_QUEUE_SIZE:
                # Keep newest
                active.sort(key=lambda s: s.get("scheduled_at", ""))
                data["slots"] = active[-_MAX_QUEUE_SIZE:]
            _save_calendar(data)

        logger.info("Content calendar: planned %d slots for week starting %s",
                     len(new_slots), start.strftime("%Y-%m-%d"))
        return new_slots

    def queue_content(
        self,
        topic: str,
        platform: str,
        content_format: str = "",
        publish_at: Optional[datetime] = None,
        pillar: str = "",
    ) -> Optional[ContentSlot]:
        """Add a single content item to the queue.

        Args:
            topic: What to write about.
            platform: Target platform (twitter, blog, etc.).
            content_format: Content format (auto-detected from platform if empty).
            publish_at: When to publish (default: next available slot).
            pillar: Topic pillar (auto-detected if empty).

        Returns:
            The created ContentSlot, or None on error.
        """
        if not content_format:
            cadence = _DEFAULT_CADENCE.get(platform, {})
            fmts = cadence.get("formats", ["blog"])
            content_format = fmts[0]

        if not publish_at:
            publish_at = datetime.now() + timedelta(hours=2)

        slot = ContentSlot(
            slot_id=_generate_slot_id(),
            pillar=pillar or "general",
            platform=platform,
            content_format=content_format,
            topic=topic,
            status="planned",
            scheduled_at=publish_at.isoformat(),
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

        with _lock:
            data = _load_calendar()
            data["slots"].append(slot.to_dict())
            _save_calendar(data)

        logger.info("Queued content: %s on %s at %s", topic[:50], platform,
                     publish_at.strftime("%Y-%m-%d %H:%M"))
        return slot

    def get_upcoming(self, days: int = 7) -> List[ContentSlot]:
        """Get planned/generated content for the next N days.

        Args:
            days: How many days ahead to look.

        Returns:
            List of ContentSlot entries sorted by scheduled time.
        """
        cutoff = (datetime.now() + timedelta(days=days)).isoformat()
        now = datetime.now().isoformat()
        data = _load_calendar()
        upcoming = []
        for s in data.get("slots", []):
            if s.get("status") not in ("planned", "generated"):
                continue
            sched = s.get("scheduled_at", "")
            if sched and now <= sched <= cutoff:
                upcoming.append(ContentSlot.from_dict(s))
        upcoming.sort(key=lambda s: s.scheduled_at)
        return upcoming

    def get_due_slots(self) -> List[ContentSlot]:
        """Get slots that are generated and past their scheduled time.

        Called by heartbeat to check what needs publishing.

        Returns:
            List of ContentSlot entries ready to publish.
        """
        data = _load_calendar()
        now = datetime.now().isoformat()
        due = []
        for s in data.get("slots", []):
            if s.get("status") == "generated" and s.get("scheduled_at", "") <= now:
                due.append(ContentSlot.from_dict(s))
        return due

    def get_pending_generation(self, limit: int = 5) -> List[ContentSlot]:
        """Get planned slots that need content generated.

        Returns slots whose scheduled time is within the next 24 hours
        and status is still 'planned'.
        """
        cutoff = (datetime.now() + timedelta(hours=24)).isoformat()
        data = _load_calendar()
        pending = []
        for s in data.get("slots", []):
            if s.get("status") != "planned":
                continue
            sched = s.get("scheduled_at", "")
            if sched and sched <= cutoff:
                pending.append(ContentSlot.from_dict(s))
        pending.sort(key=lambda s: s.scheduled_at)
        return pending[:limit]

    def mark_generated(self, slot_id: str, content: str) -> bool:
        """Mark a slot as generated with its content text."""
        return self._update_slot(slot_id, status="generated",
                                 generated_content=content)

    def mark_published(self, slot_id: str, result: str = "") -> bool:
        """Mark a slot as published with optional result URL."""
        return self._update_slot(slot_id, status="published",
                                 publish_result=result)

    def mark_failed(self, slot_id: str, error: str = "") -> bool:
        """Mark a slot as failed with error message."""
        return self._update_slot(slot_id, status="failed",
                                 publish_result=error)

    def queue_depth_days(self) -> float:
        """How many days of content are queued (planned + generated)."""
        data = _load_calendar()
        active = [s for s in data.get("slots", [])
                  if s.get("status") in ("planned", "generated")]
        if not active:
            return 0.0
        # Count unique scheduled dates
        dates = set()
        for s in active:
            sched = s.get("scheduled_at", "")
            if sched:
                dates.add(sched[:10])  # YYYY-MM-DD
        return float(len(dates))

    def needs_planning(self) -> bool:
        """True if queue is thinner than _MIN_QUEUE_DAYS."""
        return self.queue_depth_days() < _MIN_QUEUE_DAYS

    def _update_slot(self, slot_id: str, **kwargs) -> bool:
        """Update fields on a slot by ID."""
        with _lock:
            data = _load_calendar()
            for s in data.get("slots", []):
                if s.get("slot_id") == slot_id:
                    for k, v in kwargs.items():
                        s[k] = v
                    s["updated_at"] = datetime.now().isoformat()
                    _save_calendar(data)
                    return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Summary stats for diagnostics."""
        data = _load_calendar()
        slots = data.get("slots", [])
        by_status = {}
        by_platform = {}
        for s in slots:
            st = s.get("status", "unknown")
            by_status[st] = by_status.get(st, 0) + 1
            if st in ("planned", "generated"):
                pl = s.get("platform", "unknown")
                by_platform[pl] = by_platform.get(pl, 0) + 1
        return {
            "total_slots": len(slots),
            "by_status": by_status,
            "queued_by_platform": by_platform,
            "queue_depth_days": self.queue_depth_days(),
            "last_plan_date": data.get("last_plan_date", ""),
        }


# ── Formatting ───────────────────────────────────────────────────────

def format_week_plan(slots: List[ContentSlot]) -> str:
    """Format a week plan for Discord display."""
    if not slots:
        return "No content planned. Say 'plan content for this week' to start."

    by_day: Dict[str, List[ContentSlot]] = {}
    for s in slots:
        day = s.scheduled_at[:10] if s.scheduled_at else "unscheduled"
        by_day.setdefault(day, []).append(s)

    parts = ["**Content Calendar:**"]
    for day in sorted(by_day.keys()):
        try:
            dt = datetime.fromisoformat(day)
            day_label = dt.strftime("%A %b %d")
        except ValueError:
            day_label = day
        day_slots = by_day[day]
        platform_counts: Dict[str, int] = {}
        for s in day_slots:
            platform_counts[s.platform] = platform_counts.get(s.platform, 0) + 1
        summary = ", ".join(f"{p} ({c})" for p, c in sorted(platform_counts.items()))
        parts.append(f"**{day_label}:** {summary}")

    total = len(slots)
    platforms = len(set(s.platform for s in slots))
    parts.append(f"\n*{total} posts across {platforms} platforms over 7 days.*")
    return "\n".join(parts)


def format_upcoming(slots: List[ContentSlot], limit: int = 10) -> str:
    """Format upcoming content for Discord display."""
    if not slots:
        return "Nothing scheduled. The queue is empty."

    parts = ["**Upcoming Content:**"]
    for s in slots[:limit]:
        try:
            dt = datetime.fromisoformat(s.scheduled_at)
            time_str = dt.strftime("%b %d %I:%M%p")
        except ValueError:
            time_str = s.scheduled_at[:16]
        icon = {"planned": "\u2b1c", "generated": "\u2705"}.get(s.status, "\u2753")
        parts.append(f"{icon} **{time_str}** — {s.platform}: {s.topic[:50]}")

    remaining = len(slots) - limit
    if remaining > 0:
        parts.append(f"*...and {remaining} more.*")
    return "\n".join(parts)
