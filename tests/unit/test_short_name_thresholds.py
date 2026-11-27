"""The two "name is too short" cutoffs are settings, not constants.

[ShortDescription] measured the text after a separator against a bare 15 written
into the rule body, and [ShortChannelName] measured a separator-less name against
a bare 25. The hide-rule parser has always accepted a number after a tag and
handed it to the rule, so a user could write [ShortDescription:20], see it parse
without complaint, and silently get 15 anyway.

The visible effect of a fixed cutoff, from a real report: on one provider's
channels "NCAAF 25: FS1 [1080p]" was hidden and "NCAAF 26: SEC NETWORK [1080p]"
stayed visible, which reads as random until you count the characters after the
colon. Those two names are the first cases below.
"""

import pytest
from ecm_parsing import (
    SHORT_CHANNEL_NAME_DEFAULT,
    SHORT_DESCRIPTION_DEFAULT,
    short_channel_name_match,
    short_description_match,
)

# ---------------------------------------------------------------------------
# The defaults must not move: a bare tag has to behave as it always has
# ---------------------------------------------------------------------------

def test_defaults_are_the_historical_values():
    assert SHORT_DESCRIPTION_DEFAULT == 15
    assert SHORT_CHANNEL_NAME_DEFAULT == 25


# ---------------------------------------------------------------------------
# [ShortDescription:N]
# ---------------------------------------------------------------------------

def test_the_reported_pair_splits_at_the_default_cutoff():
    """Both names are real; only the character count differs."""
    assert short_description_match("NCAAF 25: FS1 [1080p]") == ("colon", 11)
    assert short_description_match("NCAAF 26: SEC NETWORK [1080p]") is None


def test_raising_the_cutoff_catches_the_longer_one_too():
    assert short_description_match("NCAAF 26: SEC NETWORK [1080p]", 25) == ("colon", 19)


def test_lowering_the_cutoff_lets_the_shorter_one_through():
    assert short_description_match("NCAAF 25: FS1 [1080p]", 5) is None


@pytest.mark.parametrize("name,separator,length", [
    ("PPV 12: UFC", "colon", 3),
    ("PPV 12 | UFC", "pipe", 3),
    ("PPV 12 - UFC", "dash", 3),
])
def test_all_three_separators_are_measured(name, separator, length):
    assert short_description_match(name, 15) == (separator, length)


def test_a_clock_time_is_not_a_separator():
    """Without the whitespace lookahead, "10:30" would read as a separator."""
    assert short_description_match("LIVE 10:30", 15) is None


def test_a_name_with_no_separator_is_not_this_rules_business():
    assert short_description_match("SOME CHANNEL NAME", 15) is None


def test_exactly_at_the_threshold_is_long_enough():
    """The comparison is strictly less-than, so N characters passes."""
    fifteen = "A" * 15
    assert short_description_match(f"PPV 1: {fifteen}", 15) is None
    assert short_description_match(f"PPV 1: {'A' * 14}", 15) == ("colon", 14)


# ---------------------------------------------------------------------------
# Only the FIRST separator in the name divides it from its description
# ---------------------------------------------------------------------------
#
# Measured live on 2026-11-27: "LIVE EVENT 05 - 1pm Cage Warriors 214: Unplugged"
# was hidden on the day of the event. The rule measured every separator in the
# name and hid on the first short one, so the colon INSIDE the event title left
# "Unplugged" (9 characters) while the 33 characters after the dash were never the
# deciding count. A separator after the first one is part of the description.

@pytest.mark.parametrize("name", [
    "LIVE EVENT 05 - 1pm Cage Warriors 214: Unplugged",
    "PPV 20 |  EVENTS 09: PRELIMS JONES VS. PETTUS (11.20 5:00 PM ET) DAZN",
    "UFC 02: UFC FREEDOM 250: TOPURIA VS GAETHJE start:2026-06-15 01:55:00",
    "LIVE EVENT 04 - 11am Mix Fight 59 | Zakarian v Mohamad",
    "PDC 04 | 2026 Grand Slam of Darts | Day 9 | Sun 22nd",
])
def test_a_separator_inside_the_description_does_not_hide_the_channel(name):
    assert short_description_match(name, 15) is None


@pytest.mark.parametrize("name,separator,length", [
    ("NCAAF 25: FS1 [1080p]", "colon", 11),
    ("PPV 12 - UFC: Main Card Prelims Tonight", "dash", 30),
    ("Boxing 1 | BKb 61 7PM", "pipe", 10),
    ("PPV 12: UFC - Main Card Prelims Tonight", "colon", 31),
])
def test_the_first_separator_is_the_one_measured(name, separator, length):
    assert short_description_match(name, 100) == (separator, length)


def test_a_short_first_description_still_hides_whatever_follows_it_later():
    """A name whose text after its first separator is short stays matched."""
    assert short_description_match("PPV 12: UFC", 15) == ("colon", 3)
    assert short_description_match("NCAAF 25: FS1 [1080p]") == ("colon", 11)


# ---------------------------------------------------------------------------
# [ShortChannelName:N]
# ---------------------------------------------------------------------------

def test_a_short_separatorless_name_is_matched():
    assert short_channel_name_match("PPV 12") == 6


def test_a_long_separatorless_name_is_not():
    assert short_channel_name_match("A Very Long Channel Name Indeed") is None


def test_lowering_the_cutoff_spares_a_short_name():
    assert short_channel_name_match("PPV 12", 5) is None


def test_raising_the_cutoff_catches_a_longer_name():
    name = "Channel Twelve Sports"  # 21 characters, under 25 already
    assert short_channel_name_match(name, 25) == 21
    assert short_channel_name_match(name, 15) is None


@pytest.mark.parametrize("name", [
    "PPV 12: UFC",     # colon followed by whitespace
    "PPV 12 | UFC",    # pipe
    "PPV 12 - UFC",    # spaced dash
])
def test_a_name_carrying_any_separator_is_skipped(name):
    """This rule only judges names that carry no event details at all."""
    assert short_channel_name_match(name, 100) is None


def test_a_trailing_empty_colon_still_counts_as_no_separator():
    """Long-standing behaviour: [EmptyPlaceholder] handles these earlier."""
    assert short_channel_name_match("PPV 25:", 25) == 7


def test_whitespace_is_collapsed_before_measuring():
    assert short_channel_name_match("PPV      12", 25) == 6


@pytest.mark.parametrize("name", ["", None])
def test_an_empty_name_does_not_crash_either_rule(name):
    assert short_description_match(name, 15) is None
    # An empty name is shorter than any positive threshold.
    assert short_channel_name_match(name, 25) == 0
