"""Unit tests for FileTracker — stale file detection and cleanup.

Tests the FileTracker class: manifest CRUD, stale detection,
persistent marking, and file removal.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.file_tracker import FileTracker


@pytest.fixture
def tracker_dir(tmp_path):
    """Create a temporary directory structure mimicking Archi's workspace."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    workspace = tmp_path / "workspace" / "projects" / "TestProject"
    workspace.mkdir(parents=True)
    return tmp_path, data_dir


@pytest.fixture
def tracker(tracker_dir):
    """Create a FileTracker with temp data dir."""
    tmp_path, data_dir = tracker_dir
    with patch("src.core.file_tracker._base_path", return_value=tmp_path):
        return FileTracker(data_dir=data_dir, stale_days=14)


class TestFileTrackerBasic:
    """Basic CRUD operations on the file manifest."""

    def test_init_empty(self, tracker):
        """New tracker starts with empty manifest."""
        assert tracker.tracked_count() == 0
        assert tracker.persistent_count() == 0

    def test_record_file(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            tracker.record_file_created(
                str(tmp_path / "workspace" / "projects" / "TestProject" / "report.md"),
                goal_id="goal_abc123",
            )
        assert tracker.tracked_count() == 1
        assert "workspace/projects/TestProject/report.md" in tracker.manifest

    def test_record_relative_path(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            tracker.record_file_created("workspace/projects/TestProject/file.md", goal_id="g1")
        assert tracker.tracked_count() == 1

    def test_ignores_non_workspace_path(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            tracker.record_file_created("/tmp/random/file.txt", goal_id="g1")
        assert tracker.tracked_count() == 0

    def test_manifest_persists(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            t1 = FileTracker(data_dir=data_dir)
            t1.record_file_created("workspace/projects/X/a.md", goal_id="g1")
            # Create a new tracker — should load from disk
            t2 = FileTracker(data_dir=data_dir)
        assert t2.tracked_count() == 1


class TestPersistent:
    """Tests for the 'never purge' persistent flag."""

    def test_mark_persistent(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            tracker.record_file_created("workspace/projects/X/keep.md", goal_id="g1")
            result = tracker.mark_persistent("workspace/projects/X/keep.md")
        assert result is True
        assert tracker.is_persistent("workspace/projects/X/keep.md")
        assert tracker.persistent_count() == 1

    def test_mark_persistent_untracked_file(self, tracker_dir):
        """Marking an untracked file as persistent creates a new entry."""
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            tracker.mark_persistent("workspace/projects/X/new.md")
        assert tracker.tracked_count() == 1
        assert tracker.is_persistent("workspace/projects/X/new.md")

    def test_persistent_survives_reload(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            t1 = FileTracker(data_dir=data_dir)
            t1.record_file_created("workspace/projects/X/keep.md", goal_id="g1")
            t1.mark_persistent("workspace/projects/X/keep.md")
            t2 = FileTracker(data_dir=data_dir)
        assert t2.is_persistent("workspace/projects/X/keep.md")

    def test_persistent_not_lost_on_re_record(self, tracker_dir):
        """Re-recording a persistent file keeps the persistent flag."""
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            tracker.record_file_created("workspace/projects/X/keep.md", goal_id="g1")
            tracker.mark_persistent("workspace/projects/X/keep.md")
            tracker.record_file_created("workspace/projects/X/keep.md", goal_id="g2")
        assert tracker.is_persistent("workspace/projects/X/keep.md")


class TestStaleDetection:
    """Tests for get_stale_files()."""

    def test_recent_file_not_stale(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir, stale_days=14)
            # Create an actual file
            fpath = tmp_path / "workspace" / "projects" / "TestProject" / "recent.md"
            fpath.write_text("content")
            tracker.record_file_created(str(fpath), goal_id="g1")
            stale = tracker.get_stale_files()
        assert len(stale) == 0

    def test_old_file_is_stale(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir, stale_days=14)
            # Create file
            fpath = tmp_path / "workspace" / "projects" / "TestProject" / "old.md"
            fpath.write_text("old content")
            tracker.record_file_created(str(fpath), goal_id="g1")
            # Backdate the creation time
            key = "workspace/projects/TestProject/old.md"
            tracker.manifest[key]["created_at"] = (
                datetime.now() - timedelta(days=20)
            ).isoformat()
            tracker.save()
            stale = tracker.get_stale_files()
        assert key in stale

    def test_persistent_file_never_stale(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir, stale_days=14)
            fpath = tmp_path / "workspace" / "projects" / "TestProject" / "keep.md"
            fpath.write_text("important")
            tracker.record_file_created(str(fpath), goal_id="g1")
            tracker.mark_persistent(str(fpath))
            # Backdate
            key = "workspace/projects/TestProject/keep.md"
            tracker.manifest[key]["created_at"] = (
                datetime.now() - timedelta(days=30)
            ).isoformat()
            tracker.save()
            stale = tracker.get_stale_files()
        assert len(stale) == 0

    def test_deleted_file_not_stale(self, tracker_dir):
        """Files that no longer exist on disk are not listed as stale."""
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir, stale_days=14)
            tracker.record_file_created("workspace/projects/TestProject/gone.md", goal_id="g1")
            key = "workspace/projects/TestProject/gone.md"
            tracker.manifest[key]["created_at"] = (
                datetime.now() - timedelta(days=20)
            ).isoformat()
            tracker.save()
            stale = tracker.get_stale_files()
        assert len(stale) == 0  # File doesn't exist on disk

    def test_custom_stale_days(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir, stale_days=7)
            fpath = tmp_path / "workspace" / "projects" / "TestProject" / "week.md"
            fpath.write_text("content")
            tracker.record_file_created(str(fpath), goal_id="g1")
            key = "workspace/projects/TestProject/week.md"
            tracker.manifest[key]["created_at"] = (
                datetime.now() - timedelta(days=10)
            ).isoformat()
            tracker.save()
            stale = tracker.get_stale_files()
        assert key in stale


class TestFileRemoval:
    """Tests for remove_file()."""

    def test_remove_existing_file(self, tracker_dir):
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            fpath = tmp_path / "workspace" / "projects" / "TestProject" / "delete_me.md"
            fpath.write_text("to be deleted")
            tracker.record_file_created(str(fpath), goal_id="g1")
            result = tracker.remove_file(str(fpath))
        assert result is True
        assert not fpath.exists()
        assert tracker.tracked_count() == 0

    def test_remove_already_gone(self, tracker_dir):
        """Removing a file that's already deleted from disk still cleans manifest."""
        tmp_path, data_dir = tracker_dir
        with patch("src.core.file_tracker._base_path", return_value=tmp_path):
            tracker = FileTracker(data_dir=data_dir)
            tracker.record_file_created("workspace/projects/TestProject/ghost.md", goal_id="g1")
            result = tracker.remove_file("workspace/projects/TestProject/ghost.md")
        assert result is True
        assert tracker.tracked_count() == 0


class TestCleanupApprovalFormat:
    """Verify the cleanup approval message formatting."""

    def test_stale_file_list_format(self):
        """Stale files should be formatted as a readable list."""
        stale = [
            "workspace/projects/Health/old_report.md",
            "workspace/projects/Archi/temp_analysis.md",
        ]
        # Simulate the format used in request_cleanup_approval
        file_list = "\n".join(f"  • `{f}`" for f in stale)
        assert "`workspace/projects/Health/old_report.md`" in file_list
        assert "`workspace/projects/Archi/temp_analysis.md`" in file_list


class TestCleanupNeverParsing:
    """Verify parsing of 'never <path>' responses."""

    def test_never_with_path(self):
        from src.interfaces.discord_bot import _check_cleanup_never
        result = _check_cleanup_never("never workspace/projects/Health/keep.md")
        assert result == "workspace/projects/Health/keep.md"

    def test_never_with_backticks(self):
        from src.interfaces.discord_bot import _check_cleanup_never
        result = _check_cleanup_never("never `workspace/projects/Health/keep.md`")
        assert result == "workspace/projects/Health/keep.md"

    def test_not_never(self):
        from src.interfaces.discord_bot import _check_cleanup_never
        result = _check_cleanup_never("no thanks")
        assert result is None

    def test_never_alone(self):
        from src.interfaces.discord_bot import _check_cleanup_never
        result = _check_cleanup_never("never")
        assert result is None

    def test_never_with_quotes(self):
        from src.interfaces.discord_bot import _check_cleanup_never
        result = _check_cleanup_never("never 'my_file.md'")
        assert result == "my_file.md"
