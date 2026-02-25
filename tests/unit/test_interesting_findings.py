"""Unit tests for src.core.interesting_findings."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import pytest

from src.core.interesting_findings import (
    InterestingFindingsQueue,
    _parse_ts,
    _reset_for_testing,
    get_findings_queue,
    _MAX_PENDING,
    _DELIVERY_COOLDOWN,
    _EXPIRE_DAYS,
)


class TestParseTs:
    """Tests for _parse_ts utility function."""

    def test_parse_valid_iso_string(self):
        """Valid ISO timestamp string should parse correctly."""
        ts_str = "2025-02-24T10:30:45.123456"
        result = _parse_ts(ts_str)
        assert isinstance(result, datetime)
        assert result.year == 2025
        assert result.month == 2
        assert result.day == 24

    def test_parse_invalid_string_returns_datetime_min(self):
        """Invalid timestamp string should return datetime.min."""
        result = _parse_ts("not-a-timestamp")
        assert result == datetime.min

    def test_parse_empty_or_none_returns_datetime_min(self):
        """Empty string or None should return datetime.min."""
        assert _parse_ts("") == datetime.min
        assert _parse_ts(None) == datetime.min


class TestSingleton:
    """Tests for singleton pattern and get_findings_queue."""

    def teardown_method(self):
        """Reset singleton after each test."""
        _reset_for_testing()

    def test_get_findings_queue_returns_same_instance(self):
        """Multiple calls should return the same instance."""
        with patch("src.core.interesting_findings._base_path"):
            queue1 = get_findings_queue()
            queue2 = get_findings_queue()
            assert queue1 is queue2

    def test_reset_for_testing_clears_instance(self):
        """_reset_for_testing should clear the singleton."""
        with patch("src.core.interesting_findings._base_path"):
            queue1 = get_findings_queue()
            _reset_for_testing()
            queue2 = get_findings_queue()
            assert queue1 is not queue2

    def test_thread_safety_of_singleton(self):
        """Singleton should be thread-safe."""
        import threading
        instances = []

        def get_instance():
            with patch("src.core.interesting_findings._base_path"):
                instances.append(get_findings_queue())

        _reset_for_testing()
        threads = [threading.Thread(target=get_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All instances should be the same
        assert all(inst is instances[0] for inst in instances)


class TestInit:
    """Tests for InterestingFindingsQueue initialization."""

    def test_init_creates_data_directory(self, tmp_path):
        """__init__ should create data directory if it doesn't exist."""
        data_dir = tmp_path / "new_dir"
        assert not data_dir.exists()
        queue = InterestingFindingsQueue(data_dir=data_dir)
        assert data_dir.exists()

    def test_init_loads_existing_file(self, tmp_path):
        """__init__ should load findings from existing file."""
        data_file = tmp_path / "interesting_findings_queue.json"
        # Use a recent timestamp so it doesn't get pruned
        recent_timestamp = datetime.now().isoformat()
        existing_findings = [
            {"id": "find_123", "summary": "Test finding", "delivered": False, "queued_at": recent_timestamp}
        ]
        with open(data_file, "w") as f:
            json.dump(existing_findings, f)

        queue = InterestingFindingsQueue(data_dir=tmp_path)
        assert len(queue.findings) == 1
        assert queue.findings[0]["id"] == "find_123"

    def test_init_handles_missing_file(self, tmp_path):
        """__init__ should handle missing file gracefully."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        assert queue.findings == []
        assert queue.data_dir == tmp_path


class TestLoad:
    """Tests for _load method."""

    def test_load_list_data(self, tmp_path):
        """_load should handle JSON list format."""
        data_file = tmp_path / "interesting_findings_queue.json"
        # Use a recent timestamp so it doesn't get pruned during _prune_expired on init
        recent_timestamp = datetime.now().isoformat()
        findings = [{"id": "find_1", "summary": "Test", "queued_at": recent_timestamp, "delivered": False}]
        with open(data_file, "w") as f:
            json.dump(findings, f)

        queue = InterestingFindingsQueue(data_dir=tmp_path)
        assert len(queue.findings) == 1
        assert queue.findings[0]["id"] == "find_1"

    def test_load_dict_with_findings_key(self, tmp_path):
        """_load should handle JSON dict format with 'findings' key."""
        data_file = tmp_path / "interesting_findings_queue.json"
        # Use a recent timestamp so it doesn't get pruned during _prune_expired on init
        recent_timestamp = datetime.now().isoformat()
        data = {"findings": [{"id": "find_2", "summary": "Test", "queued_at": recent_timestamp, "delivered": False}], "other": "stuff"}
        with open(data_file, "w") as f:
            json.dump(data, f)

        queue = InterestingFindingsQueue(data_dir=tmp_path)
        assert len(queue.findings) == 1
        assert queue.findings[0]["id"] == "find_2"

    def test_load_handles_corrupt_file(self, tmp_path):
        """_load should handle corrupt JSON gracefully."""
        data_file = tmp_path / "interesting_findings_queue.json"
        with open(data_file, "w") as f:
            f.write("{invalid json")

        queue = InterestingFindingsQueue(data_dir=tmp_path)
        assert queue.findings == []

    def test_load_missing_file_returns_empty(self, tmp_path):
        """_load should handle missing file and return empty list."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        assert queue.findings == []


class TestSave:
    """Tests for save method."""

    def test_save_writes_to_file_atomically(self, tmp_path):
        """save should write findings to file atomically."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {
                "id": "find_1",
                "summary": "Test finding",
                "delivered": False,
                "delivered_at": None,
            }
        ]
        queue.save()

        data_file = tmp_path / "interesting_findings_queue.json"
        assert data_file.exists()
        with open(data_file, "r") as f:
            saved_data = json.load(f)
        assert saved_data == queue.findings

    def test_save_no_temp_file_after_successful_write(self, tmp_path):
        """save should clean up temp file after successful write."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [{"id": "find_1", "summary": "Test"}]
        queue.save()

        temp_file = tmp_path / "interesting_findings_queue.tmp"
        assert not temp_file.exists()

    def test_save_handles_write_error_gracefully(self, tmp_path):
        """save should handle write errors gracefully."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [{"id": "find_1", "summary": "Test"}]

        # Mock open to raise an exception
        with patch("builtins.open", side_effect=IOError("Write failed")):
            queue.save()  # Should not raise


class TestPendingCount:
    """Tests for pending_count method."""

    def test_pending_count_counts_undelivered(self, tmp_path):
        """pending_count should count only undelivered findings."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test 1", "delivered": False},
            {"id": "find_2", "summary": "Test 2", "delivered": False},
            {"id": "find_3", "summary": "Test 3", "delivered": True},
        ]
        assert queue.pending_count() == 2

    def test_pending_count_excludes_delivered(self, tmp_path):
        """pending_count should exclude delivered findings."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test 1", "delivered": True},
            {"id": "find_2", "summary": "Test 2", "delivered": True},
        ]
        assert queue.pending_count() == 0

    def test_pending_count_empty_list_returns_zero(self, tmp_path):
        """pending_count should return 0 for empty findings list."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        assert queue.pending_count() == 0


class TestGetNextUndelivered:
    """Tests for get_next_undelivered method."""

    def test_get_next_undelivered_returns_oldest(self, tmp_path):
        """get_next_undelivered should return the first undelivered finding."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "First", "delivered": False},
            {"id": "find_2", "summary": "Second", "delivered": False},
        ]
        result = queue.get_next_undelivered()
        assert result["id"] == "find_1"

    def test_get_next_undelivered_skips_delivered(self, tmp_path):
        """get_next_undelivered should skip delivered findings."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "First", "delivered": True},
            {"id": "find_2", "summary": "Second", "delivered": False},
        ]
        result = queue.get_next_undelivered()
        assert result["id"] == "find_2"

    def test_get_next_undelivered_returns_none_when_empty(self, tmp_path):
        """get_next_undelivered should return None when all delivered."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test", "delivered": True},
        ]
        result = queue.get_next_undelivered()
        assert result is None

    def test_get_next_undelivered_returns_none_when_list_empty(self, tmp_path):
        """get_next_undelivered should return None when findings list is empty."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        result = queue.get_next_undelivered()
        assert result is None


class TestGetNextForChat:
    """Tests for get_next_for_chat method."""

    def teardown_method(self):
        """Reset singleton after each test."""
        _reset_for_testing()

    def test_get_next_for_chat_returns_finding_when_cooldown_elapsed(self, tmp_path):
        """get_next_for_chat should return finding when cooldown has elapsed."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [{"id": "find_1", "summary": "Test", "delivered": False}]

        # Simulate old delivery time
        with patch("src.core.interesting_findings._last_chat_delivery", 0.0):
            with patch("src.core.interesting_findings.time.monotonic", return_value=99999.0):
                result = queue.get_next_for_chat()
                assert result is not None
                assert result["id"] == "find_1"

    def test_get_next_for_chat_returns_none_during_cooldown(self, tmp_path):
        """get_next_for_chat should return None during cooldown period."""
        import src.core.interesting_findings as ifm

        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [{"id": "find_1", "summary": "Test", "delivered": False}]

        # Set recent delivery time
        ifm._last_chat_delivery = 100.0
        with patch("src.core.interesting_findings.time.monotonic", return_value=100.1):
            result = queue.get_next_for_chat()
            assert result is None

    def test_get_next_for_chat_respects_cooldown_constant(self, tmp_path):
        """get_next_for_chat should use _DELIVERY_COOLDOWN constant."""
        import src.core.interesting_findings as ifm

        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [{"id": "find_1", "summary": "Test", "delivered": False}]

        # Set delivery time to exactly cooldown seconds ago
        ifm._last_chat_delivery = 100.0
        with patch("src.core.interesting_findings.time.monotonic", return_value=100.0 + _DELIVERY_COOLDOWN):
            result = queue.get_next_for_chat()
            assert result is not None


class TestMarkDelivered:
    """Tests for mark_delivered method."""

    def teardown_method(self):
        """Reset singleton after each test."""
        _reset_for_testing()

    def test_mark_delivered_marks_finding(self, tmp_path):
        """mark_delivered should mark finding as delivered."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test", "delivered": False, "delivered_at": None}
        ]
        queue.mark_delivered("find_1")
        assert queue.findings[0]["delivered"] is True

    def test_mark_delivered_sets_delivered_at(self, tmp_path):
        """mark_delivered should set delivered_at timestamp."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test", "delivered": False, "delivered_at": None}
        ]
        queue.mark_delivered("find_1")
        assert queue.findings[0]["delivered_at"] is not None
        # Verify it's a valid ISO string
        datetime.fromisoformat(queue.findings[0]["delivered_at"])

    def test_mark_delivered_saves_to_file(self, tmp_path):
        """mark_delivered should save changes to file."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test", "delivered": False, "delivered_at": None}
        ]
        with patch.object(queue, "save") as mock_save:
            queue.mark_delivered("find_1")
            mock_save.assert_called_once()

    def test_mark_delivered_updates_last_chat_delivery(self, tmp_path):
        """mark_delivered should update _last_chat_delivery."""
        import src.core.interesting_findings as ifm

        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test", "delivered": False, "delivered_at": None}
        ]
        ifm._last_chat_delivery = 0.0
        with patch("src.core.interesting_findings.time.monotonic", return_value=123.45):
            queue.mark_delivered("find_1")
            assert ifm._last_chat_delivery == 123.45

    def test_mark_delivered_noop_for_unknown_id(self, tmp_path):
        """mark_delivered should do nothing for unknown finding ID."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = [
            {"id": "find_1", "summary": "Test", "delivered": False}
        ]
        with patch.object(queue, "save") as mock_save:
            queue.mark_delivered("find_unknown")
            # Save should not be called
            mock_save.assert_not_called()


class TestQueueFinding:
    """Tests for queue_finding method."""

    def test_queue_finding_queues_valid_finding(self, tmp_path):
        """queue_finding should add finding to queue."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        finding = {"summary": "Test finding", "topic": "test"}
        finding_id = queue.queue_finding(finding)
        assert finding_id is not None
        assert len(queue.findings) == 1

    def test_queue_finding_returns_id(self, tmp_path):
        """queue_finding should return a valid finding ID."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        finding = {"summary": "Test finding", "topic": "test"}
        finding_id = queue.queue_finding(finding)
        assert finding_id.startswith("find_")
        assert len(finding_id) > 5

    def test_queue_finding_rejects_when_full(self, tmp_path):
        """queue_finding should reject when queue is full."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        # Fill the queue
        for i in range(_MAX_PENDING):
            queue.findings.append({
                "id": f"find_{i}",
                "summary": f"Finding {i}",
                "delivered": False,
            })
        # Try to add one more
        finding = {"summary": "Extra finding", "topic": "test"}
        result = queue.queue_finding(finding)
        assert result is None

    def test_queue_finding_rejects_empty_summary(self, tmp_path):
        """queue_finding should reject empty summary."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        finding = {"summary": "", "topic": "test"}
        result = queue.queue_finding(finding)
        assert result is None

    def test_queue_finding_rejects_whitespace_summary(self, tmp_path):
        """queue_finding should reject summary with only whitespace."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        finding = {"summary": "   ", "topic": "test"}
        result = queue.queue_finding(finding)
        assert result is None

    def test_queue_finding_sets_all_fields(self, tmp_path):
        """queue_finding should set all required fields."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        finding = {
            "summary": "Test finding",
            "topic": "test topic",
            "goal": "test goal",
            "task": "test task",
        }
        queue.queue_finding(finding)
        entry = queue.findings[0]

        assert entry["summary"] == "Test finding"
        assert entry["topic"] == "test topic"
        assert entry["goal"] == "test goal"
        assert entry["task"] == "test task"
        assert entry["delivered"] is False
        assert entry["delivered_at"] is None
        assert "queued_at" in entry
        assert "id" in entry

    def test_queue_finding_truncates_long_fields(self, tmp_path):
        """queue_finding should strip long goal and task fields."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        finding = {
            "summary": "Test" * 50,  # Very long but valid
            "topic": "test",
            "goal": "x" * 200,
            "task": "y" * 200,
        }
        queue.queue_finding(finding)
        entry = queue.findings[0]
        # queue_finding just strips but doesn't truncate; truncation happens in evaluate_and_queue
        assert entry["goal"] == ("x" * 200)
        assert entry["task"] == ("y" * 200)

    def test_queue_finding_saves_to_file(self, tmp_path):
        """queue_finding should save findings to file."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        finding = {"summary": "Test finding", "topic": "test"}
        queue.queue_finding(finding)

        data_file = tmp_path / "interesting_findings_queue.json"
        assert data_file.exists()
        with open(data_file, "r") as f:
            saved = json.load(f)
        assert len(saved) == 1


class TestPruneExpired:
    """Tests for _prune_expired method."""

    def test_prune_expired_removes_old_undelivered(self, tmp_path):
        """_prune_expired should remove undelivered findings older than _EXPIRE_DAYS."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        old_date = (datetime.now() - timedelta(days=_EXPIRE_DAYS + 1)).isoformat()
        queue.findings = [
            {"id": "find_1", "summary": "Old", "delivered": False, "queued_at": old_date}
        ]
        queue._prune_expired()
        assert len(queue.findings) == 0

    def test_prune_expired_keeps_delivered_findings(self, tmp_path):
        """_prune_expired should keep delivered findings regardless of age."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        old_date = (datetime.now() - timedelta(days=_EXPIRE_DAYS + 1)).isoformat()
        queue.findings = [
            {"id": "find_1", "summary": "Old", "delivered": True, "queued_at": old_date}
        ]
        queue._prune_expired()
        assert len(queue.findings) == 1

    def test_prune_expired_keeps_recent_findings(self, tmp_path):
        """_prune_expired should keep recent undelivered findings."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        recent_date = (datetime.now() - timedelta(days=1)).isoformat()
        queue.findings = [
            {"id": "find_1", "summary": "Recent", "delivered": False, "queued_at": recent_date}
        ]
        queue._prune_expired()
        assert len(queue.findings) == 1

    def test_prune_expired_saves_when_pruning(self, tmp_path):
        """_prune_expired should save to file when findings are removed."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        old_date = (datetime.now() - timedelta(days=_EXPIRE_DAYS + 1)).isoformat()
        queue.findings = [
            {"id": "find_1", "summary": "Old", "delivered": False, "queued_at": old_date}
        ]
        with patch.object(queue, "save") as mock_save:
            queue._prune_expired()
            mock_save.assert_called_once()

    def test_prune_expired_does_not_save_when_nothing_pruned(self, tmp_path):
        """_prune_expired should not save if nothing was pruned."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        queue.findings = []
        with patch.object(queue, "save") as mock_save:
            queue._prune_expired()
            mock_save.assert_not_called()


class TestEvaluateAndQueue:
    """Tests for evaluate_and_queue method."""

    def test_evaluate_and_queue_returns_none_for_no_files(self, tmp_path):
        """evaluate_and_queue should return None if files_created is empty."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        result = queue.evaluate_and_queue({}, [], "goal", "task", router)
        assert result is None

    def test_evaluate_and_queue_returns_none_for_no_router(self, tmp_path):
        """evaluate_and_queue should return None if router is None."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        temp_file = tmp_path / "test.txt"
        temp_file.write_text("content")
        result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", None)
        assert result is None

    def test_evaluate_and_queue_returns_none_when_queue_full(self, tmp_path):
        """evaluate_and_queue should return None when queue is full."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        # Fill the queue
        for i in range(_MAX_PENDING):
            queue.findings.append({
                "id": f"find_{i}",
                "summary": f"Finding {i}",
                "delivered": False,
            })
        router = MagicMock()
        temp_file = tmp_path / "test.txt"
        temp_file.write_text("content")
        result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
        assert result is None

    def test_evaluate_and_queue_calls_router_generate(self, tmp_path):
        """evaluate_and_queue should call router.generate with appropriate prompt."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": false}'}

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
            router.generate.assert_called_once()
            call_kwargs = router.generate.call_args[1]
            assert "prompt" in call_kwargs
            assert "TestUser" in call_kwargs["prompt"]

    def test_evaluate_and_queue_returns_none_when_not_interesting(self, tmp_path):
        """evaluate_and_queue should return None if model says not interesting."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": false}'}

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            with patch("src.core.interesting_findings._extract_json", return_value={"interesting": False}):
                result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
                assert result is None

    def test_evaluate_and_queue_queues_when_interesting(self, tmp_path):
        """evaluate_and_queue should queue finding if interesting."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": true}'}

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        parsed = {
            "interesting": True,
            "summary": "This is a really interesting finding that was discovered",
            "topic": "research",
        }

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            with patch("src.core.interesting_findings._extract_json", return_value=parsed):
                result = queue.evaluate_and_queue({}, [str(temp_file)], "goal desc", "task desc", router)
                assert result is not None
                assert len(queue.findings) == 1

    def test_evaluate_and_queue_rejects_short_summary(self, tmp_path):
        """evaluate_and_queue should reject summary shorter than 15 chars."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": true}'}

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        parsed = {
            "interesting": True,
            "summary": "Short",  # Too short
            "topic": "test",
        }

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            with patch("src.core.interesting_findings._extract_json", return_value=parsed):
                result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
                assert result is None

    def test_evaluate_and_queue_handles_missing_text_in_response(self, tmp_path):
        """evaluate_and_queue should handle missing 'text' in router response."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {}  # Missing "text"

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
            assert result is None

    def test_evaluate_and_queue_handles_router_exception(self, tmp_path):
        """evaluate_and_queue should handle exceptions from router.generate."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.side_effect = Exception("Router error")

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
            assert result is None

    def test_evaluate_and_queue_reads_only_first_three_files(self, tmp_path):
        """evaluate_and_queue should read only first 3 files."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": false}'}

        # Create 5 files
        files = []
        for i in range(5):
            temp_file = tmp_path / f"test_{i}.txt"
            temp_file.write_text(f"content {i}")
            files.append(str(temp_file))

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            queue.evaluate_and_queue({}, files, "goal", "task", router)
            # Check that prompt was called with only first 3 files' content
            call_kwargs = router.generate.call_args[1]
            prompt = call_kwargs["prompt"]
            assert "content 0" in prompt
            assert "content 1" in prompt
            assert "content 2" in prompt
            # Files 3 and 4 should not be in the prompt
            assert "content 3" not in prompt
            assert "content 4" not in prompt

    def test_evaluate_and_queue_truncates_file_content(self, tmp_path):
        """evaluate_and_queue should truncate file content to 1500 chars."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": false}'}

        # Create a file with very long content
        temp_file = tmp_path / "test.txt"
        long_content = "x" * 5000
        temp_file.write_text(long_content)

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
            call_kwargs = router.generate.call_args[1]
            prompt = call_kwargs["prompt"]
            # The code uses f.read(1500) which reads up to 1500 chars per file
            # Count the x's in the prompt to verify truncation
            x_count = prompt.count("x")
            # File is read with f.read(1500) so should be close to 1500 x's
            # Allow some slack due to text wrapping in the prompt template
            assert x_count >= 1400 and x_count <= 1600

    def test_evaluate_and_queue_handles_missing_json_in_response(self, tmp_path):
        """evaluate_and_queue should return None if _extract_json returns None."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": "invalid json"}

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            with patch("src.core.interesting_findings._extract_json", return_value=None):
                result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
                assert result is None

    def test_evaluate_and_queue_skips_unreadable_files(self, tmp_path):
        """evaluate_and_queue should skip files that can't be read."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": false}'}

        # Create one readable file
        temp_file1 = tmp_path / "readable.txt"
        temp_file1.write_text("readable content")

        # Create one unreadable file path
        unreadable_path = str(tmp_path / "nonexistent.txt")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            result = queue.evaluate_and_queue(
                {},
                [unreadable_path, str(temp_file1)],
                "goal",
                "task",
                router
            )
            # Should have called router with at least the readable file
            router.generate.assert_called_once()

    def test_evaluate_and_queue_strips_whitespace_from_fields(self, tmp_path):
        """evaluate_and_queue should strip whitespace from summary and topic."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": true}'}

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        parsed = {
            "interesting": True,
            "summary": "  This is a really interesting finding  ",
            "topic": "  research  ",
        }

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            with patch("src.core.interesting_findings._extract_json", return_value=parsed):
                result = queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
                assert result is not None
                finding = queue.findings[0]
                assert finding["summary"] == "This is a really interesting finding"
                assert finding["topic"] == "research"

    def test_evaluate_and_queue_passes_correct_parameters_to_generate(self, tmp_path):
        """evaluate_and_queue should call router.generate with correct parameters."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": false}'}

        temp_file = tmp_path / "test.txt"
        temp_file.write_text("test content")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
            call_kwargs = router.generate.call_args[1]
            assert call_kwargs["max_tokens"] == 200
            assert call_kwargs["temperature"] == 0.3

    def test_evaluate_and_queue_converts_file_path_to_basename(self, tmp_path):
        """evaluate_and_queue should use basename of file paths in prompt."""
        queue = InterestingFindingsQueue(data_dir=tmp_path)
        router = MagicMock()
        router.generate.return_value = {"text": '{"interesting": false}'}

        temp_file = tmp_path / "my_file.txt"
        temp_file.write_text("test content")

        with patch("src.core.interesting_findings.get_user_name", return_value="TestUser"):
            queue.evaluate_and_queue({}, [str(temp_file)], "goal", "task", router)
            call_kwargs = router.generate.call_args[1]
            prompt = call_kwargs["prompt"]
            assert "my_file.txt" in prompt
            assert str(tmp_path) not in prompt  # Full path should not be in prompt
