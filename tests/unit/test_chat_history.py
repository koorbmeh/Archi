"""Unit tests for src.interfaces.chat_history.

Covers: load, save, append, seconds_since_last_message, format_for_prompt,
pop_archivable, get_recent, and _ensure_file.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from src.interfaces import chat_history


# ─── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def tmp_chat_files(tmp_path):
    """Create temporary history file paths and patch module globals."""
    history_file = tmp_path / "chat_history.json"
    old_history_file = tmp_path / "web_chat_history.json"
    data_dir = tmp_path

    with patch.object(chat_history, "_HISTORY_FILE", history_file), \
         patch.object(chat_history, "_OLD_HISTORY_FILE", old_history_file), \
         patch.object(chat_history, "_DATA_DIR", data_dir):
        yield {
            "history_file": history_file,
            "old_history_file": old_history_file,
            "data_dir": data_dir,
        }


@pytest.fixture
def mock_strip_thinking():
    """Mock strip_thinking to simulate thinking block removal."""
    with patch("src.interfaces.chat_history.strip_thinking") as mock:
        # Default: pass through unchanged
        mock.side_effect = lambda x: x
        yield mock


# ─── TestEnsureFile ─────────────────────────────────────────────────────

class TestEnsureFile:
    """Tests for _ensure_file() function."""

    def test_creates_file_if_missing(self, tmp_chat_files):
        """_ensure_file() creates the history file if it doesn't exist."""
        history_file = tmp_chat_files["history_file"]
        assert not history_file.exists()

        result = chat_history._ensure_file()

        assert result == history_file
        assert history_file.exists()
        assert history_file.read_text() == "[]"

    def test_creates_parent_directory(self, tmp_path):
        """_ensure_file() creates parent directories as needed."""
        nested_dir = tmp_path / "a" / "b" / "c"
        history_file = nested_dir / "chat_history.json"

        with patch.object(chat_history, "_HISTORY_FILE", history_file), \
             patch.object(chat_history, "_OLD_HISTORY_FILE", nested_dir / "old.json"), \
             patch.object(chat_history, "_DATA_DIR", nested_dir):

            result = chat_history._ensure_file()

            assert nested_dir.exists()
            assert result == history_file
            assert history_file.exists()

    def test_migrates_old_file(self, tmp_chat_files):
        """_ensure_file() renames old_history_file to _HISTORY_FILE if new doesn't exist."""
        old_file = tmp_chat_files["old_history_file"]
        history_file = tmp_chat_files["history_file"]

        # Create old file with data
        old_file.write_text("[{\"role\": \"user\", \"content\": \"hello\"}]")
        assert old_file.exists()
        assert not history_file.exists()

        result = chat_history._ensure_file()

        assert result == history_file
        assert history_file.exists()
        assert not old_file.exists()
        assert "[{" in history_file.read_text()

    def test_skips_migration_if_new_exists(self, tmp_chat_files):
        """_ensure_file() doesn't migrate if history_file already exists."""
        old_file = tmp_chat_files["old_history_file"]
        history_file = tmp_chat_files["history_file"]

        # Create both files
        old_content = "[{\"role\": \"user\", \"content\": \"old\"}]"
        new_content = "[{\"role\": \"user\", \"content\": \"new\"}]"
        old_file.write_text(old_content)
        history_file.write_text(new_content)

        chat_history._ensure_file()

        # Old file should remain unchanged
        assert old_file.exists()
        assert history_file.read_text() == new_content

    def test_handles_migration_error(self, tmp_chat_files):
        """_ensure_file() logs warning if migration fails and creates empty file."""
        old_file = tmp_chat_files["old_history_file"]
        history_file = tmp_chat_files["history_file"]

        old_file.write_text("old data")

        # Simulate rename failure by making history file unmoveable
        # (rename will fail if target location can't be written)
        with patch.object(chat_history, "_OLD_HISTORY_FILE", old_file), \
             patch.object(Path, "rename", side_effect=OSError("Permission denied")):
            with patch("src.interfaces.chat_history.logger") as mock_logger:
                result = chat_history._ensure_file()

                assert result == history_file
                assert history_file.exists()
                mock_logger.warning.assert_called_once()

    def test_returns_history_file_path(self, tmp_chat_files):
        """_ensure_file() returns the _HISTORY_FILE path."""
        result = chat_history._ensure_file()
        assert result == tmp_chat_files["history_file"]


# ─── TestLoad ───────────────────────────────────────────────────────────

class TestLoad:
    """Tests for load() function."""

    def test_loads_valid_json_list(self, tmp_chat_files):
        """load() returns list from valid JSON file."""
        history_file = tmp_chat_files["history_file"]
        test_data = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        history_file.write_text(json.dumps(test_data))

        result = chat_history.load()

        assert result == test_data
        assert len(result) == 2

    def test_returns_empty_list_for_non_list_json(self, tmp_chat_files):
        """load() returns [] if JSON is not a list."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text('{"key": "value"}')

        result = chat_history.load()

        assert result == []

    def test_returns_empty_list_for_invalid_json(self, tmp_chat_files):
        """load() returns [] if JSON is invalid."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text("not valid json at all")

        result = chat_history.load()

        assert result == []

    def test_returns_empty_list_if_file_missing(self, tmp_chat_files):
        """load() returns [] if file doesn't exist (creates empty one)."""
        history_file = tmp_chat_files["history_file"]
        assert not history_file.exists()

        result = chat_history.load()

        assert result == []
        assert history_file.exists()  # Created by _ensure_file

    def test_handles_permission_error(self, tmp_chat_files):
        """load() returns [] if file can't be read due to permissions."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text("[]")
        history_file.chmod(0o000)

        try:
            result = chat_history.load()
            assert result == []
        finally:
            history_file.chmod(0o644)

    def test_loads_empty_array(self, tmp_chat_files):
        """load() returns [] from empty JSON array."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text("[]")

        result = chat_history.load()

        assert result == []

    def test_preserves_all_fields(self, tmp_chat_files):
        """load() preserves all fields in message objects."""
        history_file = tmp_chat_files["history_file"]
        test_data = [
            {
                "role": "user",
                "content": "hello",
                "ts": 1234567890.5,
                "extra_field": "preserved",
            }
        ]
        history_file.write_text(json.dumps(test_data))

        result = chat_history.load()

        assert result[0] == test_data[0]
        assert result[0].get("extra_field") == "preserved"


# ─── TestSave ───────────────────────────────────────────────────────────

class TestSave:
    """Tests for save() function."""

    def test_saves_messages_to_file(self, tmp_chat_files):
        """save() writes messages to the history file as JSON."""
        history_file = tmp_chat_files["history_file"]
        test_data = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]

        chat_history.save(test_data)

        assert history_file.exists()
        loaded = json.loads(history_file.read_text())
        assert loaded == test_data

    def test_truncates_to_max_messages(self, tmp_chat_files):
        """save() keeps only the last _MAX_MESSAGES messages."""
        history_file = tmp_chat_files["history_file"]

        # Create more messages than _MAX_MESSAGES
        test_data = [
            {"role": "user", "content": f"msg {i}", "ts": i}
            for i in range(chat_history._MAX_MESSAGES + 10)
        ]

        chat_history.save(test_data)

        loaded = json.loads(history_file.read_text())
        assert len(loaded) == chat_history._MAX_MESSAGES
        # Check that the oldest messages were dropped
        assert loaded[0]["content"] == f"msg {10}"

    def test_saves_empty_list(self, tmp_chat_files):
        """save() can save an empty message list."""
        history_file = tmp_chat_files["history_file"]

        chat_history.save([])

        assert history_file.exists()
        loaded = json.loads(history_file.read_text())
        assert loaded == []

    def test_uses_compact_json_format(self, tmp_chat_files):
        """save() uses indent=0 for compact JSON format."""
        history_file = tmp_chat_files["history_file"]
        test_data = [{"role": "user", "content": "test"}]

        chat_history.save(test_data)

        content = history_file.read_text()
        # indent=0 means compact JSON
        assert "role" in content
        assert "user" in content
        # ensure_ascii=False is used to preserve non-ASCII characters

    def test_preserves_unicode(self, tmp_chat_files):
        """save() preserves non-ASCII characters (ensure_ascii=False)."""
        history_file = tmp_chat_files["history_file"]
        test_data = [{"role": "user", "content": "こんにちは"}]

        chat_history.save(test_data)

        loaded = json.loads(history_file.read_text(encoding="utf-8"))
        assert loaded[0]["content"] == "こんにちは"

    def test_handles_write_error(self, tmp_chat_files, tmp_path):
        """save() logs warning if write fails."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text("[]")

        # Simulate write failure via mkstemp error
        with patch("tempfile.mkstemp", side_effect=OSError("disk full")):
            with patch("src.interfaces.chat_history.logger") as mock_logger:
                chat_history.save([{"role": "user", "content": "test"}])
                mock_logger.warning.assert_called_once()
                assert "Could not save" in mock_logger.warning.call_args[0][0]

    def test_creates_file_if_missing(self, tmp_chat_files):
        """save() creates the file if it doesn't exist."""
        history_file = tmp_chat_files["history_file"]
        assert not history_file.exists()

        chat_history.save([{"role": "user", "content": "hello"}])

        assert history_file.exists()


# ─── TestAppend ─────────────────────────────────────────────────────────

class TestAppend:
    """Tests for append() function."""

    def test_appends_user_message(self, tmp_chat_files, mock_strip_thinking):
        """append() adds a user message with timestamp."""
        history_file = tmp_chat_files["history_file"]
        before = time.time()

        chat_history.append("user", "Hello")

        after = time.time()
        loaded = json.loads(history_file.read_text())
        assert len(loaded) == 1
        assert loaded[0]["role"] == "user"
        assert loaded[0]["content"] == "Hello"
        assert before <= loaded[0]["ts"] <= after

    def test_appends_assistant_message(self, tmp_chat_files, mock_strip_thinking):
        """append() adds an assistant message and strips thinking."""
        history_file = tmp_chat_files["history_file"]

        chat_history.append("assistant", "Response text")

        # strip_thinking was called on assistant content
        mock_strip_thinking.assert_called_once_with("Response text")

        loaded = json.loads(history_file.read_text())
        assert len(loaded) == 1
        assert loaded[0]["role"] == "assistant"

    def test_strips_thinking_from_assistant(self, tmp_chat_files):
        """append() calls strip_thinking on assistant messages."""
        history_file = tmp_chat_files["history_file"]

        with patch("src.interfaces.chat_history.strip_thinking") as mock_strip:
            mock_strip.return_value = "cleaned response"
            chat_history.append("assistant", "<think>reasoning</think>Answer")

            mock_strip.assert_called_once_with("<think>reasoning</think>Answer")

        loaded = json.loads(history_file.read_text())
        assert loaded[0]["content"] == "cleaned response"

    def test_skips_empty_assistant_message(self, tmp_chat_files):
        """append() doesn't save assistant message if it becomes empty after stripping."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text("[]")  # Ensure file exists

        with patch("src.interfaces.chat_history.strip_thinking") as mock_strip:
            mock_strip.return_value = ""  # Empty after stripping
            chat_history.append("assistant", "<think>only thinking</think>")

            loaded = json.loads(history_file.read_text())
            assert loaded == []

    def test_skips_whitespace_only_assistant_message(self, tmp_chat_files):
        """append() skips assistant message if only whitespace remains after stripping."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text("[]")  # Ensure file exists

        with patch("src.interfaces.chat_history.strip_thinking") as mock_strip:
            mock_strip.return_value = "   \n\t  "  # Only whitespace
            with patch("src.interfaces.chat_history.logger") as mock_logger:
                chat_history.append("assistant", "<think>text</think>")

                mock_logger.debug.assert_called_once()
                assert "empty assistant message" in mock_logger.debug.call_args[0][0].lower()

            loaded = json.loads(history_file.read_text())
            assert loaded == []

    def test_appends_multiple_messages_in_order(self, tmp_chat_files, mock_strip_thinking):
        """append() preserves order when adding multiple messages."""
        history_file = tmp_chat_files["history_file"]

        chat_history.append("user", "First")
        chat_history.append("assistant", "Second")
        chat_history.append("user", "Third")

        loaded = json.loads(history_file.read_text())
        assert len(loaded) == 3
        assert loaded[0]["content"] == "First"
        assert loaded[1]["content"] == "Second"
        assert loaded[2]["content"] == "Third"

    def test_does_not_strip_user_messages(self, tmp_chat_files):
        """append() doesn't strip thinking from user messages."""
        history_file = tmp_chat_files["history_file"]

        with patch("src.interfaces.chat_history.strip_thinking") as mock_strip:
            chat_history.append("user", "<think>my thinking</think>")

            # strip_thinking should NOT be called for user
            mock_strip.assert_not_called()

        loaded = json.loads(history_file.read_text())
        assert loaded[0]["content"] == "<think>my thinking</think>"

    def test_timestamp_increases_for_sequential_appends(self, tmp_chat_files, mock_strip_thinking):
        """append() assigns increasing timestamps for sequential messages."""
        history_file = tmp_chat_files["history_file"]

        chat_history.append("user", "First")
        time.sleep(0.01)
        chat_history.append("user", "Second")

        loaded = json.loads(history_file.read_text())
        assert loaded[1]["ts"] > loaded[0]["ts"]


# ─── TestSecondsSinceLastMessage ────────────────────────────────────────

class TestSecondsSinceLastMessage:
    """Tests for seconds_since_last_message() function."""

    def test_returns_none_for_empty_history(self, tmp_chat_files, mock_strip_thinking):
        """seconds_since_last_message() returns None if no messages."""
        result = chat_history.seconds_since_last_message()
        assert result is None

    def test_returns_seconds_since_last_message(self, tmp_chat_files, mock_strip_thinking):
        """seconds_since_last_message() returns time delta since last message."""
        # Create a message in the past
        history_file = tmp_chat_files["history_file"]
        past_time = time.time() - 10.0
        history_file.write_text(
            json.dumps([{"role": "user", "content": "hello", "ts": past_time}])
        )

        result = chat_history.seconds_since_last_message()

        assert result is not None
        assert 9.9 < result < 10.1  # Allow small time variance

    def test_skips_messages_without_timestamp(self, tmp_chat_files, mock_strip_thinking):
        """seconds_since_last_message() skips messages without 'ts' field."""
        history_file = tmp_chat_files["history_file"]
        past_time = time.time() - 5.0
        test_data = [
            {"role": "user", "content": "first"},  # No ts
            {"role": "user", "content": "second", "ts": past_time},  # Has ts
            {"role": "user", "content": "third"},  # No ts
        ]
        history_file.write_text(json.dumps(test_data))

        result = chat_history.seconds_since_last_message()

        # Should return time since second message (last with ts)
        assert result is not None
        assert 4.9 < result < 5.1

    def test_returns_none_if_no_message_has_timestamp(self, tmp_chat_files, mock_strip_thinking):
        """seconds_since_last_message() returns None if all messages lack timestamps."""
        history_file = tmp_chat_files["history_file"]
        test_data = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
        history_file.write_text(json.dumps(test_data))

        result = chat_history.seconds_since_last_message()

        assert result is None

    def test_uses_current_time(self, tmp_chat_files, mock_strip_thinking):
        """seconds_since_last_message() uses current time for calculation."""
        history_file = tmp_chat_files["history_file"]
        msg_time = time.time()
        history_file.write_text(
            json.dumps([{"role": "user", "content": "msg", "ts": msg_time}])
        )

        before = time.time()
        result = chat_history.seconds_since_last_message()
        after = time.time()

        expected_min = before - msg_time
        expected_max = after - msg_time
        assert expected_min <= result <= expected_max


# ─── TestFormatForPrompt ────────────────────────────────────────────────

class TestFormatForPrompt:
    """Tests for format_for_prompt() function."""

    def test_returns_empty_string_for_empty_list(self, mock_strip_thinking):
        """format_for_prompt() returns empty string if no messages."""
        result = chat_history.format_for_prompt([])
        assert result == ""

    def test_returns_empty_string_for_none_list(self, mock_strip_thinking):
        """format_for_prompt() treats None as empty."""
        result = chat_history.format_for_prompt(None)
        assert result == ""

    def test_formats_user_message(self, mock_strip_thinking):
        """format_for_prompt() prefixes user messages with 'User:'."""
        messages = [{"role": "user", "content": "hello"}]
        result = chat_history.format_for_prompt(messages)
        assert "User: hello" in result
        assert "Previous conversation:" in result

    def test_formats_assistant_message(self, mock_strip_thinking):
        """format_for_prompt() prefixes assistant messages with 'Archi:'."""
        messages = [{"role": "assistant", "content": "world"}]
        result = chat_history.format_for_prompt(messages)
        assert "Archi: world" in result
        assert "Previous conversation:" in result

    def test_limits_to_recent_exchanges(self, mock_strip_thinking):
        """format_for_prompt() includes only recent exchanges (max_exchanges * 2 messages)."""
        messages = [
            {"role": "user", "content": f"old_msg_{i:02d}"}
            for i in range(20)
        ]

        result = chat_history.format_for_prompt(messages, max_exchanges=3)

        # Should only include last 6 messages (3 exchanges * 2)
        for i in range(14):
            assert f"old_msg_{i:02d}" not in result
        for i in range(14, 20):
            assert f"old_msg_{i:02d}" in result

    def test_default_max_exchanges_is_five(self, mock_strip_thinking):
        """format_for_prompt() uses max_exchanges=5 by default."""
        messages = [
            {"role": "user", "content": f"old_msg_{i:02d}"}
            for i in range(20)
        ]

        result = chat_history.format_for_prompt(messages)

        # Should include last 10 messages (5 exchanges * 2)
        for i in range(10):
            assert f"old_msg_{i:02d}" not in result
        for i in range(10, 20):
            assert f"old_msg_{i:02d}" in result

    def test_strips_thinking_from_assistant_content(self, mock_strip_thinking):
        """format_for_prompt() strips <think> blocks from assistant messages."""
        messages = [{"role": "assistant", "content": "response"}]

        chat_history.format_for_prompt(messages)

        # strip_thinking should be called on assistant content
        mock_strip_thinking.assert_called_with("response")

    def test_skips_empty_content(self, mock_strip_thinking):
        """format_for_prompt() skips messages with empty content after stripping."""
        # Configure mock to return empty for assistant content, pass through for user
        mock_strip_thinking.side_effect = lambda x: "" if "think" in x else x

        messages = [
            {"role": "user", "content": "user msg"},
            {"role": "assistant", "content": "<think>only</think>"},  # Becomes empty
        ]

        result = chat_history.format_for_prompt(messages)

        assert "user msg" in result
        # Empty assistant message should not appear
        assert "Archi:" not in result or "Archi: \n" not in result

    def test_strips_whitespace_from_content(self, mock_strip_thinking):
        """format_for_prompt() strips whitespace from message content."""
        messages = [{"role": "user", "content": "  hello  \n"}]
        result = chat_history.format_for_prompt(messages)
        assert "User: hello" in result
        assert "  hello  " not in result

    def test_handles_messages_without_role_field(self, mock_strip_thinking):
        """format_for_prompt() defaults to 'user' if role is missing."""
        messages = [{"content": "hello"}]
        result = chat_history.format_for_prompt(messages)
        assert "User: hello" in result

    def test_handles_messages_without_content_field(self, mock_strip_thinking):
        """format_for_prompt() treats missing content as empty string."""
        messages = [{"role": "user"}]
        result = chat_history.format_for_prompt(messages)
        assert result == ""

    def test_returns_empty_string_if_all_content_empty(self, mock_strip_thinking):
        """format_for_prompt() returns empty string if all messages are empty after cleaning."""
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": None},
        ]
        result = chat_history.format_for_prompt(messages)
        assert result == ""

    def test_conversation_header_and_newlines(self, mock_strip_thinking):
        """format_for_prompt() formats output with header and proper newlines."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        result = chat_history.format_for_prompt(messages)
        assert result.startswith("Previous conversation:\n")
        assert result.endswith("\n\n")
        assert "User: hello\n" in result
        assert "Archi: world" in result


# ─── TestPopArchivable ──────────────────────────────────────────────────

class TestPopArchivable:
    """Tests for pop_archivable() function."""

    def test_returns_empty_when_below_keep_threshold(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable() returns [] if messages <= keep."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": f"msg {i}"}
            for i in range(5)
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.pop_archivable(keep=8)

        assert result == []
        # Original messages should still be in file
        loaded = json.loads(history_file.read_text())
        assert len(loaded) == 5

    def test_archives_oldest_messages(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable() returns oldest messages when above keep threshold."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": f"msg {i}"}
            for i in range(15)
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.pop_archivable(keep=8)

        assert len(result) == 7
        assert result[0]["content"] == "msg 0"
        assert result[-1]["content"] == "msg 6"

    def test_saves_remaining_messages(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable() keeps the most recent 'keep' messages in file."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": f"msg {i}"}
            for i in range(15)
        ]
        history_file.write_text(json.dumps(messages))

        chat_history.pop_archivable(keep=8)

        loaded = json.loads(history_file.read_text())
        assert len(loaded) == 8
        assert loaded[0]["content"] == "msg 7"
        assert loaded[-1]["content"] == "msg 14"

    def test_default_keep_is_eight(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable() uses keep=8 by default."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": f"msg {i}"}
            for i in range(20)
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.pop_archivable()

        # Should keep 8, archive 12
        assert len(result) == 12
        loaded = json.loads(history_file.read_text())
        assert len(loaded) == 8

    def test_logs_archive_info(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable() logs number of archived and kept messages."""
        history_file = tmp_chat_files["history_file"]
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(15)]
        history_file.write_text(json.dumps(messages))

        with patch("src.interfaces.chat_history.logger") as mock_logger:
            chat_history.pop_archivable(keep=8)
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0]
            assert "7" in str(call_args)  # 7 archived
            assert "8" in str(call_args)  # 8 kept

    def test_handles_empty_history(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable() handles empty history gracefully."""
        history_file = tmp_chat_files["history_file"]
        history_file.write_text("[]")

        result = chat_history.pop_archivable(keep=8)

        assert result == []
        loaded = json.loads(history_file.read_text())
        assert loaded == []

    def test_preserves_all_fields_in_archivable(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable() preserves all fields in returned messages."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {
                "role": "user",
                "content": f"msg {i}",
                "ts": 1000 + i,
                "extra": f"field {i}",
            }
            for i in range(15)
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.pop_archivable(keep=8)

        assert result[0]["ts"] == 1000
        assert result[0]["extra"] == "field 0"


# ─── TestGetRecent ──────────────────────────────────────────────────────

class TestGetRecent:
    """Tests for get_recent() function."""

    def test_returns_empty_list_for_empty_history(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() returns [] if no messages."""
        result = chat_history.get_recent()
        assert result == []

    def test_returns_all_messages(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() returns all messages from history."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": "msg 1"},
            {"role": "assistant", "content": "msg 2"},
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.get_recent()

        assert len(result) == 2
        assert result[0]["content"] == "msg 1"
        assert result[1]["content"] == "msg 2"

    def test_strips_thinking_from_assistant_content(self, tmp_chat_files):
        """get_recent() strips <think> blocks from assistant messages."""
        history_file = tmp_chat_files["history_file"]
        messages = [{"role": "assistant", "content": "response"}]
        history_file.write_text(json.dumps(messages))

        with patch("src.interfaces.chat_history.strip_thinking") as mock_strip:
            mock_strip.return_value = "cleaned"
            chat_history.get_recent()
            mock_strip.assert_called_once_with("response")

    def test_skips_messages_with_empty_content(self, tmp_chat_files):
        """get_recent() filters out messages with empty content after stripping."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "empty"},
            {"role": "user", "content": "world"},
        ]
        history_file.write_text(json.dumps(messages))

        with patch("src.interfaces.chat_history.strip_thinking") as mock_strip:
            mock_strip.return_value = ""  # Empty after stripping
            result = chat_history.get_recent()

            assert len(result) == 2
            assert result[0]["content"] == "hello"
            assert result[1]["content"] == "world"

    def test_removes_timestamp_field_from_returned_messages(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() returns cleaned messages without ts field."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": "hello", "ts": 12345},
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.get_recent()

        assert len(result) == 1
        assert "ts" not in result[0]
        assert result[0] == {"role": "user", "content": "hello"}

    def test_handles_messages_without_role(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() defaults to 'user' role if missing."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"content": "hello"},  # No role
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.get_recent()

        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_strips_whitespace_from_content(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() strips whitespace from content."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": "  hello  \n\t"},
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.get_recent()

        assert result[0]["content"] == "hello"

    def test_handles_none_content(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() treats None content as empty string."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": None},
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.get_recent()

        assert len(result) == 1
        assert result[0]["content"] == "hello"

    def test_preserves_order_of_messages(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() maintains the order of messages from history."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": f"msg {i}"}
            for i in range(5)
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.get_recent()

        for i, msg in enumerate(result):
            assert msg["content"] == f"msg {i}"

    def test_returns_only_role_and_content_fields(self, tmp_chat_files, mock_strip_thinking):
        """get_recent() returns cleaned messages with only role and content."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {
                "role": "user",
                "content": "hello",
                "ts": 12345,
                "extra_field": "value",
            },
        ]
        history_file.write_text(json.dumps(messages))

        result = chat_history.get_recent()

        assert len(result[0]) == 2  # Only role and content
        assert set(result[0].keys()) == {"role", "content"}


# ─── Integration Tests ──────────────────────────────────────────────────

class TestIntegration:
    """Integration tests across multiple functions."""

    def test_append_and_load_roundtrip(self, tmp_chat_files, mock_strip_thinking):
        """Messages appended are correctly loaded."""
        chat_history.append("user", "Hello")
        chat_history.append("assistant", "World")

        messages = chat_history.load()

        assert len(messages) == 2
        assert messages[0]["content"] == "Hello"
        assert messages[1]["content"] == "World"

    def test_save_load_consistency(self, tmp_chat_files, mock_strip_thinking):
        """save() and load() are consistent."""
        original = [
            {"role": "user", "content": "test 1"},
            {"role": "assistant", "content": "test 2"},
        ]

        chat_history.save(original)
        loaded = chat_history.load()

        assert loaded == original

    def test_format_uses_get_recent_content(self, tmp_chat_files, mock_strip_thinking):
        """format_for_prompt works with actual message data."""
        history_file = tmp_chat_files["history_file"]
        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a language"},
        ]
        history_file.write_text(json.dumps(messages))

        formatted = chat_history.format_for_prompt(messages)

        assert "User: What is Python?" in formatted
        assert "Archi: Python is a language" in formatted
        assert "Previous conversation:" in formatted

    def test_pop_archivable_with_subsequent_append(self, tmp_chat_files, mock_strip_thinking):
        """pop_archivable reduces history; append adds to reduced history."""
        history_file = tmp_chat_files["history_file"]
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(15)]
        history_file.write_text(json.dumps(messages))

        archived = chat_history.pop_archivable(keep=8)
        assert len(archived) == 7

        # Append a new message
        chat_history.append("user", "new message")

        loaded = chat_history.load()
        assert len(loaded) == 9  # 8 remaining + 1 new
        assert loaded[-1]["content"] == "new message"

    def test_max_messages_enforced_on_repeated_appends(self, tmp_chat_files, mock_strip_thinking):
        """Repeated appends respect _MAX_MESSAGES limit."""
        for i in range(chat_history._MAX_MESSAGES + 10):
            chat_history.append("user", f"message {i}")

        loaded = chat_history.load()
        assert len(loaded) == chat_history._MAX_MESSAGES


# ─── Thread Safety Tests ──────────────────────────────────────────────

class TestThreadSafety:
    """Tests for thread-safe atomic writes and locking."""

    def test_save_atomic_write(self, tmp_chat_files):
        """save() uses atomic write (temp file → rename)."""
        import os
        history_file = tmp_chat_files["history_file"]
        data = [{"role": "user", "content": "atomic test"}]

        chat_history.save(data)

        loaded = json.loads(history_file.read_text())
        assert loaded == data
        # No temp files should remain
        parent = history_file.parent
        temps = [f for f in os.listdir(parent) if f.startswith("chat_hist_") and f.endswith(".tmp")]
        assert temps == []

    def test_concurrent_appends_no_data_loss(self, tmp_chat_files, mock_strip_thinking):
        """Multiple threads appending concurrently should not lose messages."""
        import threading

        n_threads = 5
        n_messages_per_thread = 10

        def appender(thread_id):
            for i in range(n_messages_per_thread):
                chat_history.append("user", f"thread_{thread_id}_msg_{i}")

        threads = [
            threading.Thread(target=appender, args=(t,))
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        loaded = chat_history.load()
        # _MAX_MESSAGES caps at 20, so we get at most 20
        total_expected = n_threads * n_messages_per_thread
        expected_count = min(total_expected, chat_history._MAX_MESSAGES)
        assert len(loaded) == expected_count
        # File should be valid JSON
        raw = tmp_chat_files["history_file"].read_text()
        json.loads(raw)  # Should not raise

    def test_save_cleans_temp_on_error(self, tmp_chat_files):
        """save() cleans up temp file if rename fails."""
        import os
        history_file = tmp_chat_files["history_file"]
        # Pre-create the file so _ensure_file works
        history_file.write_text("[]")

        # save should handle errors gracefully
        with patch("os.replace", side_effect=OSError("mock rename fail")):
            chat_history.save([{"role": "user", "content": "test"}])

        # No orphaned temp files
        parent = history_file.parent
        temps = [f for f in os.listdir(parent) if f.startswith("chat_hist_") and f.endswith(".tmp")]
        assert temps == []

    def test_module_has_lock(self):
        """Module exposes a threading lock for concurrent access."""
        import threading
        assert isinstance(chat_history._lock, type(threading.Lock()))
