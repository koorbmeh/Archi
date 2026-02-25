"""
Skill Creator — Pipeline for creating new skills from requests or patterns.

Two creation paths:
1. Explicit: User says "learn how to do X" → create_skill_from_request()
2. Autonomous: Dream cycle detects pattern → create_skill_from_pattern()

Both paths:
- Generate skill code via the model
- Validate with SkillValidator
- Write files to data/skills/<name>/
- Register in the SkillRegistry
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.skill_validator import SkillValidator

logger = logging.getLogger(__name__)


@dataclass
class SkillProposal:
    """A proposed skill before finalization."""
    name: str
    description: str
    code: str = ""
    manifest: Dict[str, Any] = field(default_factory=dict)
    readme: str = ""
    confidence: float = 0.0
    source: str = "user_request"  # "user_request" or "pattern_detection"
    pattern_description: str = ""
    from_experience_ids: List[int] = field(default_factory=list)


class SkillCreator:
    """Creates new skills from user requests or detected patterns."""

    def __init__(self, skills_dir: Optional[Path] = None):
        self._validator = SkillValidator()
        if skills_dir:
            self._skills_dir = skills_dir
        else:
            try:
                from src.utils.paths import base_path
                self._skills_dir = Path(base_path()) / "data" / "skills"
            except ImportError:
                self._skills_dir = Path("data") / "skills"

    def create_skill_from_request(
        self,
        description: str,
        model: Any,
        example_execution: Optional[Dict[str, Any]] = None,
    ) -> Optional[SkillProposal]:
        """Create a skill from an explicit user request.

        Args:
            description: What the skill should do (e.g., "summarize web pages").
            model: Model with generate(prompt, max_tokens, temperature) -> {text}.
            example_execution: Optional example of a past execution to learn from.

        Returns:
            SkillProposal if generation succeeded, None on failure.
        """
        # Generate a clean name from description
        name = self._name_from_description(description)

        # Build the generation prompt
        example_section = ""
        if example_execution:
            example_section = f"""
Here is an example of a past execution that this skill should generalize:
Input: {json.dumps(example_execution.get('input', {}), indent=2)[:500]}
Output: {json.dumps(example_execution.get('output', {}), indent=2)[:500]}
"""

        prompt = f"""Create a reusable Python skill for Archi (an AI agent).

TASK: {description}
{example_section}
Requirements:
1. The skill must have a single entry point: def execute(params: dict) -> dict
2. The execute() function must ALWAYS return a dict with at least {{"success": True/False}}
3. Include error handling (try/except) that returns {{"success": False, "error": "message"}}
4. Use only standard library imports (os.path, json, re, pathlib, datetime, etc.)
5. Do NOT import: subprocess, socket, eval, exec, pickle, paramiko, ctypes
6. Keep the code focused and under 200 lines
7. Include a docstring explaining what the skill does

Return ONLY the Python code, no markdown fences, no explanation:

def execute(params: dict) -> dict:
    ..."""

        try:
            response = model.generate(prompt, max_tokens=1500, temperature=0.3)
            code = response.get("text", "").strip()

            # Clean up: remove markdown fences if present
            code = self._clean_code(code)

            if not code:
                logger.warning("Model returned empty code for skill: %s", description)
                return None

            # Validate the generated code
            validation = self._validator.validate_code(code)
            if not validation.valid:
                logger.warning(
                    "Generated skill code failed validation: %s",
                    "; ".join(validation.issues),
                )
                # Try one retry with the issues as feedback
                code = self._retry_generation(model, description, code, validation.issues)
                if not code:
                    return None

            # Build manifest
            manifest = self._build_manifest(name, description, model)

            # Build README
            readme = self._build_readme(name, description, manifest)

            return SkillProposal(
                name=name,
                description=description,
                code=code,
                manifest=manifest,
                readme=readme,
                confidence=0.8,
                source="user_request",
            )

        except Exception as e:
            logger.error("Skill creation failed for '%s': %s", description, e)
            return None

    def create_skill_from_pattern(
        self,
        pattern: str,
        experiences: List[Dict[str, Any]],
        model: Any,
    ) -> Optional[SkillProposal]:
        """Create a skill from a detected repeated pattern.

        Args:
            pattern: Description of the detected pattern.
            experiences: The experiences that form the pattern.
            model: Model for code generation.

        Returns:
            SkillProposal if generation succeeded, None on failure.
        """
        # Summarize the experiences for context
        exp_summary = "\n".join(
            f"- Context: {e.get('context', '')[:100]}, "
            f"Action: {e.get('action', '')[:60]}, "
            f"Outcome: {e.get('outcome', '')[:100]}"
            for e in experiences[:5]
        )

        description = f"Automated skill from pattern: {pattern}"
        name = self._name_from_description(pattern)

        prompt = f"""Create a reusable Python skill that generalizes this repeated pattern:

PATTERN: {pattern}

Past executions of this pattern:
{exp_summary}

Requirements:
1. Entry point: def execute(params: dict) -> dict
2. Always return {{"success": True/False, ...}}
3. Include error handling with try/except
4. Use only standard library imports
5. Do NOT import: subprocess, socket, eval, exec, pickle
6. Generalize the pattern — don't hardcode specific values from the examples
7. Accept parameters for anything that varied across the examples

Return ONLY the Python code:"""

        try:
            response = model.generate(prompt, max_tokens=1500, temperature=0.3)
            code = self._clean_code(response.get("text", "").strip())

            if not code:
                return None

            validation = self._validator.validate_code(code)
            if not validation.valid:
                code = self._retry_generation(model, description, code, validation.issues)
                if not code:
                    return None

            manifest = self._build_manifest(name, description, model)
            manifest["origin"] = {
                "source": "pattern_detection",
                "pattern_description": pattern,
                "experience_count": len(experiences),
            }

            readme = self._build_readme(name, description, manifest)

            return SkillProposal(
                name=name,
                description=description,
                code=code,
                manifest=manifest,
                readme=readme,
                confidence=0.6,
                source="pattern_detection",
                pattern_description=pattern,
            )

        except Exception as e:
            logger.error("Pattern-based skill creation failed: %s", e)
            return None

    def finalize_skill(self, proposal: SkillProposal) -> bool:
        """Write skill files to data/skills/<name>/ and register.

        Returns True on success.
        """
        skill_dir = self._skills_dir / proposal.name
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)

            # Write skill.py
            (skill_dir / "skill.py").write_text(proposal.code, encoding="utf-8")

            # Write SKILL.json
            (skill_dir / "SKILL.json").write_text(
                json.dumps(proposal.manifest, indent=2),
                encoding="utf-8",
            )

            # Write README.md
            (skill_dir / "README.md").write_text(proposal.readme, encoding="utf-8")

            # Write __init__.py for importability
            (skill_dir / "__init__.py").write_text(
                f'"""Archi skill: {proposal.name}"""\n',
                encoding="utf-8",
            )

            # Final validation of the written files
            final_check = self._validator.validate_skill_directory(str(skill_dir))
            if not final_check.valid:
                logger.error(
                    "Finalized skill failed validation: %s",
                    "; ".join(final_check.issues),
                )
                # Clean up
                import shutil
                shutil.rmtree(skill_dir, ignore_errors=True)
                return False

            # Register in the shared registry
            try:
                from src.core.skill_system import get_shared_skill_registry
                registry = get_shared_skill_registry()
                registry.register_skill(str(skill_dir))
            except Exception as e:
                logger.warning("Skill written but registry update failed: %s", e)
                # Files are still on disk — will be picked up on next load

            logger.info("Skill '%s' created at %s", proposal.name, skill_dir)
            return True

        except Exception as e:
            logger.error("Failed to finalize skill '%s': %s", proposal.name, e)
            return False

    # -- Internal helpers --------------------------------------------------

    def _name_from_description(self, description: str) -> str:
        """Generate a clean skill name from a description."""
        # Extract key words, lowercase, join with underscore
        words = re.sub(r"[^a-zA-Z0-9\s]", "", description.lower()).split()
        # Take first 4 meaningful words
        skip = {"a", "an", "the", "to", "for", "of", "in", "on", "how", "do", "learn"}
        meaningful = [w for w in words if w not in skip][:4]
        name = "_".join(meaningful) if meaningful else "custom_skill"
        # Ensure valid Python identifier
        if not name[0].isalpha():
            name = "skill_" + name
        return name

    def _clean_code(self, code: str) -> str:
        """Remove markdown fences and cleanup model-generated code."""
        # Remove ```python ... ``` wrappers
        if "```" in code:
            lines = code.split("\n")
            cleaned = []
            in_fence = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if not in_fence or (in_fence and not line.strip().startswith("```")):
                    cleaned.append(line)
            code = "\n".join(cleaned)

        # Ensure the code starts with a docstring or import or def
        lines = code.strip().split("\n")
        start_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and (
                stripped.startswith(("import ", "from ", "def ", '"""', "'''", "#"))
            ):
                start_idx = i
                break
        code = "\n".join(lines[start_idx:])

        return code.strip()

    def _retry_generation(
        self,
        model: Any,
        description: str,
        failed_code: str,
        issues: List[str],
    ) -> Optional[str]:
        """Retry code generation with validation feedback."""
        issues_text = "\n".join(f"- {i}" for i in issues)
        prompt = f"""The following Python skill code has validation issues. Fix them.

TASK: {description}

ISSUES:
{issues_text}

ORIGINAL CODE:
{failed_code}

Return ONLY the fixed Python code, no markdown fences:"""

        try:
            response = model.generate(prompt, max_tokens=1500, temperature=0.2)
            code = self._clean_code(response.get("text", "").strip())
            if not code:
                return None

            validation = self._validator.validate_code(code)
            if not validation.valid:
                logger.warning("Retry also failed validation: %s", validation.issues)
                return None
            return code
        except Exception as e:
            logger.error("Retry generation failed: %s", e)
            return None

    def _build_manifest(
        self,
        name: str,
        description: str,
        model: Any,
    ) -> Dict[str, Any]:
        """Build a SKILL.json manifest for a new skill."""
        return {
            "name": name,
            "version": "1.0.0",
            "description": description,
            "author": "Archi (self-taught)",
            "created_at": datetime.now().isoformat(),
            "tags": [],
            "risk_level": "L2_MEDIUM",
            "interface": {
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "description": "Parameters vary by use case",
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "error": {"type": "string"},
                    },
                },
            },
            "origin": {
                "source": "user_request",
            },
            "dependencies": [],
        }

    def _build_readme(
        self,
        name: str,
        description: str,
        manifest: Dict[str, Any],
    ) -> str:
        """Build a README.md for a new skill."""
        return f"""# {name}

{description}

## Usage

```python
from data.skills.{name}.skill import execute

result = execute({{"param": "value"}})
print(result)  # {{"success": True, ...}}
```

## Created

- **Author:** {manifest.get('author', 'Archi')}
- **Date:** {manifest.get('created_at', 'unknown')}
- **Risk Level:** {manifest.get('risk_level', 'L2_MEDIUM')}
"""
