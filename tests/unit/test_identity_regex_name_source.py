"""The three identity regex settings read the channel's own name, always.

Reported on pull request 31 by ferteque (2026-09-19): with Name Source set to
Stream Name, Regex: Channel Names to Ignore, Regex: Mark Channel as Inactive and
Regex: Force Visible Channels stopped matching the channels they were written for.

Those three settings identify or protect a particular channel rather than read an
event out of its name. The operator types them while looking at the Dispatcharr
channel list, and the stream bound to a channel can change on every M3U refresh, so
the text they are matched against must not follow the Name Source setting. Every
other rule reads the Name Source text, which is what that setting is for.
"""

import ecm_parsing
import pytest


class _NullLogger:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


class _FakeChannel:
    """Only the attributes the identity decision reads."""

    def __init__(self, name):
        self.id = 1
        self.name = name


# --- the decision, on its own ------------------------------------------------------

IDENTITY_SETTINGS = ["regex_channels_to_ignore", "regex_mark_inactive", "regex_force_visible"]


@pytest.mark.parametrize("setting_id", IDENTITY_SETTINGS)
def test_identity_settings_read_the_channel_name(setting_id):
    assert ecm_parsing.regex_target_name(
        setting_id, "US: PPV 04 CANCELLED", "Sky Sports Main Event HD") == "US: PPV 04 CANCELLED"


@pytest.mark.parametrize("setting_id", ["regex_ignore_epg", "dummy_epg_title_pattern", ""])
def test_every_other_setting_reads_the_name_source_text(setting_id):
    assert ecm_parsing.regex_target_name(
        setting_id, "US: PPV 04 CANCELLED", "Sky Sports Main Event HD") == "Sky Sports Main Event HD"


def test_the_three_identity_settings_are_named_exactly():
    assert sorted(ecm_parsing.IDENTITY_REGEX_SETTINGS) == sorted(IDENTITY_SETTINGS)


def test_a_missing_channel_name_is_an_empty_string_not_none():
    assert ecm_parsing.regex_target_name("regex_mark_inactive", None, "Stream") == ""


# --- [InactiveRegex] reads the channel name, not the Name Source text --------------

def _inactive(bare_plugin, pattern, channel_name, effective_name=None):
    hidden, _reason = bare_plugin._check_hide_rule(
        "InactiveRegex", None, _FakeChannel(channel_name),
        channel_name if effective_name is None else effective_name,
        _NullLogger(), {"regex_mark_inactive": pattern})
    return hidden


def test_inactive_regex_matches_the_channel_name_under_stream_name_source(bare_plugin):
    """The reported symptom: the channel name matches, the stream name does not."""
    assert _inactive(bare_plugin, "CANCELL?ED",
                     "LIVE EVENT 08 - CANCELLED 6:15pm Cedar Lake Late Models",
                     effective_name="Sky Sports Main Event HD")


def test_inactive_regex_ignores_a_match_that_is_only_in_the_stream_name(bare_plugin):
    """The negative control: a pattern written for channel names must not start
    hiding channels because some stream happens to carry the word."""
    assert not _inactive(bare_plugin, "CANCELL?ED",
                         "LIVE EVENT 11 - 7pm Williams Grove Sprints",
                         effective_name="FEED 12 CANCELLED BACKUP")
