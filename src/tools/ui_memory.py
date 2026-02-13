"""
UI Memory Map.
SQLite cache for UI element locations to minimize vision calls.
Converts vision from "per-action" to "per-discovery" (expected 90-95% reduction).
"""

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class UIMemory:
    """
    Cache UI element locations to avoid repeated vision analysis.

    Stores:
    - Element coordinates (for desktop)
    - CSS selectors (for browser)
    - Last successful access time
    - Screenshot hash (for invalidation)
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = Path("data/ui_memory.db")
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Return a persistent connection (reused across calls)."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_db(self) -> None:
        """Create UI memory table if not exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ui_elements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_name TEXT NOT NULL,
                element_name TEXT NOT NULL,
                element_type TEXT NOT NULL,
                location TEXT NOT NULL,
                screenshot_hash TEXT,
                confidence REAL DEFAULT 1.0,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(app_name, element_name)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_app_element
            ON ui_elements(app_name, element_name)
        """)
        conn.commit()
        logger.info("UI memory initialized at %s", self.db_path)

    def store_element(
        self,
        app_name: str,
        element_name: str,
        element_type: str,
        location: Dict[str, Any],
        screenshot_hash: Optional[str] = None,
        confidence: float = 1.0,
    ) -> bool:
        """
        Store or update UI element location.

        Args:
            app_name: Application name (e.g., "notepad", "chrome")
            element_name: Element identifier (e.g., "login_button", "submit")
            element_type: "coordinate", "selector", or "text"
            location: Dict with coords {"x": 100, "y": 200} or {"selector": ".btn"}
            screenshot_hash: Hash of current screen state
            confidence: How confident we are (0.0-1.0)

        Returns:
            True if stored successfully
        """
        try:
            conn = self._get_conn()
            location_json = json.dumps(location)
            conn.execute("""
                INSERT INTO ui_elements (
                    app_name, element_name, element_type, location,
                    screenshot_hash, confidence, success_count, last_used
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(app_name, element_name) DO UPDATE SET
                    element_type = excluded.element_type,
                    location = excluded.location,
                    screenshot_hash = excluded.screenshot_hash,
                    confidence = excluded.confidence,
                    last_used = CURRENT_TIMESTAMP
            """, (app_name, element_name, element_type, location_json,
                  screenshot_hash, confidence))
            conn.commit()
            logger.info("Stored UI element: %s/%s", app_name, element_name)
            return True
        except Exception as e:
            logger.error("Failed to store UI element: %s", e)
            return False

    def get_element(
        self,
        app_name: str,
        element_name: str,
        screenshot_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached UI element location.

        Args:
            app_name: Application name
            element_name: Element identifier
            screenshot_hash: Current screen hash (for validation)

        Returns:
            Dict with element info, or None if not found/invalid
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT element_type, location, screenshot_hash,
                       confidence, success_count, failure_count
                FROM ui_elements
                WHERE app_name = ? AND element_name = ?
            """, (app_name, element_name))
            row = cursor.fetchone()
            if not row:
                return None
            element_type, location_json, stored_hash, confidence, successes, failures = row
            if screenshot_hash and stored_hash and screenshot_hash != stored_hash:
                logger.debug("Screen changed for %s/%s, cache invalid", app_name, element_name)
                return None
            if failures > successes * 2:
                logger.debug("Low confidence for %s/%s, cache invalid", app_name, element_name)
                return None
            location = json.loads(location_json)
            logger.info("Cache HIT: %s/%s", app_name, element_name)
            return {
                "type": element_type,
                "location": location,
                "confidence": confidence,
            }
        except Exception as e:
            logger.error("Failed to retrieve UI element: %s", e)
            return None

    def record_success(self, app_name: str, element_name: str) -> None:
        """Record successful use of cached element."""
        try:
            conn = self._get_conn()
            conn.execute("""
                UPDATE ui_elements
                SET success_count = success_count + 1,
                    last_used = CURRENT_TIMESTAMP
                WHERE app_name = ? AND element_name = ?
            """, (app_name, element_name))
            conn.commit()
        except Exception as e:
            logger.error("Failed to record success: %s", e)

    def record_failure(self, app_name: str, element_name: str) -> None:
        """Record failed use of cached element."""
        try:
            conn = self._get_conn()
            conn.execute("""
                UPDATE ui_elements
                SET failure_count = failure_count + 1
                WHERE app_name = ? AND element_name = ?
            """, (app_name, element_name))
            conn.commit()
        except Exception as e:
            logger.error("Failed to record failure: %s", e)

    def hash_screenshot(self, screenshot_path: Path) -> str:
        """Generate hash of screenshot for change detection."""
        try:
            with open(screenshot_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception as e:
            logger.error("Failed to hash screenshot: %s", e)
            return ""

    def clear_stale(self, days: int = 30) -> int:
        """Remove elements not used in N days. Returns number deleted."""
        try:
            conn = self._get_conn()
            cursor = conn.execute("""
                DELETE FROM ui_elements
                WHERE last_used < datetime('now', '-' || ? || ' days')
            """, (days,))
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info("Cleared %s stale UI elements", deleted)
            return deleted
        except Exception as e:
            logger.error("Failed to clear stale elements: %s", e)
            return 0
