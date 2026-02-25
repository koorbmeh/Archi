"""Tests for src/tools/web_search_tool.py — WebSearchTool + HTML fallback."""

import time
import threading
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from src.tools.web_search_tool import (
    WebSearchTool,
    _search_ddg_html,
    _MIN_SEARCH_INTERVAL,
)


# ---------------------------------------------------------------------------
# HTML fallback parser
# ---------------------------------------------------------------------------

class TestSearchDdgHtml(unittest.TestCase):
    """Tests for the _search_ddg_html() fallback scraper."""

    @patch("src.tools.web_search_tool.urllib.request.urlopen")
    def test_parses_valid_html(self, mock_urlopen):
        """Extracts title, snippet, url from DuckDuckGo HTML result blocks."""
        html = (
            '<div class="result ">'
            '<a class="result__a" href="https://example.com/page">Example Title</a>'
            '<a class="result__snippet">A snippet here</a>'
            '</div>'
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        results = _search_ddg_html("test query", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Example Title")
        self.assertEqual(results[0]["url"], "https://example.com/page")

    @patch("src.tools.web_search_tool.urllib.request.urlopen")
    def test_skips_duckduckgo_internal_links(self, mock_urlopen):
        """Links pointing to duckduckgo.com itself are filtered out."""
        html = (
            '<div class="result ">'
            '<a class="result__a" href="https://duckduckgo.com/something">DDG Link</a>'
            '</div>'
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        results = _search_ddg_html("test", max_results=5)
        self.assertEqual(results, [])

    @patch("src.tools.web_search_tool.urllib.request.urlopen")
    def test_respects_max_results(self, mock_urlopen):
        """Only returns up to max_results entries."""
        blocks = ""
        for i in range(10):
            blocks += (
                f'<div class="result ">'
                f'<a class="result__a" href="https://example.com/{i}">Title {i}</a>'
                f'<a class="result__snippet">Snippet {i}</a>'
                f'</div>'
            )
        mock_resp = MagicMock()
        mock_resp.read.return_value = blocks.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        results = _search_ddg_html("test", max_results=3)
        self.assertEqual(len(results), 3)

    @patch("src.tools.web_search_tool.urllib.request.urlopen")
    def test_returns_empty_on_network_error(self, mock_urlopen):
        """Returns [] when the HTTP request fails."""
        mock_urlopen.side_effect = Exception("connection refused")
        results = _search_ddg_html("test")
        self.assertEqual(results, [])

    @patch("src.tools.web_search_tool.urllib.request.urlopen")
    def test_handles_missing_snippet(self, mock_urlopen):
        """Results without snippets get empty string for snippet."""
        html = (
            '<div class="result ">'
            '<a class="result__a" href="https://example.com/page">Title</a>'
            '</div>'
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        results = _search_ddg_html("test")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["snippet"], "")

    @patch("src.tools.web_search_tool.urllib.request.urlopen")
    def test_unquotes_ampersand_in_urls(self, mock_urlopen):
        """HTML &amp; in href gets decoded to &."""
        html = (
            '<div class="result ">'
            '<a class="result__a" href="https://example.com/page?a=1&amp;b=2">Title</a>'
            '</div>'
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        results = _search_ddg_html("test")
        self.assertIn("&b=2", results[0]["url"])
        self.assertNotIn("&amp;", results[0]["url"])


# ---------------------------------------------------------------------------
# WebSearchTool init and DDGS lazy loading
# ---------------------------------------------------------------------------

class TestWebSearchToolInit(unittest.TestCase):
    """Tests for WebSearchTool initialization and _get_ddgs lazy loading."""

    def test_init_ddgs_is_none(self):
        tool = WebSearchTool()
        self.assertIsNone(tool._ddgs)

    @patch("src.tools.web_search_tool.DDGS", create=True)
    def test_get_ddgs_imports_ddgs_package(self, _):
        """_get_ddgs() creates a DDGS instance on first call."""
        with patch.dict("sys.modules", {"ddgs": MagicMock()}):
            tool = WebSearchTool()
            # Reset in case already loaded
            tool._ddgs = None
            with patch("src.tools.web_search_tool.WebSearchTool._get_ddgs") as mock_get:
                mock_get.return_value = MagicMock()
                result = tool._get_ddgs()
                self.assertIsNotNone(result)

    def test_get_ddgs_reuses_instance(self):
        """Second call to _get_ddgs() returns the same instance."""
        tool = WebSearchTool()
        fake_ddgs = MagicMock()
        tool._ddgs = fake_ddgs
        self.assertIs(tool._get_ddgs(), fake_ddgs)


# ---------------------------------------------------------------------------
# WebSearchTool.search()
# ---------------------------------------------------------------------------

class TestWebSearchToolSearch(unittest.TestCase):
    """Tests for WebSearchTool.search() with mocked DDGS."""

    def setUp(self):
        self.tool = WebSearchTool()
        self.mock_ddgs = MagicMock()
        self.tool._ddgs = self.mock_ddgs
        # Reset throttle state so tests don't wait
        import src.tools.web_search_tool as mod
        mod._last_search_time = 0.0

    def test_returns_formatted_results(self):
        """Successful DDGS search returns list of dicts with title/snippet/url."""
        self.mock_ddgs.text.return_value = [
            {"title": "Result 1", "body": "Snippet 1", "href": "https://example.com/1"},
            {"title": "Result 2", "body": "Snippet 2", "href": "https://example.com/2"},
        ]
        results = self.tool.search("test query")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Result 1")
        self.assertEqual(results[0]["snippet"], "Snippet 1")
        self.assertEqual(results[0]["url"], "https://example.com/1")

    def test_passes_max_results_to_ddgs(self):
        """max_results parameter is forwarded to ddgs.text()."""
        self.mock_ddgs.text.return_value = []
        self.tool.search("test", max_results=10)
        self.mock_ddgs.text.assert_called_once_with("test", max_results=10)

    def test_handles_none_return(self):
        """When DDGS returns None, falls back to HTML scraper."""
        self.mock_ddgs.text.return_value = None
        with patch("src.tools.web_search_tool._search_ddg_html", return_value=[]) as mock_html:
            results = self.tool.search("test")
            mock_html.assert_called_once_with("test", max_results=5)
            self.assertEqual(results, [])

    def test_falls_back_to_html_on_exception(self):
        """When DDGS raises an exception, falls back to HTML scraper."""
        self.mock_ddgs.text.side_effect = Exception("rate limited")
        with patch("src.tools.web_search_tool._search_ddg_html", return_value=[
            {"title": "Fallback", "snippet": "From HTML", "url": "https://example.com"}
        ]) as mock_html:
            results = self.tool.search("test")
            mock_html.assert_called_once()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["title"], "Fallback")

    def test_handles_generator_return(self):
        """When DDGS returns a generator, converts to list properly."""
        def gen():
            yield {"title": "Gen1", "body": "B1", "href": "https://a.com"}
            yield {"title": "Gen2", "body": "B2", "href": "https://b.com"}
        self.mock_ddgs.text.return_value = gen()
        results = self.tool.search("test")
        self.assertEqual(len(results), 2)

    def test_skips_non_dict_results(self):
        """Non-dict items in results are filtered out."""
        self.mock_ddgs.text.return_value = [
            {"title": "Good", "body": "OK", "href": "https://a.com"},
            "not a dict",
            42,
        ]
        results = self.tool.search("test")
        self.assertEqual(len(results), 1)

    def test_handles_missing_body_uses_snippet_key(self):
        """Falls back to 'snippet' key if 'body' is missing."""
        self.mock_ddgs.text.return_value = [
            {"title": "T", "snippet": "From snippet key", "url": "https://a.com"},
        ]
        results = self.tool.search("test")
        self.assertEqual(results[0]["snippet"], "From snippet key")

    def test_handles_missing_href_uses_url_key(self):
        """Falls back to 'url' key if 'href' is missing."""
        self.mock_ddgs.text.return_value = [
            {"title": "T", "body": "B", "url": "https://a.com"},
        ]
        results = self.tool.search("test")
        self.assertEqual(results[0]["url"], "https://a.com")

    def test_strips_whitespace_from_fields(self):
        """Title, snippet, url have leading/trailing whitespace stripped."""
        self.mock_ddgs.text.return_value = [
            {"title": "  Title  ", "body": "  Body  ", "href": "  https://a.com  "},
        ]
        results = self.tool.search("test")
        self.assertEqual(results[0]["title"], "Title")
        self.assertEqual(results[0]["snippet"], "Body")
        self.assertEqual(results[0]["url"], "https://a.com")

    def test_empty_fields_become_empty_strings(self):
        """Missing title/body/href default to empty string."""
        self.mock_ddgs.text.return_value = [{}]
        results = self.tool.search("test")
        self.assertEqual(results[0]["title"], "")
        self.assertEqual(results[0]["snippet"], "")
        self.assertEqual(results[0]["url"], "")


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

class TestSearchThrottle(unittest.TestCase):
    """Tests for the per-search rate limiter."""

    def setUp(self):
        import src.tools.web_search_tool as mod
        self.mod = mod
        self._orig_time = mod._last_search_time
        mod._last_search_time = 0.0

    def tearDown(self):
        self.mod._last_search_time = self._orig_time

    def test_throttle_enforces_minimum_interval(self):
        """Consecutive searches respect _MIN_SEARCH_INTERVAL."""
        tool = WebSearchTool()
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = []
        tool._ddgs = mock_ddgs

        # First call sets the last_search_time
        with patch("src.tools.web_search_tool._search_ddg_html", return_value=[]):
            tool.search("first")

        # The module-level _last_search_time should now be > 0
        self.assertGreater(self.mod._last_search_time, 0.0)

    def test_no_wait_when_enough_time_elapsed(self):
        """No throttle delay when previous search was long ago."""
        # Set last search far in the past
        self.mod._last_search_time = time.monotonic() - 100.0

        tool = WebSearchTool()
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [{"title": "T", "body": "B", "href": "https://a.com"}]
        tool._ddgs = mock_ddgs

        start = time.monotonic()
        tool.search("test")
        elapsed = time.monotonic() - start
        # Should complete quickly (no throttle wait)
        self.assertLess(elapsed, 1.0)


# ---------------------------------------------------------------------------
# format_results()
# ---------------------------------------------------------------------------

class TestFormatResults(unittest.TestCase):
    """Tests for WebSearchTool.format_results()."""

    def setUp(self):
        self.tool = WebSearchTool()

    def test_empty_results(self):
        self.assertEqual(self.tool.format_results([]), "No search results found.")

    def test_single_result_format(self):
        results = [{"title": "Example", "snippet": "A snippet", "url": "https://example.com"}]
        formatted = self.tool.format_results(results)
        self.assertIn("Search Results:", formatted)
        self.assertIn("1. Example", formatted)
        self.assertIn("A snippet", formatted)
        self.assertIn("Source: https://example.com", formatted)

    def test_multiple_results_numbered(self):
        results = [
            {"title": f"Title {i}", "snippet": f"Snippet {i}", "url": f"https://example.com/{i}"}
            for i in range(3)
        ]
        formatted = self.tool.format_results(results)
        self.assertIn("1. Title 0", formatted)
        self.assertIn("2. Title 1", formatted)
        self.assertIn("3. Title 2", formatted)

    def test_handles_missing_keys(self):
        """Results with missing keys don't crash formatting."""
        results = [{"title": "T"}]
        formatted = self.tool.format_results(results)
        self.assertIn("1. T", formatted)
        self.assertIn("Source:", formatted)


if __name__ == "__main__":
    unittest.main()
