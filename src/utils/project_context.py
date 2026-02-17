"""
Project context — loading and updating dynamic project data.

Static identity config (name, role, timezone) stays in config/archi_identity.yaml.
Dynamic project data (active projects, interests, focus areas) lives in
data/project_context.json, which Archi can update at runtime.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

_CONTEXT_FILE = "data/project_context.json"
_IDENTITY_FILE = "config/archi_identity.yaml"


def load() -> Dict[str, Any]:
    """Load project context from data/project_context.json.

    Falls back to extracting from archi_identity.yaml for backward compat.
    """
    path = _base_path() / _CONTEXT_FILE
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:
            logger.warning("Could not load project_context.json: %s", e)
    return _extract_from_identity()


def save(context: Dict[str, Any]) -> bool:
    """Atomically write project context to data/project_context.json."""
    path = _base_path() / _CONTEXT_FILE
    context["last_updated"] = datetime.now().isoformat()
    context.setdefault("version", 1)
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
        logger.info("Project context saved")
        return True
    except Exception as e:
        logger.error("Failed to save project context: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def scan_project_files(project_path: str) -> List[str]:
    """List actual files in a project directory.

    Returns relative names like "supplements.md", "Categories/" for subdirs.
    Scans one level deep to keep it lightweight.
    """
    try:
        root = _base_path() / project_path
        if not root.exists():
            return []
        items = []
        for entry in sorted(root.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                items.append(f"{entry.name}/")
            elif entry.is_file():
                items.append(entry.name)
        return items
    except Exception as e:
        logger.debug("Could not scan %s: %s", project_path, e)
        return []


def _extract_from_identity() -> Dict[str, Any]:
    """Extract project context from legacy archi_identity.yaml."""
    try:
        import yaml
        path = _base_path() / _IDENTITY_FILE
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        uc = data.get("user_context", {})
        return {
            "version": 1,
            "focus_areas": data.get("focus_areas", []),
            "interests": uc.get("interests", []),
            "current_projects": uc.get("current_projects", []),
            "active_projects": uc.get("active_projects", {}),
        }
    except Exception as e:
        logger.warning("Could not extract from identity: %s", e)
        return {}
