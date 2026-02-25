"""Unit tests for src/service/archi_service.py."""

import logging
import os
import signal
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.service.archi_service import ArchiService, _set_process_name


# ── TestArchiServiceInit ────────────────────────────────────────────


class TestArchiServiceInit:
    def test_default_state(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        assert svc.running is False
        assert svc.heartbeat is None
        assert svc.core_goal_manager is None
        assert svc.discord_bot_thread is None
        assert svc.voice_interface is None
        assert svc._shared_router is None
        assert isinstance(svc._stop_event, threading.Event)
        assert not svc._stop_event.is_set()


# ── TestLoadEnv ─────────────────────────────────────────────────────


class TestLoadEnv:
    def test_loads_env_when_dotenv_available(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_KEY=test_value\n")
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        with patch("src.service.archi_service._root", tmp_path), \
             patch("dotenv.load_dotenv") as mock_load:
            svc._load_env()
            mock_load.assert_called_once()

    def test_handles_missing_dotenv(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        with patch("src.service.archi_service._root", Path("/nonexistent")), \
             patch.dict("sys.modules", {"dotenv": None}):
            # Should not raise
            svc._load_env()


# ── TestStartupRecovery ─────────────────────────────────────────────


class TestStartupRecovery:
    def test_with_goal_manager(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        mock_gm = MagicMock()
        mock_gm.prune_duplicates.return_value = 2
        svc.core_goal_manager = mock_gm
        svc._shared_router = None

        with patch("src.core.agent_loop.startup_recovery") as mock_sr:
            svc._startup_recovery()
        mock_gm.prune_duplicates.assert_called_once()
        mock_sr.assert_called_once_with(mock_gm)

    def test_prune_exception_handled(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        mock_gm = MagicMock()
        mock_gm.prune_duplicates.side_effect = Exception("db error")
        svc.core_goal_manager = mock_gm
        svc._shared_router = None

        with patch("src.core.agent_loop.startup_recovery"):
            svc._startup_recovery()  # Should not raise

    def test_router_connectivity_test(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        mock_router = MagicMock()
        mock_router.ping.return_value = {
            "model": "grok", "text": "4", "cost_usd": 0.001
        }
        svc._shared_router = mock_router
        svc.core_goal_manager = None

        svc._startup_recovery()
        mock_router.ping.assert_called_once()

    def test_no_goal_manager_no_crash(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        svc.core_goal_manager = None
        svc._shared_router = None
        svc._startup_recovery()  # Should not raise


# ── TestInitializeHeartbeat ─────────────────────────────────────────


class TestInitializeHeartbeat:
    def test_creates_heartbeat_and_goal_manager(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()

        mock_router = MagicMock()
        with patch("src.models.router.ModelRouter", return_value=mock_router), \
             patch("src.service.archi_service.Heartbeat") as MockHB, \
             patch("src.service.archi_service.CoreGoalManager") as MockGM, \
             patch("src.utils.config.get_heartbeat_config", return_value={
                 "interval": 900, "min_interval": 300, "max_interval": 7200
             }):
            mock_hb_instance = MagicMock()
            MockHB.return_value = mock_hb_instance
            mock_gm_instance = MagicMock()
            MockGM.return_value = mock_gm_instance

            svc._initialize_heartbeat()

        assert svc.core_goal_manager is mock_gm_instance
        assert svc.heartbeat is mock_hb_instance
        assert svc._shared_router is mock_router
        mock_hb_instance.set_router.assert_called_once_with(mock_router)
        mock_hb_instance.enable_autonomous_mode.assert_called_once_with(mock_gm_instance)

    def test_no_router_available(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()

        with patch("src.models.router.ModelRouter", side_effect=Exception("no key")), \
             patch("src.core.heartbeat.Heartbeat") as MockHB, \
             patch("src.core.goal_manager.GoalManager"), \
             patch("src.utils.config.get_heartbeat_config", return_value={
                 "interval": 900, "min_interval": 300, "max_interval": 7200
             }):
            mock_hb_instance = MagicMock()
            MockHB.return_value = mock_hb_instance

            # Need to also mock send_notification since it tries to warn user
            with patch("src.interfaces.discord_bot.send_notification", return_value=False):
                svc._initialize_heartbeat()

        assert svc._shared_router is None
        mock_hb_instance.set_router.assert_not_called()


# ── TestInstallSignalHandlers ───────────────────────────────────────


class TestInstallSignalHandlers:
    def test_installs_handlers(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()

        with patch("signal.signal") as mock_signal:
            svc._install_signal_handlers()
            assert mock_signal.call_count == 2

    def test_handles_unsupported_signals(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()

        with patch("signal.signal", side_effect=ValueError("not supported")):
            svc._install_signal_handlers()  # Should not raise


# ── TestStop ────────────────────────────────────────────────────────


class TestStop:
    def test_not_running_returns_early(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        svc.running = False
        svc.stop()  # Should return immediately

    def test_stops_all_components(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        svc.running = True

        mock_router = MagicMock()
        svc._shared_router = mock_router

        mock_hb = MagicMock()
        svc.heartbeat = mock_hb

        mock_voice = MagicMock()
        svc.voice_interface = mock_voice

        mock_gm = MagicMock()
        svc.core_goal_manager = mock_gm

        with patch("src.core.plan_executor.signal_task_cancellation") as mock_cancel, \
             patch("src.interfaces.discord_bot.close_bot") as mock_close_bot, \
             patch("src.tools.tool_registry.get_shared_registry") as mock_reg, \
             patch("src.tools.browser_control._cleanup_all_browsers"), \
             patch("src.monitoring.cost_tracker.get_cost_tracker") as mock_ct:
            mock_ct_instance = MagicMock()
            mock_ct_instance.get_summary.return_value = {"total_cost": 0.05}
            mock_ct.return_value = mock_ct_instance

            svc.stop()

        assert svc.running is False
        mock_cancel.assert_called_once_with("service_shutdown")
        mock_router.close.assert_called_once()
        mock_voice.stop_listening.assert_called_once()
        mock_hb.stop.assert_called_once()
        mock_gm.save_state.assert_called_once()

    def test_handles_router_close_failure(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        svc.running = True
        svc._shared_router = MagicMock()
        svc._shared_router.close.side_effect = Exception("router close error")

        with patch("src.core.plan_executor.signal_task_cancellation"), \
             patch("src.interfaces.discord_bot.close_bot"), \
             patch("src.tools.tool_registry.get_shared_registry") as mock_reg, \
             patch("src.tools.browser_control._cleanup_all_browsers"), \
             patch("src.monitoring.cost_tracker.get_cost_tracker") as mock_ct:
            mock_ct_instance = MagicMock()
            mock_ct_instance.get_summary.return_value = {"total_cost": 0}
            mock_ct.return_value = mock_ct_instance

            svc.stop()  # Should not raise despite router failure

        assert svc.running is False

    def test_discord_thread_join_timeout(self):
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()
        svc.running = True

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True  # Simulate stuck thread
        svc.discord_bot_thread = mock_thread

        with patch("src.core.plan_executor.signal_task_cancellation"), \
             patch("src.interfaces.discord_bot.close_bot"), \
             patch("src.tools.tool_registry.get_shared_registry") as mock_reg, \
             patch("src.monitoring.cost_tracker.get_cost_tracker") as mock_ct:
            mock_ct_instance = MagicMock()
            mock_ct_instance.get_summary.return_value = {"total_cost": 0}
            mock_ct.return_value = mock_ct_instance

            svc.stop()

        mock_thread.join.assert_called_once_with(timeout=8)


# ── TestSetProcessName ──────────────────────────────────────────────


class TestSetProcessName:
    def test_handles_missing_setproctitle(self):
        with patch.dict("sys.modules", {"setproctitle": None}):
            _set_process_name("Archi")  # Should not raise

    def test_with_setproctitle(self):
        mock_spt = MagicMock()
        with patch.dict("sys.modules", {"setproctitle": mock_spt}):
            _set_process_name("Archi")

    def test_windows_path(self):
        with patch("sys.platform", "win32"):
            try:
                _set_process_name("Archi")
            except Exception:
                pass  # ctypes may not be available but shouldn't crash

    def test_non_windows(self):
        with patch("sys.platform", "linux"):
            _set_process_name("Archi")  # Should not raise


# ── TestStart ───────────────────────────────────────────────────────


class TestStart:
    def test_start_calls_initialize_and_stop(self):
        """Test that start() initializes components and calls stop in finally."""
        with patch("src.service.archi_service.base_path", return_value="/fake"):
            svc = ArchiService()

        stop_called = []
        original_stop = svc.stop

        def _mock_stop():
            stop_called.append(True)
            svc.running = False

        svc.stop = _mock_stop

        with patch.object(svc, "_load_env"), \
             patch.object(svc, "_initialize_heartbeat"), \
             patch.object(svc, "_startup_recovery"), \
             patch.object(svc, "_install_signal_handlers"), \
             patch("os.makedirs"), \
             patch("src.service.archi_service.base_path", return_value="/fake"), \
             patch("src.service.archi_service.health_check") as mock_hc, \
             patch("src.tools.tool_registry.get_shared_registry") as mock_reg:
            mock_hc.check_all.return_value = {
                "overall_status": "healthy", "summary": "All good"
            }
            # Set stop event to break the main loop immediately
            svc._stop_event.set()
            svc.start()

        assert len(stop_called) == 1
