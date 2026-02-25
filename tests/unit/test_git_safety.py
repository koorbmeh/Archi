"""
Unit tests for git_safety.py functions.

Covers: _git, _has_changes, _create_tag, _prune_old_tags, pre_modify_checkpoint,
post_modify_commit, rollback_last.

Session 150.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import subprocess

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest

from src.utils.git_safety import (
    _git,
    _has_changes,
    _create_tag,
    _prune_old_tags,
    pre_modify_checkpoint,
    post_modify_commit,
    rollback_last,
    _COMMIT_PREFIX,
    _TAG_PREFIX,
    _MAX_TAGS,
)


# ── TestGitHelper ────────────────────────────────────────────────────────────


class TestGitHelper:
    """Tests for the _git helper function."""

    @patch("subprocess.run")
    def test_git_success(self, mock_run):
        """Test _git with successful execution."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="modified: file.txt\n",
            stderr="",
        )
        result = _git("status")
        assert result.returncode == 0
        assert result.stdout == "modified: file.txt\n"
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_git_failure_no_check(self, mock_run):
        """Test _git with failure but check=False (default)."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "commit", "-m", "test"],
            returncode=1,
            stdout="",
            stderr="nothing to commit",
        )
        result = _git("commit", "-m", "test")
        assert result.returncode == 1
        assert result.stderr == "nothing to commit"
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_git_failure_with_check(self, mock_run):
        """Test _git with failure and check=True logs warning."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "commit", "-m", "test"],
            returncode=1,
            stdout="",
            stderr="fatal: not a git repository",
        )
        with patch("src.utils.git_safety.logger") as mock_logger:
            result = _git("commit", "-m", "test", check=True)
            assert result.returncode == 1
            mock_logger.warning.assert_called_once()
            assert "failed" in mock_logger.warning.call_args[0][0]

    @patch("subprocess.run")
    def test_git_timeout(self, mock_run):
        """Test _git with timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["git", "status"], timeout=30
        )
        with patch("src.utils.git_safety.logger") as mock_logger:
            result = _git("status", timeout=30)
            assert result.returncode == 1
            assert result.stderr == "timeout"
            mock_logger.warning.assert_called_once()
            assert "timed out" in mock_logger.warning.call_args[0][0]

    @patch("subprocess.run")
    def test_git_exception(self, mock_run):
        """Test _git with generic exception."""
        mock_run.side_effect = OSError("Permission denied")
        with patch("src.utils.git_safety.logger") as mock_logger:
            result = _git("status")
            assert result.returncode == 1
            assert "Permission denied" in result.stderr
            mock_logger.warning.assert_called_once()
            assert "error" in mock_logger.warning.call_args[0][0]

    @patch("subprocess.run")
    def test_git_custom_timeout(self, mock_run):
        """Test _git with custom timeout parameter."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )
        _git("status", timeout=60)
        call_args = mock_run.call_args
        assert call_args.kwargs["timeout"] == 60

    @patch("subprocess.run")
    def test_git_environment_variables(self, mock_run):
        """Test _git sets correct environment variables."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )
        _git("status")
        call_args = mock_run.call_args
        env = call_args.kwargs["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_PAGER"] == ""

    @patch("subprocess.run")
    def test_git_multiple_args(self, mock_run):
        """Test _git with multiple arguments."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "commit", "-m", "test message"],
            returncode=0,
            stdout="",
            stderr="",
        )
        _git("commit", "-m", "test message")
        call_args = mock_run.call_args
        assert call_args[0][0] == ["git", "commit", "-m", "test message"]

    @patch("subprocess.run")
    def test_git_stderr_truncation(self, mock_run):
        """Test _git truncates long stderr in warning message."""
        long_stderr = "x" * 300
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=1,
            stdout="",
            stderr=long_stderr,
        )
        with patch("src.utils.git_safety.logger") as mock_logger:
            _git("status", check=True)
            warning_msg = mock_logger.warning.call_args[0][3]
            assert len(warning_msg) <= 200


# ── TestHasChanges ──────────────────────────────────────────────────────────


class TestHasChanges:
    """Tests for the _has_changes function."""

    @patch("src.utils.git_safety._git")
    def test_has_changes_with_output(self, mock_git):
        """Test _has_changes returns True when there are changes."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=" M file.txt\nA new_file.py\n",
            stderr="",
        )
        result = _has_changes()
        assert result is True
        mock_git.assert_called_once_with("status", "--porcelain")

    @patch("src.utils.git_safety._git")
    def test_has_changes_empty(self, mock_git):
        """Test _has_changes returns False when there are no changes."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout="",
            stderr="",
        )
        result = _has_changes()
        assert result is False

    @patch("src.utils.git_safety._git")
    def test_has_changes_whitespace_only(self, mock_git):
        """Test _has_changes returns False for whitespace-only output."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout="   \n\n",
            stderr="",
        )
        result = _has_changes()
        assert result is False


# ── TestCreateTag ───────────────────────────────────────────────────────────


class TestCreateTag:
    """Tests for the _create_tag function."""

    @patch("src.utils.git_safety._git")
    def test_create_tag_success(self, mock_git):
        """Test _create_tag returns True on successful tag creation."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "tag", "archi-checkpoint-1234567890"],
            returncode=0,
            stdout="",
            stderr="",
        )
        result = _create_tag("archi-checkpoint-1234567890")
        assert result is True
        mock_git.assert_called_once_with("tag", "archi-checkpoint-1234567890")

    @patch("src.utils.git_safety._git")
    def test_create_tag_failure(self, mock_git):
        """Test _create_tag returns False on tag creation failure."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "tag", "archi-checkpoint-1234567890"],
            returncode=1,
            stdout="",
            stderr="fatal: tag already exists",
        )
        result = _create_tag("archi-checkpoint-1234567890")
        assert result is False


# ── TestPruneOldTags ────────────────────────────────────────────────────────


class TestPruneOldTags:
    """Tests for the _prune_old_tags function."""

    @patch("src.utils.git_safety._git")
    def test_prune_old_tags_exceeds_max(self, mock_git):
        """Test _prune_old_tags removes tags when count exceeds _MAX_TAGS."""
        tags_output = "\n".join([f"archi-checkpoint-{i}" for i in range(60)])
        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "tag", "--list"],
                returncode=0,
                stdout=tags_output,
                stderr="",
            ),
            # Then calls to delete tags (10 deletions)
            *[
                subprocess.CompletedProcess(
                    args=["git", "tag", "-d", f"archi-checkpoint-{i}"],
                    returncode=0,
                    stdout="",
                    stderr="",
                )
                for i in range(10)
            ],
        ]
        with patch("src.utils.git_safety.logger") as mock_logger:
            _prune_old_tags()
            # Called once to list, then 10 times to delete
            assert mock_git.call_count == 11
            mock_logger.debug.assert_called_once()
            # Check the format string and argument
            debug_call = mock_logger.debug.call_args
            assert debug_call[0][0] == "Pruned %d old checkpoint tags"
            assert debug_call[0][1] == 10

    @patch("src.utils.git_safety._git")
    def test_prune_old_tags_below_max(self, mock_git):
        """Test _prune_old_tags does nothing when tag count <= _MAX_TAGS."""
        tags_output = "\n".join([f"archi-checkpoint-{i}" for i in range(30)])
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "tag", "--list"],
            returncode=0,
            stdout=tags_output,
            stderr="",
        )
        _prune_old_tags()
        # Should only be called once (to list tags)
        mock_git.assert_called_once()

    @patch("src.utils.git_safety._git")
    def test_prune_old_tags_at_max(self, mock_git):
        """Test _prune_old_tags does nothing when tag count equals _MAX_TAGS."""
        tags_output = "\n".join([f"archi-checkpoint-{i}" for i in range(_MAX_TAGS)])
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "tag", "--list"],
            returncode=0,
            stdout=tags_output,
            stderr="",
        )
        _prune_old_tags()
        # Should only be called once (to list tags)
        mock_git.assert_called_once()

    @patch("src.utils.git_safety._git")
    def test_prune_old_tags_git_failure(self, mock_git):
        """Test _prune_old_tags exits early if git tag --list fails."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "tag", "--list"],
            returncode=1,
            stdout="",
            stderr="fatal: not a git repository",
        )
        _prune_old_tags()
        # Should only be called once (to list tags)
        mock_git.assert_called_once()

    @patch("src.utils.git_safety._git")
    def test_prune_old_tags_empty_list(self, mock_git):
        """Test _prune_old_tags with no tags."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "tag", "--list"],
            returncode=0,
            stdout="",
            stderr="",
        )
        _prune_old_tags()
        # Should only be called once (to list tags)
        mock_git.assert_called_once()


# ── TestPreModifyCheckpoint ─────────────────────────────────────────────────


class TestPreModifyCheckpoint:
    """Tests for the pre_modify_checkpoint function."""

    @patch("src.utils.git_safety._git")
    def test_pre_modify_checkpoint_not_a_repo(self, mock_git):
        """Test pre_modify_checkpoint returns None if not in a git repo."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--is-inside-work-tree"],
            returncode=1,
            stdout="",
            stderr="fatal: not a git repository",
        )
        result = pre_modify_checkpoint("modify", "test.txt")
        assert result is None
        mock_git.assert_called_once_with("rev-parse", "--is-inside-work-tree")

    @patch("src.utils.git_safety._prune_old_tags")
    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._create_tag")
    @patch("src.utils.git_safety._git")
    def test_pre_modify_checkpoint_no_changes_tag_success(
        self, mock_git, mock_create_tag, mock_has_changes, mock_prune
    ):
        """Test pre_modify_checkpoint creates tag when no changes exist."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--is-inside-work-tree"],
            returncode=0,
            stdout="true",
            stderr="",
        )
        mock_has_changes.return_value = False
        mock_create_tag.return_value = True

        with patch("src.utils.git_safety.time.time", return_value=1234567890):
            result = pre_modify_checkpoint("modify", "test.txt")

        assert result == "archi-checkpoint-1234567890"
        mock_git.assert_called_once_with("rev-parse", "--is-inside-work-tree")
        mock_create_tag.assert_called_once_with("archi-checkpoint-1234567890")
        mock_prune.assert_called_once()

    @patch("src.utils.git_safety._prune_old_tags")
    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._create_tag")
    @patch("src.utils.git_safety._git")
    def test_pre_modify_checkpoint_has_changes_commit_success(
        self, mock_git, mock_create_tag, mock_has_changes, mock_prune
    ):
        """Test pre_modify_checkpoint commits and creates tag when changes exist."""
        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "rev-parse", "--is-inside-work-tree"],
                returncode=0,
                stdout="true",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "add", "--", "test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "commit", "-m", f"{_COMMIT_PREFIX} checkpoint before modify: test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]
        mock_has_changes.return_value = True
        mock_create_tag.return_value = True

        with patch("src.utils.git_safety.time.time", return_value=1234567890):
            result = pre_modify_checkpoint("modify", "test.txt")

        assert result == "archi-checkpoint-1234567890"
        assert mock_git.call_count == 3
        mock_create_tag.assert_called_once_with("archi-checkpoint-1234567890")
        mock_prune.assert_called_once()

    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._git")
    def test_pre_modify_checkpoint_has_changes_commit_failure(
        self, mock_git, mock_has_changes
    ):
        """Test pre_modify_checkpoint returns None if commit fails."""
        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "rev-parse", "--is-inside-work-tree"],
                returncode=0,
                stdout="true",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "add", "--", "test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "commit", "-m", f"{_COMMIT_PREFIX} checkpoint before modify: test.txt"],
                returncode=1,
                stdout="",
                stderr="nothing to commit",
            ),
        ]
        mock_has_changes.return_value = True

        result = pre_modify_checkpoint("modify", "test.txt")

        assert result is None

    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._create_tag")
    @patch("src.utils.git_safety._git")
    def test_pre_modify_checkpoint_tag_creation_failure(
        self, mock_git, mock_create_tag, mock_has_changes
    ):
        """Test pre_modify_checkpoint returns None if tag creation fails."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--is-inside-work-tree"],
            returncode=0,
            stdout="true",
            stderr="",
        )
        mock_has_changes.return_value = False
        mock_create_tag.return_value = False

        with patch("src.utils.git_safety.time.time", return_value=1234567890):
            result = pre_modify_checkpoint("modify", "test.txt")

        assert result is None

    @patch("src.utils.git_safety._prune_old_tags")
    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._create_tag")
    @patch("src.utils.git_safety._git")
    def test_pre_modify_checkpoint_custom_action(
        self, mock_git, mock_create_tag, mock_has_changes, mock_prune
    ):
        """Test pre_modify_checkpoint with custom action name."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--is-inside-work-tree"],
            returncode=0,
            stdout="true",
            stderr="",
        )
        mock_has_changes.return_value = False
        mock_create_tag.return_value = True

        with patch("src.utils.git_safety.time.time", return_value=1234567890):
            result = pre_modify_checkpoint("delete", "old_file.txt")

        assert result == "archi-checkpoint-1234567890"
        # Verify action name is used but prune_old_tags is also called
        assert mock_git.call_count == 1
        mock_prune.assert_called_once()


# ── TestPostModifyCommit ─────────────────────────────────────────────────────


class TestPostModifyCommit:
    """Tests for the post_modify_commit function."""

    @patch("src.utils.git_safety._has_changes")
    def test_post_modify_commit_no_changes(self, mock_has_changes):
        """Test post_modify_commit returns True when there are no changes."""
        mock_has_changes.return_value = False
        result = post_modify_commit("archi-checkpoint-1234567890", "test.txt")
        assert result is True

    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._git")
    def test_post_modify_commit_changes_success(self, mock_git, mock_has_changes):
        """Test post_modify_commit commits changes successfully."""
        mock_has_changes.return_value = True
        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "add", "--", "test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "commit", "-m", f"{_COMMIT_PREFIX} test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]

        result = post_modify_commit("archi-checkpoint-1234567890", "test.txt")

        assert result is True
        assert mock_git.call_count == 2

    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._git")
    def test_post_modify_commit_changes_failure(self, mock_git, mock_has_changes):
        """Test post_modify_commit returns False if commit fails."""
        mock_has_changes.return_value = True
        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "add", "--", "test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "commit", "-m", f"{_COMMIT_PREFIX} test.txt"],
                returncode=1,
                stdout="",
                stderr="nothing to commit",
            ),
        ]

        result = post_modify_commit("archi-checkpoint-1234567890", "test.txt")

        assert result is False

    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._git")
    def test_post_modify_commit_with_summary(self, mock_git, mock_has_changes):
        """Test post_modify_commit uses summary in commit message."""
        mock_has_changes.return_value = True
        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "add", "--", "test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "commit", "-m", f"{_COMMIT_PREFIX} updated file"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]

        result = post_modify_commit(
            "archi-checkpoint-1234567890", "test.txt", summary="updated file"
        )

        assert result is True
        commit_call = mock_git.call_args_list[1]
        # The commit message is passed as: _git("commit", "-m", msg, "--no-verify")
        # So args are ("commit", "-m", msg, "--no-verify")
        assert "updated file" in commit_call[0][2]

    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._git")
    def test_post_modify_commit_summary_truncation(
        self, mock_git, mock_has_changes
    ):
        """Test post_modify_commit truncates summary at 120 characters."""
        mock_has_changes.return_value = True
        long_summary = "x" * 200
        expected_summary = "x" * 120

        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "add", "--", "test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "commit", "-m", f"{_COMMIT_PREFIX} {expected_summary}"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]

        result = post_modify_commit(
            "archi-checkpoint-1234567890", "test.txt", summary=long_summary
        )

        assert result is True
        commit_call = mock_git.call_args_list[1]
        commit_msg = commit_call[0][2]
        assert expected_summary in commit_msg
        assert "x" * 121 not in commit_msg

    @patch("src.utils.git_safety._has_changes")
    @patch("src.utils.git_safety._git")
    def test_post_modify_commit_no_summary_uses_filepath(
        self, mock_git, mock_has_changes
    ):
        """Test post_modify_commit uses file path when summary is empty."""
        mock_has_changes.return_value = True
        mock_git.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "add", "--", "test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["git", "commit", "-m", f"{_COMMIT_PREFIX} test.txt"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]

        result = post_modify_commit(
            "archi-checkpoint-1234567890", "test.txt", summary=""
        )

        assert result is True
        commit_call = mock_git.call_args_list[1]
        commit_msg = commit_call[0][2]
        assert "test.txt" in commit_msg


# ── TestRollbackLast ────────────────────────────────────────────────────────


class TestRollbackLast:
    """Tests for the rollback_last function."""

    def test_rollback_last_none_tag(self):
        """Test rollback_last returns False when tag is None."""
        result = rollback_last(None)
        assert result is False

    @patch("src.utils.git_safety._git")
    def test_rollback_last_success(self, mock_git):
        """Test rollback_last succeeds with valid tag."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "reset", "--hard", "archi-checkpoint-1234567890"],
            returncode=0,
            stdout="",
            stderr="",
        )

        result = rollback_last("archi-checkpoint-1234567890")

        assert result is True
        mock_git.assert_called_once_with("reset", "--hard", "archi-checkpoint-1234567890")

    @patch("src.utils.git_safety._git")
    def test_rollback_last_failure(self, mock_git):
        """Test rollback_last returns False on git reset failure."""
        mock_git.return_value = subprocess.CompletedProcess(
            args=["git", "reset", "--hard", "invalid-tag"],
            returncode=1,
            stdout="",
            stderr="fatal: reference not found: invalid-tag",
        )

        result = rollback_last("invalid-tag")

        assert result is False

    @patch("src.utils.git_safety._git")
    def test_rollback_last_empty_string_tag(self, mock_git):
        """Test rollback_last returns False for empty string tag."""
        result = rollback_last("")
        assert result is False
        mock_git.assert_not_called()
