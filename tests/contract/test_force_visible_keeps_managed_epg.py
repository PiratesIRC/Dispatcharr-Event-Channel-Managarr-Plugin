"""Channels held visible by the force-visible regex must reach the managed EPG pass.

The managed dummy EPG pass receives one list of "visible after this scan"
channel ids. That list is both the set it attaches the managed source to and
the keep-set its detach step protects, so a channel missing from it gets no
guide data and has any managed EPG stripped on every run.

Until this test existed, the force-visible branch of the per-channel loop
returned to the top of the loop without recording the channel anywhere, and the
list was built inline from a collection that branch never touched. Measured on
the live installation on 2026-09-05: 17 visible channels with no EPG row while
the managed dummy EPG feature was on (bug-175).

The decision itself is unit-tested in tests/unit/test_managed_epg_enabled_ids.py.
This file is the single structural test that keeps it WIRED IN. It reads
plugin.py with ast because plugin.py imports Django at module scope and cannot
be imported outside the container. Every assertion is scoped to one specific
node rather than searched for across the whole method, because a string search
over a whole method is satisfied by text that has nothing to do with the branch
under test.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

DECISION_FUNCTION = "managed_epg_enabled_ids"
FORCED_LIST = "force_visible_channel_ids"


def _scan_method():
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "_scan_and_update_channels":
            return node
    pytest.fail("plugin.py has no _scan_and_update_channels method")


def _enabled_ids_assignments():
    """Every assignment to enabled_channel_ids inside the scan method."""
    found = []
    for node in ast.walk(_scan_method()):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "enabled_channel_ids":
                found.append(node)
    return found


def _force_visible_branch():
    """The `if regex_force_visible and regex_force_visible.search(...)` branch."""
    for node in ast.walk(_scan_method()):
        if not isinstance(node, ast.If):
            continue
        if not isinstance(node.test, ast.BoolOp):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if "regex_force_visible" in names:
            return node
    pytest.fail("plugin.py has no force-visible branch in _scan_and_update_channels")


def test_the_enabled_set_is_built_exactly_once():
    """Two assignments would mean one of them is dead and unreviewed."""
    assert len(_enabled_ids_assignments()) == 1


def test_the_enabled_set_comes_from_the_pure_decision_function():
    """Re-inlining the comprehension is what allowed the defect to appear."""
    value = _enabled_ids_assignments()[0].value
    assert isinstance(value, ast.Call), (
        "enabled_channel_ids must be computed by ecm_profiles."
        + DECISION_FUNCTION + ", not built inline")
    assert ast.unparse(value.func) == "ecm_profiles." + DECISION_FUNCTION


def test_the_forced_channels_are_passed_to_the_decision_function():
    call = _enabled_ids_assignments()[0].value
    assert isinstance(call, ast.Call), (
        "enabled_channel_ids is not a call, so nothing can be passed to it")
    passed = {ast.unparse(arg) for arg in call.args}
    passed |= {ast.unparse(kw.value) for kw in call.keywords}
    assert FORCED_LIST in passed, (
        "the force-visible ids must reach the decision function or forced "
        "channels get no managed EPG and have any managed EPG detached")


def test_the_force_visible_branch_records_the_channel():
    """The branch ends in `continue`, so it must record the id before it does."""
    branch = _force_visible_branch()
    appends = [
        node for node in ast.walk(branch)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == FORCED_LIST
    ]
    assert appends, (
        "the force-visible branch must append to " + FORCED_LIST
        + " before it returns to the top of the loop")


def test_the_forced_list_is_initialised_before_the_loop():
    method = _scan_method()
    for node in ast.walk(method):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == FORCED_LIST:
                assert isinstance(node.value, ast.List) and not node.value.elts, (
                    FORCED_LIST + " must start as an empty list")
                return
    pytest.fail(FORCED_LIST + " is never initialised in _scan_and_update_channels")
