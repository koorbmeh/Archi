"""
Structured Output Contracts — schema validation for PlanExecutor actions.

Each action has a set of required fields with expected types. The validator
checks model JSON before dispatch and returns specific error messages on
failure, enabling auto re-prompt with targeted fixes.

Schema format: {action_name: {field: type_or_tuple_of_types}}
Fields marked as required must be present and non-empty (for strings).
"""

import logging

logger = logging.getLogger(__name__)

# Schema definitions: action -> {field: expected_type}
# All actions require "action" (str) — enforced separately.
ACTION_SCHEMAS = {
    "web_search": {"query": str},
    "fetch_webpage": {"url": str},
    "create_file": {"path": str, "content": str},
    "append_file": {"path": str, "content": str},
    "read_file": {"path": str},
    "list_files": {"path": str},
    "write_source": {"path": str, "content": str},
    "edit_file": {"path": str, "find": str, "replace": str},
    "run_python": {"code": str},
    "run_command": {"command": str},
    "think": {"note": str},
    "done": {"summary": str},
    "ask_user": {"question": str},
}

# Actions that map to real actions (hallucinated names → canonical)
ACTION_ALIASES = {
    "research": "web_search",
    "analyze": "web_search",
    "search": "web_search",
}


def validate_action(parsed: dict) -> str | None:
    """Validate a parsed action dict against its schema.

    Returns None if valid, or a specific error message describing
    what's wrong (missing fields, wrong types, empty values).
    The error message is designed to be injected directly into
    a re-prompt so the model can fix the issue.
    """
    if not isinstance(parsed, dict):
        return "Response must be a JSON object with an 'action' field."

    action = parsed.get("action")
    if not action or not isinstance(action, str):
        return "Missing or invalid 'action' field. Must be a string."

    # Resolve aliases before validation
    canonical = ACTION_ALIASES.get(action, action)

    schema = ACTION_SCHEMAS.get(canonical)
    if schema is None:
        # Unknown action — let the executor handle it (may be a new action)
        return None

    errors = []
    for field, expected_type in schema.items():
        value = parsed.get(field)
        if value is None:
            errors.append(f"missing required field '{field}'")
        elif not isinstance(value, expected_type):
            errors.append(
                f"field '{field}' must be {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )
        elif isinstance(value, str) and not value.strip():
            errors.append(f"field '{field}' is empty")

    if errors:
        return (
            f"Invalid '{canonical}' action: {'; '.join(errors)}. "
            f"Required fields for '{canonical}': {list(schema.keys())}."
        )
    return None
