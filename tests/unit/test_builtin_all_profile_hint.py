"""builtin_all_profile_hint: explain why a profile named "All" cannot work.

Dispatcharr's "All" entry in the Channel Profile picker is a synthetic view of every
channel (frontend id '0'), not a ChannelProfile row. The plugin hides channels by
setting ChannelProfileMembership.enabled in a real profile, so "All" can never match.
"""

import ecm_parsing
import pytest
from ecm_parsing import builtin_all_profile_hint

HINT_TEXT = (
    "'All' is Dispatcharr's built-in view of every channel, not a Channel Profile, "
    "so this plugin cannot hide channels in it. Create a Channel Profile, add your "
    "channels to it, and enter that profile's name here."
)


@pytest.mark.parametrize("names", [
    ["All"],
    ["  all "],
    ["ALL CHANNELS"],
    ["all profiles"],
    ["Sports", "All"],
])
def test_builtin_all_name_returns_the_hint(names):
    assert builtin_all_profile_hint(names) == HINT_TEXT


@pytest.mark.parametrize("names", [
    ["Sports"],
    [],
    None,
])
def test_other_names_return_empty_string(names):
    assert builtin_all_profile_hint(names) == ""


def test_hint_text_is_exact():
    assert builtin_all_profile_hint(["All"]) == HINT_TEXT


def test_hint_is_module_level_function_on_ecm_parsing():
    assert ecm_parsing.builtin_all_profile_hint is builtin_all_profile_hint
