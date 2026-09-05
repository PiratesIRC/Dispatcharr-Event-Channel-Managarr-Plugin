"""The CSV preamble says what the file is, what the run did, and what was set.

Measured 2026-09-05 against a real export this installation wrote. Four things
were wrong with it, none of which is a spelling matter.

It never said WHAT THE FILE IS. It opened with a version string and a row count,
so a person opening it in a spreadsheet met a wall of comment lines with nothing
telling them the lines are a preamble to skip.

SETTINGS PRINTED AS PYTHON. "keep_duplicates: False" and "manage_dummy_epg:
True" are not how the interface writes them, and Dispatcharr stores some of
these booleans as the STRING "true", so a naive rendering shows two different
spellings for the same state.

SETTINGS PRINTED AS INTERNAL IDS. A reader who wants to change what they see
cannot find "auto_set_dummy_epg_on_hide" in the interface, where it is called
Auto-Remove EPG on Hide. That id is also misleading on its own: it says set and
it REMOVES.

BARE NUMBERS SAID NOTHING. "past_date_grace_hours: 4" leaves a reader to guess
the unit and the direction.

Five settings that change what a run does were absent from the report
altogether, so a report could not explain its own behaviour: the date format,
whether existing EPG is overridden, whether a rescan follows an M3U refresh, the
export retention, and the ignore regex behaviour was described without them.
"""

import ecm_parsing  # resolves via pyproject.toml pythonpath

yes_no = ecm_parsing.yes_no
lines_for = ecm_parsing.settings_report_lines


# --- Yes and No, not True and False ---------------------------------------------

def test_a_real_boolean_renders_as_yes_or_no():
    assert yes_no(True) == "Yes"
    assert yes_no(False) == "No"


def test_the_string_true_renders_as_yes():
    """Dispatcharr stores some of these booleans as strings."""
    for value in ("true", "True", "TRUE", " true "):
        assert yes_no(value) == "Yes", value


def test_the_string_false_renders_as_no():
    for value in ("false", "False", "FALSE", " false "):
        assert yes_no(value) == "No", value


def test_the_other_spellings_a_checkbox_can_produce():
    assert yes_no("yes") == "Yes"
    assert yes_no("no") == "No"
    assert yes_no(1) == "Yes"
    assert yes_no(0) == "No"


def test_an_unset_value_reads_as_no_rather_than_blank():
    assert yes_no(None) == "No"
    assert yes_no("") == "No"


def test_an_unrecognised_value_is_shown_as_it_is_rather_than_guessed():
    assert yes_no("sometimes") == "sometimes"


# --- the preamble identifies the file --------------------------------------------

def test_the_first_line_says_what_the_file_is():
    first = ecm_parsing.REPORT_INTRO_LINES[0].lower()
    assert "event channel managarr" in first


def test_the_preamble_tells_a_spreadsheet_reader_to_skip_the_comment_lines():
    joined = " ".join(ecm_parsing.REPORT_INTRO_LINES).lower()
    assert "#" in joined
    assert "skip" in joined


def test_the_intro_comes_before_anything_else_and_is_short():
    assert 1 <= len(ecm_parsing.REPORT_INTRO_LINES) <= 4


# --- the settings block ----------------------------------------------------------

def _rendered(settings):
    return "\n".join(lines_for(settings))


def test_a_boolean_setting_is_rendered_as_yes_or_no():
    out = _rendered({"manage_dummy_epg": True, "keep_duplicates": False})
    assert ": Yes" in out and ": No" in out
    assert "True" not in out and "False" not in out


def test_a_boolean_stored_as_a_string_renders_the_same_way():
    from_bool = _rendered({"manage_dummy_epg": True})
    from_text = _rendered({"manage_dummy_epg": "true"})
    assert from_bool == from_text


def test_every_line_uses_the_label_from_the_interface():
    out = _rendered({})
    assert "auto_set_dummy_epg_on_hide" not in out
    assert "Auto-Remove EPG on Hide" in out


def test_a_bare_number_carries_its_meaning():
    out = _rendered({"past_date_grace_hours": 4, "dummy_epg_event_duration_hours": 3})
    assert "4 hours" in out
    assert "3 hours" in out


def test_an_empty_setting_says_so_rather_than_showing_nothing():
    out = _rendered({"regex_force_visible": ""})
    assert "(empty)" in out


def test_the_settings_that_change_what_a_run_does_are_all_reported():
    """A report that omits a setting cannot explain its own behaviour."""
    reported = {sid for sid, _label, _kind in ecm_parsing.SETTINGS_REPORT}
    for required in ("channel_profile_name", "channel_groups", "name_source",
                     "date_format", "hide_rules_priority",
                     "regex_channels_to_ignore", "regex_mark_inactive",
                     "regex_force_visible", "past_date_grace_hours",
                     "undated_event_grace_hours", "duplicate_strategy",
                     "keep_duplicates", "auto_set_dummy_epg_on_hide",
                     "manage_dummy_epg", "override_existing_epg",
                     "dummy_epg_channel_format", "dummy_epg_event_duration_hours",
                     "dummy_epg_event_timezone", "group_epg_source_map",
                     "scheduled_times", "enable_scheduled_csv_export",
                     "csv_retention_days", "auto_rescan_on_m3u_refresh",
                     "rate_limiting"):
        assert required in reported, f"{required} is missing from the report"


def test_the_runtime_timezone_is_reported_and_says_where_it_comes_from():
    """It is not a setting of this plugin; it is read from Dispatcharr."""
    out = _rendered({"timezone": "America/Chicago"})
    assert "America/Chicago" in out
    assert "Dispatcharr" in out


def test_a_missing_setting_does_not_break_the_report():
    assert lines_for({}) and lines_for(None)


# --- plain ASCII throughout --------------------------------------------------------

def test_the_intro_is_plain_ascii():
    """A spreadsheet opening this under another codepage turns anything else to
    mojibake."""
    for line in ecm_parsing.REPORT_INTRO_LINES:
        assert line.isascii(), line


def test_every_label_in_the_settings_block_is_plain_ascii():
    for _sid, label, _kind in ecm_parsing.SETTINGS_REPORT:
        assert label.isascii(), label
        assert chr(0x2014) not in label


def test_the_rendered_block_is_plain_ascii_for_ascii_input():
    out = _rendered({"channel_groups": "US: PPV", "manage_dummy_epg": True})
    assert out.isascii()


def test_a_non_ascii_setting_value_is_the_operators_own_text_and_is_kept():
    """Their group names really do contain emoji; the plugin must not mangle them."""
    out = _rendered({"channel_groups": "USA | NCAAF " + chr(0x1F3C8)})
    assert chr(0x1F3C8) in out


# --- faults found by RENDERING the preamble and reading it -----------------------
#
# All three were invisible in the source and obvious in the output.

def test_a_single_hour_is_not_written_as_hours():
    assert "1 hour:" not in _rendered({"undated_event_grace_hours": 1})
    assert "1 hours" not in _rendered({"undated_event_grace_hours": 1})
    assert "1 hour" in _rendered({"undated_event_grace_hours": 1})


def test_a_single_day_is_not_written_as_days():
    assert "1 days" not in _rendered({"csv_retention_days": 1})
    assert "1 day" in _rendered({"csv_retention_days": 1})


def test_an_unset_retention_reads_as_its_default_not_as_empty():
    """It is absent from a settings file until someone sets it, and absent means
    keep everything, which is a fact worth stating rather than a blank."""
    out = _rendered({})
    assert "Delete CSV Exports Older Than: (empty)" not in out
    assert "keeps every export" in out


def test_an_unset_number_of_hours_reads_as_its_default():
    out = _rendered({})
    assert "Past Date Grace Period: (empty)" not in out


def test_a_stored_code_is_rendered_as_the_words_the_interface_uses():
    """lowest_number is not what the settings form calls it."""
    out = _rendered({"duplicate_strategy": "lowest_number"})
    assert "lowest_number" not in out
    assert "Keep Lowest Channel Number" in out


def test_every_choice_setting_renders_its_stored_code_as_words():
    pairs = [("name_source", "Channel_Name", "Channel Name"),
             ("name_source", "Stream_Name", "Stream Name"),
             ("date_format", "Auto", "Auto-detect"),
             ("dummy_epg_channel_format", "US", "US"),
             ("rate_limiting", "none", "None")]
    for setting, stored, expected in pairs:
        out = _rendered({setting: stored})
        assert expected in out, f"{setting}={stored} should mention {expected!r}"


def test_an_unknown_stored_code_is_shown_as_it_is():
    """A value the plugin does not recognise must be visible, not hidden."""
    assert "something_new" in _rendered({"duplicate_strategy": "something_new"})
