"""effective_channel_number: the override number wins, because Dispatcharr's UI shows it.

Dispatcharr's ChannelOverride model can carry its own channel_number. When it is set,
the UI shows that number instead of Channel.channel_number, so the duplicate rules and
the CSV export must read the same number the operator sees.
"""

import ecm_parsing
from ecm_parsing import effective_channel_number


def test_override_wins_over_raw():
    assert effective_channel_number(5.0, 7.0) == 7.0


def test_override_zero_wins_over_raw():
    assert effective_channel_number(5.0, 0) == 0.0


def test_override_zero_float_wins_over_raw():
    assert effective_channel_number(5.0, 0.0) == 0.0


def test_override_none_falls_back_to_raw():
    assert effective_channel_number(5.0, None) == 5.0


def test_both_none_gives_none():
    assert effective_channel_number(None, None) is None


def test_int_input_returns_float():
    result = effective_channel_number(3, None)
    assert result == 3.0
    assert isinstance(result, float)


def test_int_override_returns_float():
    result = effective_channel_number(None, 9)
    assert result == 9.0
    assert isinstance(result, float)


def test_is_exported_from_ecm_parsing():
    assert ecm_parsing.effective_channel_number is effective_channel_number
