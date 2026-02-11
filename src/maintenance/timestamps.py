"""
Persistent timestamps in metadata table (data/memory.db).
Used for startup recovery: last_dream_cycle, etc.
"""

import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _db_path() -> str:
    base = os.environ.get("ARCHI_ROOT", os.getcwd())
    return os.path.join(base, "data", "memory.db")


def _ensure_metadata() -> None:
    """Ensure metadata table exists (MemoryManager creates it; this is a safety net)."""
    os.makedirs(os.path.dirname(_db_path()), exist_ok=True)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def load_timestamp(key: str) -> Optional[datetime]:
    """Load timestamp from metadata table. Returns None if missing or invalid."""
    _ensure_metadata()
    with sqlite3.connect(_db_path()) as conn:
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
    _ensure_metadata()
    ts = (value or datetime.utcnow()).isoformat()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO metadata (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, ts),
        )
        conn.commit()
