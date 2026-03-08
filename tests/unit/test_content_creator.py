"""Tests for the content creation pipeline (sessions 228-230)."""

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
    publish_to_facebook,
    publish_to_facebook_photo,
    publish_to_instagram,
    publish_to_instagram_carousel,
    _meta_graph_post,
    _get_meta_config,
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


# ── Meta Graph API Publisher (session 230) ─────────────────────────────


class TestMetaConfig:
    """Tests for Meta Graph API configuration."""

    def test_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            config = _get_meta_config()
            assert config["page_access_token"] is None
            assert config["page_id"] is None
            assert config["instagram_account_id"] is None

    def test_credentials_from_env(self):
        env = {
            "META_PAGE_ACCESS_TOKEN": "test_token",
            "META_PAGE_ID": "12345",
            "META_INSTAGRAM_ACCOUNT_ID": "67890",
        }
        with patch.dict(os.environ, env, clear=True):
            config = _get_meta_config()
            assert config["page_access_token"] == "test_token"
            assert config["page_id"] == "12345"
            assert config["instagram_account_id"] == "67890"


class TestPublishToFacebook:
    """Tests for Facebook Page publishing."""

    def test_missing_token(self):
        with patch.dict(os.environ, {}, clear=True):
            result = publish_to_facebook("Hello world")
            assert not result["success"]
            assert "META_PAGE_ACCESS_TOKEN" in result["error"]

    def test_missing_page_id(self):
        with patch.dict(os.environ, {"META_PAGE_ACCESS_TOKEN": "tok"}, clear=True):
            result = publish_to_facebook("Hello world")
            assert not result["success"]
            assert "META_PAGE_ID" in result["error"]

    def test_empty_message(self):
        env = {"META_PAGE_ACCESS_TOKEN": "tok", "META_PAGE_ID": "123"}
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_facebook("   ")
            assert not result["success"]
            assert "empty" in result["error"].lower()

    @patch("src.tools.content_creator._meta_graph_post")
    @patch("src.tools.content_creator._log_content_event")
    def test_successful_post(self, mock_log, mock_post):
        env = {"META_PAGE_ACCESS_TOKEN": "tok", "META_PAGE_ID": "123"}
        mock_post.return_value = {"id": "123_456"}
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_facebook("Hello from Archi!")
            assert result["success"]
            assert result["post_id"] == "123_456"
            mock_log.assert_called_once()

    @patch("src.tools.content_creator._meta_graph_post")
    def test_api_error(self, mock_post):
        env = {"META_PAGE_ACCESS_TOKEN": "tok", "META_PAGE_ID": "123"}
        mock_post.return_value = {"error": "Invalid token"}
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_facebook("Hello")
            assert not result["success"]
            assert "Invalid token" in result["error"]

    @patch("src.tools.content_creator._meta_graph_post")
    @patch("src.tools.content_creator._log_content_event")
    def test_post_with_link(self, mock_log, mock_post):
        env = {"META_PAGE_ACCESS_TOKEN": "tok", "META_PAGE_ID": "123"}
        mock_post.return_value = {"id": "123_789"}
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_facebook("Check this out!", link="https://example.com")
            assert result["success"]
            call_args = mock_post.call_args
            assert call_args[0][1]["link"] == "https://example.com"


class TestPublishToFacebookPhoto:
    """Tests for Facebook Page photo publishing."""

    def test_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            result = publish_to_facebook_photo("https://example.com/img.jpg")
            assert not result["success"]

    @patch("src.tools.content_creator._meta_graph_post")
    @patch("src.tools.content_creator._log_content_event")
    def test_successful_photo(self, mock_log, mock_post):
        env = {"META_PAGE_ACCESS_TOKEN": "tok", "META_PAGE_ID": "123"}
        mock_post.return_value = {"id": "photo_123"}
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_facebook_photo("https://example.com/img.jpg", "Nice pic")
            assert result["success"]
            assert result["post_id"] == "photo_123"


class TestPublishToInstagram:
    """Tests for Instagram publishing."""

    def test_missing_ig_account_id(self):
        with patch.dict(os.environ, {}, clear=True):
            result = publish_to_instagram("https://example.com/img.jpg")
            assert not result["success"]
            assert "META_INSTAGRAM_ACCOUNT_ID" in result["error"]

    def test_missing_token(self):
        env = {"META_INSTAGRAM_ACCOUNT_ID": "ig_123"}
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_instagram("https://example.com/img.jpg")
            assert not result["success"]
            assert "token" in result["error"].lower()

    @patch("src.tools.content_creator._meta_graph_get")
    @patch("src.tools.content_creator._meta_graph_post")
    @patch("src.tools.content_creator._log_content_event")
    def test_successful_single_image(self, mock_log, mock_post, mock_get):
        env = {
            "META_PAGE_ACCESS_TOKEN": "tok",
            "META_INSTAGRAM_ACCOUNT_ID": "ig_123",
        }
        # Step 1: container creation returns ID
        mock_post.side_effect = [
            {"id": "container_1"},  # container
            {"id": "media_1"},      # publish
        ]
        # Step 2: status check returns FINISHED
        mock_get.return_value = {"status_code": "FINISHED"}

        with patch.dict(os.environ, env, clear=True):
            result = publish_to_instagram("https://example.com/img.jpg", "Archi was here")
            assert result["success"]
            assert result["media_id"] == "media_1"
            mock_log.assert_called_once()

    @patch("src.tools.content_creator._meta_graph_post")
    def test_container_creation_error(self, mock_post):
        env = {
            "META_PAGE_ACCESS_TOKEN": "tok",
            "META_INSTAGRAM_ACCOUNT_ID": "ig_123",
        }
        mock_post.return_value = {"error": "Invalid image"}
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_instagram("https://bad-url/img.jpg")
            assert not result["success"]
            assert "Container creation failed" in result["error"]


class TestPublishToInstagramCarousel:
    """Tests for Instagram carousel publishing."""

    def test_too_few_images(self):
        env = {
            "META_PAGE_ACCESS_TOKEN": "tok",
            "META_INSTAGRAM_ACCOUNT_ID": "ig_123",
        }
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_instagram_carousel(["https://example.com/1.jpg"])
            assert not result["success"]
            assert "at least 2" in result["error"]

    def test_too_many_images(self):
        env = {
            "META_PAGE_ACCESS_TOKEN": "tok",
            "META_INSTAGRAM_ACCOUNT_ID": "ig_123",
        }
        urls = [f"https://example.com/{i}.jpg" for i in range(11)]
        with patch.dict(os.environ, env, clear=True):
            result = publish_to_instagram_carousel(urls)
            assert not result["success"]
            assert "at most 10" in result["error"]

    @patch("src.tools.content_creator._meta_graph_get")
    @patch("src.tools.content_creator._meta_graph_post")
    @patch("src.tools.content_creator._log_content_event")
    def test_successful_carousel(self, mock_log, mock_post, mock_get):
        env = {
            "META_PAGE_ACCESS_TOKEN": "tok",
            "META_INSTAGRAM_ACCOUNT_ID": "ig_123",
        }
        mock_post.side_effect = [
            {"id": "child_1"},      # child 1
            {"id": "child_2"},      # child 2
            {"id": "carousel_1"},   # carousel container
            {"id": "published_1"},  # publish
        ]
        mock_get.return_value = {"status_code": "FINISHED"}

        with patch.dict(os.environ, env, clear=True):
            result = publish_to_instagram_carousel(
                ["https://example.com/1.jpg", "https://example.com/2.jpg"],
                caption="Two images!",
            )
            assert result["success"]
            assert result["image_count"] == 2
            mock_log.assert_called_once()
