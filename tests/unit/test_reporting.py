"""Unit tests for src/core/reporting.py."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.core.reporting import (
    _humanize_task,
    _pop_next_finding,
    load_overnight_results,
    save_overnight_results,
    send_finding_notification,
    send_user_goal_completion,
    _get_user_goal_progress,
    send_morning_report,
    send_hourly_summary,
)


# ── _humanize_task ──────────────────────────────────────────────────


class TestHumanizeTask:
    def test_empty_input(self):
        assert _humanize_task("") == "Background task"

    def test_none_input(self):
        assert _humanize_task(None) == "Background task"

    def test_human_readable_passthrough(self):
        text = "Research protein supplements"
        assert _humanize_task(text) == text

    def test_long_human_readable_truncated(self):
        text = "A" * 100
        result = _humanize_task(text)
        assert len(result) <= 61  # 60 + ellipsis

    def test_web_search_action(self):
        text = "web_search('best protein powder 2026 reviews')"
        result = _humanize_task(text)
        assert "Researched" in result
        assert "protein" in result.lower()

    def test_create_file_action(self):
        text = "create_file('workspace/projects/diet.md', 'content')"
        result = _humanize_task(text)
        assert "Created diet.md" in result

    def test_append_file_action(self):
        text = "append_file('workspace/notes.txt', 'more')"
        result = _humanize_task(text)
        assert "Updated notes.txt" in result

    def test_write_source_action(self):
        text = "write_source('src/tools/new_tool.py', 'code')"
        result = _humanize_task(text)
        assert "Wrote new_tool.py" in result

    def test_read_file_action(self):
        text = "read_file('workspace/data.json')"
        result = _humanize_task(text)
        assert "Reviewed project files" in result

    def test_list_files_action(self):
        text = "list_files('workspace/')"
        result = _humanize_task(text)
        assert "Reviewed project files" in result

    def test_fetch_webpage_action(self):
        text = "fetch_webpage('https://example.com')"
        result = _humanize_task(text)
        assert "Fetched web content" in result

    def test_edit_file_action(self):
        text = "edit_file('src/core/heartbeat.py', 'old', 'new')"
        result = _humanize_task(text)
        assert "Edited heartbeat.py" in result

    def test_compound_actions_deduped(self):
        text = "read_file('a.txt'); read_file('b.txt'); create_file('c.md', 'data')"
        result = _humanize_task(text)
        # "Reviewed project files" should appear once (deduped)
        assert result.count("Reviewed project files") == 1
        assert "Created c.md" in result

    def test_max_three_actions(self):
        text = "web_search('a'); web_search('b'); web_search('c'); web_search('d')"
        result = _humanize_task(text)
        assert result.count("Researched") <= 3


# ── _pop_next_finding ───────────────────────────────────────────────


class TestPopNextFinding:
    def test_returns_summary_and_marks_delivered(self):
        mock_module = MagicMock()
        mock_ifq = MagicMock()
        mock_ifq.get_next_undelivered.return_value = {"id": "f1", "summary": "Found cool stuff"}
        mock_module.get_findings_queue.return_value = mock_ifq
        with patch.dict("sys.modules", {"src.core.interesting_findings": mock_module}):
            result = _pop_next_finding()
        assert result == "Found cool stuff"
        mock_ifq.mark_delivered.assert_called_once_with("f1")

    def test_returns_none_on_import_error(self):
        with patch.dict("sys.modules", {"src.core.interesting_findings": None}):
            result = _pop_next_finding()
        assert result is None

    def test_returns_none_when_no_findings(self):
        mock_module = MagicMock()
        mock_ifq = MagicMock()
        mock_ifq.get_next_undelivered.return_value = None
        mock_module.get_findings_queue.return_value = mock_ifq
        with patch.dict("sys.modules", {"src.core.interesting_findings": mock_module}):
            result = _pop_next_finding()
        assert result is None


# ── load_overnight_results / save_overnight_results ─────────────────


class TestOvernightResultsPersistence:
    def test_load_valid_file(self, tmp_path):
        path = tmp_path / "overnight.json"
        data = [{"task": "t1", "success": True}]
        path.write_text(json.dumps(data))
        result = load_overnight_results(path)
        assert result == data

    def test_load_nonexistent_file(self, tmp_path):
        path = tmp_path / "missing.json"
        result = load_overnight_results(path)
        assert result == []

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        result = load_overnight_results(path)
        assert result == []

    def test_load_non_list_json(self, tmp_path):
        path = tmp_path / "dict.json"
        path.write_text('{"key": "val"}')
        result = load_overnight_results(path)
        assert result == []

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "overnight.json"
        data = [{"task": "t1", "success": True, "cost": 0.01}]
        save_overnight_results(data, path)
        assert path.exists()
        loaded = load_overnight_results(path)
        assert loaded == data

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "results.json"
        save_overnight_results([{"x": 1}], path)
        assert path.exists()


# ── send_finding_notification ───────────────────────────────────────


class TestSendFindingNotification:
    def setup_method(self):
        # Reset cooldown
        import src.core.reporting as rmod
        rmod._last_finding_notify = 0.0

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting.format_finding", create=True)
    def test_sends_notification(self, mock_format, mock_notify):
        mock_module = MagicMock()
        mock_module.format_finding.return_value = {"message": "Cool finding!"}
        with patch.dict("sys.modules", {"src.core.notification_formatter": mock_module}):
            result = send_finding_notification(
                "Research goal", "Interesting discovery about X", ["file.md"]
            )
        assert result is True

    def test_skips_empty_summary(self):
        result = send_finding_notification("goal", "", [])
        assert result is False

    def test_skips_short_summary(self):
        result = send_finding_notification("goal", "too short", [])
        assert result is False

    @patch("src.core.reporting._notify")
    def test_cooldown_prevents_spam(self, mock_notify):
        import src.core.reporting as rmod
        import time
        rmod._last_finding_notify = time.time()  # Just sent one
        result = send_finding_notification(
            "goal", "This is a long enough finding summary", []
        )
        assert result is False


# ── send_user_goal_completion ───────────────────────────────────────


class TestSendUserGoalCompletion:
    @patch("src.core.reporting._notify")
    def test_with_findings(self, mock_notify):
        results = [
            {"summary": "Done: Jesse, I researched protein supplements and found great options."},
            {"summary": "Done: Jesse, I compiled a comparison table of the top brands."},
        ]
        result = send_user_goal_completion("Research protein", results, [])
        assert result is True
        msg = mock_notify.call_args[0][0]
        assert "Research protein" in msg
        assert "researched protein" in msg.lower() or "Done" in msg

    @patch("src.core.reporting._notify")
    def test_without_findings_shows_files(self, mock_notify):
        result = send_user_goal_completion(
            "Build a tool", [{"summary": "completed"}], ["workspace/tool.py"]
        )
        assert result is True
        msg = mock_notify.call_args[0][0]
        assert "tool.py" in msg

    @patch("src.core.reporting._notify")
    def test_truncates_long_goal_label(self, mock_notify):
        long_goal = "A" * 200
        send_user_goal_completion(long_goal, [], [])
        msg = mock_notify.call_args[0][0]
        assert len(msg.split("\n")[0]) < 200


# ── _get_user_goal_progress ─────────────────────────────────────────


class TestGetUserGoalProgress:
    def test_returns_empty_on_import_error(self):
        with patch.dict("sys.modules", {"src.core.goal_manager": None}):
            result = _get_user_goal_progress()
        assert result == []

    def test_filters_non_user_goals(self):
        mock_goal = MagicMock()
        mock_goal.user_intent = "System suggested"
        mock_goal.description = "Auto-generated work"
        mock_gm = MagicMock()
        mock_gm.goals = {"g1": mock_goal}
        mock_module = MagicMock()
        mock_module.GoalManager.return_value = mock_gm
        with patch.dict("sys.modules", {"src.core.goal_manager": mock_module}):
            result = _get_user_goal_progress()
        assert result == []


# ── send_morning_report ─────────────────────────────────────────────


class TestSendMorningReport:
    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_skips_empty_results(self, mock_progress, mock_finding, mock_notify):
        send_morning_report([], Path("/tmp/overnight.json"))
        mock_notify.assert_not_called()

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_sends_report_with_results(self, mock_progress, mock_finding, mock_notify, tmp_path):
        results = [{"success": True, "cost": 0.01, "summary": "Did a thing"}]
        path = tmp_path / "overnight.json"
        mock_module = MagicMock()
        mock_module.format_morning_report.return_value = {"message": "Morning! Here's what I did."}
        with patch.dict("sys.modules", {"src.core.notification_formatter": mock_module}):
            send_morning_report(results, path)
        mock_notify.assert_called_once()

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_clears_results_after_report(self, mock_progress, mock_finding, mock_notify, tmp_path):
        results = [{"success": True}]
        path = tmp_path / "overnight.json"
        path.write_text("[]")
        mock_module = MagicMock()
        mock_module.format_morning_report.return_value = {"message": "Report"}
        with patch.dict("sys.modules", {"src.core.notification_formatter": mock_module}):
            send_morning_report(results, path)
        assert results == []  # Cleared

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_passes_journal_context_to_formatter(self, mock_progress, mock_finding, mock_notify, tmp_path):
        """Session 198: morning report should pass journal orientation to formatter."""
        results = [{"success": True, "cost": 0.01, "summary": "Did a thing"}]
        path = tmp_path / "overnight.json"
        mock_module = MagicMock()
        mock_module.format_morning_report.return_value = {"message": "Morning!"}
        mock_journal = MagicMock()
        mock_journal.get_orientation.return_value = "- Yesterday (3 tasks): 5 entries"
        with patch.dict("sys.modules", {
            "src.core.notification_formatter": mock_module,
            "src.core.journal": mock_journal,
        }):
            send_morning_report(results, path)
        # Verify formatter was called with journal_context kwarg
        call_kwargs = mock_module.format_morning_report.call_args[1]
        assert "journal_context" in call_kwargs
        assert "Yesterday" in call_kwargs["journal_context"]

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_journal_import_failure_graceful(self, mock_progress, mock_finding, mock_notify, tmp_path):
        """Session 198: journal import failure should not break morning report."""
        results = [{"success": True, "cost": 0.01}]
        path = tmp_path / "overnight.json"
        mock_module = MagicMock()
        mock_module.format_morning_report.return_value = {"message": "Morning!"}
        with patch.dict("sys.modules", {"src.core.notification_formatter": mock_module}), \
             patch("src.core.reporting.logger") as mock_logger:
            # Patch the journal import to raise
            import importlib
            with patch.dict("sys.modules", {"src.core.journal": None}):
                send_morning_report(results, path)
        # Should still send report (journal_context empty fallback)
        mock_notify.assert_called_once()


# ── send_hourly_summary ─────────────────────────────────────────────


class TestSendHourlySummary:
    @patch("src.core.reporting._notify")
    def test_skips_empty_results(self, mock_notify):
        send_hourly_summary([])
        mock_notify.assert_not_called()

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_sends_summary(self, mock_progress, mock_finding, mock_notify):
        results = [{"success": True, "files_created": ["workspace/out.md"]}]
        mock_module = MagicMock()
        mock_module.format_hourly_summary.return_value = {"message": "Hourly update"}
        with patch.dict("sys.modules", {"src.core.notification_formatter": mock_module}):
            send_hourly_summary(results)
        mock_notify.assert_called_once()

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_clears_results_after_summary(self, mock_progress, mock_finding, mock_notify):
        results = [{"success": True, "files_created": []}]
        mock_module = MagicMock()
        mock_module.format_hourly_summary.return_value = {"message": "Summary"}
        with patch.dict("sys.modules", {"src.core.notification_formatter": mock_module}):
            send_hourly_summary(results)
        assert results == []

    @patch("src.core.reporting._notify")
    @patch("src.core.reporting._pop_next_finding", return_value=None)
    @patch("src.core.reporting._get_user_goal_progress", return_value=[])
    def test_deduplicates_file_names(self, mock_progress, mock_finding, mock_notify):
        results = [
            {"success": True, "files_created": ["workspace/a.md", "workspace/b.md"]},
            {"success": True, "files_created": ["workspace/a.md"]},
        ]
        mock_module = MagicMock()
        mock_module.format_hourly_summary.return_value = {"message": "Summary"}
        with patch.dict("sys.modules", {"src.core.notification_formatter": mock_module}):
            send_hourly_summary(results)
        # Check the call args to format_hourly_summary
        call_kwargs = mock_module.format_hourly_summary.call_args
        files = call_kwargs.kwargs.get("files_created") or call_kwargs[1].get("files_created", [])
        assert files.count("a.md") == 1
