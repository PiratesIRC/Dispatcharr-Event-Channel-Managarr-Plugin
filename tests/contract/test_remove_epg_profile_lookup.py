"""remove_epg_from_hidden_action must match profile names case-insensitively.

The scan path looks profiles up with name__iexact, so a profile configured as
"all" works there. This action once used a bare name= lookup, which fails for
the same configuration. plugin.py imports Django at module scope and cannot be
imported outside the container, so the method is located and inspected with ast.
"""

import ast
from pathlib import Path

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


def _channel_profile_lookups(func):
    """Yield every call of the form ChannelProfile.objects.<method>(...)."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if not isinstance(callee, ast.Attribute):
            continue
        manager = callee.value
        if (isinstance(manager, ast.Attribute)
                and manager.attr == "objects"
                and isinstance(manager.value, ast.Name)
                and manager.value.id == "ChannelProfile"):
            yield node


def test_the_method_performs_channel_profile_lookups():
    lookups = list(_channel_profile_lookups(_method("remove_epg_from_hidden_action")))
    assert lookups, "the scan of the method found no ChannelProfile lookup"


def test_no_channel_profile_lookup_uses_a_case_sensitive_name_keyword():
    lookups = list(_channel_profile_lookups(_method("remove_epg_from_hidden_action")))
    bare = [
        ast.unparse(call) for call in lookups
        if any(kw.arg == "name" for kw in call.keywords)
    ]
    assert not bare, f"case-sensitive ChannelProfile lookup(s): {bare}"


def test_channel_profile_lookup_is_case_insensitive():
    lookups = list(_channel_profile_lookups(_method("remove_epg_from_hidden_action")))
    iexact = [
        call for call in lookups
        if any(kw.arg == "name__iexact" for kw in call.keywords)
    ]
    assert iexact, "expected a ChannelProfile lookup using name__iexact"
