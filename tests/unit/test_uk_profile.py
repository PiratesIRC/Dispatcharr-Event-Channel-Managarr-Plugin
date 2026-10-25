"""The uk_london profile: channel names that state a UK clock time.

The names below were captured from the live lineup on 2026-10-25. The provider
writes the zone either before the time ("UK 5:00pm") or after it ("6:30pm uk").
"""
import ecm_profiles
import pytest

from .test_ecm_profiles import _fixture_names

UK_NAMES = (
    "Boxing 1 | SMITH vs PUELLO     UK 5:00pm",
    "Boxing 3 | SMITH vs PUELLO     UK 5:00pm",
    "Boxing 1 | Misfits : Essex v Laughs 6:30pm uk",
)

# Kentucky is "UK" in college football stream names. A UK that is not next to a
# clock time is not a timezone.
NOT_UK_NAMES = (
    "US (ESPN+ 374) | NCAA Football: VAN vs. #12 UK • #7 MISS vs. #19 TEX "
    "(VAN vs. #12 UK ) (2026-10-24 19:37:15)",
    "Boxing 4  | Dubois v Wardley 2",
    "Boxing  05  :  FURY vs HALL  6PM",
    "LIVE EVENT 08 - 7pm Peter Welchs Athletics Rising Stars Boxing",
    "PPV EVENT 15: Peter Welchs Athletics Rising Stars Boxing (10.24 7:00 PM ET)",
    "UKRAINE vs ENGLAND 5:00pm",
)


def _uk():
    return next(p for p in ecm_profiles.PROFILES if p.key == "uk_london")


def test_uk_profile_reads_london_time():
    uk = _uk()
    assert uk.timezone == "Europe/London"
    assert uk.source_name == "ECM UK Dummy"
    assert uk.is_default is False
    assert uk.release_unclaimed is True


@pytest.mark.parametrize("name", UK_NAMES)
def test_uk_selector_claims_uk_timed_names(name):
    assert ecm_profiles.route([name])["uk_london"] == [name]


@pytest.mark.parametrize("name", NOT_UK_NAMES)
def test_uk_selector_leaves_other_names_alone(name):
    assert ecm_profiles.route([name])["uk_london"] == []


def test_uk_selector_claims_nothing_in_the_committed_fixture():
    """The fixture holds the US and DAZN families. Claiming any of them would take
    those channels off the source that reads their timezone correctly."""
    assert ecm_profiles.route(_fixture_names())["uk_london"] == []


@pytest.mark.parametrize("name,title,hour,minute", [
    ("Boxing 1 | SMITH vs PUELLO     UK 5:00pm", "SMITH vs PUELLO", "5", "00"),
    ("Boxing 1 | Misfits : Essex v Laughs 6:30pm uk", "Misfits : Essex v Laughs", "6", "30"),
])
def test_uk_patterns_extract_title_and_time(name, title, hour, minute):
    uk = _uk()
    t = ecm_profiles.compile_pattern(uk.title_pattern).search(name)
    assert t is not None and t.group("title") == title
    m = ecm_profiles.compile_pattern(uk.time_pattern).search(name)
    assert m is not None
    assert (m.group("hour"), m.group("minute"), m.group("ampm").lower()) == (hour, minute, "pm")


def _binding(cid, name, source):
    return ecm_profiles.ChannelBinding(
        id=cid, name=name, group_name="US: PPV", source_name=source,
        source_is_plugin_created=False)


def test_uk_named_channel_moves_to_the_uk_source():
    dest = ecm_profiles.routing_destinations(
        [_binding(1, UK_NAMES[0], "ECM Managed Dummy")], (), ecm_profiles.PROFILES,
        "ECM Managed Dummy", True)
    assert dest == {1: "ECM UK Dummy"}


def test_renamed_slot_returns_from_the_uk_source():
    """Boxing 1..8 are reused slots. Once the name no longer states a UK time the
    channel must leave the London source, or an Eastern time is read as London."""
    dest = ecm_profiles.routing_destinations(
        [_binding(1, "Boxing 1 | NEXT FIGHT 8:00pm", "ECM UK Dummy")], (),
        ecm_profiles.PROFILES, "ECM Managed Dummy", True)
    assert dest == {1: "ECM Managed Dummy"}


def test_renamed_slot_stays_put_when_the_mapping_has_problems():
    dest = ecm_profiles.routing_destinations(
        [_binding(1, "Boxing 1 | NEXT FIGHT 8:00pm", "ECM UK Dummy")], (),
        ecm_profiles.PROFILES, "ECM Managed Dummy", False)
    assert dest == {}


def test_dazn_source_still_does_not_release_its_channels():
    """Only uk_london opts in. The DAZN behaviour is unchanged."""
    dest = ecm_profiles.routing_destinations(
        [_binding(1, "NO EVENT STREAMING NOW - | US: DAZN PPV 50", "DAZN PPV Dummy (GMT)")],
        (), ecm_profiles.PROFILES, "ECM Managed Dummy", True)
    assert dest == {}


def test_uk_source_name_cannot_be_a_group_target():
    mapping, problems = ecm_profiles.parse_group_source_map("NFL = ECM UK Dummy")
    assert mapping == {} and len(problems) == 1
