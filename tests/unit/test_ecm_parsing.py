"""Unit tests for ecm_parsing — the Django-free parsing module.

These fixtures were captured from the live plugin as a behavior baseline.
They document the ground-truth outputs of extract_date_from_channel_name,
apply_meridiem, resolve_numeric_date_pair, and name_has_stop_timestamp.
Failing any of these tests means a regression in the parsing logic.
"""

import pytest
from datetime import datetime

import ecm_parsing
from ecm_parsing import (
    apply_meridiem,
    coerce_timezone,
    extract_date_from_channel_name,
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
    # DDth MONTH (Pattern 2c)
    ("Race 28th Apr",                                            "Auto", "start", "2026-04-28T00:00:00"),
    # M.D without year (Pattern 3)
    ("Event 10.25",                                              "Auto", "start", "2026-10-25T00:00:00"),
    ("PPV 6.9",                                                  "Auto", "start", "2026-06-09T00:00:00"),
    # M/D without year (Pattern 4) — format variants
    ("Game 10/27",                                               "Auto", "start", "2026-10-27T00:00:00"),
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
    (123, "UTC"),           # non-string
]


@pytest.mark.parametrize("value,expected", COERCE_TZ_CASES)
def test_coerce_timezone(value, expected):
    assert coerce_timezone(value) == expected
