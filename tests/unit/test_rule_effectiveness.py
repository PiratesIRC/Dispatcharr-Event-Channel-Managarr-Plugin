"""Unit tests for the CSV's Rule Effectiveness tally and the tag it groups by.

A user's own export carried a single Rule Effectiveness line reading

    #   : 10 channels

with a blank rule name. All ten channels had been hidden by duplicate handling,
which produced no bracketed tag in the reason, so the tally grouped them under
the empty string. That blank label was exactly the information needed to
diagnose their report, and its absence sent a diagnosis down three wrong paths
(bug-177).

Two things are fixed here. Duplicate hides now carry a real tag, and the tally
refuses to print a blank label for any hidden channel, so a hide path added
later without a tag is visible instead of silent.
"""

import ecm_parsing  # resolves via pyproject.toml pythonpath

tag = ecm_parsing.hide_rule_tag
tally = ecm_parsing.rule_effectiveness


# --- hide_rule_tag --------------------------------------------------------------

def test_a_bracketed_tag_is_extracted():
    assert tag("[PastDate:0] Event date 08/30/2026 is 6 days in the past") == "PastDate:0"


def test_a_tag_with_no_parameter_is_extracted():
    assert tag("[NoEventPattern] Name contains 'no event'") == "NoEventPattern"


def test_a_tag_that_is_the_whole_reason_is_extracted():
    assert tag("[BlankName]") == "BlankName"


def test_a_reason_with_no_tag_returns_empty():
    assert tag("Has event") == ""


def test_a_reason_that_does_not_start_with_a_bracket_returns_empty():
    """A bracket later in the text is not a tag."""
    assert tag("Matches force visible regex [not a tag]") == ""


def test_an_unclosed_bracket_returns_empty():
    assert tag("[PastDate:0 Event date") == ""


def test_an_empty_bracket_returns_empty():
    assert tag("[] something") == ""


def test_empty_and_none_return_empty():
    assert tag("") == ""
    assert tag(None) == ""


def test_the_duplicate_reason_carries_a_tag():
    """The defect: this reason used to produce no tag at all."""
    assert tag(ecm_parsing.DUPLICATE_HIDE_REASON) == "Duplicate"


# --- rule_effectiveness ---------------------------------------------------------

def row(action, hide_rule):
    return {"action": action, "hide_rule": hide_rule}


def test_only_hidden_channels_are_counted():
    rows = [row("Hide", "PastDate:0"), row("No change", "PastDate:0"),
            row("Visible", ""), row("Show", "")]
    assert tally(rows) == [("PastDate:0", 1)]


def test_counts_are_summed_per_tag():
    rows = [row("Hide", "NoEventPattern")] * 3 + [row("Hide", "PastDate:0")] * 2
    assert tally(rows) == [("NoEventPattern", 3), ("PastDate:0", 2)]


def test_the_largest_count_comes_first():
    rows = [row("Hide", "A")] + [row("Hide", "B")] * 5
    assert tally(rows)[0] == ("B", 5)


def test_ties_are_ordered_by_tag_so_the_output_is_stable():
    rows = [row("Hide", "B"), row("Hide", "A")]
    assert tally(rows) == [("A", 1), ("B", 1)]


def test_a_hidden_channel_with_no_tag_never_produces_a_blank_label():
    """The defect. A blank label reads as a bug in the CSV, not as information."""
    result = tally([row("Hide", "")])
    assert len(result) == 1
    label, count = result[0]
    assert label.strip(), "a hidden channel must never be counted under a blank label"
    assert count == 1


def test_the_untagged_label_says_what_it_means():
    label, _ = tally([row("Hide", "")])[0]
    assert "rule" in label.lower()


def test_a_missing_hide_rule_key_is_treated_like_an_empty_one():
    """dict.get cannot tell an absent key from one present and empty."""
    result = tally([{"action": "Hide"}])
    assert len(result) == 1 and result[0][1] == 1
    assert result[0][0].strip()


def test_a_none_hide_rule_is_treated_like_an_empty_one():
    result = tally([{"action": "Hide", "hide_rule": None}])
    assert len(result) == 1 and result[0][0].strip()


def test_no_hidden_channels_gives_an_empty_tally():
    assert tally([row("No change", ""), row("Visible", "")]) == []


def test_an_empty_result_set_gives_an_empty_tally():
    assert tally([]) == []


def test_the_reporters_run_would_now_name_the_cause():
    """Ten duplicate hides, which previously tallied under a blank label."""
    rows = [row("Hide", tag(ecm_parsing.DUPLICATE_HIDE_REASON)) for _ in range(10)]
    assert tally(rows) == [("Duplicate", 10)]
