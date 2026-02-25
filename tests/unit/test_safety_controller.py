"""
Unit tests for safety_controller.py: action authorization, path validation, risk levels.

Covers:
  - Action: dataclass creation and defaults
  - SafetyController.__init__: rules loading, allowed paths setup
  - validate_path: workspace isolation (realpath resolution)
  - _get_risk_level: action type lookup in risk_levels config
  - _is_write_action: read-only vs write classification
  - _violates_non_override_rules: path isolation for write actions
  - _request_approval: user approval prompt (mocked)
  - _queue_for_manual_execution: approval queue management
  - authorize: full authorization chain

Test classes:
  - TestAction: dataclass creation and defaults
  - TestInit: rules loading, allowed paths, missing file handling
  - TestValidatePath: allowed path, blocked path, symlink resolution
  - TestGetRiskLevel: known action type, unknown action type
  - TestIsWriteAction: read-only vs write actions
  - TestViolatesNonOverrideRules: path isolation, read-only bypass
  - TestAuthorize: autonomous, notify_and_log, human_approval, manual_execute_only
  - TestQueueForManualExecution: queue management
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import tempfile

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
import yaml

from src.core.safety_controller import Action, SafetyController


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_rules_dir(tmp_path):
    """Create a temporary directory with a minimal rules.yaml for tests."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    rules_content = {
        "risk_levels": {
            "L1_LOW": {
                "threshold": 0.5,
                "actions": ["read_file", "list_directory", "get_file_info"],
                "requirement": "autonomous",
            },
            "L2_MEDIUM": {
                "threshold": 0.7,
                "actions": ["create_file", "edit_file"],
                "requirement": "autonomous",
            },
            "L3_HIGH": {
                "threshold": 0.9,
                "actions": ["write_source", "run_command"],
                "requirement": "autonomous",
            },
            "L4_CRITICAL": {
                "threshold": 1.0,
                "actions": ["delete_file"],
                "requirement": "manual_execute_only",
            },
            "L5_SPECIAL": {
                "threshold": 0.8,
                "actions": ["special_action"],
                "requirement": "notify_and_log",
            },
            "L6_APPROVAL": {
                "threshold": 0.75,
                "actions": ["approval_action"],
                "requirement": "human_approval",
            },
        },
        "non_override_rules": [
            {
                "name": "no_unauthorized_contact",
                "enabled": True,
            },
        ],
    }

    rules_path = config_dir / "rules.yaml"
    rules_path.write_text(yaml.dump(rules_content), encoding="utf-8")

    return tmp_path




# ─── TestAction ──────────────────────────────────────────────────────────


class TestAction:
    """Test Action dataclass creation and defaults."""

    def test_action_creation_minimal(self):
        """Create an Action with only required fields."""
        action = Action(
            type="read_file",
            parameters={"path": "/tmp/test.txt"},
        )
        assert action.type == "read_file"
        assert action.parameters == {"path": "/tmp/test.txt"}
        assert action.confidence == 0.0
        assert action.reasoning is None
        assert action.risk_level is None

    def test_action_creation_full(self):
        """Create an Action with all fields."""
        action = Action(
            type="create_file",
            parameters={"path": "/tmp/new.txt", "content": "hello"},
            confidence=0.95,
            reasoning="User requested file creation",
            risk_level="L2_MEDIUM",
        )
        assert action.type == "create_file"
        assert action.parameters == {"path": "/tmp/new.txt", "content": "hello"}
        assert action.confidence == 0.95
        assert action.reasoning == "User requested file creation"
        assert action.risk_level == "L2_MEDIUM"

    def test_action_confidence_default(self):
        """Confidence defaults to 0.0."""
        action = Action(type="test", parameters={})
        assert action.confidence == 0.0

    def test_action_reasoning_default(self):
        """Reasoning defaults to None."""
        action = Action(type="test", parameters={})
        assert action.reasoning is None

    def test_action_risk_level_default(self):
        """Risk level defaults to None."""
        action = Action(type="test", parameters={})
        assert action.risk_level is None


# ─── TestInit ────────────────────────────────────────────────────────────


class TestInit:
    """Test SafetyController.__init__ and rules loading."""

    def test_init_loads_rules(self, tmp_rules_dir):
        """SafetyController loads rules.yaml on init."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            assert controller.rules is not None
            assert "risk_levels" in controller.rules

    def test_init_sets_rules_path(self, tmp_rules_dir):
        """__init__ sets rules_path from base_path or explicit arg."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            expected = os.path.join(str(tmp_rules_dir), "config", "rules.yaml")
            assert controller.rules_path == expected

    def test_init_sets_custom_rules_path(self, tmp_rules_dir):
        """__init__ respects custom rules_path argument."""
        custom_path = os.path.join(str(tmp_rules_dir), "custom_rules.yaml")
        # Create the custom file
        with open(custom_path, "w") as f:
            yaml.dump({}, f)

        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController(rules_path=custom_path)
            assert controller.rules_path == custom_path

    def test_init_initializes_approval_queue(self, tmp_rules_dir):
        """__init__ initializes empty approval_queue."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            assert controller.approval_queue == []

    def test_init_sets_allowed_write_paths(self, tmp_rules_dir):
        """__init__ sets _allowed_write_paths from base_path."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            assert len(controller._allowed_write_paths) > 0
            # Should be normalized with trailing /
            for path in controller._allowed_write_paths:
                assert path.endswith("/")

    def test_init_missing_rules_file(self, tmp_path):
        """SafetyController handles missing rules.yaml gracefully."""
        # Create a temp directory without rules.yaml
        empty_config = tmp_path / "config"
        empty_config.mkdir()

        with patch("src.core.safety_controller._base_path", return_value=str(tmp_path)):
            controller = SafetyController()
            # Should initialize with empty rules dict
            assert controller.rules == {}

    def test_init_corrupted_yaml(self, tmp_path):
        """SafetyController handles corrupted YAML gracefully."""
        config = tmp_path / "config"
        config.mkdir()
        rules_path = config / "rules.yaml"
        rules_path.write_text("{{{{invalid yaml!!!!", encoding="utf-8")

        with patch("src.core.safety_controller._base_path", return_value=str(tmp_path)):
            controller = SafetyController()
            # Should fall back to empty dict
            assert controller.rules == {}

    def test_init_allowed_write_paths_normalized(self, tmp_rules_dir):
        """Allowed write paths are normalized with realpath."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            for path in controller._allowed_write_paths:
                # Should be an absolute path with trailing /
                assert os.path.isabs(path)
                assert path.endswith("/")


# ─── TestValidatePath ────────────────────────────────────────────────────


class TestValidatePath:
    """Test path validation and workspace isolation."""

    def test_validate_path_allowed(self, tmp_rules_dir):
        """Validate path allows files under allowed write paths."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # Create a test file within the allowed path
            test_file = tmp_rules_dir / "test.txt"
            test_file.write_text("test")

            assert controller.validate_path(str(test_file)) is True

    def test_validate_path_blocked(self, tmp_rules_dir):
        """Validate path blocks files outside allowed write paths."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # Try to access /etc/passwd (almost certainly outside allowed)
            assert controller.validate_path("/etc/passwd") is False

    def test_validate_path_realpath_resolution(self, tmp_rules_dir):
        """Validate path resolves symlinks via realpath."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # Create a file and a symlink to it
            test_file = tmp_rules_dir / "real.txt"
            test_file.write_text("test")
            link_path = tmp_rules_dir / "link.txt"
            link_path.symlink_to(test_file)

            # Both should validate as True (symlink resolves to real file)
            assert controller.validate_path(str(test_file)) is True
            assert controller.validate_path(str(link_path)) is True

    def test_validate_path_empty_allowed_paths(self, tmp_rules_dir):
        """Validate path returns False if no allowed paths are set."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            controller._allowed_write_paths = []

            assert controller.validate_path("/tmp/test.txt") is False

    def test_validate_path_exact_match(self, tmp_rules_dir):
        """Validate path accepts exact match of allowed path."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # Test exact path (the base itself)
            allowed = controller._allowed_write_paths[0].rstrip("/")
            assert controller.validate_path(allowed) is True

    def test_validate_path_subdirectory(self, tmp_rules_dir):
        """Validate path accepts subdirectories of allowed path."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # Test subdirectory
            sub_path = os.path.join(str(tmp_rules_dir), "subdir", "file.txt")
            assert controller.validate_path(sub_path) is True

    def test_validate_path_relative_paths(self, tmp_rules_dir):
        """Validate path resolves relative paths correctly."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # Save current directory and change to tmp_rules_dir
            original_cwd = os.getcwd()
            try:
                os.chdir(str(tmp_rules_dir))
                # Relative path should resolve and validate
                assert controller.validate_path("./test.txt") is True
            finally:
                os.chdir(original_cwd)

    def test_validate_path_exception_handling(self, tmp_rules_dir):
        """Validate path handles exceptions gracefully."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # Path with invalid characters or other issues
            with patch("os.path.realpath") as mock_realpath:
                mock_realpath.side_effect = OSError("Path error")
                assert controller.validate_path("/some/path") is False


# ─── TestGetRiskLevel ────────────────────────────────────────────────────


class TestGetRiskLevel:
    """Test risk level lookup for action types."""

    def test_get_risk_level_known_action(self, tmp_rules_dir):
        """_get_risk_level returns config for known action type."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            risk_config = controller._get_risk_level("read_file")
            assert risk_config is not None
            assert risk_config["_name"] == "L1_LOW"
            assert risk_config["threshold"] == 0.5
            assert risk_config["requirement"] == "autonomous"

    def test_get_risk_level_unknown_action(self, tmp_rules_dir):
        """_get_risk_level returns None for unknown action type."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            risk_config = controller._get_risk_level("unknown_action")
            assert risk_config is None

    def test_get_risk_level_multiple_risk_levels(self, tmp_rules_dir):
        """_get_risk_level correctly distinguishes between risk levels."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            l1 = controller._get_risk_level("read_file")
            l2 = controller._get_risk_level("create_file")
            l3 = controller._get_risk_level("write_source")

            assert l1["_name"] == "L1_LOW"
            assert l2["_name"] == "L2_MEDIUM"
            assert l3["_name"] == "L3_HIGH"

    def test_get_risk_level_includes_name(self, tmp_rules_dir):
        """_get_risk_level includes _name field."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            risk_config = controller._get_risk_level("read_file")
            assert "_name" in risk_config
            assert risk_config["_name"] == "L1_LOW"

    def test_get_risk_level_empty_rules(self, tmp_rules_dir):
        """_get_risk_level handles empty rules gracefully."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            controller.rules = {}

            risk_config = controller._get_risk_level("read_file")
            assert risk_config is None

    def test_get_risk_level_skips_non_dict_levels(self, tmp_rules_dir):
        """_get_risk_level skips non-dict risk level entries."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()
            controller.rules["risk_levels"]["INVALID"] = "not a dict"

            # Should still work for valid levels
            risk_config = controller._get_risk_level("read_file")
            assert risk_config is not None
            assert risk_config["_name"] == "L1_LOW"


# ─── TestIsWriteAction ────────────────────────────────────────────────────


class TestIsWriteAction:
    """Test write action classification."""

    def test_is_write_action_read_only(self, tmp_rules_dir):
        """_is_write_action returns False for read-only actions."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            assert controller._is_write_action("read_file") is False
            assert controller._is_write_action("list_directory") is False
            assert controller._is_write_action("search_files") is False
            assert controller._is_write_action("get_file_info") is False

    def test_is_write_action_write(self, tmp_rules_dir):
        """_is_write_action returns True for write actions."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            assert controller._is_write_action("create_file") is True
            assert controller._is_write_action("edit_file") is True
            assert controller._is_write_action("delete_file") is True
            assert controller._is_write_action("write_source") is True

    def test_is_write_action_custom_action(self, tmp_rules_dir):
        """_is_write_action treats unknown actions as write by default."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            assert controller._is_write_action("custom_action") is True


# ─── TestViolatesNonOverrideRules ────────────────────────────────────────


class TestViolatesNonOverrideRules:
    """Test non-override rules enforcement (path isolation, etc.)."""

    def test_violates_rules_write_to_allowed_path(self, tmp_rules_dir):
        """Write to allowed path does not violate rules."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="create_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
            )

            assert controller._violates_non_override_rules(action) is False

    def test_violates_rules_write_to_blocked_path(self, tmp_rules_dir):
        """Write to blocked path violates rules."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="create_file",
                parameters={"path": "/etc/passwd"},
            )

            assert controller._violates_non_override_rules(action) is True

    def test_violates_rules_read_only_bypasses_path_check(self, tmp_rules_dir):
        """Read-only actions bypass path isolation check."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": "/etc/passwd"},
            )

            assert controller._violates_non_override_rules(action) is False

    def test_violates_rules_multiple_path_params(self, tmp_rules_dir):
        """Checks all path parameters for violations."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            # One valid path, one blocked path
            action = Action(
                type="create_file",
                parameters={
                    "path": str(tmp_rules_dir / "valid.txt"),
                    "dest": "/etc/passwd",
                },
            )

            # Should violate because dest is blocked
            assert controller._violates_non_override_rules(action) is True

    def test_violates_rules_path_param_keys(self, tmp_rules_dir):
        """Tests various path parameter keys."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            path_keys = ["path", "file_path", "dest", "destination", "source", "target"]

            for key in path_keys:
                action = Action(
                    type="create_file",
                    parameters={key: "/etc/passwd"},
                )
                assert controller._violates_non_override_rules(action) is True

    def test_violates_rules_non_string_path_params(self, tmp_rules_dir):
        """Non-string path parameters are ignored."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="create_file",
                parameters={
                    "path": 12345,  # Non-string value
                },
            )

            # Should not raise exception, ignore non-string paths
            assert controller._violates_non_override_rules(action) is False

    def test_violates_rules_no_path_params(self, tmp_rules_dir):
        """Write action without path parameters does not violate path rules."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="create_file",
                parameters={"content": "hello world"},
            )

            # Should not violate (no path parameters to check)
            assert controller._violates_non_override_rules(action) is False


# ─── TestRequestApproval ─────────────────────────────────────────────────


class TestRequestApproval:
    """Test human approval request functionality."""

    def test_request_approval_approved(self, tmp_rules_dir):
        """_request_approval returns True when user approves."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="delete_file",
                parameters={"path": "/tmp/test.txt"},
                confidence=1.0,
                risk_level="L4_CRITICAL",
            )

            with patch("builtins.input", return_value="yes"):
                result = controller._request_approval(action)
                assert result is True

    def test_request_approval_denied(self, tmp_rules_dir):
        """_request_approval returns False when user denies."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="delete_file",
                parameters={"path": "/tmp/test.txt"},
                confidence=1.0,
                risk_level="L4_CRITICAL",
            )

            with patch("builtins.input", return_value="no"):
                result = controller._request_approval(action)
                assert result is False

    def test_request_approval_case_insensitive(self, tmp_rules_dir):
        """_request_approval accepts 'YES' or 'Yes'."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="delete_file",
                parameters={"path": "/tmp/test.txt"},
                confidence=1.0,
            )

            with patch("builtins.input", return_value="YES"):
                result = controller._request_approval(action)
                assert result is True

    def test_request_approval_empty_input(self, tmp_rules_dir):
        """_request_approval treats empty input as denial."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(type="delete_file", parameters={})

            with patch("builtins.input", return_value=""):
                result = controller._request_approval(action)
                assert result is False

    def test_request_approval_eoferror(self, tmp_rules_dir):
        """_request_approval handles EOFError (no input) as denial."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(type="delete_file", parameters={})

            with patch("builtins.input", side_effect=EOFError):
                result = controller._request_approval(action)
                assert result is False

    def test_request_approval_keyboard_interrupt(self, tmp_rules_dir):
        """_request_approval handles KeyboardInterrupt as denial."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(type="delete_file", parameters={})

            with patch("builtins.input", side_effect=KeyboardInterrupt):
                result = controller._request_approval(action)
                assert result is False

    def test_request_approval_prints_details(self, tmp_rules_dir, capsys):
        """_request_approval displays action details."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="delete_file",
                parameters={"path": "/tmp/test.txt"},
                confidence=0.95,
                reasoning="User requested deletion",
                risk_level="L4_CRITICAL",
            )

            with patch("builtins.input", return_value="no"):
                controller._request_approval(action)
                captured = capsys.readouterr()

                # Check that key details are printed
                assert "delete_file" in captured.out
                assert "L4_CRITICAL" in captured.out
                assert "95" in captured.out


# ─── TestQueueForManualExecution ─────────────────────────────────────────


class TestQueueForManualExecution:
    """Test approval queue management."""

    def test_queue_for_manual_execution_adds_to_queue(self, tmp_rules_dir):
        """_queue_for_manual_execution adds action to approval_queue."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="delete_file",
                parameters={"path": "/tmp/test.txt"},
            )

            assert len(controller.approval_queue) == 0
            controller._queue_for_manual_execution(action)
            assert len(controller.approval_queue) == 1
            assert controller.approval_queue[0] == action

    def test_queue_for_manual_execution_multiple_actions(self, tmp_rules_dir):
        """Queue stores multiple actions in order."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action1 = Action(type="delete_file", parameters={"path": "/tmp/1.txt"})
            action2 = Action(type="delete_file", parameters={"path": "/tmp/2.txt"})

            controller._queue_for_manual_execution(action1)
            controller._queue_for_manual_execution(action2)

            assert len(controller.approval_queue) == 2
            assert controller.approval_queue[0] == action1
            assert controller.approval_queue[1] == action2

    def test_queue_for_manual_execution_logs_action(self, tmp_rules_dir):
        """_queue_for_manual_execution logs the queued action."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(type="delete_file", parameters={})

            with patch("src.core.safety_controller.logger") as mock_logger:
                controller._queue_for_manual_execution(action)
                mock_logger.info.assert_called()


# ─── TestAuthorize ───────────────────────────────────────────────────────


class TestAuthorize:
    """Test full authorization check chain."""

    def test_authorize_autonomous_action(self, tmp_rules_dir):
        """Autonomous actions return True without approval."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=0.6,
            )

            result = controller.authorize(action)
            assert result is True

    def test_authorize_notify_and_log_action(self, tmp_rules_dir):
        """notify_and_log actions return True and log."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="special_action",
                parameters={},
                confidence=0.8,
            )

            with patch("src.core.safety_controller.logger") as mock_logger:
                result = controller.authorize(action)
                assert result is True
                mock_logger.info.assert_called()

    def test_authorize_human_approval_granted(self, tmp_rules_dir):
        """human_approval returns True when user approves."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="approval_action",
                parameters={},
                confidence=0.8,
            )

            with patch.object(controller, "_request_approval", return_value=True):
                result = controller.authorize(action)
                assert result is True

    def test_authorize_human_approval_denied(self, tmp_rules_dir):
        """human_approval returns False when user denies."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="approval_action",
                parameters={},
                confidence=0.8,
            )

            with patch.object(controller, "_request_approval", return_value=False):
                result = controller.authorize(action)
                assert result is False

    def test_authorize_manual_execute_only(self, tmp_rules_dir):
        """manual_execute_only queues action and returns False."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="delete_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=1.0,
            )

            result = controller.authorize(action)
            assert result is False
            assert len(controller.approval_queue) == 1
            assert controller.approval_queue[0] == action

    def test_authorize_unknown_action(self, tmp_rules_dir):
        """Unknown action types are denied."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="unknown_action",
                parameters={},
                confidence=0.9,
            )

            result = controller.authorize(action)
            assert result is False

    def test_authorize_confidence_too_low(self, tmp_rules_dir):
        """Actions below confidence threshold are denied."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=0.3,  # Below L1_LOW threshold of 0.5
            )

            result = controller.authorize(action)
            assert result is False

    def test_authorize_violates_non_override_rules(self, tmp_rules_dir):
        """Actions violating non-override rules are denied."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="create_file",
                parameters={"path": "/etc/passwd"},
                confidence=0.9,
            )

            result = controller.authorize(action)
            assert result is False

    def test_authorize_sets_risk_level_on_action(self, tmp_rules_dir):
        """authorize sets the risk_level field on the action."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=0.6,
            )

            assert action.risk_level is None
            controller.authorize(action)
            assert action.risk_level == "L1_LOW"

    def test_authorize_logs_low_confidence(self, tmp_rules_dir):
        """authorize logs warning for low confidence."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=0.3,
            )

            with patch("src.core.safety_controller.logger") as mock_logger:
                controller.authorize(action)
                # Should log warning about confidence
                calls = [str(call) for call in mock_logger.warning.call_args_list]
                assert any("Confidence too low" in str(c) for c in calls)

    def test_authorize_logs_blocked_action(self, tmp_rules_dir):
        """authorize logs error for blocked actions."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="create_file",
                parameters={"path": "/etc/passwd"},
                confidence=0.9,
            )

            with patch("src.core.safety_controller.logger") as mock_logger:
                controller.authorize(action)
                # Should log error about violation
                assert mock_logger.error.called

    def test_authorize_exact_confidence_threshold(self, tmp_rules_dir):
        """Action with confidence exactly at threshold is accepted."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=0.5,  # Exactly L1_LOW threshold
            )

            result = controller.authorize(action)
            assert result is True

    def test_authorize_confidence_slightly_below_threshold(self, tmp_rules_dir):
        """Action with confidence just below threshold is rejected."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=0.49,  # Just below L1_LOW threshold of 0.5
            )

            result = controller.authorize(action)
            assert result is False


# ─── Integration Tests ───────────────────────────────────────────────────


class TestAuthorizationFlow:
    """Integration tests for common authorization flows."""

    def test_flow_autonomous_read_action(self, tmp_rules_dir):
        """Complete flow: read action passes all checks."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "config" / "rules.yaml")},
                confidence=0.8,
                reasoning="Reading configuration",
            )

            assert controller.authorize(action) is True
            assert action.risk_level == "L1_LOW"

    def test_flow_denied_unauthorized_write(self, tmp_rules_dir):
        """Complete flow: write outside workspace is blocked."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="create_file",
                parameters={"path": "/root/.ssh/id_rsa"},
                confidence=0.95,
                reasoning="Attempting unauthorized file creation",
            )

            assert controller.authorize(action) is False

    def test_flow_blocked_then_queued(self, tmp_rules_dir):
        """Flow: manual execution action is queued and returns False."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            action = Action(
                type="delete_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=1.0,
                reasoning="Critical file deletion",
            )

            result = controller.authorize(action)
            assert result is False
            assert action in controller.approval_queue

    def test_flow_multiple_actions_mixed_results(self, tmp_rules_dir):
        """Flow: multiple actions with different outcomes."""
        with patch("src.core.safety_controller._base_path", return_value=str(tmp_rules_dir)):
            controller = SafetyController()

            read_action = Action(
                type="read_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=0.8,
            )

            delete_action = Action(
                type="delete_file",
                parameters={"path": str(tmp_rules_dir / "test.txt")},
                confidence=1.0,
            )

            blocked_action = Action(
                type="create_file",
                parameters={"path": "/etc/passwd"},
                confidence=0.9,
            )

            # Read succeeds
            assert controller.authorize(read_action) is True

            # Delete is queued
            assert controller.authorize(delete_action) is False
            assert len(controller.approval_queue) == 1

            # Blocked returns False
            assert controller.authorize(blocked_action) is False
            assert len(controller.approval_queue) == 1  # Not queued (blocked before that)
