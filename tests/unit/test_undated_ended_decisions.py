"""Run the [UndatedEnded] rule and assert on what it decides.

Every other test of this rule reads plugin.py with ast. That cannot tell whether the
anchor date reaches the call that builds the event window, and five separate mutations
that reverted the anchor passed the whole suite. These tests execute the rule.

The scenario throughout is the one that prompted the anchor. A provider publishes an
American football slate on Thursday 2026-09-10 for games played that weekend. A channel
named for Monday Night Football is not a Thursday event, and anchoring it on the day the
channel appeared judged it finished before it had been played.
"""

from datetime import datetime

import pytest
import pytz

FIRST_SEEN = "2026-09-10"                     # a Thursday
FIRST_SEEN_AT = "2026-09-10T20:56:41-05:00"
EASTERN = "US/Eastern"
DURATION_MINUTES = 300                        # a five hour programme
GRACE_HOURS = 1


class _Channel:
    """The only attribute the rule reads from a channel is its id."""

    def __init__(self, channel_id=4242):
        self.id = channel_id


@pytest.fixture
def rule(bare_plugin, monkeypatch):
    """The rule, with its two container-bound lookups replaced by fixed answers.

    _undated_event_properties reads the dummy EPG source the channel is bound to and
    _get_system_timezone reads Dispatcharr's own setting. Neither is what these tests
    are about, and both need a database.
    """
    channel = _Channel()
    bare_plugin._undated_tracker = {
        str(channel.id): {"first_seen": FIRST_SEEN, "first_seen_at": FIRST_SEEN_AT}}
    monkeypatch.setattr(
        bare_plugin, "_undated_event_properties",
        lambda ch, settings, logger=None: (None, EASTERN, DURATION_MINUTES),
        raising=False)
    monkeypatch.setattr(
        bare_plugin, "_get_system_timezone",
        lambda settings: "America/Chicago", raising=False)

    def run(channel_name, now):
        """Evaluate the rule as if the current moment were `now`."""
        real_datetime = pytz.timezone("America/Chicago").localize(now) \
            if now.tzinfo is None else now

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime.astimezone(tz) if tz else real_datetime

        module = type(bare_plugin).__module__
        monkeypatch.setattr(__import__(module, fromlist=["datetime"]),
                            "datetime", _FrozenDatetime)
        return bare_plugin._check_hide_rule(
            "UndatedEnded", None, channel, channel_name,
            _logger(), {"undated_event_grace_hours": GRACE_HOURS})

    return run


def _logger():
    class _Logger:
        def debug(self, *a, **k):
            pass

        info = warning = error = debug
    return _Logger()


def _central(year, month, day, hour, minute=0):
    return pytz.timezone("America/Chicago").localize(
        datetime(year, month, day, hour, minute))


def test_monday_night_football_is_not_hidden_on_the_sunday_before_it(rule):
    """The defect this anchor exists to fix.

    Anchored on the first-seen Thursday, the window closed on the Friday morning and the
    channel was hidden all weekend. Anchored on the Monday its name states, it is not.
    """
    hidden, reason = rule("NFL  | 16 - MNF 8:15pm Broncos at Chiefs",
                          _central(2026, 9, 13, 7, 30))
    assert hidden is False, f"hidden before it was played: {reason}"


def test_monday_night_football_is_hidden_once_it_has_been_played(rule):
    hidden, reason = rule("NFL  | 16 - MNF 8:15pm Broncos at Chiefs",
                          _central(2026, 9, 15, 8, 0))
    assert hidden is True
    assert "day read from the name as 2026-09-14" in reason


def test_sunday_night_football_is_not_hidden_on_the_sunday_afternoon(rule):
    hidden, reason = rule("NFL  | 15 - SNF 8:20pm Cowboys at Giants",
                          _central(2026, 9, 13, 14, 0))
    assert hidden is False, f"hidden before it was played: {reason}"


def test_a_thursday_game_is_still_hidden_by_the_sunday(rule):
    """The anchor must not keep a finished event visible. Thursday resolves to the
    first-seen date itself, so this is the behaviour the rule already had."""
    hidden, _ = rule("NFL  | 02 - TNF 8:35pm 49ers at Rams",
                     _central(2026, 9, 13, 7, 30))
    assert hidden is True


def test_a_name_stating_no_day_keeps_the_first_seen_anchor(rule):
    """The fallback. A 4:25pm event on the Thursday it was first seen has ended by
    Sunday, and no day in the name means nothing moves it."""
    hidden, reason = rule("NFL  | 14 - 4:25pm Cardinals at Chargers",
                          _central(2026, 9, 13, 7, 30))
    assert hidden is True
    assert "day read from the name" not in reason


def test_a_name_stating_no_day_is_not_hidden_while_its_event_runs(rule):
    hidden, _ = rule("NFL  | 14 - 4:25pm Cardinals at Chargers",
                     _central(2026, 9, 10, 17, 0))
    assert hidden is False


def test_a_channel_with_no_first_seen_record_is_left_visible(bare_plugin, rule):
    bare_plugin._undated_tracker = {}
    hidden, reason = rule("NFL  | 16 - MNF 8:15pm Broncos at Chiefs",
                          _central(2026, 9, 15, 8, 0))
    assert (hidden, reason) == (False, None)


def test_a_name_with_no_readable_time_is_left_visible(rule):
    hidden, reason = rule("NFL REDZONE", _central(2026, 9, 15, 8, 0))
    assert (hidden, reason) == (False, None)
