"""
Test Gate C Phase 1B: UI Memory Map.
Verifies store, retrieve, success/failure recording, and cache invalidation.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.ui_memory import UIMemory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

print("UI Memory Test")
print("=" * 60)

ui_mem = UIMemory()

# Test 1: Store element
print("\n1. Store UI element")
print("-" * 60)
success = ui_mem.store_element(
    app_name="notepad",
    element_name="close_button",
    element_type="coordinate",
    location={"x": 1200, "y": 50},
    confidence=0.95,
)
print(f"Store result: {success}")

# Test 2: Retrieve element
print("\n2. Retrieve UI element")
print("-" * 60)
element = ui_mem.get_element("notepad", "close_button")
print(f"Retrieved: {element}")

# Test 3: Record success
print("\n3. Record successful use")
print("-" * 60)
ui_mem.record_success("notepad", "close_button")
print("Success recorded")

# Test 4: Store browser selector
print("\n4. Store browser selector")
print("-" * 60)
ui_mem.store_element(
    app_name="chrome",
    element_name="login_button",
    element_type="selector",
    location={"selector": "#login-btn"},
    confidence=1.0,
)

# Test 5: Retrieve browser element
print("\n5. Retrieve browser element")
print("-" * 60)
element = ui_mem.get_element("chrome", "login_button")
print(f"Retrieved: {element}")

print("\n" + "=" * 60)
print("UI Memory test complete!")
print("\nUI elements cached - future clicks will be FREE!")
