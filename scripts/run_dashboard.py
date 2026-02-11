r"""
Run Archi Web Dashboard standalone (without full service).

Use when you want to test the dashboard UI without starting
the full Archi agent. APIs that need goal_manager/dream_cycle
will return "not initialized".

Run: .\venv\Scripts\python.exe scripts\run_dashboard.py

Then open http://127.0.0.1:5000
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env", override=True)
except ImportError:
    pass

if __name__ == "__main__":
    from src.web.dashboard import init_dashboard, run_dashboard

    # Dashboard works without service - health/costs will work,
    # goals/dream will return "not initialized"
    init_dashboard(None, None)

    print("Starting dashboard at http://127.0.0.1:5000")
    print("Press Ctrl+C to stop")
    run_dashboard(host="127.0.0.1", port=5000)
