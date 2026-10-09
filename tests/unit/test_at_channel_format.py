"""Channel Name Format "AT": the managed dummy EPG patterns for "... @ 9 Oct 07:00 PM ET" names.

The fixture holds every channel name containing " @ " from two dry-run reports a
user attached to a support case (2026-10-07 and 2026-10-09), 162 names across NBA,
NHL, MLB, NCAAF and NCAA baseball. Expected values are derived from each name with
plain string splitting and strptime, NOT from the patterns under test, so a defect
in a pattern cannot also hide in the expectation.

Dispatcharr compiles these patterns with the third-party regex module, which is
absent on this machine; Python's re accepts the same syntax once the JavaScript
named groups are converted, which is exactly what the renderer itself does.
"""

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import ecm_parsing
import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "at_style_channel_names.txt"
NAMES = [n for n in FIXTURE.read_text(encoding="utf-8").splitlines() if n.strip()]

PATTERNS = {
    "title": ecm_parsing.AT_TITLE_PATTERN,
    "time": ecm_parsing.AT_TIME_PATTERN,
    "date": ecm_parsing.AT_DATE_PATTERN,
}


def _py(pattern):
    # Same conversion as Dispatcharr's _convert_js_named_groups and plugin._py_named.
    return re.compile(re.sub(r"\(\?<(?![=!])", "(?P<", pattern))


TITLE = _py(ecm_parsing.AT_TITLE_PATTERN)
TIME = _py(ecm_parsing.AT_TIME_PATTERN)
DATE = _py(ecm_parsing.AT_DATE_PATTERN)


def _render(name):
    """What the renderer would read: (title, hour24, minute, month, day), or None."""
    t = TITLE.search(name)
    if not t:
        return None
    tm = TIME.search(name)
    d = DATE.search(name)
    hour = minute = month = day = None
    if tm:
        hour = int(tm.group("hour"))
        minute = int(tm.group("minute") or 0)
        ampm = (tm.group("ampm") or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    if d:
        month = datetime.strptime(d.group("month")[:3], "%b").month
        day = int(d.group("day"))
    return t.group("title"), hour, minute, month, day


def _expected(name):
    head, tail = name.rsplit(" @ ", 1)
    title = re.split(r"[:|]", head, maxsplit=1)[1].strip()
    # Drop everything after the meridiem: the zone, and any suffix such as
    # " ET - Special Feed".
    when = tail[:max(tail.find(" AM"), tail.find(" PM")) + 3]
    for fmt in ("%d %b %I:%M %p", "%b %d %I:%M %p"):
        try:
            dt = datetime.strptime(when, fmt)
            break
        except ValueError:
            continue
    else:
        raise AssertionError(f"fixture name has a date shape the test does not know: {name!r}")
    return title, dt.hour, dt.minute, dt.month, dt.day


def test_fixture_is_the_captured_set():
    assert len(NAMES) == 162


@pytest.mark.parametrize("name", NAMES)
def test_every_captured_name_renders_its_event(name):
    assert _render(name) == _expected(name)


@pytest.mark.parametrize("name,expected", [
    # Month first, as the same provider writes NCAA baseball.
    ("NCAA Baseball 01: Oklahoma vs #5 North Carolina @ Jun 22 07:00 PM ET",
     ("Oklahoma vs #5 North Carolina", 19, 0, 6, 22)),
    # The 2026-09-05 support case: no space before PM, no zone.
    ("NCAAF 09: Ohio State at Texas @ Sep 4 9:00PM", ("Ohio State at Texas", 21, 0, 9, 4)),
    # An @ inside the title is part of the title.
    ("NHL Game Pass 01: Kraken @ Red Wings @ 9 Oct 07:00 PM ET",
     ("Kraken @ Red Wings", 19, 0, 10, 9)),
    # A team that starts with a month abbreviation is not a date.
    ("MLB 05 | Mets @ Marlins 2 @ 9 Oct 7:10 PM ET", ("Mets @ Marlins 2", 19, 10, 10, 9)),
    ("BOX 01: Mayweather @ May 3 9:00 PM ET", ("Mayweather", 21, 0, 5, 3)),
    # Long and "Sept" month spellings; the renderer is handed the abbreviation.
    ("NFL 03: Bears at Lions @ Sept 7 1:00 PM ET", ("Bears at Lions", 13, 0, 9, 7)),
    ("NFL 04: Bears at Lions @ 14 September 8:15 PM ET", ("Bears at Lions", 20, 15, 9, 14)),
    # Ordinal day, comma and a year before the time.
    ("NFL 05: Bears at Lions @ Nov 27th, 12:30 PM ET", ("Bears at Lions", 12, 30, 11, 27)),
    ("NFL 06: Bears at Lions @ 27 Nov 2026 12:30 PM ET", ("Bears at Lions", 12, 30, 11, 27)),
    # 24-hour clock with no am/pm: the renderer reads the hour as already 24-hour.
    ("EPL 01: Arsenal v Spurs @ 9 Oct 19:30 BST", ("Arsenal v Spurs", 19, 30, 10, 9)),
    # 12 AM and 12 PM.
    ("NBA 08: Kings vs Suns @ 9 Oct 12:00 AM ET", ("Kings vs Suns", 0, 0, 10, 9)),
    ("NBA 09: Kings vs Suns @ 9 Oct 12:00 PM ET", ("Kings vs Suns", 12, 0, 10, 9)),
])
def test_shapes_beyond_the_fixture(name, expected):
    assert _render(name) == expected


@pytest.mark.parametrize("name", [
    "NHL 09: NO EVENT",
    "NHL Network HD",
    "60 Minutes",
    "NHL Game Pass 01: Kraken @ Red Wings",          # an @ with no date after it
    "MLB 05 | Mets @ Marlins tonight",
    "PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)",  # the US layout is not this one
    "NBA 01: Kings vs Suns @ Oct 7:30 PM ET",       # a month and a clock, but no day
    # The slot prefix ends at the FIRST colon or pipe. A name whose first colon has no
    # slot number before it is not this layout, so the title is not cut at a later one.
    "UFC: Fight Night 2: Usyk vs Fury @ 9 Oct 9:00 PM ET",
])
def test_names_without_an_at_date_fall_to_the_renderer_fallback(name):
    assert TITLE.search(name) is None


def test_a_clock_hour_is_never_read_as_the_day():
    assert DATE.search("NBA 01: Kings vs Suns @ Oct 7:30 PM ET") is None


def test_a_number_in_the_title_is_never_the_air_time():
    # "76ers" and "#5" precede the @ date; only the clock after it may be read.
    assert _render("NBA 06: Nets vs 76ers (76ers In-Arena) @ 8 Oct 07:30 PM ET")[1:3] == (19, 30)


def test_no_named_group_is_repeated():
    # Python's re rejects a repeated group name; the regex module and JavaScript
    # differ on it. Keeping every name unique keeps all three engines in agreement.
    for key, pattern in PATTERNS.items():
        names = re.findall(r"\(\?<([A-Za-z_]\w*)>", pattern)
        assert len(names) == len(set(names)), key


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_patterns_compile_in_javascript():
    # Dispatcharr's Pattern Configuration panel validates these in the browser and
    # rejects a pattern JavaScript cannot compile (issue #21), so the form a user
    # opens must accept what the plugin writes.
    src = ";".join(f"new RegExp(String.raw`{p}`)" for p in PATTERNS.values())
    result = subprocess.run(["node", "-e", src + ";console.log('ok')"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr
