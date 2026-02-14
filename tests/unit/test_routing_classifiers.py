"""
Unit tests for the three routing classifiers in action_executor.py.

These classifiers determine how incoming messages are handled:
  1. _is_greeting_or_social() — detects pure greetings/social (→ hardcoded response, $0)
  2. _needs_multi_step()       — detects research/analysis/multi-part (→ PlanExecutor, 12 steps)
  3. _is_coding_request_check() — detects code modification/creation (→ PlanExecutor, 30 steps)

The classifiers are evaluated in this priority order:
  greeting → coding → multi-step → intent model

A message that matches an earlier classifier never reaches a later one. These tests
verify that each classifier fires (or doesn't) for a range of realistic inputs,
and that the priority ordering produces correct routing for ambiguous messages.
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.interfaces.action_executor import (
    _is_greeting_or_social,
    _needs_multi_step,
    _is_coding_request_check,
)


# ============================================================================
# 1. _is_greeting_or_social
# ============================================================================

class TestIsGreetingOrSocial:
    """Tests for the greeting/social classifier."""

    # ---- Should return True (pure social) ----

    @pytest.mark.parametrize("msg", [
        "hi",
        "hello",
        "hey ",
        "good morning",
        "good evening",
        "good night",
        "howdy",
        "greetings",
        "hi friend",
        "hello friend",
    ])
    def test_basic_greetings(self, msg):
        assert _is_greeting_or_social(msg) is True

    @pytest.mark.parametrize("msg", [
        "hi Archi",
        "hey Archi",
        "hello Archi!",
        "hi buddy",
        "hey friend",
        "hello mate",
        "hi pal",
        "hey dude",
        "hi bro",
    ])
    def test_greetings_with_name(self, msg):
        assert _is_greeting_or_social(msg) is True

    @pytest.mark.parametrize("msg", [
        "hi Archi, how's it going?",
        "hello! how are you?",
        "hey there!",
    ])
    def test_greetings_with_short_tail(self, msg):
        """Short trailing content (<=15 chars after stripping) is still social."""
        assert _is_greeting_or_social(msg) is True

    def test_hey_comma_now_matched(self):
        """'hey,' (comma variant) now matches as a greeting prefix."""
        assert _is_greeting_or_social("hey, Archi") is True
        assert _is_greeting_or_social("hey, buddy") is True

    def test_hi_comma_now_matched(self):
        """'hi,' (comma variant) now matches as a greeting prefix."""
        assert _is_greeting_or_social("hi, Archi") is True

    def test_whats_up_social_exception(self):
        """'what's up' is a social exception — fires before action keywords."""
        assert _is_greeting_or_social("hey what's up?") is True
        assert _is_greeting_or_social("hey, what's up?") is True
        assert _is_greeting_or_social("what's up") is True
        assert _is_greeting_or_social("what's new") is True
        assert _is_greeting_or_social("what's going on") is True

    def test_whats_exceptions_dont_override_real_requests(self):
        """Social exceptions have length < 200, so they're fine. But verify that
        'what's the status' still triggers action keywords (it has 'what's' but
        NOT in the social exceptions list)."""
        # "what's the status" doesn't match any social exception phrase
        # but DOES match "what's" action keyword → returns False
        assert _is_greeting_or_social("what's the status of the project?") is False

    @pytest.mark.parametrize("msg", [
        "how are you",
        "how's it going",
        "how are things",
        "are you there",
        "you there",
        "still there",
        "still working",
        "still functioning",
        "checking on you",
        "going to bed",
        "going to sleep",
        "good night",
        "see you",
        "catch you",
    ])
    def test_social_phrases(self, msg):
        assert _is_greeting_or_social(msg) is True

    @pytest.mark.parametrize("msg", [
        "good job",
        "nice work",
        "well done",
        "great job",
        "thanks",
        "thank you",
        "perfect",
        "excellent",
        "awesome",
        "that's right",
        "correct",
        "exactly",
        "brilliant",
        "fantastic",
        "nailed it",
        "spot on",
        "good job!",
        "nice work.",
    ])
    def test_praise(self, msg):
        assert _is_greeting_or_social(msg) is True

    def test_empty_and_none(self):
        assert _is_greeting_or_social("") is False
        assert _is_greeting_or_social(None) is False

    # ---- Should return False (NOT social — has substantive content) ----

    @pytest.mark.parametrize("msg", [
        "Hey Archi. Make it a goal to read all files in the workspace.",
        "Hi! Can you research thermal paste options for me?",
        "Hello, please search for the latest news on AI regulation.",
        "Hey Archi, look into the best CPU coolers for a Ryzen 9.",
        "Good morning! Could you run the tests for me?",
        "Hi there, fix the bug in router.py that causes timeouts.",
    ])
    def test_greeting_plus_request(self, msg):
        """Greeting prefix + real request should NOT be classified as social."""
        assert _is_greeting_or_social(msg) is False

    @pytest.mark.parametrize("msg", [
        "search for thermal paste reviews",
        "can you help me with something?",
        "please research the best monitors",
        "tell me about quantum computing",
        "what is the capital of France?",
        "how do I install pytorch?",
        "where is the config file?",
        "add a new goal for me",
        "remind me to check the logs tomorrow",
    ])
    def test_action_keyword_messages(self, msg):
        """Messages with action keywords should never be social."""
        assert _is_greeting_or_social(msg) is False

    @pytest.mark.parametrize("msg", [
        "create a file called notes.txt",
        "write a report on AI safety",
        "make a file for my notes",
        "create file test.md",
        "save this as output.txt",
    ])
    def test_file_creation_intent(self, msg):
        """File creation patterns should never be social."""
        assert _is_greeting_or_social(msg) is False

    def test_slash_commands(self):
        """Slash commands are never social."""
        assert _is_greeting_or_social("/goal research AI") is False
        assert _is_greeting_or_social("/status") is False
        assert _is_greeting_or_social("/help") is False
        assert _is_greeting_or_social("/cost") is False

    def test_long_messages(self):
        """Messages over 200 chars are never social."""
        long_msg = "hello " + "a" * 200
        assert _is_greeting_or_social(long_msg) is False

    def test_praise_with_extra_content(self):
        """Praise words with additional text aren't exact-match praise."""
        # These don't exact-match the praise list
        assert _is_greeting_or_social("good job, now research thermal paste") is False
        assert _is_greeting_or_social("thanks, can you also look into X?") is False


# ============================================================================
# 2. _needs_multi_step
# ============================================================================

class TestNeedsMultiStep:
    """Tests for the multi-step request classifier."""

    # ---- Should return True (multi-step) ----

    @pytest.mark.parametrize("msg", [
        "research the best thermal paste for CPUs",
        "investigate why the API is returning errors",
        "look into the best monitors for programming",
        "find out about the latest GPU releases",
        "dig into the performance issues we've been having",
        "deep dive into Archi's dream cycle behavior",
        "explore different approaches to caching",
        "study the effects of context window size",
        "analyze the cost data from last week",
        "compare Intel vs AMD for my next build",
        "evaluate the top 5 vector databases",
        "review the dream cycle output quality",
    ])
    def test_research_patterns(self, msg):
        assert _needs_multi_step(msg) is True

    @pytest.mark.parametrize("msg", [
        "write a report on AI safety trends",
        "write a summary of the project status",
        "write me a report on thermal paste options",
        "write a document about the architecture",
        "write up the findings from yesterday",
        "write an analysis of the cost data",
        "put together a report on GPU options",
        "put together a summary of what Archi did",
        "compile all the research into one document",
        "gather information about the latest CPUs",
    ])
    def test_report_writing_patterns(self, msg):
        assert _needs_multi_step(msg) is True

    @pytest.mark.parametrize("msg", [
        "create files for a new Python module",
        "create 3 test files for the classifiers",
        "organize the workspace reports folder",
        "clean up the old log files",
        "set up a new project directory",
        "build a dashboard for monitoring costs",
        "summarize the files in workspace/reports",
        "read all the reports and tell me what's useful",
        "go through the dream logs from this week",
        "process all the CSV files in data/",
    ])
    def test_workspace_patterns(self, msg):
        assert _needs_multi_step(msg) is True

    @pytest.mark.parametrize("msg", [
        "search for thermal paste and then write a comparison",
        "find the best GPU and then create a report",
        "research monitors and also check prices",
        "look up CPUs and create a spreadsheet",
        "check the logs and then summarize them",
        "read the config and then send me the values",
    ])
    def test_multi_task_signals(self, msg):
        assert _needs_multi_step(msg) is True

    @pytest.mark.parametrize("msg", [
        "figure out why the tests are failing",
        "work on the thermal paste research",
        "handle the dream cycle optimization",
        "take care of the log cleanup",
        "get me the latest news on AI",
        "fetch the data from the API",
        "download the latest model weights",
        "check on the dream cycle progress",
        "monitor the API costs for today",
        "track the response times over the last hour",
    ])
    def test_work_verbs(self, msg):
        assert _needs_multi_step(msg) is True

    def test_search_with_conjunction(self):
        """'search for X and Y' implies thorough research."""
        assert _needs_multi_step("search for thermal paste and CPU coolers") is True
        assert _needs_multi_step("search for GPUs then compare prices") is True

    # ---- Should return False (NOT multi-step) ----

    @pytest.mark.parametrize("msg", [
        "hi",
        "thanks",
        "yes",
        "no",
        "ok",
        "sure",
        "",
        None,
    ])
    def test_short_and_empty(self, msg):
        """Short messages (<15 chars) and empty/None should not be multi-step."""
        assert _needs_multi_step(msg) is False

    @pytest.mark.parametrize("msg", [
        "what time is it right now?",
        "how's the weather in Seattle?",
        "what did you do last night?",
        "tell me a joke about programming",
        "what's your favorite color?",
        "who won the Super Bowl this year?",
        "when was Python first released?",
    ])
    def test_simple_questions(self, msg):
        """Simple conversational questions should NOT trigger multi-step."""
        assert _needs_multi_step(msg) is False

    @pytest.mark.parametrize("msg", [
        "take a screenshot of my desktop",
        "what's 2 + 2?",
        "list the files in workspace/",
        "read the config file for me",
        "hello, how are you doing today?",
    ])
    def test_single_action_requests(self, msg):
        """Single-action requests should stay in the intent model."""
        assert _needs_multi_step(msg) is False


# ============================================================================
# 3. _is_coding_request_check
# ============================================================================

class TestIsCodingRequestCheck:
    """Tests for the coding fast-path classifier."""

    # ---- Should return True (coding request) ----

    @pytest.mark.parametrize("msg", [
        "add a function to parse JSON in router.py",
        "add a method to the GoalManager class",
        "add a class for handling websocket connections",
        "add function get_stats to performance_monitor.py",
        "add method validate_input to the form handler",
    ])
    def test_add_code_patterns(self, msg):
        assert _is_coding_request_check(msg) is True

    @pytest.mark.parametrize("msg", [
        "modify the greeting handler to be less aggressive",
        "change the code to use async/await",
        "update the code for the new API version",
        "fix the code that handles timeouts",
        "fix the bug in the dream cycle",
        "fix this bug where greetings drop commands",
        "edit the file to add error handling",
        "edit this file and remove the dead code",
        "refactor the action executor into smaller functions",
        "rewrite the caching logic to be more efficient",
    ])
    def test_modification_patterns(self, msg):
        assert _is_coding_request_check(msg) is True

    @pytest.mark.parametrize("msg", [
        "create a script to migrate the database",
        "write a script that backs up the logs",
        "create a module for cost tracking",
        "write a function that validates URLs",
        "write a class for managing sessions",
        "write code to handle the new webhook",
        "implement a retry mechanism for API calls",
        "add a feature for exporting data as CSV",
    ])
    def test_creation_patterns(self, msg):
        assert _is_coding_request_check(msg) is True

    @pytest.mark.parametrize("msg", [
        "run the tests",
        "run tests for the router module",
        "run pytest on the unit tests",
        "run the command to start the server",
        "pip install requests",
        "npm install lodash",
        "install the package from requirements.txt",
    ])
    def test_command_patterns(self, msg):
        assert _is_coding_request_check(msg) is True

    def test_install_not_overly_broad(self):
        """'install' without code context should NOT trigger coding path."""
        assert _is_coding_request_check("install the new kitchen shelves") is False
        assert _is_coding_request_check("install numpy for data processing") is False
        # But 'install' + file extension still works via verb+ext combo
        assert _is_coding_request_check("install the plugin from config.yaml") is True

    @pytest.mark.parametrize("msg", [
        "add to src/ a new utility module",
        "modify src/core/plan_executor.py",
        "update src/models/router.py with the new logic",
        "fix src/interfaces/action_executor.py",
        "edit src/tools/tool_registry.py",
        "modify config/rules.yaml to increase budget",
    ])
    def test_path_reference_patterns(self, msg):
        assert _is_coding_request_check(msg) is True

    @pytest.mark.parametrize("msg", [
        "fix the timeout in router.py",
        "update dream_cycle.py to handle edge cases",
        "edit action_executor.py line 500",
        "remove the old handler from bot.js",
        "rename the class in schema.ts",
        "create a new config.yaml for staging",
        "delete the unused styles.css file",
        "add validation to form.html",
        "refactor the parsing in data.json loader",
    ])
    def test_file_extension_plus_verb(self, msg):
        """File extension + action verb combo should trigger coding path."""
        assert _is_coding_request_check(msg) is True

    # ---- Should return False (NOT coding) ----

    @pytest.mark.parametrize("msg", [
        "",
        None,
    ])
    def test_empty_and_none(self, msg):
        assert _is_coding_request_check(msg) is False

    @pytest.mark.parametrize("msg", [
        "what does the router.py file do?",
        "explain the code in plan_executor.py",
        "how does the greeting handler work?",
        "tell me about the dream cycle",
        "what is action_executor.py responsible for?",
    ])
    def test_questions_about_code(self, msg):
        """Asking ABOUT code (no action verb as standalone word) is not a coding request."""
        assert _is_coding_request_check(msg) is False

    @pytest.mark.parametrize("msg", [
        "research the best Python frameworks",
        "compare different testing approaches",
        "search for thermal paste reviews",
        "what time is it?",
        "hello, how are you?",
        "good morning Archi",
        "tell me a joke",
    ])
    def test_non_coding_requests(self, msg):
        """Research, greetings, and conversation should NOT trigger coding path."""
        assert _is_coding_request_check(msg) is False


# ============================================================================
# 4. Routing Priority / Interaction Tests
# ============================================================================

class TestRoutingPriority:
    """Tests for correct routing when messages could match multiple classifiers.

    Priority order: greeting → coding → multi-step → intent model
    A message that matches an earlier classifier should NOT reach later ones.
    """

    def test_greeting_with_code_keywords(self):
        """'Hi Archi' should be greeting, not coding, even though it could match other things."""
        assert _is_greeting_or_social("hi Archi") is True
        # The coding classifier would also get this message, but greeting wins by priority
        # This is a routing-order test — greeting fires first

    def test_greeting_blocks_multi_step(self):
        """Pure greetings should not leak into multi-step."""
        msg = "how are you"
        assert _is_greeting_or_social(msg) is True
        # _needs_multi_step might also match (length check), verify it doesn't matter
        # because greeting fires first in the actual routing

    def test_coding_request_also_matches_multi_step(self):
        """Some coding requests contain multi-step keywords. Coding should win."""
        msg = "implement a caching layer and then write tests for it"
        # This has "implement " (coding pattern) AND " and then " (multi-step signal)
        assert _is_coding_request_check(msg) is True
        assert _needs_multi_step(msg) is True
        # In actual routing, coding fires first — no conflict

    def test_research_not_coding(self):
        """Pure research should be multi-step, not coding."""
        msg = "research the best approaches to error handling in Python"
        assert _is_coding_request_check(msg) is False
        assert _needs_multi_step(msg) is True

    def test_greeting_plus_code_request_not_social(self):
        """'Hey Archi, fix the bug in router.py' — greeting prefix but real request."""
        msg = "Hey Archi, fix the bug in router.py"
        assert _is_greeting_or_social(msg) is False
        assert _is_coding_request_check(msg) is True

    def test_greeting_plus_research_not_social(self):
        """'Hello! Can you research thermal paste?' — not social, is multi-step."""
        msg = "Hello! Can you research thermal paste for me?"
        assert _is_greeting_or_social(msg) is False
        assert _needs_multi_step(msg) is True

    def test_simple_chat_falls_through_all(self):
        """A simple question shouldn't match any classifier → intent model."""
        msg = "what time is it right now?"
        assert _is_greeting_or_social(msg) is False
        assert _is_coding_request_check(msg) is False
        assert _needs_multi_step(msg) is False

    def test_praise_doesnt_leak(self):
        """Short praise should be social, not coding or multi-step."""
        msg = "good job"
        assert _is_greeting_or_social(msg) is True
        assert _is_coding_request_check(msg) is False
        assert _needs_multi_step(msg) is False

    def test_file_question_not_coding(self):
        """Asking about a .py file without an action verb should not be coding."""
        msg = "what does the router.py file do?"
        assert _is_coding_request_check(msg) is False
        # "what" triggers action keyword in greeting check → not social
        assert _is_greeting_or_social(msg) is False

    def test_pip_install_is_coding(self):
        """'pip install X' matches coding. Verify routing is correct."""
        msg = "pip install pytorch for the AI project"
        assert _is_coding_request_check(msg) is True


# ============================================================================
# 5. Edge Cases & Regression Guards
# ============================================================================

class TestEdgeCases:
    """Edge cases and regression tests for known failure modes."""

    def test_greeting_with_goal_request(self):
        """Regression: 'Hey Archi. Make it a goal to read all files' was being
        swallowed by greeting handler. Should NOT be social."""
        msg = "Hey Archi. Make it a goal to read all files in the workspace."
        assert _is_greeting_or_social(msg) is False

    def test_case_insensitivity(self):
        """All classifiers should be case-insensitive."""
        assert _is_greeting_or_social("HELLO") is True
        assert _is_greeting_or_social("Hello") is True
        assert _needs_multi_step("RESEARCH the best thermal paste for CPUs") is True
        assert _is_coding_request_check("FIX THE BUG in router.py") is True

    def test_whitespace_handling(self):
        """Leading/trailing whitespace should be handled."""
        assert _is_greeting_or_social("  hello  ") is True
        assert _needs_multi_step("  research thermal paste options  ") is True
        assert _is_coding_request_check("  fix the bug in router.py  ") is True

    def test_multiline_messages(self):
        """Multi-line messages should work correctly."""
        msg = "Hey Archi.\nCan you research the best thermal paste?\nI need it for my CPU."
        # Has action keyword "can you" and "research" → not social
        assert _is_greeting_or_social(msg) is False
        assert _needs_multi_step(msg) is True

    def test_exact_boundary_15_chars(self):
        """Test the 15-char remainder boundary in greeting checker."""
        # "hi archi" → remainder after stripping "hi " + "archi" = ""
        assert _is_greeting_or_social("hi archi") is True
        # "hi archi, yes ok cool" → remainder = "yes ok cool" (11 chars) → social
        assert _is_greeting_or_social("hi archi, yes ok cool") is True
        # "hi archi, what should we work on today" → remainder > 15 chars → not social
        # BUT "what" is an action keyword, so it returns False even earlier
        assert _is_greeting_or_social("hi archi, what should we work on today") is False

    def test_needs_multi_step_14_char_boundary(self):
        """Messages under 15 chars should not be multi-step."""
        assert _needs_multi_step("research it") is False  # 11 chars
        assert _needs_multi_step("research this!") is False  # 14 chars
        assert _needs_multi_step("research this!!") is True  # 15 chars, has pattern

    def test_coding_verb_must_be_standalone_word(self):
        """Coding verbs are checked with split(), so they must be standalone words.
        'edited' contains 'edit' but split() gives 'edited' not 'edit'."""
        # "I edited the file" — "edited" is not in _CODE_VERBS
        # But "edit the file" IS a code pattern match, so this is tricky
        msg = "I already edited config.yaml yesterday"
        # "edited" doesn't match verb "edit" via split()
        # But ".yaml" is a code extension — need both ext AND verb
        # "edited" won't match "edit" in split, "already" etc. also won't match
        # However "I" won't match. Let's check: split gives ["i", "already", "edited", "config.yaml", "yesterday"]
        # None of those are in _CODE_VERBS → should be False
        assert _is_coding_request_check(msg) is False

    def test_partial_word_no_false_positive(self):
        """'modify ' has a trailing space in the pattern. 'modification' shouldn't match...
        Actually the pattern check is substring, so 'modification' contains 'modify '.
        Wait — 'modification' doesn't contain 'modify ' (with space). Let's verify."""
        msg = "the modification was successful"
        # "modify " (with trailing space) — "modification" doesn't have a space after "modify"
        assert _is_coding_request_check(msg) is False

    def test_no_false_positive_on_implement_in_sentence(self):
        """'implement ' is a coding pattern. 'implementation' starts with it.
        'implementation' contains 'implement ' only if there's a space. Let's check."""
        # "The implementation looks good" — does it contain "implement "?
        # "implementation" → does "implementation" contain "implement "? No — "implementa" not "implement "
        # Actually: "implement " (with space) vs "implementation" — no space after "implement" in "implementation"
        msg = "the implementation looks good"
        assert _is_coding_request_check(msg) is False
