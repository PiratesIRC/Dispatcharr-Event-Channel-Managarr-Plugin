"""The [PastDate] reason for a name that carries a clock time.

The rule hides such a channel once start + event duration + grace has passed, but
the reason it wrote said "Event ended <start time>", so a 2:00 PM ET game read
"Event ended 10/24 02:00 PM EDT" in the CSV when it ended at 5:00 PM. A user reading
the export took the start time for the end time. The reason now prints the end time
(start + duration) and names the start separately.
"""

from datetime import datetime


class _Channel:
    def __init__(self, name):
        self.id = 1
        self.name = name


class _NullLogger:
    def debug(self, *a, **k):
        pass

    info = warning = error = debug


def _past_date_reason(bare_plugin, monkeypatch, start):
    name = "NCAAF 30: Mercyhurst vs. Robert Morris @ 3 Oct 02:00 PM ET"
    monkeypatch.setattr(bare_plugin, "_get_system_timezone",
                        lambda settings: "America/Chicago", raising=False)
    monkeypatch.setattr(bare_plugin, "_extract_date_from_channel_name",
                        lambda *a, **k: start, raising=False)
    monkeypatch.setattr(bare_plugin, "_name_has_stop_timestamp",
                        lambda name: False, raising=False)
    return bare_plugin._check_hide_rule(
        "PastDate", (0, 4), _Channel(name), name, _NullLogger(),
        {"dummy_epg_event_timezone": "America/New_York",
         "dummy_epg_event_duration_hours": "3"})


def test_reason_prints_the_end_time_not_the_start_time(bare_plugin, monkeypatch):
    hidden, reason = _past_date_reason(bare_plugin, monkeypatch, datetime(2020, 10, 3, 14, 0))
    assert hidden is True
    assert reason.startswith("[PastDate:0] Event ended 10/03 05:00 PM EDT")
    assert "started 10/03 02:00 PM EDT" in reason


def test_end_time_rolls_to_the_next_day(bare_plugin, monkeypatch):
    hidden, reason = _past_date_reason(bare_plugin, monkeypatch, datetime(2020, 10, 3, 22, 15))
    assert hidden is True
    assert reason.startswith("[PastDate:0] Event ended 10/04 01:15 AM EDT")
