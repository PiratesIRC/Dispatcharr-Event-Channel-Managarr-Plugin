"""Contract tests for the per-group managed EPG sources (issue 29).

WHAT THESE CAN AND CANNOT DO. plugin.py imports Django at module scope and cannot
be imported outside the Dispatcharr container, so everything here parses the source
with ast and asserts on its STRUCTURE. A structural assertion cannot tell a correct
comparison from an inverted one.

That is why the decisions themselves are pure functions in ecm_profiles.py, covered
by real behavioural tests in tests/unit/. These tests hold the WIRING: if plugin.py
stops calling one of those functions, the unit coverage silently stops applying to
the shipped behaviour and nothing else would report it. The same split, and the same
reasoning, as tests/contract/test_undated_ended_rule.py.

A specific trap this file must avoid: the existing tests for the rebinding method are
string searches over that whole method's source, and it already contains the names
they search for. Any new assertion of the form `assert "x" in src` over that method
is pre-satisfied and proves nothing. Assertions here are therefore about CALLS TO THE
PURE FUNCTIONS and about the ORDER of statements, not about the presence of names the
method already carried.
"""

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"
SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _fn(name):
    node = next((n for n in ast.walk(TREE)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)
    assert node is not None, f"plugin.py has no method named {name}"
    return node


def _src(name):
    return ast.get_source_segment(SOURCE, _fn(name))


def _calls(name):
    """Every attribute or bare name called inside `name`."""
    out = set()
    for node in ast.walk(_fn(name)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


# --- the wiring: plugin.py must call the pure decisions ------------------------


def test_the_reroute_asks_the_pure_function_where_each_channel_belongs():
    """Without this call, every unit test of routing_destinations is decoration."""
    assert "routing_destinations" in _calls("_reroute_claimed_channels")


def test_the_reroute_builds_its_input_from_the_group_profiles():
    assert "build_group_profiles" in _calls("_reroute_claimed_channels")


def test_the_reroute_selects_the_channel_group_in_its_query():
    """Without channel_group on the query, the group is fetched one row at a time,
    or arrives as None and every group mapping silently routes nothing."""
    src = _src("_reroute_claimed_channels")
    assert "select_related(" in src
    assert '"channel_group"' in src


def test_the_reroute_reads_the_record_of_sources_the_plugin_created():
    """That record is the only thing stopping the reverse move taking a channel off
    a source the operator built by hand."""
    assert "_load_group_source_record" in _calls("_reroute_claimed_channels")


def test_the_ownership_decision_is_delegated_to_the_pure_function():
    """An inverted comparison here would freeze the shared source and rewrite every
    mapped one. A structural test cannot see that, so the decision must not live in
    plugin.py at all."""
    assert "source_props_to_write" in _calls("_ensure_profile_source")


def test_the_ensure_step_records_a_source_it_creates_for_a_mapping():
    assert "_record_group_source" in _calls("_ensure_profile_source")


def test_the_ensure_step_no_longer_compares_property_keys_itself():
    """The old inline refresh loop must be gone, not merely bypassed.

    If it were left in place beside the new call, a future edit could revive it and
    both would be true at once, with the inline copy winning.
    """
    src = _src("_ensure_profile_source")
    assert '"title_pattern", "time_pattern", "date_pattern"' not in src, (
        "the inline pattern-key skip list is still here; the comparison belongs in "
        "ecm_profiles.source_props_to_write")


# --- the record file -----------------------------------------------------------


def test_the_record_file_is_declared_and_lives_under_data():
    assert "GROUP_SOURCE_RECORD_FILE" in SOURCE
    node = next(n for n in ast.walk(TREE)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "GROUP_SOURCE_RECORD_FILE"
                        for t in n.targets))
    assert node.value.value.startswith("/data/"), (
        "the record must be on the persistent volume, not beside the plugin code, "
        "which is replaced on every deploy")


def test_the_record_is_not_stored_in_the_epg_source_properties():
    """Dispatcharr's own EPG source editor rebuilds custom_properties from a fixed
    key list and drops anything else, and a mapped source is never rewritten, so a
    marker stored there would be deleted by the very edit this feature exists for."""
    assert "ecm_group_mapped" not in SOURCE, (
        "a marker key in custom_properties cannot survive Dispatcharr's source editor")


@pytest.mark.parametrize("helper", [
    "_load_group_source_record", "_save_group_source_record", "_record_group_source"])
def test_the_record_helpers_exist(helper):
    _fn(helper)


def test_loading_the_record_fails_closed():
    """Any failure must yield an empty record, which makes every channel ineligible
    for the reverse move, so a lost file strands channels rather than moving them."""
    src = _src("_load_group_source_record")
    assert "FileNotFoundError" in src
    assert "JSONDecodeError" in src
    assert src.count("return {}") >= 3, (
        "each failure path must return an empty record, not raise or return None")


# --- source creation for a group with no channels yet --------------------------


def test_source_pre_creation_happens_only_on_an_applied_run_with_the_toggle_on():
    """Two separate hazards.

    A dry run must never create a database row: that is the stated contract of
    _run_managed_epg_pass. And the pre-creation must sit INSIDE the manage_dummy_epg
    check, or an operator who has the managed EPG feature switched off would still
    get EPG sources created for them.
    """
    fn = _fn("_run_managed_epg_pass")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_precreate_mapped_sources"]
    assert calls, "no pre-creation call found in _run_managed_epg_pass"

    dry_returns = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Return)]
    first_return = min(dry_returns)
    assert all(c.lineno > first_return for c in calls), (
        "pre-creation must come after the dry-run early return, or a dry run writes")


def test_pre_creation_is_guarded_by_the_master_toggle():
    src = _src("_run_managed_epg_pass")
    idx = src.index("_precreate_mapped_sources")
    before = src[:idx]
    assert "toggle_on" in before.rsplit("if ", 1)[-1] or "if toggle_on:" in before, (
        "pre-creation must sit inside the manage_dummy_epg branch")


# --- the setting ---------------------------------------------------------------


def test_the_setting_is_declared_in_both_the_code_and_the_manifest():
    """Dispatcharr serves the LIVE fields property from plugin.py, so a setting
    missing there is missing in production even if the manifest declares it.

    tests/contract/test_manifest_parity.py covers actions, events and version only.
    It has no field parity assertion at all, so this is the whole coupling.
    """
    assert "group_epg_source_map" in SOURCE
    ids = [f["id"] for f in json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["fields"]]
    assert "group_epg_source_map" in ids


def test_the_setting_is_recorded_in_the_csv_header():
    """A CSV that cannot show the mapping cannot explain why a channel moved.

    The requirement is unchanged. On 2026-09-05 the mechanism moved: the report
    preamble was built from a hand-maintained `settings_keys` list inside
    plugin.py, which had drifted and omitted five settings, and it is now built
    from ecm_parsing.SETTINGS_REPORT, which is unit-tested and carries the
    interface label for each setting.
    """
    import ecm_parsing
    reported = {sid for sid, _label, _kind in ecm_parsing.SETTINGS_REPORT}
    assert "group_epg_source_map" in reported


def test_validate_configuration_shows_the_mapping_the_plugin_actually_read():
    """The operator cannot otherwise tell which of two settings stores won.

    Measured in plugin.py's `run`: the merged settings start from the values cached
    on disk and are then overlaid with whatever the form sent, so a field the form
    OMITS keeps its saved value automatically and needs no preservation code. That
    is the right default, since Dispatcharr does not reliably send a field the
    operator has cleared and acting on an ambiguous blank would rebind channels.

    The cost is that clearing the box may not appear to take effect, so Validate
    Configuration must print the mapping the plugin will actually use. Separately,
    scheduled runs reload settings from the disk file, which only an action button
    writes, so this action is also what arms a new mapping for an unattended run.
    """
    assert "parse_group_source_map" in _calls("validate_configuration_action")


def test_the_csv_names_the_epg_source_each_channel_is_bound_to():
    """Without this column the export can say a channel HAS a guide but not which
    source serves it, which is the one thing a reader needs when a group mapping
    moved something unexpectedly."""
    # plugin.py declares fieldnames for TWO different exports. Select the scan
    # export by a column only it has, rather than taking whichever ast.walk
    # reaches first, which is the EPG-cleanup export and needs no such column.
    candidates = [ast.literal_eval(n.value) for n in ast.walk(TREE)
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "fieldnames"
                          for t in n.targets)]
    scan_export = next(c for c in candidates if "has_epg" in c)
    assert "epg_source" in scan_export


def test_every_csv_row_supplies_the_epg_source_column():
    """csv.DictWriter raises on a row missing a declared field, so a row built at
    one of the early-exit paths and lacking this key would abort the export."""
    assert SOURCE.count('"epg_source"') + SOURCE.count("'epg_source'") >= 5, (
        "expected the column plus a value at each row-construction site")
