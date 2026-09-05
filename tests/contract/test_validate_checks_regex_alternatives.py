"""Validate Configuration must judge a regex field by more than whether it compiles.

A user's Regex: Channel Names to Ignore field compiled perfectly and silently
skipped every channel whose name contained the text "USA ", because the four
channel group names they pasted in each already contained a pipe. Validate
Configuration reported it as Valid.

The judgement itself is unit-tested in
tests/unit/test_regex_alternative_problems.py. This is the single structural
test that keeps it wired into the action. plugin.py imports Django at module
scope and cannot be imported outside the container, so its structure is read
with ast, and every assertion is scoped to the regex branch of the action
rather than searched for across the whole method.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

SUMMARY_FUNCTION = "regex_alternative_summary"
DETAIL_FUNCTION = "regex_alternative_problems"


def _validate_action():
    for node in ast.walk(TREE):
        if (isinstance(node, ast.FunctionDef)
                and node.name == "validate_configuration_action"):
            return node
    pytest.fail("plugin.py has no validate_configuration_action method")


def _calls_named(function_name):
    """Every call to ecm_parsing.<function_name> inside the validate action."""
    return [
        node for node in ast.walk(_validate_action())
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "ecm_parsing." + function_name
    ]


def test_the_action_asks_for_the_summary():
    assert _calls_named(SUMMARY_FUNCTION), (
        "validate_configuration_action must call ecm_parsing."
        + SUMMARY_FUNCTION + " or a pattern that compiles is still reported Valid")


def test_the_summary_is_computed_from_the_pattern_being_checked():
    call = _calls_named(SUMMARY_FUNCTION)[0]
    assert [ast.unparse(arg) for arg in call.args] == ["pattern"], (
        "the summary must be computed from the field being validated")


def test_the_full_detail_is_produced_as_well():
    assert _calls_named(DETAIL_FUNCTION), (
        "the per-alternative detail must still be produced for the log")


def test_the_full_detail_does_not_go_into_the_readout():
    """Dispatcharr clips a toast at about 280 characters, from the middle.

    The detail is several hundred characters per alternative. Appending it to
    validation_results would silently push the rest of the validation out of
    view, which is worse than not warning at all.
    """
    for node in ast.walk(_validate_action()):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "validation_results"):
            continue
        assert DETAIL_FUNCTION not in ast.unparse(node), (
            "the per-alternative detail must go to the log, not into "
            "validation_results")


def test_the_detail_is_logged_from_the_loop_over_it():
    """The detail has to reach the operator somewhere, and the log is where.

    Scoped to a loop whose iterator IS the detail call, so an unrelated logger
    line elsewhere in this long method cannot satisfy it.
    """
    for node in ast.walk(_validate_action()):
        if not isinstance(node, ast.For):
            continue
        if ast.unparse(node.iter) != f"ecm_parsing.{DETAIL_FUNCTION}(pattern)":
            continue
        logged = [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in ("warning", "info")
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "logger"
        ]
        assert logged, "each per-alternative detail must be logged"
        return
    pytest.fail(
        f"no loop over ecm_parsing.{DETAIL_FUNCTION}(pattern) in "
        "validate_configuration_action, so the detail reaches nobody")
