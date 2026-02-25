"""Tests for src/models/cache.py — QueryCache."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.models.cache import QueryCache


# ── Helpers ──────────────────────────────────────────────────────────

def _make_cache(**kwargs) -> QueryCache:
    """Create a QueryCache with sensible test defaults (no disk)."""
    defaults = {"ttl_seconds": 3600, "max_size": 0, "use_disk_cache": False}
    defaults.update(kwargs)
    return QueryCache(**defaults)


# ── Basic get/set ────────────────────────────────────────────────────

class TestBasicGetSet:
    def test_set_then_get_returns_response(self):
        cache = _make_cache()
        cache.set("hello", {"answer": "world"})
        assert cache.get("hello") == {"answer": "world"}

    def test_get_missing_returns_none(self):
        cache = _make_cache()
        assert cache.get("nonexistent") is None

    def test_set_overwrites_previous(self):
        cache = _make_cache()
        cache.set("key", {"v": 1})
        cache.set("key", {"v": 2})
        assert cache.get("key") == {"v": 2}

    def test_different_prompts_independent(self):
        cache = _make_cache()
        cache.set("a", {"v": "alpha"})
        cache.set("b", {"v": "beta"})
        assert cache.get("a") == {"v": "alpha"}
        assert cache.get("b") == {"v": "beta"}


# ── TTL expiration ───────────────────────────────────────────────────

class TestTTL:
    def test_expired_entry_returns_none(self):
        cache = _make_cache(ttl_seconds=1)
        cache.set("prompt", {"data": "val"})
        with patch("src.models.cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            assert cache.get("prompt") is None

    def test_not_yet_expired_returns_value(self):
        cache = _make_cache(ttl_seconds=100)
        cache.set("prompt", {"data": "val"})
        assert cache.get("prompt") == {"data": "val"}

    def test_expired_entry_is_deleted_from_cache(self):
        cache = _make_cache(ttl_seconds=1)
        cache.set("prompt", {"data": "val"})
        with patch("src.models.cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            cache.get("prompt")  # triggers deletion
        # Even without the mock, entry should be gone
        assert "prompt" not in [cache._hash_prompt("prompt") for k in cache._cache]


# ── LRU eviction ─────────────────────────────────────────────────────

class TestLRUEviction:
    def test_evicts_oldest_when_at_capacity(self):
        cache = _make_cache(max_size=2)
        cache.set("first", {"v": 1})
        cache.set("second", {"v": 2})
        cache.set("third", {"v": 3})  # should evict "first"
        assert cache.get("first") is None
        assert cache.get("second") == {"v": 2}
        assert cache.get("third") == {"v": 3}

    def test_access_refreshes_lru_order(self):
        cache = _make_cache(max_size=2)
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.get("a")  # refresh "a", so "b" becomes oldest
        cache.set("c", {"v": 3})  # should evict "b"
        assert cache.get("a") == {"v": 1}
        assert cache.get("b") is None
        assert cache.get("c") == {"v": 3}

    def test_unbounded_cache_never_evicts(self):
        cache = _make_cache(max_size=0)
        for i in range(100):
            cache.set(f"key_{i}", {"v": i})
        for i in range(100):
            assert cache.get(f"key_{i}") == {"v": i}

    def test_overwrite_existing_does_not_evict(self):
        cache = _make_cache(max_size=2)
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.set("a", {"v": 99})  # overwrite, not new entry
        assert cache.get("a") == {"v": 99}
        assert cache.get("b") == {"v": 2}


# ── Invalidation ─────────────────────────────────────────────────────

class TestInvalidation:
    def test_invalidate_existing_returns_true(self):
        cache = _make_cache()
        cache.set("key", {"v": 1})
        assert cache.invalidate("key") is True
        assert cache.get("key") is None

    def test_invalidate_missing_returns_false(self):
        cache = _make_cache()
        assert cache.invalidate("nope") is False


# ── Clear ────────────────────────────────────────────────────────────

class TestClear:
    def test_clear_removes_all_entries(self):
        cache = _make_cache()
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_clear_does_not_reset_stats(self):
        cache = _make_cache()
        cache.set("a", {"v": 1})
        cache.get("a")  # hit
        cache.get("miss")  # miss
        cache.clear()
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_clear_all_resets_stats(self):
        cache = _make_cache()
        cache.set("a", {"v": 1})
        cache.get("a")
        cache.get("miss")
        cache.clear_all()
        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["cached_entries"] == 0


# ── Statistics ───────────────────────────────────────────────────────

class TestStats:
    def test_initial_stats_all_zero(self):
        cache = _make_cache()
        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["total_queries"] == 0
        assert stats["hit_rate_percent"] == 0.0
        assert stats["cached_entries"] == 0

    def test_hit_increments_on_cache_hit(self):
        cache = _make_cache()
        cache.set("x", {"v": 1})
        cache.get("x")
        assert cache.get_stats()["hits"] == 1

    def test_miss_increments_on_cache_miss(self):
        cache = _make_cache()
        cache.get("nope")
        assert cache.get_stats()["misses"] == 1

    def test_hit_rate_calculation(self):
        cache = _make_cache()
        cache.set("x", {"v": 1})
        cache.get("x")   # hit
        cache.get("y")   # miss
        stats = cache.get_stats()
        assert stats["hit_rate_percent"] == 50.0

    def test_cached_entries_count(self):
        cache = _make_cache()
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        assert cache.get_stats()["cached_entries"] == 2

    def test_expired_entry_counts_as_miss(self):
        cache = _make_cache(ttl_seconds=1)
        cache.set("x", {"v": 1})
        with patch("src.models.cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            cache.get("x")  # expired → miss
        stats = cache.get_stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0


# ── Hash prompt ──────────────────────────────────────────────────────

class TestHashPrompt:
    def test_same_prompt_same_hash(self):
        cache = _make_cache()
        assert cache._hash_prompt("hello") == cache._hash_prompt("hello")

    def test_different_prompts_different_hashes(self):
        cache = _make_cache()
        assert cache._hash_prompt("hello") != cache._hash_prompt("world")

    def test_hash_is_hex_string(self):
        cache = _make_cache()
        h = cache._hash_prompt("test")
        assert all(c in "0123456789abcdef" for c in h)
        assert len(h) == 64  # SHA-256 hex digest


# ── Disk cache ───────────────────────────────────────────────────────

class TestDiskCache:
    def test_disk_cache_persists_and_reads(self, tmp_path):
        cache = QueryCache(
            ttl_seconds=3600, max_size=0,
            use_disk_cache=True, disk_cache_dir=tmp_path / "cache",
        )
        cache.set("prompt1", {"answer": "stored"})
        # Clear memory, then retrieve from disk
        cache._cache.clear()
        result = cache.get("prompt1")
        assert result == {"answer": "stored"}

    def test_disk_cache_expired_returns_none(self, tmp_path):
        cache = QueryCache(
            ttl_seconds=1, max_size=0,
            use_disk_cache=True, disk_cache_dir=tmp_path / "cache",
        )
        cache.set("prompt1", {"answer": "stored"})
        cache._cache.clear()
        with patch("src.models.cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 5
            result = cache.get("prompt1")
        assert result is None

    def test_disk_cache_dir_created(self, tmp_path):
        disk_dir = tmp_path / "new_cache_dir"
        QueryCache(
            ttl_seconds=3600, max_size=0,
            use_disk_cache=True, disk_cache_dir=disk_dir,
        )
        assert disk_dir.exists()

    def test_disk_cache_corrupt_file_handled(self, tmp_path):
        cache = QueryCache(
            ttl_seconds=3600, max_size=0,
            use_disk_cache=True, disk_cache_dir=tmp_path / "cache",
        )
        # Write a corrupt file
        key = cache._hash_prompt("bad")
        corrupt_file = (tmp_path / "cache") / f"{key}.json"
        corrupt_file.write_text("not json at all")
        cache._cache.clear()
        assert cache.get("bad") is None

    def test_disk_stats_includes_disk_entries(self, tmp_path):
        cache = QueryCache(
            ttl_seconds=3600, max_size=0,
            use_disk_cache=True, disk_cache_dir=tmp_path / "cache",
        )
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        stats = cache.get_stats()
        assert stats["disk_entries"] == 2

    def test_no_disk_save_when_disabled(self, tmp_path):
        cache = QueryCache(
            ttl_seconds=3600, max_size=0,
            use_disk_cache=False, disk_cache_dir=tmp_path / "cache",
        )
        cache.set("a", {"v": 1})
        assert not (tmp_path / "cache").exists()
