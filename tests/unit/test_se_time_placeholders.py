"""The Swedish channel-name format must use the 24-hour time placeholders.

Measured in the running container on 2026-08-12, in /app/apps/output/epg.py
(the dummy EPG renderer; it used to be apps/output/views.py and MOVED, so a
search in the old path returns nothing and reads as "the placeholder does not
exist"):

    starttime24 / endtime24  ->  "HH:MM", 24-hour
    starttime   / endtime    ->  explicitly converted to 12-hour with AM/PM
    starttime_long / endtime_long -> also 12-hour

So {starttime} is unconditionally 12-hour, and `output_timezone` converts the
INSTANT rather than the format. Swedish channel names already carry 24-hour
times, which the format's own time pattern shows: it matches HH:MM with no
AM/PM. A channel named "19:55" was therefore being titled "Upcoming at 7:55 PM",
contradicting its own name. US names carry native AM/PM times and keep the
12-hour placeholders.

These tests read plugin.py as text rather than importing it: plugin.py imports
Django and cannot be imported outside Dispatcharr, which is why every other
contract test in this repository parses instead.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

# The two methods that build the upcoming/ended title templates.
TEMPLATE_BUILDERS = ("_get_or_create_managed_epg_source", "_localized_template_props")


@pytest.fixture(scope="module")
def source():
    return PLUGIN_PY.read_text(encoding="utf-8")


def _fn_source(source, name):
    fn = next((n for n in ast.walk(ast.parse(source))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
    assert fn is not None, f"{name} is gone; this test no longer guards anything"
    return ast.get_source_segment(source, fn)


@pytest.mark.parametrize("name", TEMPLATE_BUILDERS)
def test_the_placeholder_is_chosen_from_the_channel_name_format(source, name):
    """Both builders must branch on the format rather than hardcoding one."""
    body = _fn_source(source, name)
    assert "dummy_epg_channel_format" in body, (
        f"{name} no longer reads the channel-name format, so it cannot be "
        f"choosing between the 12-hour and 24-hour placeholders")
    assert "starttime24" in body and "endtime24" in body, (
        f"{name} no longer mentions the 24-hour placeholders")


@pytest.mark.parametrize("name", TEMPLATE_BUILDERS)
def test_se_selects_24_hour_and_us_keeps_12_hour(source, name):
    """The conditional must map SE to the 24-hour placeholder, not the reverse.

    A transposition here renders every time wrong in both formats at once and
    raises nothing, so the direction is asserted rather than merely the presence
    of both placeholders.
    """
    body = _fn_source(source, name)
    start = re.search(r'"\{starttime24\}"\s+if\s+(\w+)\s*==\s*"SE"\s+else\s+"\{starttime\}"', body)
    end = re.search(r'"\{endtime24\}"\s+if\s+(\w+)\s*==\s*"SE"\s+else\s+"\{endtime\}"', body)
    assert start, (
        f"{name} does not select the 24-hour START placeholder for SE and the "
        f"12-hour one otherwise")
    assert end, (
        f"{name} does not select the 24-hour END placeholder for SE and the "
        f"12-hour one otherwise")


def test_the_format_is_compared_case_insensitively(source):
    """Settings are operator-typed. 'se' must work as well as 'SE'."""
    for name in TEMPLATE_BUILDERS:
        body = _fn_source(source, name)
        assert ".upper()" in body, (
            f"{name} compares the channel-name format without normalising case, "
            f"so a stored value of 'se' would silently take the US branch")


def test_no_12_hour_placeholder_is_left_hardcoded_in_a_template(source):
    """The point of the change is that no template string pins the 12-hour form.

    A literal "{starttime}" inside an f-string template would defeat the
    conditional above while both tests still passed.
    """
    offenders = []
    for name in TEMPLATE_BUILDERS:
        for line in _fn_source(source, name).splitlines():
            if "title_template" not in line:
                continue
            if "{starttime}" in line or "{endtime}" in line:
                offenders.append((name, line.strip()))
    assert not offenders, (
        f"a title template still hardcodes a 12-hour placeholder: {offenders}. "
        f"It must come from the format-dependent variable instead.")


def test_the_se_format_really_carries_24_hour_times(source):
    """The premise. If the Swedish time pattern ever accepted AM/PM, choosing the
    24-hour placeholder for it would stop being the right call."""
    match = re.search(r"se_time_pattern\s*=\s*r?\"([^\"]+)\"", source)
    assert match, "se_time_pattern is gone or was renamed"
    pattern = match.group(1)
    assert "hour" in pattern and "minute" in pattern
    assert not re.search(r"(?i)am|pm", pattern), (
        "the Swedish time pattern now accepts AM/PM, so it no longer follows "
        "that Swedish names are 24-hour; re-check the placeholder choice")
