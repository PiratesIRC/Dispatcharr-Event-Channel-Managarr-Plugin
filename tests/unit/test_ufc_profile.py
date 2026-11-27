"""UFC slot names carry their own start and stop stamps.

Measured on the live installation 2026-11-27, in US: PPV and in the provider's UFC
stream group:

    "UFC 02: UFC FREEDOM 250: TOPURIA VS GAETHJE start:2026-06-15 01:55:00 stop:..."
    "UFC event: 1 name: UFC FIGHT NIGHT: PROCHAZKA VS STIRLING  start:2026-11-21 18:55:00 stop:..."

The shared US source matched none of them, so the guide repeated the whole name
in four-hour blocks all day. They now route to their own source, ECM UFC Dummy,
which reads the date and time after "start:".

The stamps are read at a fixed UTC+1 (Etc/GMT-1; the sign in Etc names is
inverted). That is inferred from published broadcast times, not measured here:
pay-per-view prelims stamped to stop at 03:00 on 2026-10-04 and at 04:00 on
2025-12-07 both end at 10 PM Eastern, the usual main-card start, only at UTC+1,
and the Contender Series stamp 00:55 on 2026-10-14 is 7:55 PM Eastern for an
8 PM show only at UTC+1. A summer and a winter date agreeing rules out London,
Paris and plain UTC.
"""

from datetime import datetime, timedelta, timezone

import ecm_profiles
import pytest

UFC_NAMES = [
    "UFC 02: UFC FREEDOM 250: TOPURIA VS GAETHJE start:2026-06-15 01:55:00 stop:2026-06-15 06:15:00",
    "UFC 01 : UFC FREEDOM 250: POST-FIGHT PRESS CONFERENCE start:2026-06-15 06:55:00 stop:2026-06-15 09:00:00",
    "UFC 08 : LUX 056 start:2025-11-22 03:55:00 stop:2025-11-22 09:00:00",
    "UFC 04 : (FRANÇAIS) UFC 332: PRELIMS start:2026-10-03 23:55:00 stop:2026-10-04 03:00:00",
    "UFC event: 1 name: UFC FIGHT NIGHT: PROCHAZKA VS STIRLING  start:2026-11-21 18:55:00 stop:2026-11-21 22:00:00",
]

NOT_UFC = [
    "LIVE EVENT 05 - 1pm Cage Warriors 214: Unplugged",
    "PPV EVENT 05: UFC 300 Watch Party (4.13 8:00 PM ET)",
    "UFC 05: No Event Scheduled",
    "Boxing (2026-05-01 02:20:00 PM)",
    "PDC 04 | 2026 Grand Slam of Darts | Day 9 | Sun 22nd Nov 7:00PM",
]


def _ufc():
    return next(p for p in ecm_profiles.PROFILES if p.key == "ufc_stamp")


def test_the_ufc_profile_reads_a_fixed_utc_plus_one_on_its_own_source():
    ufc = _ufc()
    assert ufc.source_name == "ECM UFC Dummy"
    assert ufc.timezone == "Etc/GMT-1"
    assert ufc.release_unclaimed is True and ufc.is_default is False
    assert ufc.source_name in ecm_profiles.RESERVED_SOURCE_NAMES


def test_etc_gmt_minus_one_is_utc_plus_one():
    """Guards the inverted sign: Etc/GMT-1 is one hour AHEAD of UTC."""
    from zoneinfo import ZoneInfo
    offset = datetime(2026, 7, 1, tzinfo=ZoneInfo("Etc/GMT-1")).utcoffset()
    assert offset == timedelta(hours=1)
    offset = datetime(2026, 1, 1, tzinfo=ZoneInfo("Etc/GMT-1")).utcoffset()
    assert offset == timedelta(hours=1)


@pytest.mark.parametrize("name", UFC_NAMES)
def test_stamped_ufc_names_are_claimed_by_the_ufc_profile(name):
    assert ecm_profiles.route([name])["ufc_stamp"] == [name]


@pytest.mark.parametrize("name", NOT_UFC)
def test_other_names_are_not_claimed_by_the_ufc_profile(name):
    assert ecm_profiles.route([name])["ufc_stamp"] == []


@pytest.mark.parametrize("name,title,start", [
    (UFC_NAMES[0], "UFC FREEDOM 250: TOPURIA VS GAETHJE", (2026, 6, 15, 1, 55)),
    (UFC_NAMES[2], "LUX 056", (2025, 11, 22, 3, 55)),
    (UFC_NAMES[3], "(FRANÇAIS) UFC 332: PRELIMS", (2026, 10, 3, 23, 55)),
    (UFC_NAMES[4], "UFC FIGHT NIGHT: PROCHAZKA VS STIRLING", (2026, 11, 21, 18, 55)),
])
def test_the_ufc_patterns_read_the_title_and_the_start_stamp(name, title, start):
    ufc = _ufc()
    t = ecm_profiles.compile_pattern(ufc.title_pattern).search(name)
    d = ecm_profiles.compile_pattern(ufc.date_pattern).search(name)
    h = ecm_profiles.compile_pattern(ufc.time_pattern).search(name)
    assert t and d and h
    assert t.group("title") == title
    got = tuple(int(x) for x in (d.group("year"), d.group("month"), d.group("day"),
                                 h.group("hour"), h.group("minute")))
    assert got == start


def test_the_stop_stamp_is_never_read_as_the_start():
    ufc = _ufc()
    name = "UFC 03 : PRELIMS stop:2026-10-04 03:00:00 start:2026-10-03 23:55:00"
    h = ecm_profiles.compile_pattern(ufc.time_pattern).search(name)
    assert (h.group("hour"), h.group("minute")) == ("23", "55")


def test_the_prelims_stamp_lands_at_the_main_card_start_in_eastern_time():
    """The inference the timezone rests on, written down as arithmetic."""
    from zoneinfo import ZoneInfo
    stop = datetime(2026, 10, 4, 3, 0, tzinfo=ZoneInfo("Etc/GMT-1"))
    eastern = stop.astimezone(ZoneInfo("America/New_York"))
    assert (eastern.day, eastern.hour) == (3, 22)
    assert stop.astimezone(timezone.utc).hour == 2


def test_the_committed_fixture_is_not_claimed_by_the_ufc_profile():
    from pathlib import Path
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"
    names = [n for n in fixture.read_text(encoding="utf-8").splitlines() if n.strip()]
    claimed = ecm_profiles.route(names)["ufc_stamp"]
    assert all("start:" in n for n in claimed)


# --- the hide rules read the stamp in the same zone as the guide ----------------
#
# Until 2026-11-27 the hide rules read a UFC stamp as a Central wall-clock time, so
# with UTC+1 stamps a channel stayed visible about seven hours past its real end
# before the grace period even started.

import logging  # noqa: E402

UFC_PRELIMS = "UFC 07: UFC 323: PRELIMS start:2025-12-07 01:55:00 stop:2025-12-07 04:00:00"


def test_a_ufc_stop_stamp_becomes_central_wall_time():
    got = ecm_profiles.stamp_wall_time(UFC_PRELIMS, datetime(2025, 12, 7, 4, 0), "America/Chicago")
    assert got == datetime(2025, 12, 6, 21, 0)
    assert got.tzinfo is None


def test_a_ufc_stamp_can_be_given_in_another_wall_zone():
    got = ecm_profiles.stamp_wall_time(UFC_PRELIMS, datetime(2025, 12, 7, 4, 0), "America/New_York")
    assert got == datetime(2025, 12, 6, 22, 0)


@pytest.mark.parametrize("name", NOT_UFC + ["Boxing (2026-05-01 02:20:00 PM)"])
def test_other_names_are_returned_unchanged(name):
    when = datetime(2026, 5, 1, 14, 20)
    assert ecm_profiles.stamp_wall_time(name, when, "America/Chicago") is when


@pytest.mark.parametrize("wall_tz", ["", None, "Not/AZone"])
def test_an_unusable_wall_zone_leaves_the_time_alone(wall_tz):
    when = datetime(2025, 12, 7, 4, 0)
    assert ecm_profiles.stamp_wall_time(UFC_PRELIMS, when, wall_tz) is when


def test_no_time_stays_no_time():
    assert ecm_profiles.stamp_wall_time(UFC_PRELIMS, None, "America/Chicago") is None


@pytest.fixture
def past_date(bare_plugin, monkeypatch, plugin_module):
    """Run the real [PastDate] rule with the clock fixed at a Central wall time."""
    def _run(name, when_central, grace_hours=4):
        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return tz.localize(when_central) if tz else when_central
        monkeypatch.setattr(plugin_module, "datetime", _Clock)
        settings = {"timezone": "America/Chicago", "past_date_grace_hours": str(grace_hours)}
        return bare_plugin._check_hide_rule("PastDate", 0, None, name, logging.getLogger("t"), settings)
    return _run


def test_past_date_hides_a_ufc_event_four_hours_after_its_real_end(past_date):
    # Real end: 04:00 UTC+1 = 21:00 Central on 6 Dec. Grace 4 h, so 01:00 Central on 7 Dec.
    assert past_date(UFC_PRELIMS, datetime(2025, 12, 7, 0, 59))[0] is False
    hide, reason = past_date(UFC_PRELIMS, datetime(2025, 12, 7, 1, 1))
    assert hide is True
    assert "12/06/2025 09:00 PM" in reason


def test_the_extractor_converts_for_the_rule_that_asks(bare_plugin):
    settings = {"timezone": "America/Chicago"}
    got = bare_plugin._extract_date_from_channel_name(UFC_PRELIMS, logging.getLogger("t"),
                                                       settings, prefer="stop")
    assert got == datetime(2025, 12, 6, 21, 0)
    got = bare_plugin._extract_date_from_channel_name(UFC_PRELIMS, logging.getLogger("t"),
                                                       settings, wall_tz="America/New_York")
    assert got == datetime(2025, 12, 6, 19, 55)
