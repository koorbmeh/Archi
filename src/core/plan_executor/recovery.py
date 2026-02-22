"""
Crash recovery and task cancellation signals.

Extracted from plan_executor.py (session 73) for SRP compliance.

Cancellation has two modes:
  1. "user_cancel" — single-shot: one user "stop" cancels one task,
     flag is cleared on first read so the next task starts clean.
  2. "shutdown" — sticky: service is shutting down, ALL concurrent
     PlanExecutors must stop.  Flag stays set until explicitly reset
     (only reset by clear_shutdown_flag, called at next service start).
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Crash-recovery state older than this is treated as stale
_STATE_MAX_AGE_HOURS = 24

# ── Task cancellation signal ─────────────────────────────────────────
_cancel_lock = threading.Lock()
_cancel_requested: bool = False
_cancel_message: str = ""
_shutdown_requested: bool = False  # sticky — survives read


def signal_task_cancellation(message: str = "") -> None:
    """Signal running PlanExecutor(s) to stop after their current step.

    If *message* is ``"shutdown"`` or ``"service_shutdown"``, the flag is
    sticky and will be seen by ALL concurrent executors (not just the
    first one to check).  Otherwise it's single-shot for user cancels.
    """
    global _cancel_requested, _cancel_message, _shutdown_requested
    with _cancel_lock:
        _cancel_requested = True
        _cancel_message = message
        if message in ("shutdown", "service_shutdown"):
            _shutdown_requested = True
    logger.info("Task cancellation signalled: %s", message[:80] if message else "(no message)")


def check_and_clear_cancellation() -> Optional[str]:
    """Check if cancellation was requested.

    For user cancels (single-shot): clears the flag so only one executor
    picks it up.  For shutdown: returns the message but leaves the flag
    set so every concurrent executor sees it.
    """
    global _cancel_requested, _cancel_message, _shutdown_requested
    with _cancel_lock:
        if _shutdown_requested:
            # Sticky — don't clear, every executor should see this
            return _cancel_message or "shutdown"
        if _cancel_requested:
            msg = _cancel_message
            _cancel_requested = False
            _cancel_message = ""
            return msg
        return None


def clear_shutdown_flag() -> None:
    """Reset the sticky shutdown flag.  Call at service startup."""
    global _cancel_requested, _cancel_message, _shutdown_requested
    with _cancel_lock:
        _shutdown_requested = False
        _cancel_requested = False
        _cancel_message = ""


# ── Crash recovery state persistence ─────────────────────────────────

def _state_dir() -> Path:
    """Directory for PlanExecutor crash-recovery state."""
    from src.utils.paths import base_path
    d = Path(base_path()) / "data" / "plan_state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_state(
    task_id: str,
    task_description: str,
    goal_context: str,
    steps_taken: List[Dict[str, Any]],
    total_cost: float,
    files_created: List[str],
) -> None:
    """Persist current execution state for crash recovery."""
    if not task_id:
        return
    try:
        state = {
            "task_id": task_id,
            "task_description": task_description,
            "goal_context": goal_context,
            "steps_taken": steps_taken,
            "total_cost": total_cost,
            "files_created": files_created,
            "saved_at": datetime.now().isoformat(),
        }
        path = _state_dir() / f"{task_id}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(str(tmp), str(path))
    except Exception as e:
        logger.debug("State save failed (non-critical): %s", e)


def load_state(task_id: str) -> Optional[Dict[str, Any]]:
    """Load interrupted execution state if it exists and isn't stale."""
    if not task_id:
        return None
    try:
        path = _state_dir() / f"{task_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        # Structural validation — reject corrupt state
        if not isinstance(state, dict) or not isinstance(state.get("steps_taken"), list):
            logger.warning("Corrupt crash-recovery state for '%s', discarding", task_id)
            path.unlink(missing_ok=True)
            return None
        # Check staleness
        saved_at = state.get("saved_at", "")
        if saved_at:
            saved_dt = datetime.fromisoformat(saved_at)
            age_hours = (datetime.now() - saved_dt).total_seconds() / 3600
            if age_hours > _STATE_MAX_AGE_HOURS:
                logger.info(
                    "PlanExecutor: stale state for '%s' (%.1fh old), starting fresh",
                    task_id, age_hours,
                )
                path.unlink(missing_ok=True)
                return None
        return state
    except Exception as e:
        logger.debug("State load failed: %s", e)
    return None


def clear_state(task_id: str) -> None:
    """Remove crash-recovery state after successful completion."""
    if not task_id:
        return
    try:
        path = _state_dir() / f"{task_id}.json"
        if path.exists():
            path.unlink()
    except Exception as e:
        logger.debug("State cleanup failed: %s", e)


def get_interrupted_tasks() -> List[Dict[str, Any]]:
    """List any interrupted tasks that can be resumed.

    Returns list of dicts with task_id, description, steps_completed, saved_at.
    Only returns non-stale entries.
    """
    try:
        interrupted = []
        sd = _state_dir()
        for f in sd.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
                # Check staleness
                saved_at = state.get("saved_at", "")
                if saved_at:
                    saved_dt = datetime.fromisoformat(saved_at)
                    age_hours = (datetime.now() - saved_dt).total_seconds() / 3600
                    if age_hours > _STATE_MAX_AGE_HOURS:
                        continue
                interrupted.append({
                    "task_id": state.get("task_id"),
                    "description": state.get("task_description", ""),
                    "steps_completed": len(state.get("steps_taken", [])),
                    "saved_at": saved_at,
                })
            except Exception:
                pass
        return interrupted
    except Exception:
        return []
