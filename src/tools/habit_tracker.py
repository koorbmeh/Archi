"""Habit Tracker — define habits, log completions, track streaks, daily reminders.

Session 249: Generalizes the supplement tracker pattern into a flexible habit system.

Discord commands: "add habit meditate 20 minutes daily", "log meditation",
"habit status", "habit report", "what habits do I track?"

Persistence: data/habit_tracker.json — habit definitions + completion log.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(base_path(), "data", "habit_tracker.json")
_lock = threading.Lock()


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class Habit:
    """A trackable habit."""
    name: str                           # e.g. "Meditate"
    target_type: str = "boolean"        # boolean, count, duration
    target_value: float = 1.0           # 1 for boolean, 8 for "8 glasses", 30 for "30 min"
    unit: str = ""                      # glasses, minutes, pages, reps, etc.
    frequency: str = "daily"            # daily, weekly, weekdays
    time_of_day: str = ""               # morning, evening, anytime
    description: str = ""               # optional context
    active: bool = True
    added_date: str = ""                # ISO date

    def display_name(self) -> str:
        if self.target_type == "boolean":
            return self.name
        return f"{self.name} ({self.target_value:.0f} {self.unit})" if self.unit else self.name


@dataclass
class CompletionEntry:
    """A single habit completion event."""
    habit_name: str
    value: float = 1.0          # 1 for boolean, actual count/duration for others
    timestamp: str = ""         # ISO datetime
    note: str = ""


class HabitTracker:
    """Manages habit definitions and daily completion logging."""

    def __init__(self, data_path: str = ""):
        self._path = data_path or _DATA_PATH
        self._habits: Dict[str, Habit] = {}
        self._completions: List[dict] = []
        self._load()

    # ── Habit CRUD ───────────────────────────────────────────────────

    def add_habit(self, name: str, target_type: str = "boolean",
                  target_value: float = 1.0, unit: str = "",
                  frequency: str = "daily", time_of_day: str = "",
                  description: str = "") -> Habit:
        key = name.lower().strip()
        if not key:
            raise ValueError("Habit name cannot be empty")
        target_type = target_type.strip().lower()
        if target_type not in ("boolean", "count", "duration"):
            target_type = "boolean"
        habit = Habit(
            name=name.strip(), target_type=target_type,
            target_value=max(0.1, float(target_value)),
            unit=unit.strip(), frequency=frequency.strip().lower(),
            time_of_day=time_of_day.strip().lower(),
            description=description.strip(), active=True,
            added_date=date.today().isoformat(),
        )
        with _lock:
            self._habits[key] = habit
            self._save()
        return habit

    def remove_habit(self, name: str) -> bool:
        """Deactivate a habit (soft delete)."""
        key = name.lower().strip()
        with _lock:
            if key in self._habits:
                self._habits[key].active = False
                self._save()
                return True
        return False

    def get_active(self) -> List[Habit]:
        return [h for h in self._habits.values() if h.active]

    def get_habit(self, name: str) -> Optional[Habit]:
        return self._habits.get(name.lower().strip())

    # ── Completion logging ───────────────────────────────────────────

    def log_completion(self, habit_name: str, value: float = 1.0,
                       note: str = "") -> CompletionEntry:
        entry = CompletionEntry(
            habit_name=habit_name.strip(),
            value=max(0, float(value)),
            timestamp=datetime.now().isoformat(),
            note=note.strip(),
        )
        with _lock:
            self._completions.append(asdict(entry))
            self._save()
        return entry

    def log_all_done(self, note: str = "") -> List[CompletionEntry]:
        """Mark all active boolean habits as done."""
        entries = []
        for habit in self.get_active():
            if habit.target_type == "boolean":
                entries.append(self.log_completion(habit.name, 1.0, note))
        return entries

    def get_today_log(self) -> List[dict]:
        today = date.today().isoformat()
        return [e for e in self._completions
                if e.get("timestamp", "").startswith(today)]

    def get_log_for_date(self, target_date: date) -> List[dict]:
        prefix = target_date.isoformat()
        return [e for e in self._completions
                if e.get("timestamp", "").startswith(prefix)]

    # ── Analysis ─────────────────────────────────────────────────────

    def _today_progress(self, habit: Habit) -> float:
        """Return today's total value for a habit (sum of logged values)."""
        today_entries = [
            e for e in self.get_today_log()
            if e["habit_name"].lower() == habit.name.lower()
        ]
        return sum(e.get("value", 1.0) for e in today_entries)

    def get_incomplete_today(self) -> List[Habit]:
        """Habits not yet meeting their daily target today."""
        result = []
        for habit in self.get_active():
            if habit.frequency not in ("daily", "weekdays"):
                continue
            if habit.frequency == "weekdays" and date.today().weekday() >= 5:
                continue
            if self._today_progress(habit) < habit.target_value:
                result.append(habit)
        return result

    def adherence_rate(self, days: int = 7) -> float:
        """Fraction of daily habits completed over the last N days."""
        active = [h for h in self.get_active()
                  if h.frequency in ("daily", "weekdays")]
        if not active:
            return 1.0
        total_expected = 0
        total_met = 0
        today = date.today()
        for offset in range(days):
            check = today - timedelta(days=offset)
            day_log = self.get_log_for_date(check)
            for habit in active:
                if habit.added_date and check.isoformat() < habit.added_date:
                    continue
                if habit.frequency == "weekdays" and check.weekday() >= 5:
                    continue
                total_expected += 1
                day_total = sum(
                    e.get("value", 1.0) for e in day_log
                    if e["habit_name"].lower() == habit.name.lower()
                )
                if day_total >= habit.target_value:
                    total_met += 1
        return total_met / total_expected if total_expected else 1.0

    def streak(self) -> int:
        """Consecutive days where ALL daily habits were met."""
        daily = [h for h in self.get_active()
                 if h.frequency in ("daily", "weekdays")]
        if not daily:
            return 0
        count = 0
        today = date.today()
        for offset in range(365):
            check = today - timedelta(days=offset)
            day_log = self.get_log_for_date(check)
            if not day_log:
                break
            all_met = True
            for habit in daily:
                if habit.added_date and check.isoformat() < habit.added_date:
                    continue
                if habit.frequency == "weekdays" and check.weekday() >= 5:
                    continue
                day_total = sum(
                    e.get("value", 1.0) for e in day_log
                    if e["habit_name"].lower() == habit.name.lower()
                )
                if day_total < habit.target_value:
                    all_met = False
                    break
            if all_met:
                count += 1
            else:
                break
        return count

    # ── Formatting ───────────────────────────────────────────────────

    def format_habit_list(self) -> str:
        active = self.get_active()
        if not active:
            return "No habits tracked yet. Try: \"add habit meditate 20 minutes daily\""
        lines = [f"**Your Habits** ({len(active)})"]
        for h in active:
            parts = [f"- **{h.name}**"]
            if h.target_type != "boolean":
                parts.append(f"— target: {h.target_value:.0f} {h.unit}")
            parts.append(f"({h.frequency})")
            if h.time_of_day:
                parts.append(f"[{h.time_of_day}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def format_daily_status(self) -> str:
        active = self.get_active()
        if not active:
            return "No habits tracked yet."
        done_count = 0
        lines = []
        for h in active:
            progress = self._today_progress(h)
            met = progress >= h.target_value
            if met:
                done_count += 1
            if h.target_type == "boolean":
                mark = "done" if met else "not yet"
                lines.append(f"- {h.name}: {mark}")
            else:
                lines.append(
                    f"- {h.name}: {progress:.0f}/{h.target_value:.0f} {h.unit}"
                )
        header = f"**Today's Habits** — {done_count}/{len(active)} complete"
        streak_val = self.streak()
        if streak_val > 0:
            header += f" | streak: {streak_val}d"
        return header + "\n" + "\n".join(lines)

    def format_report(self, days: int = 7) -> str:
        active = self.get_active()
        if not active:
            return "No habits tracked yet."
        rate = self.adherence_rate(days)
        streak_val = self.streak()
        lines = [
            f"**Habit Report** ({days}-day window)",
            f"Adherence: {rate:.0%} | Streak: {streak_val} days",
            "",
        ]
        today = date.today()
        for h in active:
            met_days = 0
            checked_days = 0
            for offset in range(days):
                check = today - timedelta(days=offset)
                if h.added_date and check.isoformat() < h.added_date:
                    continue
                if h.frequency == "weekdays" and check.weekday() >= 5:
                    continue
                if h.frequency == "weekly":
                    continue
                checked_days += 1
                day_log = self.get_log_for_date(check)
                day_total = sum(
                    e.get("value", 1.0) for e in day_log
                    if e["habit_name"].lower() == h.name.lower()
                )
                if day_total >= h.target_value:
                    met_days += 1
            if checked_days:
                pct = met_days / checked_days
                lines.append(f"- {h.name}: {pct:.0%} ({met_days}/{checked_days} days)")
            else:
                lines.append(f"- {h.name}: no data yet")
        return "\n".join(lines)

    def format_reminder(self) -> str:
        """Format a reminder for incomplete habits."""
        incomplete = self.get_incomplete_today()
        if not incomplete:
            return ""
        names = ", ".join(h.display_name() for h in incomplete)
        return f"Habit reminder: still need to do {names} today."

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, hdict in data.get("habits", {}).items():
                self._habits[key] = Habit(**hdict)
            self._completions = data.get("completions", [])
            cutoff = (date.today() - timedelta(days=90)).isoformat()
            self._completions = [
                e for e in self._completions if e.get("timestamp", "") >= cutoff
            ]
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.error("Failed to load habit data: %s", e)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        data = {
            "habits": {k: asdict(v) for k, v in self._habits.items()},
            "completions": self._completions,
            "last_updated": datetime.now().isoformat(),
        }
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.error("Failed to save habit data: %s", e)


# ── Singleton ────────────────────────────────────────────────────────

_instance: Optional[HabitTracker] = None
_instance_lock = threading.Lock()


def get_tracker() -> HabitTracker:
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is not None:
            return _instance
        _instance = HabitTracker()
        return _instance


def _reset_for_testing() -> None:
    global _instance
    with _instance_lock:
        _instance = None
