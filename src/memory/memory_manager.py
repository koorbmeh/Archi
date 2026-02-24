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

from src.utils.paths import db_path as _db_path

logger = logging.getLogger(__name__)

SHORT_TERM_MAXLEN = 50

# Memory dedup thresholds (cosine distance from LanceDB, 0 = identical, 2 = opposite)
_DEDUP_DISTANCE = 0.15   # Below this → near-duplicate, skip
_UPDATE_DISTANCE = 0.35  # Below this → same topic, update existing


def _try_load_vector_store():
    """Lazy-load VectorStore; returns None if ML deps are unavailable."""
    try:
        from src.memory.vector_store import VectorStore
        return VectorStore()
    except Exception as e:
        logger.warning("Vector store unavailable (long-term memory disabled): %s", e)
        return None


class MemoryManager:
    """Short-term action buffer, working memory (SQLite), long-term semantic (LanceDB)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.short_term: deque = deque(maxlen=SHORT_TERM_MAXLEN)
        self.vector_store = _try_load_vector_store()
        self.db_path = db_path or _db_path()
        self._init_db()
        logger.info("Memory manager initialized (vector store: %s)",
                     "active" if self.vector_store else "disabled")

    def _init_db(self) -> None:
        import sqlite3
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
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
        """Append an action to short-term memory and persist to working memory (SQLite)."""
        import sqlite3
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action_type": action_type,
            "parameters": parameters,
            "result": result,
            "confidence": confidence,
        }
        self.short_term.append(entry)
        # Persist to SQLite working memory
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute(
                    "INSERT INTO working_memory (timestamp, memory_type, content, metadata) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        entry["timestamp"],
                        action_type,
                        json.dumps({"parameters": parameters, "result": result}),
                        json.dumps({"confidence": confidence}),
                    ),
                )
        except Exception as e:
            logger.debug("Working memory INSERT failed: %s", e)
        logger.debug("Stored action in short-term + working memory: %s", action_type)

    def get_recent_actions(self, n: int = 10) -> List[Dict[str, Any]]:
        """Last n actions from short-term buffer."""
        return list(self.short_term)[-n:]

    def store_long_term(
        self,
        text: str,
        memory_type: str = "general",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store in long-term semantic memory with dedup/update logic.

        Before adding, searches for similar existing memories:
        - distance < _DEDUP_DISTANCE → near-duplicate, skip (return existing ID)
        - distance < _UPDATE_DISTANCE → same topic, update existing with new text
        - otherwise → genuinely new, add normally

        Returns memory id (new or existing).
        """
        if not self.vector_store:
            logger.debug("Vector store disabled, skipping long-term store")
            return ""

        meta = dict(metadata or {})
        meta["type"] = memory_type

        # Check for similar existing memories before adding
        try:
            similar = self.vector_store.find_similar(
                text, n_results=3, max_distance=_UPDATE_DISTANCE,
            )
            if similar:
                closest = similar[0]
                dist = closest["distance"]
                existing_id = closest["id"]

                if dist <= _DEDUP_DISTANCE:
                    logger.info(
                        "Memory dedup: skipping near-duplicate (dist=%.3f): %s...",
                        dist, text[:50],
                    )
                    return existing_id

                # Same topic but updated info → replace old memory
                logger.info(
                    "Memory update: replacing similar memory (dist=%.3f): %s...",
                    dist, text[:50],
                )
                self.vector_store.update_memory(existing_id, text, meta)
                return existing_id
        except Exception as e:
            logger.debug("Memory dedup check failed, adding as new: %s", e)

        memory_id = self.vector_store.add_memory(text, meta)
        logger.info("Stored in long-term memory: %s...", text[:50])
        return memory_id

    def store_conversation(
        self,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store a conversation summary in long-term memory.

        Used by the heartbeat to archive chat exchanges that would otherwise
        be lost when chat_history.json trims to its 20-message cap.

        Args:
            summary: A 2-3 sentence summary of the conversation exchange.
            metadata: Optional dict (e.g. message count, timespan).

        Returns:
            Memory ID, or empty string if vector store is unavailable.
        """
        meta = dict(metadata or {})
        meta["type"] = "conversation"
        return self.store_long_term(summary, memory_type="conversation", metadata=meta)

    def get_conversation_context(
        self,
        query: str,
        n_results: int = 3,
    ) -> List[str]:
        """Retrieve relevant past conversation summaries for a query.

        Returns a list of summary strings, most relevant first.
        Filters to conversation-type memories only.
        """
        if not self.vector_store:
            return []
        results = self.vector_store.search(
            query, n_results=n_results,
            filter_metadata={"type": "conversation"},
        )
        # Filter out low-relevance results (cosine distance > 0.8)
        return [r["text"] for r in results if r.get("distance", 1.0) < 0.8]

    def retrieve_relevant(
        self,
        query: str,
        n_results: int = 5,
    ) -> Dict[str, Any]:
        """Retrieve relevant semantic memories and recent actions."""
        if self.vector_store:
            semantic = self.vector_store.search(query, n_results=n_results)
        else:
            semantic = []
        recent = self.get_recent_actions(5)
        return {"semantic": semantic, "recent_actions": recent}

    def get_stats(self) -> Dict[str, int]:
        """Counts for short-term and long-term."""
        return {
            "short_term_count": len(self.short_term),
            "long_term_count": self.vector_store.get_memory_count() if self.vector_store else 0,
        }
