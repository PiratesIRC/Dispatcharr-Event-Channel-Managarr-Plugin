"""Unit tests for ecm_parsing — the Django-free parsing module.

These fixtures were captured from the live plugin as a behavior baseline.
They document the ground-truth outputs of extract_date_from_channel_name,
apply_meridiem, resolve_numeric_date_pair, and name_has_stop_timestamp.
Failing any of these tests means a regression in the parsing logic.
"""

import pytest
from datetime import date, datetime

import ecm_parsing
from ecm_parsing import (
    apply_meridiem,
    coerce_timezone,
    extract_date_from_channel_name,
    lock_is_stale,
    name_has_stop_timestamp,
    resolve_numeric_date_pair,
)

# Pin "now" so year-relative patterns are deterministic.
NOW = datetime(2026, 6, 10, 12, 0, 0)


# ---------------------------------------------------------------------------
# extract_date_from_channel_name — parametrized ground-truth table
# ---------------------------------------------------------------------------

EXTRACT_CASES = [
    # (name, date_format, prefer, expected_iso)
    # start:/stop: timestamp pairs — prefer selects which end
    ("Fight start:2026-06-10 20:00:00 stop:2026-06-10 23:00:00", "Auto", "start", "2026-06-10T20:00:00"),
    ("Fight start:2026-06-10 20:00:00 stop:2026-06-10 23:00:00", "Auto", "stop",  "2026-06-10T23:00:00"),
    # stop-only: falls back to stop regardless of prefer
    ("Game stop:2026-06-10 23:00:00",                            "Auto", "start", "2026-06-10T23:00:00"),
    # parenthesised YYYY-MM-DD HH:MM:SS AM/PM (Pattern 0a)
    ("Boxing (2026-05-01 02:20:00 PM)",                          "Auto", "start", "2026-05-01T14:20:00"),
    ("NYE (2026-01-01 12:00:00 AM)",                             "Auto", "start", "2026-01-01T00:00:00"),
    ("Noon (2026-01-01 12:00:00 PM)",                            "Auto", "start", "2026-01-01T12:00:00"),
    # M/D/YYYY — format variants
    ("Match 15/04/2026",                                         "Auto", "start", "2026-04-15T00:00:00"),
    ("Match 15/04/2026",                                         "US",   "start", None),
    ("Match 15/04/2026",                                         "EU",   "start", "2026-04-15T00:00:00"),
    ("Xmas 04/15/2026",                                          "Auto", "start", "2026-04-15T00:00:00"),
    # M/D/YY two-digit year
    ("Old 12/25/24",                                             "Auto", "start", "2024-12-25T00:00:00"),
    # MONTH DD[ HH:MM] (Pattern 2b)
    ("NBA Nov 8 16:00",                                          "Auto", "start", "2026-11-08T16:00:00"),
    # MONTH DD with 12-hour clock + AM/PM (Pattern 2b, bug-047). Meridiem must be applied.
    ("UFC Fight Night @ Jun 20 4:00 PM",                         "Auto", "start", "2026-06-20T16:00:00"),
    ("Show @ Jun 20 11:00 PM",                                   "Auto", "start", "2026-06-20T23:00:00"),
    ("Show @ Jun 20 12:00 AM",                                   "Auto", "start", "2026-06-20T00:00:00"),
    ("Show @ Jun 20 12:00 PM",                                   "Auto", "start", "2026-06-20T12:00:00"),
    # MONTH DD with seconds + AM/PM — seconds tolerated, meridiem applied (Pattern 2b)
    ("Gala Jun 20 4:00:00 PM",                                   "Auto", "start", "2026-06-20T16:00:00"),
    # MONTH DD with no time still yields a midnight date (Pattern 2b regression guard)
    ("NBA Nov 8",                                                "Auto", "start", "2026-11-08T00:00:00"),
    # DDth MONTH (Pattern 2c)
    ("Race 28th Apr",                                            "Auto", "start", "2026-04-28T00:00:00"),
    # M.D without year (Pattern 3)
    ("Event 10.25",                                              "Auto", "start", "2026-10-25T00:00:00"),
    ("PPV 6.9",                                                  "Auto", "start", "2026-06-09T00:00:00"),
    # M.D with a trailing 12-hour event time (Pattern 3, bug-046) — time must be attached
    ("EVENT 21: Dirt Zone (6.19 7:30 PM ET)",                    "Auto", "start", "2026-06-19T19:30:00"),
    ("PPV 6.9 10:00 PM",                                         "Auto", "start", "2026-06-09T22:00:00"),
    ("Game 10.25 12:00 AM",                                      "Auto", "start", "2026-10-25T00:00:00"),
    ("Game 10.25 12:00 PM",                                      "Auto", "start", "2026-10-25T12:00:00"),
    # M/D without year (Pattern 4) — format variants
    ("Game 10/27",                                               "Auto", "start", "2026-10-27T00:00:00"),
    # M/D with trailing 12-hour event time (Pattern 4, bug-046)
    ("Race 10/27 8:00 PM",                                       "Auto", "start", "2026-10-27T20:00:00"),
    # Lookahead still rejects "1/3:30pm" even with the new optional time group
    ("Time 1/3:30pm",                                            "Auto", "start", None),
    ("Match 15/04",                                              "Auto", "start", "2026-04-15T00:00:00"),
    ("Match 15/04",                                              "US",   "start", None),
    ("Match 15/04",                                              "EU",   "start", "2026-04-15T00:00:00"),
    # Lookahead exclusion: "1/3:30pm" looks like M/D but `:` follows second number
    ("Time 1/3:30pm",                                            "Auto", "start", None),
    # No date present
    ("ESPN HD",                                                  "Auto", "start", None),
    ("",                                                         "Auto", "start", None),
]


@pytest.mark.parametrize("name,fmt,prefer,expected_iso", EXTRACT_CASES)
def test_extract_date_from_channel_name(name, fmt, prefer, expected_iso):
    result = extract_date_from_channel_name(name, date_format=fmt, prefer=prefer, now=NOW)
    actual = result.isoformat() if result else None
    assert actual == expected_iso, (
        f"extract_date_from_channel_name({name!r}, fmt={fmt!r}, prefer={prefer!r}) "
        f"=> {actual!r}, expected {expected_iso!r}"
    )


# ---------------------------------------------------------------------------
# apply_meridiem
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,meridiem,expected", [
    (12, "AM", 0),
    (12, "PM", 12),
    (1,  "PM", 13),
    (11, "AM", 11),
    (5,  None, 5),
])
def test_apply_meridiem(hour, meridiem, expected):
    assert apply_meridiem(hour, meridiem) == expected


# ---------------------------------------------------------------------------
# resolve_numeric_date_pair
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first,second,year,fmt,expected", [
    # US: 15 is not a valid month -> None
    (15, 4,  2026, "US",   None),
    # EU: DD/MM -> April 15
    (15, 4,  2026, "EU",   datetime(2026, 4, 15)),
    # Auto with unambiguous MM/DD (4 is valid month, 15 valid day) -> April 15
    (4,  15, 2026, "Auto", datetime(2026, 4, 15)),
    # Auto where first (15) > 12 so falls back to DD/MM -> April 15
    (15, 4,  2026, "Auto", datetime(2026, 4, 15)),
])
def test_resolve_numeric_date_pair(first, second, year, fmt, expected):
    result = resolve_numeric_date_pair(first, second, year, fmt)
    assert result == expected, (
        f"resolve_numeric_date_pair({first}, {second}, {year}, {fmt!r}) "
        f"=> {result!r}, expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# name_has_stop_timestamp
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Game stop:2026-06-10 23:00:00",           True),
    ("Fight start:2026-06-10 20:00:00",         False),
    ("",                                        False),
])
def test_name_has_stop_timestamp(name, expected):
    assert name_has_stop_timestamp(name) == expected


# ---------------------------------------------------------------------------
# coerce_timezone — validate Dispatcharr's global tz, fall back to UTC
# ---------------------------------------------------------------------------

COERCE_TZ_CASES = [
    ("America/New_York", "America/New_York"),
    ("Europe/Stockholm", "Europe/Stockholm"),
    ("  Europe/Stockholm  ", "Europe/Stockholm"),  # trimmed
    ("UTC", "UTC"),
    ("", "UTC"),            # blank
    ("   ", "UTC"),         # whitespace only
    (None, "UTC"),          # missing row -> getattr default
    ("Not/AZone", "UTC"),   # invalid name
    (123, "UTC"),           # non-string (e.g. mis-configured int)
]


@pytest.mark.parametrize("value,expected", COERCE_TZ_CASES)
def test_coerce_timezone(value, expected):
    assert coerce_timezone(value) == expected


# ---------------------------------------------------------------------------
# lock_is_stale — decide whether a held scan lock is leaked/abandoned
# ---------------------------------------------------------------------------

def test_lock_is_stale_basic():
    assert lock_is_stale(0.0, 1000.0, 900.0) is True       # 1000 > 900
    assert lock_is_stale(500.0, 1000.0, 900.0) is False    # 500 < 900
    assert lock_is_stale(1000.0, 1000.0, 900.0) is False   # age 0


def test_lock_is_stale_boundary_is_exclusive():
    # age exactly == max_age is NOT stale (strictly greater-than)
    assert lock_is_stale(100.0, 1000.0, 900.0) is False    # age 900 == threshold


def test_lock_is_stale_handles_bad_input():
    assert lock_is_stale(None, 1000.0, 900.0) is False
    assert lock_is_stale(0.0, None, 900.0) is False
    assert lock_is_stale(0.0, 1000.0, None) is False


# --- [UndatedEnded]: a clock time with no date, and the window inferred from it ------

@pytest.mark.parametrize("name, expected", [
    ("Boxing 3 : MOSES vs HRGOVIC  4:00pm", (16, 0)),
    ("PPV 07 - 8pm Main Card", (20, 0)),
    ("EVENT 12 | 11:30 AM Coverage", (11, 30)),
    ("PPV 02 - Championship Final", None),
])
def test_extract_time_of_day_reads_the_first_clock_time(name, expected):
    assert ecm_parsing.extract_time_of_day(name) == expected


def test_extract_time_of_day_uses_a_supplied_24_hour_pattern():
    # The SE channel-name format carries a 24-hour clock and no am/pm marker.
    name = "LIVE | GIRONA - REAL SOCIEDAD | Thu 14 May 19:55 CEST (SE)"
    pattern = r"(?<hour>\d{1,2}):(?<minute>\d{2})"
    assert ecm_parsing.extract_time_of_day(name, pattern) == (19, 55)


def test_extract_time_of_day_falls_back_when_the_pattern_does_not_compile():
    assert ecm_parsing.extract_time_of_day("PPV 07 - 8pm Main Card", "(unclosed") == (20, 0)


def test_infer_undated_event_window_adds_duration_and_grace():
    start, hide_after = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", 180, 1)
    assert (start.year, start.month, start.day, start.hour) == (2026, 8, 30, 20)
    # 20:00 plus a three hour programme plus one hour of grace is 00:00 the next day.
    assert (hide_after.year, hide_after.month, hide_after.day, hide_after.hour) == (2026, 8, 31, 0)
    assert start.tzinfo is not None and hide_after.tzinfo is not None


def test_infer_undated_event_window_crosses_midnight():
    start, hide_after = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 22, 30, "US/Eastern", 240, 2)
    assert (hide_after.month, hide_after.day, hide_after.hour, hide_after.minute) == (8, 31, 4, 30)


def test_infer_undated_event_window_rejects_an_unknown_timezone():
    assert ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "Mars/Olympus", 180, 1) is None


# --- the hide decision itself, which the contract tests cannot exercise -------------

def _eastern(year, month, day, hour, minute=0):
    import pytz
    return pytz.timezone("US/Eastern").localize(
        datetime(year, month, day, hour, minute))


def test_undated_event_has_not_ended_while_the_event_is_running():
    # Event at 20:00, three hours long, one hour of grace, so it hides from 00:00.
    _, hide_after = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", 180, 1)
    assert ecm_parsing.undated_event_has_ended(
        _eastern(2026, 8, 30, 21), hide_after) is False


def test_undated_event_has_ended_once_the_window_closes():
    _, hide_after = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", 180, 1)
    assert ecm_parsing.undated_event_has_ended(
        _eastern(2026, 8, 31, 1), hide_after) is True


def test_the_grace_period_moves_the_boundary():
    """A longer grace period must keep the channel visible at a moment a shorter one hides.

    Without this, a rule that ignored the configured grace period entirely would still
    pass every other test in this file.
    """
    moment = _eastern(2026, 8, 31, 3)
    _, one_hour = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", 180, 1)
    _, six_hours = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", 180, 6)
    assert ecm_parsing.undated_event_has_ended(moment, one_hour) is True
    assert ecm_parsing.undated_event_has_ended(moment, six_hours) is False


def test_a_window_that_closed_before_the_channel_appeared_does_not_hide_it():
    """A channel seen at 23:00 named for a 1:00am event is named for the NEXT 1:00am.

    The first-seen date is today, so the inferred window is 01:00 to 05:00 THIS morning,
    already past. Hiding on it would remove a channel two hours before its event starts.
    """
    _, hide_after = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 1, 0, "US/Eastern", 180, 1)
    first_seen_at = _eastern(2026, 8, 30, 23)
    assert ecm_parsing.undated_event_has_ended(
        _eastern(2026, 8, 30, 23), hide_after, first_seen_at) is False
    # And it stays visible later that night, rather than only at the instant of the scan.
    assert ecm_parsing.undated_event_has_ended(
        _eastern(2026, 8, 31, 0, 30), hide_after, first_seen_at) is False


def test_a_record_without_a_first_seen_moment_still_hides_a_finished_event():
    """An entry written by an earlier version carries the date only, and must still work."""
    _, hide_after = ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", 180, 1)
    assert ecm_parsing.undated_event_has_ended(
        _eastern(2026, 8, 31, 1), hide_after, None) is True


def test_undated_event_has_ended_is_false_when_there_is_no_window():
    assert ecm_parsing.undated_event_has_ended(_eastern(2026, 8, 30, 23), None) is False
    assert ecm_parsing.undated_event_has_ended(None, _eastern(2026, 8, 30, 23)) is False


# --- failures must abandon the window, never shorten it -----------------------------

def test_a_pattern_that_matches_nothing_means_the_name_carries_no_time():
    """A narrowed pattern must be respected, not silently replaced by the built-in one.

    The SE pattern reads a 24 hour clock. Against a name written in am/pm it matches
    nothing, and the honest answer is that this pattern finds no time here. Falling back
    to the built-in am/pm pattern would revert the narrowing the user configured.
    """
    se_pattern = r"(?<hour>\d{1,2}):(?<minute>\d{2})"
    assert ecm_parsing.extract_time_of_day("PPV 07 - 8pm Main Card", se_pattern) is None


def test_a_pattern_that_does_not_compile_still_falls_back_to_the_builtin():
    """A typing mistake must not stop the name being read at all."""
    assert ecm_parsing.extract_time_of_day("PPV 07 - 8pm Main Card", "(unclosed") == (20, 0)


def test_time_pattern_problem_reports_only_a_pattern_that_cannot_compile():
    assert ecm_parsing.time_pattern_problem(None) is None
    assert ecm_parsing.time_pattern_problem("") is None
    # The JavaScript named-group form is what Dispatcharr stores, so it is not a problem.
    assert ecm_parsing.time_pattern_problem(r"(?<hour>\d{1,2}):(?<minute>\d{2})") is None
    problem = ecm_parsing.time_pattern_problem("(unclosed")
    assert isinstance(problem, str) and problem


def test_an_unreadable_duration_abandons_the_window_rather_than_shortening_it():
    """Collapsing to zero would hide the channel earlier than any configured value."""
    assert ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", "not a number", 1) is None


def test_an_unreadable_grace_period_abandons_the_window():
    assert ecm_parsing.infer_undated_event_window(
        date(2026, 8, 30), 20, 0, "US/Eastern", 180, "not a number") is None


def test_an_unreadable_minute_abandons_the_time_rather_than_assuming_oclock():
    """A user pattern can capture a non-numeric minute. Assuming :00 would move the
    inferred start up to 59 minutes earlier and the end of the window with it."""
    pattern = r"(?<hour>\d{1,2}):(?<minute>[A-Za-z]{2})"
    assert ecm_parsing.extract_time_of_day("Event 7:xx tonight", pattern) is None
