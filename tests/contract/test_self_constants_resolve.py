"""Every `self.SOME_CONSTANT` inside Plugin must actually be defined on Plugin.

The file-path constants in this module live on `PluginConfig`, not on `Plugin`,
and every method that needs one reaches it as `PluginConfig.EXPORTS_DIR`. Writing
`self.EXPORTS_DIR` instead raises AttributeError at run time.

That is not hypothetical. `_append_ledger_entry` shipped reading
`self.LEDGER_FILE`, which raised on every applied run that changed a channel. The
method catches Exception on purpose, so it can never fail a scan, which meant the
failure surfaced only as a WARNING in the container log: the run ledger stayed
empty, the public badge stayed at 0, and every other signal looked healthy. A
fail-safe hides its own bugs, so the bug has to be caught here instead.

plugin.py imports Django at module scope and cannot be imported outside the
container, so the classes are read with ast.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"

TREE = ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))


def _class_named(name):
    for node in TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    pytest.fail(f"plugin.py no longer defines class {name}")


def _class_level_constants(cls):
    """UPPER_CASE names assigned directly in the class body."""
    names = set()
    for stmt in cls.body:
        targets = []
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id.isupper():
                names.add(t.id)
    return names


def _self_constant_reads(cls):
    """Every `self.UPPER_CASE` read anywhere in the class, with its line number."""
    found = []
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr.isupper()):
            found.append((node.attr, node.lineno))
    return found


PLUGIN = _class_named("Plugin")
PLUGIN_CONSTANTS = _class_level_constants(PLUGIN)


def test_plugin_defines_some_constants_of_its_own():
    """Guard against the check below passing because nothing was collected."""
    assert PLUGIN_CONSTANTS, (
        "no class-level constants were found on Plugin, so the resolution check "
        "below would pass vacuously"
    )


def test_every_self_constant_in_plugin_resolves():
    unresolved = [
        f"self.{name} at plugin.py:{line}"
        for name, line in _self_constant_reads(PLUGIN)
        if name not in PLUGIN_CONSTANTS
    ]
    assert not unresolved, (
        "these read a constant that is not defined on Plugin, so they raise "
        "AttributeError at run time: " + "; ".join(unresolved) +
        ". Constants such as the file paths live on PluginConfig; reach them as "
        "PluginConfig.NAME."
    )


# ---------------------------------------------------------------------------
# The two the run ledger needs, named explicitly so a rename is caught here
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["LEDGER_FILE", "LEDGER_MAX_BYTES"])
def test_ledger_constants_live_on_pluginconfig(name):
    assert name in _class_level_constants(_class_named("PluginConfig")), (
        f"{name} is no longer defined on PluginConfig, where the ledger writer "
        f"looks for it"
    )


def test_the_ledger_writer_reaches_them_through_pluginconfig():
    fn = next(
        (n for n in ast.walk(PLUGIN)
         if isinstance(n, ast.FunctionDef) and n.name == "_append_ledger_entry"),
        None,
    )
    assert fn is not None, "plugin.py no longer defines _append_ledger_entry()"
    body = ast.unparse(fn)
    assert "PluginConfig.LEDGER_FILE" in body
    assert "self.LEDGER_FILE" not in body, (
        "the ledger writer is reading self.LEDGER_FILE again; that raises "
        "AttributeError and the writer's own except-Exception hides it"
    )
