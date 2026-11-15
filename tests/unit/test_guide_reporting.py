"""Decisions behind the messages added after the 2026-11-14 support case.

A user had 39 visible event channels linked to a provider guide that titled every
block "No EVENT Today". The plugin correctly left that guide alone and said nothing,
so the run read as a success. These functions are the pure decisions behind the
warnings that now name the cause.
"""

import re

import ecm_parsing
import ecm_profiles


def _row(action, has_epg, source, assigned=False):
    return {"action": action, "has_epg": has_epg, "epg_source": source,
            "managed_epg_assigned": assigned}


# --- kept_guide_summary -----------------------------------------------------

def test_kept_guide_counts_visible_channels_on_a_non_dummy_source():
    rows = ([_row("Visible", "Yes", "Infinity")] * 39
            + [_row("Visible", "Yes", "ECM Managed Dummy")] * 13)
    assert ecm_parsing.kept_guide_summary(rows, {"ECM Managed Dummy"}) == [("Infinity", 39)]


def test_kept_guide_ignores_hidden_and_unlinked_and_taken_over_channels():
    rows = [
        _row("Hide", "Yes", "Infinity"),
        _row("No change", "Yes", "Infinity"),
        _row("Visible", "No", ""),
        _row("Visible", "Yes", "Infinity", assigned=True),
        _row("Show", "Yes", "Infinity"),
    ]
    assert ecm_parsing.kept_guide_summary(rows, set()) == [("Infinity", 1)]


def test_kept_guide_orders_by_count_then_name_and_matches_dummy_names_exactly():
    rows = ([_row("Visible", "Yes", "B")] * 2 + [_row("Visible", "Yes", "A")] * 2
            + [_row("Visible", "Yes", "C")] * 5 + [_row("Visible", "Yes", "ecm")])
    assert ecm_parsing.kept_guide_summary(rows, {"ECM"}) == [
        ("C", 5), ("A", 2), ("B", 2), ("ecm", 1)]


def test_kept_guide_line_is_none_when_nothing_was_kept():
    assert ecm_parsing.kept_guide_line([]) is None


def test_kept_guide_line_names_the_sources_and_caps_the_list():
    line = ecm_parsing.kept_guide_line([("Infinity", 39), ("B", 2), ("C", 1), ("D", 1)])
    assert line.startswith("43 visible channel(s) kept another guide (Infinity: 39, B: 2, C: 1, +1 more)")
    assert "\n" not in line


# --- placeholder_only_epg_ids ------------------------------------------------

def test_placeholder_guide_is_one_whose_every_title_matches():
    pattern = re.compile("NO EVENT", re.IGNORECASE)
    rows = [(1, "No EVENT Today"), (1, "No Event Today"),
            (2, "No EVENT Today"), (2, "Rice vs. Army"),
            (3, "Rice vs. Army")]
    assert ecm_parsing.placeholder_only_epg_ids(rows, pattern) == {1}


def test_placeholder_guide_needs_a_pattern_and_treats_a_missing_title_as_real():
    pattern = re.compile("NO EVENT", re.IGNORECASE)
    assert ecm_parsing.placeholder_only_epg_ids([(1, "No EVENT Today")], None) == set()
    assert ecm_parsing.placeholder_only_epg_ids([(1, None)], pattern) == set()
    assert ecm_parsing.placeholder_only_epg_ids([], pattern) == set()


# --- channel_format_hint -------------------------------------------------------

AT_NAMES = [
    "NCAAB 10: Coastal Georgia vs. Florida Atlantic @ 14 Nov 02:00 PM ET",
    "NCAAB 11: Northern Illinois vs. Bellarmine @ 14 Nov 02:00 PM ET",
    "NCAAB 12: Gardner-Webb vs. South Carolina @ 14 Nov 02:00 PM ET",
    "NHL 01: Kraken @ Red Wings @ 9 Oct 07:00 PM ET",
]
US_NAMES = [
    "PPV EVENT 12: Cage Fury FC 141 (4.17 8:30 PM ET)",
    "LIVE EVENT 04: Boxing (4.18 9:00 PM ET)",
    "PPV EVENT 01: Wrestling Night (4.19 7:00 PM ET)",
]
SE_NAMES = [
    "LIVE | GIRONA - REAL SOCIEDAD | Thu 14 May 19:55 CEST (SE) | 8K EXCLUSIVE | SE: TV4 PLAY PPV 7",
    "LIVE | AIK - HAMMARBY | Sun 17 May 15:00 CEST (SE) | HD | SE: VIAPLAY PPV 2",
    "LIVE | MALMO - ELFSBORG | Mon 18 May 19:00 CEST (SE) | HD | SE: VIAPLAY PPV 3",
]


def _hint(names, selected):
    return ecm_profiles.channel_format_hint(names, selected, ecm_parsing.AT_TITLE_PATTERN)


def test_each_layout_is_counted_under_its_own_format():
    counts = ecm_profiles.channel_format_counts(
        AT_NAMES + US_NAMES + SE_NAMES, ecm_parsing.AT_TITLE_PATTERN)
    assert counts == {"US": 3, "SE": 3, "AT": 4}


def test_hint_names_the_format_that_reads_the_names():
    assert _hint(AT_NAMES, "US") == ("AT", 4, 0)
    assert _hint(US_NAMES, "AT") == ("US", 3, 0)
    assert _hint(SE_NAMES, "US") == ("SE", 3, 0)


def test_no_hint_when_the_selected_format_reads_the_names():
    assert _hint(AT_NAMES, "AT") is None
    assert _hint(AT_NAMES, "at") is None


def test_no_hint_from_too_few_names_or_from_names_no_format_reads():
    assert _hint(AT_NAMES[:2], "US") is None
    assert _hint(["ESPN", "NFL REDZONE", "Sky Sports News", "TNT"], "US") is None
    assert _hint([None, "", 5], "US") is None


def test_no_hint_when_the_selected_format_reads_a_comparable_share():
    # Mixed lineup: the selected format is doing real work, so it is not "wrong".
    assert _hint(AT_NAMES + US_NAMES, "US") is None


# --- parse_group_source_map: two mappings on one line -------------------------

def test_two_mappings_on_one_line_get_a_hint_that_says_how_the_line_was_read():
    raw = "USA | NCAAB = ECM NCAAB USA | NCAAF = NCAAF"
    hints = ecm_profiles.one_line_mapping_hints(raw)
    assert len(hints) == 1
    assert "'USA | NCAAB'" in hints[0]
    assert "'ECM NCAAB USA | NCAAF = NCAAF'" in hints[0]
    assert "own line" in hints[0]
    # The parser itself is unchanged: a source name may contain an equals sign
    # (tests/unit/test_ecm_profiles.py pins that), so the hint rejects nothing.
    mapping, problems = ecm_profiles.parse_group_source_map(raw)
    assert mapping == {"usa | ncaab": "ECM NCAAB USA | NCAAF = NCAAF"}
    assert problems == []


def test_one_mapping_per_line_gets_no_hint():
    raw = "USA | NCAAB = ECM NCAAB\n# note = comment = ignored\nUSA | NCAAF = NCAAF\n= x = y"
    assert ecm_profiles.one_line_mapping_hints(raw) == []
    assert ecm_profiles.one_line_mapping_hints(None) == []


# --- the one-line toast ---------------------------------------------------------

def test_kept_guide_short_is_toast_sized():
    assert ecm_parsing.kept_guide_short([]) is None
    assert ecm_parsing.kept_guide_short([("Infinity", 39)]) == "39 kept another guide (Infinity: 39)"
    assert ecm_parsing.kept_guide_short([("A", 5), ("B", 2), ("C", 1)]) == (
        "8 kept another guide (A: 5, B: 2, +1 more)")


def test_the_toast_summary_carries_the_notices(bare_plugin):
    result = {"status": "success", "results": {
        "total_channels": 1116, "csv_file": "/data/exports/x.csv",
        "notices": ["39 kept another guide (Infinity: 39)",
                    "Channel Name Format US reads 0 names, AT reads 170"]}}
    line = bare_plugin._compact_scan_summary("Dry run", result)
    assert "39 kept another guide (Infinity: 39)" in line
    assert "Channel Name Format US reads 0 names, AT reads 170" in line
    assert len(line) < 280
    assert "\n" not in line


def test_the_toast_summary_is_unchanged_without_notices(bare_plugin):
    result = {"status": "success", "results": {"total_channels": 227, "csv_file": "/data/exports/x.csv"}}
    assert bare_plugin._compact_scan_summary("Dry run", result) == "Dry run: 227 channels | CSV: x.csv"
