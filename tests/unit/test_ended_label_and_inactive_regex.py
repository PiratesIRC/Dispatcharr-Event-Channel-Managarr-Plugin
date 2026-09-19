"""Two defects found on 2026-09-19 while reviewing the visible US: PPV channels.

The ended label (bug-193). The managed dummy EPG source carried the ended title
template "Ended at {month}/{day} {endtime} CDT: {title}". Dispatcharr fills {month},
{day}, {date} and {year} with the START date of the event and offers no end-date
placeholder at all, so an event that ends after midnight was labelled with the day
before it ended: a 7pm Central event lasting five hours read "Ended at 9/19 12 AM",
when it ended at midnight on 9/20. The ended label now carries the end time only.

The inactive regex (bug-192). The [InactiveRegex] rule passed the stored pattern
through the unicode_escape codec before compiling it, which turns the regex word
boundary \\b into a backspace character, so a pattern such as \\bCANCELLED\\b never
matched anything. The Ignore and Force Visible fields and Validate Configuration all
compile the pattern exactly as typed; the Inactive field now does the same.
"""

import ecm_profiles
import pytest

# Every placeholder Dispatcharr 0.31.0 fills from the event's start date, read from
# /app/apps/output/dummy_epg.py. None of them may appear in the ended label.
START_DATE_PLACEHOLDERS = ("{month}", "{day}", "{date}", "{year}")


def _assert_ended_has_no_start_date(template):
    for placeholder in START_DATE_PLACEHOLDERS:
        assert placeholder not in template, template
    assert "{title}" in template


@pytest.mark.parametrize("date_format", ["Auto", "US", "EU"])
def test_resolve_output_timezone_ended_label_has_no_start_date(date_format):
    got = ecm_profiles.resolve_output_timezone(
        "America/New_York", "America/Chicago", date_format)
    _assert_ended_has_no_start_date(got["ended_title_template"])
    assert "{endtime}" in got["ended_title_template"]
    # The upcoming label is shown BEFORE the event starts, so the start date in it is
    # the correct date and stays.
    assert "{starttime}" in got["upcoming_title_template"]
    assert ("{day}/{month}" if date_format == "EU" else "{month}/{day}") \
        in got["upcoming_title_template"]


@pytest.mark.parametrize("date_format", ["Auto", "US", "EU"])
@pytest.mark.parametrize("channel_format,end_ph", [("US", "{endtime}"), ("SE", "{endtime24}")])
def test_localized_template_props_ended_label_has_no_start_date(
        bare_plugin, monkeypatch, date_format, channel_format, end_ph):
    """The plugin.py copy is the one written to the managed source."""
    monkeypatch.setattr(bare_plugin, "_get_system_timezone",
                        lambda settings: "America/Chicago", raising=False)
    got = bare_plugin._localized_template_props({
        "dummy_epg_event_timezone": "America/New_York",
        "dummy_epg_channel_format": channel_format,
        "date_format": date_format,
    })
    assert got["output_timezone"] == "America/Chicago"
    _assert_ended_has_no_start_date(got["ended_title_template"])
    assert end_ph in got["ended_title_template"]
    assert ("{day}/{month}" if date_format == "EU" else "{month}/{day}") \
        in got["upcoming_title_template"]


def test_plugin_and_profiles_write_the_same_ended_label(bare_plugin, monkeypatch):
    monkeypatch.setattr(bare_plugin, "_get_system_timezone",
                        lambda settings: "America/Chicago", raising=False)
    from_plugin = bare_plugin._localized_template_props({
        "dummy_epg_event_timezone": "America/New_York",
        "dummy_epg_channel_format": "US",
        "date_format": "Auto",
    })
    from_profiles = ecm_profiles.resolve_output_timezone(
        "America/New_York", "America/Chicago", "Auto")
    assert from_plugin["ended_title_template"] == from_profiles["ended_title_template"]


# --- [InactiveRegex] --------------------------------------------------------------

def _inactive(bare_plugin, pattern, channel_name):
    hidden, _reason = bare_plugin._check_hide_rule(
        "InactiveRegex", None, None, channel_name, _NullLogger(),
        {"regex_mark_inactive": pattern})
    return hidden


class _NullLogger:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


@pytest.mark.parametrize("pattern", [r"\bCANCELL?ED\b", "CANCELL?ED", r"\bcancel"])
def test_inactive_regex_word_boundary_matches(bare_plugin, pattern):
    assert _inactive(bare_plugin, pattern,
                     "LIVE EVENT 08 - CANCELLED 6:15pm Cedar Lake Late Models")


def test_inactive_regex_word_boundary_still_excludes(bare_plugin):
    """The negative control: the boundary must still refuse a longer word."""
    assert not _inactive(bare_plugin, r"\bCANCEL\b",
                         "LIVE EVENT 08 - CANCELLED 6:15pm Cedar Lake Late Models")
    assert not _inactive(bare_plugin, r"\bCANCELL?ED\b",
                         "LIVE EVENT 11 - 7pm Williams Grove Sprints")


def test_inactive_regex_digit_class(bare_plugin):
    assert _inactive(bare_plugin, r"^OFF AIR \d+$", "OFF AIR 12")


def test_inactive_regex_non_ascii(bare_plugin):
    """unicode_escape also decoded UTF-8 bytes as Latin-1, so a pattern carrying a
    non-ASCII character could not match the same character in a name."""
    assert _inactive(bare_plugin, "PE\u00d1A", "PPV EVENT 3: PE\u00d1A vs LOPEZ")


def test_inactive_regex_invalid_pattern_does_not_hide(bare_plugin):
    assert not _inactive(bare_plugin, "CANCELL?ED(", "LIVE EVENT 08 - CANCELLED")
