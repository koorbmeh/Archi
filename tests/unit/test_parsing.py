"""Unit tests for src/utils/parsing.py."""

import pytest

from src.utils.parsing import extract_json, extract_json_array, _parse_numbered_list, read_file_contents


# ── extract_json ────────────────────────────────────────────────────


class TestExtractJson:
    def test_none_input(self):
        assert extract_json(None) is None

    def test_empty_string(self):
        assert extract_json("") is None

    def test_direct_json(self):
        result = extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_whitespace(self):
        result = extract_json('  \n {"key": 1} \n ')
        assert result == {"key": 1}

    def test_markdown_code_fence(self):
        text = 'Here is the result:\n```json\n{"answer": 42}\n```\nDone.'
        result = extract_json(text)
        assert result == {"answer": 42}

    def test_markdown_fence_no_language(self):
        text = '```\n{"x": 1}\n```'
        result = extract_json(text)
        assert result == {"x": 1}

    def test_bare_json_in_prose(self):
        text = 'The output is {"status": "ok", "count": 3} as expected.'
        result = extract_json(text)
        assert result == {"status": "ok", "count": 3}

    def test_strips_thinking_blocks(self):
        text = '<think>reasoning here</think>{"result": true}'
        result = extract_json(text)
        assert result == {"result": True}

    def test_invalid_json_returns_none(self):
        assert extract_json("not json at all") is None

    def test_json_array_not_returned_as_dict(self):
        # extract_json expects a dict, not a list
        text = '[1, 2, 3]'
        result = extract_json(text)
        # json.loads returns a list, which is not a dict — check behavior
        # Actually extract_json does json.loads which returns list, but
        # the function doesn't type-check. Let me verify...
        # Looking at the code: it does `return json.loads(text)` without checking
        # if it's a dict. This is technically a mild issue but let's document behavior.
        assert result == [1, 2, 3]  # current behavior: returns whatever json.loads gives

    def test_nested_json(self):
        text = '{"outer": {"inner": [1, 2]}}'
        result = extract_json(text)
        assert result == {"outer": {"inner": [1, 2]}}


# ── extract_json_array ──────────────────────────────────────────────


class TestExtractJsonArray:
    def test_none_input(self):
        assert extract_json_array(None) == []

    def test_empty_string(self):
        assert extract_json_array("") == []

    def test_direct_array(self):
        result = extract_json_array('["a", "b", "c"]')
        assert result == ["a", "b", "c"]

    def test_markdown_code_fence(self):
        text = '```json\n["x", "y"]\n```'
        result = extract_json_array(text)
        assert result == ["x", "y"]

    def test_bare_array_in_prose(self):
        text = 'Here are items: ["one", "two"] and more text.'
        result = extract_json_array(text)
        assert result == ["one", "two"]

    def test_non_array_json_returns_empty(self):
        result = extract_json_array('{"key": "val"}')
        assert result == []

    def test_strips_thinking_blocks(self):
        text = '<think>let me think</think>["result"]'
        result = extract_json_array(text)
        assert result == ["result"]

    def test_invalid_json_returns_empty(self):
        assert extract_json_array("no json here") == []

    def test_prose_fallback_disabled_by_default(self):
        text = "1. First\n2. Second\n3. Third"
        result = extract_json_array(text)
        assert result == []

    def test_prose_fallback_numbered_list(self):
        text = "1. First item\n2. Second item\n3. Third item"
        result = extract_json_array(text, allow_prose_fallback=True)
        assert result == ["First item", "Second item", "Third item"]

    def test_prose_fallback_bullet_list(self):
        text = "- Alpha\n- Beta\n- Gamma"
        result = extract_json_array(text, allow_prose_fallback=True)
        assert result == ["Alpha", "Beta", "Gamma"]

    def test_array_of_objects(self):
        text = '[{"name": "a"}, {"name": "b"}]'
        result = extract_json_array(text)
        assert len(result) == 2
        assert result[0]["name"] == "a"


# ── _parse_numbered_list ────────────────────────────────────────────


class TestParseNumberedList:
    def test_numbered_with_dot(self):
        text = "1. Alpha\n2. Beta\n3. Gamma"
        result = _parse_numbered_list(text)
        assert result == ["Alpha", "Beta", "Gamma"]

    def test_numbered_with_paren(self):
        text = "1) First\n2) Second"
        result = _parse_numbered_list(text)
        assert result == ["First", "Second"]

    def test_bullet_dash(self):
        text = "- One\n- Two\n- Three"
        result = _parse_numbered_list(text)
        assert result == ["One", "Two", "Three"]

    def test_bullet_asterisk(self):
        text = "* Foo\n* Bar"
        result = _parse_numbered_list(text)
        assert result == ["Foo", "Bar"]

    def test_no_list_returns_empty(self):
        text = "Just a plain sentence with no list structure."
        result = _parse_numbered_list(text)
        assert result == []

    def test_numbered_takes_precedence_over_bullets(self):
        text = "1. First\n- Bullet"
        result = _parse_numbered_list(text)
        # Numbered list found first, returned immediately
        assert result == ["First"]

    def test_strips_whitespace(self):
        text = "  1.  Padded item  \n  2.  Another  "
        result = _parse_numbered_list(text)
        assert result == ["Padded item", "Another"]


# ── read_file_contents ─────────────────────────────────────────────


class TestReadFileContents:
    def test_reads_files(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("print('hi')")
        result = read_file_contents([str(f)])
        assert "hello.py" in result
        assert "print" in result

    def test_empty_list_returns_label(self):
        assert read_file_contents([]) == "(no files)"

    def test_custom_empty_label(self):
        assert read_file_contents([], empty_label="nothing") == "nothing"

    def test_skips_missing_by_default(self, tmp_path):
        result = read_file_contents([str(tmp_path / "nope.txt")])
        assert "MISSING" not in result
        assert result == "(no files)"

    def test_note_missing_includes_marker(self, tmp_path):
        result = read_file_contents([str(tmp_path / "gone.txt")], note_missing=True)
        assert "MISSING: gone.txt" in result

    def test_max_files_respected(self, tmp_path):
        files = []
        for i in range(5):
            f = tmp_path / f"f{i}.txt"
            f.write_text(f"content {i}")
            files.append(str(f))
        result = read_file_contents(files, max_files=2)
        assert "f0.txt" in result
        assert "f1.txt" in result
        assert "f2.txt" not in result

    def test_total_budget_stops_reading(self, tmp_path):
        files = []
        for i in range(10):
            f = tmp_path / f"big{i}.txt"
            f.write_text("x" * 500)
            files.append(str(f))
        result = read_file_contents(files, max_files=10, total_budget=100)
        # Should stop early due to budget
        count = result.count("---")
        assert count < 10

    def test_max_chars_truncates(self, tmp_path):
        f = tmp_path / "long.txt"
        f.write_text("a" * 5000)
        result = read_file_contents([str(f)], max_chars=100)
        # Content should be truncated
        content_lines = result.split("\n", 1)
        assert len(content_lines[1]) <= 110  # 100 chars + some tolerance

    def test_includes_file_size(self, tmp_path):
        f = tmp_path / "sized.txt"
        f.write_text("hello world")
        result = read_file_contents([str(f)])
        assert "bytes" in result

    def test_handles_unreadable_file(self, tmp_path):
        f = tmp_path / "noperm.txt"
        f.write_text("secret")
        f.chmod(0o000)
        result = read_file_contents([str(f)])
        # Should not crash
        assert isinstance(result, str)
        f.chmod(0o644)  # restore for cleanup
