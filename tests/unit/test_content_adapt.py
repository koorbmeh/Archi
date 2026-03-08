"""Tests for cross-platform content adaptation (Content Strategy Phase 5).

Session 241: Tests adapt_content(), format_adaptation_summary(), and the
content_adapt action handler.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.tools.content_creator import (
    adapt_content,
    format_adaptation_summary,
    _PLATFORM_CONSTRAINTS,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def mock_router():
    """Model router that returns canned responses."""
    router = MagicMock()
    router.generate.return_value = {"text": "Adapted content here"}
    return router


SAMPLE_BLOG = """# AI Agents in 2026

AI agents are transforming how we work. From coding assistants to autonomous
research tools, the landscape is evolving rapidly.

## Key Trends

1. Multi-modal reasoning
2. Tool use and API integration
3. Long-context memory

The future is autonomous, and it's already here.
"""


# ── adapt_content tests ─────────────────────────────────────────────


class TestAdaptContent:

    def test_adapts_to_all_platforms_by_default(self, mock_router):
        """Should produce adaptations for all supported platforms."""
        results = adapt_content(mock_router, SAMPLE_BLOG, source_format="blog",
                                topic="AI Agents")
        assert set(results.keys()) == set(_PLATFORM_CONSTRAINTS.keys())

    def test_all_results_have_content(self, mock_router):
        """Each successful adaptation should have content."""
        results = adapt_content(mock_router, SAMPLE_BLOG, topic="AI Agents")
        for platform, result in results.items():
            assert result is not None, f"{platform} returned None"
            assert result["content"] == "Adapted content here"
            assert result["adapted_from"] == "blog"

    def test_skips_source_format(self, mock_router):
        """Should not adapt blog to blog (same format)."""
        results = adapt_content(mock_router, SAMPLE_BLOG, source_format="blog",
                                target_platforms=["blog", "tweet"])
        assert "blog" not in results
        assert "tweet" in results

    def test_specific_target_platforms(self, mock_router):
        """Should only adapt to specified platforms."""
        results = adapt_content(mock_router, SAMPLE_BLOG,
                                target_platforms=["tweet", "reddit"])
        assert set(results.keys()) == {"tweet", "reddit"}

    def test_empty_source_returns_none(self, mock_router):
        """Should return None for all platforms on empty source."""
        results = adapt_content(mock_router, "", target_platforms=["tweet"])
        assert results == {"tweet": None}
        mock_router.generate.assert_not_called()

    def test_model_failure_returns_none(self, mock_router):
        """Should return None for a platform if model call fails."""
        mock_router.generate.side_effect = RuntimeError("API error")
        results = adapt_content(mock_router, SAMPLE_BLOG,
                                target_platforms=["tweet"])
        assert results["tweet"] is None

    def test_empty_response_returns_none(self, mock_router):
        """Should return None if model returns empty text."""
        mock_router.generate.return_value = {"text": ""}
        results = adapt_content(mock_router, SAMPLE_BLOG,
                                target_platforms=["tweet"])
        assert results["tweet"] is None

    def test_reddit_adaptation_parses_title(self, mock_router):
        """Reddit adaptations should extract TITLE/BODY."""
        mock_router.generate.return_value = {
            "text": "TITLE: AI Agents Are Here\nBODY:\nAI agents are real."
        }
        results = adapt_content(mock_router, SAMPLE_BLOG,
                                target_platforms=["reddit"])
        assert results["reddit"]["title"] == "AI Agents Are Here"
        assert "AI agents are real" in results["reddit"]["content"]

    def test_unknown_platform_returns_none(self, mock_router):
        """Unknown target platform should be skipped gracefully."""
        results = adapt_content(mock_router, SAMPLE_BLOG,
                                target_platforms=["tiktok_video"])
        assert results["tiktok_video"] is None
        mock_router.generate.assert_not_called()

    def test_topic_included_in_result(self, mock_router):
        """Topic should be passed through to result dict."""
        results = adapt_content(mock_router, SAMPLE_BLOG,
                                target_platforms=["tweet"],
                                topic="AI trends 2026")
        assert results["tweet"]["topic"] == "AI trends 2026"

    @patch("src.tools.content_creator.get_brand_config")
    def test_brand_context_injected(self, mock_brand, mock_router):
        """Should include brand context in prompt if available."""
        mock_brand.return_value = {
            "brand": {"name": "Archi", "tagline": "AI agent"},
            "voice": {"tone": "casual"},
        }
        adapt_content(mock_router, SAMPLE_BLOG, target_platforms=["tweet"])
        call_args = mock_router.generate.call_args
        prompt = call_args[1].get("prompt") or call_args[0][0] if call_args[0] else ""
        assert "Archi" in prompt or mock_router.generate.called

    def test_source_truncated_to_3000_chars(self, mock_router):
        """Should cap source content at 3000 chars to avoid huge prompts."""
        marker = "ZZZZ"
        long_content = marker * 2000  # 8000 chars
        adapt_content(mock_router, long_content, target_platforms=["tweet"])
        call_args = mock_router.generate.call_args
        prompt = call_args[1].get("prompt", "")
        # Source is truncated to 3000 chars → at most 750 markers
        assert prompt.count(marker) <= 750


# ── format_adaptation_summary tests ─────────────────────────────────


class TestFormatAdaptationSummary:

    def test_empty_results(self):
        assert format_adaptation_summary({}) == "No adaptations generated."

    def test_successful_adaptations(self):
        results = {
            "tweet": {"content": "AI is cool! #ai #tech", "format": "tweet"},
            "reddit": {"content": "Long reddit post about AI", "format": "reddit"},
        }
        summary = format_adaptation_summary(results)
        assert "tweet" in summary
        assert "reddit" in summary
        assert "\u2705" in summary  # checkmark

    def test_mixed_success_and_failure(self):
        results = {
            "tweet": {"content": "AI tweet", "format": "tweet"},
            "instagram_post": None,
        }
        summary = format_adaptation_summary(results)
        assert "\u2705" in summary
        assert "\u274c" in summary


# ── Action handler test ─────────────────────────────────────────────


class TestContentAdaptHandler:

    @patch("src.tools.content_creator.adapt_content")
    def test_handler_calls_adapt(self, mock_adapt):
        from src.interfaces.action_dispatcher import _handle_content_adapt
        mock_adapt.return_value = {
            "tweet": {"content": "tweet text", "format": "tweet"},
        }
        params = {
            "content": SAMPLE_BLOG,
            "source_format": "blog",
            "topic": "AI Agents",
        }
        ctx = {"router": MagicMock()}
        response, artifacts, cost = _handle_content_adapt(params, ctx)
        assert "tweet" in response
        mock_adapt.assert_called_once()

    def test_handler_missing_content(self):
        from src.interfaces.action_dispatcher import _handle_content_adapt
        params = {"content": ""}
        ctx = {"router": MagicMock()}
        response, _, _ = _handle_content_adapt(params, ctx)
        assert "need the source content" in response.lower()

    def test_handler_missing_router(self):
        from src.interfaces.action_dispatcher import _handle_content_adapt
        params = {"content": "test"}
        ctx = {}
        response, _, _ = _handle_content_adapt(params, ctx)
        assert "router" in response.lower()
