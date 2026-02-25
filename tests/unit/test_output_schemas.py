"""Unit tests for src.core.output_schemas."""

import pytest

from src.core.output_schemas import validate_action, ACTION_SCHEMAS, ACTION_ALIASES


# ── TestActionSchemas ───────────────────────────────────────────────────


class TestActionSchemas:
    """Test the ACTION_SCHEMAS dictionary structure and completeness."""

    def test_action_schemas_is_dict(self):
        """Verify ACTION_SCHEMAS is a dictionary."""
        assert isinstance(ACTION_SCHEMAS, dict)
        assert len(ACTION_SCHEMAS) > 0

    def test_web_search_schema_present(self):
        """Verify web_search action is in schemas."""
        assert "web_search" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["web_search"] == {"query": str}

    def test_fetch_webpage_schema_present(self):
        """Verify fetch_webpage action is in schemas."""
        assert "fetch_webpage" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["fetch_webpage"] == {"url": str}

    def test_create_file_schema_present(self):
        """Verify create_file action is in schemas."""
        assert "create_file" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["create_file"] == {"path": str, "content": str}

    def test_append_file_schema_present(self):
        """Verify append_file action is in schemas."""
        assert "append_file" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["append_file"] == {"path": str, "content": str}

    def test_read_file_schema_present(self):
        """Verify read_file action is in schemas."""
        assert "read_file" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["read_file"] == {"path": str}

    def test_list_files_schema_present(self):
        """Verify list_files action is in schemas."""
        assert "list_files" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["list_files"] == {"path": str}

    def test_write_source_schema_present(self):
        """Verify write_source action is in schemas."""
        assert "write_source" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["write_source"] == {"path": str, "content": str}

    def test_edit_file_schema_present(self):
        """Verify edit_file action is in schemas."""
        assert "edit_file" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["edit_file"] == {"path": str, "find": str, "replace": str}

    def test_run_python_schema_present(self):
        """Verify run_python action is in schemas."""
        assert "run_python" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["run_python"] == {"code": str}

    def test_run_command_schema_present(self):
        """Verify run_command action is in schemas."""
        assert "run_command" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["run_command"] == {"command": str}

    def test_think_schema_present(self):
        """Verify think action is in schemas."""
        assert "think" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["think"] == {"note": str}

    def test_done_schema_present(self):
        """Verify done action is in schemas."""
        assert "done" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["done"] == {"summary": str}

    def test_ask_user_schema_present(self):
        """Verify ask_user action is in schemas."""
        assert "ask_user" in ACTION_SCHEMAS
        assert ACTION_SCHEMAS["ask_user"] == {"question": str}

    def test_all_schemas_have_string_typed_fields(self):
        """Verify all schemas have str type values (not tuples or other types)."""
        for action, schema in ACTION_SCHEMAS.items():
            assert isinstance(schema, dict), f"{action} schema must be dict"
            for field, field_type in schema.items():
                assert field_type is str, f"{action}.{field} must have str type"

    def test_action_aliases_is_dict(self):
        """Verify ACTION_ALIASES is a dictionary."""
        assert isinstance(ACTION_ALIASES, dict)
        assert len(ACTION_ALIASES) > 0

    def test_research_alias_maps_to_web_search(self):
        """Verify 'research' alias maps to 'web_search'."""
        assert ACTION_ALIASES["research"] == "web_search"

    def test_analyze_alias_maps_to_web_search(self):
        """Verify 'analyze' alias maps to 'web_search'."""
        assert ACTION_ALIASES["analyze"] == "web_search"

    def test_search_alias_maps_to_web_search(self):
        """Verify 'search' alias maps to 'web_search'."""
        assert ACTION_ALIASES["search"] == "web_search"

    def test_all_aliases_point_to_valid_schemas(self):
        """Verify all aliases map to actions that exist in ACTION_SCHEMAS."""
        for alias, canonical in ACTION_ALIASES.items():
            assert canonical in ACTION_SCHEMAS, \
                f"Alias '{alias}' maps to non-existent action '{canonical}'"


# ── TestValidateAction ──────────────────────────────────────────────────


class TestValidateActionBasicErrors:
    """Test validate_action with basic input validation."""

    def test_non_dict_input_returns_error(self):
        """Verify that non-dict input returns appropriate error message."""
        result = validate_action("not a dict")
        assert result is not None
        assert "Response must be a JSON object" in result

    def test_non_dict_list_input_returns_error(self):
        """Verify that list input returns appropriate error message."""
        result = validate_action([1, 2, 3])
        assert result is not None
        assert "Response must be a JSON object" in result

    def test_non_dict_int_input_returns_error(self):
        """Verify that int input returns appropriate error message."""
        result = validate_action(42)
        assert result is not None
        assert "Response must be a JSON object" in result

    def test_missing_action_field_returns_error(self):
        """Verify that missing 'action' field returns error."""
        result = validate_action({"query": "test"})
        assert result is not None
        assert "Missing or invalid 'action' field" in result

    def test_empty_string_action_returns_error(self):
        """Verify that empty string action returns error."""
        result = validate_action({"action": ""})
        assert result is not None
        assert "Missing or invalid 'action' field" in result

    def test_action_field_non_string_returns_error(self):
        """Verify that non-string action field returns error."""
        result = validate_action({"action": 123})
        assert result is not None
        assert "Missing or invalid 'action' field" in result

    def test_action_field_none_returns_error(self):
        """Verify that None action field returns error."""
        result = validate_action({"action": None})
        assert result is not None
        assert "Missing or invalid 'action' field" in result

    def test_empty_dict_returns_error(self):
        """Verify that empty dict returns error."""
        result = validate_action({})
        assert result is not None
        assert "Missing or invalid 'action' field" in result


class TestValidateActionUnknownActions:
    """Test validate_action with unknown/unregistered actions."""

    def test_unknown_action_returns_none(self):
        """Verify that unknown action returns None (passthrough)."""
        result = validate_action({"action": "unknown_future_action"})
        assert result is None

    def test_unknown_action_with_extra_fields_returns_none(self):
        """Verify that unknown action with fields returns None."""
        result = validate_action({"action": "future_capability", "param": "value"})
        assert result is None

    def test_unknown_action_typo_returns_none(self):
        """Verify that unknown action (typo) returns None."""
        result = validate_action({"action": "web_serch"})  # typo
        assert result is None


class TestValidateActionValidActions:
    """Test validate_action with valid actions."""

    def test_valid_web_search_returns_none(self):
        """Verify that valid web_search action returns None (valid)."""
        result = validate_action({"action": "web_search", "query": "test query"})
        assert result is None

    def test_valid_web_search_with_whitespace_returns_none(self):
        """Verify that web_search with normal whitespace is valid."""
        result = validate_action({"action": "web_search", "query": "test query with spaces"})
        assert result is None

    def test_valid_fetch_webpage_returns_none(self):
        """Verify that valid fetch_webpage action returns None (valid)."""
        result = validate_action({"action": "fetch_webpage", "url": "https://example.com"})
        assert result is None

    def test_valid_create_file_returns_none(self):
        """Verify that valid create_file action returns None (valid)."""
        result = validate_action({
            "action": "create_file",
            "path": "/path/to/file.txt",
            "content": "file content"
        })
        assert result is None

    def test_valid_append_file_returns_none(self):
        """Verify that valid append_file action returns None (valid)."""
        result = validate_action({
            "action": "append_file",
            "path": "/path/to/file.txt",
            "content": "appended content"
        })
        assert result is None

    def test_valid_read_file_returns_none(self):
        """Verify that valid read_file action returns None (valid)."""
        result = validate_action({"action": "read_file", "path": "/path/to/file.txt"})
        assert result is None

    def test_valid_list_files_returns_none(self):
        """Verify that valid list_files action returns None (valid)."""
        result = validate_action({"action": "list_files", "path": "/path/to/dir"})
        assert result is None

    def test_valid_write_source_returns_none(self):
        """Verify that valid write_source action returns None (valid)."""
        result = validate_action({
            "action": "write_source",
            "path": "/path/to/source.py",
            "content": "def foo():\n    pass"
        })
        assert result is None

    def test_valid_edit_file_returns_none(self):
        """Verify that valid edit_file action returns None (valid)."""
        result = validate_action({
            "action": "edit_file",
            "path": "/path/to/file.txt",
            "find": "old text",
            "replace": "new text"
        })
        assert result is None

    def test_valid_run_python_returns_none(self):
        """Verify that valid run_python action returns None (valid)."""
        result = validate_action({
            "action": "run_python",
            "code": "print('hello')"
        })
        assert result is None

    def test_valid_run_command_returns_none(self):
        """Verify that valid run_command action returns None (valid)."""
        result = validate_action({
            "action": "run_command",
            "command": "ls -la /tmp"
        })
        assert result is None

    def test_valid_think_returns_none(self):
        """Verify that valid think action returns None (valid)."""
        result = validate_action({
            "action": "think",
            "note": "Let me analyze this step by step"
        })
        assert result is None

    def test_valid_done_returns_none(self):
        """Verify that valid done action returns None (valid)."""
        result = validate_action({
            "action": "done",
            "summary": "Task completed successfully"
        })
        assert result is None

    def test_valid_ask_user_returns_none(self):
        """Verify that valid ask_user action returns None (valid)."""
        result = validate_action({
            "action": "ask_user",
            "question": "Should I proceed with this action?"
        })
        assert result is None


class TestValidateActionMissingFields:
    """Test validate_action with missing required fields."""

    def test_missing_query_field_in_web_search(self):
        """Verify that web_search without query field returns error."""
        result = validate_action({"action": "web_search"})
        assert result is not None
        assert "missing required field 'query'" in result
        assert "web_search" in result

    def test_missing_url_field_in_fetch_webpage(self):
        """Verify that fetch_webpage without url field returns error."""
        result = validate_action({"action": "fetch_webpage"})
        assert result is not None
        assert "missing required field 'url'" in result

    def test_missing_path_field_in_create_file(self):
        """Verify that create_file without path field returns error."""
        result = validate_action({
            "action": "create_file",
            "content": "some content"
        })
        assert result is not None
        assert "missing required field 'path'" in result

    def test_missing_content_field_in_create_file(self):
        """Verify that create_file without content field returns error."""
        result = validate_action({
            "action": "create_file",
            "path": "/path/to/file"
        })
        assert result is not None
        assert "missing required field 'content'" in result

    def test_missing_find_field_in_edit_file(self):
        """Verify that edit_file without find field returns error."""
        result = validate_action({
            "action": "edit_file",
            "path": "/path/to/file",
            "replace": "new text"
        })
        assert result is not None
        assert "missing required field 'find'" in result

    def test_missing_replace_field_in_edit_file(self):
        """Verify that edit_file without replace field returns error."""
        result = validate_action({
            "action": "edit_file",
            "path": "/path/to/file",
            "find": "old text"
        })
        assert result is not None
        assert "missing required field 'replace'" in result

    def test_missing_code_field_in_run_python(self):
        """Verify that run_python without code field returns error."""
        result = validate_action({"action": "run_python"})
        assert result is not None
        assert "missing required field 'code'" in result

    def test_missing_command_field_in_run_command(self):
        """Verify that run_command without command field returns error."""
        result = validate_action({"action": "run_command"})
        assert result is not None
        assert "missing required field 'command'" in result


class TestValidateActionWrongTypes:
    """Test validate_action with wrong field types."""

    def test_wrong_type_query_int(self):
        """Verify that web_search with int query returns type error."""
        result = validate_action({"action": "web_search", "query": 123})
        assert result is not None
        assert "field 'query' must be str" in result
        assert "got int" in result

    def test_wrong_type_url_dict(self):
        """Verify that fetch_webpage with dict url returns type error."""
        result = validate_action({"action": "fetch_webpage", "url": {"key": "value"}})
        assert result is not None
        assert "field 'url' must be str" in result
        assert "got dict" in result

    def test_wrong_type_path_list(self):
        """Verify that create_file with list path returns type error."""
        result = validate_action({
            "action": "create_file",
            "path": ["/path", "to", "file"],
            "content": "text"
        })
        assert result is not None
        assert "field 'path' must be str" in result
        assert "got list" in result

    def test_wrong_type_content_int(self):
        """Verify that create_file with int content returns type error."""
        result = validate_action({
            "action": "create_file",
            "path": "/path/to/file",
            "content": 999
        })
        assert result is not None
        assert "field 'content' must be str" in result

    def test_wrong_type_code_float(self):
        """Verify that run_python with float code returns type error."""
        result = validate_action({
            "action": "run_python",
            "code": 3.14
        })
        assert result is not None
        assert "field 'code' must be str" in result
        assert "got float" in result

    def test_wrong_type_find_bool(self):
        """Verify that edit_file with bool find returns type error."""
        result = validate_action({
            "action": "edit_file",
            "path": "/path/to/file",
            "find": True,
            "replace": "text"
        })
        assert result is not None
        assert "field 'find' must be str" in result
        assert "got bool" in result


class TestValidateActionEmptyStrings:
    """Test validate_action with empty string fields."""

    def test_empty_query_returns_error(self):
        """Verify that web_search with empty query returns error."""
        result = validate_action({"action": "web_search", "query": ""})
        assert result is not None
        assert "field 'query' is empty" in result

    def test_empty_query_with_whitespace_returns_error(self):
        """Verify that web_search with whitespace-only query returns error."""
        result = validate_action({"action": "web_search", "query": "   "})
        assert result is not None
        assert "field 'query' is empty" in result

    def test_empty_url_returns_error(self):
        """Verify that fetch_webpage with empty url returns error."""
        result = validate_action({"action": "fetch_webpage", "url": ""})
        assert result is not None
        assert "field 'url' is empty" in result

    def test_empty_path_in_create_file_returns_error(self):
        """Verify that create_file with empty path returns error."""
        result = validate_action({
            "action": "create_file",
            "path": "  \t\n  ",
            "content": "text"
        })
        assert result is not None
        assert "field 'path' is empty" in result

    def test_empty_content_in_create_file_returns_error(self):
        """Verify that create_file with empty content returns error."""
        result = validate_action({
            "action": "create_file",
            "path": "/path/to/file",
            "content": ""
        })
        assert result is not None
        assert "field 'content' is empty" in result

    def test_empty_code_in_run_python_returns_error(self):
        """Verify that run_python with empty code returns error."""
        result = validate_action({
            "action": "run_python",
            "code": ""
        })
        assert result is not None
        assert "field 'code' is empty" in result

    def test_empty_command_in_run_command_returns_error(self):
        """Verify that run_command with empty command returns error."""
        result = validate_action({
            "action": "run_command",
            "command": ""
        })
        assert result is not None
        assert "field 'command' is empty" in result

    def test_empty_summary_in_done_returns_error(self):
        """Verify that done with empty summary returns error."""
        result = validate_action({
            "action": "done",
            "summary": "   "
        })
        assert result is not None
        assert "field 'summary' is empty" in result


class TestValidateActionMultipleErrors:
    """Test validate_action with multiple field errors."""

    def test_multiple_missing_fields_in_create_file(self):
        """Verify that create_file with all missing fields returns combined error."""
        result = validate_action({"action": "create_file"})
        assert result is not None
        assert "missing required field 'path'" in result
        assert "missing required field 'content'" in result
        assert "; " in result  # errors are joined with semicolon

    def test_multiple_wrong_types_in_edit_file(self):
        """Verify that edit_file with wrong types returns combined error."""
        result = validate_action({
            "action": "edit_file",
            "path": 123,  # wrong type
            "find": 456,  # wrong type
            "replace": "text"
        })
        assert result is not None
        assert "field 'path' must be str" in result
        assert "field 'find' must be str" in result
        assert "; " in result  # errors are joined with semicolon

    def test_empty_and_missing_fields_combined(self):
        """Verify that combination of empty and missing fields returns combined error."""
        result = validate_action({
            "action": "edit_file",
            "path": "/path/to/file",
            "find": "",  # empty
            "replace": "text"
        })
        assert result is not None
        assert "field 'find' is empty" in result

    def test_error_message_includes_required_fields_list(self):
        """Verify that error message includes the list of required fields."""
        result = validate_action({
            "action": "edit_file",
            "path": "",
            "find": "",
            "replace": ""
        })
        assert result is not None
        assert "Required fields" in result
        assert "path" in result
        assert "find" in result
        assert "replace" in result

    def test_error_message_includes_canonical_action_name(self):
        """Verify that error message includes the canonical action name."""
        result = validate_action({
            "action": "web_search"
        })
        assert result is not None
        assert "Invalid 'web_search' action:" in result


class TestValidateActionAliases:
    """Test validate_action with alias resolution."""

    def test_research_alias_resolves_as_web_search(self):
        """Verify that 'research' alias validates as web_search."""
        result = validate_action({"action": "research", "query": "test"})
        assert result is None

    def test_research_alias_error_shows_canonical_action(self):
        """Verify that 'research' alias error shows 'web_search' as canonical."""
        result = validate_action({"action": "research"})
        assert result is not None
        assert "Invalid 'web_search' action:" in result

    def test_analyze_alias_resolves_as_web_search(self):
        """Verify that 'analyze' alias validates as web_search."""
        result = validate_action({"action": "analyze", "query": "test"})
        assert result is None

    def test_analyze_alias_error_shows_canonical_action(self):
        """Verify that 'analyze' alias error shows 'web_search' as canonical."""
        result = validate_action({"action": "analyze"})
        assert result is not None
        assert "Invalid 'web_search' action:" in result

    def test_search_alias_resolves_as_web_search(self):
        """Verify that 'search' alias validates as web_search."""
        result = validate_action({"action": "search", "query": "test"})
        assert result is None

    def test_search_alias_error_shows_canonical_action(self):
        """Verify that 'search' alias error shows 'web_search' as canonical."""
        result = validate_action({"action": "search"})
        assert result is not None
        assert "Invalid 'web_search' action:" in result

    def test_research_alias_with_wrong_type_shows_web_search(self):
        """Verify that 'research' alias with wrong type shows web_search in error."""
        result = validate_action({"action": "research", "query": 123})
        assert result is not None
        assert "Invalid 'web_search' action:" in result
        assert "field 'query' must be str" in result


class TestValidateActionEdgeCases:
    """Test validate_action with edge cases."""

    def test_extra_fields_are_allowed(self):
        """Verify that extra fields beyond schema are allowed."""
        result = validate_action({
            "action": "web_search",
            "query": "test",
            "extra_field": "extra_value",
            "another": 123
        })
        assert result is None

    def test_none_value_for_required_field(self):
        """Verify that None value for required field returns error."""
        result = validate_action({
            "action": "web_search",
            "query": None
        })
        assert result is not None
        assert "missing required field 'query'" in result

    def test_zero_int_as_wrong_type(self):
        """Verify that int 0 is treated as wrong type (not falsy)."""
        result = validate_action({
            "action": "web_search",
            "query": 0
        })
        assert result is not None
        assert "field 'query' must be str" in result

    def test_false_bool_as_wrong_type(self):
        """Verify that bool False is treated as wrong type (not falsy)."""
        result = validate_action({
            "action": "web_search",
            "query": False
        })
        assert result is not None
        assert "field 'query' must be str" in result

    def test_action_with_newlines_is_valid(self):
        """Verify that string fields with newlines are valid."""
        result = validate_action({
            "action": "create_file",
            "path": "/path/to/file",
            "content": "line1\nline2\nline3"
        })
        assert result is None

    def test_action_with_special_characters_is_valid(self):
        """Verify that string fields with special characters are valid."""
        result = validate_action({
            "action": "web_search",
            "query": "test@#$%^&*()|[]{}?!"
        })
        assert result is None

    def test_unicode_characters_are_valid(self):
        """Verify that string fields with unicode characters are valid."""
        result = validate_action({
            "action": "web_search",
            "query": "検索 поиск искать"
        })
        assert result is None


@pytest.mark.parametrize("action_name,action_data", [
    ("web_search", {"action": "web_search", "query": "test"}),
    ("fetch_webpage", {"action": "fetch_webpage", "url": "https://example.com"}),
    ("create_file", {"action": "create_file", "path": "/p", "content": "c"}),
    ("append_file", {"action": "append_file", "path": "/p", "content": "c"}),
    ("read_file", {"action": "read_file", "path": "/p"}),
    ("list_files", {"action": "list_files", "path": "/p"}),
    ("write_source", {"action": "write_source", "path": "/p", "content": "c"}),
    ("edit_file", {"action": "edit_file", "path": "/p", "find": "f", "replace": "r"}),
    ("run_python", {"action": "run_python", "code": "c"}),
    ("run_command", {"action": "run_command", "command": "c"}),
    ("think", {"action": "think", "note": "n"}),
    ("done", {"action": "done", "summary": "s"}),
    ("ask_user", {"action": "ask_user", "question": "q"}),
])
def test_all_schema_actions_valid_with_correct_input(action_name, action_data):
    """Parametrized test: verify all defined schema actions pass with valid input."""
    result = validate_action(action_data)
    assert result is None, f"Expected {action_name} to be valid, but got error: {result}"
