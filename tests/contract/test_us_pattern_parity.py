"""The US title pattern is written down twice; these must not drift apart.

plugin.py builds the default managed EPG source from a literal named
us_title_pattern. ecm_profiles.US_ET carries a copy of the same string, and its
own comment records that the copy is pinned to plugin.py. Nothing enforced that
before this file existed, so editing one and not the other was silent: the
routing module and the source the renderer actually reads would disagree, and
only the guide would show it.

plugin.py imports Django at module scope and cannot be imported outside the
container, so the literal is read with ast.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

import ecm_profiles  # noqa: E402  resolves via pyproject.toml pythonpath


def _string_literal_assigned_to(name):
    """Return the constant string assigned to `name` anywhere in plugin.py.

    Implicit concatenation across several source lines folds to one Constant in
    the tree, so this reads the finished pattern rather than a fragment.
    """
    tree = ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))
    found = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]
    assert len(found) == 1, f"expected exactly one assignment to {name}, got {len(found)}"
    return found[0]


@pytest.mark.parametrize("literal_name,profile_attr", [
    ("us_title_pattern", "title_pattern"),
    ("us_time_pattern", "time_pattern"),
    ("us_date_pattern", "date_pattern"),
])
def test_plugin_literal_matches_the_us_et_profile(literal_name, profile_attr):
    us = next(p for p in ecm_profiles.PROFILES if p.key == "us_et")
    assert _string_literal_assigned_to(literal_name) == getattr(us, profile_attr), (
        f"plugin.py's {literal_name} and ecm_profiles.US_ET.{profile_attr} have "
        f"drifted apart. The renderer reads the plugin.py value; routing reads "
        f"the profile value."
    )


def test_the_superseded_pattern_is_still_listed_as_a_stock_default():
    """An existing installation carries the OLD pattern on its EPG source row.

    plugin.py only re-applies its default to a pattern that is unset or still
    equals a default this plugin has shipped (issue #21), so every superseded
    default has to stay listed or those installations never receive the new one.
    """
    source = PLUGIN_PY.read_text(encoding="utf-8")
    superseded = (
        r"(?:(?:PPV|LIVE)\s*(?:EVENT\s*)?|EVENT\s*)\d+\s*[:|\-\s]\s*"
    )
    assert superseded in source, (
        "the keyword-required US title pattern is no longer listed among the "
        "stock defaults, so installations still carrying it will keep the "
        "pattern that does not match bare-numbered event names"
    )
