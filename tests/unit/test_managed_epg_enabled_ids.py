"""Unit tests for ecm_profiles.managed_epg_enabled_ids.

This function decides which channels count as "visible once this scan's
decisions are applied". That set is what the managed dummy EPG pass attaches
to, and it is also the keep-set the detach step protects, so a channel missing
from it gets no guide data AND has any managed EPG stripped on every run.

The defect that motivated these tests (bug-175, measured on the live
installation 2026-09-05): a channel matched by the Regex: Force Visible
Channels setting left the per-channel loop through an early branch that never
recorded it, so 17 visible channels carried no EPG at all and the run reported
"Managed EPG Attached: 0".
"""

import ecm_profiles  # resolves via pyproject.toml pythonpath
import pytest

enabled_ids = ecm_profiles.managed_epg_enabled_ids


def test_visible_channel_left_alone_stays_enabled():
    assert enabled_ids([(1, True)], [], [], [], []) == [1]


def test_hidden_channel_left_alone_is_not_enabled():
    assert enabled_ids([(1, False)], [], [], [], []) == []


def test_channel_being_hidden_this_scan_is_not_enabled():
    assert enabled_ids([(1, True)], [], [1], [], []) == []


def test_channel_being_shown_this_scan_is_enabled():
    assert enabled_ids([(1, False)], [], [], [1], []) == [1]


def test_duplicate_hidden_channel_is_not_enabled():
    assert enabled_ids([(1, True)], [], [], [], [1]) == []


def test_forced_visible_channel_is_enabled_even_though_it_was_never_evaluated():
    """The defect. A forced-visible channel never reaches the evaluated list."""
    assert enabled_ids([], [7], [], [], []) == [7]


def test_forced_visible_channel_already_visible_is_enabled():
    assert enabled_ids([], [7], [], [], []) == [7]


def test_forced_visible_channel_currently_hidden_is_enabled():
    """It is in the show list too, but the forced set alone must be enough."""
    assert enabled_ids([], [7], [], [7], []) == [7]


def test_forced_visible_beats_the_hide_list():
    """Force Visible means force visible. Nothing later may take it back."""
    assert enabled_ids([], [7], [7], [], []) == [7]


def test_forced_visible_beats_the_duplicate_hide_list():
    assert enabled_ids([], [7], [], [], [7]) == [7]


def test_result_has_no_duplicates_when_a_channel_appears_in_both_inputs():
    assert enabled_ids([(7, True)], [7], [], [], []) == [7]


def test_order_is_stable_evaluated_first_then_forced():
    result = enabled_ids([(3, True), (1, True)], [9, 2], [], [], [])
    assert result == [3, 1, 9, 2]


def test_accepts_sets_for_the_membership_arguments():
    """The caller passes lists today; a set must not change the answer."""
    assert enabled_ids([(1, True), (2, True)], set(), {2}, set(), set()) == [1]


def test_a_realistic_mixed_scan():
    evaluated = [
        (10, True),    # visible, nothing decided -> stays
        (11, True),    # visible, being hidden
        (12, False),   # hidden, being shown
        (13, False),   # hidden, stays hidden
        (14, True),    # visible, hidden as a duplicate
    ]
    forced = [20, 21]
    assert enabled_ids(evaluated, forced, [11], [12], [14]) == [10, 12, 20, 21]


def test_returns_a_list_not_a_generator():
    result = enabled_ids([(1, True)], [], [], [], [])
    assert isinstance(result, list)


def test_empty_scan_returns_empty_list():
    assert enabled_ids([], [], [], [], []) == []


@pytest.mark.parametrize("bad", [None, 0, ""])
def test_none_and_empty_membership_arguments_are_treated_as_empty(bad):
    assert enabled_ids([(1, True)], bad, bad, bad, bad) == [1]
