"""Stream Name Groups: named channel groups read the stream name, everything else does not.

Measured 2026-10-09 on the live box: group US: NFL keeps static channel names while the
provider rewrites the weekly slate on the STREAM names, so the hide rules judged last
week's games and the operator had to rename 63 channels by hand each week. The global
Name Source setting could not be switched to Stream Name because US: PPV, scanned in
the same run, has channel names that run AHEAD of their streams (22 of 146 differ, the
streams still reading NO EVENT). The new setting names the groups that read the stream
name, and a per-group dummy EPG source created for such a group is seeded to parse the
stream name too, which Dispatcharr 0.32.0 supports through custom_properties
name_source 'stream'.
"""

import logging

import ecm_profiles
import pytest

# --- the list parser -----------------------------------------------------------------


def test_blank_means_no_group():
    assert ecm_profiles.stream_name_group_keys("") == frozenset()
    assert ecm_profiles.stream_name_group_keys(None) == frozenset()


def test_comma_separated_trimmed_and_casefolded():
    assert ecm_profiles.stream_name_group_keys(" US: NFL ,  nfl sunday ticket,,") == \
        frozenset({"us: nfl", "nfl sunday ticket"})


def test_a_value_that_is_not_text_is_read_as_text():
    assert ecm_profiles.stream_name_group_keys(123) == frozenset({"123"})


# --- the per-channel decision --------------------------------------------------------

KEYS = frozenset({"us: nfl"})


@pytest.mark.parametrize("group_name", ["US: NFL", "us: nfl", "  US: NFL  "])
def test_a_listed_group_reads_the_stream_name(group_name):
    assert ecm_profiles.name_source_for_group("Channel_Name", group_name, KEYS) == "Stream_Name"


def test_an_unlisted_group_keeps_the_global_setting():
    assert ecm_profiles.name_source_for_group("Channel_Name", "US: PPV", KEYS) == "Channel_Name"
    assert ecm_profiles.name_source_for_group("Stream_Name", "US: PPV", KEYS) == "Stream_Name"


def test_a_channel_with_no_group_keeps_the_global_setting():
    assert ecm_profiles.name_source_for_group("Channel_Name", None, KEYS) == "Channel_Name"


def test_an_empty_list_changes_nothing():
    assert ecm_profiles.name_source_for_group("Channel_Name", "US: NFL", frozenset()) == "Channel_Name"


# --- the per-group source is seeded to parse stream names ----------------------------

def _profiles(**settings):
    profiles, problems = ecm_profiles.build_group_profiles(settings)
    assert problems == []
    return {p.source_name: p for p in profiles}


def test_a_mapped_source_for_a_listed_group_parses_the_stream_name():
    profiles = _profiles(group_epg_source_map="US: NFL = ECM NFL Dummy\nUS: PPV = ECM PPV",
                         stream_name_groups="us: nfl")
    nfl = ecm_profiles.profile_props(profiles["ECM NFL Dummy"])
    assert nfl["name_source"] == "stream"
    assert nfl["stream_index"] == 1


def test_a_mapped_source_for_an_unlisted_group_carries_no_name_source_key():
    # Writing name_source 'channel' everywhere would be harmless to the renderer but
    # would add a key to every source the plugin rewrites on each run.
    profiles = _profiles(group_epg_source_map="US: PPV = ECM PPV",
                         stream_name_groups="US: NFL")
    props = ecm_profiles.profile_props(profiles["ECM PPV"])
    assert "name_source" not in props and "stream_index" not in props


def test_the_profiles_defined_in_code_carry_no_name_source_key():
    for profile in ecm_profiles.build_profiles({"stream_name_groups": "US: NFL"}):
        assert "name_source" not in ecm_profiles.profile_props(profile)


def test_a_source_shared_by_a_listed_and_an_unlisted_group_parses_channel_names():
    # One source has ONE name_source. Reading stream names for a group that was not
    # asked for is the failure that kept the global setting on Channel Name, so a
    # mixed source stays on channel names and Validate Configuration says why.
    profiles = _profiles(group_epg_source_map="US: NFL = Shared\nUS: PPV = Shared",
                         stream_name_groups="US: NFL")
    assert "name_source" not in ecm_profiles.profile_props(profiles["Shared"])
    assert ecm_profiles.mixed_name_source_problems(
        {"group_epg_source_map": "US: NFL = Shared\nUS: PPV = Shared",
         "stream_name_groups": "US: NFL"}) != []


def test_no_mixed_source_problem_when_every_group_on_a_source_is_listed():
    assert ecm_profiles.mixed_name_source_problems(
        {"group_epg_source_map": "US: NFL = NFL\nNFL Extra = NFL",
         "stream_name_groups": "US: NFL, NFL Extra"}) == []


# --- the plugin asks the decision for each channel ----------------------------------

class _Streams:
    def __init__(self, names):
        self._names = names

    def order_by(self, *_a):
        return self

    def exists(self):
        return bool(self._names)

    def first(self):
        return type("S", (), {"name": self._names[0]})() if self._names else None


class _Channel:
    def __init__(self, name, group, streams):
        self.id = 1
        self.name = name
        self.channel_group = type("G", (), {"name": group})() if group else None
        self.streams = _Streams(streams)


LOG = logging.getLogger("t")
NFL = _Channel("NFL  | 10 - 4:05pm Jaguars at Broncos", "US: NFL", ["NFL  | 10 - 1pm Giants at Commanders"])
PPV = _Channel("PPV EVENT 01: GCW Fight Club (10.9 6:00 PM ET)", "US: PPV", ["PPV EVENT 01"])


def test_a_listed_group_reads_its_stream_while_the_global_setting_is_channel_name(bare_plugin):
    settings = {"name_source": "Channel_Name", "stream_name_groups": "US: NFL"}
    assert bare_plugin._get_effective_name(NFL, settings, LOG) == "NFL  | 10 - 1pm Giants at Commanders"
    assert bare_plugin._get_effective_name(PPV, settings, LOG) == PPV.name


def test_without_the_setting_nothing_changes(bare_plugin):
    settings = {"name_source": "Channel_Name"}
    assert bare_plugin._get_effective_name(NFL, settings, LOG) == NFL.name


def test_the_global_stream_name_setting_still_applies_to_every_group(bare_plugin):
    settings = {"name_source": "Stream_Name", "stream_name_groups": ""}
    assert bare_plugin._get_effective_name(PPV, settings, LOG) == "PPV EVENT 01"


def test_the_payload_written_when_the_plugin_creates_the_source(bare_plugin, monkeypatch):
    """What _ensure_profile_source passes as the new source's custom_properties."""
    monkeypatch.setattr(bare_plugin, "_get_system_timezone",
                        lambda settings: "America/Chicago", raising=False)
    settings = {"group_epg_source_map": "US: NFL = ECM NFL Dummy",
                "stream_name_groups": "US: NFL",
                "dummy_epg_event_timezone": "America/New_York"}
    (profile,), _problems = ecm_profiles.build_group_profiles(settings)
    props = bare_plugin._managed_props_for_profile(profile, settings)
    assert props["name_source"] == "stream" and props["stream_index"] == 1
    # Everything else is seeded as before.
    assert props["timezone"] == "America/New_York"
    assert props["output_timezone"] == "America/Chicago"
