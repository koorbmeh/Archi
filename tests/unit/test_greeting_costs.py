"""
Test that greetings cost $0.00 (no model call).

Verifies the hardcoded greeting path: "Hello?", "hi", etc. return instantly
without calling local model or Grok.
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest
from src.interfaces.action_executor import process_message


class MockRouter:
    """Router that raises if called - proves greeting path doesn't use it."""

    def generate(self, **kwargs):
        raise AssertionError("Greeting should not call router.generate (costs $0)")


def test_hello_costs_zero():
    """'Hello?' returns hardcoded response with $0 cost."""
    router = MockRouter()
    response, actions, cost = process_message("Hello?", router=router, source="test")
    assert cost == 0.0
    assert "ready to help" in response.lower()


def test_hello_variants_cost_zero():
    """Various greeting variants cost $0."""
    router = MockRouter()
    for msg in ("hi", "hey", "good morning", "how are you", "you there?"):
        response, _, cost = process_message(msg, router=router, source="test")
        assert cost == 0.0, f"'{msg}' should cost $0"
        assert "ready" in response.lower() or "help" in response.lower()
