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
