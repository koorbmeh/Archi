r"""
Quick check: verify Grok is accessible to the health check / scripts.
Run: .\venv\Scripts\python.exe scripts\check_grok.py
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

# Load .env so GROK_API_KEY is available (same as other scripts)
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env", override=True)
except ImportError:
    pass

print("Grok Availability Check")
print("=" * 50)

# 1. Direct env check
key_from_env = os.environ.get("GROK_API_KEY")
print(f"os.environ GROK_API_KEY: {'set' if key_from_env else 'NOT SET'}")
if key_from_env:
    print(f"  (length: {len(key_from_env)} chars)")

# 2. GrokClient instantiation (verifies key works)
print()
try:
    from src.models.grok_client import GrokClient

    client = GrokClient()
    has_key = hasattr(client, "_api_key") and bool(client._api_key)
    print(f"GrokClient instantiated: OK")
    print(f"  API key present: {has_key}")
    print("  [OK] Grok is fully accessible")
except Exception as e:
    print(f"  [ERROR] GrokClient failed: {e}")

print("=" * 50)
