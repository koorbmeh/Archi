"""
Unit tests for src/core/agent_loop.py

Tests: EmergencyStop, startup_recovery.
agent_loop.py is now a thin shim that re-exports EmergencyStop from
src.core.heartbeat and provides startup_recovery.

Heavy mocking of ModelRouter, GoalManager, MemoryManager, SystemMonitor, etc.
"""
import logging
import os
from unittest.mock import MagicMock

import pytest

# ========================== EmergencyStop ==========================


class TestEmergencyStop:
    """Tests for the EmergencyStop sentinel-file checker."""

    def test_no_file_returns_false(self, tmp_path):
        from src.core.agent_loop import EmergencyStop
        es = EmergencyStop(stop_file_path=str(tmp_path / "EMERGENCY_STOP"))
        assert es.check() is False

    def test_file_present_returns_true(self, tmp_path):
        from src.core.agent_loop import EmergencyStop
        stop_file = tmp_path / "EMERGENCY_STOP"
        stop_file.write_text("stop")
        es = EmergencyStop(stop_file_path=str(stop_file))
        assert es.check() is True

    def test_default_path_uses_base_path(self, tmp_path, monkeypatch):
        from src.core.agent_loop import EmergencyStop
        monkeypatch.setattr("src.core.heartbeat._base_path_str", lambda: str(tmp_path))
        es = EmergencyStop()
        assert es.stop_file == os.path.join(str(tmp_path), "EMERGENCY_STOP")
        assert es.check() is False

    def test_custom_path_overrides_default(self, tmp_path, monkeypatch):
        from src.core.agent_loop import EmergencyStop
        monkeypatch.setattr("src.core.heartbeat._base_path_str", lambda: str(tmp_path))
        custom = str(tmp_path / "custom_stop")
        es = EmergencyStop(stop_file_path=custom)
        assert es.stop_file == custom

    def test_oserror_returns_false(self, monkeypatch):
        """OSError during isfile check should return False gracefully."""
        from src.core.agent_loop import EmergencyStop
        es = EmergencyStop(stop_file_path="/nonexistent/path/STOP")
        monkeypatch.setattr("os.path.isfile", lambda p: (_ for _ in ()).throw(OSError("fail")))
        assert es.check() is False

    def test_repeated_checks(self, tmp_path):
        """check() can be called multiple times."""
        from src.core.agent_loop import EmergencyStop
        stop_file = tmp_path / "EMERGENCY_STOP"
        es = EmergencyStop(stop_file_path=str(stop_file))
        assert es.check() is False
        stop_file.write_text("stop")
        assert es.check() is True
        stop_file.unlink()
        assert es.check() is False

    def test_emergency_stop_can_be_imported_from_heartbeat(self):
        """EmergencyStop should be importable from both agent_loop and heartbeat."""
        from src.core.agent_loop import EmergencyStop as ES_AL
        from src.core.heartbeat import EmergencyStop as ES_HB
        # They should be the same class
        assert ES_AL is ES_HB


# ========================== startup_recovery ==========================


class TestStartupRecovery:
    """Tests for the startup_recovery log-only function."""

    def test_logs_active_goals(self, caplog):
        from src.core.agent_loop import startup_recovery
        mock_gm = MagicMock()
        mock_gm.get_status.return_value = {"active_goals": 3, "pending_tasks": 7}
        with caplog.at_level(logging.INFO, logger="src.core.agent_loop"):
            startup_recovery(mock_gm)
        assert any("Goals: 3 active, 7 pending" in r.message for r in caplog.records)

    def test_no_active_goals_no_goal_line(self, caplog):
        from src.core.agent_loop import startup_recovery
        mock_gm = MagicMock()
        mock_gm.get_status.return_value = {"active_goals": 0, "pending_tasks": 0}
        with caplog.at_level(logging.INFO, logger="src.core.agent_loop"):
            startup_recovery(mock_gm)
        assert not any("Goals:" in r.message for r in caplog.records)

    def test_exception_logged_as_warning(self, caplog):
        from src.core.agent_loop import startup_recovery
        mock_gm = MagicMock()
        mock_gm.get_status.side_effect = RuntimeError("db locked")
        with caplog.at_level(logging.WARNING, logger="src.core.agent_loop"):
            startup_recovery(mock_gm)
        assert any("goal status check failed" in r.message for r in caplog.records)

    def test_always_logs_complete(self, caplog):
        from src.core.agent_loop import startup_recovery
        mock_gm = MagicMock()
        mock_gm.get_status.return_value = {}
        with caplog.at_level(logging.INFO, logger="src.core.agent_loop"):
            startup_recovery(mock_gm)
        assert any("Startup recovery complete" in r.message for r in caplog.records)
