"""
Adaptive heartbeat: three-tier sleep (command 10s / monitoring 60s / deep sleep 600s).
Loads config/heartbeat.yaml. Command mode for 2 min after user interaction; then
monitoring (1 min); after 10 min idle, deep sleep (10 min, max 30 min). Night mode override.
"""

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)


class AdaptiveHeartbeat:
    """
    Three-tier sleep: command (10s for 2 min after interaction), monitoring (60s),
    deep sleep (600s when idle 10+ min, max 1800s). Night mode uses 1800s.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        base = _base_path()
        path = config_path or os.path.join(base, "config", "heartbeat.yaml")
        self._config = self._load_config(path)
        ad = self._config.get("adaptive_sleep", {}) or {}

        cmd = ad.get("command_mode", {}) or {}
        self._command_cooldown = float(cmd.get("cooldown", 10.0))
        self._command_duration = float(cmd.get("duration", 120))

        mon = ad.get("monitoring_mode", {}) or {}
        self._monitoring_cooldown = float(mon.get("cooldown", 60.0))
        self._idle_threshold = float(mon.get("idle_threshold", 600))

        deep = ad.get("deep_sleep_mode", {}) or {}
        self._deep_cooldown = float(deep.get("cooldown", 600.0))
        self._max_cooldown = float(deep.get("max_cooldown", 1800.0))

        self._last_user_interaction = time.monotonic()
        self._last_system_event = time.monotonic()
        self._mode = "monitoring"

    def _load_config(self, path: str) -> Dict[str, Any]:
        """Load heartbeat.yaml."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.warning("Could not load heartbeat config %s: %s; using defaults", path, e)
            return {}

    def record_user_interaction(self) -> None:
        """Call when the user sends a command or interacts. Enters command mode."""
        self._last_user_interaction = time.monotonic()
        self._mode = "command"
        logger.info("Entered command mode (%.0fs checks for %.0f min)", self._command_cooldown, self._command_duration / 60)

    def record_system_event(self) -> None:
        """Call when triggers fire, file changes, etc."""
        self._last_system_event = time.monotonic()

    def _is_night_time(self) -> bool:
        """True if current time is in night_mode window."""
        ta = self._config.get("time_awareness", {}) or {}
        if not ta.get("enabled", True):
            return False
        night = ta.get("night_mode", {}) or {}
        if not night:
            return False
        try:
            from datetime import datetime
            hour = datetime.now().hour
        except Exception:
            return False
        start = int(night.get("start_hour", 23))
        end = int(night.get("end_hour", 6))
        if start > end:  # e.g. 23-6
            return hour >= start or hour < end
        return start <= hour < end

    def _time_of_day_multiplier(self) -> float:
        """Multiplier for non-night (work_hours 1.0, evening 1.5)."""
        ta = self._config.get("time_awareness", {}) or {}
        if not ta.get("enabled", True):
            return 1.0
        try:
            from datetime import datetime
            hour = datetime.now().hour
        except Exception:
            return 1.0
        work = ta.get("work_hours", {}) or {}
        if work and work.get("start_hour") is not None:
            s, e = int(work.get("start_hour", 9)), int(work.get("end_hour", 17))
            if s <= hour < e:
                return float(work.get("multiplier", 1.0))
        evening = ta.get("evening", {}) or {}
        if evening and evening.get("start_hour") is not None:
            s, e = int(evening.get("start_hour", 18)), int(evening.get("end_hour", 22))
            if s <= hour < e:
                return float(evening.get("multiplier", 1.5))
        return 1.0

    def _night_cooldown(self) -> float:
        """Absolute cooldown during night window (e.g. 1800s)."""
        night = (self._config.get("time_awareness", {}) or {}).get("night_mode", {}) or {}
        return float(night.get("cooldown", 1800.0))

    def get_sleep_duration(self) -> float:
        """
        Return seconds to sleep using three tiers:
        - Command: 10s for 2 min after user interaction
        - Monitoring: 60s when not very idle
        - Deep sleep: 600s (max 1800s) when idle 10+ min
        Night time overrides to 1800s.
        """
        now = time.monotonic()
        time_since_command = now - self._last_user_interaction
        time_since_event = now - self._last_system_event
        time_idle = min(time_since_command, time_since_event)

        # Command mode first: recent user activity always gets fast checks (even at night)
        if time_since_command < self._command_duration:
            self._mode = "command"
            logger.debug("Sleep %.2f s (command mode)", self._command_cooldown)
            return max(0.1, self._command_cooldown)

        # Night mode: fixed long cooldown (only when no recent user interaction)
        if self._is_night_time():
            if self._mode == "command":
                self._mode = "monitoring"
                logger.info("Exited command mode → night mode (%.0fs checks)", self._night_cooldown())
            base = self._night_cooldown()
            logger.debug("Sleep %.0f s (night mode)", base)
            return max(0.1, base)

        # Exited command mode during day
        if self._mode == "command":
            self._mode = "monitoring"
            logger.info("Exited command mode → monitoring (%.0fs checks)", self._monitoring_cooldown)

        # Monitoring vs deep sleep
        if time_idle >= self._idle_threshold:
            if self._mode != "deep_sleep":
                self._mode = "deep_sleep"
                logger.info("Entered deep sleep mode (%.0fs checks)", self._deep_cooldown)
            base = min(self._deep_cooldown, self._max_cooldown)
        else:
            if self._mode == "deep_sleep":
                self._mode = "monitoring"
            base = self._monitoring_cooldown

        mult = self._time_of_day_multiplier()
        sleep = base * mult
        logger.debug("Sleep %.2f s (mode=%s, idle=%.0fs)", sleep, self._mode, time_idle)
        return max(0.1, sleep)
