"""Each profile-lookup path must offer the built-in "All" hint.

plugin.py imports Django at module scope and cannot be imported outside the container,
so each method is located with ast and checked for a call to
ecm_parsing.builtin_all_profile_hint. The check is scoped to the METHOD, not the file,
so a call elsewhere in plugin.py cannot satisfy it.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

WIRED_METHODS = [
    "validate_configuration_action",
    "_scan_and_update_channels",
    "remove_epg_from_hidden_action",
]


def _method(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"plugin.py has no method {name}")


def _hint_calls(func):
    """Yield every call of the form ecm_parsing.builtin_all_profile_hint(...)."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if (isinstance(callee, ast.Attribute)
                and callee.attr == "builtin_all_profile_hint"
                and isinstance(callee.value, ast.Name)
                and callee.value.id == "ecm_parsing"):
            yield node


@pytest.mark.parametrize("name", WIRED_METHODS)
def test_method_calls_builtin_all_profile_hint(name):
    calls = list(_hint_calls(_method(name)))
    assert calls, f"{name} does not call ecm_parsing.builtin_all_profile_hint"
