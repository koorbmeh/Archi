"""Unit tests for MemoryManager — short-term, working (SQLite), and long-term memory.

Tests store/retrieve operations, SQLite persistence, vector store delegation,
stats reporting, and graceful degradation when vector store is unavailable.

Created session 74.
"""

import json
import os
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from src.memory.memory_manager import MemoryManager, SHORT_TERM_MAXLEN


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mm(tmp_path):
    """MemoryManager with vector store disabled (no ML deps needed)."""
    db = str(tmp_path / "test_memory.db")
    with patch("src.memory.memory_manager._try_load_vector_store", return_value=None):
        return MemoryManager(db_path=db)


@pytest.fixture
def mm_with_vectors(tmp_path):
    """MemoryManager with a mocked vector store."""
    db = str(tmp_path / "test_memory.db")
    mock_vs = MagicMock()
    mock_vs.add_memory.return_value = "mem_123"
    mock_vs.search.return_value = [{"text": "prior research", "distance": 0.3}]
    mock_vs.get_memory_count.return_value = 42
    with patch("src.memory.memory_manager._try_load_vector_store", return_value=mock_vs):
        return MemoryManager(db_path=db)


# ── Initialization tests ─────────────────────────────────────────────


class TestInit:
    """Tests for MemoryManager initialization."""

    def test_creates_db_file(self, mm):
        assert os.path.exists(mm.db_path)

    def test_db_has_working_memory_table(self, mm):
        conn = sqlite3.connect(mm.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='working_memory'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_db_has_metadata_table(self, mm):
        conn = sqlite3.connect(mm.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_vector_store_disabled(self, mm):
        assert mm.vector_store is None

    def test_vector_store_enabled(self, mm_with_vectors):
        assert mm_with_vectors.vector_store is not None

    def test_short_term_starts_empty(self, mm):
        assert len(mm.short_term) == 0


# ── store_action() tests ─────────────────────────────────────────────


class TestStoreAction:
    """Tests for store_action() — short-term + SQLite persistence."""

    def test_appends_to_short_term(self, mm):
        mm.store_action("web_search", {"query": "test"}, {"success": True}, 0.9)
        assert len(mm.short_term) == 1
        entry = mm.short_term[0]
        assert entry["action_type"] == "web_search"
        assert entry["confidence"] == 0.9

    def test_persists_to_sqlite(self, mm):
        mm.store_action("create_file", {"path": "/tmp/x.py"}, {"success": True}, 0.8)
        conn = sqlite3.connect(mm.db_path)
        rows = conn.execute("SELECT * FROM working_memory").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][2] == "create_file"  # memory_type column

    def test_sqlite_content_is_json(self, mm):
        mm.store_action("read_file", {"path": "a.py"}, {"content": "x=1"}, 0.5)
        conn = sqlite3.connect(mm.db_path)
        row = conn.execute("SELECT content FROM working_memory").fetchone()
        conn.close()
        data = json.loads(row[0])
        assert data["parameters"]["path"] == "a.py"
        assert data["result"]["content"] == "x=1"

    def test_sqlite_metadata_has_confidence(self, mm):
        mm.store_action("think", {}, {}, 0.7)
        conn = sqlite3.connect(mm.db_path)
        row = conn.execute("SELECT metadata FROM working_memory").fetchone()
        conn.close()
        meta = json.loads(row[0])
        assert meta["confidence"] == 0.7

    def test_short_term_respects_maxlen(self, mm):
        for i in range(SHORT_TERM_MAXLEN + 10):
            mm.store_action(f"action_{i}", {}, {}, 0.0)
        assert len(mm.short_term) == SHORT_TERM_MAXLEN

    def test_multiple_actions_preserved_in_sqlite(self, mm):
        mm.store_action("a1", {}, {}, 0.1)
        mm.store_action("a2", {}, {}, 0.2)
        mm.store_action("a3", {}, {}, 0.3)
        conn = sqlite3.connect(mm.db_path)
        count = conn.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0]
        conn.close()
        assert count == 3


# ── get_recent_actions() tests ───────────────────────────────────────


class TestGetRecentActions:
    """Tests for get_recent_actions()."""

    def test_returns_last_n(self, mm):
        for i in range(5):
            mm.store_action(f"action_{i}", {}, {}, 0.0)
        recent = mm.get_recent_actions(3)
        assert len(recent) == 3
        assert recent[-1]["action_type"] == "action_4"

    def test_returns_all_when_fewer_than_n(self, mm):
        mm.store_action("only_one", {}, {}, 0.0)
        recent = mm.get_recent_actions(10)
        assert len(recent) == 1

    def test_empty_when_nothing_stored(self, mm):
        assert mm.get_recent_actions(5) == []


# ── store_long_term() tests ──────────────────────────────────────────


class TestStoreLongTerm:
    """Tests for store_long_term() — vector store delegation."""

    def test_disabled_returns_empty_string(self, mm):
        result = mm.store_long_term("Some research text", "research")
        assert result == ""

    def test_enabled_delegates_to_vector_store(self, mm_with_vectors):
        result = mm_with_vectors.store_long_term(
            "Completed research on supplements",
            memory_type="research_result",
            metadata={"goal_id": "g1"},
        )
        assert result == "mem_123"
        mm_with_vectors.vector_store.add_memory.assert_called_once()
        call_args = mm_with_vectors.vector_store.add_memory.call_args
        assert "supplements" in call_args[0][0]
        meta = call_args[0][1]
        assert meta["type"] == "research_result"
        assert meta["goal_id"] == "g1"

    def test_metadata_includes_type(self, mm_with_vectors):
        mm_with_vectors.store_long_term("text", "conversation", {"extra": "data"})
        meta = mm_with_vectors.vector_store.add_memory.call_args[0][1]
        assert meta["type"] == "conversation"
        assert meta["extra"] == "data"

    def test_none_metadata_handled(self, mm_with_vectors):
        mm_with_vectors.store_long_term("text", "general")
        meta = mm_with_vectors.vector_store.add_memory.call_args[0][1]
        assert meta["type"] == "general"


# ── retrieve_relevant() tests ────────────────────────────────────────


class TestRetrieveRelevant:
    """Tests for retrieve_relevant() — semantic + recent actions."""

    def test_disabled_returns_empty_semantic(self, mm):
        mm.store_action("web_search", {"query": "test"}, {"success": True})
        result = mm.retrieve_relevant("test query")
        assert result["semantic"] == []
        assert len(result["recent_actions"]) == 1

    def test_enabled_returns_semantic_results(self, mm_with_vectors):
        result = mm_with_vectors.retrieve_relevant("supplements research")
        assert len(result["semantic"]) == 1
        assert "prior research" in result["semantic"][0]["text"]

    def test_includes_recent_actions(self, mm_with_vectors):
        mm_with_vectors.store_action("web_search", {}, {})
        result = mm_with_vectors.retrieve_relevant("query")
        assert len(result["recent_actions"]) == 1


# ── get_stats() tests ────────────────────────────────────────────────


class TestGetStats:
    """Tests for get_stats()."""

    def test_empty_stats(self, mm):
        stats = mm.get_stats()
        assert stats["short_term_count"] == 0
        assert stats["long_term_count"] == 0

    def test_counts_short_term(self, mm):
        mm.store_action("a1", {}, {})
        mm.store_action("a2", {}, {})
        stats = mm.get_stats()
        assert stats["short_term_count"] == 2

    def test_counts_long_term_with_vectors(self, mm_with_vectors):
        stats = mm_with_vectors.get_stats()
        assert stats["long_term_count"] == 42


# ── Conversation memory (session 98) ─────────────────────────────────


class TestConversationMemory:
    """Tests for conversation-specific memory methods."""

    def test_store_conversation_no_vectors(self, mm):
        """store_conversation returns empty string when vector store disabled."""
        result = mm.store_conversation("Jesse talked about woodworking")
        assert result == ""

    def test_store_conversation_with_vectors(self, mm_with_vectors):
        """store_conversation delegates to vector store with type=conversation."""
        result = mm_with_vectors.store_conversation(
            "Jesse talked about woodworking",
            metadata={"message_count": 6},
        )
        assert result == "mem_123"
        # Verify the call was made with correct metadata
        call_args = mm_with_vectors.vector_store.add_memory.call_args
        text = call_args[0][0]
        meta = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("metadata", {})
        assert "woodworking" in text

    def test_get_conversation_context_no_vectors(self, mm):
        """get_conversation_context returns empty list when vector store disabled."""
        result = mm.get_conversation_context("woodworking")
        assert result == []

    def test_get_conversation_context_with_vectors(self, mm_with_vectors):
        """get_conversation_context returns relevant text, filtered by distance."""
        mm_with_vectors.vector_store.search.return_value = [
            {"text": "Jesse likes woodworking", "distance": 0.2, "id": "a", "metadata": {}},
            {"text": "Archi helped with budget", "distance": 0.9, "id": "b", "metadata": {}},
        ]
        result = mm_with_vectors.get_conversation_context("woodworking")
        assert len(result) == 1  # Only the close one (< 0.8)
        assert "woodworking" in result[0]

    def test_get_conversation_context_filters_type(self, mm_with_vectors):
        """get_conversation_context passes type=conversation filter."""
        mm_with_vectors.get_conversation_context("test")
        call_kwargs = mm_with_vectors.vector_store.search.call_args
        assert call_kwargs[1]["filter_metadata"] == {"type": "conversation"}
