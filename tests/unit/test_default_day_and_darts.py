"""Two faults found in the applied run of 2026-11-22 14:39 CST.

1. [UndatedEnded] ignored the channel group's Default Event Day. Every game of the
   NFL slate ("NFL | 08 - 1pm Ravens at Panthers"), first seen on Tuesday 11/17, was
   judged finished by Saturday and stayed hidden on the Sunday it was played.
2. PDC darts channels ("PDC 04 | 2026 Grand Slam of Darts | ... | Sun 22nd Nov 7:00PM")
   carry a UK clock time with no "UK" beside it, so they sat on the shared US source
   and the guide drew them five hours late.
"""
from datetime import date, datetime

import pytest
import pytz

import ecm_parsing
import ecm_profiles

CENTRAL = pytz.timezone("America/Chicago")
SUNDAY = 6


def _seen(y, m, d, hh, mm):
    return CENTRAL.localize(datetime(y, m, d, hh, mm))


# --- 1. Default Event Day for [UndatedEnded] -----------------------------------

def test_a_slate_first_seen_on_tuesday_anchors_on_that_sunday():
    got = ecm_parsing.resolve_default_day_date(
        date(2026, 11, 17), SUNDAY, 13, 0, "US/Eastern", _seen(2026, 11, 17, 19, 56))
    assert got == date(2026, 11, 22)


def test_a_slate_posted_on_sunday_after_kickoff_is_for_the_next_sunday():
    """Next week's names appear on Sunday afternoon, after that day's games."""
    got = ecm_parsing.resolve_default_day_date(
        date(2026, 11, 22), SUNDAY, 13, 0, "US/Eastern", _seen(2026, 11, 22, 15, 52))
    assert got == date(2026, 11, 29)


def test_a_name_seen_on_sunday_before_kickoff_is_for_that_sunday():
    got = ecm_parsing.resolve_default_day_date(
        date(2026, 11, 22), SUNDAY, 13, 0, "US/Eastern", _seen(2026, 11, 22, 10, 0))
    assert got == date(2026, 11, 22)


def test_without_a_first_seen_moment_the_plain_forward_date_is_used():
    got = ecm_parsing.resolve_default_day_date(
        date(2026, 11, 22), SUNDAY, 13, 0, "US/Eastern", None)
    assert got == date(2026, 11, 22)


def test_an_unreadable_timezone_falls_back_to_the_plain_forward_date():
    got = ecm_parsing.resolve_default_day_date(
        date(2026, 11, 17), SUNDAY, 13, 0, "Not/AZone", _seen(2026, 11, 17, 19, 56))
    assert got == date(2026, 11, 22)


@pytest.mark.parametrize("weekday", [None, 7, -1, True, "6"])
def test_an_unusable_weekday_gives_no_anchor(weekday):
    assert ecm_parsing.resolve_default_day_date(
        date(2026, 11, 17), weekday, 13, 0, "US/Eastern", None) is None


def test_the_sunday_game_is_not_ended_during_the_sunday_run():
    """End to end through the shared window helpers: the 2026-11-22 14:39 CST run."""
    anchor = ecm_parsing.resolve_default_day_date(
        date(2026, 11, 17), SUNDAY, 16, 25, "US/Eastern", _seen(2026, 11, 17, 19, 56))
    start, hide_after = ecm_parsing.infer_undated_event_window(
        anchor, 16, 25, "US/Eastern", 240, 96)
    now = _seen(2026, 11, 22, 14, 39)
    assert not ecm_parsing.undated_event_has_ended(now, hide_after, _seen(2026, 11, 17, 19, 56))
    # The old first-seen anchor was judged ended, which is the fault.
    _s, old_hide = ecm_parsing.infer_undated_event_window(
        date(2026, 11, 17), 16, 25, "US/Eastern", 240, 96)
    assert ecm_parsing.undated_event_has_ended(now, old_hide, _seen(2026, 11, 17, 19, 56))


# --- 2. PDC darts profile -------------------------------------------------------

DARTS = [
    "PDC 04 | 2026 Grand Slam of Darts | Day 9   Evening Session   Board 2 | Sun 22nd Nov 7:00PM",
    "PDC 01 | 2026 Grand Slam of Darts | Day 9   Afternoon Session   Board 1 | Sun 22nd Nov 12:00PM",
]
NOT_DARTS = [
    "PDC 05: No Event Scheduled",
    "Boxing 3 | Canelo vs Mbilli     UK 2:00pm",
    "PPV EVENT 01: Thor Mini Os at Gatorback (11.22 12:00 PM ET)",
    "NFL  | 08 - 1pm Ravens at Panthers",
]


def _pdc():
    return next(p for p in ecm_profiles.PROFILES if p.key == "pdc_london")


def test_the_darts_profile_reads_london_time_on_its_own_source():
    pdc = _pdc()
    assert pdc.timezone == "Europe/London"
    assert pdc.source_name == "ECM UK Darts Dummy"
    assert pdc.release_unclaimed is True and pdc.is_default is False
    assert pdc.source_name in ecm_profiles.RESERVED_SOURCE_NAMES


@pytest.mark.parametrize("name", DARTS)
def test_darts_names_are_claimed_by_the_darts_profile(name):
    assert ecm_profiles.route([name])["pdc_london"] == [name]


@pytest.mark.parametrize("name", NOT_DARTS)
def test_other_names_are_not_claimed_by_the_darts_profile(name):
    assert ecm_profiles.route([name])["pdc_london"] == []


def test_uk_boxing_still_routes_to_the_uk_profile():
    name = "Boxing 3 | Canelo vs Mbilli     UK 2:00pm"
    assert ecm_profiles.route([name])["uk_london"] == [name]


def test_the_darts_patterns_read_title_date_and_time():
    pdc = _pdc()
    name = DARTS[0]
    title = ecm_profiles.compile_pattern(pdc.title_pattern).search(name)
    assert title.group("title") == "2026 Grand Slam of Darts | Day 9   Evening Session   Board 2"
    d = ecm_profiles.compile_pattern(pdc.date_pattern).search(name)
    assert (d.group("day"), d.group("month")) == ("22", "Nov")
    t = ecm_profiles.compile_pattern(pdc.time_pattern).search(name)
    assert (t.group("hour"), t.group("minute"), t.group("ampm").upper()) == ("7", "00", "PM")


def test_the_committed_fixture_is_not_claimed_by_the_darts_profile():
    from pathlib import Path
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"
    names = fixture.read_text(encoding="utf-8").splitlines()
    assert ecm_profiles.route(names)["pdc_london"] == []
