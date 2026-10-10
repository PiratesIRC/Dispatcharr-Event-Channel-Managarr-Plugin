"""Default Event Day by Group, and the tolerance number on [WrongDayOfWeek].

Measured 2026-10-10 (a Saturday) on the live box: the US: NFL slate names carry a
kickoff time and no date, for example "NFL  | 10 - 1pm Giants at Commanders", and
NFL.com lists every one of them for Sunday 11 October except Bills at Rams (Monday
12 October). With no date and no day word in the name, nothing hid them on the
Saturday, and Dispatcharr's dummy guide drew each one as a game played that day. The
named-day games ("SNF", "MNF") were shown a day early too, because [WrongDayOfWeek]
always allowed one day either side.

Two settings fix it without touching any other group: a default day for names in a
group that carry no day word, and a tolerance number on the rule, where
[WrongDayOfWeek:0] means the named day only and plain [WrongDayOfWeek] keeps the
historical one day either side.
"""

import logging
from datetime import datetime

import ecm_parsing
import pytest

MON, THU, SAT, SUN = 0, 3, 5, 6

# --- the setting parser --------------------------------------------------------------


def test_blank_is_empty_with_no_problems():
    assert ecm_parsing.parse_group_default_days("") == ({}, [])
    assert ecm_parsing.parse_group_default_days(None) == ({}, [])


def test_one_mapping_per_line_group_casefolded():
    mapping, problems = ecm_parsing.parse_group_default_days("US: NFL = Sunday\nNCAAF = sat\n")
    assert mapping == {"us: nfl": SUN, "ncaaf": SAT}
    assert problems == []


@pytest.mark.parametrize("word,day", [("Monday", 0), ("mon", 0), ("THURSDAY", 3), ("thu", 3),
                                      ("Sun", 6), ("sunday", 6)])
def test_full_and_three_letter_day_names(word, day):
    assert ecm_parsing.parse_group_default_days(f"US: NFL = {word}")[0] == {"us: nfl": day}


@pytest.mark.parametrize("line", ["US: NFL", "US: NFL = Funday", "= Sunday", "US: NFL = "])
def test_a_bad_line_is_reported_and_skipped(line):
    mapping, problems = ecm_parsing.parse_group_default_days(line + "\nUS: PPV = Saturday")
    assert mapping == {"us: ppv": SAT}
    assert len(problems) == 1


def test_a_group_named_twice_keeps_the_first_and_says_so():
    mapping, problems = ecm_parsing.parse_group_default_days("US: NFL = Sunday\nus: nfl = Monday")
    assert mapping == {"us: nfl": SUN}
    assert len(problems) == 1


# --- the decision --------------------------------------------------------------------

@pytest.mark.parametrize("named,default,today,tolerance,hide", [
    # No day anywhere: the rule does not apply, whatever the tolerance.
    (None, None, SAT, 0, False),
    # The group default supplies the day when the name has none.
    (None, SUN, SAT, 0, True),       # Sunday game on Saturday, game day only
    (None, SUN, SUN, 0, False),      # on Sunday
    (None, SUN, MON, 0, True),       # on Monday
    (None, SUN, SAT, 1, False),      # historical tolerance shows it a day early
    # A day word in the name wins over the group default.
    (MON, SUN, SUN, 0, True),        # MNF on Sunday, game day only
    (MON, SUN, MON, 0, False),
    (THU, SUN, THU, 0, False),       # TNF on Thursday
    # Plain [WrongDayOfWeek] (tolerance None) behaves exactly as before: one day.
    (SUN, None, SAT, None, False),
    (SUN, None, THU, None, True),
    (SUN, None, MON, None, False),   # wraps across the week boundary
    (MON, None, SUN, None, False),
])
def test_wrong_day_decision(named, default, today, tolerance, hide):
    assert ecm_parsing.wrong_day_hides(named, default, today, tolerance)[0] is hide


def test_the_decision_names_the_day_it_used_and_where_it_came_from():
    hide, day, source = ecm_parsing.wrong_day_hides(None, SUN, SAT, 0)
    assert (hide, day, source) == (True, SUN, "group default")
    assert ecm_parsing.wrong_day_hides(MON, SUN, SAT, 0)[2] == "name"


@pytest.mark.parametrize("tolerance", [-1, 7, 100])
def test_a_tolerance_outside_0_to_3_is_clamped(tolerance):
    # 3 either side already allows every day of the week, so a larger number can
    # only mean "never hide", which 3 already says.
    hide, _day, _src = ecm_parsing.wrong_day_hides(SUN, None, SAT, tolerance)
    assert hide is False if tolerance > 0 else hide is True


# --- the rule in the plugin ----------------------------------------------------------

class _Group:
    def __init__(self, name):
        self.name = name


class _Channel:
    def __init__(self, name, group):
        self.id = 1
        self.name = name
        self.channel_group = _Group(group)


LOG = logging.getLogger("t")
SATURDAY = datetime(2026, 10, 10, 8, 0)
SUNDAY = datetime(2026, 10, 11, 8, 0)
MONDAY = datetime(2026, 10, 12, 8, 0)


@pytest.fixture
def rule(bare_plugin, monkeypatch, plugin_module):
    monkeypatch.setattr(bare_plugin, "_get_system_timezone",
                        lambda settings: "America/Chicago", raising=False)

    def _run(name, group, when, param, default_days=""):
        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return tz.localize(when) if tz else when
        monkeypatch.setattr(plugin_module, "datetime", _Clock)
        settings = {"group_default_event_day": default_days}
        return bare_plugin._check_hide_rule("WrongDayOfWeek", param, _Channel(name, group),
                                            name, LOG, settings)
    return _run


SUNDAY_GAME = "NFL  | 10 - 1pm Giants at Commanders"
MNF_GAME = "NFL  | 15 - MNF 8:15pm Bills at Rams"


def test_a_sunday_afternoon_game_is_hidden_on_saturday(rule):
    hide, reason = rule(SUNDAY_GAME, "US: NFL", SATURDAY, 0, "US: NFL = Sunday")
    assert hide and "Sunday" in reason and "Saturday" in reason


def test_it_shows_on_sunday_and_hides_again_on_monday(rule):
    assert rule(SUNDAY_GAME, "US: NFL", SUNDAY, 0, "US: NFL = Sunday")[0] is False
    assert rule(SUNDAY_GAME, "US: NFL", MONDAY, 0, "US: NFL = Sunday")[0] is True


def test_monday_night_shows_only_on_monday(rule):
    assert rule(MNF_GAME, "US: NFL", SUNDAY, 0, "US: NFL = Sunday")[0] is True
    assert rule(MNF_GAME, "US: NFL", MONDAY, 0, "US: NFL = Sunday")[0] is False


def test_a_group_without_a_default_is_untouched(rule):
    assert rule(SUNDAY_GAME, "US: PPV", SATURDAY, 0, "US: NFL = Sunday")[0] is False


def test_plain_rule_keeps_the_one_day_tolerance(rule):
    assert rule(MNF_GAME, "US: NFL", SUNDAY, None, "")[0] is False


def test_a_negative_tolerance_never_hides_a_channel_on_its_own_day():
    # Without the clamp, distance 0 > -1 would hide every channel on its game day.
    assert ecm_parsing.wrong_day_hides(SUN, None, SUN, -1)[0] is False
