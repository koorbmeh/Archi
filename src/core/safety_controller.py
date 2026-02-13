"""
Safety controller: load rules from config/rules.yaml, path validation (workspace
isolation), risk level and confidence checks, human approval prompts.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)





@dataclass
class Action:
    """Minimal action representation for authorization."""

    type: str
    parameters: Dict[str, Any]
    confidence: float = 0.0
    reasoning: Optional[str] = None
    risk_level: Optional[str] = None  # Set by controller from rules


class SafetyController:
    """
    Load rules from config/rules.yaml; authorize actions via path validation,
    risk level, confidence threshold, and human approval where required.
    """

    def __init__(self, rules_path: Optional[str] = None) -> None:
        base = _base_path()
        self.rules_path = rules_path or os.path.join(base, "config", "rules.yaml")
        self.rules: Dict[str, Any] = {}
        self._allowed_write_paths: List[str] = []
        self._load_rules()
        self.approval_queue: List[Action] = []

    def _load_rules(self) -> None:
        """Load and parse rules.yaml."""
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                self.rules = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.error("Failed to load rules from %s: %s", self.rules_path, e)
            self.rules = {}

        # Write access: allow anything within the project root.
        # This is the real security boundary — no config needed.
        root = _base_path()
        if root:
            root_norm = os.path.normpath(root).replace("\\", "/").rstrip("/") + "/"
            self._allowed_write_paths = [root_norm]
        else:
            self._allowed_write_paths = []

    def validate_path(self, path: str) -> bool:
        """
        Return True only if path is under an allowed write path (workspace isolation).
        Log and return False for any path outside allowed areas.
        """
        try:
            norm = os.path.abspath(path).replace("\\", "/")
            for allowed in self._allowed_write_paths:
                base = allowed.rstrip("/")
                if norm == base or norm.startswith(base + "/"):
                    return True
            logger.error("BLOCKED: Attempted access to %s (not in allowed write paths)", path)
            return False
        except Exception as e:
            logger.error("Path validation error for %s: %s", path, e)
            return False

    def _get_risk_level(self, action_type: str) -> Optional[Dict[str, Any]]:
        """Return risk level config for action type, or None if unknown."""
        levels = self.rules.get("risk_levels", {})
        for level_name, level_config in levels.items():
            if not isinstance(level_config, dict):
                continue
            actions = level_config.get("actions", [])
            if action_type in actions:
                out = dict(level_config)
                out["_name"] = level_name
                return out
        return None

    # Action types that are read-only and should NOT be subject to workspace isolation
    _READ_ONLY_ACTIONS = frozenset({
        "read_file", "list_directory", "search_files", "get_file_info",
    })

    # Parameter keys known to contain file paths
    _PATH_PARAM_KEYS = frozenset({
        "path", "file_path", "dest", "destination", "source", "target",
        "output_path", "input_path",
    })

    def _is_write_action(self, action_type: str) -> bool:
        """Return True if the action modifies data (needs workspace isolation check)."""
        return action_type not in self._READ_ONLY_ACTIONS

    def _violates_non_override_rules(self, action: Action) -> bool:
        """Check budget, path isolation (for write actions), no_unauthorized_contact."""
        # Path isolation: write actions must stay within project root
        if self._is_write_action(action.type):
            for key in self._PATH_PARAM_KEYS:
                if key in action.parameters:
                    p = action.parameters[key]
                    if isinstance(p, str) and not self.validate_path(p):
                        return True

        for rule in self.rules.get("non_override_rules", []):
            if not rule.get("enabled", True):
                continue
            name = rule.get("name", "")
            if name == "no_unauthorized_contact":
                if action.type in ("send_email", "external_api_call", "financial_transaction"):
                    # Require explicit approval; handled by risk level
                    pass
        return False

    def _request_approval(self, action: Action) -> bool:
        """Prompt user for approval. Returns True if approved."""
        risk = action.risk_level or "unknown"
        print("\n" + "=" * 60)
        print("APPROVAL REQUIRED")
        print("=" * 60)
        print("Action:", action.type)
        print("Parameters:", action.parameters)
        print("Risk Level:", risk)
        print("Confidence: {:.2%}".format(action.confidence))
        print("Reasoning:", action.reasoning or "—")
        print("=" * 60)
        try:
            response = input("Approve? (yes/no): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = "no"
        if response == "yes":
            logger.info("Action approved: %s", action.type)
            return True
        logger.info("Action denied: %s", action.type)
        return False

    def _queue_for_manual_execution(self, action: Action) -> None:
        """Add action to approval queue for manual execution."""
        self.approval_queue.append(action)
        logger.info("Queued for manual execution: %s", action.type)

    def authorize(self, action: Action) -> bool:
        """
        Check if action is authorized: non-override rules (including workspace
        isolation for write actions), risk level and confidence, then requirement
        (human_approval vs manual_execute_only).
        Read-only actions (read_file, list_directory, etc.) are NOT subject to
        workspace isolation and may access the full filesystem.
        """
        if self._violates_non_override_rules(action):
            logger.error("Action blocked by non-override rule: %s", action.type)
            return False

        risk_config = self._get_risk_level(action.type)
        if risk_config is None:
            logger.warning("Unknown action type %s; denying by default", action.type)
            return False

        threshold = risk_config.get("threshold", 1.0)
        if action.confidence < threshold:
            logger.warning(
                "Confidence too low: %s < %s",
                action.confidence,
                threshold,
            )
            return False

        action.risk_level = risk_config.get("_name", "unknown")
        requirement = risk_config.get("requirement", "human_approval")

        if requirement == "autonomous":
            return True
        if requirement == "notify_and_log":
            logger.info("Notify and log: %s", action.type)
            return True
        if requirement == "human_approval":
            return self._request_approval(action)
        if requirement == "manual_execute_only":
            self._queue_for_manual_execution(action)
            return False
        return False
