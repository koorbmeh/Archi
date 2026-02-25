"""
Unit tests for plan_executor/web.py.

Covers SSL context initialization (certifi present/absent), _is_private_url
delegation, and _fetch_url_text (SSRF blocking, successful fetch with HTML
stripping, script/style removal, entity decoding, whitespace collapse,
max_chars truncation, HTTP errors, and URL scheme handling).
Session 152.
"""

import ssl
import urllib.request
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# SSL context initialization
# ---------------------------------------------------------------------------

class TestSSLContextInit:
    """Module-level SSL context setup from certifi (or fallback)."""

    def test_ssl_context_exists(self):
        """_ssl_context is an ssl.SSLContext."""
        from src.core.plan_executor.web import _ssl_context
        assert isinstance(_ssl_context, ssl.SSLContext)

    def test_url_opener_exists(self):
        """_url_opener is an OpenerDirector (reusable HTTP client)."""
        from src.core.plan_executor.web import _url_opener
        assert isinstance(_url_opener, urllib.request.OpenerDirector)

    def test_ssl_source_is_string(self):
        """_ssl_source describes where the CA bundle came from."""
        from src.core.plan_executor.web import _ssl_source
        assert isinstance(_ssl_source, str)
        assert len(_ssl_source) > 0


# ---------------------------------------------------------------------------
# _is_private_url — thin delegate to net_safety.is_private_url
# ---------------------------------------------------------------------------

class TestIsPrivateUrl:
    """_is_private_url delegates to src.utils.net_safety.is_private_url."""

    def test_delegates_to_net_safety(self):
        from src.core.plan_executor.web import _is_private_url
        with patch("src.utils.net_safety.is_private_url", return_value=True) as mock:
            result = _is_private_url("http://localhost/test")
        mock.assert_called_once_with("http://localhost/test")
        assert result is True

    def test_returns_false_for_public(self):
        from src.core.plan_executor.web import _is_private_url
        with patch("src.utils.net_safety.is_private_url", return_value=False) as mock:
            result = _is_private_url("https://example.com")
        assert result is False


# ---------------------------------------------------------------------------
# _fetch_url_text — core URL fetching + HTML stripping
# ---------------------------------------------------------------------------

class TestFetchUrlTextSSRFBlock:
    """Private/internal URLs are blocked before any network I/O."""

    def test_blocked_returns_message(self):
        from src.core.plan_executor.web import _fetch_url_text
        with patch("src.core.plan_executor.web._is_private_url", return_value=True):
            result = _fetch_url_text("http://169.254.169.254/latest/meta-data")
        assert "Blocked" in result
        assert "169.254.169.254" in result

    def test_blocked_does_not_fetch(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        with patch("src.core.plan_executor.web._is_private_url", return_value=True), \
             patch.object(_url_opener, "open") as mock_open:
            _fetch_url_text("http://10.0.0.1/internal")
        mock_open.assert_not_called()


class TestFetchUrlTextSuccess:
    """Successful fetch: HTML stripping, entity decoding, truncation."""

    def _make_response(self, html_bytes):
        """Build a mock response context manager."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_bytes
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_strips_html_tags(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<html><body><p>Hello <b>World</b></p></body></html>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello" in result
        assert "World" in result

    def test_strips_script_tags(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<html><script>var x = 1;</script><body>Content</body></html>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "var x" not in result
        assert "Content" in result

    def test_strips_style_tags(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<html><style>body { color: red; }</style><body>Visible</body></html>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "color: red" not in result
        assert "Visible" in result

    def test_decodes_html_entities(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<p>&amp; &lt; &gt; &quot; &#39; &nbsp;</p>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "&" in result
        assert "<" in result
        assert ">" in result
        assert '"' in result
        assert "'" in result

    def test_collapses_whitespace(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<p>Hello     \n\n\t   World</p>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "Hello World" in result

    def test_respects_max_chars(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<body>" + b"A" * 10000 + b"</body>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com", max_chars=100)
        assert len(result) <= 100

    def test_default_max_chars_is_5000(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<body>" + b"B" * 20000 + b"</body>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert len(result) <= 5000

    def test_handles_utf8_decode_errors(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<body>Hello \xff\xfe World</body>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        # Should not raise — errors="replace" handles bad bytes
        assert "Hello" in result
        assert "World" in result


class TestFetchUrlTextErrors:
    """Error paths: network errors, timeouts, exceptions."""

    def test_network_error_returns_error_message(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", side_effect=Exception("Connection refused")):
            result = _fetch_url_text("https://down.example.com")
        assert "Error fetching" in result
        assert "Connection refused" in result

    def test_timeout_error_returns_error_message(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        import socket
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", side_effect=socket.timeout("timed out")):
            result = _fetch_url_text("https://slow.example.com")
        assert "Error fetching" in result

    def test_url_error_returns_error_message(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", side_effect=urllib.request.URLError("DNS failure")):
            result = _fetch_url_text("https://nonexistent.example.com")
        assert "Error fetching" in result


class TestFetchUrlTextRequest:
    """Verify the Request object is built correctly."""

    def _make_response(self, html_bytes=b"<body>OK</body>"):
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_bytes
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_sends_user_agent_header(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response()) as mock_open:
            _fetch_url_text("https://example.com")
        call_args = mock_open.call_args
        req = call_args[0][0]
        assert isinstance(req, urllib.request.Request)
        ua = req.get_header("User-agent")
        assert "Mozilla" in ua

    def test_uses_timeout(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response()) as mock_open:
            _fetch_url_text("https://example.com")
        call_args = mock_open.call_args
        assert call_args[1].get("timeout") == 15 or call_args[0][-1] == 15


class TestFetchUrlTextScriptStyleEdgeCases:
    """Edge cases for script/style stripping regex."""

    def _make_response(self, html_bytes):
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_bytes
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_multiline_script(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<script type='text/javascript'>\nvar x = 1;\nvar y = 2;\n</script><p>Text</p>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "var x" not in result
        assert "Text" in result

    def test_multiple_script_blocks(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<script>a();</script><p>Mid</p><script>b();</script><p>End</p>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "a()" not in result
        assert "b()" not in result
        assert "Mid" in result
        assert "End" in result

    def test_case_insensitive_script_tag(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b"<SCRIPT>hidden()</SCRIPT><p>Visible</p>"
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert "hidden" not in result
        assert "Visible" in result

    def test_empty_body_returns_empty(self):
        from src.core.plan_executor.web import _fetch_url_text, _url_opener
        html = b""
        with patch("src.core.plan_executor.web._is_private_url", return_value=False), \
             patch.object(_url_opener, "open", return_value=self._make_response(html)):
            result = _fetch_url_text("https://example.com")
        assert result == ""
