"""Unit tests for the timestamps module — persistent timestamp storage in SQLite.

Tests load_timestamp, save_timestamp, and module-level connection management.

Created session 149.
"""

import sqlite3
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import src.maintenance.timestamps as ts_mod


@pytest.fixture(autouse=True)
def reset_module_state(tmp_path):
    """Reset module-level state before each test."""
    # Save original state
    orig_conn = ts_mod._conn
    orig_init = ts_mod._initialized

    # Reset for test isolation
    ts_mod._conn = None
    ts_mod._initialized = False

    # Patch db_path to use temp directory
    db_file = str(tmp_path / "memory.db")
    with patch("src.maintenance.timestamps._db_path", return_value=db_file):
        yield tmp_path

    # Restore original state
    if ts_mod._conn is not None and ts_mod._conn is not orig_conn:
        try:
            ts_mod._conn.close()
        except Exception:
            pass
    ts_mod._conn = orig_conn
    ts_mod._initialized = orig_init


# ── save_timestamp() tests ─────────────────────────────────────────


class TestSaveTimestamp:
    """Tests for save_timestamp()."""

    def test_save_with_value(self):
        dt = datetime(2026, 2, 24, 12, 0, 0, tzinfo=timezone.utc)
        ts_mod.save_timestamp("test_key", dt)
        loaded = ts_mod.load_timestamp("test_key")
        assert loaded is not None
        assert loaded.year == 2026
        assert loaded.month == 2
        assert loaded.day == 24

    def test_save_without_value_uses_now(self):
        ts_mod.save_timestamp("auto_key")
        loaded = ts_mod.load_timestamp("auto_key")
        assert loaded is not None
        # Should be very recent
        now = datetime.now(timezone.utc)
        diff = abs((now - loaded).total_seconds())
        assert diff < 5

    def test_overwrite_existing_key(self):
        dt1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 15, tzinfo=timezone.utc)
        ts_mod.save_timestamp("key", dt1)
        ts_mod.save_timestamp("key", dt2)
        loaded = ts_mod.load_timestamp("key")
        assert loaded.month == 6

    def test_multiple_keys(self):
        dt1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt2 = datetime(2026, 2, 2, tzinfo=timezone.utc)
        ts_mod.save_timestamp("key_a", dt1)
        ts_mod.save_timestamp("key_b", dt2)
        assert ts_mod.load_timestamp("key_a").month == 1
        assert ts_mod.load_timestamp("key_b").month == 2


# ── load_timestamp() tests ─────────────────────────────────────────


class TestLoadTimestamp:
    """Tests for load_timestamp()."""

    def test_missing_key_returns_none(self):
        result = ts_mod.load_timestamp("nonexistent")
        assert result is None

    def test_invalid_value_returns_none(self, reset_module_state):
        """If DB has an invalid timestamp string, return None."""
        tmp_path = reset_module_state
        # Save a valid one first to init the table
        ts_mod.save_timestamp("bad_key", datetime(2026, 1, 1, tzinfo=timezone.utc))
        # Manually corrupt the value
        conn = ts_mod._get_conn()
        conn.execute("UPDATE metadata SET value = 'not-a-date' WHERE key = 'bad_key'")
        conn.commit()
        result = ts_mod.load_timestamp("bad_key")
        assert result is None

    def test_empty_value_returns_none(self, reset_module_state):
        tmp_path = reset_module_state
        ts_mod.save_timestamp("empty_key", datetime(2026, 1, 1, tzinfo=timezone.utc))
        conn = ts_mod._get_conn()
        conn.execute("UPDATE metadata SET value = '' WHERE key = 'empty_key'")
        conn.commit()
        result = ts_mod.load_timestamp("empty_key")
        assert result is None

    def test_roundtrip_preserves_timezone(self):
        dt = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        ts_mod.save_timestamp("tz_key", dt)
        loaded = ts_mod.load_timestamp("tz_key")
        assert loaded is not None
        assert loaded.tzinfo is not None


# ── _get_conn() tests ──────────────────────────────────────────────


class TestGetConn:
    """Tests for _get_conn() — connection management."""

    def test_creates_table(self, reset_module_state):
        conn = ts_mod._get_conn()
        assert ts_mod._initialized is True
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'")
        assert cursor.fetchone() is not None

    def test_reuses_connection(self):
        conn1 = ts_mod._get_conn()
        conn2 = ts_mod._get_conn()
        assert conn1 is conn2

    def test_wal_mode(self, reset_module_state):
        conn = ts_mod._get_conn()
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"
