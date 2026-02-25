"""
Unit tests for time_awareness.py.

Covers: _parse_hour, _parse_working_hours, _load_config, _now_in_user_tz,
record_user_activity, _is_user_recently_active, is_quiet_hours, is_user_awake,
time_until_awake, get_user_hour, _reset_for_testing.

Testing patterns:
- Mock _now_in_user_tz for deterministic time control
- Call _reset_for_testing in setUp/tearDown for isolation
- Test edge cases (12 AM/PM hour parsing)
- Test activity override behavior
- Test wrapping midnight in working hours
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import src.utils.time_awareness as time_awareness


class TestParseHour:
    """Tests for _parse_hour function."""

    def test_parse_hour_9_am(self):
        """9 AM should parse to 9."""
        assert time_awareness._parse_hour("9 AM") == 9

    def test_parse_hour_9_am_lowercase(self):
        """9 am (lowercase) should parse to 9."""
        assert time_awareness._parse_hour("9 am") == 9

    def test_parse_hour_11_pm(self):
        """11 PM should parse to 23."""
        assert time_awareness._parse_hour("11 PM") == 23

    def test_parse_hour_11_pm_lowercase(self):
        """11 pm (lowercase) should parse to 23."""
        assert time_awareness._parse_hour("11 pm") == 23

    def test_parse_hour_12_am(self):
        """12 AM should parse to 0 (midnight)."""
        assert time_awareness._parse_hour("12 AM") == 0

    def test_parse_hour_12_pm(self):
        """12 PM should parse to 12 (noon)."""
        assert time_awareness._parse_hour("12 PM") == 12

    def test_parse_hour_1_am(self):
        """1 AM should parse to 1."""
        assert time_awareness._parse_hour("1 AM") == 1

    def test_parse_hour_1_pm(self):
        """1 PM should parse to 13."""
        assert time_awareness._parse_hour("1 PM") == 13

    def test_parse_hour_with_extra_whitespace(self):
        """Extra whitespace should be stripped."""
        assert time_awareness._parse_hour("  9   AM  ") == 9

    def test_parse_hour_6_am(self):
        """6 AM should parse to 6."""
        assert time_awareness._parse_hour("6 AM") == 6

    def test_parse_hour_6_pm(self):
        """6 PM should parse to 18."""
        assert time_awareness._parse_hour("6 PM") == 18

    def test_parse_hour_midnight_edge_case(self):
        """12:00 AM is midnight (hour 0)."""
        assert time_awareness._parse_hour("12AM") == 0

    def test_parse_hour_noon_edge_case(self):
        """12:00 PM is noon (hour 12)."""
        assert time_awareness._parse_hour("12PM") == 12


class TestParseWorkingHours:
    """Tests for _parse_working_hours function."""

    def test_parse_working_hours_normal_range(self):
        """Normal range '6 AM - 11 PM' should parse to (6, 23)."""
        assert time_awareness._parse_working_hours("6 AM - 11 PM") == (6, 23)

    def test_parse_working_hours_with_extra_spaces(self):
        """Extra spaces should be handled."""
        assert time_awareness._parse_working_hours("6 AM  -  11 PM") == (6, 23)

    def test_parse_working_hours_lowercase(self):
        """Lowercase should be handled."""
        assert time_awareness._parse_working_hours("6 am - 11 pm") == (6, 23)

    def test_parse_working_hours_custom_range(self):
        """Custom range '8 AM - 9 PM' should parse to (8, 21)."""
        assert time_awareness._parse_working_hours("8 AM - 9 PM") == (8, 21)

    def test_parse_working_hours_midnight_to_noon(self):
        """Wrapping range '12 AM - 12 PM' should parse to (0, 12)."""
        assert time_awareness._parse_working_hours("12 AM - 12 PM") == (0, 12)

    def test_parse_working_hours_invalid_no_hyphen(self):
        """Invalid format without hyphen should return defaults (6, 23)."""
        assert time_awareness._parse_working_hours("6 AM 11 PM") == (6, 23)

    def test_parse_working_hours_invalid_too_many_parts(self):
        """Invalid format with too many hyphens should return defaults (6, 23)."""
        assert time_awareness._parse_working_hours("6 AM - 11 PM - 2 AM") == (6, 23)

    def test_parse_working_hours_invalid_text(self):
        """Invalid hour text should return defaults (6, 23)."""
        assert time_awareness._parse_working_hours("invalid - text") == (6, 23)

    def test_parse_working_hours_empty_string(self):
        """Empty string should return defaults (6, 23)."""
        assert time_awareness._parse_working_hours("") == (6, 23)


class TestLoadConfig:
    """Tests for _load_config function."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    def test_load_config_sets_loaded_flag(self):
        """_load_config should set _loaded to True."""
        assert time_awareness._loaded is False
        time_awareness._load_config()
        assert time_awareness._loaded is True

    def test_load_config_idempotent(self):
        """Calling _load_config twice should not reload."""
        time_awareness._load_config()
        first_loaded = time_awareness._loaded
        # Modify a global to track if reload happened
        time_awareness._work_start = 999
        time_awareness._load_config()
        # Should stay 999 because _load_config returns early when _loaded=True
        assert time_awareness._work_start == 999

    @patch("src.utils.paths.project_root")
    def test_load_config_uses_defaults_on_missing_file(self, mock_project_root):
        """If config file missing, should use defaults and log warning."""
        # Mock project_root to return a non-existent path
        fake_root = Path("/nonexistent/path")
        mock_project_root.return_value = fake_root

        time_awareness._reset_for_testing()
        time_awareness._load_config()

        # Should still set _loaded flag even if file is missing
        assert time_awareness._loaded is True
        # Should keep defaults
        assert time_awareness._tz_name == "America/Chicago"
        assert time_awareness._work_start == 6
        assert time_awareness._work_end == 23
        assert time_awareness._activity_override_minutes == 30

    @patch("src.utils.paths.project_root")
    def test_load_config_parses_yaml(self, mock_project_root):
        """_load_config should parse YAML and extract user_context."""
        import tempfile
        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            config_dir = tmpdir_path / "config"
            config_dir.mkdir()
            config_file = config_dir / "archi_identity.yaml"

            # Write test config
            test_config = {
                "user_context": {
                    "timezone": "America/New_York",
                    "working_hours": "8 AM - 6 PM",
                    "activity_override_minutes": 45,
                }
            }
            with open(config_file, "w") as f:
                yaml.dump(test_config, f)

            mock_project_root.return_value = tmpdir_path
            time_awareness._reset_for_testing()
            time_awareness._load_config()

            assert time_awareness._tz_name == "America/New_York"
            assert time_awareness._work_start == 8
            assert time_awareness._work_end == 18
            assert time_awareness._activity_override_minutes == 45


class TestNowInUserTz:
    """Tests for _now_in_user_tz function."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    def test_now_in_user_tz_returns_datetime(self):
        """_now_in_user_tz should return a datetime object."""
        result = time_awareness._now_in_user_tz()
        assert isinstance(result, datetime)

    def test_now_in_user_tz_chicago_timezone(self):
        """Result should be in Chicago timezone (default)."""
        time_awareness._tz_name = "America/Chicago"
        result = time_awareness._now_in_user_tz()
        # Should have tzinfo set
        assert result.tzinfo is not None

    @patch("src.utils.time_awareness._load_config")
    def test_now_in_user_tz_calls_load_config(self, mock_load_config):
        """_now_in_user_tz should call _load_config."""
        time_awareness._now_in_user_tz()
        mock_load_config.assert_called_once()


class TestRecordUserActivity:
    """Tests for record_user_activity and _is_user_recently_active."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    @patch("src.utils.time_awareness._now_in_user_tz")
    def test_record_user_activity_sets_timestamp(self, mock_now):
        """record_user_activity should set _last_user_activity."""
        test_time = datetime(2025, 2, 24, 10, 30, 0)
        mock_now.return_value = test_time

        time_awareness.record_user_activity()

        assert time_awareness._last_user_activity == test_time

    @patch("src.utils.time_awareness._now_in_user_tz")
    def test_record_user_activity_updates_timestamp(self, mock_now):
        """record_user_activity should update timestamp on subsequent calls."""
        time1 = datetime(2025, 2, 24, 10, 30, 0)
        time2 = datetime(2025, 2, 24, 10, 35, 0)

        mock_now.return_value = time1
        time_awareness.record_user_activity()
        assert time_awareness._last_user_activity == time1

        mock_now.return_value = time2
        time_awareness.record_user_activity()
        assert time_awareness._last_user_activity == time2

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_user_recently_active_when_no_activity(self, mock_load, mock_now):
        """_is_user_recently_active should return False when no activity recorded."""
        assert time_awareness._is_user_recently_active() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_user_recently_active_within_window(self, mock_load, mock_now):
        """_is_user_recently_active should return True if activity within window."""
        time_awareness._activity_override_minutes = 30
        activity_time = datetime(2025, 2, 24, 10, 30, 0)
        current_time = datetime(2025, 2, 24, 10, 45, 0)  # 15 minutes later

        time_awareness._last_user_activity = activity_time
        mock_now.return_value = current_time

        assert time_awareness._is_user_recently_active() is True

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_user_recently_active_outside_window(self, mock_load, mock_now):
        """_is_user_recently_active should return False if activity outside window."""
        time_awareness._activity_override_minutes = 30
        activity_time = datetime(2025, 2, 24, 10, 30, 0)
        current_time = datetime(2025, 2, 24, 11, 15, 0)  # 45 minutes later

        time_awareness._last_user_activity = activity_time
        mock_now.return_value = current_time

        assert time_awareness._is_user_recently_active() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_user_recently_active_exactly_at_boundary(self, mock_load, mock_now):
        """Activity exactly at override window boundary should be False."""
        time_awareness._activity_override_minutes = 30
        activity_time = datetime(2025, 2, 24, 10, 30, 0)
        current_time = datetime(2025, 2, 24, 11, 0, 0)  # Exactly 30 minutes later

        time_awareness._last_user_activity = activity_time
        mock_now.return_value = current_time

        # 30 minutes is NOT less than 30 minutes
        assert time_awareness._is_user_recently_active() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_user_recently_active_one_minute_within(self, mock_load, mock_now):
        """Activity 1 minute within window should be True."""
        time_awareness._activity_override_minutes = 30
        activity_time = datetime(2025, 2, 24, 10, 30, 0)
        current_time = datetime(2025, 2, 24, 10, 59, 59)  # 29m59s later

        time_awareness._last_user_activity = activity_time
        mock_now.return_value = current_time

        assert time_awareness._is_user_recently_active() is True


class TestIsQuietHours:
    """Tests for is_quiet_hours function."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_during_work_hours(self, mock_load, mock_now):
        """During work hours (6 AM - 11 PM), should not be quiet."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 10 AM is within work hours
        mock_now.return_value = datetime(2025, 2, 24, 10, 0, 0)

        assert time_awareness.is_quiet_hours() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_after_work_hours(self, mock_load, mock_now):
        """After work hours (11 PM - 6 AM), should be quiet."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 11 PM (23:00) is start of quiet hours
        mock_now.return_value = datetime(2025, 2, 24, 23, 0, 0)

        assert time_awareness.is_quiet_hours() is True

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_before_work_hours(self, mock_load, mock_now):
        """Before work hours (12 AM - 6 AM), should be quiet."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 3 AM is before work hours
        mock_now.return_value = datetime(2025, 2, 24, 3, 0, 0)

        assert time_awareness.is_quiet_hours() is True

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_at_work_start_boundary(self, mock_load, mock_now):
        """At work start time, should not be quiet."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # Exactly 6 AM (start of work hours)
        mock_now.return_value = datetime(2025, 2, 24, 6, 0, 0)

        assert time_awareness.is_quiet_hours() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_just_before_work_start(self, mock_load, mock_now):
        """Just before work start, should be quiet."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 5:59 AM
        mock_now.return_value = datetime(2025, 2, 24, 5, 59, 0)

        assert time_awareness.is_quiet_hours() is True

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_just_before_work_end(self, mock_low, mock_now):
        """Just before work end, should not be quiet."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 10:59 PM
        mock_now.return_value = datetime(2025, 2, 24, 22, 59, 0)

        assert time_awareness.is_quiet_hours() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    @patch("src.utils.time_awareness._is_user_recently_active")
    def test_is_quiet_hours_suppressed_by_activity(self, mock_active, mock_load, mock_now):
        """With recent activity, should suppress quiet hours even outside work hours."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 3 AM (normally quiet hours)
        mock_now.return_value = datetime(2025, 2, 24, 3, 0, 0)
        mock_active.return_value = True

        assert time_awareness.is_quiet_hours() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_wrapping_midnight_format(self, mock_load, mock_now):
        """Work hours wrapping midnight (e.g., 22-8) should be handled."""
        # Work hours from 10 PM to 8 AM (wrapping midnight)
        time_awareness._work_start = 22
        time_awareness._work_end = 8
        # 11 PM is within work hours
        mock_now.return_value = datetime(2025, 2, 24, 23, 0, 0)

        assert time_awareness.is_quiet_hours() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_quiet_hours_wrapping_midnight_outside(self, mock_load, mock_now):
        """Work hours wrapping midnight, but checking outside (e.g., 10 AM)."""
        # Work hours from 10 PM to 8 AM
        time_awareness._work_start = 22
        time_awareness._work_end = 8
        # 10 AM is outside work hours
        mock_now.return_value = datetime(2025, 2, 24, 10, 0, 0)

        assert time_awareness.is_quiet_hours() is True


class TestIsUserAwake:
    """Tests for is_user_awake function."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_user_awake_during_work_hours(self, mock_load, mock_now):
        """is_user_awake is True when during work hours."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        mock_now.return_value = datetime(2025, 2, 24, 10, 0, 0)

        assert time_awareness.is_user_awake() is True

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_is_user_awake_outside_work_hours(self, mock_load, mock_now):
        """is_user_awake is False when outside work hours (no activity)."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        mock_now.return_value = datetime(2025, 2, 24, 3, 0, 0)

        assert time_awareness.is_user_awake() is False

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    @patch("src.utils.time_awareness._is_user_recently_active")
    def test_is_user_awake_with_recent_activity(self, mock_active, mock_load, mock_now):
        """is_user_awake is True when user is recently active."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # Outside work hours
        mock_now.return_value = datetime(2025, 2, 24, 3, 0, 0)
        mock_active.return_value = True

        assert time_awareness.is_user_awake() is True

    @patch("src.utils.time_awareness.is_quiet_hours")
    def test_is_user_awake_inverse_of_quiet_hours(self, mock_quiet):
        """is_user_awake should be the inverse of is_quiet_hours."""
        mock_quiet.return_value = True
        assert time_awareness.is_user_awake() is False

        mock_quiet.return_value = False
        assert time_awareness.is_user_awake() is True


class TestTimeUntilAwake:
    """Tests for time_until_awake function."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_time_until_awake_during_work_hours(self, mock_load, mock_now):
        """During work hours, time_until_awake should return timedelta(0)."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        mock_now.return_value = datetime(2025, 2, 24, 10, 0, 0)

        result = time_awareness.time_until_awake()
        assert result == timedelta(0)

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_time_until_awake_before_work_start(self, mock_load, mock_now):
        """Before work start, should calculate hours until work_start."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 3 AM - should wait 3 hours until 6 AM
        mock_now.return_value = datetime(2025, 2, 24, 3, 0, 0)

        result = time_awareness.time_until_awake()
        # Should be approximately 3 hours
        assert result > timedelta(hours=2, minutes=59)
        assert result < timedelta(hours=3, minutes=1)

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_time_until_awake_after_work_end(self, mock_load, mock_now):
        """After work end, should calculate hours until next day's work_start."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 11 PM (23:00) - should wait 7 hours until next 6 AM
        mock_now.return_value = datetime(2025, 2, 24, 23, 0, 0)

        result = time_awareness.time_until_awake()
        # Should be approximately 7 hours (23:00 + 7 = 6:00 next day)
        assert result > timedelta(hours=6, minutes=59)
        assert result < timedelta(hours=7, minutes=1)

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_time_until_awake_midnight(self, mock_load, mock_now):
        """At midnight (0:00), should wait 6 hours until 6 AM."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        mock_now.return_value = datetime(2025, 2, 24, 0, 0, 0)

        result = time_awareness.time_until_awake()
        # Should be approximately 6 hours
        assert result > timedelta(hours=5, minutes=59)
        assert result < timedelta(hours=6, minutes=1)

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_time_until_awake_just_before_work_start(self, mock_load, mock_now):
        """Just before work start, should have small timedelta."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # 5:50 AM - should wait 10 minutes until 6 AM
        mock_now.return_value = datetime(2025, 2, 24, 5, 50, 0)

        result = time_awareness.time_until_awake()
        # Should be approximately 10 minutes
        assert result > timedelta(minutes=9, seconds=59)
        assert result < timedelta(minutes=10, seconds=1)

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    @patch("src.utils.time_awareness._is_user_recently_active")
    def test_time_until_awake_with_activity_override(self, mock_active, mock_load, mock_now):
        """With activity override, time_until_awake should return 0."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        # Outside work hours but user is active
        mock_now.return_value = datetime(2025, 2, 24, 3, 0, 0)
        mock_active.return_value = True

        result = time_awareness.time_until_awake()
        assert result == timedelta(0)


class TestGetUserHour:
    """Tests for get_user_hour function."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_get_user_hour_returns_int(self, mock_load, mock_now):
        """get_user_hour should return an integer."""
        mock_now.return_value = datetime(2025, 2, 24, 10, 30, 45)
        result = time_awareness.get_user_hour()
        assert isinstance(result, int)

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_get_user_hour_returns_current_hour(self, mock_load, mock_now):
        """get_user_hour should return the current hour (0-23)."""
        mock_now.return_value = datetime(2025, 2, 24, 10, 30, 45)
        result = time_awareness.get_user_hour()
        assert result == 10

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_get_user_hour_midnight(self, mock_load, mock_now):
        """get_user_hour at midnight should return 0."""
        mock_now.return_value = datetime(2025, 2, 24, 0, 0, 0)
        result = time_awareness.get_user_hour()
        assert result == 0

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_get_user_hour_late_evening(self, mock_load, mock_now):
        """get_user_hour in late evening should return correct hour."""
        mock_now.return_value = datetime(2025, 2, 24, 23, 59, 59)
        result = time_awareness.get_user_hour()
        assert result == 23


class TestResetForTesting:
    """Tests for _reset_for_testing function."""

    def test_reset_clears_loaded_flag(self):
        """_reset_for_testing should set _loaded to False."""
        time_awareness._loaded = True
        time_awareness._reset_for_testing()
        assert time_awareness._loaded is False

    def test_reset_clears_last_user_activity(self):
        """_reset_for_testing should set _last_user_activity to None."""
        time_awareness._last_user_activity = datetime.now()
        time_awareness._reset_for_testing()
        assert time_awareness._last_user_activity is None

    def test_reset_restores_default_timezone(self):
        """_reset_for_testing should restore default timezone."""
        time_awareness._tz_name = "America/New_York"
        time_awareness._reset_for_testing()
        assert time_awareness._tz_name == "America/Chicago"

    def test_reset_restores_default_work_hours(self):
        """_reset_for_testing should restore default work hours."""
        time_awareness._work_start = 8
        time_awareness._work_end = 20
        time_awareness._reset_for_testing()
        assert time_awareness._work_start == 6
        assert time_awareness._work_end == 23

    def test_reset_restores_default_activity_override_minutes(self):
        """_reset_for_testing should restore default activity_override_minutes."""
        time_awareness._activity_override_minutes = 60
        time_awareness._reset_for_testing()
        assert time_awareness._activity_override_minutes == 30

    def test_reset_for_testing_idempotent(self):
        """Calling _reset_for_testing multiple times should be safe."""
        time_awareness._work_start = 999
        time_awareness._reset_for_testing()
        assert time_awareness._work_start == 6
        time_awareness._reset_for_testing()
        assert time_awareness._work_start == 6


class TestIntegration:
    """Integration tests combining multiple functions."""

    def setup_method(self):
        """Reset module state before each test."""
        time_awareness._reset_for_testing()

    def teardown_method(self):
        """Clean up after each test."""
        time_awareness._reset_for_testing()

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_work_hours_and_activity_override_flow(self, mock_load, mock_now):
        """Test full flow: recording activity and checking quiet hours."""
        time_awareness._work_start = 6
        time_awareness._work_end = 23
        time_awareness._activity_override_minutes = 30

        # User is inactive at 3 AM (quiet hours)
        mock_now.return_value = datetime(2025, 2, 24, 3, 0, 0)
        assert time_awareness.is_quiet_hours() is True
        assert time_awareness.is_user_awake() is False

        # User sends a message
        time_awareness._last_user_activity = datetime(2025, 2, 24, 3, 0, 0)
        mock_now.return_value = datetime(2025, 2, 24, 3, 10, 0)

        # Now Archi should be responsive (activity override)
        assert time_awareness.is_quiet_hours() is False
        assert time_awareness.is_user_awake() is True

        # After 30+ minutes with no new activity
        mock_now.return_value = datetime(2025, 2, 24, 3, 31, 0)
        # Back to quiet hours
        assert time_awareness.is_quiet_hours() is True

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_parse_and_use_working_hours(self, mock_load, mock_now):
        """Test that parsed working hours are used correctly."""
        time_awareness._work_start, time_awareness._work_end = (
            time_awareness._parse_working_hours("8 AM - 6 PM")
        )

        # 8 AM should be awake
        mock_now.return_value = datetime(2025, 2, 24, 8, 0, 0)
        assert time_awareness.is_user_awake() is True

        # 7 AM should be asleep
        mock_now.return_value = datetime(2025, 2, 24, 7, 0, 0)
        assert time_awareness.is_user_awake() is False

        # 6 PM (18:00) should be asleep
        mock_now.return_value = datetime(2025, 2, 24, 18, 0, 0)
        assert time_awareness.is_user_awake() is False

        # 5:59 PM should be awake
        mock_now.return_value = datetime(2025, 2, 24, 17, 59, 0)
        assert time_awareness.is_user_awake() is True

    @patch("src.utils.time_awareness._now_in_user_tz")
    @patch("src.utils.time_awareness._load_config")
    def test_multiple_activity_records(self, mock_load, mock_now):
        """Test recording multiple activities within and outside override window."""
        time_awareness._activity_override_minutes = 30

        # First activity at 3 AM
        activity1 = datetime(2025, 2, 24, 3, 0, 0)
        time_awareness._last_user_activity = activity1

        # Check at 3:15 (within window)
        mock_now.return_value = datetime(2025, 2, 24, 3, 15, 0)
        assert time_awareness._is_user_recently_active() is True

        # Check at 3:40 (outside window)
        mock_now.return_value = datetime(2025, 2, 24, 3, 40, 0)
        assert time_awareness._is_user_recently_active() is False

        # New activity at 3:45
        time_awareness._last_user_activity = datetime(2025, 2, 24, 3, 45, 0)

        # Check at 4:10 (within new window)
        mock_now.return_value = datetime(2025, 2, 24, 4, 10, 0)
        assert time_awareness._is_user_recently_active() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
