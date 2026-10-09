"""Channel Name Format "AT" reaches the managed dummy EPG source.

Runs the real Plugin._get_or_create_managed_epg_source against a stand-in for
Dispatcharr's EPGSource model and reads what it writes, so the test asserts on the
properties the renderer will be handed rather than on the method's source text.
The patterns themselves are tested in tests/unit/test_at_channel_format.py.
"""

import logging
import sys

import ecm_parsing
import pytest

AT_PATTERNS = {
    "title_pattern": ecm_parsing.AT_TITLE_PATTERN,
    "time_pattern": ecm_parsing.AT_TIME_PATTERN,
    "date_pattern": ecm_parsing.AT_DATE_PATTERN,
}


class _Source:
    def __init__(self, props):
        self.id = 18
        self.source_type = "dummy"
        self.custom_properties = props
        self.saved = False

    def save(self, update_fields=None):
        self.saved = True


class _Manager:
    def __init__(self, existing):
        self.existing = existing

    def get_or_create(self, name, defaults):
        if self.existing is None:
            return _Source(dict(defaults["custom_properties"])), True
        return self.existing, False


@pytest.fixture
def run(bare_plugin, monkeypatch):
    monkeypatch.setattr(bare_plugin, "_get_system_timezone",
                        lambda settings: "America/Chicago", raising=False)

    def _run(channel_format, existing=None):
        epg_source = type("EPGSource", (), {"objects": _Manager(existing)})
        monkeypatch.setattr(sys.modules["apps.epg.models"], "EPGSource", epg_source,
                            raising=False)
        settings = {"dummy_epg_channel_format": channel_format,
                    "dummy_epg_event_timezone": "America/New_York"}
        return bare_plugin._get_or_create_managed_epg_source(settings, logging.getLogger("t"))
    return _run


def _patterns(source):
    return {k: source.custom_properties.get(k) for k in AT_PATTERNS}


@pytest.mark.parametrize("value", ["AT", "at", " At "])
def test_a_new_source_gets_the_at_patterns(run, value):
    source = run(value)
    assert _patterns(source) == AT_PATTERNS
    # AT names carry AM/PM, so the 12-hour placeholders apply, as for US.
    assert "{starttime}" in source.custom_properties["upcoming_title_template"]
    assert "{endtime}" in source.custom_properties["ended_title_template"]


def test_switching_a_stock_us_source_to_at_rewrites_its_patterns(run):
    us_source = run("US")
    assert _patterns(us_source) != AT_PATTERNS
    switched = run("AT", existing=_Source(dict(us_source.custom_properties)))
    assert switched.saved
    assert _patterns(switched) == AT_PATTERNS


def test_switching_back_from_at_to_us_rewrites_its_patterns(run):
    # The AT patterns must be listed as plugin defaults, or a source that once ran
    # with AT would keep them for ever after the operator chose US again (issue #21).
    us_patterns = _patterns(run("US"))
    at_source = run("AT")
    back = run("US", existing=_Source(dict(at_source.custom_properties)))
    assert _patterns(back) == us_patterns


def test_a_pattern_the_operator_wrote_is_kept(run):
    custom = dict(run("US").custom_properties, title_pattern=r"(?<title>.+)")
    kept = run("AT", existing=_Source(custom))
    assert kept.custom_properties["title_pattern"] == r"(?<title>.+)"
    assert kept.custom_properties["time_pattern"] == ecm_parsing.AT_TIME_PATTERN
