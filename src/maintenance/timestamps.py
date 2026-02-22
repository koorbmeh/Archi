"""
Persistent timestamps in metadata table (data/memory.db).
Used for startup recovery: last_dream_cycle, etc.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional
from src.utils.paths import db_path as _db_path

logger = logging.getLogger(__name__)

# Module-level persistent connection (avoids open/close per call)
_conn: Optional[sqlite3.Connection] = None
_initialized = False
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """Return a persistent WAL-mode connection to memory.db."""
    global _conn, _initialized
    if _conn is None:
        os.makedirs(os.path.dirname(_db_path()), exist_ok=True)
        _conn = sqlite3.connect(
            _db_path(), check_same_thread=False, timeout=15.0,
        )
        _conn.execute("PRAGMA journal_mode=WAL")
    if not _initialized:
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _conn.commit()
        _initialized = True
    return _conn


def load_timestamp(key: str) -> Optional[datetime]:
    """Load timestamp from metadata table. Returns None if missing or invalid."""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except (ValueError, TypeError):
        return None


def save_timestamp(key: str, value: Optional[datetime] = None) -> None:
    """Save timestamp to metadata table. Uses now() if value is None."""
    ts = (value or datetime.now(timezone.utc)).isoformat()
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO metadata (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, ts),
        )
        conn.commit()
