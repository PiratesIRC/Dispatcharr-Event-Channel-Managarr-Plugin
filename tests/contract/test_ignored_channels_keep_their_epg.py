"""A channel matched by the ignore regex must be left alone in both directions.

The managed dummy EPG pass receives a detach scope: the channels it is allowed
to take the managed source off. Channels matched by the Regex: Channels to
Ignore setting used to be inside it. The plugin makes no visibility decision
for them and never attaches the managed source to them, so their only possible
outcome was losing EPG they already had.

The decision is unit-tested in tests/unit/test_managed_epg_detach_scope.py.
This is the single structural test that keeps it wired in. plugin.py imports
Django at module scope and cannot be imported outside the container, so its
structure is read with ast. Assertions are scoped to the one assignment under
test rather than searched across the whole method.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

DECISION_FUNCTION = "managed_epg_detach_scope"
IGNORED_LIST = "channels_ignored"


def _scan_method():
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "_scan_and_update_channels":
            return node
    pytest.fail("plugin.py has no _scan_and_update_channels method")


def _scope_assignment():
    found = []
    for node in ast.walk(_scan_method()):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "scanned_channel_ids":
                found.append(node)
    assert len(found) == 1, (
        "scanned_channel_ids must be built exactly once; "
        f"found {len(found)} assignments")
    return found[0]


def test_the_detach_scope_comes_from_the_pure_decision_function():
    value = _scope_assignment().value
    assert isinstance(value, ast.Call), (
        "scanned_channel_ids must be computed by ecm_profiles."
        + DECISION_FUNCTION + ", not built inline")
    assert ast.unparse(value.func) == "ecm_profiles." + DECISION_FUNCTION


def test_the_ignored_channels_are_passed_to_the_decision_function():
    call = _scope_assignment().value
    assert isinstance(call, ast.Call), (
        "scanned_channel_ids is not a call, so nothing can be passed to it")
    passed = {ast.unparse(arg) for arg in call.args}
    passed |= {ast.unparse(kw.value) for kw in call.keywords}
    assert IGNORED_LIST in passed, (
        "the ignored ids must reach the decision function or the plugin can "
        "still detach managed EPG from a channel it was told to ignore")


def test_the_scope_is_still_derived_from_the_scanned_channels():
    """Removing the group narrowing would resurrect bug-045."""
    call = _scope_assignment().value
    rendered = ast.unparse(call)
    assert "for c in channels" in rendered, (
        "the detach scope must still start from the channels this run scanned")
