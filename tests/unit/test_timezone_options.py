"""The Channel Name Event Timezone dropdown must offer the setting's own default.

The options come from zone1970.tab, which lists canonical names only, so the
default "US/Eastern" was not selectable (measured 2026-11-14: zone1970.tab in
the container has no US/Eastern line).
"""

from ecm_parsing import timezone_options_with


def _opts(*names):
    return [{"label": n, "value": n} for n in names]


def test_missing_default_is_added_and_list_stays_sorted():
    result = timezone_options_with(
        _opts("America/Chicago", "America/New_York", "Europe/London"), "US/Eastern")
    assert [o["value"] for o in result] == [
        "America/Chicago", "America/New_York", "Europe/London", "US/Eastern"]
    assert {"label": "US/Eastern", "value": "US/Eastern"} in result


def test_present_value_is_not_duplicated():
    result = timezone_options_with(_opts("America/New_York", "US/Eastern"), "US/Eastern")
    assert [o["value"] for o in result].count("US/Eastern") == 1


def test_blank_and_non_string_values_are_ignored():
    base = _opts("America/New_York")
    assert timezone_options_with(base, "", "  ", None, 5) == base


def test_input_list_is_not_mutated():
    base = _opts("America/New_York")
    timezone_options_with(base, "US/Eastern")
    assert base == _opts("America/New_York")
