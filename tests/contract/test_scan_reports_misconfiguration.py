"""A run that quietly ignored part of the configuration must say so.

Two configuration mistakes used to leave no trace an operator would ever see.

A channel group name that matched nothing was written to the container log and
put in the result message ONLY when the scan found no channels at all. A user who
typed six group names, three of which matched nothing, got a successful-looking
run over the remaining channels and no hint that half their configuration was
inert. The specific way they broke it was separating the names with "|", the
alternation character the three regex fields use, which glues several real group
names into one that exists nowhere.

A regex field that matched no channel was reported nowhere at all. The same user
pasted guide programme titles into "Regex: Mark Channel as Inactive", a field
matched only against channel and stream names, so it could never fire, and the
run looked exactly like one where it was working.

plugin.py imports Django at module scope and cannot be imported outside the
container, so its structure is read with ast.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _scan_function():
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "_scan_and_update_channels":
            return node
    pytest.fail("plugin.py no longer defines _scan_and_update_channels()")


SCAN = _scan_function()
SCAN_SOURCE = ast.unparse(SCAN)


def _assigned_names(fn):
    names = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


# ---------------------------------------------------------------------------
# Channel groups that matched nothing
# ---------------------------------------------------------------------------

def test_scan_still_works_out_which_groups_matched_nothing():
    assert "unmatched_groups" in _assigned_names(SCAN)


def test_unmatched_groups_reach_the_result_message():
    """Not just the container log, and not only when zero channels were found."""
    assert "message_parts" in SCAN_SOURCE
    message_region = SCAN_SOURCE.split("message_parts", 1)[1]
    assert "unmatched_groups" in message_region, (
        "Group names that matched nothing no longer reach the run's result message, "
        "so a partly inert configuration reads as a clean run again."
    )


def test_unmatched_groups_reach_the_csv_header():
    # Bound the region at message_parts, which is built later in the same function.
    # Without that bound this test would also pass on the result-message code alone.
    header_region = SCAN_SOURCE.split("header_lines", 1)[1].split("message_parts", 1)[0]
    assert "unmatched_groups" in header_region, (
        "The CSV header no longer records group names that matched nothing, which is "
        "the only place the full list survives the toast's length limit."
    )


def test_a_pipe_in_a_group_name_gets_an_explanation():
    """The separator mix-up needs naming, or the report is a puzzle."""
    assert "piped_groups" in _assigned_names(SCAN)
    assert "separator_hint" in _assigned_names(SCAN)
    assert "comma-separated" in SCAN_SOURCE


# ---------------------------------------------------------------------------
# Regex fields that matched nothing
# ---------------------------------------------------------------------------

def test_scan_counts_what_each_regex_field_matched():
    names = _assigned_names(SCAN)
    assert "regex_field_counts" in names
    assert "unmatched_regex_fields" in names


def test_all_three_regex_fields_are_counted():
    for setting in ("regex_mark_inactive", "regex_force_visible"):
        assert setting in SCAN_SOURCE, f"{setting} is no longer counted in the scan"
    assert "channels_ignored" in SCAN_SOURCE


def test_an_unused_regex_field_reaches_the_result_message():
    message_region = SCAN_SOURCE.split("message_parts", 1)[1]
    assert "unmatched_regex_fields" in message_region, (
        "A regex field the user filled in that matched nothing no longer reaches the "
        "result message, so a pattern that can never fire looks like one that works."
    )


def test_the_message_explains_what_the_regex_fields_read():
    """The reported mistake was pasting guide text into a name-matching field."""
    assert "never guide programme titles" in SCAN_SOURCE
