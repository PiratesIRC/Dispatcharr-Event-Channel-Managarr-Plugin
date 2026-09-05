"""Unit tests for ecm_profiles.managed_epg_detach_scope.

The managed dummy EPG pass is told which channels it may take the managed
source OFF. That scope exists so that narrowing the Channel Groups setting
cannot strip the managed source from channels in groups the run did not look
at (bug-045).

Channels matched by the Regex: Channels to Ignore setting were inside that
scope. The plugin skips them for every visibility decision and never attaches
the managed source to them, but it would still take the managed source away
from one, which is the opposite of ignoring it. Same shape as the
force-visible defect (bug-175), and the safe direction here is to leave an
ignored channel alone in both directions.
"""

import ecm_profiles  # resolves via pyproject.toml pythonpath

detach_scope = ecm_profiles.managed_epg_detach_scope


def test_a_scanned_channel_is_in_scope():
    assert detach_scope([1, 2, 3], []) == [1, 2, 3]


def test_an_ignored_channel_is_removed_from_scope():
    assert detach_scope([1, 2, 3], [2]) == [1, 3]


def test_every_channel_ignored_leaves_an_empty_scope():
    assert detach_scope([1, 2], [1, 2]) == []


def test_an_ignored_id_that_was_never_scanned_changes_nothing():
    assert detach_scope([1, 2], [99]) == [1, 2]


def test_order_is_preserved():
    assert detach_scope([5, 1, 4, 2], [4]) == [5, 1, 2]


def test_accepts_a_set_of_ignored_ids():
    assert detach_scope([1, 2, 3], {1, 3}) == [2]


def test_none_ignored_argument_is_treated_as_empty():
    assert detach_scope([1, 2], None) == [1, 2]


def test_empty_scan_returns_empty_list():
    assert detach_scope([], [1]) == []


def test_returns_a_list_not_a_generator():
    assert isinstance(detach_scope([1], []), list)


def test_the_result_is_a_new_list_not_the_input():
    scanned = [1, 2]
    result = detach_scope(scanned, [])
    assert result == scanned
    assert result is not scanned
