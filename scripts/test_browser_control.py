"""
Test Gate C Phase 2: Browser automation with Playwright.
Opens DuckDuckGo, performs a search, captures screenshot.
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.browser_control import BrowserControl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

print("Browser Control Test")
print("=" * 60)
print("\nThis will open a browser and perform a search.")
print("Press Enter to continue...")
input()

browser = BrowserControl(headless=False)

# Test 1: Start browser
print("\nTest 1: Start browser")
print("-" * 60)
result = browser.start()
print(f"Start result: {result}")
time.sleep(1)

# Test 2: Navigate to DuckDuckGo
print("\nTest 2: Navigate to DuckDuckGo")
print("-" * 60)
result = browser.navigate("https://duckduckgo.com")
print(f"Navigate result: {result}")
time.sleep(1)

# Test 3: Fill search box (DuckDuckGo uses #searchbox_input or input[name="q"])
print("\nTest 3: Fill search box")
print("-" * 60)
# Try common selectors; DuckDuckGo may use input[name="q"] or #search_form_input
result = browser.fill('input[name="q"]', "Archi AI agent GitHub")
if not result.get("success"):
    result = browser.fill("#searchbox_input", "Archi AI agent GitHub")
print(f"Fill result: {result}")
time.sleep(1)

# Test 4: Press Enter to search
print("\nTest 4: Press Enter to search")
print("-" * 60)
result = browser.press_key("Enter")
print(f"Press key result: {result}")
time.sleep(2)

# Test 5: Get page info
print("\nTest 5: Get page info")
print("-" * 60)
url = browser.get_current_url()
title = browser.get_title()
print(f"URL: {url}")
print(f"Title: {title}")

# Test 6: Screenshot
print("\nTest 6: Take screenshot")
print("-" * 60)
screenshot_path = Path("data/browser_test.png")
screenshot_path.parent.mkdir(parents=True, exist_ok=True)
result = browser.screenshot(filepath=screenshot_path, full_page=True)
print(f"Screenshot result: {result}")

# Test 7: Stop browser
print("\nTest 7: Stop browser")
print("-" * 60)
time.sleep(2)
result = browser.stop()
print(f"Stop result: {result}")

print("\n" + "=" * 60)
print("Browser control test complete!")
print(f"Screenshot saved to: {screenshot_path}")
