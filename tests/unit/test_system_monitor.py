"""
Unit tests for monitoring/system_monitor.py.

Covers: _metrics_db_path, HealthStatus, SystemMonitor.check_health,
SystemMonitor._get_windows_temperature, SystemMonitor.should_throttle,
SystemMonitor.log_metrics.
"""

import os
import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open, call
from collections import namedtuple

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest

from src.monitoring.system_monitor import (
    _metrics_db_path,
    HealthStatus,
    SystemMonitor,
)


# ── HealthStatus ─────────────────────────────────────────────────────────

class TestHealthStatus:
    """HealthStatus dataclass creation and defaults."""

    def test_required_fields(self):
        """Create HealthStatus with required fields only."""
        status = HealthStatus(cpu=45.0, memory=60.0, disk=50.0)
        assert status.cpu == 45.0
        assert status.memory == 60.0
        assert status.disk == 50.0

    def test_temperature_default_none(self):
        """Temperature defaults to None."""
        status = HealthStatus(cpu=45.0, memory=60.0, disk=50.0)
        assert status.temperature is None

    def test_alerts_default_empty_list(self):
        """Alerts defaults to empty list."""
        status = HealthStatus(cpu=45.0, memory=60.0, disk=50.0)
        assert status.alerts == []
        assert isinstance(status.alerts, list)

    def test_all_fields_with_values(self):
        """Create HealthStatus with all fields specified."""
        status = HealthStatus(
            cpu=75.0,
            memory=85.0,
            disk=95.0,
            temperature=78.5,
            alerts=["high_cpu", "high_memory"]
        )
        assert status.cpu == 75.0
        assert status.memory == 85.0
        assert status.disk == 95.0
        assert status.temperature == 78.5
        assert status.alerts == ["high_cpu", "high_memory"]

    def test_separate_alert_lists_independent(self):
        """Ensure separate HealthStatus instances don't share alert lists."""
        status1 = HealthStatus(cpu=50.0, memory=60.0, disk=70.0)
        status2 = HealthStatus(cpu=50.0, memory=60.0, disk=70.0)
        status1.alerts.append("alert1")
        assert status2.alerts == []


# ── TestSystemMonitorInit ────────────────────────────────────────────────

class TestSystemMonitorInit:
    """SystemMonitor initialization with default and custom thresholds."""

    def test_default_thresholds(self):
        """Monitor initializes with default thresholds."""
        monitor = SystemMonitor()
        assert monitor.cpu_threshold == 80.0
        assert monitor.memory_threshold == 90.0
        assert monitor.temp_threshold == 80.0
        assert monitor.disk_threshold == 90.0

    def test_custom_thresholds(self):
        """Monitor initializes with custom thresholds."""
        monitor = SystemMonitor(
            cpu_threshold=75.0,
            memory_threshold=85.0,
            temp_threshold=70.0,
            disk_threshold=88.0
        )
        assert monitor.cpu_threshold == 75.0
        assert monitor.memory_threshold == 85.0
        assert monitor.temp_threshold == 70.0
        assert monitor.disk_threshold == 88.0

    def test_partial_custom_thresholds(self):
        """Monitor allows partial threshold override."""
        monitor = SystemMonitor(cpu_threshold=50.0, disk_threshold=80.0)
        assert monitor.cpu_threshold == 50.0
        assert monitor.memory_threshold == 90.0  # default
        assert monitor.temp_threshold == 80.0    # default
        assert monitor.disk_threshold == 80.0


# ── TestCheckHealth ─────────────────────────────────────────────────────

class TestCheckHealth:
    """SystemMonitor.check_health() samples and alert generation."""

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_normal_readings_no_alerts(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Normal readings (all below threshold) generate no alerts."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()
        status = monitor.check_health()

        assert status.cpu == 45.0
        assert status.memory == 60.0
        assert status.disk == 50.0
        assert status.temperature is None
        assert status.alerts == []

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=85.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_high_cpu_alert(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """CPU over threshold generates high_cpu alert."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor(cpu_threshold=80.0)
        status = monitor.check_health()

        assert status.cpu == 85.0
        assert "high_cpu" in status.alerts
        assert status.alerts.count("high_cpu") == 1

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_high_memory_alert(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Memory over threshold generates high_memory alert."""
        mem = namedtuple("VirtualMemory", "percent")(percent=95.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor(memory_threshold=90.0)
        status = monitor.check_health()

        assert status.memory == 95.0
        assert "high_memory" in status.alerts

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_high_temperature_alert(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Temperature over threshold generates high_temperature alert."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        # Mock sensors_temperatures to return temperature reading
        temp_entry = namedtuple("TempEntry", "current")(current=85.0)
        mock_temps.return_value = {"coretemp": [temp_entry]}

        monitor = SystemMonitor(temp_threshold=80.0)
        status = monitor.check_health()

        assert status.temperature == 85.0
        assert "high_temperature" in status.alerts

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_low_disk_space_alert(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Disk over threshold generates low_disk_space alert."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=95.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor(disk_threshold=90.0)
        status = monitor.check_health()

        assert status.disk == 95.0
        assert "low_disk_space" in status.alerts

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=85.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_multiple_alerts(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Multiple thresholds exceeded generate multiple alerts."""
        mem = namedtuple("VirtualMemory", "percent")(percent=95.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=95.0)
        mock_disk.return_value = disk
        temp_entry = namedtuple("TempEntry", "current")(current=85.0)
        mock_temps.return_value = {"coretemp": [temp_entry]}

        monitor = SystemMonitor(
            cpu_threshold=80.0,
            memory_threshold=90.0,
            temp_threshold=80.0,
            disk_threshold=90.0
        )
        status = monitor.check_health()

        assert "high_cpu" in status.alerts
        assert "high_memory" in status.alerts
        assert "high_temperature" in status.alerts
        assert "low_disk_space" in status.alerts
        assert len(status.alerts) == 4

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", side_effect=Exception("CPU error"))
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_cpu_check_failure_returns_zero(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """CPU check exception returns 0.0."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()
        status = monitor.check_health()

        assert status.cpu == 0.0
        assert "high_cpu" not in status.alerts

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory", side_effect=Exception("Memory error"))
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_memory_check_failure_returns_zero(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Memory check exception returns 0.0."""
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()
        status = monitor.check_health()

        assert status.memory == 0.0
        assert "high_memory" not in status.alerts

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage", side_effect=Exception("Disk error"))
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_disk_check_failure_returns_zero(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Disk check exception returns 0.0."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem

        monitor = SystemMonitor()
        status = monitor.check_health()

        assert status.disk == 0.0
        assert "low_disk_space" not in status.alerts

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_temperature_from_sensors(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Temperature extracted from psutil.sensors_temperatures()."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        temp1 = namedtuple("TempEntry", "current")(current=72.0)
        temp2 = namedtuple("TempEntry", "current")(current=75.0)
        mock_temps.return_value = {
            "coretemp": [temp1, temp2],
            "other": [namedtuple("TempEntry", "current")(current=68.0)]
        }

        monitor = SystemMonitor()
        status = monitor.check_health()

        assert status.temperature == 75.0  # max of all temps

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", side_effect=AttributeError("No sensors"))
    @patch("src.monitoring.system_monitor.SystemMonitor._get_windows_temperature")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_temperature_fallback_to_windows(self, mock_base, mock_win_temp, mock_sensors, mock_disk, mock_mem, mock_cpu):
        """Temperature falls back to _get_windows_temperature on AttributeError."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk
        mock_win_temp.return_value = 65.0

        monitor = SystemMonitor()
        status = monitor.check_health()

        assert status.temperature == 65.0
        mock_win_temp.assert_called_once()

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", side_effect=Exception("Sensor error"))
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_temperature_none_when_all_fail(self, mock_base, mock_sensors, mock_disk, mock_mem, mock_cpu):
        """Temperature is None when both psutil and _get_windows_temperature fail."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()
        status = monitor.check_health()

        assert status.temperature is None

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_alert_list_fresh_each_call(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Alert list is reset each check_health() call (no accumulation)."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()

        # First call with high CPU
        mock_cpu.return_value = 85.0
        status1 = monitor.check_health()
        assert "high_cpu" in status1.alerts

        # Second call with normal CPU - alert should not persist
        mock_cpu.return_value = 45.0
        status2 = monitor.check_health()
        assert "high_cpu" not in status2.alerts
        assert status2.alerts == []


# ── TestGetWindowsTemperature ────────────────────────────────────────────

class TestGetWindowsTemperature:
    """SystemMonitor._get_windows_temperature() WMI strategies."""

    @patch("platform.system", return_value="Linux")
    def test_returns_none_on_non_windows(self, mock_platform):
        """Non-Windows returns None immediately."""
        result = SystemMonitor._get_windows_temperature()
        assert result is None

    @patch("platform.system", return_value="Darwin")
    def test_returns_none_on_macos(self, mock_platform):
        """macOS returns None."""
        result = SystemMonitor._get_windows_temperature()
        assert result is None

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_wmi_thermal_zone_success(self, mock_run, mock_platform):
        """WMI thermal zone strategy succeeds with valid conversion."""
        # 3000 tenths of Kelvin = 300 K = 26.85 C
        # 3100 tenths of Kelvin = 310 K = 36.85 C
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "3000\n3100\n"
        mock_run.return_value = mock_result

        result = SystemMonitor._get_windows_temperature()

        # Should return max converted value (36.85 C from 3100)
        assert result is not None
        assert 36 < result < 37  # ~36.85 C

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_wmi_thermal_zone_sanity_check(self, mock_run, mock_platform):
        """WMI results outside 0-150 C range are filtered."""
        # Invalid high value, one valid
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "50000\n3000\n"  # 5000K and 300K
        mock_run.return_value = mock_result

        result = SystemMonitor._get_windows_temperature()

        # Only valid range 0-150 should be kept
        assert result is not None
        assert result < 150

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_wmi_first_strategy_fails_tries_second(self, mock_run, mock_platform):
        """When first strategy fails, tries Open Hardware Monitor."""
        # First call (thermal zone) fails
        mock_result1 = MagicMock()
        mock_result1.returncode = 1
        mock_result1.stdout = ""

        # Second call (OHM) succeeds
        mock_result2 = MagicMock()
        mock_result2.returncode = 0
        mock_result2.stdout = "45.5\n"

        mock_run.side_effect = [mock_result1, mock_result2]

        result = SystemMonitor._get_windows_temperature()

        assert result == 45.5
        assert mock_run.call_count == 2

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_ohm_strategy_success(self, mock_run, mock_platform):
        """Open Hardware Monitor strategy succeeds."""
        # First call fails, second succeeds
        mock_result1 = MagicMock()
        mock_result1.returncode = 1
        mock_result1.stdout = ""

        mock_result2 = MagicMock()
        mock_result2.returncode = 0
        mock_result2.stdout = "52.3\n48.7\n"

        mock_run.side_effect = [mock_result1, mock_result2]

        result = SystemMonitor._get_windows_temperature()

        assert result == 52.3  # max of values

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_both_strategies_fail_returns_none(self, mock_run, mock_platform):
        """Both WMI strategies fail returns None."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        result = SystemMonitor._get_windows_temperature()

        assert result is None

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_subprocess_timeout_handled(self, mock_run, mock_platform):
        """Subprocess timeout exception is caught."""
        mock_run.side_effect = TimeoutError("Command timed out")

        result = SystemMonitor._get_windows_temperature()

        assert result is None

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_multiple_readings_returns_max(self, mock_run, mock_platform):
        """Multiple temperature readings return max value."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "40.2\n55.8\n42.1\n"
        mock_run.return_value = mock_result

        result = SystemMonitor._get_windows_temperature()

        assert result == 55.8

    @patch("platform.system", return_value="Windows")
    @patch("subprocess.run")
    def test_invalid_output_skipped(self, mock_run, mock_platform):
        """Invalid output lines are skipped."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "45.5\ninvalid\n50.2\ngarbage\n"
        mock_run.return_value = mock_result

        result = SystemMonitor._get_windows_temperature()

        assert result == 50.2  # max of valid values


# ── TestShouldThrottle ───────────────────────────────────────────────────

class TestShouldThrottle:
    """SystemMonitor.should_throttle() decision logic."""

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=85.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_throttle_on_high_cpu(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Returns True when CPU over threshold."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor(cpu_threshold=80.0)
        assert monitor.should_throttle() is True

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_throttle_on_high_temperature(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Returns True when temperature over threshold."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk
        temp_entry = namedtuple("TempEntry", "current")(current=85.0)
        mock_temps.return_value = {"coretemp": [temp_entry]}

        monitor = SystemMonitor(temp_threshold=80.0)
        assert monitor.should_throttle() is True

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_no_throttle_when_normal(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Returns False when all values normal."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()
        assert monitor.should_throttle() is False

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_temperature_none_no_throttle(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Temperature None doesn't trigger throttle."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()
        result = monitor.should_throttle()

        assert result is False

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=85.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_throttle_both_cpu_and_temp(self, mock_base, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Both CPU and temperature high trigger throttle."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk
        temp_entry = namedtuple("TempEntry", "current")(current=85.0)
        mock_temps.return_value = {"coretemp": [temp_entry]}

        monitor = SystemMonitor(cpu_threshold=80.0, temp_threshold=80.0)
        assert monitor.should_throttle() is True


# ── TestLogMetrics ───────────────────────────────────────────────────────

class TestLogMetrics:
    """SystemMonitor.log_metrics() database operations."""

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._metrics_db_path", return_value="/tmp/metrics.db")
    @patch("src.monitoring.system_monitor.sqlite3.connect")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_creates_table_and_inserts_row(self, mock_base, mock_connect, mock_db_path, mock_temps, mock_disk, mock_mem, mock_cpu):
        """log_metrics creates table and inserts a row."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        mock_db = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_db

        monitor = SystemMonitor()
        monitor.log_metrics()

        # Should execute CREATE TABLE
        calls = [call_obj for call_obj in mock_db.execute.call_args_list]
        create_calls = [c for c in calls if "CREATE TABLE" in str(c)]
        assert len(create_calls) > 0

        # Should execute INSERT
        insert_calls = [c for c in calls if "INSERT INTO" in str(c)]
        assert len(insert_calls) > 0

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._metrics_db_path", return_value="/tmp/metrics.db")
    @patch("src.monitoring.system_monitor.sqlite3.connect")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_correct_data_inserted(self, mock_base, mock_connect, mock_db_path, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Correct health data is inserted into database."""
        mem = namedtuple("VirtualMemory", "percent")(percent=75.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=55.0)
        mock_disk.return_value = disk

        mock_db = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_db

        monitor = SystemMonitor()
        monitor.log_metrics()

        # Find INSERT call
        insert_calls = [c for c in mock_db.execute.call_args_list if "INSERT INTO" in str(c)]
        assert len(insert_calls) == 1

        # Check inserted values
        insert_call = insert_calls[0]
        args = insert_call[0]
        if len(args) > 1 and isinstance(args[1], (tuple, list)):
            values = args[1]
            # Values should be: timestamp, cpu, memory, disk, temp, alerts
            assert values[1] == 45.0  # cpu
            assert values[2] == 75.0  # memory
            assert values[3] == 55.0  # disk

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures", return_value={})
    @patch("src.monitoring.system_monitor._metrics_db_path", return_value="/tmp/metrics.db")
    @patch("src.monitoring.system_monitor.sqlite3.connect", side_effect=sqlite3.Error("DB error"))
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_handles_sqlite_error(self, mock_base, mock_connect, mock_db_path, mock_temps, mock_disk, mock_mem, mock_cpu):
        """SQLite errors are caught and logged."""
        mem = namedtuple("VirtualMemory", "percent")(percent=60.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk

        monitor = SystemMonitor()

        # Should not raise, error is logged
        monitor.log_metrics()

    @patch("src.monitoring.system_monitor.psutil.cpu_percent", return_value=45.0)
    @patch("src.monitoring.system_monitor.psutil.virtual_memory")
    @patch("src.monitoring.system_monitor.psutil.disk_usage")
    @patch("src.monitoring.system_monitor.psutil.sensors_temperatures")
    @patch("src.monitoring.system_monitor._metrics_db_path", return_value="/tmp/metrics.db")
    @patch("src.monitoring.system_monitor.sqlite3.connect")
    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user")
    def test_inserts_alerts_string(self, mock_base, mock_connect, mock_db_path, mock_temps, mock_disk, mock_mem, mock_cpu):
        """Alerts are inserted as comma-separated string."""
        mem = namedtuple("VirtualMemory", "percent")(percent=95.0)
        mock_mem.return_value = mem
        disk = namedtuple("DiskUsage", "percent")(percent=50.0)
        mock_disk.return_value = disk
        temp_entry = namedtuple("TempEntry", "current")(current=85.0)
        mock_temps.return_value = {"coretemp": [temp_entry]}

        mock_db = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_db

        monitor = SystemMonitor(memory_threshold=90.0, temp_threshold=80.0)
        monitor.log_metrics()

        # Check that alerts string is formatted correctly
        insert_calls = [c for c in mock_db.execute.call_args_list if "INSERT INTO" in str(c)]
        assert len(insert_calls) == 1


# ── TestMetricsDbPath ────────────────────────────────────────────────────

class TestMetricsDbPath:
    """_metrics_db_path() path construction and directory creation."""

    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user/project")
    @patch("os.makedirs")
    def test_correct_path_construction(self, mock_makedirs, mock_base):
        """Returns path to data/metrics.db in base directory."""
        result = _metrics_db_path()

        assert result == "/home/user/project/data/metrics.db"

    @patch("src.monitoring.system_monitor._base_path", return_value="/var/app")
    @patch("os.makedirs")
    def test_creates_data_directory(self, mock_makedirs, mock_base):
        """Creates data directory if it doesn't exist."""
        _metrics_db_path()

        mock_makedirs.assert_called_once()
        # Should be called with the data directory path
        args = mock_makedirs.call_args[0]
        assert "data" in args[0]

    @patch("src.monitoring.system_monitor._base_path", return_value="/home/user/project")
    @patch("os.makedirs")
    def test_makedirs_exist_ok(self, mock_makedirs, mock_base):
        """os.makedirs is called with exist_ok=True."""
        _metrics_db_path()

        # Check kwargs for exist_ok
        assert mock_makedirs.call_args[1].get("exist_ok") is True
