"""
Git safety net for autonomous self-modification.

Provides automatic git checkpoints before and after Archi modifies its
own source code.  If a modification breaks something, ``git revert`` or
``git checkout`` can instantly restore the last known-good state — far
more reliable than individual file backups.

Usage in PlanExecutor::

    from src.utils.git_safety import pre_modify_checkpoint, post_modify_commit, rollback_last

    tag = pre_modify_checkpoint("write_source", "src/tools/new_tool.py")
    # ... perform the modification ...
    if modification_ok:
        post_modify_commit(tag, "src/tools/new_tool.py", "Added new_tool")
    else:
        rollback_last(tag)

Design principles:
- Never blocks on failure (git errors are logged, not raised).
- All operations are non-interactive (no editor, no pager).
- Commits use a machine-readable prefix so they're easy to filter.
- Tags are lightweight and auto-cleaned after 50 accumulate.
"""

import logging
import os
import subprocess
import time
from typing import Optional

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

# Prefix for all auto-checkpoint commits and tags
_COMMIT_PREFIX = "[archi-safety]"
_TAG_PREFIX = "archi-checkpoint-"
_MAX_TAGS = 50  # auto-prune oldest tags beyond this count


def _git(
    *args: str,
    timeout: int = 30,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a git command in the project root.

    Returns the CompletedProcess.  Errors are logged but not raised
    unless ``check=True``.
    """
    cmd = ["git"] + list(args)
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",   # never prompt
        "GIT_PAGER": "",              # no pager
    }
    try:
        result = subprocess.run(
            cmd,
            cwd=base_path(),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0 and check:
            logger.warning(
                "git %s failed (rc=%d): %s",
                args[0], result.returncode, result.stderr.strip()[:200],
            )
        return result
    except subprocess.TimeoutExpired:
        logger.warning("git %s timed out (%ds)", args[0], timeout)
        return subprocess.CompletedProcess(cmd, 1, "", "timeout")
    except Exception as e:
        logger.warning("git %s error: %s", args[0], e)
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def _has_changes() -> bool:
    """Return True if the working tree has any uncommitted changes."""
    result = _git("status", "--porcelain")
    return bool(result.stdout.strip())


def _create_tag(tag_name: str) -> bool:
    """Create a lightweight tag at HEAD.  Returns True on success."""
    result = _git("tag", tag_name)
    return result.returncode == 0


def _prune_old_tags() -> None:
    """Remove oldest archi-checkpoint tags if we have more than _MAX_TAGS."""
    result = _git("tag", "--list", f"{_TAG_PREFIX}*", "--sort=creatordate")
    if result.returncode != 0:
        return
    tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    if len(tags) <= _MAX_TAGS:
        return
    to_remove = tags[: len(tags) - _MAX_TAGS]
    for tag in to_remove:
        _git("tag", "-d", tag)
    logger.debug("Pruned %d old checkpoint tags", len(to_remove))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pre_modify_checkpoint(action: str, file_path: str) -> Optional[str]:
    """Create a git checkpoint BEFORE modifying a source file.

    Commits any outstanding changes (so the checkpoint is clean), then
    tags the commit.  Returns the tag name, or None if git is not
    available or there was nothing to commit and HEAD is already clean.

    Args:
        action: The action type (e.g. "write_source", "edit_file").
        file_path: Relative path of the file about to be modified.

    Returns:
        Tag name string (used later by post_modify_commit / rollback_last),
        or None if checkpointing was skipped.
    """
    # Quick sanity check: is this a git repo?
    result = _git("rev-parse", "--is-inside-work-tree")
    if result.returncode != 0:
        logger.debug("Not a git repo; skipping checkpoint")
        return None

    tag_name = f"{_TAG_PREFIX}{int(time.time())}"

    # If there are outstanding changes, commit them first so the
    # checkpoint captures a clean state.
    if _has_changes():
        _git("add", "-A")
        msg = f"{_COMMIT_PREFIX} checkpoint before {action}: {file_path}"
        result = _git("commit", "-m", msg, "--no-verify")
        if result.returncode != 0:
            logger.warning("Checkpoint commit failed: %s", result.stderr.strip()[:200])
            return None

    # Tag the current HEAD
    if _create_tag(tag_name):
        logger.info(
            "Git checkpoint created: %s (before %s on %s)",
            tag_name, action, file_path,
        )
        _prune_old_tags()
        return tag_name

    return None


def post_modify_commit(
    tag: Optional[str],
    file_path: str,
    summary: str = "",
) -> bool:
    """Commit the modification after it passes all checks.

    Args:
        tag: The tag returned by pre_modify_checkpoint (unused here but
             kept for symmetry; the tag marks the pre-state).
        file_path: Relative path of the modified file.
        summary: Short description of what changed.

    Returns:
        True if the commit succeeded.
    """
    if not _has_changes():
        logger.debug("No changes to commit after modification of %s", file_path)
        return True

    _git("add", "-A")
    desc = summary[:120] if summary else file_path
    msg = f"{_COMMIT_PREFIX} {desc}"
    result = _git("commit", "-m", msg, "--no-verify")

    if result.returncode == 0:
        logger.info("Git committed modification: %s", desc)
        return True

    logger.warning("Post-modify commit failed: %s", result.stderr.strip()[:200])
    return False


def rollback_last(tag: Optional[str]) -> bool:
    """Roll back to the checkpoint tag after a failed modification.

    This does a hard reset to the tagged commit, discarding the broken
    changes.  The file-level backup in PlanExecutor is still the first
    line of defense; this is the nuclear option if something slips through.

    Args:
        tag: The tag returned by pre_modify_checkpoint.

    Returns:
        True if the rollback succeeded.
    """
    if not tag:
        logger.debug("No checkpoint tag; skipping git rollback")
        return False

    logger.warning("Rolling back to git checkpoint: %s", tag)
    result = _git("reset", "--hard", tag)

    if result.returncode == 0:
        logger.info("Git rollback to %s succeeded", tag)
        return True

    logger.error("Git rollback to %s FAILED: %s", tag, result.stderr.strip()[:200])
    return False


def get_recent_checkpoints(limit: int = 10) -> list:
    """List recent archi checkpoint tags (newest first).

    Returns list of dicts with 'tag', 'commit', and 'message' keys.
    Useful for the dashboard or manual recovery.
    """
    result = _git(
        "tag", "--list", f"{_TAG_PREFIX}*",
        "--sort=-creatordate",
        f"--format=%(refname:short) %(objectname:short) %(subject)",
    )
    if result.returncode != 0:
        return []

    checkpoints = []
    for line in result.stdout.splitlines()[:limit]:
        parts = line.strip().split(" ", 2)
        if len(parts) >= 2:
            checkpoints.append({
                "tag": parts[0],
                "commit": parts[1],
                "message": parts[2] if len(parts) > 2 else "",
            })
    return checkpoints
