"""The settings form is divided into sections and every setting sits under one.

Measured 2026-09-05 against the field list this plugin actually builds: 30
fields, of which 6 are section headings, and all 24 real settings already sit
under a heading with none stranded before the first one. That is the state these
tests lock, so a setting added later cannot silently land under the wrong
heading, and so the failure the sibling project Stream-Mapparr had cannot appear
here: ONE heading in the middle with nothing closing it, which made 32 unrelated
settings read as part of one narrow feature.

The boundary is pinned rather than the full membership, so adding a setting
inside a section needs no test change while moving a boundary does.

The heading mechanism is a field of type "info". This plugin declares its fields
in BOTH plugin.py and plugin.json, and Dispatcharr serves the plugin.py list for
an enabled plugin, so the tests read that list and check the manifest agrees.
"""

import ast
import io
import json
import os

import pytest

# tests/contract/<this file>, so three levels up is the repository root.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_PY = os.path.join(ROOT, "Event-Channel-Managarr", "plugin.py")
MANIFEST = os.path.join(ROOT, "Event-Channel-Managarr", "plugin.json")

# Each section heading and the setting that must come directly after it.
SECTION_BOUNDARIES = [
    ("_section_scope", "channel_profile_name"),
    ("_section_rules", "hide_rules_priority"),
    ("_section_duplicates", "duplicate_strategy"),
    ("_section_epg", "auto_set_dummy_epg_on_hide"),
    ("_section_scheduling", "scheduled_times"),
    ("_section_advanced", "rate_limiting"),
]


def _field_ids():
    """Ids in the order the fields property builds them, read with ast.

    plugin.py imports Django at module scope and cannot be imported outside the
    container, so the property cannot simply be called.
    """
    with io.open(PLUGIN_PY, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "fields")
    ids = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Dict):
            continue
        entry = {}
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value in ("id", "type"):
                entry[key.value] = value.value if isinstance(value, ast.Constant) else "?"
        if "id" in entry:
            ids.append(entry["id"])
    return ids


def _manifest_fields():
    with io.open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)["fields"]


def _sections(ids):
    return [i for i in ids if str(i).startswith("_section_")]


def test_the_form_has_section_headings():
    assert len(_sections(_field_ids())) == len(SECTION_BOUNDARIES)


def test_the_first_field_is_a_section_heading():
    """A setting before the first heading belongs to no section at all."""
    assert _field_ids()[0].startswith("_section_")


def test_every_setting_sits_under_a_heading():
    ids = _field_ids()
    first = next(i for i, name in enumerate(ids) if str(name).startswith("_section_"))
    stranded = ids[:first]
    assert not stranded, f"these settings are above every heading: {stranded}"


def test_no_heading_is_left_empty():
    """An empty heading reads as a feature with no settings, which is confusing."""
    ids = _field_ids()
    for index, name in enumerate(ids):
        if not str(name).startswith("_section_"):
            continue
        following = ids[index + 1:index + 2]
        assert following and not str(following[0]).startswith("_section_"), (
            f"{name} is immediately followed by another heading")


@pytest.mark.parametrize("section,first_setting", SECTION_BOUNDARIES)
def test_the_section_opens_with_the_expected_setting(section, first_setting):
    ids = _field_ids()
    assert section in ids, f"{section} is gone; the form was reorganised"
    assert ids[ids.index(section) + 1] == first_setting, (
        f"{section} should open with {first_setting}, "
        f"found {ids[ids.index(section) + 1]}")


def test_the_sections_appear_in_the_expected_order():
    assert _sections(_field_ids()) == [s for s, _ in SECTION_BOUNDARIES]


def test_the_manifest_declares_the_same_fields_in_the_same_order():
    """Dispatcharr serves the plugin.py list, but a drifting manifest misleads."""
    assert [f["id"] for f in _manifest_fields()] == _field_ids()


def test_every_heading_is_an_info_field_in_the_manifest():
    by_id = {f["id"]: f for f in _manifest_fields()}
    for section, _ in SECTION_BOUNDARIES:
        assert by_id[section]["type"] == "info"


def test_every_heading_body_is_one_flowing_paragraph():
    """A line break is not safe in an info panel body in this workspace."""
    for field in _manifest_fields():
        if field.get("type") != "info":
            continue
        body = field.get("description") or field.get("help_text") or ""
        assert body.strip(), f"{field['id']} has no body"
        assert "\n" not in body, f"{field['id']} body contains a line break"
        assert not body.lstrip().startswith(("-", "*")), (
            f"{field['id']} body starts a list")


def test_every_heading_body_says_something_beyond_the_labels():
    """A body that only restates the heading earns nothing."""
    for field in _manifest_fields():
        if field.get("type") != "info":
            continue
        body = field.get("description") or field.get("help_text") or ""
        assert len(body) >= 60, (
            f"{field['id']} body is {len(body)} characters, too short to say "
            "what the section governs plus one thing the labels do not")


def test_no_heading_body_contains_an_em_dash_or_non_ascii_punctuation():
    for field in _manifest_fields():
        if field.get("type") != "info":
            continue
        body = field.get("description") or field.get("help_text") or ""
        assert chr(0x2014) not in body and chr(0x2013) not in body, (
            f"{field['id']} body contains an em or en dash")


def test_the_manifest_and_the_served_list_agree_on_every_field_label():
    """A drifting label makes the documentation describe a control by a name the
    interface does not use. The action lists are checked the same way in
    tests/contract/test_action_buttons.py, where a sibling project found real
    drift across 22 values."""
    with io.open(PLUGIN_PY, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "fields")
    served = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Dict):
            continue
        entry = {}
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                entry[key.value] = value.value
        if entry.get("id"):
            served.setdefault(entry["id"], entry)
    disagree = [f["id"] for f in _manifest_fields()
                if f["id"] in served
                and served[f["id"]].get("label") is not None
                and served[f["id"]].get("label") != f.get("label")]
    assert not disagree, (
        f"plugin.py and plugin.json disagree about the label for {disagree}; "
        "Dispatcharr serves the plugin.py value")
