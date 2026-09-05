"""The report writes a stored choice using the words the settings form uses.

The CSV preamble used to print raw stored values: "duplicate_strategy:
lowest_number", "name_source: Channel_Name", "rate_limiting: none". None of
those phrases appears on the settings form, so a reader who wanted to change
what the report described could not find the control.

ecm_parsing.SETTING_VALUE_LABELS carries the interface wording. It is a second
copy of text that lives in plugin.py's options lists, so this test holds the two
equal: if a label on the form is reworded and the report is not, the report
starts describing a control by a name that no longer exists.
"""

import ast
import io
import os

import ecm_parsing  # resolves via pyproject.toml pythonpath
import pytest

# tests/contract/<this file>, so three levels up is the repository root.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_PY = os.path.join(ROOT, "Event-Channel-Managarr", "plugin.py")


def _form_options():
    """{setting id: {stored value: label}} for every choice on the settings form."""
    with io.open(PLUGIN_PY, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    fields = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "fields")
    out = {}
    for node in ast.walk(fields):
        if not isinstance(node, ast.Dict):
            continue
        entry = {}
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant):
                continue
            try:
                entry[key.value] = ast.literal_eval(value)
            except Exception:
                entry[key.value] = None
        options = entry.get("options")
        if entry.get("id") and isinstance(options, list) and options:
            pairs = {o["value"]: o["label"] for o in options
                     if isinstance(o, dict) and "value" in o and "label" in o}
            if pairs:
                out[entry["id"]] = pairs
    return out


def test_the_form_offers_choices_to_compare_against():
    """A guard that finds nothing to check would pass for the wrong reason."""
    assert len(_form_options()) >= 5


@pytest.mark.parametrize("setting_id", sorted(ecm_parsing.SETTING_VALUE_LABELS))
def test_every_labelled_setting_is_a_real_choice_on_the_form(setting_id):
    assert setting_id in _form_options(), (
        f"{setting_id} has report labels but is not a choice on the settings form")


@pytest.mark.parametrize("setting_id", sorted(ecm_parsing.SETTING_VALUE_LABELS))
def test_the_report_labels_match_the_form_exactly(setting_id):
    form = _form_options()[setting_id]
    report = dict(ecm_parsing.SETTING_VALUE_LABELS[setting_id])
    assert report == form, (
        f"{setting_id}: the report and the settings form disagree about how a "
        f"stored value is written")


def test_every_choice_on_the_form_is_covered_by_the_report():
    """A choice with no entry prints its raw stored value, which is the defect."""
    reported_in_csv = {sid for sid, _label, _kind in ecm_parsing.SETTINGS_REPORT}
    for setting_id, options in _form_options().items():
        if setting_id not in reported_in_csv:
            continue
        if setting_id == "dummy_epg_event_timezone":
            # Its options are the timezone list read from a file, not fixed words.
            continue
        assert setting_id in ecm_parsing.SETTING_VALUE_LABELS, (
            f"{setting_id} is a choice on the form and appears in the report, but "
            f"the report has no wording for its values: {sorted(options)}")


def test_an_unknown_stored_value_is_passed_through_unchanged():
    assert ecm_parsing.value_label("duplicate_strategy", "invented") == "invented"


def test_a_setting_with_no_labels_is_passed_through_unchanged():
    assert ecm_parsing.value_label("channel_groups", "US: PPV") == "US: PPV"


# --- the defaults the report names come from the plugin, not from a copy ----------

def _plugin_tree():
    with io.open(PLUGIN_PY, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _function(name):
    for node in ast.walk(_plugin_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"plugin.py has no function named {name}")


def test_the_plugin_derives_its_defaults_from_the_live_fields_list():
    """A second hand-written copy of the defaults would drift from the form."""
    rendered = ast.unparse(_function("_field_defaults"))
    assert "self.fields" in rendered, (
        "the defaults must come from the fields property Dispatcharr serves")


def test_naming_the_defaults_never_breaks_an_export():
    """It runs while a report is being written, so it degrades rather than raises."""
    rendered = ast.unparse(_function("_field_defaults"))
    assert "except" in rendered and "return {}" in rendered


def test_every_report_site_passes_the_defaults():
    calls = [c for c in ast.walk(_plugin_tree())
             if isinstance(c, ast.Call)
             and ast.unparse(c.func) == "ecm_parsing.settings_report_lines"]
    assert calls, "nothing builds the settings block"
    for call in calls:
        assert len(call.args) == 2, (
            "a report site does not pass the defaults, so an unset choice there "
            "still reads as (empty) when a default is in fact applied")
        assert ast.unparse(call.args[1]) == "self._field_defaults()"
