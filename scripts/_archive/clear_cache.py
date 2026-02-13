r"""
Clear the web chat router's query cache.

Run this while Archi is running to remove cached responses.
Then ask "Who are you?" again for a fresh response.

Usage:
  .\venv\Scripts\python.exe scripts\clear_cache.py

Or with curl (service must be running):
  curl http://127.0.0.1:5001/clear-cache
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

if __name__ == "__main__":
    try:
        import urllib.request
        req = urllib.request.urlopen("http://127.0.0.1:5001/clear-cache", timeout=5)
        data = req.read().decode()
        print("Cache cleared:", data)
    except Exception as e:
        print("Failed to clear cache (is Archi running at http://127.0.0.1:5001?):", e)
        sys.exit(1)
