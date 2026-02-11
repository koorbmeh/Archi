"""
Memory manager: short-term (deque), working (SQLite), long-term (LanceDB VectorStore).
Store actions and retrieve relevant context for the agent.
"""

import json
import logging
import os
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.memory.vector_store import VectorStore

logger = logging.getLogger(__name__)

SHORT_TERM_MAXLEN = 50


def _db_path() -> str:
    base = os.environ.get("ARCHI_ROOT", os.getcwd())
    return os.path.join(base, "data", "memory.db")


class MemoryManager:
    """Short-term action buffer, working memory (SQLite), long-term semantic (LanceDB)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.short_term: deque = deque(maxlen=SHORT_TERM_MAXLEN)
        self.vector_store = VectorStore()
        self.db_path = db_path or _db_path()
        self._init_db()
        logger.info("Memory manager initialized")

    def _init_db(self) -> None:
        import sqlite3
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS working_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def store_action(
        self,
        action_type: str,
        parameters: Dict[str, Any],
        result: Any,
        confidence: float = 0.0,
    ) -> None:
        """Append an action to short-term memory."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action_type": action_type,
            "parameters": parameters,
            "result": result,
            "confidence": confidence,
        }
        self.short_term.append(entry)
        logger.debug("Stored action in short-term memory: %s", action_type)

    def get_recent_actions(self, n: int = 10) -> List[Dict[str, Any]]:
        """Last n actions from short-term buffer."""
        return list(self.short_term)[-n:]

    def store_long_term(
        self,
        text: str,
        memory_type: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store in long-term semantic memory. Returns memory id."""
        meta = dict(metadata or {})
        meta["type"] = memory_type
        memory_id = self.vector_store.add_memory(text, meta)
        logger.info("Stored in long-term memory: %s...", text[:50])
        return memory_id

    def retrieve_relevant(
        self,
        query: str,
        n_results: int = 5,
    ) -> Dict[str, Any]:
        """Retrieve relevant semantic memories and recent actions."""
        semantic = self.vector_store.search(query, n_results=n_results)
        recent = self.get_recent_actions(5)
        return {"semantic": semantic, "recent_actions": recent}

    def get_stats(self) -> Dict[str, int]:
        """Counts for short-term and long-term."""
        return {
            "short_term_count": len(self.short_term),
            "long_term_count": self.vector_store.get_memory_count(),
        }
