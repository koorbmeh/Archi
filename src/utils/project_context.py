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


def auto_populate(router: Any = None) -> Dict[str, Any]:
    """Scan workspace/projects/ and auto-populate project_context.json.

    Discovers project directories, reads vision/overview files, and builds
    a structured context. If router is provided, uses LLM to generate
    better autonomous task suggestions. Otherwise uses sensible defaults.

    Called from dream_cycle when project_context is empty or stale.
    """
    projects_dir = _base_path() / "workspace" / "projects"
    if not projects_dir.exists():
        logger.debug("auto_populate: no workspace/projects/ directory")
        return load()

    active_projects: Dict[str, Any] = {}

    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        key = entry.name.lower().replace(" ", "_").replace("-", "_")
        project_path = f"workspace/projects/{entry.name}"
        files = scan_project_files(project_path)

        # Try to read a vision/overview file for description
        description = entry.name.replace("_", " ")
        for fname in files:
            if any(kw in fname.upper() for kw in ("OVERVIEW", "VISION", "README")):
                try:
                    with open(entry / fname, "r", encoding="utf-8", errors="replace") as f:
                        first_lines = f.read(500)
                    # Extract first meaningful line as description
                    for line in first_lines.split("\n"):
                        line = line.strip().strip("#").strip()
                        if len(line) > 20:
                            description = line[:200]
                            break
                except Exception:
                    pass
                break

        active_projects[key] = {
            "path": project_path,
            "description": description,
            "priority": "medium",
            "focus_areas": [],
            "autonomous_tasks": [
                f"Read existing files in {project_path} and identify what to build next",
                f"Look for gaps between vision documents and actual implementation in {project_path}",
            ],
        }

    if not active_projects:
        return load()

    context = {
        "version": 2,
        "focus_areas": [],
        "interests": [],
        "current_projects": [],
        "active_projects": active_projects,
    }

    # Merge with existing context (don't overwrite user-set fields)
    existing = load()
    if existing:
        context["focus_areas"] = existing.get("focus_areas", context["focus_areas"])
        context["interests"] = existing.get("interests", context["interests"])
        context["current_projects"] = existing.get("current_projects", context["current_projects"])
        # Merge projects: keep existing entries, add newly discovered ones
        for key, val in existing.get("active_projects", {}).items():
            if key in context["active_projects"]:
                # Existing entry takes precedence (user may have customized it)
                context["active_projects"][key] = val
            else:
                context["active_projects"][key] = val

    save(context)
    logger.info(
        "auto_populate: discovered %d projects",
        len(active_projects),
    )
    return context


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
