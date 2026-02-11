"""
Goal queue: persistent goals in SQLite (data/memory.db).
Used during idle time for autonomous work; supports priority, status, stale detection.
"""

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _db_path() -> str:
    base = os.environ.get("ARCHI_ROOT", os.getcwd())
    return os.path.join(base, "data", "memory.db")


@dataclass
class Goal:
    """Single goal with priority and status."""

    id: int
    description: str
    priority: int  # 1=highest, 5=lowest
    status: str  # active, paused, complete, stale
    inferred_from: Optional[str]
    next_step: Optional[str]
    last_touched: datetime
    created_at: datetime
    constraints: Optional[str]  # JSON: budget, risk_level, etc.

    @classmethod
    def from_row(cls, row: tuple) -> "Goal":
        return cls(
            id=row[0],
            description=row[1],
            priority=row[2],
            status=row[3],
            inferred_from=row[4],
            next_step=row[5],
            last_touched=datetime.fromisoformat(row[6]) if row[6] else datetime.now(),
            created_at=datetime.fromisoformat(row[7]) if row[7] else datetime.now(),
            constraints=row[8],
        )


class GoalManager:
    """Persistent goal queue; same DB as memory (data/memory.db)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _db_path()
        self._init_db()
        logger.info("Goal manager initialized (db=%s)", self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    priority INTEGER DEFAULT 3,
                    status TEXT DEFAULT 'active',
                    inferred_from TEXT,
                    next_step TEXT,
                    last_touched TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    constraints TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_goals_status_priority "
                "ON goals(status, priority)"
            )

    def add_goal(
        self,
        description: str,
        priority: int = 3,
        inferred_from: Optional[str] = None,
        next_step: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Add a new goal. Returns goal id."""
        import json
        now = datetime.utcnow().isoformat()
        constraints_json = json.dumps(constraints) if constraints else None
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO goals (description, priority, inferred_from, next_step, last_touched, constraints)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (description, priority, inferred_from, next_step, now, constraints_json),
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_next_goal(self) -> Optional[Goal]:
        """Get highest-priority active goal for idle work."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT id, description, priority, status, inferred_from, next_step,
                       last_touched, created_at, constraints
                FROM goals
                WHERE status = 'active'
                ORDER BY priority ASC, last_touched ASC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return Goal.from_row(tuple(row))

    def touch_goal(self, goal_id: int) -> None:
        """Update last_touched for a goal."""
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE goals SET last_touched = ? WHERE id = ?",
                (now, goal_id),
            )
            conn.commit()

    def update_status(self, goal_id: int, status: str) -> None:
        """Set goal status: active, paused, complete, stale."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE goals SET status = ?, last_touched = ? WHERE id = ?",
                (status, datetime.utcnow().isoformat(), goal_id),
            )
            conn.commit()

    def mark_stale(self, days: int = 30) -> int:
        """Mark goals not touched in N days as stale. Returns count updated."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE goals SET status = 'stale' WHERE status = 'active' AND last_touched < ?",
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount

    def list_goals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Goal]:
        """List goals optionally filtered by status."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    """
                    SELECT id, description, priority, status, inferred_from, next_step,
                           last_touched, created_at, constraints
                    FROM goals WHERE status = ? ORDER BY priority, last_touched LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, description, priority, status, inferred_from, next_step,
                           last_touched, created_at, constraints
                    FROM goals ORDER BY priority, last_touched LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [Goal.from_row(tuple(r)) for r in rows]
