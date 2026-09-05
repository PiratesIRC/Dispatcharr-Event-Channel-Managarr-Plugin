"""The CSV must never report a hidden channel under a blank rule name.

A user's export carried one Rule Effectiveness line reading "  : 10 channels".
All ten channels had been hidden by duplicate handling, whose reason carried no
bracketed tag, and the inline tally grouped them under the empty string. That
blank label was the information needed to diagnose their report (bug-177).

The two decisions are unit-tested in tests/unit/test_rule_effectiveness.py. This
is the single structural test that keeps them wired in. plugin.py imports Django
at module scope and cannot be imported outside the container, so its structure is
read with ast, and each assertion is scoped to one node rather than searched for
across the whole method.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

OLD_REASON = "Duplicate channel (keeping better match)"


def _assignments_to(name):
    return [node for node in ast.walk(TREE)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]


def test_the_tally_comes_from_the_pure_function():
    found = _assignments_to("rule_stats")
    assert len(found) == 1, f"expected one rule_stats assignment, found {len(found)}"
    value = found[0].value
    assert isinstance(value, ast.Call), (
        "rule_stats must be computed by ecm_parsing.rule_effectiveness, not built inline")
    assert ast.unparse(value.func) == "ecm_parsing.rule_effectiveness"


def test_the_tally_is_computed_from_the_result_rows():
    call = _assignments_to("rule_stats")[0].value
    assert [ast.unparse(arg) for arg in call.args] == ["results"]


def test_the_rule_tag_comes_from_the_pure_function():
    found = _assignments_to("hide_rule")
    assert found, "plugin.py never assigns hide_rule"
    calls = [ast.unparse(f.value.func) for f in found if isinstance(f.value, ast.Call)]
    assert "ecm_parsing.hide_rule_tag" in calls, (
        "the rule tag must come from ecm_parsing.hide_rule_tag so the duplicate "
        "reason is tagged like every other hide reason")


def test_the_duplicate_reason_is_the_shared_constant():
    """A second copy of the reason text would drift away from its tag."""
    assert OLD_REASON not in SOURCE, (
        "the untagged duplicate reason must not appear in plugin.py; use "
        "ecm_parsing.DUPLICATE_HIDE_REASON")
    uses = [node for node in ast.walk(TREE)
            if isinstance(node, ast.Attribute)
            and node.attr == "DUPLICATE_HIDE_REASON"]
    assert len(uses) == 2, (
        f"expected both duplicate-reason sites to use the constant, found {len(uses)}")


def test_the_tally_is_not_treated_as_a_dictionary():
    """rule_effectiveness returns an ordered list, already sorted.

    Re-sorting it, or calling .items() on it, would either crash or silently undo
    the tie-breaking that keeps two CSV headers comparable.
    """
    for node in ast.walk(TREE):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "rule_stats"):
            pytest.fail(f"rule_stats is used as a dict via .{node.attr} on line {node.lineno}")
