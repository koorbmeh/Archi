"""
Centralised configuration loader for Archi.

Loads values from config/rules.yaml and config/heartbeat.yaml once,
then exposes them through simple accessor functions so that no module
needs to hard-code magic numbers or duplicate YAML-loading logic.

Usage:
    from src.utils.config import get_monitoring, get_ports, get_browser_config

Single source of truth — if you need a threshold, port, or timeout,
add it to the relevant YAML file and expose it here.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.utils.paths import base_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal cache
# ---------------------------------------------------------------------------
_rules_cache: Optional[Dict[str, Any]] = None
_heartbeat_cache: Optional[Dict[str, Any]] = None


def _load_yaml(filename: str) -> Dict[str, Any]:
    """Load a YAML file from config/ and return as dict (empty on failure)."""
    path = os.path.join(base_path(), "config", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.debug("Could not load %s: %s", path, e)
        return {}


def _rules() -> Dict[str, Any]:
    """Return cached rules.yaml contents."""
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = _load_yaml("rules.yaml")
    return _rules_cache


def _heartbeat() -> Dict[str, Any]:
    """Return cached heartbeat.yaml contents."""
    global _heartbeat_cache
    if _heartbeat_cache is None:
        _heartbeat_cache = _load_yaml("heartbeat.yaml")
    return _heartbeat_cache


def reload() -> None:
    """Force re-read of all config files (useful after editing YAML)."""
    global _rules_cache, _heartbeat_cache
    _rules_cache = None
    _heartbeat_cache = None


# ---------------------------------------------------------------------------
# Monitoring thresholds
# ---------------------------------------------------------------------------

# Defaults match the historical hard-coded values so that Archi behaves
# identically even if the YAML section is missing.
_MONITORING_DEFAULTS: Dict[str, Any] = {
    "cpu_threshold": 80,
    "memory_threshold": 90,
    "temp_threshold": 80,
    "disk_threshold": 90,
    "budget_warning_pct": 80,
}


def get_monitoring() -> Dict[str, Any]:
    """Return the ``monitoring`` section of rules.yaml with defaults."""
    section = _rules().get("monitoring", {}) or {}
    merged = dict(_MONITORING_DEFAULTS)
    merged.update({k: v for k, v in section.items() if v is not None})
    return merged


# ---------------------------------------------------------------------------
# Web service ports
# ---------------------------------------------------------------------------

_PORT_DEFAULTS: Dict[str, int] = {
    "dashboard": 5000,
    "web_chat": 5001,
}


def get_ports() -> Dict[str, int]:
    """Return the ``ports`` section of rules.yaml with defaults."""
    section = _rules().get("ports", {}) or {}
    merged = dict(_PORT_DEFAULTS)
    merged.update({k: int(v) for k, v in section.items() if v is not None})
    return merged


# ---------------------------------------------------------------------------
# Browser automation
# ---------------------------------------------------------------------------

_BROWSER_DEFAULTS: Dict[str, int] = {
    "default_timeout_ms": 5000,
    "navigation_timeout_ms": 30000,
}


def get_browser_config() -> Dict[str, int]:
    """Return the ``browser`` section of rules.yaml with defaults."""
    section = _rules().get("browser", {}) or {}
    merged = dict(_BROWSER_DEFAULTS)
    merged.update({k: int(v) for k, v in section.items() if v is not None})
    return merged
