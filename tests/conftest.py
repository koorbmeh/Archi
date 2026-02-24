"""Root conftest — ensures the project root is on sys.path for all tests.

This eliminates the need for per-file sys.path preambles and makes
`PYTHONPATH=.` unnecessary when running `pytest tests/`.
"""

import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
