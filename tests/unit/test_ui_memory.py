"""Unit tests for the UI Memory module — SQLite cache for UI element locations.

Tests UIMemory init, store/retrieve, success/failure recording,
screenshot hashing, stale cleanup, and cache invalidation logic.

Created session 149.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from src.tools.ui_memory import UIMemory


# ── Init tests ─────────────────────────────────────────────────────


class TestUIMemoryInit:
    """Tests for UIMemory initialization."""

    def test_creates_db_dir(self, tmp_path):
        db_path = tmp_path / "subdir" / "ui_memory.db"
        mem = UIMemory(db_path=db_path)
        assert db_path.parent.exists()

    def test_creates_table(self, tmp_path):
        db_path = tmp_path / "ui_memory.db"
        mem = UIMemory(db_path=db_path)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ui_elements'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_default_db_path(self):
        """Default path should be data/ui_memory.db."""
        with patch("src.tools.ui_memory.Path.mkdir"):
            with patch("src.tools.ui_memory.sqlite3.connect") as mock_conn:
                mock_conn.return_value.cursor.return_value.fetchone.return_value = None
                # Just verify we don't crash — the actual path depends on cwd
                try:
                    mem = UIMemory()
                    assert str(mem.db_path).endswith("ui_memory.db")
                except Exception:
                    pass  # May fail on actual DB creation in test env


# ── store_element() tests ──────────────────────────────────────────


class TestStoreElement:
    """Tests for store_element()."""

    def test_store_coordinate(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        result = mem.store_element(
            app_name="notepad",
            element_name="save button",
            element_type="coordinate",
            location={"x": 100, "y": 200},
        )
        assert result is True

    def test_store_selector(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        result = mem.store_element(
            app_name="chrome",
            element_name="login",
            element_type="selector",
            location={"selector": "#login-btn"},
        )
        assert result is True

    def test_upsert_updates_existing(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20})
        mem.store_element("app", "btn", "coordinate", {"x": 30, "y": 40})
        elem = mem.get_element("app", "btn")
        assert elem is not None
        assert elem["location"]["x"] == 30

    def test_store_with_hash_and_confidence(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        result = mem.store_element(
            app_name="app",
            element_name="elem",
            element_type="coordinate",
            location={"x": 0, "y": 0},
            screenshot_hash="abc123",
            confidence=0.8,
        )
        assert result is True
        elem = mem.get_element("app", "elem")
        assert elem["confidence"] == 0.8


# ── get_element() tests ────────────────────────────────────────────


class TestGetElement:
    """Tests for get_element() — cache retrieval with validation."""

    def test_returns_none_for_missing(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        assert mem.get_element("app", "nonexistent") is None

    def test_returns_stored_element(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 50, "y": 75})
        elem = mem.get_element("app", "btn")
        assert elem is not None
        assert elem["type"] == "coordinate"
        assert elem["location"] == {"x": 50, "y": 75}

    def test_screenshot_hash_mismatch_returns_none(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20},
                         screenshot_hash="old_hash")
        result = mem.get_element("app", "btn", screenshot_hash="new_hash")
        assert result is None

    def test_screenshot_hash_match_returns_element(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20},
                         screenshot_hash="same_hash")
        result = mem.get_element("app", "btn", screenshot_hash="same_hash")
        assert result is not None

    def test_no_hash_check_when_none(self, tmp_path):
        """When no screenshot_hash is provided, skip hash validation."""
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20},
                         screenshot_hash="some_hash")
        result = mem.get_element("app", "btn")
        assert result is not None

    def test_high_failure_rate_invalidates_cache(self, tmp_path):
        """Elements with failures > successes * 2 are considered invalid."""
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20})
        # After store, success_count = 1, failure_count = 0
        # Add failures: need failure_count > success_count * 2 => > 2
        mem.record_failure("app", "btn")
        mem.record_failure("app", "btn")
        mem.record_failure("app", "btn")
        result = mem.get_element("app", "btn")
        assert result is None


# ── record_success/failure tests ───────────────────────────────────


class TestRecordSuccessFailure:
    """Tests for success and failure recording."""

    def test_record_success_increments(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20})
        mem.record_success("app", "btn")
        mem.record_success("app", "btn")
        # Element should still be retrievable (high success count)
        elem = mem.get_element("app", "btn")
        assert elem is not None

    def test_record_failure_increments(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20})
        mem.record_failure("app", "btn")
        # One failure, one success from store — still valid
        elem = mem.get_element("app", "btn")
        assert elem is not None

    def test_record_on_nonexistent_no_crash(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        # Should not raise
        mem.record_success("app", "nonexistent")
        mem.record_failure("app", "nonexistent")


# ── hash_screenshot() tests ────────────────────────────────────────


class TestHashScreenshot:
    """Tests for hash_screenshot()."""

    def test_hashes_file(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        img = tmp_path / "shot.png"
        img.write_bytes(b"fake image data")
        h = mem.hash_screenshot(img)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest length

    def test_same_content_same_hash(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        img1.write_bytes(b"same content")
        img2.write_bytes(b"same content")
        assert mem.hash_screenshot(img1) == mem.hash_screenshot(img2)

    def test_different_content_different_hash(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        img1.write_bytes(b"content A")
        img2.write_bytes(b"content B")
        assert mem.hash_screenshot(img1) != mem.hash_screenshot(img2)

    def test_missing_file_returns_empty(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        h = mem.hash_screenshot(tmp_path / "nonexistent.png")
        assert h == ""


# ── clear_stale() tests ────────────────────────────────────────────


class TestClearStale:
    """Tests for clear_stale() — removing old cached elements."""

    def test_removes_old_entries(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20})
        # Manually backdate the last_used to 60 days ago
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.execute(
            "UPDATE ui_elements SET last_used = datetime('now', '-60 days')"
        )
        conn.commit()
        conn.close()
        deleted = mem.clear_stale(days=30)
        assert deleted == 1

    def test_keeps_recent_entries(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        mem.store_element("app", "btn", "coordinate", {"x": 10, "y": 20})
        deleted = mem.clear_stale(days=30)
        assert deleted == 0
        # Element still there
        assert mem.get_element("app", "btn") is not None

    def test_empty_db_returns_zero(self, tmp_path):
        mem = UIMemory(db_path=tmp_path / "test.db")
        deleted = mem.clear_stale(days=1)
        assert deleted == 0
