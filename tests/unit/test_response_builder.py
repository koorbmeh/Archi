"""Unit tests for src.interfaces.response_builder."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, call
import pytest

from src.interfaces import response_builder


class TestTrace:
    """Tests for the trace() function."""

    def test_trace_writes_to_file_with_timestamp(self, tmp_path):
        """Test that trace writes a line with timestamp to the trace file."""
        trace_file = tmp_path / "chat_trace.log"
        msg = "Test message"

        with patch.object(response_builder, "_trace_file", trace_file):
            with patch("src.interfaces.response_builder.datetime") as mock_dt:
                mock_dt.now.return_value.isoformat.return_value = "2026-02-24T10:30:00"
                response_builder.trace(msg)

        assert trace_file.exists()
        content = trace_file.read_text()
        assert "2026-02-24T10:30:00 Test message" in content

    def test_trace_appends_multiple_lines(self, tmp_path):
        """Test that trace appends multiple lines to the trace file."""
        trace_file = tmp_path / "chat_trace.log"

        with patch.object(response_builder, "_trace_file", trace_file):
            with patch("src.interfaces.response_builder.datetime") as mock_dt:
                mock_dt.now.return_value.isoformat.return_value = "2026-02-24T10:00:00"
                response_builder.trace("Message 1")
                mock_dt.now.return_value.isoformat.return_value = "2026-02-24T10:01:00"
                response_builder.trace("Message 2")

        content = trace_file.read_text()
        assert "Message 1" in content
        assert "Message 2" in content
        assert content.count("\n") == 2

    def test_trace_handles_file_write_error_silently(self, tmp_path):
        """Test that trace silently handles file write errors."""
        with patch.object(response_builder, "_trace_file") as mock_file:
            mock_file.open = MagicMock(side_effect=IOError("Permission denied"))
            # Should not raise an exception
            response_builder.trace("Test message")

    def test_trace_handles_encoding_errors_silently(self, tmp_path):
        """Test that trace silently handles encoding errors."""
        trace_file = tmp_path / "chat_trace.log"

        with patch.object(response_builder, "_trace_file", trace_file):
            with patch("builtins.open", side_effect=UnicodeEncodeError("utf-8", "", 0, 1, "invalid")):
                # Should not raise an exception
                response_builder.trace("Test message")


class TestLogConversation:
    """Tests for the log_conversation() function."""

    def test_log_conversation_writes_jsonl_record(self, tmp_path):
        """Test that log_conversation writes a JSONL record with all fields."""
        convo_file = tmp_path / "conversations.jsonl"

        with patch.object(response_builder, "_convo_file", convo_file):
            with patch("src.interfaces.response_builder.strip_thinking", return_value="response text"):
                with patch("src.interfaces.response_builder.datetime") as mock_dt:
                    mock_dt.now.return_value.isoformat.return_value = "2026-02-24T10:30:00"
                    response_builder.log_conversation(
                        source="web",
                        user_msg="What is AI?",
                        response="response text",
                        action="answer",
                        cost=0.05
                    )

        assert convo_file.exists()
        content = convo_file.read_text()
        record = json.loads(content.strip())

        assert record["ts"] == "2026-02-24T10:30:00"
        assert record["source"] == "web"
        assert record["user"] == "What is AI?"
        assert record["response"] == "response text"
        assert record["action"] == "answer"
        assert record["cost_usd"] == 0.05

    def test_log_conversation_skips_test_source(self, tmp_path):
        """Test that log_conversation skips logging for test sources."""
        convo_file = tmp_path / "conversations.jsonl"

        with patch.object(response_builder, "_convo_file", convo_file):
            response_builder.log_conversation(
                source="test",
                user_msg="Test message",
                response="Test response",
                action="test_action",
                cost=0.01
            )

        assert not convo_file.exists()

    def test_log_conversation_skips_test_runner_source(self, tmp_path):
        """Test that log_conversation skips test_runner source."""
        convo_file = tmp_path / "conversations.jsonl"

        with patch.object(response_builder, "_convo_file", convo_file):
            response_builder.log_conversation(
                source="test_runner",
                user_msg="Test message",
                response="Test response",
                action="test_action",
                cost=0.01
            )

        assert not convo_file.exists()

    def test_log_conversation_skips_test_harness_source(self, tmp_path):
        """Test that log_conversation skips test_harness source."""
        convo_file = tmp_path / "conversations.jsonl"

        with patch.object(response_builder, "_convo_file", convo_file):
            response_builder.log_conversation(
                source="test_harness",
                user_msg="Test message",
                response="Test response",
                action="test_action",
                cost=0.01
            )

        assert not convo_file.exists()

    def test_log_conversation_truncates_long_user_message(self, tmp_path):
        """Test that log_conversation truncates user messages to 2000 characters."""
        convo_file = tmp_path / "conversations.jsonl"
        long_msg = "x" * 3000

        with patch.object(response_builder, "_convo_file", convo_file):
            with patch("src.interfaces.response_builder.strip_thinking", return_value="response"):
                response_builder.log_conversation(
                    source="web",
                    user_msg=long_msg,
                    response="response",
                    action="answer",
                    cost=0.01
                )

        record = json.loads(convo_file.read_text().strip())
        assert len(record["user"]) == 2000
        assert record["user"] == "x" * 2000

    def test_log_conversation_truncates_long_response(self, tmp_path):
        """Test that log_conversation truncates responses to 2000 characters."""
        convo_file = tmp_path / "conversations.jsonl"
        long_response = "y" * 3000

        with patch.object(response_builder, "_convo_file", convo_file):
            with patch("src.interfaces.response_builder.strip_thinking", return_value=long_response):
                response_builder.log_conversation(
                    source="web",
                    user_msg="message",
                    response=long_response,
                    action="answer",
                    cost=0.01
                )

        record = json.loads(convo_file.read_text().strip())
        assert len(record["response"]) == 2000
        assert record["response"] == "y" * 2000

    def test_log_conversation_strips_thinking_from_response(self, tmp_path):
        """Test that log_conversation strips thinking blocks from response."""
        convo_file = tmp_path / "conversations.jsonl"
        raw_response = "response without thinking"

        with patch.object(response_builder, "_convo_file", convo_file):
            with patch("src.interfaces.response_builder.strip_thinking", return_value=raw_response) as mock_strip:
                response_builder.log_conversation(
                    source="web",
                    user_msg="message",
                    response="<think>internal</think>response without thinking",
                    action="answer",
                    cost=0.01
                )
                mock_strip.assert_called_once()

    def test_log_conversation_handles_write_error_silently(self, tmp_path):
        """Test that log_conversation handles write errors silently."""
        with patch.object(response_builder, "_convo_file") as mock_file:
            with patch("builtins.open", side_effect=IOError("Permission denied")):
                # Should not raise an exception
                response_builder.log_conversation(
                    source="web",
                    user_msg="message",
                    response="response",
                    action="answer",
                    cost=0.01
                )

    def test_log_conversation_handles_none_response(self, tmp_path):
        """Test that log_conversation handles None response gracefully."""
        convo_file = tmp_path / "conversations.jsonl"

        with patch.object(response_builder, "_convo_file", convo_file):
            with patch("src.interfaces.response_builder.strip_thinking", return_value=""):
                response_builder.log_conversation(
                    source="web",
                    user_msg="message",
                    response=None,
                    action="answer",
                    cost=0.01
                )

        record = json.loads(convo_file.read_text().strip())
        assert record["response"] == ""


class TestBuildResponse:
    """Tests for the build_response() function."""

    def test_build_response_strips_thinking(self):
        """Test that build_response strips thinking blocks."""
        with patch("src.interfaces.response_builder.strip_thinking", return_value="clean response"):
            with patch("src.interfaces.response_builder.sanitize_identity", return_value="clean response"):
                result = response_builder.build_response("<think>internal</think>clean response")
                assert result == "clean response"

    def test_build_response_sanitizes_identity(self):
        """Test that build_response sanitizes identity."""
        with patch("src.interfaces.response_builder.strip_thinking", return_value="text with grok"):
            with patch("src.interfaces.response_builder.sanitize_identity", return_value="text with Archi"):
                result = response_builder.build_response("text with grok")
                assert result == "text with Archi"

    def test_build_response_prepends_action_prefix_when_text_present(self):
        """Test that build_response prepends action_prefix when cleaned text is present."""
        with patch("src.interfaces.response_builder.strip_thinking", return_value="response text"):
            with patch("src.interfaces.response_builder.sanitize_identity") as mock_sanitize:
                mock_sanitize.side_effect = lambda x: x
                result = response_builder.build_response(
                    raw_text="response text",
                    action_prefix="Action:"
                )
                assert "Action:" in result
                assert "response text" in result
                assert result.startswith("Action:")

    def test_build_response_uses_prefix_only_when_no_text(self):
        """Test that build_response returns only prefix when raw_text is empty."""
        with patch("src.interfaces.response_builder.strip_thinking", return_value=""):
            with patch("src.interfaces.response_builder.sanitize_identity") as mock_sanitize:
                mock_sanitize.side_effect = lambda x: x
                result = response_builder.build_response(
                    raw_text="",
                    action_prefix="Action:"
                )
                assert result == "Action:"

    def test_build_response_handles_empty_raw_text(self):
        """Test that build_response returns fallback message when everything is empty."""
        with patch("src.interfaces.response_builder.strip_thinking", return_value=""):
            with patch("src.interfaces.response_builder.sanitize_identity", return_value=""):
                result = response_builder.build_response("")
                assert result == "I'm not sure how to respond."

    def test_build_response_appends_finding_when_short(self):
        """Test that build_response appends finding when response is short."""
        finding = {"summary": "Found something interesting"}

        with patch("src.interfaces.response_builder.strip_thinking", return_value="short"):
            with patch("src.interfaces.response_builder.sanitize_identity", return_value="short"):
                result = response_builder.build_response(
                    raw_text="short",
                    pending_finding=finding
                )
                assert "Found something interesting" in result
                assert "Also —" in result

    def test_build_response_skips_finding_when_response_at_limit(self):
        """Test that build_response skips finding when response is at 1500 char limit."""
        finding = {"summary": "Found something"}
        long_text = "x" * 1500

        with patch("src.interfaces.response_builder.strip_thinking", return_value=long_text):
            with patch("src.interfaces.response_builder.sanitize_identity", return_value=long_text):
                result = response_builder.build_response(
                    raw_text=long_text,
                    pending_finding=finding
                )
                assert "Found something" not in result

    def test_build_response_skips_finding_when_response_too_long(self):
        """Test that build_response skips finding when response exceeds 1500 chars."""
        finding = {"summary": "Found something"}
        long_text = "x" * 1501

        with patch("src.interfaces.response_builder.strip_thinking", return_value=long_text):
            with patch("src.interfaces.response_builder.sanitize_identity", return_value=long_text):
                result = response_builder.build_response(
                    raw_text=long_text,
                    pending_finding=finding
                )
                assert "Found something" not in result

    def test_build_response_skips_finding_when_cleaned_is_empty(self):
        """Test that build_response does not append finding when cleaned text is empty."""
        finding = {"summary": "Found something"}

        with patch("src.interfaces.response_builder.strip_thinking", return_value=""):
            with patch("src.interfaces.response_builder.sanitize_identity", return_value=""):
                result = response_builder.build_response(
                    raw_text="",
                    pending_finding=finding
                )
                assert "Found something" not in result
                assert result == "I'm not sure how to respond."

    def test_build_response_sanitizes_action_prefix(self):
        """Test that build_response sanitizes the action_prefix."""
        with patch("src.interfaces.response_builder.strip_thinking", return_value="text"):
            with patch("src.interfaces.response_builder.sanitize_identity") as mock_sanitize:
                mock_sanitize.side_effect = lambda x: x.replace("grok", "Archi")
                result = response_builder.build_response(
                    raw_text="text",
                    action_prefix="Made by grok"
                )
                assert "Made by Archi" in result


class TestGetPendingFinding:
    """Tests for the get_pending_finding() function."""

    def test_get_pending_finding_returns_finding_when_available(self):
        """Test that get_pending_finding returns a finding when available."""
        finding = {"id": "123", "summary": "Interesting finding"}

        with patch("src.core.interesting_findings.get_findings_queue") as mock_queue_func:
            mock_queue = MagicMock()
            mock_queue.get_ready_for_delivery.return_value = [finding]
            mock_queue_func.return_value = mock_queue

            result = response_builder.get_pending_finding()
            assert result == finding

    def test_get_pending_finding_returns_none_when_no_ready_findings(self):
        """Test that get_pending_finding returns None when no findings are ready."""
        with patch("src.core.interesting_findings.get_findings_queue") as mock_queue_func:
            mock_queue = MagicMock()
            mock_queue.get_ready_for_delivery.return_value = []
            mock_queue_func.return_value = mock_queue

            result = response_builder.get_pending_finding()
            assert result is None

    def test_get_pending_finding_returns_none_on_import_error(self):
        """Test that get_pending_finding returns None when import fails."""
        with patch("builtins.__import__", side_effect=ImportError("Module not found")):
            result = response_builder.get_pending_finding()
            assert result is None

    def test_get_pending_finding_returns_none_on_attribute_error(self):
        """Test that get_pending_finding returns None when attribute access fails."""
        with patch("src.core.interesting_findings.get_findings_queue", side_effect=AttributeError("No such attribute")):
            result = response_builder.get_pending_finding()
            assert result is None


class TestMarkFindingDelivered:
    """Tests for the mark_finding_delivered() function."""

    def test_mark_finding_delivered_calls_mark_delivered(self):
        """Test that mark_finding_delivered calls the queue's mark_delivered method."""
        with patch("src.core.interesting_findings.get_findings_queue") as mock_queue_func:
            mock_queue = MagicMock()
            mock_queue_func.return_value = mock_queue

            response_builder.mark_finding_delivered("123")

            mock_queue.mark_delivered.assert_called_once_with("123")

    def test_mark_finding_delivered_handles_import_error(self):
        """Test that mark_finding_delivered handles import errors gracefully."""
        with patch("builtins.__import__", side_effect=ImportError("Module not found")):
            # Should not raise an exception
            response_builder.mark_finding_delivered("123")

    def test_mark_finding_delivered_handles_attribute_error(self):
        """Test that mark_finding_delivered handles attribute errors gracefully."""
        with patch("src.core.interesting_findings.get_findings_queue", side_effect=AttributeError("No attribute")):
            # Should not raise an exception
            response_builder.mark_finding_delivered("123")


class TestExtractPreferences:
    """Tests for the extract_preferences() function."""

    def test_extract_preferences_calls_extract_and_record(self):
        """Test that extract_preferences calls extract_and_record with correct arguments."""
        with patch("src.core.user_preferences.extract_and_record") as mock_extract:
            router = MagicMock()
            response_builder.extract_preferences("user message", "web", router)

            mock_extract.assert_called_once_with("user message", "web", router)

    def test_extract_preferences_handles_import_error(self):
        """Test that extract_preferences handles import errors gracefully."""
        with patch("builtins.__import__", side_effect=ImportError("Module not found")):
            router = MagicMock()
            # Should not raise an exception
            response_builder.extract_preferences("user message", "web", router)

    def test_extract_preferences_handles_attribute_error(self):
        """Test that extract_preferences handles attribute errors gracefully."""
        with patch("src.core.user_preferences.extract_and_record", side_effect=AttributeError("No attribute")):
            router = MagicMock()
            # Should not raise an exception
            response_builder.extract_preferences("user message", "web", router)
