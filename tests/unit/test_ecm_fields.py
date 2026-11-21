"""The sectioned settings form that Dispatcharr 0.34 and later draws.

Dispatcharr 0.33 and earlier read ``Plugin.fields`` and draw an unknown field
type as a text box, so the flat form must keep only the legacy types. 0.34 calls
``Plugin.get_fields`` instead, which returns the same settings in collapsible
sections. These tests pin that the two forms carry the same settings.
"""

import json
import logging
from pathlib import Path

import ecm_fields
import pytest

LEGACY_TYPES = {"string", "number", "boolean", "select", "text", "info"}
PLUGIN_JSON = Path(__file__).resolve().parents[2] / "Event-Channel-Managarr" / "plugin.json"


def _value_fields(fields):
    return {f["id"]: f for f in fields if f["type"] not in ("info", "section")}


def test_flat_form_uses_only_types_older_dispatcharr_can_draw(bare_plugin):
    assert {f["type"] for f in bare_plugin.fields} <= LEGACY_TYPES
    assert not any("section" in f or "rows" in f for f in bare_plugin.fields)


@pytest.fixture
def names(bare_plugin, monkeypatch):
    monkeypatch.setattr(type(bare_plugin), "_pick_list_names",
                        staticmethod(lambda: (["Default"], ["US: PPV", "US: NFL", "Sports"])))


def test_sectioned_form_keeps_every_other_setting_with_the_same_type_and_default(bare_plugin, names):
    flat = _value_fields(bare_plugin.fields)
    sectioned = _value_fields(bare_plugin.get_fields({}))
    replaced = set(ecm_fields.NEW_ID_BY_OLD_ID)
    assert sectioned.keys() == (flat.keys() - replaced) | set(ecm_fields.NEW_ID_BY_OLD_ID.values())
    for fid, field in flat.items():
        if fid in replaced:
            continue
        assert sectioned[fid]["type"] == field["type"], fid
        assert sectioned[fid].get("default") == field.get("default"), fid
        assert sectioned[fid].get("options") == field.get("options"), fid


def test_pick_lists_default_to_the_saved_text_settings(bare_plugin, names):
    saved = {
        "channel_profile_name": "default",
        "channel_groups": "us: ppv, US: NFL, Gone Group",
        "stream_name_groups": "US: NFL",
        "scheduled_times": "0400,1000,0617",
        "group_default_event_day": "US: NFL = sun\nbroken line",
        "group_epg_source_map": "US: NFL = ECM NFL = Dummy",
    }
    fields = {f["id"]: f for f in bare_plugin.get_fields({"settings": saved})}
    assert fields["channel_profile_list"]["type"] == "multiselect"
    assert fields["channel_profile_list"]["default"] == ["Default"]
    groups = fields["channel_group_list"]
    assert groups["default"] == ["US: PPV", "US: NFL", "Gone Group"]
    # A saved name that no longer exists stays an option, or Dispatcharr rejects the save.
    assert {o["value"] for o in groups["options"]} == {"US: PPV", "US: NFL", "Sports", "Gone Group"}
    times = fields["scheduled_time_list"]
    assert times["default"] == ["0400", "1000", "0617"]
    values = [o["value"] for o in times["options"]]
    assert "0617" in values and "2355" in values and len(values) == 24 * 12 + 1
    days = fields["group_default_event_day_rows"]
    assert days["type"] == "table"
    assert days["default"] == [{"group": "US: NFL", "day": "Sunday"},
                               {"group": "broken line", "day": ""}]
    assert [o["value"] for o in days["columns"][1]["options"]][:7] == list(ecm_fields.WEEKDAY_NAMES)
    sources = fields["group_epg_source_rows"]
    assert sources["default"] == [{"group": "US: NFL", "source": "ECM NFL = Dummy"}]
    assert sources["columns"][1]["type"] == "string"
    # A table draws no help text, so an info field above it carries the explanation.
    assert fields["group_epg_source_rows_about"]["description"].startswith("Gives a channel group")
    assert sources["label"] == ""


def test_legacy_settings_writes_lists_and_rows_back_into_the_text_settings():
    out = ecm_fields.legacy_settings({
        "channel_profile_list": ["Default"],
        "channel_group_list": [],
        "stream_name_group_list": ["US: NFL"],
        "scheduled_time_list": ["0400", "1300"],
        "group_default_event_day_rows": [{"group": "US: NFL", "day": "Sunday"}, {}, {"group": "X"}],
        "group_epg_source_rows": [{"group": "US: NFL", "source": "ECM = NFL"}],
        "channel_profile_name": "stale",
    })
    assert out["channel_profile_name"] == "Default"
    assert out["channel_groups"] == ""  # an emptied pick list means every group
    assert out["stream_name_groups"] == "US: NFL"
    assert out["scheduled_times"] == "0400,1300"
    assert out["group_default_event_day"] == "US: NFL = Sunday\nX = "
    assert out["group_epg_source_map"] == "US: NFL = ECM = NFL"


def test_legacy_settings_ignores_a_new_id_that_is_not_a_list():
    out = ecm_fields.legacy_settings({"channel_group_list": "oops", "channel_groups": "A"})
    assert out["channel_groups"] == "A"


def test_round_trip_through_the_table_keeps_what_the_parsers_read():
    import ecm_parsing
    import ecm_profiles
    text = "US: NFL = Sunday\nUS: PPV = sat"
    rows = ecm_fields.rows_from_lines(text, "group", "day")
    assert ecm_parsing.parse_group_default_days(ecm_fields.lines_from_rows(rows, "group", "day")) == \
        ecm_parsing.parse_group_default_days(text)
    src = "US: NFL = ECM NFL Dummy"
    rows = ecm_fields.rows_from_lines(src, "group", "source")
    assert ecm_profiles.parse_group_source_map(ecm_fields.lines_from_rows(rows, "group", "source")) == \
        ecm_profiles.parse_group_source_map(src)


def test_run_translates_pick_lists_only_after_get_fields_built_them(bare_plugin, names, monkeypatch):
    seen = {}

    def fake_call(action, handler, settings, logger):
        seen.update(settings)
        return {"status": "success", "message": "ok"}

    monkeypatch.setattr(bare_plugin, "_call_action", fake_call, raising=False)
    monkeypatch.setattr(bare_plugin, "saved_settings", {}, raising=False)
    live = {"channel_group_list": ["US: PPV"], "channel_groups": "Old"}
    bare_plugin.run("check_scheduler_status", {}, {"settings": dict(live)})
    assert seen["channel_groups"] == "Old"  # 0.33: get_fields never ran
    bare_plugin.get_fields({})
    bare_plugin.run("check_scheduler_status", {}, {"settings": dict(live)})
    assert seen["channel_groups"] == "US: PPV"


def test_m3u_rescan_history_names_the_csv_by_full_path(bare_plugin, monkeypatch):
    scan = {"status": "success", "message": "long",
            "results": {"total_channels": 3, "csv_file": "/data/exports/x.csv"}}
    monkeypatch.setattr(bare_plugin, "_scan_and_update_channels",
                        lambda *a, **k: dict(scan, results=dict(scan["results"])), raising=False)
    result = bare_plugin.on_m3u_refresh_action(
        {"event": "m3u_refresh", "auto_rescan_on_m3u_refresh": True}, logging.getLogger("t"))
    assert "CSV: /data/exports/x.csv" in result["message"]
    assert result["file"] == "/data/exports/x.csv"
    scan["results"]["csv_file"] = "N/A"
    result = bare_plugin.on_m3u_refresh_action(
        {"event": "m3u_refresh", "auto_rescan_on_m3u_refresh": True}, logging.getLogger("t"))
    assert "file" not in result and "CSV" not in result["message"]


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
    assert rows["regex_force_visible"] == 2
    assert rows["placeholder_guide_titles"] == 2
    assert rows["regex_channels_to_ignore"] == 2
    # The seven settings drawn as pick lists and tables are no longer text fields.
    assert not set(rows) & set(ecm_fields.NEW_ID_BY_OLD_ID)


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


def test_long_help_texts_are_split_into_titled_paragraphs_below_the_field(bare_plugin, names):
    fields = bare_plugin.get_fields({})
    ids = [f["id"] for f in fields]
    for fid, (short, paragraphs) in ecm_fields.HELP_PARAGRAPHS.items():
        assert fid in ids, fid
        at = ids.index(fid)
        field = fields[at]
        assert (field.get("help_text") or field.get("description")) == short
        follow = fields[at + 1: at + 1 + len(paragraphs)]
        assert [(f["label"], f["description"]) for f in follow] == paragraphs
        assert all(f["type"] == "info" and f["section"] == field["section"] for f in follow)


def test_every_tag_the_flat_help_names_is_offered_in_the_hide_rules_table(bare_plugin, names):
    import re
    full = next(f for f in bare_plugin.fields if f["id"] == "hide_rules_priority")["help_text"]
    named = set(re.findall(r"\[([A-Za-z]+)(?::[a-zA-Z0-9]+)*\]", full))
    table = {f["id"]: f for f in bare_plugin.get_fields({})}["hide_rule_rows"]
    offered = {o["value"] for o in table["columns"][0]["options"]}
    assert named and named == {name for name, _ in ecm_fields.HIDE_RULE_TAGS} == offered


def test_the_flat_form_keeps_the_full_help_text(bare_plugin):
    full = next(f for f in bare_plugin.fields if f["id"] == "hide_rules_priority")["help_text"]
    assert len(full) > len(ecm_fields.HELP_PARAGRAPHS["hide_rule_rows_about"][0]) * 3


def test_no_help_text_claims_the_first_rule_decides_whether_a_channel_hides(bare_plugin):
    full = next(f for f in bare_plugin.fields if f["id"] == "hide_rules_priority")["help_text"]
    short, paragraphs = ecm_fields.HELP_PARAGRAPHS["hide_rule_rows_about"]
    for text in [full, short] + [p for _, p in paragraphs]:
        assert "trust most" not in text
        assert "first tag that matches hides" not in text


# The hide rules table on Dispatcharr 0.34.

DEFAULT_RULES = "[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedEnded],[UndatedAge:2],[ShortDescription],[ShortChannelName]"


def _parsed(bare_plugin, text):
    return bare_plugin._parse_hide_rules(text, logging.getLogger("test"))


@pytest.mark.parametrize("text", [
    DEFAULT_RULES,
    "[PastDate:0:4h],[FutureDate:7],[PreEvent:30],[UndatedEnded:3],[NoEPG]",
    "[WrongDayOfWeek:0], [ShortDescription:20] ,[ShortChannelName:40]",
    "[BlankName]\n[PastDate:1]\n[NumberOnly]",  # the old newline form
    "[FutureDate:x],[PastDate:1:2:3],[Unknown:5],junk,[InactiveRegex]",  # what the parser skips stays skipped
])
def test_rules_written_back_from_the_table_parse_the_same_as_the_text(bare_plugin, text):
    rows = ecm_fields.rows_from_hide_rules(text)
    assert _parsed(bare_plugin, ecm_fields.hide_rules_from_rows(rows)) == _parsed(bare_plugin, text)


def test_hide_rule_rows_keep_order_number_and_grace_hours():
    rows = ecm_fields.rows_from_hide_rules("[InactiveRegex],[PastDate:0:4h],[FutureDate:2],junk")
    assert rows == [
        {"rule": "InactiveRegex", "number": "default", "hours": "default"},
        {"rule": "PastDate", "number": "0", "hours": "4"},
        {"rule": "FutureDate", "number": "2", "hours": "default"},
    ]


def test_hide_rules_from_rows_drops_what_a_rule_cannot_use():
    rows = [
        {"rule": "PastDate", "number": "default", "hours": "6"},  # hours with no days: days 0
        {"rule": "FutureDate", "number": "3", "hours": "6"},  # hours only for PastDate
        {"rule": "NoEPG", "number": "5"},  # takes no number
        {"rule": "UndatedAge"},  # a row added in the form has no number keys
        {"rule": "ShortDescription", "number": None, "hours": None},
        {"rule": "", "number": "4"},  # no rule: dropped
        {},
    ]
    assert ecm_fields.hide_rules_from_rows(rows) == (
        "[PastDate:0:6h],[FutureDate:3],[NoEPG],[UndatedAge],[ShortDescription]")
    assert ecm_fields.hide_rules_from_rows([]) == ""  # the parser then applies the defaults


def test_hide_rules_table_defaults_to_the_saved_text(bare_plugin, names):
    fields = {f["id"]: f for f in bare_plugin.get_fields(
        {"settings": {"hide_rules_priority": "[PastDate:1:3h],[Custom:9],[FutureDate:999]"}})}
    assert "hide_rules_priority" not in fields
    table = fields["hide_rule_rows"]
    assert table["type"] == "table" and table["label"] == ""
    assert table["default"] == [
        {"rule": "PastDate", "number": "1", "hours": "3"},
        {"rule": "Custom", "number": "9", "hours": "default"},
        {"rule": "FutureDate", "number": "999", "hours": "default"},
    ]
    rule, number, hours = table["columns"]
    assert [c["type"] for c in table["columns"]] == ["select", "select", "select"]
    # A saved value that is not offered is added, or Dispatcharr refuses the save.
    assert "Custom" in {o["value"] for o in rule["options"]}
    assert {o["label"] for o in rule["options"]} >= {"[PastDate] (days)", "[NoEPG]"}
    number_values = [o["value"] for o in number["options"]]
    assert number_values[0] == "default" and "999" in number_values and "1440" in number_values
    assert number_values.index("60") < number_values.index("999")
    assert [o["value"] for o in hours["options"]][:3] == ["default", "0", "1"]
    about = fields["hide_rule_rows_about"]
    assert about["type"] == "info" and about["label"].endswith("Hide Rules Priority")


def test_a_blank_saved_text_shows_the_default_rules(bare_plugin, names):
    table = {f["id"]: f for f in bare_plugin.get_fields(
        {"settings": {"hide_rules_priority": "  "}})}["hide_rule_rows"]
    assert ecm_fields.hide_rules_from_rows(table["default"]) == bare_plugin.DEFAULT_HIDE_RULES


def test_a_row_saved_in_the_table_is_offered_in_its_pick_lists(bare_plugin, names):
    table = {f["id"]: f for f in bare_plugin.get_fields({"settings": {
        "hide_rule_rows": [{"rule": "Gone", "number": "7777", "hours": "500"}]}})}["hide_rule_rows"]
    rule, number, hours = table["columns"]
    assert "Gone" in {o["value"] for o in rule["options"]}
    assert "7777" in {o["value"] for o in number["options"]}
    assert "500" in {o["value"] for o in hours["options"]}


def test_legacy_settings_writes_the_hide_rules_table_back_into_the_text():
    out = ecm_fields.legacy_settings({
        "hide_rule_rows": [{"rule": "BlankName"}, {"rule": "PastDate", "number": "0", "hours": "4"}],
        "hide_rules_priority": "stale",
    })
    assert out["hide_rules_priority"] == "[BlankName],[PastDate:0:4h]"
    untouched = ecm_fields.legacy_settings({"hide_rule_rows": "oops", "hide_rules_priority": "[NoEPG]"})
    assert untouched["hide_rules_priority"] == "[NoEPG]"


def test_a_successful_scan_with_notices_is_reported_as_partial():
    import ecm_parsing
    with_notice = {"status": "success", "message": "m", "results": {"notices": ["Set but matched nothing: X"]}}
    assert ecm_parsing.status_for_dispatcharr(with_notice)["status"] == "partial"
    for unchanged in (
        {"status": "success", "results": {"notices": []}},
        {"status": "success", "results": {}},
        {"status": "success"},
        {"status": "error", "results": {"notices": ["n"]}},
        None,
        "text",
    ):
        before = repr(unchanged)
        out = ecm_parsing.status_for_dispatcharr(unchanged)
        assert repr(out) == before


def test_a_warning_result_is_reported_as_partial():
    # Validate Configuration returns "warning" when it finds problems; 0.34
    # draws an unknown status green and records it as ok.
    import ecm_parsing
    out = ecm_parsing.status_for_dispatcharr({"status": "warning", "message": "2 problems"})
    assert out["status"] == "partial"
    assert ecm_parsing.status_for_dispatcharr({"status": "warning", "results": {}})["status"] == "partial"


def test_placeholder_regex_notice_names_only_an_invalid_pattern():
    import ecm_parsing
    notice = ecm_parsing.placeholder_regex_notice("No Event (Today")
    assert notice and "Placeholder Guide Titles" in notice
    for fine in ("", "   ", None, "^No Event Today$", r"\bTBA\b"):
        assert ecm_parsing.placeholder_regex_notice(fine) is None


def test_run_returns_partial_only_after_its_own_success_checks(bare_plugin, monkeypatch):
    sent = []
    result = {"status": "success", "message": "Dry run: 3 channels", "results": {"notices": ["n"]}}
    monkeypatch.setattr(bare_plugin, "_call_action", lambda *a: dict(result), raising=False)
    monkeypatch.setattr(bare_plugin, "saved_settings", {}, raising=False)
    import ecm_plugin_under_test as module
    monkeypatch.setattr(module, "send_websocket_update", lambda *a: sent.append(a[2]["message"]))
    out = bare_plugin.run("dry_run", {}, {"settings": {}})
    assert out["status"] == "partial"
    # The plugin's own notification still saw "success" and used the success mark.
    assert sent and sent[0].startswith("[+]")
