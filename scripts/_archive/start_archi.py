r"""
Start Archi Service

Convenience script to start Archi as a background service.
Run from project root: .\venv\Scripts\python.exe scripts\start_archi.py
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env", override=True)
except ImportError:
    pass

if __name__ == "__main__":
    print("Starting Archi...")
    from src.service.archi_service import main

    main()
