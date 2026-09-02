"""Unit tests for ecm_profiles - the pure, Django-free profile module."""

import dataclasses
import importlib.util
import re as _stdlib_re
from pathlib import Path

import pytest

import ecm_profiles   # resolves via pyproject.toml pythonpath

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"


def _fixture_names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


# --- Profile / PROFILES shape -------------------------------------------------

def test_exactly_one_default_profile():
    defaults = [p for p in ecm_profiles.PROFILES if p.is_default]
    assert len(defaults) == 1, f"expected 1 default, got {[p.key for p in defaults]}"


def test_profile_keys_are_unique():
    """A duplicate key silently merges two buckets - one profile then routes
    nowhere, and the partition test still passes because the total is preserved."""
    keys = [p.key for p in ecm_profiles.PROFILES]
    assert len(keys) == len(set(keys)), f"duplicate profile keys: {keys}"


def test_no_profile_key_collides_with_the_unclaimed_sentinel():
    assert ecm_profiles.UNCLAIMED not in {p.key for p in ecm_profiles.PROFILES}


@pytest.mark.parametrize("key,expected_name", [
    ("us_et", "ECM Managed Dummy"),
    ("dazn_gmt", "DAZN PPV Dummy (GMT)"),
])
def test_source_names_are_pinned(key, expected_name):
    """EPGSource.name is unique=True and is how sources are looked up. A changed
    name makes get_or_create mint a SECOND source while the original keeps every
    binding - the guide then renders from a row nothing manages.

    BOTH profiles are pinned. Pinning only the default is how rev 1 shipped a
    dazn_gmt name that disagreed with both the live source and bootstrap."""
    profile = next(p for p in ecm_profiles.PROFILES if p.key == key)
    assert profile.source_name == expected_name


def test_dazn_profile_targets_utc():
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    assert dazn.timezone == "UTC"
    assert dazn.output_timezone == "America/Chicago"


def test_profile_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ecm_profiles.PROFILES[0].key = "mutated"


def test_profile_props_round_trips_every_renderer_key():
    """profile_props is what would be written to EPGSource.custom_properties.
    Missing a key means the renderer silently falls back to a default."""
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    props = ecm_profiles.profile_props(dazn)
    for key in ("timezone", "output_timezone", "title_pattern", "date_pattern",
                "time_pattern", "title_template", "upcoming_title_template",
                "ended_title_template", "program_duration", "include_date",
                "fallback_title_template", "fallback_description_template"):
        assert key in props, f"profile_props missing {key}"
    assert props["timezone"] == "UTC"
    assert props["program_duration"] == 240


# --- regex dialect shim --------------------------------------------------------

def _fake_native_engine():
    """Stand-in for the `regex` package: accepts JS (?<name>) natively."""
    class _Engine:
        @staticmethod
        def compile(pattern):
            return _stdlib_re.compile(ecm_profiles.to_python_named(pattern))
    return _Engine


DIALECTS = [
    pytest.param(None, True, id="stdlib_re_converting"),
    pytest.param(_fake_native_engine(), False, id="regex_native"),
]


def test_dialect_detection_matches_environment():
    """Guards against the shim silently flipping: production (container) has
    `regex`, dev machines do not, so each only ever exercises one branch."""
    assert ecm_profiles._NEEDS_CONVERSION == (importlib.util.find_spec("regex") is None)


def test_to_python_named_converts_js_groups():
    assert ecm_profiles.to_python_named(r"(?<title>.+)") == r"(?P<title>.+)"


@pytest.mark.parametrize("pattern", [r"(?<=foo)bar", r"(?<!foo)bar"])
def test_to_python_named_preserves_lookbehind(pattern):
    """(?<= and (?<! are lookbehinds, NOT named groups."""
    assert ecm_profiles.to_python_named(pattern) == pattern


def test_to_python_named_handles_mixed():
    assert ecm_profiles.to_python_named(r"(?<=x)(?<name>\d+)(?<!y)") == r"(?<=x)(?P<name>\d+)(?<!y)"


@pytest.mark.parametrize("engine,convert", DIALECTS)
@pytest.mark.parametrize("profile", ecm_profiles.PROFILES, ids=lambda p: p.key)
@pytest.mark.parametrize("attr", ["selector", "title_pattern", "date_pattern", "time_pattern"])
def test_shipped_patterns_compile_in_both_dialects(profile, attr, engine, convert):
    """Both branches tested on every machine - not just whichever one is installed."""
    value = getattr(profile, attr)
    assert ecm_profiles.compile_pattern(value, engine=engine, convert=convert) is not None, \
        f"{profile.key}.{attr} failed to compile: {value!r}"


def test_compile_pattern_degrades_on_bad_pattern():
    """A bad pattern must never raise on the scan path."""
    assert ecm_profiles.compile_pattern(r"(?<unclosed") is None


# --- pattern EXTRACTION (not merely compilation) -------------------------------

@pytest.mark.parametrize("attr,required_groups", [
    ("title_pattern", {"title"}),
    ("date_pattern", {"year", "month", "day"}),
    ("time_pattern", {"hour", "minute"}),
])
def test_dazn_patterns_extract_from_every_routed_name(attr, required_groups):
    """Compiling is not enough - a pattern that matches NOTHING compiles fine.
    Every name route() gives dazn_gmt must yield every group the renderer needs."""
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    routed = ecm_profiles.route(_fixture_names())["dazn_gmt"]
    assert routed, "no DAZN names routed - extraction assertions would be vacuous"

    rx = ecm_profiles.compile_pattern(getattr(dazn, attr))
    failures = [n for n in routed
                if not (rx.search(n) and required_groups <=
                        {k for k, v in rx.search(n).groupdict().items() if v})]
    assert not failures, (
        f"dazn_gmt.{attr} extracted no {sorted(required_groups)} from "
        f"{len(failures)}/{len(routed)} names, e.g. {failures[:3]}")


def test_us_et_title_extracts_where_the_name_has_event_text():
    """Bare slots (PPV EVENT 48) legitimately extract nothing - the renderer's
    fallback handles them. Names WITH event text must extract."""
    us = next(p for p in ecm_profiles.PROFILES if p.key == "us_et")
    rx = ecm_profiles.compile_pattern(us.title_pattern)
    m = rx.search("PPV EVENT 07: MARS Late Models at Farmer City (7.17 7:30 PM ET)")
    assert m and m.group("title") == "MARS Late Models at Farmer City"


# --- bare-numbered event names (no PPV/LIVE/EVENT keyword) ---------------------
#
# Measured on the live installation 2026-08-14: a provider names its NFL slots
# "07 - 8/14 7pm Broncos at Falcons", with no keyword before the slot number. The
# keyword-only pattern did not match, so Dispatcharr's renderer fell through to
# generate_fallback_programs and the guide showed a repeating block titled with
# the whole channel name instead of the upcoming and ended entries.

@pytest.mark.parametrize("name,expected", [
    ("07 - 8/14 7pm Broncos at Falcons", "Broncos at Falcons"),
    ("10 - 8/15 1pm Panthers at Bills", "Panthers at Bills"),
    ("16 - 8/15 8pm Cowboys at Seahawks", "Cowboys at Seahawks"),
    ("3 - 9/1 12:30pm Some Team at Another", "Some Team at Another"),
    ("07 | 8/14 7pm Broncos at Falcons", "Broncos at Falcons"),
    ("07 - 7pm Broncos at Falcons", "Broncos at Falcons"),
])
def test_us_et_title_extracts_from_bare_numbered_event_names(name, expected):
    """A slot number with no keyword, followed by a date or a time, is an event."""
    us = next(p for p in ecm_profiles.PROFILES if p.key == "us_et")
    rx = ecm_profiles.compile_pattern(us.title_pattern)
    m = rx.search(name)
    assert m, f"no match for {name!r}"
    assert m.group("title") == expected


@pytest.mark.parametrize("name", [
    "60 Minutes",
    "48 Hours",
    "9 News",
    "100 Huntley Street",
    "24 Hours in A&E",
    "1 - Some Channel",
    "3 | News Talk",
])
def test_us_et_title_does_not_claim_an_ordinary_numeric_channel_name(name):
    """Accepting a bare slot number must not strip the number off an ordinary
    channel name. Without a guard, "60 Minutes" extracts the title "Minutes"
    and the guide silently renames the channel. This is the same failure the
    keyword requirement was protecting against (bug-051)."""
    us = next(p for p in ecm_profiles.PROFILES if p.key == "us_et")
    rx = ecm_profiles.compile_pattern(us.title_pattern)
    assert rx.search(name) is None, f"{name!r} must not be treated as an event name"


# --- routing over the real corpus ----------------------------------------------

def test_route_returns_a_bucket_per_profile_plus_unclaimed():
    result = ecm_profiles.route(["PPV EVENT 01: Something"])
    assert set(result) == {"dazn_gmt", "us_et", ecm_profiles.UNCLAIMED}


def test_dazn_bucket_is_exactly_the_gmt_bearing_names():
    """Ground truth is the NAME ITSELF, not a count. This subsumes any count
    assertion and survives lineup churn."""
    names = _fixture_names()
    result = ecm_profiles.route(names)
    assert set(result["dazn_gmt"]) == {n for n in names if "(GMT)" in n}


def test_us_et_bucket_is_exactly_the_legacy_family():
    """SET IDENTITY, not just the total. A count-only assertion would pass on a
    different set of 104 names - precisely the blind spot this plan exists to close."""
    names = _fixture_names()
    result = ecm_profiles.route(names)
    legacy = {n for n in names if n.startswith(("PPV EVENT", "LIVE EVENT"))}
    assert set(result["us_et"]) == legacy


def test_route_partitions_the_corpus_exactly_once():
    names = _fixture_names()
    result = ecm_profiles.route(names)
    assert sum(len(v) for v in result.values()) == len(names)


def test_no_dazn_name_leaks_into_us_et():
    """The failure mode of both rejected design revisions."""
    result = ecm_profiles.route(_fixture_names())
    assert not [n for n in result["us_et"] if "(GMT)" in n]


def test_idle_dazn_slots_are_unclaimed_not_us_et():
    """'NO EVENT STREAMING NOW - | ... | US: DAZN PPV 50' contains 'PPV 50'. An
    unanchored us_et selector claims it; the anchored one must not."""
    result = ecm_profiles.route(_fixture_names())
    assert not [n for n in result["us_et"] if n.startswith("NO EVENT STREAMING NOW")]


def test_no_profile_selector_claims_another_familys_names():
    """Generalizes the rejected 'se' profile: ANY selector broad enough to claim
    the GMT family ahead of dazn_gmt re-creates the no-op. Name-agnostic, so it
    still bites if someone adds the same greedy pattern under a different key."""
    names = _fixture_names()
    gmt = [n for n in names if "(GMT)" in n]
    assert gmt, "fixture has no GMT names - this test would be vacuous"
    for profile in ecm_profiles.PROFILES:
        if profile.key == "dazn_gmt":
            continue
        rx = ecm_profiles.compile_pattern(profile.selector)
        greedy = [n for n in gmt if rx and rx.search(n)]
        assert not greedy, (
            f"{profile.key}'s selector claims {len(greedy)} GMT names, e.g. {greedy[:2]}. "
            f"Ordered ahead of dazn_gmt, dazn_gmt would route ZERO channels.")


def test_fixture_era_counts_for_the_record():
    """Counts against the COMMITTED FIXTURE (not live data), so lineup churn
    cannot make this flap. If it fails, a selector changed - investigate, do not
    edit the numbers."""
    result = ecm_profiles.route(_fixture_names())
    assert len(result["dazn_gmt"]) == 48
    assert len(result["us_et"]) == 104
    assert len(result[ecm_profiles.UNCLAIMED]) == 126


# --- route() contract ----------------------------------------------------------

def _mk(key, selector, is_default=False):
    return ecm_profiles.Profile(
        key=key, source_name=key, selector=selector, title_pattern=r"(?<title>.*)",
        date_pattern="", time_pattern="", timezone="UTC", output_timezone="UTC",
        program_duration_minutes=60, include_date=False, title_template="{title}",
        upcoming_title_template="", ended_title_template="",
        fallback_title_template="", fallback_description_template="",
        is_default=is_default)


def test_default_profile_is_evaluated_last_regardless_of_declaration_order():
    greedy = _mk("greedy", r".*", is_default=True)
    specific = _mk("specific", r"^SPECIAL")
    result = ecm_profiles.route(["SPECIAL thing"], profiles=(greedy, specific))
    assert result["specific"] == ["SPECIAL thing"]
    assert result["greedy"] == []


def test_non_default_profiles_are_evaluated_in_declaration_order():
    first, second = _mk("first", r"FOO"), _mk("second", r"FOO")
    assert ecm_profiles.route(["FOO"], profiles=(first, second))["first"] == ["FOO"]
    assert ecm_profiles.route(["FOO"], profiles=(second, first))["second"] == ["FOO"]


def test_route_rejects_duplicate_keys():
    with pytest.raises(ValueError, match="duplicate profile keys"):
        ecm_profiles.route(["x"], profiles=(_mk("dup", r"a"), _mk("dup", r"b")))


def test_route_rejects_multiple_defaults():
    with pytest.raises(ValueError, match="more than one default"):
        ecm_profiles.route(["x"], profiles=(_mk("a", r"a", True), _mk("b", r"b", True)))


def test_route_rejects_a_key_colliding_with_the_sentinel():
    with pytest.raises(ValueError, match="sentinel"):
        ecm_profiles.route(["x"], profiles=(_mk(ecm_profiles.UNCLAIMED, r"a"),))


def test_route_with_uncompilable_selector_claims_nothing():
    broken = _mk("broken", r"(?<unclosed")
    default = next(p for p in ecm_profiles.PROFILES if p.is_default)
    result = ecm_profiles.route(["PPV EVENT 01: X"], profiles=(broken, default))
    assert result["broken"] == []
    assert result["us_et"] == ["PPV EVENT 01: X"]


def test_route_without_a_default_leaves_unmatched_names_unclaimed():
    only = next(p for p in ecm_profiles.PROFILES if not p.is_default)
    result = ecm_profiles.route(["nothing matches"], profiles=(only,))
    assert result[ecm_profiles.UNCLAIMED] == ["nothing matches"]


def test_route_on_empty_input_returns_empty_buckets():
    result = ecm_profiles.route([])
    assert all(v == [] for v in result.values())


def test_bucket_order_follows_input_order():
    """Observable by callers - the in-container gate slices [:5]."""
    names = _fixture_names()
    result = ecm_profiles.route(names)
    for bucket in result.values():
        members = set(bucket)
        assert bucket == [n for n in names if n in members]


# --- settings and timezone resolution (pure) ------------------------------------

def test_build_profiles_honours_the_event_timezone_setting():
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Europe/Stockholm"})
    assert next(p for p in built if p.is_default).timezone == "Europe/Stockholm"


def test_build_profiles_never_changes_the_dazn_timezone():
    """UTC is a fact about the provider's data, not a user preference."""
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Europe/Stockholm"})
    assert next(p for p in built if p.key == "dazn_gmt").timezone == "UTC"


@pytest.mark.parametrize("bad", ["", "nonsense", 0, -3, None])
def test_build_profiles_falls_back_on_a_bad_duration(bad):
    built = ecm_profiles.build_profiles({"dummy_epg_event_duration_hours": bad})
    assert next(p for p in built if p.is_default).program_duration_minutes > 0


def test_build_profiles_preserves_dazn_selector_and_patterns():
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Asia/Tokyo"})
    dazn = next(p for p in built if p.key == "dazn_gmt")
    assert dazn.selector == ecm_profiles.DAZN_GMT.selector
    assert dazn.title_pattern == ecm_profiles.DAZN_GMT.title_pattern


def test_resolve_output_timezone_converts_and_labels():
    """THE assertion this plumbing exists for. If the GMT source inherits the ET
    source's config, every DAZN time renders five hours wrong."""
    got = ecm_profiles.resolve_output_timezone("UTC", "America/Chicago")
    assert got["output_timezone"] == "America/Chicago"
    assert "{starttime}" in got["upcoming_title_template"]


def test_resolve_output_timezone_is_not_symmetric():
    """Guards a swapped-argument bug: both parameters are plain strings, so
    transposing them raises nothing and silently renders times wrong."""
    a = ecm_profiles.resolve_output_timezone("UTC", "America/Chicago")
    b = ecm_profiles.resolve_output_timezone("America/Chicago", "UTC")
    assert a != b


def test_resolve_output_timezone_same_zone_uses_plain_templates():
    got = ecm_profiles.resolve_output_timezone("America/Chicago", "America/Chicago")
    assert got["upcoming_title_template"] == "Upcoming at {starttime}: {title}"


@pytest.mark.parametrize("src,sys_tz", [("", "America/Chicago"),
                                        ("Not/AZone", "America/Chicago"),
                                        ("UTC", "Not/AZone")])
def test_resolve_output_timezone_degrades_without_raising(src, sys_tz):
    got = ecm_profiles.resolve_output_timezone(src, sys_tz)
    assert set(got) == {"output_timezone", "title_template",
                        "upcoming_title_template", "ended_title_template"}


# --- a clock time must not be read as a slot number and separator -------------
#
# Measured on the live installation 2026-08-29: four visible channels named
# "Boxing 3 : MOSES vs HRGOVIC  4:00pm" rendered the guide title "00pm", and the
# ended template turned that into "Ended at 8/29 7 PM CDT: 00pm". The slot number
# 3 is followed by text rather than by a date or a time, so the engine skipped it
# and started the match on the air time itself: "4" became the slot number, the
# time's own colon became the separator, and "00pm" became the title.
#
# This is the same failure bug-051 describes, reached through the keyword-less
# branch added in 1.26.2261346. Such a name has no parseable slot, so the correct
# outcome is no match at all, which leaves the channel on the renderer fallback
# exactly as it was before that release.

@pytest.mark.parametrize("name", [
    "Boxing 3 : MOSES vs HRGOVIC  4:00pm",
    "Boxing 1 : MOSES vs HRGOVIC  4:00pm",
    "Fight Night 7 : Smith vs Jones 10:30pm",
    "Wrestling : Main Card 8:15 PM",
])
def test_us_et_title_does_not_start_a_match_inside_a_clock_time(name):
    us = next(p for p in ecm_profiles.PROFILES if p.key == "us_et")
    rx = ecm_profiles.compile_pattern(us.title_pattern)
    m = rx.search(name)
    assert m is None, (
        f"{name!r} matched and produced the title {m.group('title')!r}; the match "
        f"began inside the air time rather than at a slot number")


@pytest.mark.parametrize("name,expected", [
    ("PPV EVENT 07: MARS Late Models at Farmer City (7.17 7:30 PM ET)",
     "MARS Late Models at Farmer City"),
    ("07 - 8/14 7pm Broncos at Falcons", "Broncos at Falcons"),
    ("07 - 7pm Broncos at Falcons", "Broncos at Falcons"),
    ("3 - 9/1 12:30pm Some Team at Another", "Some Team at Another"),
    ("07 | 8/14 7pm Broncos at Falcons", "Broncos at Falcons"),
    ("LIVE EVENT 11 - 8pm Lara v Ornelas", "Lara v Ornelas"),
])
def test_the_clock_time_guard_leaves_real_event_names_alone(name, expected):
    """The guard must not cost any name that parsed correctly before it."""
    us = next(p for p in ecm_profiles.PROFILES if p.key == "us_et")
    rx = ecm_profiles.compile_pattern(us.title_pattern)
    m = rx.search(name)
    assert m, f"no match for {name!r}"
    assert m.group("title") == expected


# --- parse_group_source_map (issue 29, Task 1) --------------------------------
#
# The mapping setting is free text typed by an operator into a Mantine textarea,
# so every shape below has been seen or is one keystroke away from one that has.
# The parser returns (mapping, problems) and NEVER raises: a malformed line must
# not be able to stop a scan.


def _parse(raw):
    return ecm_profiles.parse_group_source_map(raw)


def test_a_plain_mapping_line_is_accepted():
    mapping, problems = _parse("NFL Sunday Ticket = ECM - NFL")
    assert mapping == {"nfl sunday ticket": "ECM - NFL"}
    assert problems == []


def test_the_group_key_is_casefolded_and_the_source_name_is_kept_verbatim():
    """Group names are matched case-insensitively; a source name is an identity.

    EPGSource.name is unique and case-SENSITIVE in Postgres, so the source side
    must survive exactly as typed or the plugin would create a differently cased
    duplicate row.
    """
    mapping, problems = _parse("  NFL Sunday TICKET  =   ECM - NFL  ")
    assert mapping == {"nfl sunday ticket": "ECM - NFL"}
    assert problems == []


def test_carriage_returns_are_stripped():
    """The value is whatever the browser posts, so CRLF reaches the parser."""
    mapping, problems = _parse("NFL = ECM - NFL\r\nNCAAF = ECM - NCAAF\r\n")
    assert mapping == {"nfl": "ECM - NFL", "ncaaf": "ECM - NCAAF"}
    assert problems == []


def test_blank_lines_and_comments_are_ignored_silently():
    mapping, problems = _parse("\n# a comment\nNFL = ECM - NFL\n   \n#another\n")
    assert mapping == {"nfl": "ECM - NFL"}
    assert problems == [], "a comment or a blank line is not a problem to report"


@pytest.mark.parametrize("raw", ["", "   ", "\n\n", None])
def test_an_empty_setting_is_silent(raw):
    """This is the default state of the setting on every existing installation.

    It must produce no mapping and, importantly, no problem: a problem string
    suppresses the reverse move for the whole run (plan Decision 3), so a noisy
    parser on an unused setting would disable a feature nobody enabled.
    """
    mapping, problems = _parse(raw)
    assert mapping == {}
    assert problems == []


def test_a_line_with_no_equals_sign_is_rejected_and_named():
    mapping, problems = _parse("NFL Sunday Ticket ECM - NFL")
    assert mapping == {}
    assert len(problems) == 1
    assert "NFL Sunday Ticket ECM - NFL" in problems[0]


@pytest.mark.parametrize("raw", ["= ECM - NFL", "NFL =", "  =  "])
def test_an_empty_side_is_rejected(raw):
    mapping, problems = _parse(raw)
    assert mapping == {}
    assert len(problems) == 1


def test_a_duplicate_group_keeps_the_first_and_reports_the_second():
    mapping, problems = _parse("NFL = ECM - A\nnfl = ECM - B")
    assert mapping == {"nfl": "ECM - A"}, "the first mapping for a group wins"
    assert len(problems) == 1
    assert "ECM - B" in problems[0] or "nfl" in problems[0].casefold()


def test_two_groups_may_share_one_source():
    """A deliberate configuration, not an error: two groups, one set of settings."""
    mapping, problems = _parse("NFL = ECM - Football\nNCAAF = ECM - Football")
    assert mapping == {"nfl": "ECM - Football", "ncaaf": "ECM - Football"}
    assert problems == []


def test_two_source_names_differing_only_in_case_are_rejected():
    """EPGSource.name is unique and case-sensitive, so both would be created.

    Accepting these produces two sources the operator believes are one, each
    holding half the channels and each needing its properties edited separately.
    """
    mapping, problems = _parse("NFL = ECM - NFL\nNCAAF = ecm - nfl")
    assert len(problems) == 1
    assert list(mapping.values()) == ["ECM - NFL"], (
        "the first spelling wins and the colliding one is dropped")


@pytest.mark.parametrize("reserved", [
    "ECM Managed Dummy", "ecm managed dummy", "DAZN PPV Dummy (GMT)"])
def test_a_reserved_source_name_is_rejected(reserved):
    """The shared source and the code-owned source cannot be group targets.

    Seeding and then abandoning the shared source would strand every group that
    is NOT mapped, since that source is where they all live.
    """
    mapping, problems = _parse(f"NFL = {reserved}")
    assert mapping == {}
    assert len(problems) == 1
    assert reserved.split()[0].casefold() in problems[0].casefold()


def test_reserved_names_are_declared_as_a_constant():
    """Both the parser and the validation action must read one list."""
    assert ecm_profiles.RESERVED_SOURCE_NAMES
    lowered = {n.casefold() for n in ecm_profiles.RESERVED_SOURCE_NAMES}
    assert "ecm managed dummy" in lowered
    assert "dazn ppv dummy (gmt)" in lowered


def test_the_parser_never_raises_on_hostile_input():
    """A scan must not be stoppable by a typo in a settings box."""
    hostile = "=\n==\n\x00\nNFL = = ECM\n" + ("x" * 5000) + " = y\n__unclaimed__ = z"
    mapping, problems = _parse(hostile)
    assert isinstance(mapping, dict)
    assert isinstance(problems, list)


def test_the_first_equals_sign_separates_and_later_ones_stay_in_the_name():
    """A source name may legitimately contain an equals sign."""
    mapping, _ = _parse("NFL = ECM = NFL")
    assert mapping == {"nfl": "ECM = NFL"}


def test_mapping_order_is_preserved():
    """Routing evaluates group profiles in declaration order, so order matters."""
    mapping, _ = _parse("B = ECM - B\nA = ECM - A\nC = ECM - C")
    assert list(mapping) == ["b", "a", "c"]


# --- build_group_profiles (issue 29, Task 2) ----------------------------------


def _build(raw, **settings):
    settings.setdefault("group_epg_source_map", raw)
    return ecm_profiles.build_group_profiles(settings)


def test_profile_props_returns_exactly_the_stored_property_keys():
    """Pin the key set BEFORE new dataclass fields exist.

    profile_props builds an EPGSource.custom_properties payload. A new field on
    the Profile dataclass must not leak into a source's stored properties, and a
    leaked key would be written to the live database rather than caught by a
    type error.
    """
    props = ecm_profiles.profile_props(ecm_profiles.US_ET)
    assert set(props) == {
        "timezone", "output_timezone", "title_pattern", "date_pattern",
        "time_pattern", "title_template", "upcoming_title_template",
        "ended_title_template", "program_duration", "include_date",
        "fallback_title_template", "fallback_description_template",
    }


def test_the_new_profile_fields_default_so_existing_constructions_keep_working():
    assert ecm_profiles.US_ET.user_managed is False
    assert ecm_profiles.US_ET.group_names == ()
    assert ecm_profiles.DAZN_GMT.user_managed is False


def test_an_empty_mapping_builds_no_group_profiles():
    profiles, problems = _build("")
    assert profiles == ()
    assert problems == []


def test_one_mapping_builds_one_user_managed_profile():
    profiles, problems = _build("NFL Sunday Ticket = ECM - NFL")
    assert problems == []
    assert len(profiles) == 1
    p = profiles[0]
    assert p.source_name == "ECM - NFL"
    assert p.group_names == ("nfl sunday ticket",)
    assert p.user_managed is True
    assert p.is_default is False


def test_a_group_profile_cannot_claim_a_channel_by_name():
    """Its selector must compile to None, which route() and claimed_targets skip.

    Asserted as behaviour rather than by reading the selector string, because it
    is the skip that matters, not the empty value that causes it.
    """
    profiles, _ = _build("NFL = ECM - NFL")
    assert ecm_profiles.compile_pattern(profiles[0].selector) is None
    claims = ecm_profiles.claimed_targets(
        ["NFL Sunday Ticket week 1", "anything at all"], profiles)
    assert claims == {}


def test_two_groups_sharing_a_source_build_one_profile_with_both_groups():
    profiles, problems = _build("NFL = ECM - Football\nNCAAF = ECM - Football")
    assert problems == []
    assert len(profiles) == 1, "one source name is one profile, not one per group"
    assert set(profiles[0].group_names) == {"nfl", "ncaaf"}


def test_group_profile_keys_are_unique_and_do_not_collide_with_the_code_profiles():
    profiles, _ = _build("A = ECM - A\nB = ECM - B\nC = ECM - C")
    keys = [p.key for p in profiles]
    assert len(keys) == len(set(keys))
    assert not set(keys) & {"us_et", "dazn_gmt", ecm_profiles.UNCLAIMED}


def test_a_source_named_like_the_unclaimed_sentinel_does_not_collide():
    """route() raises if a profile key equals the sentinel, which is an outage."""
    profiles, _ = _build(f"NFL = {ecm_profiles.UNCLAIMED}")
    assert profiles
    assert profiles[0].key != ecm_profiles.UNCLAIMED


@pytest.mark.parametrize("raw", [
    "NFL = __unclaimed__",
    "A = ECM - A\nB = ecm - a",
    "= =\nNFL = ECM - NFL",
    "NFL = us_et",
    "NFL = dazn_gmt",
])
def test_route_never_raises_on_a_profile_set_built_from_a_hostile_mapping(raw):
    """route() raises ValueError on duplicate keys or a sentinel collision.

    An unhandled raise here is an outage on the scan path, not a fail-safe, so
    the builder must never hand route() a set it will reject.
    """
    group_profiles, _ = _build(raw)
    combined = tuple(group_profiles) + ecm_profiles.build_profiles({})
    buckets = ecm_profiles.route(["some channel name"], combined)
    assert ecm_profiles.UNCLAIMED in buckets


def test_a_group_profile_inherits_the_global_timezone_and_duration():
    profiles, _ = _build(
        "NFL = ECM - NFL",
        dummy_epg_event_timezone="America/Denver",
        dummy_epg_event_duration_hours=2)
    assert profiles[0].timezone == "America/Denver"
    assert profiles[0].program_duration_minutes == 120


def test_parser_problems_are_returned_by_the_builder():
    """The caller gates the reverse move on this list, so it must not be dropped."""
    profiles, problems = _build("this line has no equals sign")
    assert profiles == ()
    assert len(problems) == 1


def test_a_missing_setting_key_is_treated_as_no_mapping():
    profiles, problems = ecm_profiles.build_group_profiles({})
    assert profiles == ()
    assert problems == []
