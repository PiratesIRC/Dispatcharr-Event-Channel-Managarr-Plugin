"""The [UndatedEnded] rule must read its grace period from the setting, not a constant.

The rule hides a channel once an inferred event end has passed. If it ignored the
configured grace period the channel would disappear while the event overran, and
nothing would report that the setting had been discarded. plugin.py imports Django
at module scope and cannot be imported outside the container, so its structure is
read with ast.
"""

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _rule_branch(rule_name):
    for node in ast.walk(TREE):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == "rule_name"
                and test.comparators
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == rule_name):
            return "\n".join(ast.unparse(stmt) for stmt in node.body)
    pytest.fail(f"plugin.py has no rule branch for {rule_name}")


def test_the_rule_branch_exists():
    assert _rule_branch("UndatedEnded")


def test_the_rule_reads_the_configured_grace_period():
    branch = _rule_branch("UndatedEnded")
    assert "undated_event_grace_hours" in branch, (
        "the rule must read the grace period from settings")


def test_the_rule_reads_the_first_seen_record():
    branch = _rule_branch("UndatedEnded")
    assert "_undated_tracker" in branch


def test_the_rule_uses_the_shared_window_helper():
    branch = _rule_branch("UndatedEnded")
    assert "infer_undated_event_window" in branch, (
        "the window arithmetic belongs in ecm_parsing so it stays unit-testable")


def test_the_setting_is_declared_in_both_the_code_and_the_manifest():
    assert "undated_event_grace_hours" in SOURCE
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    ids = [field["id"] for field in manifest["fields"]]
    assert "undated_event_grace_hours" in ids, (
        "plugin.json must declare the same field id as plugin.py")


def test_the_default_hide_rules_place_the_new_rule_before_the_day_count_rule():
    defaults = next(
        node.value.value for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "DEFAULT_HIDE_RULES"
        and isinstance(node.value, ast.Constant))
    assert "[UndatedEnded]" in defaults
    assert defaults.index("[UndatedEnded]") < defaults.index("[UndatedAge:")
