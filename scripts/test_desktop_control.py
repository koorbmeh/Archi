"""
Test Gate C desktop automation: mouse, keyboard, screenshot, open app.
WARNING: Controls your mouse/keyboard. Move mouse to top-left corner to abort (failsafe).
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.desktop_control import DesktopControl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

print("Desktop Control Test")
print("=" * 60)
print("\nWARNING: This will control your mouse/keyboard!")
print("Move mouse to top-left corner to abort (failsafe)")
print("\nPress Enter to continue or Ctrl+C to cancel...")
input()

desktop = DesktopControl()

# Test 1: Get mouse position
print("\nTest 1: Get mouse position")
print("-" * 60)
pos = desktop.get_mouse_position()
print(f"Current mouse position: {pos}")

# Test 2: Open Notepad
print("\nTest 2: Open Notepad")
print("-" * 60)
result = desktop.open_application("notepad")
print(f"Open result: {result}")
time.sleep(2)

# Test 3: Type text
print("\nTest 3: Type text in Notepad")
print("-" * 60)
result = desktop.type_text("Hello from Archi! Testing desktop control.")
print(f"Type result: {result}")
time.sleep(1)

# Test 4: Hotkey (Select All)
print("\nTest 4: Hotkey (Ctrl+A)")
print("-" * 60)
result = desktop.hotkey("ctrl", "a")
print(f"Hotkey result: {result}")
time.sleep(1)

# Test 5: Screenshot
print("\nTest 5: Take screenshot")
print("-" * 60)
screenshot_path = Path("data/test_screenshot.png")
screenshot_path.parent.mkdir(exist_ok=True)
result = desktop.screenshot(filepath=screenshot_path)
print(f"Screenshot result: {result}")

# Test 6 & 7: Close Notepad via pywinauto only (no global Alt+F4 — avoids hitting Cursor)
print("\nTest 6 & 7: Close Notepad and dismiss 'Don't Save' (pywinauto only)")
print("-" * 60)
try:
    from pywinauto import Application

    app = Application(backend="uia").connect(title_re=".*Notepad.*", timeout=3)
    notepad = app.top_window()
    notepad.close()  # Triggers "Save changes?" dialog
    time.sleep(0.7)
    # Dialog title is still "Notepad"; find and click "Don't Save" button
    dlg_app = Application(backend="uia").connect(title_re=".*Notepad.*", timeout=2)
    dlg = dlg_app.window(title_re=".*Notepad.*")
    try:
        btn = dlg.child_window(title_re="Don't save|Don't Save|Ne pas enregistrer", control_type="Button")
        btn.wait("ready", timeout=2)
        btn.click()
    except Exception:
        dlg.type_keys("{TAB}{ENTER}")  # keyboard to dialog (no global hotkey)
    print("Notepad closed (pywinauto).")
except Exception as e:
    print(f"pywinauto close failed: {e}. Close Notepad manually (no Alt+F4 used — safe for Cursor).")

print("\n" + "=" * 60)
print("Desktop control test complete!")
print(f"Screenshot saved to: {screenshot_path}")
