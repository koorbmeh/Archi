"""Unit tests for src/core/initiative_tracker.py — Budget and logging for proactive work.

Tests cover:
- _get_config(): loading initiative config from rules.yaml with fallback defaults
- _data_dir(): creating and returning data directory Path
- InitiativeTracker class:
  - __init__(): loads config, sets budget/max_per_day/enabled, loads state
  - _load_state(): loads from JSON, resets on new day or corrupt
  - _save_state(): saves to JSON
  - can_initiate(): checks budget + count + enabled + day boundary
  - budget_remaining(): returns remaining budget
  - record(): logs initiative, increments count, saves state
  - record_cost(): records actual cost, saves state
  - get_summary(): returns summary dict

All external dependencies are mocked (_get_config, _data_dir). Data directories use
tempfile.mkdtemp() for filesystem isolation. Tests follow pytest/unittest style with
class-based organization.
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open, call
from collections import defaultdict

import src.core.initiative_tracker as initiative_tracker_module
from src.core.initiative_tracker import (
    InitiativeTracker,
    _get_config,
    _data_dir,
)


# ─────────────────────────────────────────────────────────────────────────────
# TestGetConfig
# ─────────────────────────────────────────────────────────────────────────────


class TestGetConfig(unittest.TestCase):
    """Test _get_config() function — loading from rules.yaml."""

    def test_loads_initiative_config_from_rules(self):
        """Successfully load initiative config from rules.yaml."""
        rules_data = {
            "initiative": {
                "daily_budget": 1.50,
                "max_per_day": 3,
                "enabled": True,
                "respect_quiet_hours": False,
            }
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(rules_data))):
            with patch("yaml.safe_load", return_value=rules_data):
                with patch("src.utils.paths.project_root", return_value=Path("/fake")):
                    result = _get_config()
                    self.assertEqual(result["daily_budget"], 1.50)
                    self.assertEqual(result["max_per_day"], 3)
                    self.assertTrue(result["enabled"])
                    self.assertFalse(result["respect_quiet_hours"])

    def test_returns_empty_dict_when_file_missing(self):
        """Return empty dict when rules.yaml not found."""
        with patch("builtins.open", side_effect=FileNotFoundError("no file")):
            result = _get_config()
            self.assertEqual(result, {})

    def test_returns_empty_dict_on_yaml_parse_error(self):
        """Return empty dict when YAML parsing fails."""
        with patch("builtins.open", mock_open(read_data="invalid: yaml: [[[]]")):
            with patch("yaml.safe_load", side_effect=Exception("parse error")):
                with patch("src.utils.paths.project_root", return_value=Path("/fake")):
                    result = _get_config()
                    self.assertEqual(result, {})

    def test_returns_empty_dict_when_initiative_key_missing(self):
        """Return empty dict when 'initiative' key not in rules."""
        rules_data = {"other_config": {}}
        with patch("builtins.open", mock_open(read_data=json.dumps(rules_data))):
            with patch("yaml.safe_load", return_value=rules_data):
                with patch("src.utils.paths.project_root", return_value=Path("/fake")):
                    result = _get_config()
                    self.assertEqual(result, {})

    def test_returns_empty_dict_for_none_yaml_load(self):
        """Return empty dict when yaml.safe_load returns None."""
        with patch("builtins.open", mock_open(read_data="")):
            with patch("yaml.safe_load", return_value=None):
                with patch("src.utils.paths.project_root", return_value=Path("/fake")):
                    result = _get_config()
                    self.assertEqual(result, {})

    def test_returns_empty_dict_on_general_exception(self):
        """Return empty dict on any exception."""
        with patch("src.utils.paths.project_root", side_effect=Exception("import error")):
            result = _get_config()
            self.assertEqual(result, {})


# ─────────────────────────────────────────────────────────────────────────────
# TestDataDir
# ─────────────────────────────────────────────────────────────────────────────


class TestDataDir(unittest.TestCase):
    """Test _data_dir() function."""

    def test_returns_path_object(self):
        """_data_dir returns a Path object."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.utils.paths.project_root", return_value=Path(tmp_dir)):
                result = _data_dir()
                self.assertIsInstance(result, Path)

    def test_creates_data_directory(self):
        """_data_dir creates data directory if missing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_root = Path(tmp_dir)
            with patch("src.utils.paths.project_root", return_value=fake_root):
                result = _data_dir()
                self.assertTrue(result.exists())
                self.assertTrue(result.is_dir())

    def test_returns_project_root_data_subdir(self):
        """_data_dir returns project_root/data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_root = Path(tmp_dir)
            with patch("src.utils.paths.project_root", return_value=fake_root):
                result = _data_dir()
                self.assertEqual(result.parent, fake_root)
                self.assertEqual(result.name, "data")

    def test_idempotent_multiple_calls(self):
        """Multiple calls to _data_dir return same path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_root = Path(tmp_dir)
            with patch("src.utils.paths.project_root", return_value=fake_root):
                result1 = _data_dir()
                result2 = _data_dir()
                self.assertEqual(result1, result2)
                self.assertTrue(result2.exists())


# ─────────────────────────────────────────────────────────────────────────────
# TestInitiativeTrackerInit
# ─────────────────────────────────────────────────────────────────────────────


class TestInitiativeTrackerInit(unittest.TestCase):
    """Test InitiativeTracker initialization."""

    def test_initializes_with_default_values(self):
        """Initialize with default values when config is empty."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.core.initiative_tracker._get_config", return_value={}):
                with patch("src.core.initiative_tracker._data_dir", return_value=Path(tmp_dir)):
                    tracker = InitiativeTracker()
                    self.assertEqual(tracker.daily_budget, 0.50)
                    self.assertEqual(tracker.max_per_day, 2)
                    self.assertTrue(tracker.enabled)
                    self.assertTrue(tracker.respect_quiet_hours)

    def test_initializes_with_config_values(self):
        """Initialize with values from _get_config()."""
        config = {
            "daily_budget": 2.00,
            "max_per_day": 5,
            "enabled": False,
            "respect_quiet_hours": False,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.core.initiative_tracker._get_config", return_value=config):
                with patch("src.core.initiative_tracker._data_dir", return_value=Path(tmp_dir)):
                    tracker = InitiativeTracker()
                    self.assertEqual(tracker.daily_budget, 2.00)
                    self.assertEqual(tracker.max_per_day, 5)
                    self.assertFalse(tracker.enabled)
                    self.assertFalse(tracker.respect_quiet_hours)

    def test_converts_budget_to_float(self):
        """Convert budget values to float."""
        config = {"daily_budget": "1.5", "max_per_day": 2}
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.core.initiative_tracker._get_config", return_value=config):
                with patch("src.core.initiative_tracker._data_dir", return_value=Path(tmp_dir)):
                    tracker = InitiativeTracker()
                    self.assertEqual(tracker.daily_budget, 1.5)
                    self.assertIsInstance(tracker.daily_budget, float)

    def test_converts_max_per_day_to_int(self):
        """Convert max_per_day to int."""
        config = {"daily_budget": 0.5, "max_per_day": "3"}
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.core.initiative_tracker._get_config", return_value=config):
                with patch("src.core.initiative_tracker._data_dir", return_value=Path(tmp_dir)):
                    tracker = InitiativeTracker()
                    self.assertEqual(tracker.max_per_day, 3)
                    self.assertIsInstance(tracker.max_per_day, int)

    def test_initializes_state_paths(self):
        """Initialize state and log file paths."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.core.initiative_tracker._get_config", return_value={}):
                with patch("src.core.initiative_tracker._data_dir", return_value=Path(tmp_dir)):
                    tracker = InitiativeTracker()
                    self.assertEqual(tracker._state_path, Path(tmp_dir) / "initiative_state.json")
                    self.assertEqual(tracker._log_path, Path(tmp_dir) / "initiative_log.jsonl")

    def test_initializes_daily_state(self):
        """Initialize daily state variables."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.core.initiative_tracker._get_config", return_value={}):
                with patch("src.core.initiative_tracker._data_dir", return_value=Path(tmp_dir)):
                    tracker = InitiativeTracker()
                    self.assertEqual(tracker.today, date.today().isoformat())
                    self.assertEqual(tracker.spend_today, 0.0)
                    self.assertEqual(tracker.count_today, 0)

    def test_calls_load_state_on_init(self):
        """Call _load_state() during initialization."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("src.core.initiative_tracker._get_config", return_value={}):
                with patch("src.core.initiative_tracker._data_dir", return_value=Path(tmp_dir)):
                    with patch.object(InitiativeTracker, "_load_state") as mock_load:
                        tracker = InitiativeTracker()
                        mock_load.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadState
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadState(unittest.TestCase):
    """Test _load_state() — state persistence and day boundary handling."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_loads_state_from_json_same_day(self):
        """Load state from JSON when it's the same day."""
        today = date.today().isoformat()
        state_data = {
            "date": today,
            "spend": 0.35,
            "count": 1,
        }
        state_file = self.data_dir / "initiative_state.json"
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.today, today)
                self.assertEqual(tracker.spend_today, 0.35)
                self.assertEqual(tracker.count_today, 1)

    def test_resets_state_on_new_day(self):
        """Reset state when file has a different date."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        state_data = {
            "date": yesterday,
            "spend": 0.50,
            "count": 2,
        }
        state_file = self.data_dir / "initiative_state.json"
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.today, date.today().isoformat())
                self.assertEqual(tracker.spend_today, 0.0)
                self.assertEqual(tracker.count_today, 0)

    def test_resets_state_on_corrupt_json(self):
        """Reset state when JSON file is corrupt."""
        state_file = self.data_dir / "initiative_state.json"
        state_file.write_text("{ invalid json [[[")

        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.today, date.today().isoformat())
                self.assertEqual(tracker.spend_today, 0.0)
                self.assertEqual(tracker.count_today, 0)

    def test_resets_state_when_file_missing(self):
        """Reset state when state file doesn't exist."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.today, date.today().isoformat())
                self.assertEqual(tracker.spend_today, 0.0)
                self.assertEqual(tracker.count_today, 0)

    def test_saves_state_on_new_day_reset(self):
        """Save state file when resetting on new day."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                state_file = self.data_dir / "initiative_state.json"
                self.assertTrue(state_file.exists())

    def test_converts_spend_to_float(self):
        """Convert loaded spend value to float."""
        today = date.today().isoformat()
        state_data = {
            "date": today,
            "spend": "0.35",
            "count": 1,
        }
        state_file = self.data_dir / "initiative_state.json"
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.spend_today, 0.35)
                self.assertIsInstance(tracker.spend_today, float)

    def test_converts_count_to_int(self):
        """Convert loaded count to int."""
        today = date.today().isoformat()
        state_data = {
            "date": today,
            "spend": 0.35,
            "count": "2",
        }
        state_file = self.data_dir / "initiative_state.json"
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.count_today, 2)
                self.assertIsInstance(tracker.count_today, int)

    def test_handles_missing_spend_key(self):
        """Use 0.0 when 'spend' key is missing."""
        today = date.today().isoformat()
        state_data = {
            "date": today,
            "count": 1,
        }
        state_file = self.data_dir / "initiative_state.json"
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.spend_today, 0.0)

    def test_handles_missing_count_key(self):
        """Use 0 when 'count' key is missing."""
        today = date.today().isoformat()
        state_data = {
            "date": today,
            "spend": 0.35,
        }
        state_file = self.data_dir / "initiative_state.json"
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.count_today, 0)


# ─────────────────────────────────────────────────────────────────────────────
# TestSaveState
# ─────────────────────────────────────────────────────────────────────────────


class TestSaveState(unittest.TestCase):
    """Test _save_state() — state persistence."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_saves_state_to_json_file(self):
        """Save state writes JSON file with correct structure."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.45
                tracker.count_today = 1
                tracker._save_state()

                state_file = self.data_dir / "initiative_state.json"
                self.assertTrue(state_file.exists())

                with open(state_file, "r") as f:
                    data = json.load(f)
                self.assertEqual(data["date"], tracker.today)
                self.assertEqual(data["spend"], 0.45)
                self.assertEqual(data["count"], 1)

    def test_rounds_spend_to_4_decimals(self):
        """Round spend to 4 decimal places in saved state."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.123456789
                tracker._save_state()

                state_file = self.data_dir / "initiative_state.json"
                with open(state_file, "r") as f:
                    data = json.load(f)
                self.assertEqual(data["spend"], 0.1235)

    def test_logs_error_on_write_failure(self):
        """Log error when save fails."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                bad_path = self.data_dir / "nonexistent_dir" / "state.json"
                tracker._state_path = bad_path

                with patch("src.core.initiative_tracker.logger") as mock_logger:
                    tracker._save_state()
                    mock_logger.error.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# TestCanInitiate
# ─────────────────────────────────────────────────────────────────────────────


class TestCanInitiate(unittest.TestCase):
    """Test can_initiate() — budget and count checks."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_false_when_disabled(self):
        """Return False when enabled is False."""
        config = {"enabled": False, "daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertFalse(tracker.can_initiate())

    def test_returns_false_when_budget_exceeded(self):
        """Return False when spend_today >= daily_budget."""
        config = {"enabled": True, "daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 1.0
                self.assertFalse(tracker.can_initiate())

    def test_returns_false_when_count_exceeded(self):
        """Return False when count_today >= max_per_day."""
        config = {"enabled": True, "daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.count_today = 2
                self.assertFalse(tracker.can_initiate())

    def test_returns_true_when_within_both_limits(self):
        """Return True when within budget and count."""
        config = {"enabled": True, "daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertTrue(tracker.can_initiate())

    def test_rechecks_day_boundary(self):
        """Recheck day boundary in can_initiate()."""
        config = {"enabled": True, "daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                # Simulate yesterday's state
                tracker.today = (date.today() - timedelta(days=1)).isoformat()

                with patch.object(tracker, "_load_state") as mock_load:
                    tracker.can_initiate()
                    mock_load.assert_called_once()

    def test_at_budget_boundary_returns_false(self):
        """Return False when spend exactly equals budget."""
        config = {"enabled": True, "daily_budget": 0.5, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.5
                self.assertFalse(tracker.can_initiate())

    def test_at_count_boundary_returns_false(self):
        """Return False when count exactly equals max_per_day."""
        config = {"enabled": True, "daily_budget": 1.0, "max_per_day": 3}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.count_today = 3
                self.assertFalse(tracker.can_initiate())

    def test_just_below_budget_returns_true(self):
        """Return True when spend is just below budget."""
        config = {"enabled": True, "daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.49
                self.assertTrue(tracker.can_initiate())

    def test_just_below_count_returns_true(self):
        """Return True when count is just below max."""
        config = {"enabled": True, "daily_budget": 1.0, "max_per_day": 3}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.count_today = 2
                self.assertTrue(tracker.can_initiate())


# ─────────────────────────────────────────────────────────────────────────────
# TestBudgetRemaining
# ─────────────────────────────────────────────────────────────────────────────


class TestBudgetRemaining(unittest.TestCase):
    """Test budget_remaining() — calculate remaining budget."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_budget_when_nothing_spent(self):
        """Return full budget when spend is 0."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.0
                self.assertEqual(tracker.budget_remaining(), 1.0)

    def test_returns_remaining_when_partially_spent(self):
        """Return difference when partially spent."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.35
                self.assertAlmostEqual(tracker.budget_remaining(), 0.65, places=4)

    def test_returns_zero_when_budget_spent(self):
        """Return 0 when full budget spent."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 1.0
                self.assertEqual(tracker.budget_remaining(), 0.0)

    def test_returns_zero_when_overspent(self):
        """Return 0 (not negative) when overspent."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 1.5
                self.assertEqual(tracker.budget_remaining(), 0.0)

    def test_handles_float_precision(self):
        """Handle floating point calculations correctly."""
        config = {"daily_budget": 0.50, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.25
                self.assertAlmostEqual(tracker.budget_remaining(), 0.25, places=4)


# ─────────────────────────────────────────────────────────────────────────────
# TestRecord
# ─────────────────────────────────────────────────────────────────────────────


class TestRecord(unittest.TestCase):
    """Test record() — log initiative and update state."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_writes_entry_to_jsonl_log(self):
        """Write initiative entry to JSONL log file."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.record(
                    title="Test Initiative",
                    why_jesse_cares="Test reason",
                    estimated_cost=0.25,
                    goal_id="goal-123"
                )

                log_file = self.data_dir / "initiative_log.jsonl"
                self.assertTrue(log_file.exists())

                with open(log_file, "r") as f:
                    line = f.readline()
                    entry = json.loads(line)

                self.assertEqual(entry["title"], "Test Initiative")
                self.assertEqual(entry["why"], "Test reason")
                self.assertEqual(entry["estimated_cost"], 0.25)
                self.assertEqual(entry["goal_id"], "goal-123")
                self.assertEqual(entry["status"], "created")

    def test_sets_timestamp_on_record(self):
        """Record entry includes ISO timestamp."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                before = datetime.now().isoformat()
                tracker.record("Test", "Reason", 0.25, "goal-123")
                after = datetime.now().isoformat()

                log_file = self.data_dir / "initiative_log.jsonl"
                with open(log_file, "r") as f:
                    entry = json.loads(f.readline())

                self.assertIn("ts", entry)
                self.assertGreaterEqual(entry["ts"], before)
                self.assertLessEqual(entry["ts"], after)

    def test_increments_count_today(self):
        """Increment count_today on record."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.count_today, 0)
                tracker.record("Test", "Reason", 0.25, "goal-123")
                self.assertEqual(tracker.count_today, 1)
                tracker.record("Test 2", "Reason 2", 0.15, "goal-124")
                self.assertEqual(tracker.count_today, 2)

    def test_saves_state_after_record(self):
        """Call _save_state() after recording."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                with patch.object(tracker, "_save_state") as mock_save:
                    tracker.record("Test", "Reason", 0.25, "goal-123")
                    mock_save.assert_called_once()

    def test_rounds_estimated_cost_to_4_decimals(self):
        """Round estimated_cost to 4 decimal places in log."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.record("Test", "Reason", 0.123456789, "goal-123")

                log_file = self.data_dir / "initiative_log.jsonl"
                with open(log_file, "r") as f:
                    entry = json.loads(f.readline())
                self.assertEqual(entry["estimated_cost"], 0.1235)

    def test_logs_error_on_write_failure(self):
        """Log error when write to JSONL fails."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                bad_path = self.data_dir / "nonexistent_dir" / "log.jsonl"
                tracker._log_path = bad_path

                with patch("src.core.initiative_tracker.logger") as mock_logger:
                    tracker.record("Test", "Reason", 0.25, "goal-123")
                    mock_logger.error.assert_called_once()

    def test_multiple_records_appended_to_log(self):
        """Multiple records append to JSONL (not overwrite)."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.record("Test 1", "Reason 1", 0.25, "goal-123")
                tracker.record("Test 2", "Reason 2", 0.15, "goal-124")

                log_file = self.data_dir / "initiative_log.jsonl"
                with open(log_file, "r") as f:
                    lines = f.readlines()
                self.assertEqual(len(lines), 2)

                entry1 = json.loads(lines[0])
                entry2 = json.loads(lines[1])
                self.assertEqual(entry1["title"], "Test 1")
                self.assertEqual(entry2["title"], "Test 2")

    def test_records_goal_id_correctly(self):
        """Record goal_id in log entry."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.record("Test", "Reason", 0.25, "my-special-goal-id")

                log_file = self.data_dir / "initiative_log.jsonl"
                with open(log_file, "r") as f:
                    entry = json.loads(f.readline())
                self.assertEqual(entry["goal_id"], "my-special-goal-id")


# ─────────────────────────────────────────────────────────────────────────────
# TestRecordCost
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordCost(unittest.TestCase):
    """Test record_cost() — record actual cost and update state."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_adds_cost_to_spend_today(self):
        """Add actual_cost to spend_today."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                self.assertEqual(tracker.spend_today, 0.0)
                tracker.record_cost("goal-123", 0.25)
                self.assertEqual(tracker.spend_today, 0.25)

    def test_accumulates_multiple_costs(self):
        """Accumulate multiple costs."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.record_cost("goal-123", 0.25)
                tracker.record_cost("goal-124", 0.15)
                self.assertAlmostEqual(tracker.spend_today, 0.40, places=4)

    def test_saves_state_after_record_cost(self):
        """Call _save_state() after recording cost."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                with patch.object(tracker, "_save_state") as mock_save:
                    tracker.record_cost("goal-123", 0.25)
                    mock_save.assert_called_once()

    def test_logs_info_on_record_cost(self):
        """Log info message after recording cost."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.daily_budget = 1.0
                with patch("src.core.initiative_tracker.logger") as mock_logger:
                    tracker.record_cost("goal-123", 0.25)
                    mock_logger.info.assert_called_once()
                    call_args = mock_logger.info.call_args[0]
                    self.assertIn("goal-123", str(call_args))
                    self.assertIn("0.25", str(call_args))

    def test_handles_zero_cost(self):
        """Handle recording zero cost."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.record_cost("goal-123", 0.0)
                self.assertEqual(tracker.spend_today, 0.0)

    def test_handles_fractional_costs(self):
        """Handle fractional cost values."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.record_cost("goal-123", 0.123456)
                tracker.record_cost("goal-124", 0.654321)
                self.assertAlmostEqual(tracker.spend_today, 0.777777, places=5)

    def test_records_goal_id_in_log(self):
        """Include goal_id in logging."""
        with patch("src.core.initiative_tracker._get_config", return_value={}):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.daily_budget = 1.0
                with patch("src.core.initiative_tracker.logger") as mock_logger:
                    tracker.record_cost("my-goal-123", 0.25)
                    call_args = mock_logger.info.call_args[0]
                    self.assertIn("my-goal-123", str(call_args))


# ─────────────────────────────────────────────────────────────────────────────
# TestGetSummary
# ─────────────────────────────────────────────────────────────────────────────


class TestGetSummary(unittest.TestCase):
    """Test get_summary() — return summary dict."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_returns_dict_with_required_keys(self):
        """Summary dict has all required keys."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                summary = tracker.get_summary()

                self.assertIn("budget", summary)
                self.assertIn("spent", summary)
                self.assertIn("remaining", summary)
                self.assertIn("count", summary)
                self.assertIn("max_per_day", summary)

    def test_summary_budget_matches_config(self):
        """Summary budget matches daily_budget from config."""
        config = {"daily_budget": 1.50, "max_per_day": 3}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                summary = tracker.get_summary()
                self.assertEqual(summary["budget"], 1.50)

    def test_summary_spent_matches_state(self):
        """Summary spent matches spend_today."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.35
                summary = tracker.get_summary()
                self.assertEqual(summary["spent"], 0.35)

    def test_summary_remaining_calculated_correctly(self):
        """Summary remaining = budget - spent."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.35
                summary = tracker.get_summary()
                self.assertAlmostEqual(summary["remaining"], 0.65, places=4)

    def test_summary_remaining_capped_at_zero(self):
        """Summary remaining is 0 when overspent."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 1.5
                summary = tracker.get_summary()
                self.assertEqual(summary["remaining"], 0.0)

    def test_summary_count_matches_state(self):
        """Summary count matches count_today."""
        config = {"daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.count_today = 2
                summary = tracker.get_summary()
                self.assertEqual(summary["count"], 2)

    def test_summary_max_per_day_matches_config(self):
        """Summary max_per_day matches config."""
        config = {"daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                summary = tracker.get_summary()
                self.assertEqual(summary["max_per_day"], 5)

    def test_summary_spent_rounded_to_4_decimals(self):
        """Spent value rounded to 4 decimals in summary."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.123456789
                summary = tracker.get_summary()
                self.assertEqual(summary["spent"], 0.1235)

    def test_summary_remaining_rounded_to_4_decimals(self):
        """Remaining value rounded to 4 decimals in summary."""
        config = {"daily_budget": 0.5, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                tracker.spend_today = 0.123456789
                summary = tracker.get_summary()
                self.assertEqual(summary["remaining"], 0.3765)

    def test_summary_empty_state(self):
        """Summary of empty state."""
        config = {"daily_budget": 1.0, "max_per_day": 2}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                summary = tracker.get_summary()
                self.assertEqual(summary["spent"], 0.0)
                self.assertEqual(summary["remaining"], 1.0)
                self.assertEqual(summary["count"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestInitiativeTrackerIntegration(unittest.TestCase):
    """Integration tests for full workflows."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_full_workflow_record_and_check_budget(self):
        """Full workflow: record initiative, check budget, record cost."""
        config = {"daily_budget": 1.0, "max_per_day": 5, "enabled": True}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()

                # Initially can initiate
                self.assertTrue(tracker.can_initiate())

                # Record first initiative (record() only increments count, not spend)
                tracker.record("Task 1", "Important", 0.30, "goal-1")
                self.assertEqual(tracker.count_today, 1)
                self.assertEqual(tracker.budget_remaining(), 1.0)

                # Record cost
                tracker.record_cost("goal-1", 0.25)
                self.assertEqual(tracker.spend_today, 0.25)

                # Can still initiate
                self.assertTrue(tracker.can_initiate())

                # Record second initiative
                tracker.record("Task 2", "Also important", 0.40, "goal-2")
                self.assertEqual(tracker.count_today, 2)

                # Record cost
                tracker.record_cost("goal-2", 0.70)
                self.assertEqual(tracker.spend_today, 0.95)

                # Still can initiate (one slot left, budget left)
                self.assertTrue(tracker.can_initiate())

                # Get summary
                summary = tracker.get_summary()
                self.assertEqual(summary["spent"], 0.95)
                self.assertEqual(summary["count"], 2)
                self.assertAlmostEqual(summary["remaining"], 0.05, places=4)

    def test_persistence_across_instances(self):
        """State persists across InitiativeTracker instances on same day."""
        config = {"daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                # First instance
                tracker1 = InitiativeTracker()
                tracker1.record("Task 1", "Important", 0.30, "goal-1")
                tracker1.record_cost("goal-1", 0.25)

                # Second instance (same day)
                tracker2 = InitiativeTracker()
                self.assertEqual(tracker2.count_today, 1)
                self.assertEqual(tracker2.spend_today, 0.25)

    def test_day_reset_clears_state(self):
        """State resets on new day."""
        # Create state file with yesterday's date
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        state_data = {
            "date": yesterday,
            "spend": 0.50,
            "count": 2,
        }
        state_file = self.data_dir / "initiative_state.json"
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        config = {"daily_budget": 1.0, "max_per_day": 5}
        with patch("src.core.initiative_tracker._get_config", return_value=config):
            with patch("src.core.initiative_tracker._data_dir", return_value=self.data_dir):
                tracker = InitiativeTracker()
                # State should be reset
                self.assertEqual(tracker.count_today, 0)
                self.assertEqual(tracker.spend_today, 0.0)
                self.assertEqual(tracker.today, date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
