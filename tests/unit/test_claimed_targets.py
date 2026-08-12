"""Positive-claim semantics.

claimed_targets returns ONLY names a non-default selector positively claims.
Everything else is ABSENT, and absence is the safety property: the reroute step
can act only on names present here, so unclaimed and default-family channels
cannot be moved at all.

NOTE: a claim is necessary but NOT sufficient to move a channel -- the caller must
also check the binding is safe to move (see _epg_binding_is_reroutable, Task 3).
A name claim says "this belongs to profile X"; it says nothing about whether the
channel currently holds a real, populated EPG that must not be destroyed.
"""

from pathlib import Path

import pytest

import ecm_profiles

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"


def _names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _profiles():
    return ecm_profiles.build_profiles({})


def test_gmt_names_are_claimed_by_dazn():
    names = _names()
    claims = ecm_profiles.claimed_targets(names, _profiles())
    assert all(claims.get(n) == "dazn_gmt" for n in names if "(GMT)" in n)


def test_unclaimed_names_are_absent_not_defaulted():
    idle = "NO EVENT STREAMING NOW - | 8K EXCLUSIVE | US: DAZN PPV 50"
    assert idle not in ecm_profiles.claimed_targets([idle], _profiles())


def test_default_family_names_are_absent():
    legacy = "PPV EVENT 07: MARS Late Models at Farmer City (7.17 7:30 PM ET)"
    assert legacy not in ecm_profiles.claimed_targets([legacy], _profiles())


def test_no_default_key_ever_appears_as_a_value():
    claims = ecm_profiles.claimed_targets(_names(), _profiles())
    assert next(p.key for p in _profiles() if p.is_default) not in set(claims.values())


def test_claim_count_on_the_real_corpus():
    """48 of 278 in the COMMITTED FIXTURE -- the maximum blast radius before the
    safety guard narrows it further.

    Note 48 (fixture) vs ~46 (live, measured later the same day): the provider
    renames these slots in place, so two had cycled to idle names between the
    fixture capture and the live measurement. This test is fixture-scoped and must
    stay 48; a LIVE count belongs in the in-container gate, never here."""
    claims = ecm_profiles.claimed_targets(_names(), _profiles())
    assert len(claims) == 48
    assert set(claims.values()) == {"dazn_gmt"}
    # Ground truth is the name itself, not the arithmetic.
    assert set(claims) == {n for n in _names() if "(GMT)" in n}


def test_empty_input_and_default_only_profiles_yield_no_claims():
    assert ecm_profiles.claimed_targets([], _profiles()) == {}
    only_default = tuple(p for p in _profiles() if p.is_default)
    assert ecm_profiles.claimed_targets(_names(), only_default) == {}


def test_a_broken_selector_claims_nothing_rather_than_raising():
    broken = ecm_profiles.Profile(
        key="broken", source_name="B", selector=r"(?<unclosed",
        title_pattern="", date_pattern="", time_pattern="", timezone="UTC",
        output_timezone="UTC", program_duration_minutes=60, include_date=False,
        title_template="{title}", upcoming_title_template="", ended_title_template="",
        fallback_title_template="", fallback_description_template="", is_default=False)
    default = next(p for p in _profiles() if p.is_default)
    assert ecm_profiles.claimed_targets(["anything"], (broken, default)) == {}
