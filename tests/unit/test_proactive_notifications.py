"""Unit tests for proactive Discord notifications.

Tests send_finding_notification() in reporting — the function that
proactively messages Jesse about interesting findings from dream cycles.
"""

import time
from unittest.mock import patch, MagicMock

import pytest

from src.core import reporting


class TestSendFindingNotification:
    """Tests for send_finding_notification()."""

    def setup_method(self):
        """Reset the notification cooldown before each test."""
        reporting._last_finding_notify = 0.0

    def test_sends_notification(self):
        """Substantive finding triggers a Discord notification."""
        with patch("src.core.reporting._notify") as mock_notify:
            result = reporting.send_finding_notification(
                goal_desc="Health optimization research",
                finding_summary="Magnesium glycinate absorbs 3x better than oxide form.",
                files_created=["/workspace/projects/Health/supplements.md"],
            )
        assert result is True
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][0]
        assert "Magnesium glycinate" in msg
        assert "supplements.md" in msg

    def test_empty_summary_skipped(self):
        """Empty summary does not trigger notification."""
        with patch("src.core.reporting._notify") as mock_notify:
            result = reporting.send_finding_notification(
                goal_desc="Test", finding_summary="", files_created=[],
            )
        assert result is False
        mock_notify.assert_not_called()

    def test_short_summary_skipped(self):
        """Very short summary (< 15 chars) does not trigger notification."""
        with patch("src.core.reporting._notify") as mock_notify:
            result = reporting.send_finding_notification(
                goal_desc="Test", finding_summary="OK fine", files_created=[],
            )
        assert result is False
        mock_notify.assert_not_called()

    def test_cooldown_enforced(self):
        """Second notification within cooldown period is skipped."""
        with patch("src.core.reporting._notify"):
            # First one succeeds
            result1 = reporting.send_finding_notification(
                goal_desc="Test",
                finding_summary="First finding about something interesting here.",
                files_created=[],
            )
            # Second one within cooldown is skipped
            result2 = reporting.send_finding_notification(
                goal_desc="Test",
                finding_summary="Second finding about something else interesting.",
                files_created=[],
            )
        assert result1 is True
        assert result2 is False

    def test_cooldown_expired(self):
        """Notification succeeds after cooldown expires."""
        with patch("src.core.reporting._notify"):
            reporting.send_finding_notification(
                goal_desc="Test",
                finding_summary="First finding about something interesting here.",
                files_created=[],
            )
            # Simulate cooldown expiry
            reporting._last_finding_notify = time.time() - 2000
            result = reporting.send_finding_notification(
                goal_desc="Test",
                finding_summary="After cooldown finding about something else.",
                files_created=[],
            )
        assert result is True

    def test_message_includes_emoji(self):
        """Notification message starts with lightbulb emoji."""
        with patch("src.core.reporting._notify") as mock_notify:
            reporting.send_finding_notification(
                goal_desc="Test",
                finding_summary="An interesting finding about creatine timing.",
                files_created=[],
            )
        msg = mock_notify.call_args[0][0]
        assert msg.startswith("💡")

    def test_message_includes_file_names(self):
        """Notification includes created file names."""
        with patch("src.core.reporting._notify") as mock_notify:
            reporting.send_finding_notification(
                goal_desc="Test",
                finding_summary="An interesting finding about supplements worth noting.",
                files_created=[
                    "/path/to/workspace/projects/Health/report.md",
                    "/path/to/workspace/projects/Health/data.json",
                ],
            )
        msg = mock_notify.call_args[0][0]
        assert "report.md" in msg
        assert "data.json" in msg

    def test_no_files_no_file_line(self):
        """No file line when files_created is empty."""
        with patch("src.core.reporting._notify") as mock_notify:
            reporting.send_finding_notification(
                goal_desc="Test",
                finding_summary="A finding without any files created this time.",
                files_created=[],
            )
        msg = mock_notify.call_args[0][0]
        assert "Updated:" not in msg


class TestHourlySummaryFindings:
    """Verify hourly summary leads with key findings."""

    def test_hourly_includes_findings(self):
        """Hourly summary should include findings from the queue."""
        mock_finding = {
            "id": "find_abc",
            "summary": "Creatine timing matters for absorption.",
            "delivered": False,
        }

        mock_ifq = MagicMock()
        mock_ifq.get_next_undelivered.side_effect = [mock_finding, None]

        mock_findings_mod = MagicMock()
        mock_findings_mod.get_findings_queue.return_value = mock_ifq

        import sys
        with patch("src.core.reporting._notify") as mock_notify:
            with patch.dict(sys.modules, {"src.core.interesting_findings": mock_findings_mod}):
                reporting.send_hourly_summary([
                    {"success": True, "task": "Updated supplements", "files_created": []},
                ])

        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][0]
        assert "Key findings" in msg
        assert "Creatine timing" in msg
        mock_ifq.mark_delivered.assert_called_with("find_abc")
