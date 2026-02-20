"""
Adaptive heartbeat: two-tier sleep (command 10s / idle 60s) + night mode.
Loads config/heartbeat.yaml. Command mode for 2 min after user interaction;
everything else uses idle mode (60s). Night mode overrides to 1800s.
"""

import logging
import os
import time
from typing import Any, Dict, Optional

import yaml

from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)


class AdaptiveHeartbeat:
    """
    Two-tier sleep: command (10s for 2 min after interaction), idle (60s).
    Night mode overrides to 1800s.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        base = _base_path()
        path = config_path or os.path.join(base, "config", "heartbeat.yaml")
        self._config = self._load_config(path)
        ad = self._config.get("adaptive_sleep", {}) or {}

        cmd = ad.get("command_mode", {}) or {}
        self._command_cooldown = float(cmd.get("cooldown", 10.0))
        self._command_duration = float(cmd.get("duration", 120))

        idle = ad.get("idle_mode", {}) or {}
        self._idle_cooldown = float(idle.get("cooldown", 60.0))

        self._last_user_interaction = time.monotonic()
        self._last_system_event = time.monotonic()
        self._mode = "idle"

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
        logger.info("Entered command mode (%.0fs checks for %.0f min)",
                     self._command_cooldown, self._command_duration / 60)

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

    def _night_cooldown(self) -> float:
        """Absolute cooldown during night window (e.g. 1800s)."""
        night = (self._config.get("time_awareness", {}) or {}).get("night_mode", {}) or {}
        return float(night.get("cooldown", 1800.0))

    def get_sleep_duration(self) -> float:
        """
        Return seconds to sleep using two tiers:
        - Command: 10s for 2 min after user interaction
        - Idle: 60s for everything else
        Night time overrides to 1800s.
        """
        now = time.monotonic()
        time_since_command = now - self._last_user_interaction

        # Command mode: recent user activity always gets fast checks (even at night)
        if time_since_command < self._command_duration:
            self._mode = "command"
            logger.debug("Sleep %.2f s (command mode)", self._command_cooldown)
            return max(0.1, self._command_cooldown)

        # Night mode: fixed long cooldown (only when no recent user interaction)
        if self._is_night_time():
            if self._mode == "command":
                logger.info("Exited command mode → night mode (%.0fs checks)", self._night_cooldown())
            self._mode = "idle"
            base = self._night_cooldown()
            logger.debug("Sleep %.0f s (night mode)", base)
            return max(0.1, base)

        # Idle mode
        if self._mode == "command":
            logger.info("Exited command mode → idle (%.0fs checks)", self._idle_cooldown)
        self._mode = "idle"
        logger.debug("Sleep %.2f s (idle mode)", self._idle_cooldown)
        return max(0.1, self._idle_cooldown)
