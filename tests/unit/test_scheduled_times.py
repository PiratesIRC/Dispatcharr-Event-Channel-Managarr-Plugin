"""A discarded schedule entry must be reported, not silently dropped.

A user configured the run times "0600,1200,1800,2400" and the midnight run never
fired. Nothing anywhere said why: the parser kept the three entries it could use
and threw the fourth away without a trace, and the settings form's own check only
tested that an entry was four digits, so it called 2400 valid. The two disagreed,
and the disagreement was invisible from the interface.

parse_scheduled_times now returns the rejected text alongside the accepted times
so both the settings form and the scheduler can name what will never run.
"""

import pytest
from datetime import time

from ecm_parsing import parse_scheduled_times


# ---------------------------------------------------------------------------
# Entries that are accepted
# ---------------------------------------------------------------------------

ACCEPTED_CASES = [
    ("0000", [time(0, 0)]),          # midnight is 0000
    ("0600", [time(6, 0)]),
    ("2359", [time(23, 59)]),        # last usable minute of the day
    ("0600,1200,1800", [time(6, 0), time(12, 0), time(18, 0)]),
    (" 0600 , 1200 ", [time(6, 0), time(12, 0)]),   # surrounding spaces are tolerated
    ("0600,,1200", [time(6, 0), time(12, 0)]),      # an empty entry is not a rejection
]


@pytest.mark.parametrize("text,expected", ACCEPTED_CASES)
def test_accepted_entries_parse_and_reject_nothing(text, expected):
    times, rejects = parse_scheduled_times(text)
    assert times == expected
    assert rejects == []


# ---------------------------------------------------------------------------
# Entries that are rejected, and must be named
# ---------------------------------------------------------------------------

REJECTED_CASES = [
    ("2400", ["2400"]),      # the reported case: four digits, but there is no hour 24
    ("2500", ["2500"]),
    ("0060", ["0060"]),      # minute out of range
    ("abcd", ["abcd"]),      # four characters, not digits
    ("600", ["600"]),        # three digits, a plausible typo for 0600
    ("06:00", ["06:00"]),    # colons are not the accepted format
]


@pytest.mark.parametrize("text,expected_rejects", REJECTED_CASES)
def test_rejected_entries_are_reported(text, expected_rejects):
    times, rejects = parse_scheduled_times(text)
    assert times == []
    assert rejects == expected_rejects


def test_a_bad_entry_does_not_discard_the_good_ones():
    """The reported configuration: three usable times and one that cannot fire."""
    times, rejects = parse_scheduled_times("0600,1200,1800,2400")
    assert times == [time(6, 0), time(12, 0), time(18, 0)]
    assert rejects == ["2400"]


def test_every_bad_entry_is_listed_not_just_the_first():
    times, rejects = parse_scheduled_times("2400,0600,9999,xx")
    assert times == [time(6, 0)]
    assert rejects == ["2400", "9999", "xx"]


@pytest.mark.parametrize("text", ["", "   ", None])
def test_no_schedule_is_not_a_rejection(text):
    """An empty setting means the user wants no scheduled runs, not a mistake."""
    times, rejects = parse_scheduled_times(text)
    assert times == []
    assert rejects == []


def test_rejects_preserve_the_user_typed_text():
    """The message shows these back to the user, so they must be recognisable."""
    _, rejects = parse_scheduled_times(" 24:00 ")
    assert rejects == ["24:00"]
