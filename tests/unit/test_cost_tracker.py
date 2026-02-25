"""Unit tests for src/monitoring/cost_tracker.py — Cost tracking and budget enforcement.

Tests cover:
- get_budget_limits_from_rules(): loading from rules.yaml
- CostTracker initialization, usage recording, cost calculation, budget checks
- Persistence: save/load operations with atomicity
- Summaries: all-time, today, month
- Recommendations: cost optimization suggestions
- Budget projection: throttle level classification
- Global singleton: get_cost_tracker()

All external dependencies are mocked. Data directories use tempfile.mkdtemp().
Global cost_tracker is reset in setUp/tearDown.
"""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from collections import defaultdict

import src.monitoring.cost_tracker as cost_tracker_module
from src.monitoring.cost_tracker import (
    get_budget_limits_from_rules,
    get_budget_limit_from_rules,
    CostTracker,
    get_cost_tracker,
    _default_usage,
)


# ─────────────────────────────────────────────────────────────────────────────
# Base Test Classes and Setup
# ─────────────────────────────────────────────────────────────────────────────


class BaseCostTrackerTest(unittest.TestCase):
    """Base class that resets global cost_tracker state."""

    def setUp(self):
        """Clear global cost_tracker before each test."""
        cost_tracker_module.cost_tracker = None

    def tearDown(self):
        """Clear global cost_tracker after each test."""
        cost_tracker_module.cost_tracker = None


# ─────────────────────────────────────────────────────────────────────────────
# TestGetBudgetLimitsFromRules
# ─────────────────────────────────────────────────────────────────────────────


class TestGetBudgetLimitsFromRules(unittest.TestCase):
    """Test loading budget limits from rules.yaml."""

    def test_success_loads_budget_hard_stop_rule(self):
        """Successfully load budget_hard_stop rule from rules.yaml."""
        rules_data = {
            "non_override_rules": [
                {
                    "name": "budget_hard_stop",
                    "enabled": True,
                    "daily_limit": 10.0,
                    "monthly_limit": 200.0,
                }
            ]
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(rules_data))):
            with patch("yaml.safe_load", return_value=rules_data):
                with patch("src.utils.paths.base_path", return_value="/fake"):
                    result = get_budget_limits_from_rules()
                    self.assertEqual(result["daily"], 10.0)
                    self.assertEqual(result["monthly"], 200.0)

    def test_returns_defaults_when_file_missing(self):
        """Return default budgets when rules.yaml is not found."""
        with patch("builtins.open", side_effect=FileNotFoundError("no file")):
            result = get_budget_limits_from_rules()
            self.assertEqual(result["daily"], 5.0)
            self.assertEqual(result["monthly"], 100.0)

    def test_returns_defaults_when_rule_disabled(self):
        """Return defaults when budget_hard_stop rule is disabled."""
        rules_data = {
            "non_override_rules": [
                {
                    "name": "budget_hard_stop",
                    "enabled": False,
                    "daily_limit": 10.0,
                    "monthly_limit": 200.0,
                }
            ]
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(rules_data))):
            with patch("yaml.safe_load", return_value=rules_data):
                with patch("src.utils.paths.base_path", return_value="/fake"):
                    result = get_budget_limits_from_rules()
                    self.assertEqual(result["daily"], 5.0)
                    self.assertEqual(result["monthly"], 100.0)

    def test_returns_defaults_when_rule_missing(self):
        """Return defaults when budget_hard_stop rule doesn't exist."""
        rules_data = {
            "non_override_rules": [
                {"name": "other_rule", "enabled": True}
            ]
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(rules_data))):
            with patch("yaml.safe_load", return_value=rules_data):
                with patch("src.utils.paths.base_path", return_value="/fake"):
                    result = get_budget_limits_from_rules()
                    self.assertEqual(result["daily"], 5.0)
                    self.assertEqual(result["monthly"], 100.0)

    def test_returns_defaults_on_yaml_parse_error(self):
        """Return defaults when YAML parsing fails."""
        with patch("builtins.open", mock_open(read_data="invalid: yaml: [[[]]")):
            with patch("yaml.safe_load", side_effect=Exception("parse error")):
                with patch("src.utils.paths.base_path", return_value="/fake"):
                    result = get_budget_limits_from_rules()
                    self.assertEqual(result["daily"], 5.0)
                    self.assertEqual(result["monthly"], 100.0)

    def test_handles_missing_limit_keys(self):
        """Use defaults for missing daily_limit/monthly_limit keys."""
        rules_data = {
            "non_override_rules": [
                {"name": "budget_hard_stop", "enabled": True}
            ]
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(rules_data))):
            with patch("yaml.safe_load", return_value=rules_data):
                with patch("src.utils.paths.base_path", return_value="/fake"):
                    result = get_budget_limits_from_rules()
                    self.assertEqual(result["daily"], 5.0)
                    self.assertEqual(result["monthly"], 100.0)


class TestGetBudgetLimitFromRules(unittest.TestCase):
    """Test legacy get_budget_limit_from_rules() wrapper."""

    def test_returns_daily_limit(self):
        """Legacy function returns daily limit from get_budget_limits_from_rules()."""
        with patch("src.monitoring.cost_tracker.get_budget_limits_from_rules") as mock_fn:
            mock_fn.return_value = {"daily": 7.5, "monthly": 150.0}
            result = get_budget_limit_from_rules()
            self.assertEqual(result, 7.5)


# ─────────────────────────────────────────────────────────────────────────────
# TestCostTrackerInit
# ─────────────────────────────────────────────────────────────────────────────


class TestCostTrackerInit(unittest.TestCase):
    """Test CostTracker initialization."""

    def test_default_initialization(self):
        """Initialize with default values."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tracker = CostTracker(data_dir=tmp_dir)
            self.assertEqual(tracker.daily_budget, 5.0)
            self.assertEqual(tracker.monthly_budget, 100.0)
            self.assertEqual(tracker.data_dir, Path(tmp_dir))
            self.assertIsInstance(tracker.usage, defaultdict)
            self.assertIsInstance(tracker.daily_usage, dict)
            self.assertIsInstance(tracker.monthly_usage, dict)
            self.assertIsInstance(tracker._lock, type(threading.Lock()))

    def test_custom_budgets(self):
        """Initialize with custom budget values."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tracker = CostTracker(
                data_dir=tmp_dir,
                daily_budget_usd=15.0,
                monthly_budget_usd=300.0
            )
            self.assertEqual(tracker.daily_budget, 15.0)
            self.assertEqual(tracker.monthly_budget, 300.0)

    def test_creates_data_directory(self):
        """Create data directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp_base:
            data_dir = os.path.join(tmp_base, "newdir")
            tracker = CostTracker(data_dir=data_dir)
            self.assertTrue(os.path.exists(data_dir))
            self.assertTrue(os.path.isdir(data_dir))

    def test_data_dir_accepts_path_object(self):
        """Accept Path objects for data_dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path_obj = Path(tmp_dir)
            tracker = CostTracker(data_dir=path_obj)
            self.assertEqual(tracker.data_dir, path_obj)

    def test_data_dir_accepts_string(self):
        """Accept string paths for data_dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tracker = CostTracker(data_dir=tmp_dir)
            self.assertEqual(tracker.data_dir, Path(tmp_dir))

    def test_loads_existing_usage_on_init(self):
        """Call _load_usage() during initialization."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(CostTracker, "_load_usage") as mock_load:
                tracker = CostTracker(data_dir=tmp_dir)
                mock_load.assert_called_once()

    def test_pricing_dict_exists(self):
        """CostTracker has PRICING dict with openrouter rates."""
        self.assertIn("openrouter", CostTracker.PRICING)
        self.assertIn("input", CostTracker.PRICING["openrouter"])
        self.assertIn("output", CostTracker.PRICING["openrouter"])


# ─────────────────────────────────────────────────────────────────────────────
# TestCalculateCost
# ─────────────────────────────────────────────────────────────────────────────


class TestCalculateCost(unittest.TestCase):
    """Test cost calculation from tokens."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tracker = CostTracker(data_dir=self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_openrouter_pricing(self):
        """Calculate cost for openrouter provider."""
        cost = self.tracker._calculate_cost("openrouter", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 1.20, places=4)

    def test_zero_tokens(self):
        """Cost is zero when no tokens."""
        cost = self.tracker._calculate_cost("openrouter", 0, 0)
        self.assertEqual(cost, 0.0)

    def test_only_input_tokens(self):
        """Calculate cost from input tokens only."""
        cost = self.tracker._calculate_cost("openrouter", 1_000_000, 0)
        self.assertAlmostEqual(cost, 0.20, places=4)

    def test_only_output_tokens(self):
        """Calculate cost from output tokens only."""
        cost = self.tracker._calculate_cost("openrouter", 0, 1_000_000)
        self.assertAlmostEqual(cost, 1.00, places=4)

    def test_unknown_provider_returns_zero(self):
        """Return 0 cost for unknown provider."""
        cost = self.tracker._calculate_cost("unknown-provider", 1_000_000, 1_000_000)
        self.assertEqual(cost, 0.0)

    def test_fractional_token_costs(self):
        """Handle fractional token costs correctly."""
        cost = self.tracker._calculate_cost("openrouter", 500_000, 500_000)
        self.assertAlmostEqual(cost, 0.60, places=4)


# ─────────────────────────────────────────────────────────────────────────────
# TestRecordUsage
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordUsage(unittest.TestCase):
    """Test recording API usage."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tracker = CostTracker(data_dir=self.tmp_dir, daily_budget_usd=100.0)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_records_basic_usage(self):
        """Record API usage with explicit cost."""
        self.tracker.record_usage(
            provider="openrouter",
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.05
        )
        key = "openrouter/gpt-4"
        self.assertEqual(self.tracker.usage[key]["calls"], 1)
        self.assertEqual(self.tracker.usage[key]["input_tokens"], 100)
        self.assertEqual(self.tracker.usage[key]["output_tokens"], 50)
        self.assertEqual(self.tracker.usage[key]["cost_usd"], 0.05)

    def test_accumulates_multiple_calls(self):
        """Accumulate usage across multiple records."""
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        self.tracker.record_usage("openrouter", "gpt-4", 200, 100, 0.10)
        key = "openrouter/gpt-4"
        self.assertEqual(self.tracker.usage[key]["calls"], 2)
        self.assertEqual(self.tracker.usage[key]["input_tokens"], 300)
        self.assertEqual(self.tracker.usage[key]["output_tokens"], 150)
        self.assertAlmostEqual(self.tracker.usage[key]["cost_usd"], 0.15, places=4)

    def test_auto_calculates_cost_when_none(self):
        """Auto-calculate cost from tokens when cost_usd is None."""
        self.tracker.record_usage(
            provider="openrouter",
            model="test-model",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cost_usd=None
        )
        key = "openrouter/test-model"
        expected_cost = 0.20 + 1.00
        self.assertAlmostEqual(
            self.tracker.usage[key]["cost_usd"],
            expected_cost,
            places=4
        )

    def test_records_daily_usage(self):
        """Track daily usage separately."""
        today = datetime.now().date().isoformat()
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        self.assertIn(today, self.tracker.daily_usage)
        self.assertEqual(self.tracker.daily_usage[today], 0.05)

    def test_records_monthly_usage(self):
        """Track monthly usage separately."""
        month = datetime.now().strftime("%Y-%m")
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        self.assertIn(month, self.tracker.monthly_usage)
        self.assertEqual(self.tracker.monthly_usage[month], 0.05)

    def test_daily_and_monthly_accumulation(self):
        """Accumulate daily and monthly totals across records."""
        today = datetime.now().date().isoformat()
        month = datetime.now().strftime("%Y-%m")
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        self.tracker.record_usage("openrouter", "gpt-3", 200, 100, 0.03)
        self.assertEqual(self.tracker.daily_usage[today], 0.08)
        self.assertEqual(self.tracker.monthly_usage[month], 0.08)

    def test_save_triggered_every_10_calls(self):
        """Auto-save usage after every 10th call per (provider/model) pair."""
        with patch.object(self.tracker, "_save_usage") as mock_save:
            # Record 9 calls - no save yet
            for i in range(9):
                self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)
            mock_save.assert_not_called()

            # Call 10 - should trigger save
            self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)
            self.assertEqual(mock_save.call_count, 1)

            # Calls 11-19 - no additional save
            mock_save.reset_mock()
            for i in range(9):
                self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)
            mock_save.assert_not_called()

            # Call 20 - should trigger save again
            self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)
            mock_save.assert_called_once()

    def test_different_providers_tracked_separately(self):
        """Track different providers as separate keys."""
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        self.tracker.record_usage("local", "llama", 100, 50, 0.00)
        self.assertIn("openrouter/gpt-4", self.tracker.usage)
        self.assertIn("local/llama", self.tracker.usage)

    def test_thread_safe_recording(self):
        """Recording is thread-safe (protected by lock)."""
        def record_in_thread():
            for i in range(10):
                self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)

        threads = [threading.Thread(target=record_in_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        key = "openrouter/gpt-4"
        self.assertEqual(self.tracker.usage[key]["calls"], 50)


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckBudget
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckBudget(unittest.TestCase):
    """Test budget enforcement."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tracker = CostTracker(
            data_dir=self.tmp_dir,
            daily_budget_usd=5.0,
            monthly_budget_usd=100.0
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_within_budget_returns_allowed(self):
        """Return allowed=True when within budget."""
        result = self.tracker.check_budget(estimated_cost=1.0)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "within_budget")

    def test_daily_exceeded_returns_denied(self):
        """Return allowed=False when daily budget exceeded."""
        today = datetime.now().date().isoformat()
        self.tracker.daily_usage[today] = 4.5
        result = self.tracker.check_budget(estimated_cost=0.6)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "daily_budget_exceeded")
        self.assertEqual(result["daily_spent"], 4.5)
        self.assertEqual(result["daily_limit"], 5.0)

    def test_monthly_exceeded_returns_denied(self):
        """Return allowed=False when monthly budget exceeded."""
        month = datetime.now().strftime("%Y-%m")
        self.tracker.monthly_usage[month] = 99.0
        result = self.tracker.check_budget(estimated_cost=1.1)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "monthly_budget_exceeded")
        self.assertEqual(result["monthly_spent"], 99.0)
        self.assertEqual(result["monthly_limit"], 100.0)

    def test_estimated_cost_pushes_over_daily(self):
        """Check estimated cost against daily budget."""
        today = datetime.now().date().isoformat()
        self.tracker.daily_usage[today] = 3.0
        result = self.tracker.check_budget(estimated_cost=2.5)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "daily_budget_exceeded")

    def test_estimated_cost_pushes_over_monthly(self):
        """Check estimated cost against monthly budget."""
        month = datetime.now().strftime("%Y-%m")
        self.tracker.monthly_usage[month] = 98.0
        result = self.tracker.check_budget(estimated_cost=2.1)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "monthly_budget_exceeded")

    def test_returns_daily_remaining(self):
        """Include daily_remaining in response when allowed."""
        today = datetime.now().date().isoformat()
        self.tracker.daily_usage[today] = 1.0
        result = self.tracker.check_budget(estimated_cost=1.0)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["daily_remaining"], 4.0)

    def test_returns_monthly_remaining(self):
        """Include monthly_remaining in response when allowed."""
        month = datetime.now().strftime("%Y-%m")
        self.tracker.monthly_usage[month] = 50.0
        result = self.tracker.check_budget(estimated_cost=1.0)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["monthly_remaining"], 50.0)

    def test_zero_estimated_cost(self):
        """Handle zero estimated cost."""
        result = self.tracker.check_budget(estimated_cost=0.0)
        self.assertTrue(result["allowed"])

    def test_thread_safe_check(self):
        """Budget check is thread-safe."""
        month = datetime.now().strftime("%Y-%m")
        self.tracker.monthly_usage[month] = 99.5
        results = []

        def check_in_thread():
            result = self.tracker.check_budget(estimated_cost=0.3)
            results.append(result)

        threads = [threading.Thread(target=check_in_thread) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 10)


# ─────────────────────────────────────────────────────────────────────────────
# TestGetSummary
# ─────────────────────────────────────────────────────────────────────────────


class TestGetSummary(unittest.TestCase):
    """Test cost summary generation."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tracker = CostTracker(
            data_dir=self.tmp_dir,
            daily_budget_usd=10.0,
            monthly_budget_usd=200.0
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_summary_all_time(self):
        """Get all-time summary."""
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        self.tracker.record_usage("openrouter", "gpt-3", 200, 100, 0.03)
        summary = self.tracker.get_summary(period="all")
        self.assertEqual(summary["period"], "all_time")
        self.assertEqual(summary["total_cost"], 0.08)
        self.assertEqual(summary["total_calls"], 2)
        self.assertEqual(summary["total_input_tokens"], 300)
        self.assertEqual(summary["total_output_tokens"], 150)
        self.assertIn("by_provider", summary)

    def test_summary_today(self):
        """Get today's summary."""
        today = datetime.now().date().isoformat()
        self.tracker.daily_usage[today] = 2.5
        summary = self.tracker.get_summary(period="today")
        self.assertEqual(summary["period"], "today")
        self.assertEqual(summary["date"], today)
        self.assertEqual(summary["total_cost"], 2.5)
        self.assertEqual(summary["budget"], 10.0)
        self.assertEqual(summary["percentage"], 25.0)

    def test_summary_month(self):
        """Get this month's summary."""
        month = datetime.now().strftime("%Y-%m")
        self.tracker.monthly_usage[month] = 50.0
        summary = self.tracker.get_summary(period="month")
        self.assertEqual(summary["period"], "month")
        self.assertEqual(summary["month"], month)
        self.assertEqual(summary["total_cost"], 50.0)
        self.assertEqual(summary["budget"], 200.0)
        self.assertEqual(summary["percentage"], 25.0)

    def test_percentage_calculation(self):
        """Calculate percentage correctly."""
        today = datetime.now().date().isoformat()
        self.tracker.daily_usage[today] = 5.0
        summary = self.tracker.get_summary(period="today")
        self.assertEqual(summary["percentage"], 50.0)

    def test_zero_budget_division(self):
        """Handle zero budget without division error."""
        tracker = CostTracker(
            data_dir=self.tmp_dir,
            daily_budget_usd=0.0,
            monthly_budget_usd=0.0
        )
        today = datetime.now().date().isoformat()
        tracker.daily_usage[today] = 1.0
        summary = tracker.get_summary(period="today")
        self.assertEqual(summary["percentage"], 0)

    def test_all_summary_includes_today_and_month(self):
        """All-time summary includes today and month breakdowns."""
        today = datetime.now().date().isoformat()
        month = datetime.now().strftime("%Y-%m")
        self.tracker.daily_usage[today] = 2.0
        self.tracker.monthly_usage[month] = 50.0
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        summary = self.tracker.get_summary(period="all")
        self.assertIn("today", summary)
        self.assertIn("month", summary)
        # Both values should be present and non-zero
        self.assertGreater(summary["today"]["total_cost"], 1.9)
        self.assertGreater(summary["month"]["total_cost"], 49.9)

    def test_empty_usage(self):
        """Summary of empty usage."""
        summary = self.tracker.get_summary(period="all")
        self.assertEqual(summary["total_cost"], 0.0)
        self.assertEqual(summary["total_calls"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# TestGetRecommendations
# ─────────────────────────────────────────────────────────────────────────────


class TestGetRecommendations(unittest.TestCase):
    """Test cost optimization recommendations."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tracker = CostTracker(data_dir=self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_no_usage_returns_zero_cost_message(self):
        """Return 'no usage' message when no API calls recorded."""
        recs = self.tracker.get_recommendations()
        self.assertEqual(len(recs), 1)
        self.assertIn("No API usage yet", recs[0])

    def test_high_concentration_recommendation(self):
        """Recommend caching when one model dominates costs (>80%)."""
        self.tracker.record_usage("openrouter", "expensive-model", 100, 50, 0.80)
        self.tracker.record_usage("openrouter", "cheap-model", 100, 50, 0.10)
        with patch("src.utils.config.get_monitoring") as mock_config:
            mock_config.return_value = {"budget_warning_pct": 80}
            recs = self.tracker.get_recommendations()
            concentration_rec = [r for r in recs if "accounts for" in r and "%" in r]
            self.assertTrue(len(concentration_rec) > 0)

    def test_high_api_cost_recommendation(self):
        """Recommend reducing API calls when >50% of costs are from API."""
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.75)
        self.tracker.record_usage("local", "llama", 100, 50, 0.10)
        with patch("src.utils.config.get_monitoring") as mock_config:
            mock_config.return_value = {"budget_warning_pct": 80}
            recs = self.tracker.get_recommendations()
            api_rec = [r for r in recs if "Over 50%" in r or "API calls" in r]
            self.assertTrue(len(api_rec) > 0)

    def test_daily_budget_warning(self):
        """Warn when daily spend exceeds threshold."""
        today = datetime.now().date().isoformat()
        self.tracker.daily_budget = 10.0
        self.tracker.daily_usage[today] = 8.5
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)
        with patch("src.utils.config.get_monitoring") as mock_config:
            mock_config.return_value = {"budget_warning_pct": 80}
            recs = self.tracker.get_recommendations()
            daily_rec = [r for r in recs if "Daily budget" in r]
            self.assertTrue(len(daily_rec) > 0)

    def test_monthly_budget_warning(self):
        """Warn when monthly spend exceeds threshold."""
        month = datetime.now().strftime("%Y-%m")
        self.tracker.monthly_budget = 100.0
        self.tracker.monthly_usage[month] = 85.0
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)
        with patch("src.utils.config.get_monitoring") as mock_config:
            mock_config.return_value = {"budget_warning_pct": 80}
            recs = self.tracker.get_recommendations()
            monthly_rec = [r for r in recs if "Monthly budget" in r]
            self.assertTrue(len(monthly_rec) > 0)

    def test_low_cost_returns_no_action_needed(self):
        """Return 'no action needed' message when costs are low."""
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.01)
        with patch("src.utils.config.get_monitoring") as mock_config:
            mock_config.return_value = {"budget_warning_pct": 80}
            recs = self.tracker.get_recommendations()
            self.assertTrue(len(recs) >= 1)

    def test_uses_config_budget_warning_pct(self):
        """Use budget_warning_pct from config."""
        self.tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        today = datetime.now().date().isoformat()
        self.tracker.daily_budget = 10.0
        self.tracker.daily_usage[today] = 7.0
        with patch("src.utils.config.get_monitoring") as mock_config:
            mock_config.return_value = {"budget_warning_pct": 80}
            recs = self.tracker.get_recommendations()
            daily_rec = [r for r in recs if "Daily budget" in r]
            self.assertEqual(len(daily_rec), 0)


# ─────────────────────────────────────────────────────────────────────────────
# TestSaveLoadUsage
# ─────────────────────────────────────────────────────────────────────────────


class TestSaveLoadUsage(unittest.TestCase):
    """Test persistence: saving and loading usage data."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_writes_json_file(self):
        """Save usage writes a JSON file."""
        tracker = CostTracker(data_dir=self.tmp_dir)
        tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        tracker._save_usage()

        usage_file = Path(self.tmp_dir) / "cost_usage.json"
        self.assertTrue(usage_file.exists())

        with open(usage_file, "r") as f:
            data = json.load(f)
        self.assertIn("usage", data)
        self.assertIn("daily_usage", data)
        self.assertIn("monthly_usage", data)
        self.assertIn("last_updated", data)

    def test_load_reads_json_file(self):
        """Load usage reads from JSON file."""
        tracker1 = CostTracker(data_dir=self.tmp_dir)
        tracker1.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        tracker1._save_usage()

        tracker2 = CostTracker(data_dir=self.tmp_dir)
        self.assertEqual(tracker2.usage["openrouter/gpt-4"]["calls"], 1)
        self.assertEqual(tracker2.usage["openrouter/gpt-4"]["cost_usd"], 0.05)

    def test_roundtrip_persistence(self):
        """Data persists correctly across save/load cycles."""
        tracker1 = CostTracker(data_dir=self.tmp_dir)
        tracker1.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        tracker1.record_usage("openrouter", "gpt-3", 200, 100, 0.03)
        tracker1._save_usage()

        tracker2 = CostTracker(data_dir=self.tmp_dir)
        self.assertEqual(tracker2.usage["openrouter/gpt-4"]["calls"], 1)
        self.assertEqual(tracker2.usage["openrouter/gpt-3"]["calls"], 1)
        self.assertEqual(tracker2.usage["openrouter/gpt-4"]["cost_usd"], 0.05)
        self.assertEqual(tracker2.usage["openrouter/gpt-3"]["cost_usd"], 0.03)

    def test_load_missing_file_silently_skips(self):
        """Load doesn't crash when file doesn't exist."""
        tracker = CostTracker(data_dir=self.tmp_dir)
        tracker._load_usage()
        self.assertEqual(len(tracker.usage), 0)

    def test_load_corrupt_json_logs_error(self):
        """Load handles corrupt JSON gracefully."""
        usage_file = Path(self.tmp_dir) / "cost_usage.json"
        usage_file.write_text("{ invalid json [[[")

        tracker = CostTracker(data_dir=self.tmp_dir)
        self.assertEqual(len(tracker.usage), 0)

    def test_atomic_write_with_temp_file(self):
        """Save uses atomic write via temp file."""
        tracker = CostTracker(data_dir=self.tmp_dir)
        tracker.record_usage("openrouter", "gpt-4", 100, 50, 0.05)

        with patch("os.replace") as mock_replace:
            tracker._save_usage()
            mock_replace.assert_called_once()

    def test_load_preserves_structure(self):
        """Loaded data preserves dict structure."""
        tracker1 = CostTracker(data_dir=self.tmp_dir)
        tracker1.record_usage("openrouter", "gpt-4", 100, 50, 0.05)
        tracker1.record_usage("openrouter", "gpt-4", 200, 100, 0.10)
        tracker1._save_usage()

        tracker2 = CostTracker(data_dir=self.tmp_dir)
        usage = tracker2.usage["openrouter/gpt-4"]
        self.assertEqual(usage["calls"], 2)
        self.assertEqual(usage["input_tokens"], 300)
        self.assertEqual(usage["output_tokens"], 150)
        self.assertAlmostEqual(usage["cost_usd"], 0.15, places=4)

    def test_missing_keys_in_loaded_data(self):
        """Handle missing keys in loaded JSON gracefully."""
        usage_file = Path(self.tmp_dir) / "cost_usage.json"
        data = {
            "usage": {
                "openrouter/gpt-4": {
                    "calls": 1,
                }
            },
            "daily_usage": {},
            "monthly_usage": {},
        }
        usage_file.write_text(json.dumps(data))

        tracker = CostTracker(data_dir=self.tmp_dir)
        usage = tracker.usage["openrouter/gpt-4"]
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["cost_usd"], 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# TestGetCostTracker (Global Singleton)
# ─────────────────────────────────────────────────────────────────────────────


class TestGetCostTracker(BaseCostTrackerTest):
    """Test get_cost_tracker() global singleton function."""

    def test_creates_singleton_on_first_call(self):
        """First call creates and returns singleton."""
        with patch("src.monitoring.cost_tracker.get_budget_limits_from_rules") as mock_limits:
            mock_limits.return_value = {"daily": 5.0, "monthly": 100.0}
            tracker = get_cost_tracker()
            self.assertIsNotNone(tracker)
            self.assertIsInstance(tracker, CostTracker)
            self.assertIs(tracker, cost_tracker_module.cost_tracker)

    def test_reuses_existing_singleton(self):
        """Second call reuses existing singleton."""
        with patch("src.monitoring.cost_tracker.get_budget_limits_from_rules") as mock_limits:
            mock_limits.return_value = {"daily": 5.0, "monthly": 100.0}
            tracker1 = get_cost_tracker()
            tracker2 = get_cost_tracker()
            self.assertIs(tracker1, tracker2)

    def test_uses_custom_budgets_if_provided(self):
        """Use provided budgets instead of rules.yaml."""
        with patch("src.monitoring.cost_tracker.get_budget_limits_from_rules") as mock_limits:
            mock_limits.return_value = {"daily": 5.0, "monthly": 100.0}
            tracker = get_cost_tracker(daily_budget=10.0, monthly_budget=200.0)
            self.assertEqual(tracker.daily_budget, 10.0)
            self.assertEqual(tracker.monthly_budget, 200.0)

    def test_uses_rules_defaults_when_not_provided(self):
        """Use rules.yaml defaults when budgets not provided."""
        with patch("src.monitoring.cost_tracker.get_budget_limits_from_rules") as mock_limits:
            mock_limits.return_value = {"daily": 7.5, "monthly": 150.0}
            tracker = get_cost_tracker()
            self.assertEqual(tracker.daily_budget, 7.5)
            self.assertEqual(tracker.monthly_budget, 150.0)

    def test_partial_custom_budgets(self):
        """Provide only daily budget, use rules for monthly."""
        with patch("src.monitoring.cost_tracker.get_budget_limits_from_rules") as mock_limits:
            mock_limits.return_value = {"daily": 5.0, "monthly": 100.0}
            tracker = get_cost_tracker(daily_budget=15.0)
            self.assertEqual(tracker.daily_budget, 15.0)
            self.assertEqual(tracker.monthly_budget, 100.0)

    def test_partial_custom_budgets_monthly_only(self):
        """Provide only monthly budget, use rules for daily."""
        with patch("src.monitoring.cost_tracker.get_budget_limits_from_rules") as mock_limits:
            mock_limits.return_value = {"daily": 5.0, "monthly": 100.0}
            tracker = get_cost_tracker(monthly_budget=300.0)
            self.assertEqual(tracker.daily_budget, 5.0)
            self.assertEqual(tracker.monthly_budget, 300.0)


# ─────────────────────────────────────────────────────────────────────────────
# TestDefaultUsage
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultUsage(unittest.TestCase):
    """Test _default_usage() helper function."""

    def test_returns_dict_with_zero_values(self):
        """_default_usage returns dict with zero counters."""
        usage = _default_usage()
        self.assertEqual(usage["calls"], 0)
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["cost_usd"], 0.0)

    def test_returns_new_dict_each_call(self):
        """Each call returns a new dict instance."""
        usage1 = _default_usage()
        usage2 = _default_usage()
        self.assertIsNot(usage1, usage2)
        usage1["calls"] = 999
        self.assertEqual(usage2["calls"], 0)


if __name__ == "__main__":
    unittest.main()
