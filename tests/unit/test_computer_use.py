"""Unit tests for the Computer Use orchestrator — UI task routing via
cache, known positions, and vision API fallback.

Tests ComputerUse init, click_element routing (cache hit/miss, known
positions, vision fallback), type_in_element, get_stats, and
_start_button_fallback.

Created session 149.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock


# ── ComputerUse init tests ─────────────────────────────────────────


class TestComputerUseInit:
    """Tests for ComputerUse.__init__() — initialization and lazy loading."""

    @patch("src.tools.computer_use._base_path", return_value="/fake/base")
    def test_init_creates_data_dir(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        assert cu._data_dir.exists()
        assert cu._desktop is None
        assert cu._browser is None
        assert cu._ui_memory is None

    @patch("src.tools.computer_use._base_path", return_value="/fake/base")
    def test_lazy_desktop(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        mock_desktop = MagicMock()
        # DesktopControl is imported lazily inside _get_desktop()
        with patch.dict("sys.modules", {"src.tools.desktop_control": MagicMock(DesktopControl=MagicMock(return_value=mock_desktop))}):
            result = cu._get_desktop()
        assert result is mock_desktop
        # Second call returns cached
        assert cu._get_desktop() is mock_desktop

    @patch("src.tools.computer_use._base_path", return_value="/fake/base")
    def test_lazy_browser(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        mock_browser = MagicMock()
        with patch.dict("sys.modules", {"src.tools.browser_control": MagicMock(BrowserControl=MagicMock(return_value=mock_browser))}):
            result = cu._get_browser()
        assert result is mock_browser

    @patch("src.tools.computer_use._base_path", return_value="/fake/base")
    def test_lazy_ui_memory(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        mock_mem = MagicMock()
        with patch.dict("sys.modules", {"src.tools.ui_memory": MagicMock(UIMemory=MagicMock(return_value=mock_mem))}):
            result = cu._get_ui_memory()
        assert result is mock_mem


# ── click_element() cache hit tests ────────────────────────────────


class TestClickElementCacheHit:
    """Tests for click_element() when UI Memory cache hits."""

    @patch("src.tools.computer_use._base_path")
    def test_cached_coordinate_click(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = {
            "type": "coordinate",
            "location": {"x": 100, "y": 200},
        }
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.click.return_value = {"success": True}
        mock_desktop.screen_size = (1920, 1080)
        cu._desktop = mock_desktop

        result = cu.click_element("login button", "app")
        assert result["success"] is True
        assert result["method"] == "cached_coordinate"
        assert result["cost_usd"] == 0.0
        mock_mem.record_success.assert_called_once()

    @patch("src.tools.computer_use._base_path")
    def test_cached_coordinate_failure_records_failure(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = {
            "type": "coordinate",
            "location": {"x": 100, "y": 200},
        }
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.click.return_value = {"success": False}
        mock_desktop.screen_size = (1920, 1080)
        cu._desktop = mock_desktop

        # Should fall through to known positions / vision after cache fail
        # Mock vision disabled to get clean failure
        result = cu.click_element("login button", "app", use_vision=False)
        mock_mem.record_failure.assert_called_once()

    @patch("src.tools.computer_use._base_path")
    def test_cached_selector_click(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = {
            "type": "selector",
            "location": {"selector": "#login-btn"},
        }
        cu._ui_memory = mock_mem

        mock_browser = MagicMock()
        mock_browser.page = True  # page is open
        mock_browser.click.return_value = {"success": True}
        cu._browser = mock_browser

        mock_desktop = MagicMock()
        mock_desktop.screen_size = (1920, 1080)
        cu._desktop = mock_desktop

        result = cu.click_element("login button", "app")
        assert result["success"] is True
        assert result["method"] == "cached_selector"
        mock_mem.record_success.assert_called_once()

    @patch("src.tools.computer_use._base_path")
    def test_cached_selector_starts_browser_if_no_page(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = {
            "type": "selector",
            "location": {"selector": "#btn"},
        }
        cu._ui_memory = mock_mem

        mock_browser = MagicMock()
        mock_browser.page = None  # No page yet
        mock_browser.click.return_value = {"success": True}
        cu._browser = mock_browser

        mock_desktop = MagicMock()
        mock_desktop.screen_size = (1920, 1080)
        cu._desktop = mock_desktop

        cu.click_element("button", "app")
        mock_browser.start.assert_called_once()


# ── click_element() known positions tests ──────────────────────────


class TestClickElementKnownPositions:
    """Tests for click_element() known position routing (e.g., Windows Start)."""

    @patch("src.tools.computer_use._base_path")
    def test_windows_start_known_position(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = None  # Cache miss
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screen_size = (1920, 1080)
        mock_desktop.click.return_value = {"success": True}
        cu._desktop = mock_desktop

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("START_BUTTON_X", None)
            result = cu.click_element("Windows Start button")

        assert result["method"] == "known_position"
        assert result["cost_usd"] == 0.0
        # Default 0.33 * 1920 = 633, y = 1080 - 45 = 1035
        mock_desktop.click.assert_called_with(633, 1035)
        mock_mem.store_element.assert_called_once()

    @patch("src.tools.computer_use._base_path")
    def test_windows_start_env_override(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = None
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screen_size = (1920, 1080)
        mock_desktop.click.return_value = {"success": True}
        cu._desktop = mock_desktop

        with patch.dict(os.environ, {"START_BUTTON_X": "0.5"}):
            result = cu.click_element("Windows Start button")

        # 0.5 * 1920 = 960
        mock_desktop.click.assert_called_with(960, 1035)

    @patch("src.tools.computer_use._base_path")
    def test_windows_start_absolute_pixel_env(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = None
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screen_size = (1920, 1080)
        mock_desktop.click.return_value = {"success": True}
        cu._desktop = mock_desktop

        with patch.dict(os.environ, {"START_BUTTON_X": "800"}):
            result = cu.click_element("Windows Start button")

        # 800 > 1 so treated as absolute pixel
        mock_desktop.click.assert_called_with(800, 1035)


# ── click_element() cache miss → vision tests ─────────────────────


class TestClickElementVision:
    """Tests for click_element() when it falls through to vision API."""

    @patch("src.tools.computer_use._base_path")
    def test_vision_disabled_returns_failure(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = None
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screen_size = (1920, 1080)
        cu._desktop = mock_desktop

        result = cu.click_element("some button", use_vision=False)
        assert result["success"] is False
        assert "vision disabled" in result["error"].lower()


# ── _click_via_vision() tests ──────────────────────────────────────


class TestClickViaVision:
    """Tests for _click_via_vision() — screenshot → API → click → cache."""

    @patch("src.tools.computer_use._base_path")
    def test_screenshot_failure(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screenshot.return_value = {"success": False}
        mock_desktop.screen_size = (1920, 1080)

        result = cu._click_via_vision("button", "app", mock_desktop, 1920, 1080)
        assert result["success"] is False
        assert "screenshot" in result["error"].lower()

    @patch("src.tools.computer_use._base_path")
    def test_vision_api_failure(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screenshot.return_value = {"success": True}

        with patch("src.tools.computer_use._find_element_with_api") as mock_api:
            mock_api.return_value = {"success": False, "error": "Not found"}
            with patch("src.tools.computer_use.Image", create=True):
                result = cu._click_via_vision("button", "app", mock_desktop, 1920, 1080)

        assert result["success"] is False

    @patch("src.tools.computer_use._base_path")
    def test_successful_vision_click_caches_result(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.hash_screenshot.return_value = "abc123"
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screenshot.return_value = {"success": True}
        mock_desktop.click.return_value = {"success": True}

        with patch("src.tools.computer_use._find_element_with_api") as mock_api:
            mock_api.return_value = {
                "success": True,
                "coordinates": (500, 300),
                "cost_usd": 0.001,
            }
            with patch("src.tools.computer_use.Image", create=True):
                result = cu._click_via_vision("button", "app", mock_desktop, 1920, 1080)

        assert result["success"] is True
        assert result["method"] == "api_vision"
        assert result["cost_usd"] == 0.001
        mock_mem.store_element.assert_called_once()
        # Verify correct coordinates were clicked
        mock_desktop.click.assert_called_with(500, 300)

    @patch("src.tools.computer_use._base_path")
    def test_start_button_vision_fallback_on_low_x(self, mock_bp, tmp_path):
        """Vision returning low X for start button triggers known-position fallback."""
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.screenshot.return_value = {"success": True}
        mock_desktop.click.return_value = {"success": True}

        with patch("src.tools.computer_use._find_element_with_api") as mock_api:
            mock_api.return_value = {
                "success": True,
                "coordinates": (120, 1060),  # x=120 < 0.22*1920=422
                "cost_usd": 0.001,
            }
            with patch("src.tools.computer_use.Image", create=True):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("START_BUTTON_X", None)
                    result = cu._click_via_vision(
                        "Windows Start button", "desktop", mock_desktop, 1920, 1080,
                    )

        assert result["method"] == "known_position_fallback"
        assert result["cost_usd"] == 0.0


# ── _start_button_fallback() tests ─────────────────────────────────


class TestStartButtonFallback:
    """Tests for _start_button_fallback()."""

    @patch("src.tools.computer_use._base_path")
    def test_default_position(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_desktop = MagicMock()
        mock_desktop.click.return_value = {"success": True}

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("START_BUTTON_X", None)
            result = cu._start_button_fallback(1920, 1080, mock_desktop)

        assert result["method"] == "known_position_fallback"
        assert result["cost_usd"] == 0.0
        mock_desktop.click.assert_called_with(633, 1035)

    @patch("src.tools.computer_use._base_path")
    def test_env_override(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_desktop = MagicMock()
        mock_desktop.click.return_value = {"success": True}

        with patch.dict(os.environ, {"START_BUTTON_X": "0.5"}):
            result = cu._start_button_fallback(1920, 1080, mock_desktop)

        mock_desktop.click.assert_called_with(960, 1035)

    @patch("src.tools.computer_use._base_path")
    def test_invalid_env_returns_error(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_desktop = MagicMock()

        with patch.dict(os.environ, {"START_BUTTON_X": "not_a_number"}):
            result = cu._start_button_fallback(1920, 1080, mock_desktop)

        assert result["success"] is False


# ── type_in_element() tests ────────────────────────────────────────


class TestTypeInElement:
    """Tests for type_in_element() — click then type."""

    @patch("src.tools.computer_use._base_path")
    def test_successful_type(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        mock_mem = MagicMock()
        mock_mem.get_element.return_value = {
            "type": "coordinate",
            "location": {"x": 100, "y": 200},
        }
        cu._ui_memory = mock_mem

        mock_desktop = MagicMock()
        mock_desktop.click.return_value = {"success": True}
        mock_desktop.type_text.return_value = {"success": True}
        mock_desktop.screen_size = (1920, 1080)
        cu._desktop = mock_desktop

        result = cu.type_in_element("search box", "hello world")
        assert result["success"] is True
        assert result["click_method"] == "cached_coordinate"
        mock_desktop.type_text.assert_called_with("hello world")

    @patch("src.tools.computer_use._base_path")
    def test_click_failure_stops_typing(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()

        # Mock click_element to return failure directly
        cu.click_element = MagicMock(return_value={"success": False, "error": "Not found"})

        mock_desktop = MagicMock()
        mock_desktop.screen_size = (1920, 1080)
        cu._desktop = mock_desktop

        result = cu.type_in_element("missing element", "text")
        assert result["success"] is False
        mock_desktop.type_text.assert_not_called()


# ── get_stats() tests ──────────────────────────────────────────────


class TestGetStats:
    """Tests for get_stats()."""

    @patch("src.tools.computer_use._base_path")
    def test_no_browser(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        stats = cu.get_stats()
        assert stats["browser_running"] is False

    @patch("src.tools.computer_use._base_path")
    def test_browser_with_page(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        cu._browser = MagicMock()
        cu._browser.page = MagicMock()
        stats = cu.get_stats()
        assert stats["browser_running"] is True

    @patch("src.tools.computer_use._base_path")
    def test_browser_no_page(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        cu._browser = MagicMock()
        cu._browser.page = None
        stats = cu.get_stats()
        assert stats["browser_running"] is False


# ── Property tests ─────────────────────────────────────────────────


class TestProperties:
    """Tests for desktop and browser properties."""

    @patch("src.tools.computer_use._base_path")
    def test_desktop_property(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        mock_d = MagicMock()
        cu._desktop = mock_d
        assert cu.desktop is mock_d

    @patch("src.tools.computer_use._base_path")
    def test_browser_property(self, mock_bp, tmp_path):
        mock_bp.return_value = str(tmp_path)
        from src.tools.computer_use import ComputerUse
        cu = ComputerUse()
        mock_b = MagicMock()
        cu._browser = mock_b
        assert cu.browser is mock_b
