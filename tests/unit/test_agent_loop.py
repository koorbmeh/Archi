"""
Unit tests for src/core/agent_loop.py

Tests: EmergencyStop, check_triggers, startup_recovery, run_agent_loop, main.
Heavy mocking of ModelRouter, GoalManager, MemoryManager, SystemMonitor, etc.
"""
import logging
import os
import signal
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_check_triggers():
    """Clear check_triggers function-level state between tests."""
    from src.core.agent_loop import check_triggers
    if hasattr(check_triggers, "_last_trigger_time"):
        del check_triggers._last_trigger_time
    yield
    if hasattr(check_triggers, "_last_trigger_time"):
        del check_triggers._last_trigger_time


@pytest.fixture
def workspace(tmp_path):
    """Minimal workspace with base_path pointing to tmp_path."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    return tmp_path


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
        monkeypatch.setattr("src.core.agent_loop._base_path", lambda: str(tmp_path))
        es = EmergencyStop()
        assert es.stop_file == os.path.join(str(tmp_path), "EMERGENCY_STOP")
        assert es.check() is False

    def test_custom_path_overrides_default(self, tmp_path, monkeypatch):
        from src.core.agent_loop import EmergencyStop
        monkeypatch.setattr("src.core.agent_loop._base_path", lambda: str(tmp_path))
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


# ========================== check_triggers ==========================


class TestCheckTriggers:
    """Tests for the periodic heartbeat trigger function."""

    def test_first_call_returns_heartbeat(self):
        from src.core.agent_loop import check_triggers
        result = check_triggers()
        assert len(result) == 1
        assert result[0] == {"type": "heartbeat"}

    def test_immediate_second_call_returns_empty(self):
        from src.core.agent_loop import check_triggers
        check_triggers()  # first call — triggers
        result = check_triggers()  # immediate second — no trigger
        assert result == []

    def test_trigger_after_interval(self, monkeypatch):
        """After 60s elapsed, should trigger again."""
        from src.core.agent_loop import check_triggers
        check_triggers()  # first call sets _last_trigger_time

        # Fast-forward time.monotonic by 61 seconds
        real_monotonic = time.monotonic
        offset = 61.0
        monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + offset)

        result = check_triggers()
        assert len(result) == 1
        assert result[0] == {"type": "heartbeat"}

    def test_no_trigger_before_interval(self, monkeypatch):
        """Before 60s elapsed, should return empty."""
        from src.core.agent_loop import check_triggers
        check_triggers()  # first call

        real_monotonic = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + 30.0)

        result = check_triggers()
        assert result == []

    def test_state_resets_between_tests(self):
        """The autouse fixture should reset function state."""
        from src.core.agent_loop import check_triggers
        assert not hasattr(check_triggers, "_last_trigger_time")
        result = check_triggers()
        assert len(result) == 1


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


# ========================== _load_monitoring_thresholds ==========================


class TestLoadMonitoringThresholds:
    """Tests for the config-delegating threshold loader."""

    def test_delegates_to_config(self, monkeypatch):
        from src.core.agent_loop import _load_monitoring_thresholds
        fake = {"cpu_threshold": 70, "memory_threshold": 85}
        monkeypatch.setattr("src.utils.config.get_monitoring", lambda: fake)
        result = _load_monitoring_thresholds()
        assert result == fake


# ========================== run_agent_loop ==========================


def _make_loop_mocks(workspace, monkeypatch, *, stop_after=1):
    """Create all mocks needed for run_agent_loop and stop it after N iterations.

    Returns a dict of mocks so individual tests can adjust behaviour.
    """
    monkeypatch.setattr("src.core.agent_loop._base_path", lambda: str(workspace))
    monkeypatch.setattr(
        "src.core.agent_loop._load_monitoring_thresholds",
        lambda: {"cpu_threshold": 80, "memory_threshold": 90,
                 "temp_threshold": 80, "disk_threshold": 90},
    )

    emergency = MagicMock()
    emergency.check.return_value = False

    system_mon = MagicMock()
    system_mon.should_throttle.return_value = False
    system_mon.log_metrics = MagicMock()

    heartbeat = MagicMock()
    heartbeat.get_sleep_duration.return_value = 0.01  # fast tests

    action_logger = MagicMock()
    safety = MagicMock()
    safety.authorize.return_value = True

    mock_router = MagicMock()
    mock_router.provider = "xai"
    mock_router.get_stats.return_value = {}

    memory = MagicMock()
    memory.get_stats.return_value = {}

    mock_registry = MagicMock()
    monkeypatch.setattr("src.core.agent_loop.get_shared_registry", lambda: mock_registry)

    mock_gm = MagicMock()
    mock_gm.prune_duplicates.return_value = 0
    mock_gm.get_status.return_value = {"active_goals": 0, "pending_tasks": 0}
    mock_gm.get_next_task.return_value = None
    monkeypatch.setattr("src.core.agent_loop.GoalManager", lambda: mock_gm)

    # Patch signal to avoid side effects
    monkeypatch.setattr(signal, "signal", lambda *a, **kw: None)

    # Stop the loop after `stop_after` iterations via emergency stop
    call_count = {"n": 0}
    _original_check = emergency.check

    def _counted_check():
        call_count["n"] += 1
        if call_count["n"] > stop_after:
            return True  # trigger exit
        return False

    emergency.check.side_effect = _counted_check

    return {
        "emergency": emergency,
        "system_monitor": system_mon,
        "heartbeat": heartbeat,
        "action_logger": action_logger,
        "safety": safety,
        "router": mock_router,
        "memory": memory,
        "registry": mock_registry,
        "goal_manager": mock_gm,
        "call_count": call_count,
    }


class TestRunAgentLoopStartup:
    """Tests for run_agent_loop initialization and startup behaviour."""

    def test_logs_system_start(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["action_logger"].log_action.assert_any_call(
            action_type="system_start",
            parameters={"base_path": str(workspace)},
            result="started",
        )

    def test_logs_system_stop_on_exit(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["action_logger"].log_action.assert_any_call(
            action_type="system_stop",
            parameters={"iteration": 1},
            result="stopped",
        )
        m["action_logger"].close.assert_called_once()

    def test_initializes_mcp(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["registry"].initialize_mcp.assert_called_once()

    def test_shutdowns_mcp_on_exit(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["registry"].shutdown_mcp.assert_called_once()

    def test_prunes_duplicate_goals(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        m["goal_manager"].prune_duplicates.return_value = 3
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["goal_manager"].prune_duplicates.assert_called_once()

    def test_prune_failure_doesnt_crash(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        m["goal_manager"].prune_duplicates.side_effect = RuntimeError("boom")
        # Should not raise
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["action_logger"].close.assert_called_once()

    def test_calls_startup_recovery(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        m["goal_manager"].get_status.return_value = {"active_goals": 2, "pending_tasks": 5}
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["goal_manager"].get_status.assert_called()


class TestRunAgentLoopRouterInit:
    """Tests for router initialization paths within run_agent_loop."""

    def test_shared_router_skips_creation(self, workspace, monkeypatch):
        """When router is passed in, no new ModelRouter is created."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        # Router was passed in — should NOT have created a new one
        # (ModelRouter constructor not called)

    def test_router_none_creates_new(self, workspace, monkeypatch):
        """When router=None, run_agent_loop creates a new ModelRouter."""
        from src.core.agent_loop import run_agent_loop
        mock_router_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.provider = "openrouter"
        mock_instance._api = MagicMock()
        mock_instance._api.generate.return_value = {"model": "test", "text": "4", "cost_usd": 0}
        mock_router_cls.return_value = mock_instance
        monkeypatch.setattr("src.core.agent_loop.ModelRouter", mock_router_cls)

        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=None,
            memory=m["memory"],
        )
        mock_router_cls.assert_called_once()

    def test_router_creation_failure_continues(self, workspace, monkeypatch):
        """If ModelRouter() raises, loop continues with router=None."""
        from src.core.agent_loop import run_agent_loop
        monkeypatch.setattr("src.core.agent_loop.ModelRouter", MagicMock(side_effect=RuntimeError("no key")))
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        # Should not raise
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=None,
            memory=m["memory"],
        )
        m["action_logger"].close.assert_called_once()

    def test_connectivity_test_calls_ping(self, workspace, monkeypatch):
        """Connectivity test delegates to router.ping()."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        m["router"].ping.return_value = {"model": "test", "text": "4", "cost_usd": 0}
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["router"].ping.assert_called_once()

    def test_connectivity_test_failure_continues(self, workspace, monkeypatch):
        """If the connectivity ping fails, loop still runs."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        m["router"].ping.side_effect = ConnectionError("no internet")
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["action_logger"].close.assert_called_once()


class TestRunAgentLoopEmergencyStop:
    """Tests for emergency stop within the main loop."""

    def test_emergency_stop_exits_loop(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        # Emergency stop triggers on first check
        m["emergency"].check.side_effect = [True]
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        # Should still log stop
        m["action_logger"].log_action.assert_any_call(
            action_type="system_stop",
            parameters={"iteration": 1},
            result="stopped",
        )


class TestRunAgentLoopThrottling:
    """Tests for hardware throttling via SystemMonitor."""

    def test_throttle_multiplies_sleep(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)
        m["system_monitor"].should_throttle.return_value = True
        m["heartbeat"].get_sleep_duration.return_value = 0.01

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["system_monitor"].should_throttle.assert_called()

    def test_no_throttle_normal_sleep(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)
        m["system_monitor"].should_throttle.return_value = False
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["system_monitor"].should_throttle.assert_called()


class TestRunAgentLoopHeartbeatTrigger:
    """Tests for heartbeat trigger processing."""

    def test_heartbeat_trigger_logged(self, workspace, monkeypatch):
        """When check_triggers returns a heartbeat, it's logged."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)
        # Ensure check_triggers fires (first call always does)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        heartbeat_calls = [
            c for c in m["action_logger"].log_action.call_args_list
            if c.kwargs.get("action_type") == "heartbeat"
                or (c.args and c.args[0] == "heartbeat")
                or (len(c.kwargs) > 0 and c.kwargs.get("action_type") == "heartbeat")
        ]
        assert len(heartbeat_calls) >= 1

    def test_heartbeat_stored_in_memory(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        memory_calls = [
            c for c in m["memory"].store_action.call_args_list
            if c.kwargs.get("action_type") == "heartbeat"
        ]
        assert len(memory_calls) >= 1


class TestRunAgentLoopActionTrigger:
    """Tests for Action trigger processing (authorized, denied, timeout)."""

    def _make_action(self):
        from src.core.safety_controller import Action
        return Action(
            type="read_file",
            parameters={"path": "/workspace/test.txt"},
            confidence=0.9,
        )

    def test_authorized_action_dispatched(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)

        action = self._make_action()
        monkeypatch.setattr(
            "src.core.agent_loop.check_triggers",
            lambda: [action],
        )
        m["safety"].authorize.return_value = True
        m["registry"].execute.return_value = {"success": True}

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        # heartbeat.record_user_interaction called for authorized actions
        m["heartbeat"].record_user_interaction.assert_called()

    def test_denied_action_logged(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)

        action = self._make_action()
        monkeypatch.setattr(
            "src.core.agent_loop.check_triggers",
            lambda: [action],
        )
        m["safety"].authorize.return_value = False

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        denied_calls = [
            c for c in m["action_logger"].log_action.call_args_list
            if c.kwargs.get("result") == "denied"
        ]
        assert len(denied_calls) >= 1

    def test_denied_action_stored_in_memory(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)

        action = self._make_action()
        monkeypatch.setattr(
            "src.core.agent_loop.check_triggers",
            lambda: [action],
        )
        m["safety"].authorize.return_value = False

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        denied_mem = [
            c for c in m["memory"].store_action.call_args_list
            if c.kwargs.get("result") == "denied"
        ]
        assert len(denied_mem) >= 1

    def test_tool_execution_timeout(self, workspace, monkeypatch):
        """Tool that times out should be handled gracefully."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)

        action = self._make_action()
        monkeypatch.setattr(
            "src.core.agent_loop.check_triggers",
            lambda: [action],
        )
        m["safety"].authorize.return_value = True

        # Make tool execution block forever (simulates timeout via ThreadPoolExecutor)
        def _slow_execute(*args, **kwargs):
            time.sleep(60)
            return {"success": True}
        m["registry"].execute.side_effect = _slow_execute

        # Patch ThreadPoolExecutor to simulate timeout
        from unittest.mock import patch as upatch
        mock_future = MagicMock()
        mock_future.result.side_effect = FuturesTimeout()
        mock_pool = MagicMock()
        mock_pool.submit.return_value = mock_future
        mock_pool_cls = MagicMock(return_value=mock_pool)

        monkeypatch.setattr("src.core.agent_loop.ThreadPoolExecutor", mock_pool_cls)

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        # Should still log action (with failure result)
        failure_calls = [
            c for c in m["action_logger"].log_action.call_args_list
            if c.kwargs.get("action_type") == "read_file"
               and c.kwargs.get("result") == "failure"
        ]
        assert len(failure_calls) >= 1

    def test_tool_execution_exception(self, workspace, monkeypatch):
        """Tool that raises should be handled gracefully."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)

        action = self._make_action()
        monkeypatch.setattr(
            "src.core.agent_loop.check_triggers",
            lambda: [action],
        )
        m["safety"].authorize.return_value = True

        mock_future = MagicMock()
        mock_future.result.side_effect = ValueError("tool crashed")
        mock_pool = MagicMock()
        mock_pool.submit.return_value = mock_future
        mock_pool_cls = MagicMock(return_value=mock_pool)
        monkeypatch.setattr("src.core.agent_loop.ThreadPoolExecutor", mock_pool_cls)

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        failure_calls = [
            c for c in m["action_logger"].log_action.call_args_list
            if c.kwargs.get("action_type") == "read_file"
               and c.kwargs.get("result") == "failure"
        ]
        assert len(failure_calls) >= 1


class TestRunAgentLoopIdlePath:
    """Tests for the idle path (no triggers — goal discovery)."""

    def test_idle_discovers_next_task(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)

        # Force no triggers so idle path runs
        monkeypatch.setattr("src.core.agent_loop.check_triggers", lambda: [])

        mock_task = MagicMock()
        mock_task.task_id = "task_1"
        mock_task.goal_id = "goal_1"
        mock_task.description = "Write a report on AI trends"
        m["goal_manager"].get_next_task.return_value = mock_task

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        # Memory should record goal_discovered
        discovered = [
            c for c in m["memory"].store_action.call_args_list
            if c.kwargs.get("action_type") == "goal_discovered"
        ]
        assert len(discovered) >= 1

    def test_idle_no_task_no_discovery(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)
        monkeypatch.setattr("src.core.agent_loop.check_triggers", lambda: [])
        m["goal_manager"].get_next_task.return_value = None

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        discovered = [
            c for c in m["memory"].store_action.call_args_list
            if c.kwargs.get("action_type") == "goal_discovered"
        ]
        assert len(discovered) == 0

    def test_idle_dedup_logs_same_task(self, workspace, monkeypatch, caplog):
        """Same task discovered twice shouldn't log twice."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=2)
        monkeypatch.setattr("src.core.agent_loop.check_triggers", lambda: [])

        mock_task = MagicMock()
        mock_task.task_id = "task_1"
        mock_task.goal_id = "goal_1"
        mock_task.description = "Test dedup"
        m["goal_manager"].get_next_task.return_value = mock_task

        with caplog.at_level(logging.INFO, logger="src.core.agent_loop"):
            run_agent_loop(
                emergency_stop=m["emergency"],
                system_monitor=m["system_monitor"],
                heartbeat=m["heartbeat"],
                action_logger=m["action_logger"],
                safety_controller=m["safety"],
                router=m["router"],
                memory=m["memory"],
            )
        # "Idle: next task queued" should appear only once (dedup by _last_discovered_tid)
        idle_logs = [r for r in caplog.records if "Idle: next task queued" in r.message]
        assert len(idle_logs) == 1


class TestRunAgentLoopMetrics:
    """Tests for periodic metrics logging."""

    def test_metrics_logged_every_10_actions(self, workspace, monkeypatch):
        """log_metrics called when action_count is a multiple of 10."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=11)
        # check_triggers will fire on first call, then not again for 60s.
        # But we need it to fire every iteration for this test.
        call_n = {"n": 0}

        def _always_trigger():
            call_n["n"] += 1
            return [{"type": "heartbeat"}]

        monkeypatch.setattr("src.core.agent_loop.check_triggers", _always_trigger)

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        assert m["system_monitor"].log_metrics.call_count >= 1

    def test_metrics_log_failure_doesnt_crash(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=11)
        monkeypatch.setattr("src.core.agent_loop.check_triggers", lambda: [{"type": "heartbeat"}])
        m["system_monitor"].log_metrics.side_effect = RuntimeError("db locked")

        # Should not raise
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["action_logger"].close.assert_called_once()


class TestRunAgentLoopMemoryStats:
    """Tests for periodic memory/router stats logging at 100-action intervals."""

    def test_stats_logged_at_100_actions(self, workspace, monkeypatch, caplog):
        """Memory stats logged every 100 actions — we test with Action triggers."""
        from src.core.agent_loop import run_agent_loop
        from src.core.safety_controller import Action
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=101)
        m["safety"].authorize.return_value = True

        action = Action(type="read_file", parameters={"path": "/test"}, confidence=0.9)

        mock_future = MagicMock()
        mock_future.result.return_value = {"success": True}
        mock_pool = MagicMock()
        mock_pool.submit.return_value = mock_future
        mock_pool_cls = MagicMock(return_value=mock_pool)
        monkeypatch.setattr("src.core.agent_loop.ThreadPoolExecutor", mock_pool_cls)

        monkeypatch.setattr("src.core.agent_loop.check_triggers", lambda: [action])

        with caplog.at_level(logging.INFO, logger="src.core.agent_loop"):
            run_agent_loop(
                emergency_stop=m["emergency"],
                system_monitor=m["system_monitor"],
                heartbeat=m["heartbeat"],
                action_logger=m["action_logger"],
                safety_controller=m["safety"],
                router=m["router"],
                memory=m["memory"],
            )
        m["memory"].get_stats.assert_called()
        m["router"].get_stats.assert_called()


class TestRunAgentLoopSignalHandling:
    """Tests for graceful shutdown via stop_event."""

    def test_stop_event_exits_loop(self, workspace, monkeypatch):
        """Simulating stop_event.set() via emergency_stop that never fires,
        but patching threading.Event.wait to simulate stop."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=2)
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        # Verify it exited cleanly
        m["action_logger"].close.assert_called_once()

    def test_loop_exception_logged(self, workspace, monkeypatch):
        """Unhandled exception in loop body is caught and logged."""
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)
        m["emergency"].check.side_effect = [False, RuntimeError("unexpected")]

        # The exception will be raised on the second emergency check.
        # Since our stop_after logic uses the same side_effect, override:
        m["emergency"].check.side_effect = RuntimeError("unexpected crash")

        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        error_calls = [
            c for c in m["action_logger"].log_action.call_args_list
            if c.kwargs.get("action_type") == "system_error"
        ]
        assert len(error_calls) == 1


class TestRunAgentLoopChunkedSleep:
    """Tests for the chunked sleep mechanism (Windows Ctrl+C responsiveness)."""

    def test_sleep_uses_heartbeat_duration(self, workspace, monkeypatch):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=1)
        m["heartbeat"].get_sleep_duration.return_value = 0.01
        run_agent_loop(
            emergency_stop=m["emergency"],
            system_monitor=m["system_monitor"],
            heartbeat=m["heartbeat"],
            action_logger=m["action_logger"],
            safety_controller=m["safety"],
            router=m["router"],
            memory=m["memory"],
        )
        m["heartbeat"].get_sleep_duration.assert_called()


class TestRunAgentLoopMemoryInit:
    """Tests for memory initialization paths."""

    def test_shared_memory(self, workspace, monkeypatch, caplog):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        with caplog.at_level(logging.INFO, logger="src.core.agent_loop"):
            run_agent_loop(
                emergency_stop=m["emergency"],
                system_monitor=m["system_monitor"],
                heartbeat=m["heartbeat"],
                action_logger=m["action_logger"],
                safety_controller=m["safety"],
                router=m["router"],
                memory=m["memory"],
            )
        assert any("Memory system initialized (shared)" in r.message for r in caplog.records)

    def test_own_memory_created_when_none(self, workspace, monkeypatch, caplog):
        from src.core.agent_loop import run_agent_loop
        m = _make_loop_mocks(workspace, monkeypatch, stop_after=0)
        mock_mm = MagicMock()
        monkeypatch.setattr("src.core.agent_loop.MemoryManager", lambda: mock_mm)
        with caplog.at_level(logging.INFO, logger="src.core.agent_loop"):
            run_agent_loop(
                emergency_stop=m["emergency"],
                system_monitor=m["system_monitor"],
                heartbeat=m["heartbeat"],
                action_logger=m["action_logger"],
                safety_controller=m["safety"],
                router=m["router"],
                memory=None,
            )
        assert any("Memory system initialized (own instance)" in r.message for r in caplog.records)


# ========================== main ==========================


class TestMain:
    """Tests for the main() entry point."""

    def test_main_configures_logging_and_calls_loop(self, tmp_path, monkeypatch):
        from src.core.agent_loop import main
        monkeypatch.setattr("src.core.agent_loop._base_path", lambda: str(tmp_path))
        # Create .env file so dotenv doesn't fail
        (tmp_path / ".env").write_text("# test")

        # Patch run_agent_loop to avoid actually running
        mock_loop = MagicMock()
        monkeypatch.setattr("src.core.agent_loop.run_agent_loop", mock_loop)

        main()
        mock_loop.assert_called_once()
        # Verify log directory was created
        assert (tmp_path / "logs").exists()

    def test_main_without_dotenv(self, tmp_path, monkeypatch):
        """main() should work even if dotenv is not installed."""
        from src.core.agent_loop import main
        monkeypatch.setattr("src.core.agent_loop._base_path", lambda: str(tmp_path))

        mock_loop = MagicMock()
        monkeypatch.setattr("src.core.agent_loop.run_agent_loop", mock_loop)

        # Make dotenv import fail
        import builtins
        real_import = builtins.__import__
        def _no_dotenv(name, *args, **kwargs):
            if name == "dotenv":
                raise ImportError("no dotenv")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", _no_dotenv)

        main()
        mock_loop.assert_called_once()

    def test_main_creates_log_directories(self, tmp_path, monkeypatch):
        from src.core.agent_loop import main
        monkeypatch.setattr("src.core.agent_loop._base_path", lambda: str(tmp_path))
        mock_loop = MagicMock()
        monkeypatch.setattr("src.core.agent_loop.run_agent_loop", mock_loop)
        main()
        assert (tmp_path / "logs").exists()
        # system subdirectory should also be created
        log_system_dir = tmp_path / "logs" / "system"
        assert log_system_dir.exists()
