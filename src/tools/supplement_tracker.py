"""Supplement Tracker — logs supplements, tracks daily intake, sends reminders.

Session 245: Practical daily-life capability.

Discord commands: "add supplement creatine 5g daily", "log my supplements",
"what supplements do I take?", "supplement report".

Persistence: data/supplement_tracker.json — supplement definitions + intake log.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(base_path(), "data", "supplement_tracker.json")
_lock = threading.Lock()

# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class Supplement:
    """A supplement Jesse takes."""
    name: str                       # e.g. "Creatine"
    dose: str = ""                  # e.g. "5g", "1000 IU"
    frequency: str = "daily"        # daily, twice_daily, weekly, as_needed
    time_of_day: str = ""           # morning, evening, with_meals, etc.
    notes: str = ""                 # any extra context
    active: bool = True
    added_date: str = ""            # ISO date when added
    stock_days: int = 0             # estimated days of supply remaining (0 = unknown)

    def display_name(self) -> str:
        parts = [self.name]
        if self.dose:
            parts.append(f"({self.dose})")
        return " ".join(parts)


@dataclass
class IntakeEntry:
    """A single logged intake event."""
    supplement_name: str
    timestamp: str = ""     # ISO datetime
    status: str = "taken"   # taken, skipped, late
    note: str = ""


class SupplementTracker:
    """Manages supplement definitions and daily intake logging."""

    def __init__(self, data_path: str = ""):
        self._path = data_path or _DATA_PATH
        self._supplements: Dict[str, Supplement] = {}
        self._intake_log: List[dict] = []
        self._load()

    # ── Supplement CRUD ──────────────────────────────────────────────

    def add_supplement(self, name: str, dose: str = "", frequency: str = "daily",
                       time_of_day: str = "", notes: str = "",
                       stock_days: int = 0) -> Supplement:
        """Add or update a supplement."""
        key = name.lower().strip()
        if not key:
            raise ValueError("Supplement name cannot be empty")

        supp = Supplement(
            name=name.strip(),
            dose=dose.strip(),
            frequency=frequency.strip().lower(),
            time_of_day=time_of_day.strip().lower(),
            notes=notes.strip(),
            active=True,
            added_date=date.today().isoformat(),
            stock_days=stock_days,
        )
        with _lock:
            self._supplements[key] = supp
            self._save()
        logger.info("Added supplement: %s", supp.display_name())
        return supp

    def remove_supplement(self, name: str) -> bool:
        """Deactivate a supplement (soft delete)."""
        key = name.lower().strip()
        with _lock:
            if key in self._supplements:
                self._supplements[key].active = False
                self._save()
                logger.info("Deactivated supplement: %s", name)
                return True
        return False

    def get_active(self) -> List[Supplement]:
        """Return all active supplements."""
        return [s for s in self._supplements.values() if s.active]

    def get_supplement(self, name: str) -> Optional[Supplement]:
        """Look up a supplement by name (case-insensitive)."""
        return self._supplements.get(name.lower().strip())

    def update_stock(self, name: str, days: int) -> bool:
        """Update estimated stock remaining for a supplement."""
        key = name.lower().strip()
        with _lock:
            if key in self._supplements:
                self._supplements[key].stock_days = max(0, days)
                self._save()
                return True
        return False

    # ── Intake logging ───────────────────────────────────────────────

    def log_intake(self, supplement_name: str, status: str = "taken",
                   note: str = "") -> IntakeEntry:
        """Log that a supplement was taken (or skipped)."""
        entry = IntakeEntry(
            supplement_name=supplement_name.strip(),
            timestamp=datetime.now().isoformat(),
            status=status,
            note=note.strip(),
        )
        with _lock:
            self._intake_log.append(asdict(entry))
            # Decrement stock if taken
            key = supplement_name.lower().strip()
            if status == "taken" and key in self._supplements:
                supp = self._supplements[key]
                if supp.stock_days > 0:
                    supp.stock_days -= 1
            self._save()
        logger.debug("Logged intake: %s (%s)", supplement_name, status)
        return entry

    def log_all_taken(self, note: str = "") -> List[IntakeEntry]:
        """Log all active supplements as taken right now."""
        entries = []
        for supp in self.get_active():
            entries.append(self.log_intake(supp.name, "taken", note))
        return entries

    def get_today_log(self) -> List[dict]:
        """Return all intake entries for today."""
        today = date.today().isoformat()
        return [e for e in self._intake_log if e.get("timestamp", "").startswith(today)]

    def get_log_for_date(self, target_date: date) -> List[dict]:
        """Return intake entries for a specific date."""
        prefix = target_date.isoformat()
        return [e for e in self._intake_log if e.get("timestamp", "").startswith(prefix)]

    # ── Analysis ─────────────────────────────────────────────────────

    def get_not_taken_today(self) -> List[Supplement]:
        """Return active supplements not yet logged today."""
        today_names = {
            e["supplement_name"].lower()
            for e in self.get_today_log()
            if e.get("status") == "taken"
        }
        return [s for s in self.get_active() if s.name.lower() not in today_names]

    def get_low_stock(self, threshold: int = 7) -> List[Supplement]:
        """Return supplements with stock below threshold days."""
        return [
            s for s in self.get_active()
            if 0 < s.stock_days <= threshold
        ]

    def adherence_rate(self, days: int = 7) -> float:
        """Calculate adherence rate over the last N days (0.0–1.0)."""
        active = self.get_active()
        if not active:
            return 1.0

        daily_supps = [s for s in active if s.frequency in ("daily", "twice_daily")]
        if not daily_supps:
            return 1.0

        total_expected = 0
        total_taken = 0
        today = date.today()

        for day_offset in range(days):
            check_date = today - timedelta(days=day_offset)
            day_log = self.get_log_for_date(check_date)
            taken_names = {
                e["supplement_name"].lower()
                for e in day_log
                if e.get("status") == "taken"
            }
            for supp in daily_supps:
                # Only count days after the supplement was added
                if supp.added_date and check_date.isoformat() < supp.added_date:
                    continue
                total_expected += 1
                if supp.name.lower() in taken_names:
                    total_taken += 1

        if total_expected == 0:
            return 1.0
        return total_taken / total_expected

    def streak(self) -> int:
        """Calculate current consecutive-day streak of taking all supplements."""
        daily_supps = [s for s in self.get_active() if s.frequency in ("daily", "twice_daily")]
        if not daily_supps:
            return 0

        streak_count = 0
        today = date.today()

        for day_offset in range(365):  # max 1 year lookback
            check_date = today - timedelta(days=day_offset)
            day_log = self.get_log_for_date(check_date)
            taken_names = {
                e["supplement_name"].lower()
                for e in day_log
                if e.get("status") == "taken"
            }
            all_taken = all(
                supp.name.lower() in taken_names
                for supp in daily_supps
                if not supp.added_date or check_date.isoformat() >= supp.added_date
            )
            if all_taken and day_log:
                streak_count += 1
            else:
                break

        return streak_count

    # ── Formatting ───────────────────────────────────────────────────

    def format_supplement_list(self) -> str:
        """Format active supplements for Discord display."""
        active = self.get_active()
        if not active:
            return "No supplements tracked yet. Add one with: \"add supplement creatine 5g daily\""

        lines = ["**Your Supplements:**"]
        for s in sorted(active, key=lambda x: x.name.lower()):
            parts = [f"• **{s.name}**"]
            if s.dose:
                parts.append(f"— {s.dose}")
            if s.frequency != "daily":
                parts.append(f"({s.frequency})")
            if s.time_of_day:
                parts.append(f"[{s.time_of_day}]")
            if s.stock_days > 0:
                parts.append(f"📦 {s.stock_days}d left")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def format_daily_status(self) -> str:
        """Format today's intake status for Discord."""
        active = self.get_active()
        if not active:
            return "No supplements being tracked."

        not_taken = self.get_not_taken_today()
        taken_count = len(active) - len(not_taken)

        lines = [f"**Today's Supplements:** {taken_count}/{len(active)} taken"]

        if not not_taken:
            s = self.streak()
            streak_text = f" (streak: {s} days)" if s > 1 else ""
            lines.append(f"All done for today!{streak_text}")
        else:
            lines.append("Still need to take:")
            for s in not_taken:
                lines.append(f"  • {s.display_name()}")

        low = self.get_low_stock()
        if low:
            lines.append("\n**Low stock:**")
            for s in low:
                lines.append(f"  • {s.name} — {s.stock_days} days left")

        return "\n".join(lines)

    def format_report(self, days: int = 7) -> str:
        """Format a multi-day adherence report."""
        active = self.get_active()
        if not active:
            return "No supplements being tracked."

        rate = self.adherence_rate(days)
        s = self.streak()

        lines = [
            f"**Supplement Report ({days}-day)**",
            f"Adherence: {rate:.0%}",
            f"Current streak: {s} day{'s' if s != 1 else ''}",
            f"Tracking: {len(active)} supplement{'s' if len(active) != 1 else ''}",
        ]

        low = self.get_low_stock()
        if low:
            lines.append("\n**Restock soon:**")
            for supp in low:
                lines.append(f"  • {supp.name} — {supp.stock_days} days left")

        return "\n".join(lines)

    def format_reminder(self) -> str:
        """Format a reminder message for untaken supplements."""
        not_taken = self.get_not_taken_today()
        if not not_taken:
            return ""
        names = ", ".join(s.display_name() for s in not_taken)
        return f"Supplement reminder: still need to take {names} today."

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load data from disk."""
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, sdict in data.get("supplements", {}).items():
                self._supplements[key] = Supplement(**sdict)
            self._intake_log = data.get("intake_log", [])
            # Trim old log entries (keep last 90 days)
            cutoff = (date.today() - timedelta(days=90)).isoformat()
            self._intake_log = [
                e for e in self._intake_log
                if e.get("timestamp", "") >= cutoff
            ]
            logger.debug("Loaded %d supplements, %d log entries",
                         len(self._supplements), len(self._intake_log))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.error("Failed to load supplement data: %s", e)

    def _save(self) -> None:
        """Persist data to disk."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        data = {
            "supplements": {k: asdict(v) for k, v in self._supplements.items()},
            "intake_log": self._intake_log,
            "last_updated": datetime.now().isoformat(),
        }
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.error("Failed to save supplement data: %s", e)


# ── Singleton ────────────────────────────────────────────────────────

_instance: Optional[SupplementTracker] = None
_instance_lock = threading.Lock()


def get_tracker() -> SupplementTracker:
    """Get or create the singleton SupplementTracker."""
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is not None:
            return _instance
        _instance = SupplementTracker()
        return _instance


def _reset_for_testing() -> None:
    """Clear the singleton for test isolation."""
    global _instance
    with _instance_lock:
        _instance = None
