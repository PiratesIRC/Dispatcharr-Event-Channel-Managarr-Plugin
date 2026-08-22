"""The run ledger records transitions, and never breaks a scan.

The badge on the README counts event channels the plugin switched to visible.
That number is only meaningful if the ledger records TRANSITIONS: the plugin
looks at every channel in scope on every scheduled run, several times a day, so
recording "channels currently visible" would re-count the same channel forever
and produce a number that describes nothing.

plugin.py imports Django at module scope and cannot be imported outside the
container, so the writer is exercised through a small stand-in built from the
same source, and the file's structure is checked with ast.
"""

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function_named(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"plugin.py no longer defines {name}()")


# ---------------------------------------------------------------------------
# The writer must not be able to break a scan
# ---------------------------------------------------------------------------

def test_the_writer_swallows_every_exception():
    """A reporting convenience must never fail the thing it reports on."""
    fn = _function_named("_append_ledger_entry")
    handlers = [h for node in ast.walk(fn) if isinstance(node, ast.Try)
                for h in node.handlers]
    broad = [h for h in handlers
             if isinstance(h.type, ast.Name) and h.type.id == "Exception"]
    assert broad, (
        "_append_ledger_entry no longer catches Exception, so a full disk or a "
        "permissions problem would abort a scan that had already changed channels."
    )


def test_the_writer_never_re_raises():
    fn = _function_named("_append_ledger_entry")
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
    assert not raises, "_append_ledger_entry must not raise"


def test_the_writer_opens_the_file_in_append_mode():
    """Truncating would silently reset the public counter to zero."""
    fn = _function_named("_append_ledger_entry")
    modes = [
        arg.value
        for node in ast.walk(fn) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "open"
        for arg in node.args if isinstance(arg, ast.Constant)
        and isinstance(arg.value, str)
    ]
    assert "a" in modes, f"expected append mode, found {modes}"


# ---------------------------------------------------------------------------
# It must be fed transition counts, from after the changes committed
# ---------------------------------------------------------------------------

def test_the_call_passes_the_transition_lists():
    """channels_to_show/hide hold only channels whose visibility changes."""
    calls = [
        node for node in ast.walk(TREE) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_append_ledger_entry"
    ]
    assert len(calls) == 1, f"expected exactly one ledger call, found {len(calls)}"
    rendered = ast.unparse(calls[0])
    assert "len(channels_to_show)" in rendered
    assert "len(channels_to_hide)" in rendered


def test_the_call_sits_inside_the_applied_branch():
    """A dry run must never append: it changes nothing by definition."""
    call_line = next(
        node.lineno for node in ast.walk(TREE) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_append_ledger_entry"
    )
    guard_lines = [
        node.lineno for node in ast.walk(TREE) if isinstance(node, ast.If)
        and "not dry_run" in ast.unparse(node.test)
        and node.lineno < call_line < (node.end_lineno or node.lineno)
    ]
    assert guard_lines, (
        "the ledger call is no longer inside a `not dry_run` branch, so a Dry Run "
        "would inflate the public counter with changes it never made."
    )


def test_the_call_follows_the_commit_not_the_plan():
    """Recording before the transaction would count changes that never landed."""
    source_lines = SOURCE.splitlines()
    call_line = next(
        node.lineno for node in ast.walk(TREE) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_append_ledger_entry"
    )
    atomic_lines = [i + 1 for i, line in enumerate(source_lines)
                    if "transaction.atomic()" in line]
    assert atomic_lines, "plugin.py no longer wraps the visibility update in a transaction"
    assert any(a < call_line for a in atomic_lines), (
        "the ledger is appended before the visibility transaction, so a failed "
        "commit would still be counted."
    )


# ---------------------------------------------------------------------------
# The line the badge script has to be able to read
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {"ts", "version", "shown", "hidden", "scheduled"}


def test_the_entry_carries_the_keys_the_badge_script_reads():
    fn = _function_named("_append_ledger_entry")
    keys = {
        key.value
        for node in ast.walk(fn) if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    missing = REQUIRED_KEYS - keys
    assert not missing, f"ledger entry lost the keys {sorted(missing)}"


def test_the_timestamp_does_not_use_a_name_this_module_lacks():
    """`timezone` here is django.utils.timezone; it has no `.utc` in Django 5."""
    fn = _function_named("_append_ledger_entry")
    rendered = ast.unparse(fn)
    assert "timezone.utc" not in rendered, (
        "django.utils.timezone.utc was removed in Django 5 and datetime.timezone "
        "is not reachable under this name; use timezone.now()."
    )


# ---------------------------------------------------------------------------
# The counting the badge script does, exercised on real ledger text
# ---------------------------------------------------------------------------

def _count(lines):
    """The same arithmetic scripts/update_events_badge.py runs in the container."""
    shown = hidden = runs = 0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        runs += 1
        for key in ("shown", "hidden"):
            value = row.get(key)
            if isinstance(value, int) and value >= 0:
                if key == "shown":
                    shown += value
                else:
                    hidden += value
    return shown, hidden, runs


def test_totals_sum_across_runs():
    lines = [
        json.dumps({"ts": "t", "version": "v", "shown": 11, "hidden": 100, "scheduled": False}),
        json.dumps({"ts": "t", "version": "v", "shown": 4, "hidden": 0, "scheduled": True}),
    ]
    assert _count(lines) == (15, 100, 2)


def test_a_corrupt_line_is_skipped_rather_than_stopping_the_count():
    """A half-written line from a killed container must not zero the badge."""
    lines = [
        json.dumps({"shown": 5, "hidden": 1}),
        '{"shown": 3, "hidden":',          # truncated mid-write
        json.dumps({"shown": 2, "hidden": 1}),
    ]
    assert _count(lines) == (7, 2, 2)


def test_an_empty_ledger_counts_zero_rather_than_failing():
    assert _count([]) == (0, 0, 0)


@pytest.mark.parametrize("bad", [
    json.dumps({"shown": "eleven"}),   # wrong type
    json.dumps({"shown": -3}),         # negative
    json.dumps(["not", "a", "dict"]),
])
def test_implausible_values_do_not_reach_the_total(bad):
    shown, _hidden, _runs = _count([json.dumps({"shown": 5}), bad])
    assert shown == 5
