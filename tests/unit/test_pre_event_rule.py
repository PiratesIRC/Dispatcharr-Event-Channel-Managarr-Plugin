"""The [PreEvent:N] hide rule (issue #33).

A channel whose name carries a date AND a clock time stays hidden until N minutes
before the event starts, so a provider slot named for a 1:00 PM game appears in the
lineup at 12:45 PM with the default of 15. The start is read exactly as [PastDate]
reads it, in the Channel Name Event Timezone. A name with no clock time, or no date,
is never hidden by this rule. The rule is not in the default list, because adding it
there would hide every one of today's events on an existing install.
"""

from datetime import datetime, timedelta

import ecm_parsing
import pytest
import pytz

EASTERN = "America/New_York"
CENTRAL = "America/Chicago"


# --- the decision in ecm_parsing ------------------------------------------------

def _window(naive_start, minutes=15, tz_name=EASTERN):
    return ecm_parsing.pre_event_window(naive_start, tz_name, minutes)


def test_window_reads_the_start_in_the_event_timezone():
    start, visible_from = _window(datetime(2026, 8, 29, 13, 0))
    assert start == pytz.timezone(EASTERN).localize(datetime(2026, 8, 29, 13, 0))
    assert visible_from == start - timedelta(minutes=15)


@pytest.mark.parametrize("offset_minutes,hidden", [
    (-16, True),    # 12:44 PM ET: before the cutoff
    (-15, False),   # 12:45 PM ET: at the cutoff, visible
    (-1, False),
    (30, False),    # the event is under way
])
def test_hidden_only_before_the_cutoff(offset_minutes, hidden):
    start, visible_from = _window(datetime(2026, 8, 29, 13, 0))
    now = start + timedelta(minutes=offset_minutes)
    assert ecm_parsing.pre_event_hides(now, visible_from) is hidden


def test_eastern_name_against_a_central_clock():
    """12:44 PM Eastern is 11:44 AM Central; the comparison is between instants."""
    start, visible_from = _window(datetime(2026, 8, 29, 13, 0))
    now_central = pytz.timezone(CENTRAL).localize(datetime(2026, 8, 29, 11, 44))
    assert ecm_parsing.pre_event_hides(now_central, visible_from) is True
    now_central = pytz.timezone(CENTRAL).localize(datetime(2026, 8, 29, 11, 45))
    assert ecm_parsing.pre_event_hides(now_central, visible_from) is False


def test_zero_minutes_means_visible_from_the_start():
    start, visible_from = _window(datetime(2026, 8, 29, 13, 0), minutes=0)
    assert visible_from == start


def test_start_tomorrow_is_hidden_today():
    start, visible_from = _window(datetime(2026, 8, 30, 13, 0))
    now = pytz.timezone(EASTERN).localize(datetime(2026, 8, 29, 20, 0))
    assert ecm_parsing.pre_event_hides(now, visible_from) is True


@pytest.mark.parametrize("naive_start,minutes,tz_name", [
    (datetime(2026, 8, 29, 0, 0), 15, EASTERN),    # midnight: the parser found no clock time
    (None, 15, EASTERN),                           # no date in the name
    (datetime(2026, 8, 29, 13, 0), -5, EASTERN),   # a negative count is not applied
    (datetime(2026, 8, 29, 13, 0), "x", EASTERN),
    (datetime(2026, 8, 29, 13, 0), 15, "Not/AZone"),
])
def test_no_window_means_the_rule_does_not_apply(naive_start, minutes, tz_name):
    assert ecm_parsing.pre_event_window(naive_start, tz_name, minutes) is None


def test_no_window_never_hides():
    now = pytz.timezone(EASTERN).localize(datetime(2026, 8, 29, 12, 0))
    assert ecm_parsing.pre_event_hides(now, None) is False


def test_not_in_the_default_rules(plugin_module):
    assert "PreEvent" not in plugin_module.Plugin.DEFAULT_HIDE_RULES


# --- the rule as plugin.py runs it ------------------------------------------------

class _Channel:
    def __init__(self, name):
        self.id = 1
        self.name = name


class _NullLogger:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _run(bare_plugin, monkeypatch, naive_start, param=15):
    name = "NCAAF 84: USC vs. Wisconsin @ 24 Oct 07:00 PM ET"
    monkeypatch.setattr(bare_plugin, "_get_system_timezone",
                        lambda settings: CENTRAL, raising=False)
    monkeypatch.setattr(bare_plugin, "_extract_date_from_channel_name",
                        lambda *a, **k: naive_start, raising=False)
    return bare_plugin._check_hide_rule(
        "PreEvent", param, _Channel(name), name, _NullLogger(),
        {"dummy_epg_event_timezone": EASTERN})


def _eastern_now_plus(minutes):
    now = datetime.now(pytz.timezone(EASTERN)).replace(second=0, microsecond=0, tzinfo=None)
    return now + timedelta(minutes=minutes)


def test_plugin_hides_an_event_two_hours_out(bare_plugin, monkeypatch):
    start = _eastern_now_plus(120)
    if start.hour == 0 and start.minute == 0:
        start += timedelta(minutes=1)
    hidden, reason = _run(bare_plugin, monkeypatch, start)
    assert hidden is True
    assert reason.startswith("[PreEvent:15] Event starts ")
    assert "visible from" in reason


def test_plugin_shows_an_event_ten_minutes_out(bare_plugin, monkeypatch):
    start = _eastern_now_plus(10)
    if start.hour == 0 and start.minute == 0:
        start += timedelta(minutes=1)
    assert _run(bare_plugin, monkeypatch, start) == (False, None)


def test_plugin_uses_15_minutes_when_no_number_is_given(bare_plugin, monkeypatch):
    start = _eastern_now_plus(120)
    if start.hour == 0 and start.minute == 0:
        start += timedelta(minutes=1)
    hidden, reason = _run(bare_plugin, monkeypatch, start, param=None)
    assert hidden is True
    assert reason.startswith("[PreEvent:15] ")


def test_plugin_ignores_a_name_without_a_date(bare_plugin, monkeypatch):
    assert _run(bare_plugin, monkeypatch, None) == (False, None)


def test_plugin_ignores_a_two_part_parameter(bare_plugin, monkeypatch):
    """[PreEvent:15:2h] parses as a (days, hours) pair, which this rule does not take."""
    assert _run(bare_plugin, monkeypatch, _eastern_now_plus(120), param=(15, 2)) == (False, None)
