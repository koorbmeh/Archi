"""Tests for src/monitoring/health_check.py — HealthCheck system."""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

from src.monitoring.health_check import (
    HealthCheck,
    STATUS_HEALTHY,
    STATUS_DEGRADED,
    STATUS_UNHEALTHY,
    STATUS_UNKNOWN,
)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit(unittest.TestCase):

    def test_initial_state(self):
        hc = HealthCheck()
        self.assertIsNone(hc.last_check)
        self.assertIsNone(hc.last_status)


# ---------------------------------------------------------------------------
# _check_system_resources
# ---------------------------------------------------------------------------

class TestCheckSystemResources(unittest.TestCase):

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    @patch("src.monitoring.health_check.psutil")
    def test_healthy_when_all_below_threshold(self, mock_psutil, _):
        mock_psutil.cpu_percent.return_value = 30.0
        mock_mem = MagicMock()
        mock_mem.percent = 40.0
        mock_psutil.virtual_memory.return_value = mock_mem
        mock_disk = MagicMock()
        mock_disk.percent = 50.0
        mock_psutil.disk_usage.return_value = mock_disk

        with patch("src.monitoring.health_check.get_monitoring", create=True) as mock_mon:
            mock_mon.return_value = {"cpu_threshold": 80, "memory_threshold": 80, "disk_threshold": 90}
            # Need to patch the import inside the method
            with patch.dict("sys.modules", {"src.utils.config": MagicMock(get_monitoring=mock_mon)}):
                hc = HealthCheck()
                result = hc._check_system_resources()
                self.assertEqual(result["status"], STATUS_HEALTHY)
                self.assertEqual(result["issues"], [])

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    @patch("src.monitoring.health_check.psutil")
    def test_degraded_when_one_threshold_exceeded(self, mock_psutil, _):
        mock_psutil.cpu_percent.return_value = 95.0
        mock_mem = MagicMock()
        mock_mem.percent = 40.0
        mock_psutil.virtual_memory.return_value = mock_mem
        mock_disk = MagicMock()
        mock_disk.percent = 50.0
        mock_psutil.disk_usage.return_value = mock_disk

        with patch("src.utils.config.get_monitoring", return_value={
            "cpu_threshold": 80, "memory_threshold": 80, "disk_threshold": 90
        }):
            hc = HealthCheck()
            result = hc._check_system_resources()
            self.assertEqual(result["status"], STATUS_DEGRADED)
            self.assertEqual(len(result["issues"]), 1)
            self.assertIn("CPU", result["issues"][0])

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    @patch("src.monitoring.health_check.psutil")
    def test_unhealthy_when_multiple_thresholds_exceeded(self, mock_psutil, _):
        mock_psutil.cpu_percent.return_value = 95.0
        mock_mem = MagicMock()
        mock_mem.percent = 95.0
        mock_psutil.virtual_memory.return_value = mock_mem
        mock_disk = MagicMock()
        mock_disk.percent = 50.0
        mock_psutil.disk_usage.return_value = mock_disk

        with patch("src.utils.config.get_monitoring", return_value={
            "cpu_threshold": 80, "memory_threshold": 80, "disk_threshold": 90
        }):
            hc = HealthCheck()
            result = hc._check_system_resources()
            self.assertEqual(result["status"], STATUS_UNHEALTHY)
            self.assertGreater(len(result["issues"]), 1)

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    @patch("src.monitoring.health_check.psutil")
    def test_unknown_on_exception(self, mock_psutil, _):
        mock_psutil.cpu_percent.side_effect = Exception("psutil error")
        hc = HealthCheck()
        result = hc._check_system_resources()
        self.assertEqual(result["status"], STATUS_UNKNOWN)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# _check_models
# ---------------------------------------------------------------------------

class TestCheckModels(unittest.TestCase):

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "fake-key"})
    def test_healthy_when_api_reachable(self, _):
        hc = HealthCheck()
        mock_client = MagicMock()
        mock_client.generate.return_value = {"success": True}
        with patch("src.models.openrouter_client.OpenRouterClient", return_value=mock_client):
            result = hc._check_models()
            self.assertEqual(result["status"], STATUS_HEALTHY)
            self.assertTrue(result["api_available"])
            self.assertTrue(result["api_reachable"])

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "fake-key"})
    def test_degraded_when_api_unreachable(self, _):
        hc = HealthCheck()
        mock_client = MagicMock()
        mock_client.generate.return_value = {"success": False, "error": "timeout"}
        with patch("src.models.openrouter_client.OpenRouterClient", return_value=mock_client):
            result = hc._check_models()
            self.assertEqual(result["status"], STATUS_DEGRADED)
            self.assertTrue(result["api_available"])
            self.assertFalse(result["api_reachable"])

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    @patch.dict("os.environ", {}, clear=True)
    def test_unhealthy_when_no_api_key(self, _):
        hc = HealthCheck()
        # Patch dotenv to not load anything
        with patch.dict("sys.modules", {"dotenv": None}):
            result = hc._check_models()
            self.assertEqual(result["status"], STATUS_UNHEALTHY)
            self.assertFalse(result["api_available"])

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    def test_unknown_on_exception(self, _):
        hc = HealthCheck()
        with patch.dict("os.environ", {}, clear=True):
            with patch("os.environ.get", side_effect=Exception("env error")):
                result = hc._check_models()
                self.assertEqual(result["status"], STATUS_UNKNOWN)


# ---------------------------------------------------------------------------
# _check_cache
# ---------------------------------------------------------------------------

class TestCheckCache(unittest.TestCase):

    def test_healthy_cache(self):
        hc = HealthCheck()
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {
            "hit_rate_percent": 50.0,
            "cached_entries": 10,
            "total_queries": 20,
        }
        mock_cache._max_size = 1000
        with patch("src.models.cache.QueryCache", return_value=mock_cache):
            result = hc._check_cache()
            self.assertEqual(result["status"], STATUS_HEALTHY)
            self.assertEqual(result["hit_rate"], 50.0)

    def test_degraded_low_hit_rate(self):
        hc = HealthCheck()
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {
            "hit_rate_percent": 5.0,
            "cached_entries": 10,
            "total_queries": 100,
        }
        mock_cache._max_size = 1000
        with patch("src.models.cache.QueryCache", return_value=mock_cache):
            result = hc._check_cache()
            self.assertEqual(result["status"], STATUS_DEGRADED)
            self.assertTrue(any("hit rate" in i.lower() for i in result["issues"]))

    def test_degraded_cache_nearly_full(self):
        hc = HealthCheck()
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {
            "hit_rate_percent": 80.0,
            "cached_entries": 950,
            "total_queries": 1000,
        }
        mock_cache._max_size = 1000
        with patch("src.models.cache.QueryCache", return_value=mock_cache):
            result = hc._check_cache()
            self.assertEqual(result["status"], STATUS_DEGRADED)
            self.assertTrue(any("nearly full" in i.lower() for i in result["issues"]))

    def test_no_degradation_with_few_queries(self):
        """Low hit rate doesn't trigger degradation if fewer than 10 queries."""
        hc = HealthCheck()
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {
            "hit_rate_percent": 0.0,
            "cached_entries": 0,
            "total_queries": 5,
        }
        mock_cache._max_size = 1000
        with patch("src.models.cache.QueryCache", return_value=mock_cache):
            result = hc._check_cache()
            self.assertEqual(result["status"], STATUS_HEALTHY)

    def test_unknown_on_exception(self):
        hc = HealthCheck()
        with patch("src.models.cache.QueryCache", side_effect=Exception("cache error")):
            result = hc._check_cache()
            self.assertEqual(result["status"], STATUS_UNKNOWN)


# ---------------------------------------------------------------------------
# _check_storage
# ---------------------------------------------------------------------------

class TestCheckStorage(unittest.TestCase):

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    def test_healthy_when_data_dir_exists(self, _):
        hc = HealthCheck()
        with patch("pathlib.Path.exists", return_value=True):
            result = hc._check_storage()
            self.assertEqual(result["status"], STATUS_HEALTHY)
            self.assertTrue(result["data_dir_exists"])

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    def test_creates_data_dir_if_missing(self, _):
        hc = HealthCheck()
        with patch("pathlib.Path.exists", return_value=False):
            with patch("pathlib.Path.mkdir") as mock_mkdir:
                result = hc._check_storage()
                mock_mkdir.assert_called_once()

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    def test_unhealthy_when_mkdir_fails(self, _):
        hc = HealthCheck()
        with patch("pathlib.Path.exists", return_value=False):
            with patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
                result = hc._check_storage()
                self.assertEqual(result["status"], STATUS_UNHEALTHY)

    @patch("src.monitoring.health_check._base_path", return_value="/tmp")
    def test_unknown_on_exception(self, _):
        hc = HealthCheck()
        with patch("pathlib.Path.exists", side_effect=Exception("unexpected")):
            # The _base_path() is called before Path.exists, so we need the error in Path
            pass
        # Force exception by patching _base_path to raise
        with patch("src.monitoring.health_check._base_path", side_effect=Exception("path error")):
            result = hc._check_storage()
            self.assertEqual(result["status"], STATUS_UNKNOWN)


# ---------------------------------------------------------------------------
# _check_monitoring
# ---------------------------------------------------------------------------

class TestCheckMonitoring(unittest.TestCase):

    def test_healthy_when_under_budget(self):
        hc = HealthCheck()
        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {
            "allowed": True, "daily_spent": 1.0, "daily_limit": 5.0,
        }
        with patch("src.monitoring.cost_tracker.get_cost_tracker", return_value=mock_tracker):
            with patch("src.utils.config.get_monitoring", return_value={"budget_warning_pct": 80}):
                result = hc._check_monitoring()
                self.assertEqual(result["status"], STATUS_HEALTHY)

    def test_degraded_when_budget_exceeded(self):
        hc = HealthCheck()
        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {
            "allowed": False, "reason": "daily_exceeded",
            "daily_spent": 6.0, "daily_limit": 5.0,
        }
        with patch("src.monitoring.cost_tracker.get_cost_tracker", return_value=mock_tracker):
            with patch("src.utils.config.get_monitoring", return_value={"budget_warning_pct": 80}):
                result = hc._check_monitoring()
                self.assertEqual(result["status"], STATUS_DEGRADED)
                self.assertTrue(any("budget" in i.lower() for i in result["issues"]))

    def test_degraded_when_nearing_budget(self):
        hc = HealthCheck()
        mock_tracker = MagicMock()
        mock_tracker.check_budget.return_value = {
            "allowed": True, "daily_spent": 4.5, "daily_limit": 5.0,
        }
        with patch("src.monitoring.cost_tracker.get_cost_tracker", return_value=mock_tracker):
            with patch("src.utils.config.get_monitoring", return_value={"budget_warning_pct": 80}):
                result = hc._check_monitoring()
                self.assertEqual(result["status"], STATUS_DEGRADED)

    def test_unknown_on_exception(self):
        hc = HealthCheck()
        with patch("src.monitoring.cost_tracker.get_cost_tracker", side_effect=Exception("tracker error")):
            result = hc._check_monitoring()
            self.assertEqual(result["status"], STATUS_UNKNOWN)


# ---------------------------------------------------------------------------
# _determine_overall_status
# ---------------------------------------------------------------------------

class TestDetermineOverallStatus(unittest.TestCase):

    def setUp(self):
        self.hc = HealthCheck()

    def test_all_healthy(self):
        checks = {
            "a": {"status": STATUS_HEALTHY},
            "b": {"status": STATUS_HEALTHY},
        }
        self.assertEqual(self.hc._determine_overall_status(checks), STATUS_HEALTHY)

    def test_any_unhealthy_is_unhealthy(self):
        checks = {
            "a": {"status": STATUS_HEALTHY},
            "b": {"status": STATUS_UNHEALTHY},
        }
        self.assertEqual(self.hc._determine_overall_status(checks), STATUS_UNHEALTHY)

    def test_degraded_without_unhealthy(self):
        checks = {
            "a": {"status": STATUS_HEALTHY},
            "b": {"status": STATUS_DEGRADED},
        }
        self.assertEqual(self.hc._determine_overall_status(checks), STATUS_DEGRADED)

    def test_unknown_without_unhealthy_or_degraded(self):
        checks = {
            "a": {"status": STATUS_HEALTHY},
            "b": {"status": STATUS_UNKNOWN},
        }
        self.assertEqual(self.hc._determine_overall_status(checks), STATUS_UNKNOWN)

    def test_unhealthy_takes_priority_over_degraded(self):
        checks = {
            "a": {"status": STATUS_DEGRADED},
            "b": {"status": STATUS_UNHEALTHY},
        }
        self.assertEqual(self.hc._determine_overall_status(checks), STATUS_UNHEALTHY)

    def test_empty_checks(self):
        self.assertEqual(self.hc._determine_overall_status({}), STATUS_HEALTHY)

    def test_non_dict_values_ignored(self):
        checks = {"a": "not a dict", "b": {"status": STATUS_HEALTHY}}
        self.assertEqual(self.hc._determine_overall_status(checks), STATUS_HEALTHY)


# ---------------------------------------------------------------------------
# _create_summary
# ---------------------------------------------------------------------------

class TestCreateSummary(unittest.TestCase):

    def setUp(self):
        self.hc = HealthCheck()

    def test_all_ok_summary(self):
        checks = {
            "system": {"status": STATUS_HEALTHY},
            "models": {"status": STATUS_HEALTHY},
        }
        summary = self.hc._create_summary(checks)
        self.assertIn("All systems operational", summary)

    def test_issues_included_in_summary(self):
        checks = {
            "system": {"status": STATUS_DEGRADED, "issues": ["CPU high"]},
            "models": {"status": STATUS_HEALTHY},
        }
        summary = self.hc._create_summary(checks)
        self.assertIn("CPU high", summary)
        self.assertIn("degraded", summary)

    def test_error_in_summary(self):
        checks = {
            "system": {"status": STATUS_UNKNOWN, "error": "psutil missing"},
        }
        summary = self.hc._create_summary(checks)
        self.assertIn("psutil missing", summary)

    def test_empty_checks_summary(self):
        summary = self.hc._create_summary({})
        self.assertEqual(summary, "No checks ran")


# ---------------------------------------------------------------------------
# check_all integration
# ---------------------------------------------------------------------------

class TestCheckAll(unittest.TestCase):

    @patch.object(HealthCheck, "_check_system_resources", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_models", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_cache", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_storage", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_monitoring", return_value={"status": STATUS_HEALTHY, "issues": []})
    def test_check_all_returns_complete_structure(self, *_):
        hc = HealthCheck()
        result = hc.check_all()
        self.assertIn("timestamp", result)
        self.assertIn("overall_status", result)
        self.assertIn("checks", result)
        self.assertIn("summary", result)
        self.assertEqual(result["overall_status"], STATUS_HEALTHY)
        self.assertIsNotNone(hc.last_check)
        self.assertEqual(hc.last_status, STATUS_HEALTHY)

    @patch.object(HealthCheck, "_check_system_resources", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_models", return_value={"status": STATUS_UNHEALTHY, "issues": ["no key"]})
    @patch.object(HealthCheck, "_check_cache", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_storage", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_monitoring", return_value={"status": STATUS_HEALTHY, "issues": []})
    def test_check_all_unhealthy_propagates(self, *_):
        hc = HealthCheck()
        result = hc.check_all()
        self.assertEqual(result["overall_status"], STATUS_UNHEALTHY)

    @patch.object(HealthCheck, "_check_system_resources", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_models", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_cache", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_storage", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_monitoring", return_value={"status": STATUS_HEALTHY, "issues": []})
    def test_check_all_has_five_component_checks(self, *_):
        hc = HealthCheck()
        result = hc.check_all()
        self.assertEqual(len(result["checks"]), 5)
        for name in ("system", "models", "cache", "storage", "monitoring"):
            self.assertIn(name, result["checks"])


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport(unittest.TestCase):

    @patch.object(HealthCheck, "_check_system_resources", return_value={
        "status": STATUS_HEALTHY, "issues": [],
        "cpu_percent": 25.0, "memory_percent": 40.0, "disk_percent": 60.0,
    })
    @patch.object(HealthCheck, "_check_models", return_value={
        "status": STATUS_HEALTHY, "issues": [], "api_reachable": True,
    })
    @patch.object(HealthCheck, "_check_cache", return_value={
        "status": STATUS_HEALTHY, "issues": [], "hit_rate": 75.0,
    })
    @patch.object(HealthCheck, "_check_storage", return_value={
        "status": STATUS_HEALTHY, "issues": [],
    })
    @patch.object(HealthCheck, "_check_monitoring", return_value={
        "status": STATUS_HEALTHY, "issues": [], "daily_budget_pct": 20.0,
    })
    def test_format_report_includes_metrics(self, *_):
        hc = HealthCheck()
        report = hc.format_report()
        self.assertIn("Overall: HEALTHY", report)
        self.assertIn("CPU 25%", report)
        self.assertIn("Mem 40%", report)
        self.assertIn("API reachable", report)
        self.assertIn("Cache hit 75%", report)
        self.assertIn("Budget 20%", report)

    @patch.object(HealthCheck, "_check_system_resources", return_value={
        "status": STATUS_DEGRADED, "issues": ["CPU high: 95%"],
        "cpu_percent": 95.0, "memory_percent": 40.0, "disk_percent": 60.0,
    })
    @patch.object(HealthCheck, "_check_models", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_cache", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_storage", return_value={"status": STATUS_HEALTHY, "issues": []})
    @patch.object(HealthCheck, "_check_monitoring", return_value={"status": STATUS_HEALTHY, "issues": []})
    def test_format_report_includes_issues(self, *_):
        hc = HealthCheck()
        report = hc.format_report()
        self.assertIn("Overall: DEGRADED", report)
        self.assertIn("CPU high: 95%", report)


if __name__ == "__main__":
    unittest.main()
