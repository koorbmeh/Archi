"""Unit tests for src/core/discovery.py.

Covers: _match_project, _enumerate_files, _rank_files, _read_selectively,
_extract_python_structure, _fallback_brief, discover_project.
"""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.discovery import (
    _enumerate_files,
    _extract_python_structure,
    _fallback_brief,
    _file_list_cache,
    _generate_brief,
    _match_project,
    _rank_files,
    _read_selectively,
    discover_project,
)


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear file list cache before each test."""
    _file_list_cache.clear()
    yield
    _file_list_cache.clear()


@pytest.fixture
def project_tree(tmp_path):
    """Create a minimal project tree for testing."""
    (tmp_path / "main.py").write_text("import os\ndef main():\n    pass\n")
    (tmp_path / "README.md").write_text("# Project\nA test project.\n")
    (tmp_path / "config.yaml").write_text("key: value\n")
    (tmp_path / "utils.py").write_text("def helper():\n    return 1\n")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "app.py").write_text("class App:\n    pass\n")
    (sub / "__init__.py").write_text("")
    return tmp_path


@pytest.fixture
def sample_project_context():
    return {
        "active_projects": {
            "archi_bot": {
                "description": "Discord AI agent with dream cycles",
                "path": "workspace/archi",
                "focus_areas": ["discord", "AI", "autonomous"],
            },
            "web_dashboard": {
                "description": "React dashboard for analytics",
                "path": "workspace/dashboard",
                "focus_areas": ["react", "analytics", "frontend"],
            },
        }
    }


# ---- TestMatchProject ----

class TestMatchProject:
    def test_matches_by_project_key(self, sample_project_context):
        result = _match_project("improve archi bot responses", sample_project_context)
        assert result is not None
        assert result[0] == "archi_bot"

    def test_matches_by_description_keywords(self, sample_project_context):
        result = _match_project("fix the discord agent", sample_project_context)
        assert result is not None
        assert result[0] == "archi_bot"

    def test_matches_by_focus_area(self, sample_project_context):
        result = _match_project("add react component for charts", sample_project_context)
        assert result is not None
        assert result[0] == "web_dashboard"

    def test_returns_none_for_no_match(self, sample_project_context):
        result = _match_project("cook dinner tonight", sample_project_context)
        assert result is None

    def test_returns_none_for_empty_active_projects(self):
        result = _match_project("anything", {"active_projects": {}})
        assert result is None

    def test_returns_none_for_missing_active_projects(self):
        result = _match_project("anything", {})
        assert result is None

    def test_skips_non_dict_project_info(self):
        ctx = {"active_projects": {"my_proj": "not_a_dict"}}
        result = _match_project("my_proj stuff", ctx)
        assert result is None

    def test_stemming_matches_plural(self, sample_project_context):
        """'agents' should stem-match 'agent' in focus_areas."""
        result = _match_project("autonomous agents running overnight", sample_project_context)
        assert result is not None
        assert result[0] == "archi_bot"

    def test_picks_best_match(self, sample_project_context):
        """When multiple projects match, pick the one with more overlap."""
        result = _match_project("react analytics frontend dashboard", sample_project_context)
        assert result is not None
        assert result[0] == "web_dashboard"

    def test_stop_words_ignored(self, sample_project_context):
        """Common words like 'create', 'build', 'add' shouldn't be matched."""
        # All words are stop words — should return None
        result = _match_project("create a new project to build", sample_project_context)
        assert result is None


# ---- TestEnumerateFiles ----

class TestEnumerateFiles:
    def test_enumerates_all_files(self, project_tree):
        files = _enumerate_files(project_tree)
        names = {f.name for f in files}
        assert "main.py" in names
        assert "README.md" in names
        assert "app.py" in names

    def test_skips_hidden_files(self, project_tree):
        (project_tree / ".hidden_file").write_text("secret")
        files = _enumerate_files(project_tree)
        names = {f.name for f in files}
        assert ".hidden_file" not in names

    def test_skips_hidden_dirs(self, project_tree):
        hidden = project_tree / ".hidden_dir"
        hidden.mkdir()
        (hidden / "file.py").write_text("x")
        files = _enumerate_files(project_tree)
        paths = {str(f) for f in files}
        assert not any(".hidden_dir" in p for p in paths)

    def test_skips_pycache(self, project_tree):
        pycache = project_tree / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.cpython-311.pyc").write_text("x")
        files = _enumerate_files(project_tree)
        paths = {str(f) for f in files}
        assert not any("__pycache__" in p for p in paths)

    def test_respects_max_files_limit(self, tmp_path):
        for i in range(150):
            (tmp_path / f"file_{i}.txt").write_text(f"content {i}")
        files = _enumerate_files(tmp_path)
        assert len(files) <= 100  # _MAX_FILES

    def test_sorts_by_mtime_newest_first(self, tmp_path):
        old = tmp_path / "old.py"
        old.write_text("old")
        os.utime(old, (1000, 1000))
        new = tmp_path / "new.py"
        new.write_text("new")
        os.utime(new, (9999999999, 9999999999))
        files = _enumerate_files(tmp_path)
        assert files[0].name == "new.py"

    def test_caching_returns_same_result(self, project_tree):
        first = _enumerate_files(project_tree)
        # Add a new file — cache should still return original result
        (project_tree / "extra.py").write_text("extra")
        second = _enumerate_files(project_tree)
        assert len(first) == len(second)

    def test_cache_expires(self, project_tree):
        first = _enumerate_files(project_tree)
        # Manually expire cache
        cache_key = str(project_tree)
        ts, files = _file_list_cache[cache_key]
        _file_list_cache[cache_key] = (ts - 120, files)  # 120s ago
        (project_tree / "extra.py").write_text("extra")
        second = _enumerate_files(project_tree)
        assert len(second) == len(first) + 1

    def test_returns_copies_not_cache_reference(self, project_tree):
        first = _enumerate_files(project_tree)
        second = _enumerate_files(project_tree)
        assert first is not second  # Different list objects

    def test_handles_nonexistent_root(self, tmp_path):
        files = _enumerate_files(tmp_path / "nonexistent")
        assert files == []


# ---- TestRankFiles ----

class TestRankFiles:
    def test_entry_points_ranked_first(self, project_tree):
        files = list(project_tree.rglob("*"))
        files = [f for f in files if f.is_file()]
        ranked = _rank_files(files, "something", project_tree)
        # main.py and app.py should be near the top
        top_names = {f.name for f in ranked[:3]}
        assert "main.py" in top_names or "app.py" in top_names

    def test_readme_ranked_high(self, project_tree):
        files = list(project_tree.rglob("*"))
        files = [f for f in files if f.is_file()]
        ranked = _rank_files(files, "documentation", project_tree)
        top_names = {f.name for f in ranked[:3]}
        assert "README.md" in top_names

    def test_keyword_match_boosts_ranking(self, tmp_path):
        """Keyword match (+4) should rank a file above generic files (+2)."""
        for name in ["alpha.py", "beta.py", "gamma.py", "database.py"]:
            (tmp_path / name).write_text(f"# {name}")
        files = list(tmp_path.rglob("*.py"))
        ranked = _rank_files(files, "fix the database connection", tmp_path)
        top_names = {f.name for f in ranked[:2]}
        assert "database.py" in top_names

    def test_limits_to_15_files(self, tmp_path):
        for i in range(30):
            (tmp_path / f"file_{i}.py").write_text(f"def func_{i}(): pass")
        files = list(tmp_path.rglob("*.py"))
        ranked = _rank_files(files, "test all files", tmp_path)
        assert len(ranked) <= 15

    def test_user_prefs_boost_ranking(self, tmp_path):
        """User preference keywords should boost file ranking."""
        for name in ["alpha.py", "beta.py", "gamma.py", "security.py"]:
            (tmp_path / name).write_text(f"# {name}")
        files = list(tmp_path.rglob("*.py"))
        ranked = _rank_files(files, "improve code", tmp_path,
                             user_prefs="Jesse prefers security focused development")
        top_names = {f.name for f in ranked[:2]}
        assert "security.py" in top_names

    def test_empty_files_list(self, tmp_path):
        ranked = _rank_files([], "anything", tmp_path)
        assert ranked == []


# ---- TestReadSelectively ----

class TestReadSelectively:
    def test_reads_python_as_structure(self, project_tree):
        files = [project_tree / "main.py"]
        result = _read_selectively(files, project_tree)
        assert len(result) == 1
        assert result[0]["path"] == "main.py"
        assert "import os" in result[0]["content"]

    def test_reads_markdown_fully(self, project_tree):
        files = [project_tree / "README.md"]
        result = _read_selectively(files, project_tree)
        assert len(result) == 1
        assert "# Project" in result[0]["content"]

    def test_skips_empty_files(self, tmp_path):
        (tmp_path / "empty.py").write_text("")
        result = _read_selectively([tmp_path / "empty.py"], tmp_path)
        assert result == []

    def test_skips_unreadable_files(self, tmp_path):
        bad = tmp_path / "nonexistent.py"
        result = _read_selectively([bad], tmp_path)
        assert result == []

    def test_respects_max_content_chars(self, tmp_path):
        # Create files with large content
        for i in range(20):
            (tmp_path / f"big_{i}.md").write_text("x" * 2000)
        files = list(tmp_path.rglob("*.md"))
        result = _read_selectively(files, tmp_path)
        total_chars = sum(len(r["content"]) for r in result)
        # Should stop before reading all files due to _MAX_CONTENT_CHARS
        assert total_chars <= 10000  # _MAX_CONTENT_CHARS + one file buffer

    def test_other_file_types_truncated(self, tmp_path):
        (tmp_path / "data.csv").write_text("a," * 1000)
        result = _read_selectively([tmp_path / "data.csv"], tmp_path)
        assert len(result) == 1
        assert len(result[0]["content"]) <= 500


# ---- TestExtractPythonStructure ----

class TestExtractPythonStructure:
    def test_extracts_imports(self):
        src = "import os\nimport sys\nx = 1\n"
        result = _extract_python_structure(src)
        assert "import os" in result
        assert "import sys" in result

    def test_extracts_class_defs(self):
        src = "class Foo:\n    def bar(self):\n        pass\n"
        result = _extract_python_structure(src)
        assert "class Foo:" in result
        assert "def bar(self):" in result

    def test_extracts_function_defs(self):
        src = "def hello(name: str) -> str:\n    return f'hi {name}'\n"
        result = _extract_python_structure(src)
        assert "def hello(name: str) -> str:" in result

    def test_extracts_decorators(self):
        src = "@staticmethod\ndef foo():\n    pass\n"
        result = _extract_python_structure(src)
        assert "@staticmethod" in result

    def test_extracts_module_level_constants(self):
        src = "MAX_SIZE = 100\n_CACHE = {}\ndef func():\n    x = 1\n"
        result = _extract_python_structure(src)
        assert "MAX_SIZE = 100" in result
        assert "_CACHE = {}" in result

    def test_includes_first_two_docstrings(self):
        src = '"""Module doc."""\nclass A:\n    """Class doc."""\n    pass\ndef b():\n    """Third doc."""\n    pass\n'
        result = _extract_python_structure(src)
        assert '"""Module doc."""' in result
        assert '"""Class doc."""' in result

    def test_skips_function_bodies(self):
        """Indented body lines beyond the first 3 lines of the file are skipped."""
        src = "import os\nimport sys\nimport re\ndef foo():\n    x = 1\n    y = 2\n    return x + y\n"
        result = _extract_python_structure(src)
        assert "def foo():" in result
        # Body lines (beyond first 3 lines) should not appear
        assert "return x + y" not in result

    def test_caps_at_80_lines(self):
        lines = ["import os"] + [f"def func_{i}(): pass" for i in range(100)]
        src = "\n".join(lines)
        result = _extract_python_structure(src)
        assert len(result.split("\n")) <= 80

    def test_empty_source(self):
        result = _extract_python_structure("")
        assert result == ""


# ---- TestFallbackBrief ----

class TestFallbackBrief:
    def test_includes_project_name(self):
        result = _fallback_brief("my_project", ["a.py", "b.py"], [])
        assert "my_project" in result

    def test_includes_file_count(self):
        result = _fallback_brief("proj", ["a.py", "b.py", "c.py"], [])
        assert "3 total" in result

    def test_includes_key_files(self):
        result = _fallback_brief("proj", ["main.py", "utils.py"], [])
        assert "main.py" in result
        assert "utils.py" in result

    def test_includes_read_files(self):
        contents = [{"path": "src/app.py", "content": "..."}]
        result = _fallback_brief("proj", ["src/app.py"], contents)
        assert "src/app.py" in result

    def test_handles_empty_files(self):
        result = _fallback_brief("proj", [], [])
        assert "proj" in result
        assert "0 total" in result


# ---- TestGenerateBrief ----

class TestGenerateBrief:
    def test_returns_model_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": "Brief text here", "cost_usd": 0.001}
        brief, cost = _generate_brief(
            "add tests", "proj", "test project",
            [{"path": "a.py", "content": "code"}], ["a.py", "b.py"], router,
        )
        assert brief == "Brief text here"
        assert cost == 0.001
        router.generate.assert_called_once()

    def test_falls_back_on_empty_response(self):
        router = MagicMock()
        router.generate.return_value = {"text": "", "cost_usd": 0.0}
        brief, cost = _generate_brief(
            "goal", "proj", "desc", [], ["a.py"], router,
        )
        assert "proj" in brief
        assert cost == 0

    def test_falls_back_on_exception(self):
        router = MagicMock()
        router.generate.side_effect = RuntimeError("API down")
        brief, cost = _generate_brief(
            "goal", "proj", "desc", [], ["a.py"], router,
        )
        assert "proj" in brief
        assert cost == 0


# ---- TestDiscoverProject (integration-style with mocks) ----

class TestDiscoverProject:
    def test_returns_none_for_non_dict_context(self):
        result = discover_project("goal", "not_a_dict", MagicMock())
        assert result is None

    def test_returns_none_for_no_matching_project(self):
        ctx = {"active_projects": {"web_app": {"description": "frontend", "path": "web"}}}
        result = discover_project("cook pasta tonight", ctx, MagicMock())
        assert result is None

    def test_returns_none_for_nonexistent_path(self, tmp_path):
        ctx = {"active_projects": {
            "my_proj": {"description": "test project", "path": "nonexistent_dir"}
        }}
        with patch("src.core.discovery._base_path", return_value=tmp_path):
            result = discover_project("test my_proj", ctx, MagicMock())
        assert result is None

    def test_returns_none_for_empty_directory(self, tmp_path):
        proj_dir = tmp_path / "empty_proj"
        proj_dir.mkdir()
        ctx = {"active_projects": {
            "empty_proj": {"description": "empty project", "path": "empty_proj"}
        }}
        with patch("src.core.discovery._base_path", return_value=tmp_path):
            result = discover_project("empty_proj stuff", ctx, MagicMock())
        assert result is None

    def test_full_discovery_flow(self, tmp_path):
        proj_dir = tmp_path / "my_project"
        proj_dir.mkdir()
        (proj_dir / "main.py").write_text("import os\ndef main(): pass\n")
        (proj_dir / "README.md").write_text("# My Project\n")

        ctx = {"active_projects": {
            "my_project": {
                "description": "A Python utility",
                "path": "my_project",
                "focus_areas": ["python", "utility"],
            }
        }}
        router = MagicMock()
        router.generate.return_value = {"text": "Brief: project has main.py", "cost_usd": 0.002}

        with patch("src.core.discovery._base_path", return_value=tmp_path):
            result = discover_project("improve my_project utility", ctx, router)

        assert result is not None
        assert result["project_name"] == "my_project"
        assert result["project_path"] == "my_project"
        assert result["files_found"] >= 2
        assert result["files_read"] >= 1
        assert result["cost"] == 0.002
        assert "Brief" in result["brief"]

    def test_user_model_import_failure_doesnt_crash(self, tmp_path):
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / "file.py").write_text("x = 1\n")

        ctx = {"active_projects": {
            "proj": {"description": "test", "path": "proj"}
        }}
        router = MagicMock()
        router.generate.return_value = {"text": "brief", "cost_usd": 0}

        with patch("src.core.discovery._base_path", return_value=tmp_path):
            # user_model import will naturally fail or be unavailable — shouldn't crash
            result = discover_project("test proj stuff", ctx, router)

        assert result is not None
