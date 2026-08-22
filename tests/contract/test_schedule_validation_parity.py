"""Validate Configuration must judge run times with the scheduler's own parser.

The settings form used to run its own check on the scheduled times: it accepted
any entry of four digits. The scheduler's parser is stricter, because it also
requires a real hour and minute. So "2400" was reported as a valid schedule by
the form and then thrown away by the scheduler, and the midnight run never fired
with nothing anywhere explaining it. Two checks on the same setting, disagreeing.

These tests pin the single-parser arrangement that replaced it. plugin.py imports
Django at module scope and cannot be imported outside the container, so its
structure is read with ast.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

TREE = ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))


def _function_named(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"plugin.py no longer defines {name}()")


def _calls_within(node):
    """Every called attribute or name inside `node`, as dotted text."""
    calls = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            calls.add(ast.unparse(sub.func))
    return calls


def test_validate_uses_the_schedulers_parser():
    """The form must not re-implement its own idea of a usable run time."""
    calls = _calls_within(_function_named("validate_configuration_action"))
    assert "self._parse_scheduled_times" in calls, (
        "validate_configuration_action no longer calls _parse_scheduled_times, so its "
        "verdict on a schedule can drift from what the scheduler actually arms."
    )


def test_the_parser_is_the_django_free_one():
    """One implementation, in the module the unit tests can import."""
    calls = _calls_within(_function_named("_parse_scheduled_times"))
    assert "ecm_parsing.parse_scheduled_times" in calls, (
        "_parse_scheduled_times no longer delegates to ecm_parsing, so the tested "
        "implementation and the shipped one can diverge."
    )


def test_the_parser_accepts_a_place_to_report_rejects():
    """Callers need the discarded entries to tell the user what will never run."""
    fn = _function_named("_parse_scheduled_times")
    arg_names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    assert "rejects" in arg_names, (
        "_parse_scheduled_times lost its `rejects` parameter; discarded run times "
        "would become invisible again."
    )


def test_the_old_shape_only_check_is_gone():
    """The exact test that called 2400 valid must not come back."""
    source = PLUGIN_PY.read_text(encoding="utf-8")
    assert "len(t) != 4 or not t.isdigit()" not in source, (
        "The four-digits-only schedule check is back in plugin.py. It accepts 2400, "
        "which the scheduler rejects, and the two verdicts disagree silently."
    )
