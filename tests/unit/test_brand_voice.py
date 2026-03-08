"""Tests for brand voice injection in content generation (session 234)."""

from unittest.mock import MagicMock, patch

import pytest

from src.tools.content_creator import (
    _build_brand_context,
    _detect_pillar,
    _pillar_context,
    generate_content,
)


# ── Brand Context Builder ──────────────────────────────────────────────


class TestBuildBrandContext:
    """Tests for _build_brand_context()."""

    _SAMPLE_BRAND = {
        "brand": {"name": "Archi", "tagline": "An AI learning out loud", "bio": "I am Archi."},
        "voice": {
            "tone": "conversational, direct",
            "perspective": "first-person",
            "style_notes": ["Short sentences.", "Lead with insight."],
        },
        "platform_style": {
            "tweet": {"tone_adjust": "punchier", "format_notes": "Hook first."},
            "blog": {"tone_adjust": "more detailed"},
        },
        "content_rules": ["Never give financial advice", "Always be transparent about being AI"],
    }

    @patch("src.tools.content_creator.get_brand_config")
    def test_full_brand_context(self, mock_brand):
        mock_brand.return_value = self._SAMPLE_BRAND
        ctx = _build_brand_context("tweet")

        assert "Archi" in ctx
        assert "An AI learning out loud" in ctx
        assert "conversational, direct" in ctx
        assert "first-person" in ctx
        assert "punchier" in ctx
        assert "Hook first." in ctx
        assert "Never give financial advice" in ctx

    @patch("src.tools.content_creator.get_brand_config")
    def test_empty_brand_returns_empty(self, mock_brand):
        mock_brand.return_value = {}
        assert _build_brand_context("blog") == ""

    @patch("src.tools.content_creator.get_brand_config")
    def test_no_platform_style_still_works(self, mock_brand):
        mock_brand.return_value = {
            "brand": {"name": "Archi", "tagline": "Test"},
            "voice": {"tone": "direct"},
        }
        ctx = _build_brand_context("unknown_format")
        assert "Archi" in ctx
        assert "direct" in ctx

    @patch("src.tools.content_creator.get_brand_config")
    def test_blog_format_gets_blog_style(self, mock_brand):
        mock_brand.return_value = self._SAMPLE_BRAND
        ctx = _build_brand_context("blog")
        assert "more detailed" in ctx
        # Should NOT have tweet-specific adjustments
        assert "punchier" not in ctx


# ── Pillar Detection ───────────────────────────────────────────────────


class TestDetectPillar:
    """Tests for _detect_pillar()."""

    _SAMPLE_BRAND = {
        "topic_pillars": [
            {"id": "ai_tech", "name": "AI & Tech", "keywords": ["ai", "machine learning", "llm", "tech"]},
            {"id": "finance", "name": "Finance", "keywords": ["finance", "money", "investing", "crypto"]},
            {"id": "health_fitness", "name": "Health", "keywords": ["health", "fitness", "workout", "nutrition"]},
            {"id": "self_improvement", "name": "Self-Improvement", "keywords": ["motivation", "discipline", "habits"]},
        ]
    }

    @patch("src.tools.content_creator.get_brand_config")
    def test_ai_topic_matches_ai_pillar(self, mock_brand):
        mock_brand.return_value = self._SAMPLE_BRAND
        pillar = _detect_pillar("Latest AI and machine learning breakthroughs")
        assert pillar is not None
        assert pillar["id"] == "ai_tech"

    @patch("src.tools.content_creator.get_brand_config")
    def test_finance_topic_matches_finance_pillar(self, mock_brand):
        mock_brand.return_value = self._SAMPLE_BRAND
        pillar = _detect_pillar("How to start investing your money wisely")
        assert pillar is not None
        assert pillar["id"] == "finance"

    @patch("src.tools.content_creator.get_brand_config")
    def test_health_topic_matches_health_pillar(self, mock_brand):
        mock_brand.return_value = self._SAMPLE_BRAND
        pillar = _detect_pillar("Best workout routines for fitness beginners")
        assert pillar is not None
        assert pillar["id"] == "health_fitness"

    @patch("src.tools.content_creator.get_brand_config")
    def test_no_match_returns_none(self, mock_brand):
        mock_brand.return_value = self._SAMPLE_BRAND
        pillar = _detect_pillar("My favorite sandwich recipes")
        assert pillar is None

    @patch("src.tools.content_creator.get_brand_config")
    def test_empty_config_returns_none(self, mock_brand):
        mock_brand.return_value = {}
        assert _detect_pillar("anything") is None

    @patch("src.tools.content_creator.get_brand_config")
    def test_multiple_keyword_hits_picks_best(self, mock_brand):
        mock_brand.return_value = self._SAMPLE_BRAND
        # "AI" + "tech" = 2 hits for ai_tech, "money" = 1 hit for finance
        pillar = _detect_pillar("AI tech and a bit about money")
        assert pillar["id"] == "ai_tech"


# ── Pillar Context ─────────────────────────────────────────────────────


class TestPillarContext:
    """Tests for _pillar_context()."""

    def test_pillar_with_angles(self):
        pillar = {
            "id": "ai_tech",
            "name": "AI & Tech",
            "angles": ["What's actually changing vs. hype", "Tools that save time"],
        }
        ctx = _pillar_context(pillar)
        assert "AI & Tech" in ctx
        assert "What's actually changing" in ctx

    def test_none_pillar(self):
        assert _pillar_context(None) == ""

    def test_pillar_no_angles(self):
        assert _pillar_context({"id": "x", "name": "X", "angles": []}) == ""


# ── Integration: generate_content with brand voice ─────────────────────


class TestGenerateContentBrandVoice:
    """Tests that generate_content() injects brand voice into prompts."""

    _BRAND = {
        "brand": {"name": "Archi", "tagline": "An AI learning out loud"},
        "voice": {"tone": "conversational", "perspective": "first-person"},
        "topic_pillars": [
            {
                "id": "ai_tech",
                "name": "AI & Tech",
                "keywords": ["ai", "machine learning"],
                "angles": ["What's changing vs. hype"],
            }
        ],
        "platform_style": {"tweet": {"tone_adjust": "punchier"}},
        "content_rules": ["Be transparent about being AI"],
    }

    @patch("src.tools.content_creator.get_brand_config")
    def test_brand_context_injected_into_prompt(self, mock_brand):
        mock_brand.return_value = self._BRAND
        router = MagicMock()
        router.generate.return_value = {"text": "AI is wild! #AI"}

        result = generate_content(router, "AI trends", "tweet")

        assert result is not None
        # Check the prompt sent to the router includes brand context
        call_args = router.generate.call_args
        prompt = call_args[1].get("prompt") or call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        assert "Archi" in prompt
        assert "An AI learning out loud" in prompt
        assert "conversational" in prompt

    @patch("src.tools.content_creator.get_brand_config")
    def test_pillar_auto_tagged(self, mock_brand):
        mock_brand.return_value = self._BRAND
        router = MagicMock()
        router.generate.return_value = {"text": "# AI Trends\n\nContent about AI."}

        result = generate_content(router, "latest AI and machine learning news", "blog")

        assert result is not None
        assert result.get("pillar") == "ai_tech"
        assert result.get("pillar_name") == "AI & Tech"

    @patch("src.tools.content_creator.get_brand_config")
    def test_no_pillar_when_topic_unrelated(self, mock_brand):
        mock_brand.return_value = self._BRAND
        router = MagicMock()
        router.generate.return_value = {"text": "Great sandwich tips!"}

        result = generate_content(router, "sandwich recipes", "tweet")

        assert result is not None
        assert "pillar" not in result

    @patch("src.tools.content_creator.get_brand_config")
    def test_graceful_without_brand_config(self, mock_brand):
        """Content generation still works without brand config — just no voice injection."""
        mock_brand.return_value = {}
        router = MagicMock()
        router.generate.return_value = {"text": "Hello world!"}

        result = generate_content(router, "greetings", "tweet")

        assert result is not None
        assert result["content"] == "Hello world!"
        assert "pillar" not in result

    @patch("src.tools.content_creator.get_brand_config")
    def test_pillar_angles_in_prompt(self, mock_brand):
        mock_brand.return_value = self._BRAND
        router = MagicMock()
        router.generate.return_value = {"text": "AI content here."}

        generate_content(router, "AI and machine learning developments", "blog")

        call_args = router.generate.call_args
        prompt = call_args[1].get("prompt") or call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        assert "What's changing vs. hype" in prompt
