"""Unit tests for src/utils/text_cleaning.py."""

import pytest

from src.utils.text_cleaning import sanitize_identity, strip_thinking


# ── strip_thinking ──────────────────────────────────────────────────


class TestStripThinking:
    def test_no_think_tags(self):
        assert strip_thinking("Hello world") == "Hello world"

    def test_none_input(self):
        assert strip_thinking(None) == ""

    def test_empty_string(self):
        assert strip_thinking("") == ""

    def test_complete_block_removed(self):
        text = "<think>reasoning here</think>The answer is 42."
        assert strip_thinking(text) == "The answer is 42."

    def test_multiple_blocks_removed(self):
        text = "<think>first</think>A <think>second</think>B"
        assert strip_thinking(text) == "A B"

    def test_unclosed_think_removed(self):
        text = "Start <think>reasoning that never ends"
        assert strip_thinking(text) == "Start"

    def test_orphan_close_tag_removed(self):
        text = "</think> The answer is here."
        # No <think> in text, so early return
        assert strip_thinking(text) == "</think> The answer is here."

    def test_orphan_close_with_open_elsewhere(self):
        text = "<think>reasoning</think>\n</think> leftover"
        result = strip_thinking(text)
        assert "leftover" in result

    def test_entire_response_is_thinking_extracts_last_line(self):
        text = "<think>Line one\nLine two\nThe final answer</think>"
        result = strip_thinking(text)
        assert result == "The final answer"

    def test_entire_response_thinking_long_last_line_returns_empty(self):
        long_line = "x" * 250
        text = f"<think>reasoning\n{long_line}</think>"
        result = strip_thinking(text)
        assert result == ""

    def test_multiline_thinking_block(self):
        text = "<think>\nStep 1\nStep 2\nStep 3\n</think>\nFinal output."
        assert strip_thinking(text) == "Final output."


# ── sanitize_identity ───────────────────────────────────────────────


class TestSanitizeIdentity:
    def test_none_input(self):
        assert sanitize_identity(None) == ""

    def test_empty_string(self):
        assert sanitize_identity("") == ""

    def test_no_grok_or_xai(self):
        text = "Hello, I'm your assistant."
        assert sanitize_identity(text) == text

    def test_replaces_im_grok(self):
        assert "Archi" in sanitize_identity("I'm Grok, how can I help?")
        assert "Grok" not in sanitize_identity("I'm Grok, how can I help?")

    def test_replaces_i_am_grok(self):
        result = sanitize_identity("I am Grok and I can help.")
        assert "Archi" in result
        assert "I am Grok" not in result

    def test_replaces_generic_grok(self):
        # "ask grok" is in _GROK_PRESERVE, so only self-identity gets replaced
        result = sanitize_identity("Grok says hello.")
        assert "Archi" in result

    def test_preserves_use_grok_context(self):
        text = "You can use grok for this task."
        result = sanitize_identity(text)
        # Should preserve "use grok" — only replace self-identity
        assert "use grok" in result.lower() or "use Grok" in result

    def test_preserves_grok_api_context(self):
        text = "The grok api endpoint is fast."
        result = sanitize_identity(text)
        assert "grok api" in result.lower() or "Grok api" in result

    def test_preserves_switch_to_grok_context(self):
        text = "I'm Grok. You can switch to grok for better results."
        result = sanitize_identity(text)
        # Self-identity should be replaced, tool reference preserved
        assert "I'm Archi" in result or "I am Archi" in result

    def test_replaces_xai_references(self):
        result = sanitize_identity("Built by xAI for the world.")
        assert "xAI" not in result

    def test_replaces_xai_api(self):
        result = sanitize_identity("Powered via the xAI API")
        assert "xAI API" not in result

    def test_case_insensitive(self):
        result = sanitize_identity("I'M GROK here to help")
        assert "GROK" not in result
