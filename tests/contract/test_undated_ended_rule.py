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


def test_the_rule_uses_the_shared_decision_helper():
    """The comparison against the clock belongs in ecm_parsing so it can be unit-tested.

    An assertion on the source text cannot tell a correct comparison from an inverted
    one, so the decision itself is tested in tests/unit/test_ecm_parsing.py. This holds
    the wiring: if the rule stops calling the helper, that unit coverage stops applying
    to the shipped behaviour and nothing else would say so.
    """
    branch = _rule_branch("UndatedEnded")
    assert "undated_event_has_ended" in branch


def test_the_rule_passes_the_first_seen_moment_to_the_decision_helper():
    """Without it, a channel first seen at 23:00 named for a 1:00am event is hidden
    on the very first scan that sees it, two hours before the event starts."""
    branch = _rule_branch("UndatedEnded")
    assert "first_seen_at" in branch


def test_every_shipped_default_rule_list_agrees():
    """The default rule list is written down in three places and all three ship.

    plugin.py holds the one the running plugin uses. plugin.json holds the manifest
    fallback, which is also what the release artifact carries. The bootstrap template
    seeds a fresh installation's stored settings, and because the plugin only falls back
    to its built-in default when the stored value is EMPTY, a template missing the tag
    disables the rule permanently on every installation seeded from it.
    """
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    manifest_default = next(
        field["default"] for field in manifest["fields"]
        if field["id"] == "hide_rules_priority")

    code_default = next(
        node.value.value for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "DEFAULT_HIDE_RULES"
        and isinstance(node.value, ast.Constant))

    template_path = ROOT / "config" / "ecm_settings.template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template_default = template["hide_rules_priority"]

    assert manifest_default == code_default, (
        "plugin.json's hide_rules_priority default has drifted from plugin.py")
    assert template_default == code_default, (
        "config/ecm_settings.template.json's hide_rules_priority has drifted from "
        "plugin.py, so a bootstrapped installation would never receive the new rule")


def test_the_bootstrap_template_carries_every_grace_period_setting():
    """A setting absent from the template is never written on a fresh install.

    The existing template test only asserts that template keys are a subset of the
    declared fields, which passes just as well when a setting is missing.
    """
    template_path = ROOT / "config" / "ecm_settings.template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert "undated_event_grace_hours" in template
    assert "past_date_grace_hours" in template
