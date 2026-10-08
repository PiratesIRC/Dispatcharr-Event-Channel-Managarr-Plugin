"""The duplicate rules and the CSV must read the override-aware channel number.

Dispatcharr's UI shows ChannelOverride.channel_number in place of the raw number. Every
channel_info builder in the scan must go through Plugin._channel_number_for, and the
queryset that loads those channels must select the reverse one-to-one "override", or
each channel costs one extra query. The structural half reads plugin.py with ast; the
behavioural half calls the helper on stand-in channels, which needs no database.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _method(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"plugin.py has no method {name}")


def test_no_builder_reads_the_raw_channel_number_directly():
    scan = ast.unparse(_method("_scan_and_update_channels"))
    assert "float(channel.channel_number)" not in scan, (
        "channel_info builders must use _channel_number_for, not the raw number")


def test_the_four_builders_call_the_helper():
    scan = _method("_scan_and_update_channels")
    calls = [
        node for node in ast.walk(scan)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_channel_number_for"
    ]
    assert len(calls) == 4, f"expected 4 builder call sites, found {len(calls)}"


def test_the_channels_queryset_selects_the_override():
    scan = _method("_scan_and_update_channels")
    selected = []
    for node in ast.walk(scan):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "select_related"):
            selected.extend(
                arg.value for arg in node.args if isinstance(arg, ast.Constant))
    assert "override" in selected, (
        "the queryset feeding the scan must select_related('override')")


def _channel(channel_number, override=None, has_override_attr=True):
    channel = SimpleNamespace(channel_number=channel_number)
    if has_override_attr:
        channel.override = override
    return channel


class _RaisingOverride:
    @property
    def override(self):
        raise AttributeError("no reverse one-to-one row")


@pytest.mark.parametrize("channel, expected", [
    (_channel(5.0, has_override_attr=False), 5.0),
    (_channel(5.0, override=None), 5.0),
    (_channel(5.0, override=SimpleNamespace(channel_number=None)), 5.0),
    (_channel(5.0, override=SimpleNamespace(channel_number=7.0)), 7.0),
    (_channel(5.0, override=SimpleNamespace(channel_number=0)), 0.0),
    (_channel(None, override=SimpleNamespace(channel_number=3.0)), 3.0),
    (_channel(0, override=None), None),
    (_channel(None, override=None), None),
])
def test_helper_returns_the_effective_number(bare_plugin, channel, expected):
    assert bare_plugin._channel_number_for(channel) == expected


def test_helper_treats_a_missing_reverse_row_as_no_override(bare_plugin):
    channel = _RaisingOverride()
    channel.channel_number = 5.0
    assert bare_plugin._channel_number_for(channel) == 5.0


def test_helper_falls_back_to_raw_when_override_access_faults(bare_plugin):
    class _Broken:
        @property
        def override(self):
            raise RuntimeError("database went away")

    channel = _Broken()
    channel.channel_number = 5.0
    assert bare_plugin._channel_number_for(channel) == 5.0
