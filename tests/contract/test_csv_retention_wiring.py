"""The CSV cleanup must run where a CSV is written, and nowhere else.

The decision and the directory wrapper are unit-tested in
tests/unit/test_csv_retention.py. This is the structural test that keeps them
wired in, and that keeps two things from drifting: the Clear CSV Exports button
must go on clearing everything, and both it and the age-based cleanup must agree
about which files belong to this plugin.

plugin.py imports Django at module scope and cannot be imported outside the
container, so its structure is read with ast.
"""

import ast
from pathlib import Path

import ecm_parsing  # resolves via pyproject.toml pythonpath
import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

SETTING = "csv_retention_days"
PRUNE = "ecm_parsing.prune_csv_exports"


def _function(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    pytest.fail(f"plugin.py has no function named {name}")


def _calls_to(node, dotted):
    return [c for c in ast.walk(node)
            if isinstance(c, ast.Call) and ast.unparse(c.func) == dotted]


# --- it runs where the file is written -------------------------------------------

def test_the_export_helper_prunes():
    fn = _function("_export_csv")
    assert _calls_to(fn, PRUNE), (
        "_export_csv must call " + PRUNE + ", which is the one place a CSV is "
        "written and therefore the only place the directory can grow")


def test_nothing_else_prunes():
    """One call site. A second would be a schedule by another name."""
    everywhere = _calls_to(TREE, PRUNE)
    inside = _calls_to(_function("_export_csv"), PRUNE)
    assert len(everywhere) == len(inside) == 1


def test_the_file_just_written_is_protected():
    call = _calls_to(_function("_export_csv"), PRUNE)[0]
    protect = [k.value for k in call.keywords if k.arg == "protect"]
    assert protect, "the prune call must pass protect="
    assert ast.unparse(protect[0]) == "filename"


def test_the_export_helper_takes_the_retention_setting():
    fn = _function("_export_csv")
    names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    assert "retention_days" in names


def test_the_retention_parameter_defaults_to_deleting_nothing():
    """A caller that forgets must delete nothing, not everything."""
    fn = _function("_export_csv")
    defaults = dict(zip([a.arg for a in fn.args.args[-len(fn.args.defaults):]],
                        fn.args.defaults))
    assert "retention_days" in defaults
    assert defaults["retention_days"].value is None


def test_every_caller_passes_the_live_setting():
    """Read from the settings dict the run was given, never from cached state."""
    calls = _calls_to(TREE, "self._export_csv")
    assert calls, "no call to self._export_csv"
    for call in calls:
        passed = [ast.unparse(k.value) for k in call.keywords if k.arg == "retention_days"]
        assert passed == [f"settings.get('{SETTING}')"], (
            f"a call to _export_csv does not pass the live setting: "
            f"{ast.unparse(call)[:120]}")


# --- no separate schedule ---------------------------------------------------------

def test_the_cleanup_has_no_schedule_of_its_own():
    """Files accumulate only when one is written, so pruning there is enough.

    A separate schedule would need its own election and duplicate-fire handling
    for no benefit.
    """
    for node in ast.walk(TREE):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "prune" not in node.value.lower() or "schedule" not in node.value.lower(), (
                f"a scheduled pruning job appears to be declared: {node.value[:80]}")


# --- the Clear CSV Exports button keeps its meaning --------------------------------

def test_the_clear_button_does_not_read_the_retention_setting():
    """Someone pressing it expects everything cleared, not everything old."""
    fn = _function("clear_csv_exports_action")
    assert SETTING not in ast.unparse(fn)
    assert "prune_csv_exports" not in ast.unparse(fn)


def test_the_clear_button_and_the_cleanup_agree_on_which_files_are_ours():
    fn = _function("clear_csv_exports_action")
    rendered = ast.unparse(fn)
    assert "ecm_parsing.CSV_EXPORT_PREFIXES" in rendered
    assert "ecm_parsing.CSV_EXPORT_SUFFIX" in rendered


def test_the_prefixes_cover_both_of_this_plugins_export_names():
    """plugin.py writes two differently named exports; both must be covered."""
    for stem in ("event_channel_managarr_", "epg_removal_"):
        assert stem in ecm_parsing.CSV_EXPORT_PREFIXES
    for node in ast.walk(TREE):
        if isinstance(node, ast.JoinedStr):
            rendered = ast.unparse(node)
            if ".csv" in rendered and "timestamp" in rendered:
                assert rendered.strip("f'\"").startswith(ecm_parsing.CSV_EXPORT_PREFIXES), (
                    f"this plugin writes an export the cleanup cannot see: {rendered}")


# --- the setting is declared in both places ----------------------------------------

def test_the_setting_is_declared_in_the_live_fields_property():
    assert f'"id": "{SETTING}"' in SOURCE


def test_the_setting_is_declared_in_the_manifest():
    assert f'"{SETTING}"' in PLUGIN_JSON.read_text(encoding="utf-8")


def test_the_setting_defaults_to_off_in_the_manifest():
    import json
    fields = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["fields"]
    field = next(f for f in fields if f["id"] == SETTING)
    assert field["type"] == "number"
    assert field["default"] == 0, "0 keeps every file; anything else deletes on upgrade"
