r"""
Start Archi CLI Chat

Interactive terminal chat with Archi.
Run: .\venv\Scripts\python.exe scripts\chat.py

Commands: /help, /goal, /goals, /status, /cost, /exit
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
    from src.interfaces.cli_chat import main

    main()
