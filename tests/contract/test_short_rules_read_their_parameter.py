"""The two length rules must actually read the number written in their tag.

The hide-rule parser splits any [Name:N] tag and hands N to the rule evaluator.
For [ShortDescription] and [ShortChannelName] the evaluator ignored it and used a
number written into the rule body, so [ShortDescription:20] parsed cleanly, ran,
and quietly applied 15. A rule that accepts a setting and discards it is worse
than one that never offered it, because nothing reports the discard.

plugin.py imports Django at module scope and cannot be imported outside the
container, so its structure is read with ast.
"""

import ast
from pathlib import Path

import pytest

import ecm_parsing  # noqa: E402  resolves via pyproject.toml pythonpath

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _rule_branch(rule_name):
    """The source of the `elif rule_name == "<rule_name>"` branch."""
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
    pytest.fail(f"plugin.py no longer has a rule branch for {rule_name}")


@pytest.mark.parametrize("rule_name", ["ShortDescription", "ShortChannelName"])
def test_the_rule_reads_rule_param(rule_name):
    body = _rule_branch(rule_name)
    assert "rule_param" in body, (
        f"[{rule_name}] no longer reads rule_param, so a number written in the tag "
        f"is accepted by the parser and then silently ignored."
    )


@pytest.mark.parametrize("rule_name,constant", [
    ("ShortDescription", "SHORT_DESCRIPTION_DEFAULT"),
    ("ShortChannelName", "SHORT_CHANNEL_NAME_DEFAULT"),
])
def test_the_default_comes_from_the_shared_constant(rule_name, constant):
    """One definition of the default, in the module the unit tests can import."""
    body = _rule_branch(rule_name)
    assert f"ecm_parsing.{constant}" in body


@pytest.mark.parametrize("rule_name,literal", [
    ("ShortDescription", "15"),
    ("ShortChannelName", "25"),
])
def test_the_cutoff_is_not_written_into_the_rule_body(rule_name, literal):
    """A bare number here is how the setting came to be ignored in the first place."""
    body = _rule_branch(rule_name)
    comparisons = [
        line for line in body.splitlines()
        if f"< {literal}" in line or f"<{literal}" in line
    ]
    assert not comparisons, (
        f"[{rule_name}] compares against the literal {literal} again: {comparisons}"
    )


def test_the_defaults_still_match_the_historical_behaviour():
    """A bare tag must keep hiding exactly what it used to hide."""
    assert ecm_parsing.SHORT_DESCRIPTION_DEFAULT == 15
    assert ecm_parsing.SHORT_CHANNEL_NAME_DEFAULT == 25


@pytest.mark.parametrize("rule_name", ["ShortDescription", "ShortChannelName"])
def test_the_reason_text_records_the_threshold_applied(rule_name):
    """The CSV must show which cutoff produced the decision, as the date rules do."""
    body = _rule_branch(rule_name)
    assert "threshold" in body, (
        f"[{rule_name}]'s reason text no longer records the cutoff it used, so a CSV "
        f"cannot show why one channel was hidden and a similar one was not."
    )
