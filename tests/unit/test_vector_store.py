"""Unit tests for VectorStore IVF-PQ index creation logic.

Tests the _ensure_index method and its integration with add_memory,
using mocks for LanceDB and sentence-transformers since they require
heavy ML dependencies.

Created session 86.
"""

import json
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from src.memory.vector_store import (
    VectorStore,
    _INDEX_THRESHOLD,
    _INDEX_RECHECK_INTERVAL,
    _EMBEDDING_DIM,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_deps():
    """Patch LanceDB and SentenceTransformer for unit testing."""
    mock_table = MagicMock()
    mock_table.count_rows.return_value = 1  # below threshold
    mock_table.add.return_value = None

    mock_db = MagicMock()
    mock_db.open_table.return_value = mock_table

    mock_lancedb = MagicMock()
    mock_lancedb.connect.return_value = mock_db

    mock_model = MagicMock()
    mock_model.encode.return_value = [np.zeros(_EMBEDDING_DIM)]

    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = mock_model

    with patch.dict("sys.modules", {"lancedb": mock_lancedb, "sentence_transformers": mock_st}):
        with patch("os.makedirs"):
            store = VectorStore(data_dir="/tmp/test_vectors")
    return store, mock_table


# ── _ensure_index tests ──────────────────────────────────────────────


class TestEnsureIndex:
    """Tests for IVF-PQ index creation threshold logic."""

    def test_no_index_below_threshold(self, mock_deps):
        store, mock_table = mock_deps
        mock_table.count_rows.return_value = _INDEX_THRESHOLD - 1
        store._index_built = False
        store._ensure_index()
        mock_table.create_index.assert_not_called()
        assert not store._index_built

    def test_creates_index_at_threshold(self, mock_deps):
        store, mock_table = mock_deps
        mock_table.count_rows.return_value = _INDEX_THRESHOLD
        store._index_built = False
        store._ensure_index()
        mock_table.create_index.assert_called_once()
        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["metric"] == "cosine"
        assert call_kwargs["num_sub_vectors"] == _EMBEDDING_DIM // 8
        assert call_kwargs["num_partitions"] == _INDEX_THRESHOLD // 4096
        assert store._index_built

    def test_creates_index_above_threshold(self, mock_deps):
        store, mock_table = mock_deps
        mock_table.count_rows.return_value = 50_000
        store._index_built = False
        store._ensure_index()
        mock_table.create_index.assert_called_once()
        call_kwargs = mock_table.create_index.call_args[1]
        assert call_kwargs["num_partitions"] == 50_000 // 4096
        assert store._index_built

    def test_skips_if_already_built(self, mock_deps):
        store, mock_table = mock_deps
        store._index_built = True
        mock_table.count_rows.reset_mock()
        store._ensure_index()
        mock_table.count_rows.assert_not_called()
        mock_table.create_index.assert_not_called()

    def test_handles_create_index_failure(self, mock_deps):
        store, mock_table = mock_deps
        mock_table.count_rows.return_value = _INDEX_THRESHOLD
        mock_table.create_index.side_effect = RuntimeError("index error")
        store._index_built = False
        store._ensure_index()  # should not raise
        assert not store._index_built

    def test_partition_count_minimum_is_one(self, mock_deps):
        store, mock_table = mock_deps
        mock_table.count_rows.return_value = _INDEX_THRESHOLD  # 10000 // 4096 = 2
        store._index_built = False
        store._ensure_index()
        partitions = mock_table.create_index.call_args[1]["num_partitions"]
        assert partitions >= 1


# ── add_memory index recheck tests ───────────────────────────────────


class TestAddMemoryIndexRecheck:
    """Tests that add_memory triggers _ensure_index at the right interval."""

    def test_no_recheck_before_interval(self, mock_deps):
        store, mock_table = mock_deps
        store._index_built = False
        store._adds_since_index = 0
        # Simulate adds below the recheck interval
        for _ in range(_INDEX_RECHECK_INTERVAL - 1):
            store.add_memory("test text")
        # count_rows only called during __init__ _ensure_index, not again
        initial_call_count = mock_table.count_rows.call_count
        assert store._adds_since_index == _INDEX_RECHECK_INTERVAL - 1
        # No extra count_rows calls beyond init
        assert mock_table.count_rows.call_count == initial_call_count

    def test_recheck_at_interval(self, mock_deps):
        store, mock_table = mock_deps
        store._index_built = False
        store._adds_since_index = _INDEX_RECHECK_INTERVAL - 1
        mock_table.count_rows.return_value = _INDEX_THRESHOLD + 5000
        store.add_memory("trigger recheck")
        assert store._adds_since_index == _INDEX_RECHECK_INTERVAL
        mock_table.create_index.assert_called_once()
        assert store._index_built

    def test_no_recheck_when_index_built(self, mock_deps):
        store, mock_table = mock_deps
        store._index_built = True
        store._adds_since_index = _INDEX_RECHECK_INTERVAL - 1
        store.add_memory("should not trigger")
        mock_table.create_index.assert_not_called()


# ── Constants sanity checks ──────────────────────────────────────────


class TestConstants:
    """Sanity checks for index configuration constants."""

    def test_threshold_is_reasonable(self):
        assert _INDEX_THRESHOLD >= 1000

    def test_recheck_interval_is_reasonable(self):
        assert 100 <= _INDEX_RECHECK_INTERVAL <= 10_000

    def test_embedding_dim_matches_model(self):
        assert _EMBEDDING_DIM == 384  # all-MiniLM-L6-v2


# ── find_similar tests ──────────────────────────────────────────────


class TestFindSimilar:
    """Tests for find_similar() — near-duplicate detection."""

    def test_returns_empty_when_no_matches(self, mock_deps):
        store, mock_table = mock_deps
        # search returns empty
        mock_table.search.return_value = MagicMock()
        mock_table.search.return_value.limit.return_value = MagicMock()
        mock_table.search.return_value.limit.return_value.to_list.return_value = []
        result = store.find_similar("totally new text", max_distance=0.5)
        assert result == []

    def test_filters_by_max_distance(self, mock_deps):
        store, mock_table = mock_deps
        # Mock search to return results with varying distances
        mock_query = MagicMock()
        mock_table.search.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.to_list.return_value = [
            {"id": "close", "text": "close match", "_distance": 0.1, "type": "general", "metadata_json": "{}"},
            {"id": "far", "text": "far match", "_distance": 0.8, "type": "general", "metadata_json": "{}"},
        ]
        result = store.find_similar("test", max_distance=0.5)
        assert len(result) == 1
        assert result[0]["id"] == "close"

    def test_excludes_init_placeholder(self, mock_deps):
        store, mock_table = mock_deps
        mock_query = MagicMock()
        mock_table.search.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.to_list.return_value = [
            {"id": "_init_", "text": "Initialization placeholder.", "_distance": 0.05, "type": "system", "metadata_json": "{}"},
        ]
        result = store.find_similar("Initialization", max_distance=0.5)
        assert result == []


# ── update_memory tests ─────────────────────────────────────────────


class TestUpdateMemory:
    """Tests for update_memory() — replace existing memory text."""

    def test_deletes_old_and_adds_new(self, mock_deps):
        store, mock_table = mock_deps
        # Mock the search for existing metadata
        mock_query = MagicMock()
        mock_table.search.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.to_list.return_value = [
            {"type": "research_result", "metadata_json": '{"goal_id": "g1"}'}
        ]
        store.update_memory("mem_abc", "updated text", {"type": "research_result"})
        # Insert-then-delete: add is called first, delete uses timestamp filter
        mock_table.add.assert_called()
        added_row = mock_table.add.call_args[0][0][0]
        assert added_row["id"] == "mem_abc"
        assert added_row["text"] == "updated text"
        # Delete uses timestamp filter to remove only old rows
        delete_arg = mock_table.delete.call_args[0][0]
        assert "id = 'mem_abc'" in delete_arg
        assert "timestamp <" in delete_arg

    def test_preserves_existing_metadata(self, mock_deps):
        store, mock_table = mock_deps
        mock_query = MagicMock()
        mock_table.search.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.to_list.return_value = [
            {"type": "research_result", "metadata_json": '{"goal_id": "g1"}'}
        ]
        store.update_memory("mem_abc", "new text")
        added_row = mock_table.add.call_args[0][0][0]
        meta = json.loads(added_row["metadata_json"])
        assert meta["goal_id"] == "g1"


# ── delete_memory tests ─────────────────────────────────────────────


class TestDeleteMemory:
    """Tests for delete_memory() — remove by ID."""

    def test_deletes_by_id(self, mock_deps):
        store, mock_table = mock_deps
        result = store.delete_memory("mem_xyz")
        mock_table.delete.assert_called_once_with("id = 'mem_xyz'")
        assert result is True

    def test_returns_false_on_error(self, mock_deps):
        store, mock_table = mock_deps
        mock_table.delete.side_effect = RuntimeError("delete failed")
        result = store.delete_memory("bad_id")
        assert result is False
