"""
Unit tests for desktop_control.py.

Covers initialization, click, type_text, press_key, hotkey, move_mouse, screenshot,
get_mouse_position, open_application, scroll, and cleanup_processes methods.
Session 150.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure a mock pyautogui is in sys.modules before desktop_control is imported
if "pyautogui" not in sys.modules:
    sys.modules["pyautogui"] = MagicMock()

from src.tools import desktop_control as dc_mod
from src.tools.desktop_control import DesktopControl


@pytest.fixture(autouse=True)
def mock_pyautogui():
    """Patch pyautogui on the desktop_control module for every test."""
    mock_pag = MagicMock()
    mock_pag.size.return_value = (1920, 1080)
    mock_pag.position.return_value = (960, 540)

    original = dc_mod.pyautogui
    dc_mod.pyautogui = mock_pag
    yield mock_pag
    dc_mod.pyautogui = original


# ── TestDesktopControlInit ──────────────────────────────────────


class TestDesktopControlInit:
    """Tests for DesktopControl initialization."""

    def test_init_success_with_pyautogui(self, mock_pyautogui):
        desktop = DesktopControl()
        assert desktop.screen_size == (1920, 1080)
        assert desktop._spawned_processes == []

    def test_init_failure_without_pyautogui(self):
        original = dc_mod.pyautogui
        dc_mod.pyautogui = None
        try:
            with pytest.raises(ImportError, match="pyautogui is required"):
                DesktopControl()
        finally:
            dc_mod.pyautogui = original


# ── TestClick ───────────────────────────────────────────────────────


class TestClick:
    """Tests for the click method."""

    def test_click_success_default_params(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.click(100, 200)
        assert result["success"] is True
        assert result["action"] == "click"
        assert result["location"] == (100, 200)
        assert result["button"] == "left"
        mock_pyautogui.click.assert_called_once_with(100, 200, clicks=1, interval=0.1, button="left")

    def test_click_success_custom_params(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.click(300, 400, button="right", clicks=2, interval=0.2)
        assert result["success"] is True
        assert result["button"] == "right"
        mock_pyautogui.click.assert_called_once_with(300, 400, clicks=2, interval=0.2, button="right")

    def test_click_exception(self, mock_pyautogui):
        mock_pyautogui.click.side_effect = Exception("Click failed")
        desktop = DesktopControl()
        result = desktop.click(100, 200)
        assert result["success"] is False
        assert "Click failed" in result["error"]


# ── TestTypeText ────────────────────────────────────────────────────


class TestTypeText:
    """Tests for the type_text method."""

    def test_type_text_success(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.type_text("Hello World")
        assert result["success"] is True
        assert result["text_length"] == 11
        mock_pyautogui.write.assert_called_once_with("Hello World", interval=0.05)

    def test_type_text_custom_interval(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.type_text("Test", interval=0.1)
        assert result["text_length"] == 4
        mock_pyautogui.write.assert_called_once_with("Test", interval=0.1)

    def test_type_text_empty_string(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.type_text("")
        assert result["success"] is True
        assert result["text_length"] == 0

    def test_type_text_exception(self, mock_pyautogui):
        mock_pyautogui.write.side_effect = Exception("Write failed")
        desktop = DesktopControl()
        result = desktop.type_text("Hello")
        assert result["success"] is False
        assert "Write failed" in result["error"]


# ── TestPressKey ────────────────────────────────────────────────────


class TestPressKey:
    """Tests for the press_key method."""

    def test_press_key_success_default(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.press_key("enter")
        assert result["success"] is True
        assert result["key"] == "enter"
        mock_pyautogui.press.assert_called_once_with("enter", presses=1)

    def test_press_key_multiple_presses(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.press_key("backspace", presses=5)
        assert result["key"] == "backspace"
        mock_pyautogui.press.assert_called_once_with("backspace", presses=5)

    def test_press_key_exception(self, mock_pyautogui):
        mock_pyautogui.press.side_effect = Exception("Press failed")
        desktop = DesktopControl()
        result = desktop.press_key("delete")
        assert result["success"] is False


# ── TestHotkey ──────────────────────────────────────────────────────


class TestHotkey:
    """Tests for the hotkey method."""

    def test_hotkey_success_two_keys(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.hotkey("ctrl", "c")
        assert result["success"] is True
        assert result["keys"] == ["ctrl", "c"]
        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "c")

    def test_hotkey_success_three_keys(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.hotkey("ctrl", "shift", "s")
        assert result["keys"] == ["ctrl", "shift", "s"]

    def test_hotkey_exception(self, mock_pyautogui):
        mock_pyautogui.hotkey.side_effect = Exception("Hotkey failed")
        desktop = DesktopControl()
        result = desktop.hotkey("alt", "tab")
        assert result["success"] is False


# ── TestMoveMouse ───────────────────────────────────────────────────


class TestMoveMouse:
    """Tests for the move_mouse method."""

    def test_move_mouse_default_duration(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.move_mouse(500, 600)
        assert result["success"] is True
        assert result["location"] == (500, 600)
        mock_pyautogui.moveTo.assert_called_once_with(500, 600, duration=0.5)

    def test_move_mouse_custom_duration(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.move_mouse(700, 800, duration=1.0)
        assert result["location"] == (700, 800)
        mock_pyautogui.moveTo.assert_called_once_with(700, 800, duration=1.0)

    def test_move_mouse_exception(self, mock_pyautogui):
        mock_pyautogui.moveTo.side_effect = Exception("Move failed")
        desktop = DesktopControl()
        result = desktop.move_mouse(100, 100)
        assert result["success"] is False


# ── TestScreenshot ──────────────────────────────────────────────────


class TestScreenshot:
    """Tests for the screenshot method."""

    def test_screenshot_no_params(self, mock_pyautogui):
        mock_img = MagicMock()
        mock_img.size = (1920, 1080)
        mock_pyautogui.screenshot.return_value = mock_img

        desktop = DesktopControl()
        result = desktop.screenshot()
        assert result["success"] is True
        assert result["image"] == mock_img
        assert result["size"] == (1920, 1080)

    def test_screenshot_with_region(self, mock_pyautogui):
        mock_img = MagicMock()
        mock_img.size = (640, 480)
        mock_pyautogui.screenshot.return_value = mock_img

        desktop = DesktopControl()
        result = desktop.screenshot(region=(0, 0, 640, 480))
        assert result["success"] is True
        mock_pyautogui.screenshot.assert_called_once_with(region=(0, 0, 640, 480))

    def test_screenshot_with_filepath(self, mock_pyautogui, tmp_path):
        mock_img = MagicMock()
        mock_img.size = (1920, 1080)
        mock_pyautogui.screenshot.return_value = mock_img

        desktop = DesktopControl()
        filepath = str(tmp_path / "screenshot.png")
        result = desktop.screenshot(filepath=filepath)
        assert result["success"] is True
        assert result["filepath"] == filepath
        assert "image" not in result
        mock_img.save.assert_called_once()

    def test_screenshot_with_region_and_filepath(self, mock_pyautogui, tmp_path):
        mock_img = MagicMock()
        mock_img.size = (640, 480)
        mock_pyautogui.screenshot.return_value = mock_img

        desktop = DesktopControl()
        filepath = str(tmp_path / "region.png")
        result = desktop.screenshot(region=(100, 100, 640, 480), filepath=filepath)
        assert result["success"] is True
        assert result["filepath"] == filepath

    def test_screenshot_exception(self, mock_pyautogui):
        mock_pyautogui.screenshot.side_effect = Exception("Screenshot failed")
        desktop = DesktopControl()
        result = desktop.screenshot()
        assert result["success"] is False


# ── TestGetMousePosition ─────────────────────────────────────────────


class TestGetMousePosition:
    """Tests for the get_mouse_position method."""

    def test_get_mouse_position(self, mock_pyautogui):
        mock_pyautogui.position.return_value = (960, 540)
        desktop = DesktopControl()
        assert desktop.get_mouse_position() == (960, 540)

    def test_get_mouse_position_different_coords(self, mock_pyautogui):
        mock_pyautogui.position.return_value = (100, 200)
        desktop = DesktopControl()
        assert desktop.get_mouse_position() == (100, 200)


# ── TestOpenApplication ──────────────────────────────────────────────


class TestOpenApplication:
    """Tests for the open_application method."""

    def test_open_safe_app(self, mock_pyautogui):
        with patch("subprocess.Popen") as mock_popen, patch("time.sleep"):
            desktop = DesktopControl()
            result = desktop.open_application("notepad")
            assert result["success"] is True
            assert result["app"] == "notepad"
            mock_popen.assert_called_once_with(["notepad"])
            assert len(desktop._spawned_processes) == 1

    def test_open_all_safe_apps(self, mock_pyautogui):
        safe_apps = {"notepad", "calc", "mspaint", "explorer", "cmd", "powershell"}
        with patch("subprocess.Popen"), patch("time.sleep"):
            for app in safe_apps:
                desktop = DesktopControl()
                result = desktop.open_application(app)
                assert result["success"] is True

    def test_open_unsafe_app_xdg_open(self, mock_pyautogui):
        with patch("subprocess.Popen") as mock_popen, patch("time.sleep"):
            desktop = DesktopControl()
            # On Linux, os doesn't have startfile, so xdg-open is used
            result = desktop.open_application("/path/to/app")
            assert result["success"] is True

    def test_open_unsafe_app_startfile(self, mock_pyautogui):
        import os
        mock_startfile = MagicMock()
        setattr(os, "startfile", mock_startfile)
        try:
            with patch("time.sleep"):
                desktop = DesktopControl()
                result = desktop.open_application("C:\\app.exe")
                assert result["success"] is True
                mock_startfile.assert_called_once_with("C:\\app.exe")
        finally:
            if hasattr(os, "startfile"):
                delattr(os, "startfile")

    def test_open_application_exception(self, mock_pyautogui):
        with patch("subprocess.Popen", side_effect=Exception("Popen failed")), patch("time.sleep"):
            desktop = DesktopControl()
            result = desktop.open_application("notepad")
            assert result["success"] is False

    def test_open_application_sleeps(self, mock_pyautogui):
        with patch("subprocess.Popen"), patch("time.sleep") as mock_sleep:
            desktop = DesktopControl()
            desktop.open_application("calc")
            mock_sleep.assert_called_once_with(1)


# ── TestScroll ──────────────────────────────────────────────────────


class TestScroll:
    """Tests for the scroll method."""

    def test_scroll_positive(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.scroll(5)
        assert result["success"] is True
        assert result["clicks"] == 5
        mock_pyautogui.scroll.assert_called_once_with(5)

    def test_scroll_negative(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.scroll(-3)
        assert result["clicks"] == -3

    def test_scroll_zero(self, mock_pyautogui):
        desktop = DesktopControl()
        result = desktop.scroll(0)
        assert result["success"] is True

    def test_scroll_exception(self, mock_pyautogui):
        mock_pyautogui.scroll.side_effect = Exception("Scroll failed")
        desktop = DesktopControl()
        result = desktop.scroll(5)
        assert result["success"] is False


# ── TestCleanupProcesses ────────────────────────────────────────────


class TestCleanupProcesses:
    """Tests for the cleanup_processes method."""

    def test_cleanup_all_finished(self, mock_pyautogui):
        desktop = DesktopControl()
        p1, p2 = MagicMock(), MagicMock()
        p1.poll.return_value = 0
        p2.poll.return_value = 1
        desktop._spawned_processes = [p1, p2]
        desktop.cleanup_processes()
        assert len(desktop._spawned_processes) == 0

    def test_cleanup_some_alive(self, mock_pyautogui):
        desktop = DesktopControl()
        p1, p2, p3 = MagicMock(), MagicMock(), MagicMock()
        p1.poll.return_value = 0
        p2.poll.return_value = None
        p3.poll.return_value = 1
        desktop._spawned_processes = [p1, p2, p3]
        desktop.cleanup_processes()
        assert len(desktop._spawned_processes) == 1
        assert desktop._spawned_processes[0] == p2

    def test_cleanup_all_alive(self, mock_pyautogui):
        desktop = DesktopControl()
        p1, p2 = MagicMock(), MagicMock()
        p1.poll.return_value = None
        p2.poll.return_value = None
        desktop._spawned_processes = [p1, p2]
        desktop.cleanup_processes()
        assert len(desktop._spawned_processes) == 2

    def test_cleanup_empty(self, mock_pyautogui):
        desktop = DesktopControl()
        desktop._spawned_processes = []
        desktop.cleanup_processes()
        assert len(desktop._spawned_processes) == 0
