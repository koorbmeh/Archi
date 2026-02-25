"""
Unit tests for browser_control.py.

Covers initialization (with/without config), start, stop, navigate, click, fill,
type_text, press_key, get_text, screenshot, wait_for, evaluate, get_current_url,
get_title, and the atexit cleanup handler.
Session 151.
"""

import sys
import weakref
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure playwright types are available as mocks before import
_mock_playwright_api = MagicMock()
if "playwright" not in sys.modules:
    sys.modules["playwright"] = MagicMock()
    sys.modules["playwright.sync_api"] = _mock_playwright_api

from src.tools import browser_control as bc_mod
from src.tools.browser_control import BrowserControl, _cleanup_all_browsers


@pytest.fixture
def bc():
    """Create a BrowserControl with mocked internals."""
    with patch.object(bc_mod, "sync_playwright", MagicMock()):
        b = BrowserControl(headless=True)
    # Clear from global WeakSet to avoid test cross-contamination
    bc_mod._live_instances.discard(b)
    return b


@pytest.fixture
def bc_with_page(bc):
    """BrowserControl with a mocked page attached."""
    bc.page = MagicMock()
    bc.browser = MagicMock()
    bc.context = MagicMock()
    bc.playwright = MagicMock()
    return bc


# ── TestInit ──────────────────────────────────────────────────────


class TestInit:
    """Tests for BrowserControl initialization."""

    def test_init_default_headless(self):
        with patch.object(bc_mod, "sync_playwright", MagicMock()):
            b = BrowserControl()
        assert b.headless is True
        assert b.page is None
        assert b.browser is None
        bc_mod._live_instances.discard(b)

    def test_init_not_headless(self):
        with patch.object(bc_mod, "sync_playwright", MagicMock()):
            b = BrowserControl(headless=False)
        assert b.headless is False
        bc_mod._live_instances.discard(b)

    def test_init_config_loaded(self):
        """Config values from get_browser_config are used."""
        mock_cfg = {"default_timeout_ms": 8000, "navigation_timeout_ms": 60000}
        with patch("src.utils.config.get_browser_config", return_value=mock_cfg):
            b = BrowserControl()
        assert b.default_timeout == 8000
        assert b.nav_timeout == 60000
        bc_mod._live_instances.discard(b)

    def test_init_config_fallback_on_error(self):
        """Falls back to hardcoded defaults if config loading fails."""
        with patch("src.utils.config.get_browser_config", side_effect=Exception("no config")):
            b = BrowserControl()
        assert b.default_timeout == 5000
        assert b.nav_timeout == 30000
        bc_mod._live_instances.discard(b)

    def test_init_registers_in_live_instances(self):
        with patch.object(bc_mod, "sync_playwright", MagicMock()):
            b = BrowserControl()
        assert b in bc_mod._live_instances
        bc_mod._live_instances.discard(b)


# ── TestStart ─────────────────────────────────────────────────────


class TestStart:
    """Tests for the start method."""

    def test_start_success(self, bc):
        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        with patch.object(bc_mod, "sync_playwright") as mock_sync:
            mock_sync.return_value.start.return_value = mock_pw
            result = bc.start()

        assert result["success"] is True
        assert bc.page is mock_page
        assert bc.browser is mock_browser

    def test_start_already_running(self, bc_with_page):
        result = bc_with_page.start()
        assert result["success"] is True
        assert result["message"] == "Already running"

    def test_start_no_playwright(self, bc):
        original = bc_mod.sync_playwright
        bc_mod.sync_playwright = None
        try:
            result = bc.start()
            assert result["success"] is False
            assert "Playwright not installed" in result["error"]
        finally:
            bc_mod.sync_playwright = original

    def test_start_exception(self, bc):
        with patch.object(bc_mod, "sync_playwright") as mock_sync:
            mock_sync.return_value.start.side_effect = RuntimeError("launch fail")
            result = bc.start()
        assert result["success"] is False
        assert "launch fail" in result["error"]


# ── TestStop ──────────────────────────────────────────────────────


class TestStop:
    """Tests for the stop method."""

    def test_stop_full_cleanup(self, bc_with_page):
        page, ctx, browser, pw = (
            bc_with_page.page,
            bc_with_page.context,
            bc_with_page.browser,
            bc_with_page.playwright,
        )
        result = bc_with_page.stop()
        assert result["success"] is True
        page.close.assert_called_once()
        ctx.close.assert_called_once()
        browser.close.assert_called_once()
        pw.stop.assert_called_once()
        assert bc_with_page.page is None
        assert bc_with_page.browser is None

    def test_stop_nothing_running(self, bc):
        result = bc.stop()
        assert result["success"] is True

    def test_stop_exception(self, bc_with_page):
        bc_with_page.page.close.side_effect = RuntimeError("close fail")
        result = bc_with_page.stop()
        assert result["success"] is False
        assert "close fail" in result["error"]


# ── TestNavigate ──────────────────────────────────────────────────


class TestNavigate:
    """Tests for the navigate method."""

    def test_navigate_success(self, bc_with_page):
        bc_with_page.page.url = "https://example.com"
        bc_with_page.page.title.return_value = "Example"
        result = bc_with_page.navigate("https://example.com")
        assert result["success"] is True
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"
        bc_with_page.page.goto.assert_called_once_with(
            "https://example.com",
            wait_until="domcontentloaded",
            timeout=bc_with_page.nav_timeout,
        )

    def test_navigate_custom_wait_until(self, bc_with_page):
        bc_with_page.page.url = "https://example.com"
        bc_with_page.page.title.return_value = "Example"
        bc_with_page.navigate("https://example.com", wait_until="networkidle")
        bc_with_page.page.goto.assert_called_once_with(
            "https://example.com",
            wait_until="networkidle",
            timeout=bc_with_page.nav_timeout,
        )

    def test_navigate_auto_starts_browser(self, bc):
        """When page is None, navigate calls start() first."""
        # Make start() set page to a mock
        mock_page = MagicMock()
        mock_page.url = "https://test.com"
        mock_page.title.return_value = "Test"

        def fake_start():
            bc.page = mock_page
            return {"success": True}

        with patch.object(bc, "start", side_effect=fake_start):
            result = bc.navigate("https://test.com")
        assert result["success"] is True

    def test_navigate_start_fails(self, bc):
        """If start() fails and page is still None, returns error."""
        with patch.object(bc, "start", return_value={"success": False}):
            result = bc.navigate("https://example.com")
        assert result["success"] is False
        assert "Browser not started" in result["error"]

    def test_navigate_exception(self, bc_with_page):
        bc_with_page.page.goto.side_effect = TimeoutError("nav timeout")
        result = bc_with_page.navigate("https://slow.com")
        assert result["success"] is False
        assert "nav timeout" in result["error"]


# ── TestClick ─────────────────────────────────────────────────────


class TestClick:
    """Tests for the click method."""

    def test_click_success(self, bc_with_page):
        result = bc_with_page.click("#button")
        assert result["success"] is True
        assert result["selector"] == "#button"
        bc_with_page.page.click.assert_called_once_with(
            "#button", timeout=bc_with_page.default_timeout
        )

    def test_click_custom_timeout(self, bc_with_page):
        bc_with_page.click("#button", timeout=10000)
        bc_with_page.page.click.assert_called_once_with("#button", timeout=10000)

    def test_click_no_page(self, bc):
        result = bc.click("#button")
        assert result["success"] is False
        assert "Browser not started" in result["error"]

    def test_click_exception(self, bc_with_page):
        bc_with_page.page.click.side_effect = Exception("element not found")
        result = bc_with_page.click("#missing")
        assert result["success"] is False


# ── TestFill ──────────────────────────────────────────────────────


class TestFill:
    """Tests for the fill method."""

    def test_fill_success(self, bc_with_page):
        result = bc_with_page.fill("#input", "hello world")
        assert result["success"] is True
        assert result["text_length"] == 11
        assert result["selector"] == "#input"

    def test_fill_no_page(self, bc):
        result = bc.fill("#input", "text")
        assert result["success"] is False

    def test_fill_custom_timeout(self, bc_with_page):
        bc_with_page.fill("#input", "text", timeout=9000)
        bc_with_page.page.fill.assert_called_once_with("#input", "text", timeout=9000)

    def test_fill_exception(self, bc_with_page):
        bc_with_page.page.fill.side_effect = Exception("fill fail")
        result = bc_with_page.fill("#input", "text")
        assert result["success"] is False


# ── TestTypeText ──────────────────────────────────────────────────


class TestTypeText:
    """Tests for the type_text method."""

    def test_type_text_success(self, bc_with_page):
        result = bc_with_page.type_text("#input", "hello")
        assert result["success"] is True
        bc_with_page.page.locator.assert_called_once_with("#input")
        bc_with_page.page.locator.return_value.press_sequentially.assert_called_once_with(
            "hello", delay=50
        )

    def test_type_text_custom_delay(self, bc_with_page):
        bc_with_page.type_text("#input", "hi", delay=100)
        bc_with_page.page.locator.return_value.press_sequentially.assert_called_once_with(
            "hi", delay=100
        )

    def test_type_text_no_page(self, bc):
        result = bc.type_text("#input", "text")
        assert result["success"] is False

    def test_type_text_exception(self, bc_with_page):
        bc_with_page.page.locator.side_effect = Exception("locator fail")
        result = bc_with_page.type_text("#input", "text")
        assert result["success"] is False


# ── TestPressKey ──────────────────────────────────────────────────


class TestPressKey:
    """Tests for the press_key method."""

    def test_press_key_success(self, bc_with_page):
        result = bc_with_page.press_key("Enter")
        assert result["success"] is True
        assert result["key"] == "Enter"
        bc_with_page.page.keyboard.press.assert_called_once_with("Enter")

    def test_press_key_no_page(self, bc):
        result = bc.press_key("Tab")
        assert result["success"] is False

    def test_press_key_exception(self, bc_with_page):
        bc_with_page.page.keyboard.press.side_effect = Exception("key fail")
        result = bc_with_page.press_key("Escape")
        assert result["success"] is False


# ── TestGetText ───────────────────────────────────────────────────


class TestGetText:
    """Tests for the get_text method."""

    def test_get_text_success(self, bc_with_page):
        mock_element = MagicMock()
        mock_element.text_content.return_value = "Hello World"
        bc_with_page.page.wait_for_selector.return_value = mock_element
        result = bc_with_page.get_text("#heading")
        assert result["success"] is True
        assert result["text"] == "Hello World"

    def test_get_text_element_none(self, bc_with_page):
        bc_with_page.page.wait_for_selector.return_value = None
        result = bc_with_page.get_text("#missing")
        assert result["success"] is True
        assert result["text"] is None

    def test_get_text_no_page(self, bc):
        result = bc.get_text("#heading")
        assert result["success"] is False

    def test_get_text_exception(self, bc_with_page):
        bc_with_page.page.wait_for_selector.side_effect = TimeoutError("timeout")
        result = bc_with_page.get_text("#slow")
        assert result["success"] is False


# ── TestScreenshot ────────────────────────────────────────────────


class TestScreenshot:
    """Tests for the screenshot method."""

    def test_screenshot_to_bytes(self, bc_with_page):
        bc_with_page.page.screenshot.return_value = b"\x89PNG"
        result = bc_with_page.screenshot()
        assert result["success"] is True
        assert result["bytes"] == b"\x89PNG"
        assert result["size"] == 4

    def test_screenshot_to_file(self, bc_with_page, tmp_path):
        filepath = tmp_path / "screenshots" / "test.png"
        result = bc_with_page.screenshot(filepath=filepath)
        assert result["success"] is True
        assert result["filepath"] == str(filepath)
        bc_with_page.page.screenshot.assert_called_once_with(
            path=str(filepath), full_page=False
        )

    def test_screenshot_full_page(self, bc_with_page):
        bc_with_page.page.screenshot.return_value = b"\x89PNG"
        bc_with_page.screenshot(full_page=True)
        bc_with_page.page.screenshot.assert_called_once_with(full_page=True)

    def test_screenshot_no_page(self, bc):
        result = bc.screenshot()
        assert result["success"] is False

    def test_screenshot_exception(self, bc_with_page):
        bc_with_page.page.screenshot.side_effect = Exception("screenshot fail")
        result = bc_with_page.screenshot()
        assert result["success"] is False


# ── TestWaitFor ───────────────────────────────────────────────────


class TestWaitFor:
    """Tests for the wait_for method."""

    def test_wait_for_success(self, bc_with_page):
        result = bc_with_page.wait_for("#element")
        assert result["success"] is True
        assert result["selector"] == "#element"

    def test_wait_for_custom_timeout(self, bc_with_page):
        bc_with_page.wait_for("#element", timeout=15000)
        bc_with_page.page.wait_for_selector.assert_called_once_with(
            "#element", timeout=15000
        )

    def test_wait_for_no_page(self, bc):
        result = bc.wait_for("#element")
        assert result["success"] is False

    def test_wait_for_exception(self, bc_with_page):
        bc_with_page.page.wait_for_selector.side_effect = TimeoutError("timeout")
        result = bc_with_page.wait_for("#slow")
        assert result["success"] is False


# ── TestEvaluate ──────────────────────────────────────────────────


class TestEvaluate:
    """Tests for the evaluate (JavaScript execution) method."""

    def test_evaluate_success(self, bc_with_page):
        bc_with_page.page.evaluate.return_value = 42
        result = bc_with_page.evaluate("1 + 41")
        assert result["success"] is True
        assert result["result"] == 42

    def test_evaluate_no_page(self, bc):
        result = bc.evaluate("document.title")
        assert result["success"] is False

    def test_evaluate_exception(self, bc_with_page):
        bc_with_page.page.evaluate.side_effect = Exception("eval fail")
        result = bc_with_page.evaluate("bad script")
        assert result["success"] is False


# ── TestGetCurrentUrl ─────────────────────────────────────────────


class TestGetCurrentUrl:
    """Tests for the get_current_url method."""

    def test_returns_url_when_page_exists(self, bc_with_page):
        bc_with_page.page.url = "https://example.com/page"
        assert bc_with_page.get_current_url() == "https://example.com/page"

    def test_returns_empty_when_no_page(self, bc):
        assert bc.get_current_url() == ""


# ── TestGetTitle ──────────────────────────────────────────────────


class TestGetTitle:
    """Tests for the get_title method."""

    def test_returns_title_when_page_exists(self, bc_with_page):
        bc_with_page.page.title.return_value = "Example Page"
        assert bc_with_page.get_title() == "Example Page"

    def test_returns_empty_when_no_page(self, bc):
        assert bc.get_title() == ""


# ── TestCleanupAllBrowsers ────────────────────────────────────────


class TestCleanupAllBrowsers:
    """Tests for the _cleanup_all_browsers atexit handler."""

    def test_cleanup_calls_stop_on_active_instances(self):
        mock_bc = MagicMock()
        mock_bc.browser = MagicMock()
        mock_bc.playwright = MagicMock()

        original = set(bc_mod._live_instances)
        bc_mod._live_instances.add(mock_bc)
        try:
            _cleanup_all_browsers()
            mock_bc.stop.assert_called_once()
        finally:
            bc_mod._live_instances.discard(mock_bc)

    def test_cleanup_skips_inactive_instances(self):
        mock_bc = MagicMock()
        mock_bc.browser = None
        mock_bc.playwright = None

        bc_mod._live_instances.add(mock_bc)
        try:
            _cleanup_all_browsers()
            mock_bc.stop.assert_not_called()
        finally:
            bc_mod._live_instances.discard(mock_bc)

    def test_cleanup_handles_stop_exception(self):
        mock_bc = MagicMock()
        mock_bc.browser = MagicMock()
        mock_bc.playwright = MagicMock()
        mock_bc.stop.side_effect = Exception("stop fail")

        bc_mod._live_instances.add(mock_bc)
        try:
            # Should not raise
            _cleanup_all_browsers()
        finally:
            bc_mod._live_instances.discard(mock_bc)
