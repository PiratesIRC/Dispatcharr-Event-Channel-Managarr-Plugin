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


def test_the_parsing_fallback_time_pattern_matches_the_plugin_literal():
    """A THIRD copy of the time pattern lives in ecm_parsing.py.

    plugin.py's us_time_pattern is written onto the managed EPG source, so it is what
    [UndatedEnded] uses for a channel bound to that source. ecm_parsing._DEFAULT_TIME_OF_DAY
    is what the same rule uses for a channel bound to nothing. If the two drift, the rule
    reads a different time depending on whether the channel happens to be bound yet, and
    nothing else would report it.

    The plugin literal is in the JavaScript named-group form because Dispatcharr's frontend
    validator rejects the Python one (issue 21), so it is converted before comparing.
    """
    import re

    import ecm_parsing

    js_form = _string_literal_assigned_to("us_time_pattern")
    python_form = re.sub(r"\(\?<(?![=!])", "(?P<", js_form)
    assert python_form == ecm_parsing._DEFAULT_TIME_OF_DAY, (
        "plugin.py's us_time_pattern and ecm_parsing._DEFAULT_TIME_OF_DAY have drifted "
        "apart. A channel bound to the managed EPG source and a channel bound to nothing "
        "would then have their event times read by different patterns."
    )


def test_the_time_pattern_does_not_read_a_word_as_a_meridiem():
    """The am or pm marker must not match the opening letters of an ordinary word.

    Without a trailing boundary "PPV 12 AMERICAN LEGENDS" parses as midnight, and
    [UndatedEnded] then hides that channel on a time that is not in its name. This
    asserts the behaviour rather than the presence of the guard, so any future rewrite
    of the pattern that reintroduces the fault fails here.
    """
    import ecm_parsing

    assert ecm_parsing.extract_time_of_day("PPV 12 AMERICAN LEGENDS") is None
    assert ecm_parsing.extract_time_of_day("Boxing 3 : ALI vs 8 AMATEUR BOUTS") is None
    assert ecm_parsing.extract_time_of_day("Fight 5 : 9 Ammo vs X") is None
    # A real clock time in the same shape of name must still be read.
    assert ecm_parsing.extract_time_of_day(
        "Boxing 3 : MOSES vs HRGOVIC  4:00pm") == (16, 0)


def test_the_superseded_time_pattern_is_still_listed_as_a_stock_default():
    """An installation carries the OLD time pattern on its EPG source row.

    Same mechanism as the title pattern below: plugin.py only re-applies its default to
    a pattern it recognises as one of its own, so an unlisted superseded default is kept
    for ever and that installation never receives the word-boundary guard.
    """
    source = PLUGIN_PY.read_text(encoding="utf-8")
    superseded = r"(?<hour>\d{1,2})(?::(?<minute>\d{2}))?\s*(?<ampm>[AaPp][Mm])"
    assert f'r"{superseded}"' in source, (
        "the unbounded US time pattern is no longer listed among the stock defaults, so "
        "installations still carrying it keep reading a word as a clock time"
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
