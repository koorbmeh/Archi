r"""
Run Archi Web Chat standalone (without full service).

Use when you want to test the web chat without starting
the full Archi agent. Model router loads on first message.

Run: .\venv\Scripts\python.exe scripts\run_web_chat.py

Then open http://127.0.0.1:5001/chat
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
    from src.interfaces.web_chat import init_web_chat, run_web_chat

    init_web_chat(None)

    print("=" * 60)
    print("STANDALONE Web Chat - http://127.0.0.1:5001/chat")
    print("For full Archi (local model, correct identity), use:")
    print("  python scripts/start_archi.py")
    print("=" * 60)
    run_web_chat(host="127.0.0.1", port=5001)
