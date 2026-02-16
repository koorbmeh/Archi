"""
File Tracker — Track workspace file creation and detect stale files.

Records which files are created by which goals, tracks persistent
("never purge") flags, and identifies stale files for cleanup.

Manifest stored at data/file_manifest.json.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# Default stale threshold (overridable via rules.yaml)
_DEFAULT_STALE_DAYS = 14


class FileTracker:
    """Track workspace files, their origin goals, and staleness."""

    def __init__(self, data_dir: Optional[Path] = None, stale_days: int = _DEFAULT_STALE_DAYS):
        self.data_dir = Path(data_dir) if data_dir else _base_path() / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "file_manifest.json"
        self.stale_days = stale_days

        # manifest: { "files": { path: { goal_id, created_at, persistent } }, ... }
        self.manifest: Dict[str, Dict[str, Any]] = {}
        self._load()

        logger.info(
            "FileTracker initialized (%d files tracked, stale_days=%d)",
            len(self.manifest), self.stale_days,
        )

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load manifest from disk."""
        if not self._file.exists():
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.manifest = data.get("files", {})
        except Exception as e:
            logger.warning("Could not load file manifest: %s", e)

    def save(self) -> None:
        """Save manifest to disk (atomic write)."""
        tmp = self._file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"files": self.manifest}, f, indent=2, ensure_ascii=False)
            tmp.replace(self._file)
        except Exception as e:
            logger.warning("Could not save file manifest: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ── Tracking operations ──────────────────────────────────────────

    def record_file_created(self, path: str, goal_id: str = "") -> None:
        """Record that a file was created by a goal.

        Args:
            path: Absolute or workspace-relative path to the created file.
            goal_id: The goal that produced this file (empty for manual).
        """
        # Normalize to relative path for consistency
        norm_path = self._normalize_path(path)
        if not norm_path:
            return

        self.manifest[norm_path] = {
            "goal_id": goal_id,
            "created_at": datetime.now().isoformat(),
            "persistent": self.manifest.get(norm_path, {}).get("persistent", False),
        }
        self.save()
        logger.debug("Tracked file: %s (goal=%s)", norm_path, goal_id[:12] if goal_id else "manual")

    def mark_persistent(self, path: str) -> bool:
        """Mark a file as 'never purge'.

        Args:
            path: File path (absolute or relative).

        Returns:
            True if the file was found and marked, False otherwise.
        """
        norm_path = self._normalize_path(path)
        if not norm_path:
            return False

        if norm_path not in self.manifest:
            # Create entry even if not previously tracked
            self.manifest[norm_path] = {
                "goal_id": "",
                "created_at": datetime.now().isoformat(),
                "persistent": True,
            }
        else:
            self.manifest[norm_path]["persistent"] = True

        self.save()
        logger.info("Marked persistent: %s", norm_path)
        return True

    def is_persistent(self, path: str) -> bool:
        """Check if a file is marked as never-purge."""
        norm_path = self._normalize_path(path)
        return self.manifest.get(norm_path, {}).get("persistent", False)

    def get_stale_files(self, days: Optional[int] = None) -> List[str]:
        """Get list of stale files (old, not persistent, still exists on disk).

        Args:
            days: Override stale threshold (default: self.stale_days).

        Returns:
            List of workspace-relative file paths that are stale.
        """
        threshold = days or self.stale_days
        cutoff = datetime.now() - timedelta(days=threshold)
        base = _base_path()
        stale = []

        for path, info in self.manifest.items():
            # Skip persistent files
            if info.get("persistent"):
                continue

            # Check age
            try:
                created = datetime.fromisoformat(info.get("created_at", ""))
            except (ValueError, TypeError):
                continue

            if created > cutoff:
                continue

            # Check file still exists on disk
            full_path = base / path
            if full_path.exists():
                stale.append(path)

        return sorted(stale)

    def remove_file(self, path: str) -> bool:
        """Remove a file from disk and from the manifest.

        Args:
            path: Workspace-relative or absolute path.

        Returns:
            True if file was deleted, False otherwise.
        """
        norm_path = self._normalize_path(path)
        if not norm_path:
            return False

        full_path = _base_path() / norm_path
        try:
            if full_path.exists():
                full_path.unlink()
                logger.info("Deleted stale file: %s", norm_path)
            # Remove from manifest regardless
            self.manifest.pop(norm_path, None)
            self.save()
            return True
        except Exception as e:
            logger.warning("Could not delete %s: %s", norm_path, e)
            return False

    def tracked_count(self) -> int:
        """Number of files being tracked."""
        return len(self.manifest)

    def persistent_count(self) -> int:
        """Number of files marked as persistent (never purge)."""
        return sum(1 for info in self.manifest.values() if info.get("persistent"))

    # ── Internal ─────────────────────────────────────────────────────

    def _normalize_path(self, path: str) -> str:
        """Normalize a path to workspace-relative form.

        Strips the base path prefix if present, returning a clean
        relative path like 'workspace/projects/Health/file.md'.
        Returns empty string if path is empty or outside workspace.
        """
        if not path:
            return ""

        path = path.replace("\\", "/")
        base = str(_base_path()).replace("\\", "/")

        # Strip base path prefix
        if path.startswith(base):
            path = path[len(base):].lstrip("/")

        # Only track workspace files
        if not path.startswith("workspace/"):
            return ""

        return path
