"""
Structured logging for Archi: JSONL action logs and separate error logs.
Auto-rotates by date. Required fields per plan: timestamp, action_type, parameters,
model_used, confidence, cost_usd, result, duration_ms.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Default base path: ARCHI_ROOT or repo root (directory containing 'config')
def _base_path() -> str:
    base = os.environ.get("ARCHI_ROOT")
    if base:
        return os.path.normpath(base)
    # Infer repo root: from this file, go up until we see 'config'
    cur = Path(__file__).resolve().parent
    for _ in range(5):
        if (cur / "config").is_dir():
            return str(cur)
        cur = cur.parent
    return os.getcwd()


class ActionLogger:
    """Append-only JSONL action log and separate error log."""

    def __init__(self, base_path: Optional[str] = None) -> None:
        self.base_path = base_path or _base_path()
        self._logs_dir = os.path.join(self.base_path, "logs")
        self._actions_dir = os.path.join(self._logs_dir, "actions")
        self._errors_dir = os.path.join(self._logs_dir, "errors")
        self._ensure_dirs()
        self._current_date: Optional[str] = None
        self._current_action_file: Optional[Any] = None
        self._error_handler: Optional[logging.FileHandler] = None
        self._setup_error_logger()

    def _ensure_dirs(self) -> None:
        """Create logs/actions and logs/errors if they do not exist."""
        try:
            os.makedirs(self._actions_dir, exist_ok=True)
            os.makedirs(self._errors_dir, exist_ok=True)
        except OSError as e:
            logging.error("Failed to create log directories: %s", e)
            raise

    def _setup_error_logger(self) -> None:
        """Configure root logger to also write to logs/errors/YYYY-MM-DD.log."""
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            error_file = os.path.join(self._errors_dir, f"{today}.log")
            self._error_handler = logging.FileHandler(error_file, encoding="utf-8")
            self._error_handler.setLevel(logging.DEBUG)
            fmt = logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            )
            self._error_handler.setFormatter(fmt)
            logging.getLogger().addHandler(self._error_handler)
        except OSError as e:
            logging.error("Failed to set up error log file: %s", e)

    def _action_file(self) -> Any:
        """Return open file for today's action log (JSONL). Rotates by date."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._current_date != today:
            if self._current_action_file is not None:
                try:
                    self._current_action_file.close()
                except OSError:
                    pass
                self._current_action_file = None
            self._current_date = today
        if self._current_action_file is None:
            path = os.path.join(self._actions_dir, f"{today}.jsonl")
            self._current_action_file = open(path, "a", encoding="utf-8")
        return self._current_action_file

    def log_action(
        self,
        *,
        action_type: str,
        parameters: Optional[dict] = None,
        model_used: Optional[str] = None,
        confidence: Optional[float] = None,
        cost_usd: Optional[float] = None,
        result: str = "success",
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """
        Append one JSONL record to logs/actions/YYYY-MM-DD.jsonl.
        Required fields per plan: timestamp, action_type, parameters, model_used,
        confidence, cost_usd, result, duration_ms.
        """
        entry = {
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "action_type": action_type,
            "parameters": parameters if parameters is not None else {},
            "model_used": model_used or "local",
            "confidence": confidence if confidence is not None else 0.0,
            "cost_usd": cost_usd if cost_usd is not None else 0.0,
            "result": result,
            "duration_ms": duration_ms,
            "error": error,
        }
        entry.update(extra)
        # Remove None values for cleaner JSON
        entry = {k: v for k, v in entry.items() if v is not None}
        try:
            f = self._action_file()
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
        except OSError as e:
            logging.error("Failed to write action log: %s", e)

    def close(self) -> None:
        """Close action log file and remove error file handler."""
        if self._current_action_file is not None:
            try:
                self._current_action_file.close()
            except OSError:
                pass
            self._current_action_file = None
        self._current_date = None
        if self._error_handler is not None:
            logging.getLogger().removeHandler(self._error_handler)
            self._error_handler = None
