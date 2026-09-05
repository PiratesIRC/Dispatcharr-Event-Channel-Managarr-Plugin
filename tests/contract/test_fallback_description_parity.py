"""The guide's fallback description is written down twice, and it is copy people read.

plugin.py builds the default managed EPG source from a dict literal containing
fallback_description_template. ecm_profiles carries the same string as
_FALLBACK_DESCRIPTION. Nothing held the two together, so editing one and not the
other would be silent: the two would disagree and only the rendered guide would
show it. Same failure this repository already guards for the US title pattern in
tests/contract/test_us_pattern_parity.py.

The string is rendered into the guide for any managed channel whose name does not
match the event title pattern, so it is plugin-facing copy and the workspace rule
against em dashes applies to it.

plugin.py imports Django at module scope and cannot be imported outside the
container, so the literal is read with ast.
"""

import ast
from pathlib import Path

import pytest

import ecm_profiles  # resolves via pyproject.toml pythonpath

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
TREE = ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))

KEY = "fallback_description_template"
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


def _dict_values_for_key(key):
    """Every constant string assigned to `key` in a dict literal in plugin.py."""
    found = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Dict):
            continue
        for name_node, value in zip(node.keys, node.values):
            if (isinstance(name_node, ast.Constant) and name_node.value == key
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                found.append(value.value)
    return found


def test_plugin_py_sets_the_description_exactly_once():
    values = _dict_values_for_key(KEY)
    assert len(values) == 1, (
        f"expected one literal {KEY} in plugin.py, found {len(values)}")


def test_the_two_copies_are_identical():
    values = _dict_values_for_key(KEY)
    if not values:
        pytest.fail(f"plugin.py has no literal {KEY}")
    assert values[0] == ecm_profiles._FALLBACK_DESCRIPTION, (
        "plugin.py and ecm_profiles disagree about the guide fallback description")


def test_the_description_carries_no_em_dash():
    """Operator-mandated: no em dashes in copy the plugin renders."""
    text = ecm_profiles._FALLBACK_DESCRIPTION
    assert EM_DASH not in text and EN_DASH not in text, (
        "the fallback description is rendered into the guide, so it is "
        "plugin-facing copy and must not contain an em dash or en dash")


def test_the_plugin_py_copy_carries_no_em_dash():
    values = _dict_values_for_key(KEY)
    assert values and EM_DASH not in values[0] and EN_DASH not in values[0]


def test_the_description_is_not_empty():
    """A non-empty description is what makes the renderer enter the fallback path.

    generate_dummy_programs gates on `if fallback_title or fallback_description`,
    and the title is deliberately empty so the renderer falls back to the real
    channel name. An empty description here would silently disable both.
    """
    assert ecm_profiles._FALLBACK_DESCRIPTION.strip()


def test_both_profiles_use_the_shared_constant():
    for profile in (ecm_profiles.US_ET, ecm_profiles.DAZN_GMT):
        assert profile.fallback_description_template == ecm_profiles._FALLBACK_DESCRIPTION
