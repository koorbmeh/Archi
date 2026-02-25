"""
Skill Validator — Safety checks for user-created skills.

Validates skill code before registration:
- AST parsing (syntax check)
- Blocked import detection (subprocess, socket, eval, etc.)
- Code size limits
- Required interface check (def execute(params) -> dict)
- SKILL.json manifest validation
"""

import ast
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Imports that are never allowed in skill code.
# These provide system-level access that could compromise safety.
BLOCKED_IMPORTS = frozenset({
    "subprocess",
    "socket",
    "paramiko",
    "pickle",
    "shelve",
    "ctypes",
    "multiprocessing",
    "signal",
    "resource",
    "pty",
    "fcntl",
    "termios",
})

# Specific attribute accesses that are blocked even when the parent
# module is allowed (e.g., os is allowed for os.path, but os.system is not).
BLOCKED_ATTRIBUTES = frozenset({
    "os.system",
    "os.exec",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execlpe",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.popen",
    "os.spawn",
    "os.spawnl",
    "os.spawnle",
    "os.kill",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "shutil.rmtree",
})

# Built-in functions that are blocked in skill code.
BLOCKED_BUILTINS = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "exit",
    "quit",
})

# Maximum code size in bytes.
MAX_CODE_SIZE = 50_000  # 50 KB

# Required manifest fields.
REQUIRED_MANIFEST_FIELDS = {"name", "version", "description", "interface"}


@dataclass
class ValidationResult:
    """Result of validating a skill."""
    valid: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_issue(self, msg: str) -> None:
        self.issues.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class SkillValidator:
    """Validates skill code and manifests for safety before registration."""

    def validate_skill_directory(self, skill_path: str) -> ValidationResult:
        """Full validation of a skill directory.

        Checks:
        - skill.py exists and passes code validation
        - SKILL.json exists and passes manifest validation
        """
        result = ValidationResult(valid=True)
        path = Path(skill_path)

        if not path.is_dir():
            result.add_issue(f"Skill path is not a directory: {skill_path}")
            return result

        # Check skill.py
        skill_file = path / "skill.py"
        if not skill_file.is_file():
            result.add_issue("Missing required file: skill.py")
        else:
            try:
                code = skill_file.read_text(encoding="utf-8")
                code_result = self.validate_code(code)
                result.issues.extend(code_result.issues)
                result.warnings.extend(code_result.warnings)
                if not code_result.valid:
                    result.valid = False
            except Exception as e:
                result.add_issue(f"Failed to read skill.py: {e}")

        # Check SKILL.json
        manifest_file = path / "SKILL.json"
        if not manifest_file.is_file():
            result.add_issue("Missing required file: SKILL.json")
        else:
            try:
                manifest_text = manifest_file.read_text(encoding="utf-8")
                manifest = json.loads(manifest_text)
                manifest_result = self.validate_manifest(manifest)
                result.issues.extend(manifest_result.issues)
                result.warnings.extend(manifest_result.warnings)
                if not manifest_result.valid:
                    result.valid = False
            except json.JSONDecodeError as e:
                result.add_issue(f"SKILL.json is not valid JSON: {e}")
            except Exception as e:
                result.add_issue(f"Failed to read SKILL.json: {e}")

        return result

    def validate_code(self, code: str) -> ValidationResult:
        """Validate Python code string for safety.

        Checks:
        - Syntax is valid
        - No blocked imports
        - No blocked builtins
        - Code size within limits
        - execute() function exists with correct signature
        """
        result = ValidationResult(valid=True)

        # Size check
        if len(code.encode("utf-8")) > MAX_CODE_SIZE:
            result.add_issue(
                f"Code exceeds maximum size ({len(code.encode('utf-8'))} bytes, "
                f"max {MAX_CODE_SIZE} bytes)"
            )
            return result

        if not code.strip():
            result.add_issue("Code is empty")
            return result

        # Syntax check
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            result.add_issue(f"Syntax error: {e}")
            return result

        # Check imports
        import_issues = self._check_imports(tree)
        for issue in import_issues:
            result.add_issue(issue)

        # Check for blocked builtins
        builtin_issues = self._check_builtins(tree)
        for issue in builtin_issues:
            result.add_issue(issue)

        # Check for blocked attribute access
        attr_issues = self._check_attributes(tree)
        for issue in attr_issues:
            result.add_issue(issue)

        # Check execute() function exists
        if not self._has_execute_function(tree):
            result.add_issue(
                "Skill must define a top-level 'execute(params)' function. "
                "Signature: def execute(params: dict) -> dict"
            )

        return result

    def validate_manifest(self, manifest: Dict[str, Any]) -> ValidationResult:
        """Validate SKILL.json manifest structure."""
        result = ValidationResult(valid=True)

        # Required fields
        for field_name in REQUIRED_MANIFEST_FIELDS:
            if field_name not in manifest:
                result.add_issue(f"Missing required manifest field: {field_name}")

        # Name validation
        name = manifest.get("name", "")
        if name and not name.replace("_", "").replace("-", "").isalnum():
            result.add_issue(
                f"Invalid skill name '{name}': must be alphanumeric with "
                f"underscores or hyphens only"
            )

        # Version validation
        version = manifest.get("version", "")
        if version:
            parts = version.split(".")
            if not all(p.isdigit() for p in parts):
                result.add_warning(f"Version '{version}' is not semver format")

        # Interface validation
        interface = manifest.get("interface", {})
        if interface:
            if "input_schema" not in interface:
                result.add_warning("Manifest interface missing 'input_schema'")
            if "output_schema" not in interface:
                result.add_warning("Manifest interface missing 'output_schema'")

        # Risk level validation
        risk = manifest.get("risk_level", "")
        if risk and risk not in ("L1_LOW", "L2_MEDIUM", "L3_HIGH"):
            result.add_warning(
                f"Unknown risk_level '{risk}'. "
                f"Expected: L1_LOW, L2_MEDIUM, or L3_HIGH"
            )

        return result

    # -- Internal checks ---------------------------------------------------

    def _check_imports(self, tree: ast.AST) -> List[str]:
        """Check for blocked imports in the AST."""
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in BLOCKED_IMPORTS:
                        issues.append(f"Blocked import: '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if module in BLOCKED_IMPORTS:
                        issues.append(f"Blocked import: 'from {node.module}'")
        return issues

    def _check_builtins(self, tree: ast.AST) -> List[str]:
        """Check for blocked builtin function calls."""
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in BLOCKED_BUILTINS:
                        issues.append(
                            f"Blocked builtin call: '{node.func.id}()'"
                        )
        return issues

    def _check_attributes(self, tree: ast.AST) -> List[str]:
        """Check for blocked attribute accesses (e.g., os.system)."""
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                # Build dotted name: e.g., os.system
                parts = []
                current = node
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                    parts.reverse()
                    # Check all prefixes: os.system, shutil.rmtree, etc.
                    for i in range(1, len(parts)):
                        dotted = ".".join(parts[:i + 1])
                        if dotted in BLOCKED_ATTRIBUTES:
                            issues.append(
                                f"Blocked attribute access: '{dotted}'"
                            )
        return issues

    def _has_execute_function(self, tree: ast.AST) -> bool:
        """Check that a top-level execute() function exists."""
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "execute":
                # Check it has at least one parameter (params)
                args = node.args
                total_args = len(args.args)
                if total_args >= 1:
                    return True
        return False
