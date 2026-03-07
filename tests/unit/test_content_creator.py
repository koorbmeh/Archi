"""Tests for the content creation pipeline (session 228)."""

import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from src.tools.content_creator import (
    _extract_blog_title,
    _parse_reddit_post,
    _slugify,
    generate_content,
    get_content_log,
    get_content_summary,
    publish_to_github_blog,
    publish_tweet,
    publish_tweet_thread,
    publish_reddit_post,
    _log_content_event,
    _CONTENT_LOG,
    _FORMAT_PROMPTS,
)


# ── Content Generation ──────────────────────────────────────────────────


class TestGenerateContent:
    """Tests for the content generator."""

    def test_generate_blog_success(self):
        router = MagicMock()
        router.generate.return_value = {"text": "# My Title\n\nGreat blog content here."}
        result = generate_content(router, "AI trends", "blog")

        assert result is not None
        assert result["format"] == "blog"
        assert result["topic"] == "AI trends"
        assert "Great blog content" in result["content"]
        assert result["title"] == "My Title"
        router.generate.assert_called_once()

    def test_generate_tweet_success(self):
        router = MagicMock()
        router.generate.return_value = {"text": "AI is changing everything! #AI #Tech"}
        result = generate_content(router, "AI news", "tweet")

        assert result is not None
        assert result["format"] == "tweet"
        assert "#AI" in result["content"]

    def test_generate_reddit_success(self):
        router = MagicMock()
        router.generate.return_value = {
            "text": "TITLE: My Reddit Post\nBODY:\nHere is the post content."
        }
        result = generate_content(router, "coding tips", "reddit")

        assert result is not None
        assert result["title"] == "My Reddit Post"
        assert "Here is the post content" in result["content"]

    def test_generate_unknown_format_returns_none(self):
        router = MagicMock()
        result = generate_content(router, "topic", "unknown_format")
        assert result is None
        router.generate.assert_not_called()

    def test_generate_empty_response_returns_none(self):
        router = MagicMock()
        router.generate.return_value = {"text": ""}
        result = generate_content(router, "topic", "blog")
        assert result is None

    def test_generate_model_error_returns_none(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API error")
        result = generate_content(router, "topic", "blog")
        assert result is None

    def test_all_format_prompts_have_placeholders(self):
        for fmt, template in _FORMAT_PROMPTS.items():
            assert "{topic}" in template, f"{fmt} missing {{topic}}"
            assert "{extra_context}" in template, f"{fmt} missing {{extra_context}}"


# ── Title / Parsing Helpers ─────────────────────────────────────────────


class TestHelpers:
    def test_extract_blog_title_h1(self):
        assert _extract_blog_title("# My Post\n\nContent", "fallback") == "My Post"

    def test_extract_blog_title_no_h1(self):
        assert _extract_blog_title("Just content\nNo headers", "fallback") == "Fallback"

    def test_extract_blog_title_skips_h2(self):
        assert _extract_blog_title("## Not H1\nContent", "my title") == "My Title"

    def test_parse_reddit_post_full(self):
        text = "TITLE: My Post\nBODY:\nHello world\nSecond line"
        title, body = _parse_reddit_post(text)
        assert title == "My Post"
        assert "Hello world" in body
        assert "Second line" in body

    def test_parse_reddit_post_no_markers(self):
        title, body = _parse_reddit_post("Just some text")
        assert title is None
        assert body is None

    def test_slugify(self):
        assert _slugify("Hello World!") == "hello-world"
        assert _slugify("AI & Machine Learning: A Guide") == "ai-machine-learning-a-guide"
        assert _slugify("  spaces  ") == "spaces"
        assert len(_slugify("a" * 100)) <= 60


# ── GitHub Blog Publisher ───────────────────────────────────────────────


class TestGitHubPublisher:
    def test_no_pat_returns_error(self):
        with patch.dict(os.environ, {"GITHUB_PAT": "", "GITHUB_BLOG_REPO": "user/repo"}, clear=False):
            result = publish_to_github_blog("Title", "Body")
            assert not result["success"]
            assert "GITHUB_PAT" in result["error"]

    def test_no_repo_returns_error(self):
        with patch.dict(os.environ, {"GITHUB_PAT": "ghp_xxx", "GITHUB_BLOG_REPO": ""}, clear=False):
            result = publish_to_github_blog("Title", "Body")
            assert not result["success"]
            assert "GITHUB_BLOG_REPO" in result["error"]

    @patch("src.tools.content_creator._github_api")
    def test_publish_success(self, mock_api):
        mock_api.return_value = {"content": {"sha": "abc123"}}
        with patch.dict(os.environ, {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": "user/blog"}, clear=False):
            result = publish_to_github_blog("Test Post", "Content here", tags=["ai", "tech"])
            assert result["success"]
            assert "user.github.io" in result["url"]
            assert result["filename"].startswith("_posts/")
            mock_api.assert_called_once()

    @patch("src.tools.content_creator._github_api")
    def test_publish_404_error(self, mock_api):
        from urllib.error import HTTPError
        from io import BytesIO
        mock_api.side_effect = HTTPError(
            "url", 404, "Not Found", {}, BytesIO(b"not found"))
        with patch.dict(os.environ, {"GITHUB_PAT": "ghp_test", "GITHUB_BLOG_REPO": "user/blog"}, clear=False):
            result = publish_to_github_blog("Title", "Body")
            assert not result["success"]
            assert "not found" in result["error"].lower()


# ── Twitter Publisher ───────────────────────────────────────────────────


class TestTwitterPublisher:
    def test_no_credentials_returns_error(self):
        with patch.dict(os.environ, {
            "TWITTER_API_KEY": "", "TWITTER_API_SECRET": "",
            "TWITTER_ACCESS_TOKEN": "", "TWITTER_ACCESS_SECRET": "",
        }, clear=False):
            result = publish_tweet("Hello world")
            assert not result["success"]
            assert "credentials" in result["error"].lower()

    def test_too_long_returns_error(self):
        with patch.dict(os.environ, {
            "TWITTER_API_KEY": "k", "TWITTER_API_SECRET": "s",
            "TWITTER_ACCESS_TOKEN": "t", "TWITTER_ACCESS_SECRET": "a",
        }, clear=False):
            result = publish_tweet("x" * 281)
            assert not result["success"]
            assert "too long" in result["error"].lower()

    def test_thread_no_credentials_returns_error(self):
        with patch.dict(os.environ, {
            "TWITTER_API_KEY": "", "TWITTER_API_SECRET": "",
            "TWITTER_ACCESS_TOKEN": "", "TWITTER_ACCESS_SECRET": "",
        }, clear=False):
            result = publish_tweet_thread(["Tweet 1", "Tweet 2"])
            assert not result["success"]


# ── Reddit Publisher ────────────────────────────────────────────────────


class TestRedditPublisher:
    def test_no_credentials_returns_error(self):
        with patch.dict(os.environ, {
            "REDDIT_CLIENT_ID": "", "REDDIT_CLIENT_SECRET": "",
            "REDDIT_USERNAME": "", "REDDIT_PASSWORD": "",
        }, clear=False):
            result = publish_reddit_post("test", "Title", "Body")
            assert not result["success"]

    def test_title_too_long_returns_error(self):
        with patch.dict(os.environ, {
            "REDDIT_CLIENT_ID": "id", "REDDIT_CLIENT_SECRET": "sec",
            "REDDIT_USERNAME": "user", "REDDIT_PASSWORD": "pass",
        }, clear=False):
            result = publish_reddit_post("test", "x" * 301, "Body")
            assert not result["success"]
            assert "too long" in result["error"].lower()


# ── Content Log ─────────────────────────────────────────────────────────


class TestContentLog:
    def test_log_and_read(self, tmp_path):
        log_file = str(tmp_path / "content_log.jsonl")
        with patch("src.tools.content_creator._CONTENT_LOG", log_file):
            _log_content_event("publish", "github_blog", "Test Post", "https://example.com")
            _log_content_event("publish", "twitter", "A tweet", "https://x.com/123")

            entries = get_content_log(10)
            assert len(entries) == 2
            assert entries[0]["platform"] == "github_blog"
            assert entries[1]["platform"] == "twitter"

    def test_empty_log_returns_empty(self, tmp_path):
        log_file = str(tmp_path / "nonexistent.jsonl")
        with patch("src.tools.content_creator._CONTENT_LOG", log_file):
            assert get_content_log() == []

    def test_content_summary_empty(self, tmp_path):
        log_file = str(tmp_path / "nonexistent.jsonl")
        with patch("src.tools.content_creator._CONTENT_LOG", log_file):
            assert "No content published" in get_content_summary()

    def test_content_summary_with_entries(self, tmp_path):
        log_file = str(tmp_path / "content_log.jsonl")
        with patch("src.tools.content_creator._CONTENT_LOG", log_file):
            _log_content_event("publish", "github_blog", "My Post", "https://example.com")
            summary = get_content_summary()
            assert "github_blog" in summary
            assert "My Post" in summary
