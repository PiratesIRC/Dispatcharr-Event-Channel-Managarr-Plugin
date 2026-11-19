"""The sectioned settings form that Dispatcharr 0.34 and later draws.

Dispatcharr 0.33 and earlier read ``Plugin.fields`` and draw an unknown field
type as a text box, so the flat form must keep only the legacy types. 0.34 calls
``Plugin.get_fields`` instead, which returns the same settings in collapsible
sections. These tests pin that the two forms carry the same settings.
"""

import json
from pathlib import Path

import ecm_fields

LEGACY_TYPES = {"string", "number", "boolean", "select", "text", "info"}
PLUGIN_JSON = Path(__file__).resolve().parents[2] / "Event-Channel-Managarr" / "plugin.json"


def _value_fields(fields):
    return {f["id"]: f for f in fields if f["type"] not in ("info", "section")}


def test_flat_form_uses_only_types_older_dispatcharr_can_draw(bare_plugin):
    assert {f["type"] for f in bare_plugin.fields} <= LEGACY_TYPES
    assert not any("section" in f or "rows" in f for f in bare_plugin.fields)


def test_sectioned_form_keeps_every_setting_with_the_same_type_and_default(bare_plugin):
    flat = _value_fields(bare_plugin.fields)
    sectioned = _value_fields(bare_plugin.get_fields({}))
    assert sectioned.keys() == flat.keys()
    for fid, field in flat.items():
        assert sectioned[fid]["type"] == field["type"], fid
        assert sectioned[fid].get("default") == field.get("default"), fid
        assert sectioned[fid].get("options") == field.get("options"), fid


def test_every_setting_belongs_to_a_declared_section(bare_plugin):
    fields = bare_plugin.get_fields({})
    sections = [f["id"] for f in fields if f["type"] == "section"]
    assert sections == ["_section_scope", "_section_rules", "_section_duplicates", "_section_epg",
                        "_section_scheduling", "_section_advanced", "_section_usage"]
    for f in fields:
        if f["type"] != "section":
            assert f.get("section") in sections, f["id"]


def test_rarely_used_sections_start_collapsed(bare_plugin):
    collapsed = {f["id"]: f["collapsed"] for f in bare_plugin.get_fields({}) if f["type"] == "section"}
    assert [s for s, c in collapsed.items() if c] == [
        "_section_duplicates", "_section_scheduling", "_section_advanced", "_section_usage"]


def test_section_explanations_are_kept_at_the_top_of_their_section(bare_plugin):
    flat_text = {f["id"]: f["description"] for f in bare_plugin.fields if ecm_fields.is_section_header(f)}
    fields = bare_plugin.get_fields({})
    for i, f in enumerate(fields):
        if f["type"] == "section":
            about = fields[i + 1]
            assert about["id"] == f["id"] + "_about"
            assert about["type"] == "info" and about["section"] == f["id"]
            assert about["description"] == flat_text[f["id"]]


def test_short_text_fields_get_a_smaller_starting_height(bare_plugin):
    rows = {f["id"]: f.get("rows") for f in bare_plugin.get_fields({}) if f["type"] == "text"}
    assert rows["hide_rules_priority"] == 3
    assert rows["regex_force_visible"] == 2
    assert rows["placeholder_guide_titles"] == 2
    assert rows["group_epg_source_map"] is None


def test_fields_before_the_first_header_stay_ungrouped_and_input_is_not_modified():
    flat = [{"id": "a", "type": "string"},
            {"id": "_section_x", "type": "info", "label": "X"},
            {"id": "regex_force_visible", "type": "text"}]
    before = json.dumps(flat)
    out = ecm_fields.sectioned_fields(flat)
    assert json.dumps(flat) == before
    assert out[0] == {"id": "a", "type": "string"}
    assert out[1] == {"id": "_section_x", "label": "X", "type": "section", "collapsed": False}
    assert out[2] == {"id": "regex_force_visible", "type": "text", "section": "_section_x", "rows": 2}


def test_get_fields_falls_back_to_the_flat_form_when_the_layout_fails(bare_plugin, monkeypatch):
    def boom(_fields):
        raise RuntimeError("layout failed")
    monkeypatch.setattr(ecm_fields, "sectioned_fields", boom)
    assert bare_plugin.get_fields({}) == bare_plugin.fields


def test_manifest_declares_version_1_and_the_capabilities_the_plugin_uses():
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 1
    assert manifest["capabilities"] == ["outbound_network", "filesystem_write"]
    # The sectioned form is reached through get_fields, so older Dispatcharr keeps working.
    assert manifest["min_dispatcharr_version"] == "v0.20.0"
