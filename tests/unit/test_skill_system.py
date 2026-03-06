"""
Tests for the self-extending skill system.

Covers:
- SkillValidator: code safety, manifest validation
- SkillRegistry: load, register, execute, inventory
- SkillCreator: name generation, code cleaning
- SkillSuggestions: pattern detection
- Integration: /skill commands in conversational_router
- Integration: _do_invoke_skill in PlanExecutor actions
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.skill_validator import (
    BLOCKED_BUILTINS,
    BLOCKED_IMPORTS,
    SkillValidator,
    ValidationResult,
)


# ── SkillValidator Tests ─────────────────────────────────────────────


class TestSkillValidator:
    """Tests for code and manifest validation."""

    def setup_method(self):
        self.validator = SkillValidator()

    # -- Code validation ---------------------------------------------------

    def test_valid_skill_code(self):
        code = '''
import json
import os.path

def execute(params: dict) -> dict:
    """A valid skill."""
    try:
        name = params.get("name", "world")
        return {"success": True, "greeting": f"Hello, {name}!"}
    except Exception as e:
        return {"success": False, "error": str(e)}
'''
        result = self.validator.validate_code(code)
        assert result.valid, f"Expected valid, got issues: {result.issues}"

    def test_empty_code_rejected(self):
        result = self.validator.validate_code("")
        assert not result.valid
        assert any("empty" in i.lower() for i in result.issues)

    def test_syntax_error_rejected(self):
        result = self.validator.validate_code("def execute(params:\n    return {}")
        assert not result.valid
        assert any("syntax" in i.lower() for i in result.issues)

    def test_blocked_import_subprocess(self):
        code = '''
import subprocess

def execute(params: dict) -> dict:
    result = subprocess.run(["ls"], capture_output=True)
    return {"success": True, "output": result.stdout}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("subprocess" in i for i in result.issues)

    def test_blocked_import_socket(self):
        code = '''
import socket

def execute(params: dict) -> dict:
    return {"success": True}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("socket" in i for i in result.issues)

    def test_blocked_import_from(self):
        code = '''
from subprocess import run

def execute(params: dict) -> dict:
    return {"success": True}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("subprocess" in i for i in result.issues)

    def test_blocked_builtin_eval(self):
        code = '''
def execute(params: dict) -> dict:
    result = eval(params.get("expr", "1+1"))
    return {"success": True, "result": result}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("eval" in i for i in result.issues)

    def test_blocked_builtin_exec(self):
        code = '''
def execute(params: dict) -> dict:
    exec(params.get("code", "pass"))
    return {"success": True}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("exec" in i for i in result.issues)

    def test_blocked_attribute_os_system(self):
        code = '''
import os

def execute(params: dict) -> dict:
    os.system("echo hello")
    return {"success": True}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("os.system" in i for i in result.issues)

    def test_allowed_os_path(self):
        """os.path usage should be allowed (os is not fully blocked)."""
        code = '''
import os.path

def execute(params: dict) -> dict:
    exists = os.path.exists(params.get("path", "."))
    return {"success": True, "exists": exists}
'''
        result = self.validator.validate_code(code)
        assert result.valid, f"Expected valid, got issues: {result.issues}"

    def test_missing_execute_function(self):
        code = '''
def process(params: dict) -> dict:
    return {"success": True}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("execute" in i for i in result.issues)

    def test_execute_no_params_rejected(self):
        code = '''
def execute() -> dict:
    return {"success": True}
'''
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("execute" in i for i in result.issues)

    def test_code_size_limit(self):
        code = "x = 1\n" * 10000 + "\ndef execute(params): return {}"
        result = self.validator.validate_code(code)
        assert not result.valid
        assert any("size" in i.lower() for i in result.issues)

    # -- Manifest validation -----------------------------------------------

    def test_valid_manifest(self):
        manifest = {
            "name": "test_skill",
            "version": "1.0.0",
            "description": "A test skill",
            "interface": {
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        }
        result = self.validator.validate_manifest(manifest)
        assert result.valid

    def test_missing_required_fields(self):
        manifest = {"name": "test"}
        result = self.validator.validate_manifest(manifest)
        assert not result.valid
        assert len(result.issues) >= 2  # missing version, description, interface

    def test_invalid_name_chars(self):
        manifest = {
            "name": "test skill!@#",
            "version": "1.0.0",
            "description": "bad name",
            "interface": {},
        }
        result = self.validator.validate_manifest(manifest)
        assert not result.valid

    # -- Directory validation ----------------------------------------------

    def test_validate_skill_directory(self):
        """Test full directory validation with valid files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "my_skill"
            skill_dir.mkdir()

            (skill_dir / "skill.py").write_text(
                'def execute(params: dict) -> dict:\n    return {"success": True}\n'
            )
            (skill_dir / "SKILL.json").write_text(json.dumps({
                "name": "my_skill",
                "version": "1.0.0",
                "description": "Test",
                "interface": {},
            }))

            result = self.validator.validate_skill_directory(str(skill_dir))
            assert result.valid, f"Got issues: {result.issues}"

    def test_validate_missing_skill_py(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "bad_skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.json").write_text('{"name":"x","version":"1","description":"x","interface":{}}')

            result = self.validator.validate_skill_directory(str(skill_dir))
            assert not result.valid


# ── SkillRegistry Tests ──────────────────────────────────────────────


class TestSkillRegistry:
    """Tests for skill loading, registration, and execution."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skills_dir = Path(self.tmpdir) / "skills"
        self.skills_dir.mkdir()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_test_skill(self, name="test_skill"):
        """Helper: create a valid skill directory."""
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        (skill_dir / "skill.py").write_text(
            'def execute(params: dict) -> dict:\n'
            '    name = params.get("name", "world")\n'
            '    return {"success": True, "greeting": f"Hello, {name}!"}\n'
        )
        (skill_dir / "SKILL.json").write_text(json.dumps({
            "name": name,
            "version": "1.0.0",
            "description": f"Test skill: {name}",
            "interface": {
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        }))
        return skill_dir

    def test_load_all_skills(self):
        from src.core.skill_system import SkillRegistry

        self._create_test_skill("alpha")
        self._create_test_skill("beta")

        registry = SkillRegistry(skills_dir=self.skills_dir)
        skills = registry.get_available_skills()
        assert "alpha" in skills
        assert "beta" in skills

    def test_register_invalid_skill_returns_false(self):
        from src.core.skill_system import SkillRegistry

        registry = SkillRegistry(skills_dir=self.skills_dir)
        # Try to register a non-existent directory
        result = registry.register_skill("/nonexistent/path")
        assert not result

    def test_execute_skill_success(self):
        from src.core.skill_system import SkillRegistry

        self._create_test_skill("greeter")
        registry = SkillRegistry(skills_dir=self.skills_dir)

        result = registry.execute_skill("greeter", {"name": "Jesse"})
        assert result["success"] is True
        assert "Jesse" in result.get("greeting", "")

    def test_execute_nonexistent_skill(self):
        from src.core.skill_system import SkillRegistry

        registry = SkillRegistry(skills_dir=self.skills_dir)
        result = registry.execute_skill("nonexistent", {})
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_execute_skill_with_error(self):
        from src.core.skill_system import SkillRegistry

        skill_dir = self.skills_dir / "buggy"
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text(
            'def execute(params: dict) -> dict:\n'
            '    raise ValueError("Intentional test error")\n'
        )
        (skill_dir / "SKILL.json").write_text(json.dumps({
            "name": "buggy",
            "version": "1.0.0",
            "description": "Buggy skill",
            "interface": {},
        }))

        registry = SkillRegistry(skills_dir=self.skills_dir)
        result = registry.execute_skill("buggy", {})
        assert result["success"] is False
        assert "error" in result

    def test_skill_metrics_tracked(self):
        from src.core.skill_system import SkillRegistry

        self._create_test_skill("tracked")
        registry = SkillRegistry(skills_dir=self.skills_dir)

        registry.execute_skill("tracked", {})
        registry.execute_skill("tracked", {})

        info = registry.get_skill_info("tracked")
        assert info is not None
        assert info["invocations"] == 2
        assert info["success_rate"] == "100%"

    def test_get_skill_inventory(self):
        from src.core.skill_system import SkillRegistry

        self._create_test_skill("summarizer")
        registry = SkillRegistry(skills_dir=self.skills_dir)

        inventory = registry.get_skill_inventory()
        assert "skill_summarizer" in inventory
        assert "Available custom skills" in inventory

    def test_empty_inventory(self):
        from src.core.skill_system import SkillRegistry

        registry = SkillRegistry(skills_dir=self.skills_dir)
        inventory = registry.get_skill_inventory()
        assert inventory == ""

    def test_unregister_skill(self):
        from src.core.skill_system import SkillRegistry

        self._create_test_skill("removable")
        registry = SkillRegistry(skills_dir=self.skills_dir)
        assert "removable" in registry.get_available_skills()

        registry.unregister_skill("removable")
        assert "removable" not in registry.get_available_skills()


# ── SkillCreator Tests ───────────────────────────────────────────────


class TestSkillCreator:
    """Tests for skill creation helpers."""

    def test_name_from_description(self):
        from src.core.skill_creator import SkillCreator

        creator = SkillCreator(skills_dir=Path("/tmp/test_skills"))
        assert creator._name_from_description("summarize web pages") == "summarize_web_pages"
        assert creator._name_from_description("how to parse JSON files") == "parse_json_files"
        assert creator._name_from_description("a simple test") == "simple_test"

    def test_clean_code_removes_fences(self):
        from src.core.skill_creator import SkillCreator

        creator = SkillCreator(skills_dir=Path("/tmp/test_skills"))
        code = '```python\ndef execute(params):\n    return {"success": True}\n```'
        cleaned = creator._clean_code(code)
        assert "```" not in cleaned
        assert "def execute" in cleaned

    def test_clean_code_preserves_normal(self):
        from src.core.skill_creator import SkillCreator

        creator = SkillCreator(skills_dir=Path("/tmp/test_skills"))
        code = 'def execute(params):\n    return {"success": True}'
        cleaned = creator._clean_code(code)
        assert cleaned == code

    def test_extract_input_schema_from_params_get(self):
        """AST extracts param names and infers types from defaults."""
        from src.core.skill_creator import SkillCreator

        code = '''
def execute(params: dict) -> dict:
    name = params.get("name", "")
    count = params.get("count", 10)
    verbose = params.get("verbose", False)
    return {"success": True}
'''
        schema = SkillCreator._extract_input_schema(code)
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "name" in props
        assert props["name"]["type"] == "string"
        assert "count" in props
        assert props["count"]["type"] == "integer"
        assert props["count"]["default"] == 10
        assert "verbose" in props
        assert props["verbose"]["type"] == "boolean"
        assert props["verbose"]["default"] is False

    def test_extract_input_schema_required_from_no_default(self):
        """Params without defaults are marked required."""
        from src.core.skill_creator import SkillCreator

        code = '''
def execute(params: dict) -> dict:
    url = params.get("url")
    max_words = params.get("max_words", 150)
    return {"success": True}
'''
        schema = SkillCreator._extract_input_schema(code)
        assert "required" in schema
        assert "url" in schema["required"]
        assert "max_words" not in schema["required"]

    def test_extract_input_schema_docstring_enrichment(self):
        """Docstring adds type hints and descriptions to AST-extracted params."""
        from src.core.skill_creator import SkillCreator

        code = '''
"""
Fetches stock prices.

Required params:
- symbol (str): The stock ticker symbol (e.g., 'AAPL')

Returns:
- On success: {"success": True, "price": float}
"""
def execute(params: dict) -> dict:
    symbol = params.get("symbol", "").upper()
    return {"success": True}
'''
        schema = SkillCreator._extract_input_schema(code)
        props = schema["properties"]
        assert "symbol" in props
        assert props["symbol"]["type"] == "string"
        assert "ticker" in props["symbol"]["description"].lower()
        assert "required" in schema
        assert "symbol" in schema["required"]

    def test_extract_input_schema_optional_docstring(self):
        """Optional params from docstring are not marked required."""
        from src.core.skill_creator import SkillCreator

        code = '''
"""
Required params:
- url (str): URL to fetch

Optional params:
- max_words (int): Maximum words in summary. Default: 150.
"""
def execute(params: dict) -> dict:
    url = params.get("url")
    max_words = params.get("max_words", 150)
    return {"success": True}
'''
        schema = SkillCreator._extract_input_schema(code)
        assert "url" in schema["required"]
        # max_words has a default so not required
        assert "max_words" not in schema["required"]
        assert schema["properties"]["max_words"]["type"] == "integer"

    def test_extract_input_schema_empty_code(self):
        """Empty or param-less code returns empty schema."""
        from src.core.skill_creator import SkillCreator

        schema = SkillCreator._extract_input_schema(
            'def execute(params):\n    return {"success": True}'
        )
        assert schema == {"type": "object", "properties": {}}

    def test_extract_input_schema_syntax_error(self):
        """Malformed code returns empty schema instead of crashing."""
        from src.core.skill_creator import SkillCreator

        schema = SkillCreator._extract_input_schema("def broken(:")
        assert schema == {"type": "object", "properties": {}}

    def test_extract_input_schema_real_stock_skill(self):
        """End-to-end test with the actual fetch_stock_prices skill code."""
        from src.core.skill_creator import SkillCreator

        code = '''"""
Fetches the current stock price for a given ticker symbol.

Required params:
- symbol: str (e.g., 'AAPL', 'GOOGL', 'BTC-USD')

Returns:
- On success: {"success": True, "symbol": str, "price": float}
"""
import json

def execute(params: dict) -> dict:
    symbol = params.get('symbol', '').strip().upper()
    if not symbol:
        return {"success": False, "error": "Missing symbol"}
    return {"success": True, "symbol": symbol, "price": 100.0}
'''
        schema = SkillCreator._extract_input_schema(code)
        assert "symbol" in schema["properties"]
        assert "required" in schema
        assert "symbol" in schema["required"]

    def test_build_manifest_populates_input_schema(self):
        """_build_manifest with code populates input_schema from code."""
        from src.core.skill_creator import SkillCreator

        creator = SkillCreator(skills_dir=Path("/tmp/test_skills"))
        code = '''
def execute(params: dict) -> dict:
    url = params.get("url")
    return {"success": True}
'''
        manifest = creator._build_manifest("test_skill", "A test", None, code=code)
        props = manifest["interface"]["input_schema"]["properties"]
        assert "url" in props

    def test_build_manifest_empty_code_fallback(self):
        """_build_manifest without code uses generic schema."""
        from src.core.skill_creator import SkillCreator

        creator = SkillCreator(skills_dir=Path("/tmp/test_skills"))
        manifest = creator._build_manifest("test_skill", "A test", None)
        schema = manifest["interface"]["input_schema"]
        assert schema["properties"] == {}
        assert schema["description"] == "Parameters vary by use case"

    def test_extract_input_schema_skips_capitalized_words(self):
        """Capitalized words in docstrings are prose, not param names."""
        from src.core.skill_creator import SkillCreator

        code = '''
"""
Required params:
- symbol (str): Ticker symbol

Returns:
- On success: {"success": True}
- On failure: {"success": False}
"""
def execute(params: dict) -> dict:
    symbol = params.get('symbol', '')
    return {"success": True}
'''
        schema = SkillCreator._extract_input_schema(code)
        assert "symbol" in schema["properties"]
        assert "On" not in schema["properties"]
        assert "Returns" not in schema.get("properties", {})

    def test_extract_description_from_module_docstring(self):
        """Extracts description from module-level docstring."""
        from src.core.skill_creator import SkillCreator

        code = '''
"""
Fetches current weather data for a given city.

Required params:
- city (str): City name
"""
def execute(params: dict) -> dict:
    return {"success": True}
'''
        desc = SkillCreator._extract_description(code, "fallback")
        assert "weather" in desc.lower()
        assert desc != "fallback"

    def test_extract_description_skips_title_lines(self):
        """Skips 'Archi Skill: ...' title lines in docstrings."""
        from src.core.skill_creator import SkillCreator

        code = '''
"""
Archi Skill: Weather Fetcher

Fetches current weather data for a given city.
"""
def execute(params: dict) -> dict:
    return {"success": True}
'''
        desc = SkillCreator._extract_description(code, "fallback")
        assert not desc.startswith("Archi Skill")
        assert "weather" in desc.lower()

    def test_extract_description_from_post_import_docstring(self):
        """Finds docstring even when placed after import statements."""
        from src.core.skill_creator import SkillCreator

        code = '''
import json
import re

"""
Parses JSON data and extracts key fields.

Required params:
- data (str): JSON string
"""
def execute(params: dict) -> dict:
    return {"success": True}
'''
        desc = SkillCreator._extract_description(code, "fallback")
        assert "JSON" in desc or "json" in desc.lower()

    def test_extract_description_fallback(self):
        """Falls back when no usable docstring exists."""
        from src.core.skill_creator import SkillCreator

        code = 'def execute(params: dict) -> dict:\n    return {"success": True}\n'
        desc = SkillCreator._extract_description(code, "my fallback")
        assert desc == "my fallback"

    def test_build_manifest_uses_docstring_description(self):
        """_build_manifest prefers docstring description over raw user input."""
        from src.core.skill_creator import SkillCreator

        creator = SkillCreator(skills_dir=Path("/tmp/test_skills"))
        code = '''
"""
Fetches the current stock price for a given ticker symbol.
"""
def execute(params: dict) -> dict:
    symbol = params.get("symbol", "")
    return {"success": True}
'''
        manifest = creator._build_manifest("test", "fetch stock prices", None, code=code)
        assert "ticker symbol" in manifest["description"]


# ── SkillSuggestions Tests ───────────────────────────────────────────


class TestSkillSuggestions:
    """Tests for pattern detection and suggestion formatting."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_experiences(self, action, context_template, count):
        """Create mock experiences."""
        from src.core.learning_system import Experience
        exps = []
        for i in range(count):
            exp = Experience(
                experience_type="success",
                context=context_template.format(i=i),
                action=action,
                outcome=f"Completed successfully ({i})",
            )
            exps.append(exp)
        return exps

    def test_detect_repeated_actions(self):
        from src.core.skill_suggestions import SkillSuggestions

        state_path = Path(self.tmpdir) / "state.json"
        suggester = SkillSuggestions(state_path=state_path)

        experiences = self._make_experiences(
            "custom_transform",
            "Transform data file {i} with custom processing",
            5,
        )

        proposals = suggester._detect_repeated_actions(experiences)
        assert len(proposals) >= 1
        assert any("custom_transform" in p.name for p in proposals)

    def test_no_suggestions_for_builtins(self):
        from src.core.skill_suggestions import SkillSuggestions

        state_path = Path(self.tmpdir) / "state.json"
        suggester = SkillSuggestions(state_path=state_path)

        experiences = self._make_experiences(
            "web_search",
            "Search for topic {i}",
            10,
        )

        proposals = suggester._detect_repeated_actions(experiences)
        assert not any("web_search" in p.name for p in proposals)

    def test_format_suggestions(self):
        from src.core.skill_creator import SkillProposal
        from src.core.skill_suggestions import SkillSuggestions

        state_path = Path(self.tmpdir) / "state.json"
        suggester = SkillSuggestions(state_path=state_path)

        proposals = [
            SkillProposal(
                name="auto_parse",
                description="Parse CSV files automatically",
                confidence=0.85,
                source="pattern_detection",
                pattern_description="CSV parsing repeated 5 times",
            ),
        ]

        text = suggester.format_suggestions_for_user(proposals)
        assert "Parse CSV" in text
        assert "85%" in text

    def test_empty_suggestions(self):
        from src.core.skill_suggestions import SkillSuggestions

        state_path = Path(self.tmpdir) / "state.json"
        suggester = SkillSuggestions(state_path=state_path)
        assert suggester.format_suggestions_for_user([]) == ""


# ── Router Integration Tests ─────────────────────────────────────────


class TestSkillRouterCommands:
    """Test /skill commands in the conversational router."""

    def test_skill_help(self):
        from src.core.conversational_router import _handle_skill_command

        result = _handle_skill_command("/skill", "/skill")
        assert result.fast_path
        assert "list" in result.answer.lower()
        assert "create" in result.answer.lower()

    def test_skill_list_empty(self):
        from src.core.conversational_router import _handle_skill_command

        # Patch the registry to return empty
        with patch("src.core.skill_system.get_shared_skill_registry") as mock_reg:
            mock_reg.return_value.get_available_skills.return_value = []
            result = _handle_skill_command("/skill list", "/skill list")
            assert result.fast_path
            assert "no skills" in result.answer.lower()

    def test_skill_create_routes_to_skill_creator(self):
        from src.core.conversational_router import _handle_skill_command

        result = _handle_skill_command(
            "/skill create summarize web pages",
            "/skill create summarize web pages",
        )
        assert result.tier == "easy"
        assert result.action == "create_skill"
        assert result.action_params.get("description") == "summarize web pages"

    def test_skill_unknown_subcommand(self):
        from src.core.conversational_router import _handle_skill_command

        result = _handle_skill_command("/skill foo", "/skill foo")
        assert result.fast_path
        assert "unknown" in result.answer.lower()


# ── Learning System Integration ──────────────────────────────────────


class TestLearningSystemSkillTracking:
    """Test skill-related additions to LearningSystem."""

    def test_record_skill_created(self):
        from src.core.learning_system import LearningSystem

        with tempfile.TemporaryDirectory() as tmpdir:
            ls = LearningSystem(data_dir=Path(tmpdir))
            ls.record_skill_created("web_summarizer", "repeated web research")
            assert "created_skill:web_summarizer" in ls.patterns
            # Should also record as a success experience
            assert any(
                e.action == "create_skill" for e in ls.experiences
            )

    def test_record_skill_suggested(self):
        from src.core.learning_system import LearningSystem

        with tempfile.TemporaryDirectory() as tmpdir:
            ls = LearningSystem(data_dir=Path(tmpdir))
            ls.record_skill_suggested("auto_parser")
            assert "auto_parser" in ls.patterns.get("suggested_skills", [])
