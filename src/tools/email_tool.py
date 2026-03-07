"""
Email tool for Archi — send, check, and search inbox.

Wraps EmailClient for use by PlanExecutor and action_dispatcher.
Lazy-initializes: EmailClient is created on first use from env config.
Logs all email activity to logs/email_log.jsonl for transparency.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_client_instance = None
_client_lock = threading.Lock()


def _get_client():
    """Lazy-load EmailClient from env config. Returns None if not configured."""
    global _client_instance
    if _client_instance is not None:
        return _client_instance
    with _client_lock:
        if _client_instance is None:
            from src.utils.config import get_email_config
            address, password = get_email_config()
            if not address or not password:
                logger.debug("Email not configured (missing ARCHI_EMAIL_ADDRESS or ARCHI_EMAIL_APP_PASSWORD)")
                return None
            from src.utils.email_client import EmailClient
            _client_instance = EmailClient(address, password)
            logger.info("EmailClient initialized for %s", address)
    return _client_instance


def _log_email_activity(activity_type: str, details: Dict) -> None:
    """Append to logs/email_log.jsonl for transparency."""
    try:
        from src.utils.paths import base_path
        log_dir = os.path.join(base_path(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "email_log.jsonl")
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": activity_type,
            **details,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug("Failed to log email activity: %s", e)


def send_email(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Send an email. Returns {success, message} or {success, error}."""
    client = _get_client()
    if not client:
        return {"success": False, "error": "Email not configured. Set ARCHI_EMAIL_ADDRESS and ARCHI_EMAIL_APP_PASSWORD in .env."}

    result = client.send(to, subject, body)
    _log_email_activity("send", {
        "to": to,
        "subject": subject[:80],
        "success": result.get("success", False),
        "error": result.get("error"),
    })
    return result


def check_inbox(max_count: int = 5, unread_only: bool = True) -> Dict[str, Any]:
    """Check recent inbox. Returns {success, count, messages}."""
    client = _get_client()
    if not client:
        return {"success": False, "error": "Email not configured. Set ARCHI_EMAIL_ADDRESS and ARCHI_EMAIL_APP_PASSWORD in .env."}

    result = client.read_inbox(max_count=max_count, unread_only=unread_only)
    _log_email_activity("check_inbox", {
        "max_count": max_count,
        "unread_only": unread_only,
        "success": result.get("success", False),
        "count": result.get("count", 0),
    })
    return result


def search_inbox(query: str, max_count: int = 5) -> Dict[str, Any]:
    """Search inbox by IMAP query. Returns {success, count, messages}."""
    client = _get_client()
    if not client:
        return {"success": False, "error": "Email not configured. Set ARCHI_EMAIL_ADDRESS and ARCHI_EMAIL_APP_PASSWORD in .env."}

    result = client.search(query, max_count=max_count)
    _log_email_activity("search_inbox", {
        "query": query[:80],
        "max_count": max_count,
        "success": result.get("success", False),
        "count": result.get("count", 0),
    })
    return result


def is_configured() -> bool:
    """Check if email credentials are available."""
    from src.utils.config import get_email_config
    address, password = get_email_config()
    return bool(address and password)


def _reset_for_testing() -> None:
    """Clear singleton — for test isolation only."""
    global _client_instance
    with _client_lock:
        _client_instance = None
