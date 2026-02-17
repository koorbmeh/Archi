"""Initiative Tracker — Budget and logging for Archi's proactive work.

Tracks daily spend on self-initiated goals (separate from user-requested
work).  Resets at midnight in the user's timezone.

Created in session 36 (companion personality overhaul).
"""

import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


def _get_config() -> Dict[str, Any]:
    """Load initiative config from rules.yaml."""
    try:
        from src.utils.paths import project_root
        path = project_root() / "config" / "rules.yaml"
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("initiative", {})
    except Exception:
        return {}


def _data_dir() -> Path:
    from src.utils.paths import project_root
    d = project_root() / "data"
    d.mkdir(exist_ok=True)
    return d


class InitiativeTracker:
    """Track proactive initiative budget and history."""

    def __init__(self) -> None:
        cfg = _get_config()
        self.daily_budget: float = float(cfg.get("daily_budget", 0.50))
        self.max_per_day: int = int(cfg.get("max_per_day", 2))
        self.enabled: bool = bool(cfg.get("enabled", True))
        self.respect_quiet_hours: bool = bool(cfg.get("respect_quiet_hours", True))

        self._state_path = _data_dir() / "initiative_state.json"
        self._log_path = _data_dir() / "initiative_log.jsonl"

        # Daily state
        self.today: str = ""
        self.spend_today: float = 0.0
        self.count_today: int = 0
        self._load_state()

    # ── State persistence ────────────────────────────────────────

    def _load_state(self) -> None:
        """Load today's state.  Reset if it's a new day."""
        today_str = date.today().isoformat()
        try:
            if self._state_path.exists():
                with open(self._state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("date") == today_str:
                    self.today = today_str
                    self.spend_today = float(data.get("spend", 0.0))
                    self.count_today = int(data.get("count", 0))
                    return
        except Exception as e:
            logger.warning("initiative_tracker: failed to load state: %s", e)
        # New day or corrupt — reset
        self.today = today_str
        self.spend_today = 0.0
        self.count_today = 0
        self._save_state()

    def _save_state(self) -> None:
        try:
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump({
                    "date": self.today,
                    "spend": round(self.spend_today, 4),
                    "count": self.count_today,
                }, f)
        except Exception as e:
            logger.error("initiative_tracker: failed to save state: %s", e)

    # ── Public API ───────────────────────────────────────────────

    def can_initiate(self) -> bool:
        """True if budget and count allow another initiative today."""
        if not self.enabled:
            return False
        # Re-check day boundary
        today_str = date.today().isoformat()
        if self.today != today_str:
            self._load_state()
        return (
            self.spend_today < self.daily_budget
            and self.count_today < self.max_per_day
        )

    def budget_remaining(self) -> float:
        """How much initiative budget remains today."""
        return max(0.0, self.daily_budget - self.spend_today)

    def record(
        self,
        title: str,
        why_jesse_cares: str,
        estimated_cost: float,
        goal_id: str,
    ) -> None:
        """Log a new initiative (at creation time)."""
        entry = {
            "ts": datetime.now().isoformat(),
            "goal_id": goal_id,
            "title": title,
            "why": why_jesse_cares,
            "estimated_cost": round(estimated_cost, 4),
            "status": "created",
        }
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("initiative_tracker: failed to log: %s", e)

        self.count_today += 1
        self._save_state()

    def record_cost(self, goal_id: str, actual_cost: float) -> None:
        """Record actual cost after an initiative completes."""
        self.spend_today += actual_cost
        self._save_state()
        logger.info(
            "initiative_tracker: recorded $%.4f for %s (total today: $%.4f/$%.2f)",
            actual_cost, goal_id, self.spend_today, self.daily_budget,
        )

    def get_summary(self) -> Dict[str, Any]:
        """Return today's initiative summary."""
        return {
            "budget": self.daily_budget,
            "spent": round(self.spend_today, 4),
            "remaining": round(self.budget_remaining(), 4),
            "count": self.count_today,
            "max_per_day": self.max_per_day,
        }
