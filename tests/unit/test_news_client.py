"""Tests for src/utils/news_client.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.utils.news_client import (
    _fetch_json,
    _fetch_hn_story,
    get_hn_top,
    get_rss_headlines,
    get_headlines,
    _DEFAULT_FEEDS,
)


# ── HN story fetching ─────────────────────────────────────────────

class TestFetchHnStory:
    @patch("src.utils.news_client._fetch_json")
    def test_returns_story_dict(self, mock_fetch):
        mock_fetch.return_value = {
            "id": 42,
            "type": "story",
            "title": "Test Story",
            "url": "https://example.com",
            "score": 100,
            "by": "user",
        }
        result = _fetch_hn_story(42)
        assert result is not None
        assert result["title"] == "Test Story"
        assert result["url"] == "https://example.com"
        assert result["source"] == "Hacker News"
        assert result["score"] == 100

    @patch("src.utils.news_client._fetch_json")
    def test_skips_dead_stories(self, mock_fetch):
        mock_fetch.return_value = {"id": 1, "type": "story", "dead": True, "title": "Dead"}
        assert _fetch_hn_story(1) is None

    @patch("src.utils.news_client._fetch_json")
    def test_skips_non_story(self, mock_fetch):
        mock_fetch.return_value = {"id": 1, "type": "comment", "title": "Not a story"}
        assert _fetch_hn_story(1) is None

    @patch("src.utils.news_client._fetch_json")
    def test_handles_fetch_error(self, mock_fetch):
        mock_fetch.side_effect = Exception("Network error")
        assert _fetch_hn_story(1) is None

    @patch("src.utils.news_client._fetch_json")
    def test_fallback_url_when_missing(self, mock_fetch):
        mock_fetch.return_value = {"id": 99, "type": "story", "title": "Ask HN", "score": 50}
        result = _fetch_hn_story(99)
        assert result is not None
        assert "item?id=99" in result["url"]


class TestGetHnTop:
    @patch("src.utils.news_client._fetch_hn_story")
    @patch("src.utils.news_client._fetch_json")
    def test_returns_top_stories(self, mock_fetch_json, mock_fetch_story):
        mock_fetch_json.return_value = [1, 2, 3, 4, 5]
        mock_fetch_story.side_effect = lambda sid: {
            "title": f"Story {sid}",
            "url": f"https://example.com/{sid}",
            "source": "Hacker News",
            "score": sid * 10,
        }
        stories = get_hn_top(count=3)
        assert len(stories) <= 3
        # Should be sorted by score descending
        scores = [s["score"] for s in stories]
        assert scores == sorted(scores, reverse=True)

    @patch("src.utils.news_client._fetch_json")
    def test_handles_api_failure(self, mock_fetch):
        mock_fetch.side_effect = Exception("API down")
        assert get_hn_top() == []


# ── RSS feeds ────────────────────────────────────────────────────

class TestGetRssHeadlines:
    @patch("src.utils.news_client._parse_rss")
    def test_collects_from_feeds(self, mock_parse):
        mock_parse.return_value = [
            {"title": "Article 1", "url": "https://example.com/1", "source": "Test"},
        ]
        results = get_rss_headlines(feeds=[{"url": "http://test.com/feed", "source": "Test"}])
        assert len(results) >= 1
        assert results[0]["title"] == "Article 1"

    @patch("src.utils.news_client._parse_rss")
    def test_handles_parse_failure(self, mock_parse):
        mock_parse.side_effect = Exception("Parse error")
        # Should not raise
        results = get_rss_headlines(feeds=[{"url": "http://bad.com", "source": "Bad"}])
        assert results == []


# ── Combined headlines ───────────────────────────────────────────

class TestGetHeadlines:
    @patch("src.utils.news_client.get_rss_headlines")
    @patch("src.utils.news_client.get_hn_top")
    def test_combines_sources(self, mock_hn, mock_rss):
        mock_hn.return_value = [
            {"title": "HN Story", "url": "https://hn.com", "source": "Hacker News", "score": 50},
        ]
        mock_rss.return_value = [
            {"title": "RSS Article", "url": "https://rss.com", "source": "BBC Tech"},
        ]
        result = get_headlines()
        assert "hn" in result
        assert "rss" in result
        assert "summary" in result
        assert "fetched_at" in result
        assert "HN Story" in result["summary"]
        assert "RSS Article" in result["summary"]

    @patch("src.utils.news_client.get_rss_headlines")
    @patch("src.utils.news_client.get_hn_top")
    def test_empty_when_all_fail(self, mock_hn, mock_rss):
        mock_hn.return_value = []
        mock_rss.return_value = []
        result = get_headlines()
        assert result["summary"] == "No headlines available."

    def test_default_feeds_exist(self):
        """Ensure default feed list is populated."""
        assert len(_DEFAULT_FEEDS) >= 2
        for feed in _DEFAULT_FEEDS:
            assert "url" in feed
            assert "source" in feed
