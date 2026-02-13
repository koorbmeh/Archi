"""
System health monitoring: CPU, memory, temperature, disk.
should_throttle() for adaptive heartbeat; check_health(); log metrics to data/metrics.db.
"""

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import psutil
from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)





def _metrics_db_path() -> str:
    base = _base_path()
    data_dir = os.path.join(base, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "metrics.db")


@dataclass
class HealthStatus:
    """Current system health snapshot."""

    cpu: float
    memory: float
    disk: float
    temperature: Optional[float] = None
    alerts: List[str] = field(default_factory=list)


class SystemMonitor:
    """
    Monitor CPU, memory, disk, and optionally temperature.
    Thresholds from config/rules.yaml monitoring section or defaults.
    """

    def __init__(
        self,
        cpu_threshold: float = 80.0,
        memory_threshold: float = 90.0,
        temp_threshold: float = 80.0,
        disk_threshold: float = 90.0,
    ) -> None:
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.temp_threshold = temp_threshold
        self.disk_threshold = disk_threshold
        self._alerts: List[str] = []

    def check_health(self) -> HealthStatus:
        """
        Sample CPU, memory, disk, and temperature (if available).
        Populate alerts when over thresholds.
        """
        self._alerts = []

        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
        except Exception as e:
            logger.debug("CPU check failed: %s", e)
            cpu_percent = 0.0
        if cpu_percent > self.cpu_threshold:
            self._alerts.append("high_cpu")
            logger.warning("High CPU: %.1f%%", cpu_percent)

        try:
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
        except Exception as e:
            logger.debug("Memory check failed: %s", e)
            memory_percent = 0.0
        if memory_percent > self.memory_threshold:
            self._alerts.append("high_memory")
            logger.warning("High memory: %.1f%%", memory_percent)

        temp: Optional[float] = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                all_t = [
                    t.current
                    for sensor in temps.values()
                    for t in sensor
                ]
                if all_t:
                    temp = max(all_t)
                    if temp > self.temp_threshold:
                        self._alerts.append("high_temperature")
                        logger.warning("High temperature: %.1f C", temp)
        except (AttributeError, OSError):
            # Windows often has no sensors_temperatures
            pass
        except Exception as e:
            logger.debug("Temperature check failed: %s", e)

        try:
            base = _base_path()
            root = os.path.splitdrive(base)[0] or "C:"
            if not root.endswith(os.sep):
                root = root + os.sep
            disk = psutil.disk_usage(root)
            disk_percent = disk.percent
        except Exception as e:
            logger.debug("Disk check failed: %s", e)
            disk_percent = 0.0
        if disk_percent > self.disk_threshold:
            self._alerts.append("low_disk_space")
            logger.warning("Low disk space: %.1f%%", disk_percent)

        return HealthStatus(
            cpu=cpu_percent,
            memory=memory_percent,
            disk=disk_percent,
            temperature=temp,
            alerts=list(self._alerts),
        )

    def should_throttle(self) -> bool:
        """
        Return True when CPU or temperature is over threshold (so the
        agent loop can sleep longer).
        """
        health = self.check_health()
        if health.cpu > self.cpu_threshold:
            return True
        if health.temperature is not None and health.temperature > self.temp_threshold:
            return True
        return False

    def log_metrics(self) -> None:
        """Append current health to data/metrics.db (create table if needed)."""
        health = self.check_health()
        db_path = _metrics_db_path()
        try:
            with sqlite3.connect(db_path) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        cpu_percent REAL,
                        memory_percent REAL,
                        disk_percent REAL,
                        temperature REAL,
                        alerts TEXT
                    )
                    """
                )
                db.execute(
                    """
                    INSERT INTO system_metrics
                    (timestamp, cpu_percent, memory_percent, disk_percent, temperature, alerts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.utcnow().isoformat(),
                        health.cpu,
                        health.memory,
                        health.disk,
                        health.temperature,
                        ",".join(health.alerts) if health.alerts else None,
                    ),
                )
        except sqlite3.Error as e:
            logger.error("Failed to log metrics to db: %s", e)
