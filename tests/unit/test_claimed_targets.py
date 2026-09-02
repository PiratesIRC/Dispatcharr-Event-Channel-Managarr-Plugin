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


# --- routing_destinations (issue 29, Task 3) ----------------------------------
#
# ONE function decides where every in-scope channel belongs, and a move is then
# simply "desired is not current", in either direction. An earlier revision of the
# plan computed the forward destination in one place and the reverse eligibility
# in another, with nothing holding the two consistent -- the same defect class as
# the channel title pattern that is written twice in this repository and needed a
# dedicated parity test to stop the copies drifting.

SHARED = "ECM Managed Dummy"


def _binding(cid, name="", group=None, source=None, plugin_created=False):
    return ecm_profiles.ChannelBinding(
        id=cid, name=name, group_name=group, source_name=source,
        source_is_plugin_created=plugin_created)


def _destinations(bindings, mapping_raw="", mapping_is_clean=True, settings=None):
    settings = dict(settings or {})
    settings["group_epg_source_map"] = mapping_raw
    group_profiles, problems = ecm_profiles.build_group_profiles(settings)
    return ecm_profiles.routing_destinations(
        bindings, group_profiles, ecm_profiles.build_profiles(settings),
        SHARED, mapping_is_clean and not problems)


def test_a_channel_whose_group_maps_goes_to_that_source():
    out = _destinations([_binding(1, group="NFL Sunday Ticket")],
                        "NFL Sunday Ticket = ECM - NFL")
    assert out == {1: "ECM - NFL"}


def test_the_group_comparison_is_case_insensitive_and_edge_stripped():
    out = _destinations([_binding(1, group="  nfl SUNDAY ticket  ")],
                        "NFL Sunday Ticket = ECM - NFL")
    assert out == {1: "ECM - NFL"}


def test_a_channel_already_on_its_destination_is_absent():
    """Absence means no write. A channel reported here is a database write."""
    out = _destinations(
        [_binding(1, group="NFL", source="ECM - NFL", plugin_created=True)],
        "NFL = ECM - NFL")
    assert out == {}


def test_a_group_mapping_beats_a_channel_name_selector():
    """Plan Decision 2: the operator typed the mapping; the selector is a default."""
    dazn_name_channel = _binding(
        1, name="Next | Fight Night | 2026-09-02 | 20:00 (GMT)", group="NFL")
    out = ecm_profiles.routing_destinations(
        [dazn_name_channel],
        ecm_profiles.build_group_profiles({"group_epg_source_map": "NFL = ECM - NFL"})[0],
        ecm_profiles.build_profiles({}), SHARED, True)
    assert out == {1: "ECM - NFL"}


def test_the_precedence_does_not_depend_on_the_order_the_profiles_are_passed():
    """route()'s own docstring records that an ordering invariant resting on list
    order shipped as a silent no-op twice. Reversing the input must change nothing."""
    groups, _ = ecm_profiles.build_group_profiles({"group_epg_source_map": "NFL = ECM - NFL"})
    code = ecm_profiles.build_profiles({})
    forward = ecm_profiles.routing_destinations(
        [_binding(1, group="NFL")], groups, code, SHARED, True)
    reversed_ = ecm_profiles.routing_destinations(
        [_binding(1, group="NFL")], tuple(reversed(groups)), tuple(reversed(code)),
        SHARED, True)
    assert forward == reversed_ == {1: "ECM - NFL"}


def test_an_unmapped_group_on_a_plugin_created_source_returns_to_the_shared_source():
    """This is the reverse move: the operator removed or changed the mapping."""
    out = _destinations(
        [_binding(1, group="NFL", source="ECM - NFL", plugin_created=True)], "")
    assert out == {1: SHARED}


def test_a_channel_on_a_source_the_plugin_did_not_create_is_never_moved():
    """Plan Decision 4, and the reason it exists.

    Measured on the live installation: three hand-made dummy sources hold five
    channels between them. A group mapping claims every channel in its group
    unconditionally, and the existing reroutability guard returns True for ANY
    dummy source with no ownership check, so without this rule those channels
    would be taken and nothing would record where they came from.
    """
    out = _destinations(
        [_binding(1, group="NFL", source="Dummy - No Guide", plugin_created=False)], "")
    assert out == {}


def test_a_parse_problem_suppresses_every_reverse_move_but_not_forward_moves():
    """Plan Decision 3.

    "Does not map anywhere" is what a typo produces, so triggering the reverse
    move on it would let one malformed line rebind a whole group and shift its
    rendered guide times by hours.
    """
    bindings = [
        _binding(1, group="NCAAF", source="ECM - NCAAF", plugin_created=True),
        _binding(2, group="NFL"),
    ]
    out = _destinations(bindings, "NFL = ECM - NFL\nthis line is malformed")
    assert 1 not in out, "no reverse move while the mapping has a problem"
    assert out == {2: "ECM - NFL"}, "a line that parsed still routes forward"


def test_a_channel_with_no_group_is_absent():
    assert _destinations([_binding(1, group=None)], "NFL = ECM - NFL") == {}
    assert _destinations([_binding(1, group="")], "NFL = ECM - NFL") == {}


def test_a_channel_with_no_group_on_a_plugin_source_still_returns():
    """Its group was deleted or cleared; it must not be stranded."""
    out = _destinations(
        [_binding(1, group=None, source="ECM - NFL", plugin_created=True)], "")
    assert out == {1: SHARED}


def test_no_reverse_move_when_the_shared_source_name_is_unknown():
    """On a fresh installation the shared source may not exist yet. Moving a
    channel to a source that is not there would unbind it."""
    out = ecm_profiles.routing_destinations(
        [_binding(1, group="NFL", source="ECM - NFL", plugin_created=True)],
        (), ecm_profiles.build_profiles({}), None, True)
    assert out == {}


def test_a_changed_mapping_moves_forward_only_and_never_back_in_the_same_pass():
    """The move must not undo itself. An earlier design read the channel's source
    twice and could move it forward then straight back, writing twice per channel
    on every run for ever."""
    out = _destinations(
        [_binding(1, group="NFL", source="ECM - Old", plugin_created=True)],
        "NFL = ECM - New")
    assert out == {1: "ECM - New"}


# --- the regression guard -----------------------------------------------------


def test_with_no_mapping_the_name_routing_is_unchanged():
    """With the setting empty, routing must behave exactly as it does today.

    Asserted against the pinned ground truth from test_claim_count_on_the_real_corpus
    as well as against the old function, because equality ALONE is vacuous: if the
    fixture path broke, both sides would come back empty and it would still pass.

    The two functions are keyed differently, one by name and one by channel id, and
    the fixture contains names that recur, so the comparison is built explicitly
    rather than by comparing the two dictionaries.
    """
    names = _names()

    old = ecm_profiles.claimed_targets(names, _profiles())
    assert len(old) == 48, "the pinned corpus claim count moved; fixture changed?"
    assert set(old.values()) == {"dazn_gmt"}

    dazn_source = next(p.source_name for p in _profiles() if p.key == "dazn_gmt")
    new = ecm_profiles.routing_destinations(
        [_binding(i, name=n) for i, n in enumerate(names)],
        (), _profiles(), SHARED, True)

    expected_ids = {i for i, n in enumerate(names) if old.get(n) == "dazn_gmt"}
    assert set(new) == expected_ids
    assert set(new.values()) == {dazn_source}
    assert len(new) == 48
