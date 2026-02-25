"""Unit tests for purpose-driven goal validation.

Tests is_purpose_driven() in idea_generator — the gate that rejects
brainstorm ideas that are just "research X" without a concrete deliverable.
"""

import pytest

from src.core.idea_generator import is_purpose_driven, _DELIVERABLE_VERBS


class TestIsPurposeDriven:
    """Tests for is_purpose_driven() validation."""

    # ── Should PASS (good goals) ────────────────────────────────────

    def test_update_existing_file(self):
        """Updating an existing project file is purpose-driven."""
        assert is_purpose_driven(
            "Update workspace/projects/Health_Optimization/supplements.md "
            "with latest creatine timing evidence"
        )

    def test_create_new_file(self):
        """Creating a new file in a project is purpose-driven."""
        assert is_purpose_driven(
            "Create workspace/projects/Health_Optimization/sleep_protocol.md "
            "synthesizing current stack with new dosing data"
        )

    def test_extend_readme(self):
        """Extending a README with specific content is purpose-driven."""
        assert is_purpose_driven(
            "Extend the Archi README.md troubleshooting section with common setup failures"
        )

    def test_add_to_project(self):
        """Adding content to a project folder is purpose-driven."""
        assert is_purpose_driven(
            "Add a comparison table in workspace/projects/Health_Optimization/stack_risks.md"
        )

    def test_synthesize_data(self):
        """Synthesizing data into a file is purpose-driven."""
        assert is_purpose_driven(
            "Synthesize sleep research into workspace/projects/Health_Optimization/sleep.md"
        )

    def test_build_script(self):
        """Building a Python script is purpose-driven."""
        assert is_purpose_driven(
            "Build a data analysis script at workspace/projects/Archi/analyze.py"
        )

    def test_consolidate_notes(self):
        """Consolidating notes into a file is purpose-driven."""
        assert is_purpose_driven(
            "Consolidate supplement notes into workspace/projects/Health_Optimization/master.md"
        )

    def test_restructure_project(self):
        """Restructuring project files is purpose-driven."""
        assert is_purpose_driven(
            "Restructure workspace/projects/Health_Optimization/supplements.md into separate sections"
        )

    def test_revise_document(self):
        """Revising an existing document is purpose-driven."""
        assert is_purpose_driven(
            "Revise workspace/projects/Health_Optimization/protocol.md with updated dosing"
        )

    def test_generate_report_in_project(self):
        """Generating a report within a project path is purpose-driven."""
        assert is_purpose_driven(
            "Generate a weekly metrics report at workspace/projects/Health_Optimization/metrics.json"
        )

    # ── Should FAIL (bad goals — research without deliverable) ──────

    def test_research_only(self):
        """Pure research with no file reference fails."""
        assert not is_purpose_driven("Research creatine timing studies")

    def test_investigate_topic(self):
        """Investigating a topic with no deliverable fails."""
        assert not is_purpose_driven("Investigate longevity interventions and findings")

    def test_compile_information(self):
        """Even 'compile' fails without a file path."""
        assert not is_purpose_driven("Compile information about sleep supplements")

    def test_study_topic(self):
        """Studying a topic is not purpose-driven."""
        assert not is_purpose_driven("Study the effects of magnesium on sleep")

    def test_explore_options(self):
        """Exploring options is not purpose-driven."""
        assert not is_purpose_driven("Explore different nootropic stacks for focus")

    def test_look_into(self):
        """'Look into' is not purpose-driven."""
        assert not is_purpose_driven("Look into the latest AI agent frameworks")

    def test_no_verb_but_has_path(self):
        """Having a path but no deliverable verb fails."""
        assert not is_purpose_driven(
            "workspace/projects/Health_Optimization/supplements.md needs attention"
        )

    def test_verb_but_no_path(self):
        """Having a verb but no file path fails."""
        assert not is_purpose_driven("Update the health optimization protocol")

    def test_empty_string(self):
        """Empty string is not purpose-driven."""
        assert not is_purpose_driven("")

    def test_generic_report(self):
        """Generic 'write a report' without a path fails."""
        assert not is_purpose_driven("Write a report on supplement interactions")


class TestDeliverableVerbs:
    """Verify the deliverable verb set is comprehensive."""

    def test_core_verbs_present(self):
        """Core deliverable verbs are in the set."""
        expected = {"update", "create", "add", "extend", "synthesize", "build"}
        assert expected.issubset(_DELIVERABLE_VERBS)

    def test_editing_verbs_present(self):
        """Editing-related verbs are in the set."""
        expected = {"revise", "merge", "refactor", "restructure"}
        assert expected.issubset(_DELIVERABLE_VERBS)

    def test_research_verbs_absent(self):
        """Research-only verbs should NOT be in the set."""
        bad_verbs = {"research", "investigate", "study", "explore", "examine", "analyze"}
        assert not bad_verbs & _DELIVERABLE_VERBS


class TestSuggestWorkPromptContent:
    """Verify the suggest_work pipeline includes purpose-driven guidance.

    After session 135 refactor, the brainstorm prompt lives in _brainstorm_fallback()
    and opportunity scoring lives in _scan_for_opportunities(). Inspect the module
    source rather than individual functions.
    """

    def _get_module_source(self):
        import src.core.idea_generator as mod
        import inspect
        return inspect.getsource(mod)

    def test_prompt_has_concrete_deliverable_guidance(self):
        """The brainstorm prompt mentions concrete deliverables."""
        source = self._get_module_source()
        assert "deliverable" in source.lower() or "target_file" in source

    def test_prompt_has_benefit_scoring(self):
        """The brainstorm prompt includes benefit scoring."""
        source = self._get_module_source()
        assert "benefit" in source

    def test_prompt_mentions_end_state(self):
        """The opportunity scanner populates an end_state field."""
        source = self._get_module_source()
        assert "end_state" in source

    def test_prompt_mentions_target_file(self):
        """The brainstorm prompt asks for a target_file field."""
        source = self._get_module_source()
        assert "target_file" in source

    def test_prompt_mentions_target_file_field(self):
        """The brainstorm prompt asks for a target_file field in its JSON schema."""
        source = self._get_module_source()
        assert "target_file" in source


class TestFollowUpTaskPromptContent:
    """Verify the follow-up task prompt stays within goal scope."""

    def test_followup_mentions_scope(self):
        """The follow-up prompt requires tasks within goal scope."""
        from src.core.autonomous_executor import extract_follow_up_tasks
        import inspect
        source = inspect.getsource(extract_follow_up_tasks)
        assert "WITHIN" in source or "within" in source

    def test_followup_prevents_duplicates(self):
        """The follow-up prompt tells the model about existing tasks."""
        from src.core.autonomous_executor import _build_follow_up_prompt
        import inspect
        source = inspect.getsource(_build_follow_up_prompt)
        assert "DO NOT duplicate" in source or "existing tasks" in source

    def test_followup_allows_empty(self):
        """The follow-up prompt allows returning an empty array."""
        from src.core.autonomous_executor import _build_follow_up_prompt
        import inspect
        source = inspect.getsource(_build_follow_up_prompt)
        assert "empty array" in source or "[]" in source
