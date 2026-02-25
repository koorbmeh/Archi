"""
Skill System — Registry, loader, and executor for self-extending skills.

Skills are reusable Python modules stored in data/skills/<name>/ with a
standardized interface:
    def execute(params: dict) -> dict

Each skill directory contains:
    SKILL.json   — Metadata manifest (name, description, schemas, risk_level)
    skill.py     — Implementation with execute() function
    README.md    — Optional documentation

The SkillRegistry is a singleton that:
    - Discovers and loads skills from data/skills/
    - Validates skills before registration (via SkillValidator)
    - Executes skills with timeout and error handling
    - Tracks execution metrics in the LearningSystem
"""

import importlib.util
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.skill_validator import SkillValidator, ValidationResult

logger = logging.getLogger(__name__)

# Execution timeout for individual skill runs.
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass
class SkillManifest:
    """Parsed SKILL.json metadata for a skill."""
    name: str
    version: str
    description: str
    author: str = "Archi"
    tags: List[str] = field(default_factory=list)
    risk_level: str = "L2_MEDIUM"
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    origin: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillManifest":
        interface = data.get("interface", {})
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", "Archi"),
            tags=data.get("tags", []),
            risk_level=data.get("risk_level", "L2_MEDIUM"),
            input_schema=interface.get("input_schema", {}),
            output_schema=interface.get("output_schema", {}),
            origin=data.get("origin", {}),
            dependencies=data.get("dependencies", []),
            created_at=data.get("created_at", ""),
        )


@dataclass
class LoadedSkill:
    """A validated, loaded skill ready for execution."""
    manifest: SkillManifest
    path: Path
    _module: Any = None  # Lazy-loaded Python module
    _module_lock: threading.Lock = field(default_factory=threading.Lock)
    invocations: int = 0
    successes: int = 0
    failures: int = 0
    total_execution_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.invocations == 0:
            return 0.0
        return self.successes / self.invocations

    @property
    def avg_execution_ms(self) -> float:
        if self.invocations == 0:
            return 0.0
        return self.total_execution_ms / self.invocations

    def get_module(self):
        """Lazy-load the skill module on first use."""
        if self._module is not None:
            return self._module
        with self._module_lock:
            if self._module is None:
                skill_file = self.path / "skill.py"
                spec = importlib.util.spec_from_file_location(
                    f"archi_skill_{self.manifest.name}",
                    str(skill_file),
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._module = module
        return self._module


class SkillRegistry:
    """Registry of all available skills.

    Thread-safe singleton. Discovers skills from data/skills/, validates
    them, and provides execution with timeout and metric tracking.
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        self._lock = threading.Lock()
        self._skills: Dict[str, LoadedSkill] = {}
        self._validator = SkillValidator()

        if skills_dir:
            self._skills_dir = skills_dir
        else:
            try:
                from src.utils.paths import base_path
                self._skills_dir = Path(base_path()) / "data" / "skills"
            except ImportError:
                self._skills_dir = Path("data") / "skills"

        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self.load_all_skills()

    def load_all_skills(self) -> int:
        """Scan data/skills/ and load all valid skills. Returns count loaded."""
        loaded = 0
        if not self._skills_dir.is_dir():
            return 0

        for entry in self._skills_dir.iterdir():
            if entry.is_dir() and (entry / "SKILL.json").is_file():
                try:
                    if self.register_skill(str(entry)):
                        loaded += 1
                except Exception as e:
                    logger.warning("Failed to load skill from %s: %s", entry.name, e)

        if loaded:
            logger.info("Loaded %d skills from %s", loaded, self._skills_dir)
        return loaded

    def register_skill(self, skill_path: str) -> bool:
        """Validate and register a single skill. Returns True on success."""
        path = Path(skill_path)

        # Validate
        result = self._validator.validate_skill_directory(skill_path)
        if not result.valid:
            logger.warning(
                "Skill validation failed for %s: %s",
                path.name, "; ".join(result.issues),
            )
            return False

        if result.warnings:
            for warning in result.warnings:
                logger.debug("Skill %s warning: %s", path.name, warning)

        # Load manifest
        try:
            manifest_data = json.loads(
                (path / "SKILL.json").read_text(encoding="utf-8")
            )
            manifest = SkillManifest.from_dict(manifest_data)
        except Exception as e:
            logger.warning("Failed to parse manifest for %s: %s", path.name, e)
            return False

        skill = LoadedSkill(manifest=manifest, path=path)

        with self._lock:
            self._skills[manifest.name] = skill

        logger.debug("Registered skill: %s (v%s)", manifest.name, manifest.version)
        return True

    def execute_skill(
        self,
        skill_name: str,
        params: Dict[str, Any],
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        learning_system: Any = None,
    ) -> Dict[str, Any]:
        """Execute a skill by name with timeout and metric tracking.

        Args:
            skill_name: Name of the skill to execute.
            params: Parameters to pass to skill.execute().
            timeout_seconds: Max execution time.
            learning_system: Optional LearningSystem for outcome recording.

        Returns:
            Dict with 'success' and skill-specific fields.
        """
        with self._lock:
            skill = self._skills.get(skill_name)
        if not skill:
            return {"success": False, "error": f"Skill not found: {skill_name}"}

        start_ms = time.monotonic() * 1000

        try:
            module = skill.get_module()
            if not hasattr(module, "execute"):
                return {"success": False, "error": f"Skill '{skill_name}' has no execute() function"}

            # Run with timeout using a thread
            result_container: Dict[str, Any] = {}
            error_container: List[Exception] = []

            def _run():
                try:
                    result_container.update(module.execute(params))
                except Exception as e:
                    error_container.append(e)

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            thread.join(timeout=timeout_seconds)

            if thread.is_alive():
                elapsed_ms = time.monotonic() * 1000 - start_ms
                skill.invocations += 1
                skill.failures += 1
                skill.total_execution_ms += elapsed_ms
                return {
                    "success": False,
                    "error": f"Skill '{skill_name}' timed out after {timeout_seconds}s",
                }

            if error_container:
                elapsed_ms = time.monotonic() * 1000 - start_ms
                skill.invocations += 1
                skill.failures += 1
                skill.total_execution_ms += elapsed_ms
                error = error_container[0]
                logger.warning("Skill '%s' raised exception: %s", skill_name, error)
                if learning_system:
                    try:
                        learning_system.record_failure(
                            context=f"Skill execution: {skill_name}",
                            action=f"skill_{skill_name}",
                            outcome=str(error),
                            lesson=f"Skill '{skill_name}' failed with params: {str(params)[:200]}",
                        )
                    except Exception:
                        pass
                return {"success": False, "error": f"Skill error: {error}"}

            # Success
            elapsed_ms = time.monotonic() * 1000 - start_ms
            skill.invocations += 1
            success = result_container.get("success", True)
            if success:
                skill.successes += 1
            else:
                skill.failures += 1
            skill.total_execution_ms += elapsed_ms

            if learning_system and success:
                try:
                    learning_system.record_action_outcome(f"skill_{skill_name}", True)
                except Exception:
                    pass

            result_container.setdefault("success", True)
            return result_container

        except Exception as e:
            elapsed_ms = time.monotonic() * 1000 - start_ms
            skill.invocations += 1
            skill.failures += 1
            skill.total_execution_ms += elapsed_ms
            logger.error("Skill execution failed for '%s': %s", skill_name, e)
            return {"success": False, "error": str(e)}

    def get_available_skills(self) -> List[str]:
        """Return sorted list of available skill names."""
        with self._lock:
            return sorted(self._skills.keys())

    def get_skill_info(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Return metadata dict for a skill, or None."""
        with self._lock:
            skill = self._skills.get(skill_name)
        if not skill:
            return None
        return {
            "name": skill.manifest.name,
            "version": skill.manifest.version,
            "description": skill.manifest.description,
            "author": skill.manifest.author,
            "tags": skill.manifest.tags,
            "risk_level": skill.manifest.risk_level,
            "input_schema": skill.manifest.input_schema,
            "invocations": skill.invocations,
            "success_rate": f"{skill.success_rate:.0%}",
            "avg_execution_ms": f"{skill.avg_execution_ms:.0f}",
        }

    def get_skill_inventory(self, limit: int = 5) -> str:
        """Return a compact summary of available skills for prompt injection.

        Returns empty string if no skills are registered.
        """
        with self._lock:
            if not self._skills:
                return ""
            skills = list(self._skills.values())

        lines = ["Available custom skills:"]
        for skill in skills[:limit]:
            rate = f" ({skill.success_rate:.0%})" if skill.invocations > 0 else ""
            desc = skill.manifest.description[:60]
            lines.append(f"  - skill_{skill.manifest.name}{rate}: {desc}")

        if len(skills) > limit:
            lines.append(f"  ... and {len(skills) - limit} more")

        return "\n".join(lines)

    def unregister_skill(self, skill_name: str) -> bool:
        """Remove a skill from the registry. Returns True if found."""
        with self._lock:
            return self._skills.pop(skill_name, None) is not None


# ── Singleton accessor ───────────────────────────────────────────────

_shared_registry: Optional[SkillRegistry] = None
_shared_lock = threading.Lock()


def get_shared_skill_registry() -> SkillRegistry:
    """Return the shared SkillRegistry singleton, creating on first call.

    Thread-safe.
    """
    global _shared_registry
    if _shared_registry is not None:
        return _shared_registry
    with _shared_lock:
        if _shared_registry is None:
            _shared_registry = SkillRegistry()
    return _shared_registry


def _reset_for_testing() -> None:
    """Clear the singleton — for test isolation only."""
    global _shared_registry
    with _shared_lock:
        _shared_registry = None
