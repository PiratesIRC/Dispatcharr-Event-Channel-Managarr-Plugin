"""Unit tests for who owns the two fallback template fields on a managed EPG source.

The plugin used to rewrite fallback_title_template and
fallback_description_template on every applied run, so an operator who edited
either in Dispatcharr's own EPG source editor lost their wording on the next
scan with nothing to tell them. That is the same defect issue #21 was filed for
on the three pattern fields, which have been protected by a list of every
shipped default ever since.

The list is what makes this safe in BOTH directions. An operator's own wording is
never touched, and an installation still holding a superseded default still
upgrades itself. Both superseded values matter here and were found by walking all
94 commits that touched plugin.py:

  fallback_title_template once shipped as "{channel_name}", which the renderer
  used verbatim and showed as literal text in the guide.
  fallback_description_template once shipped with an em dash.

Freezing these fields instead would strand those installations on the broken
value for ever.
"""

import ecm_profiles  # resolves via pyproject.toml pythonpath

owned = ecm_profiles.template_is_plugin_owned
EM_DASH_DESCRIPTION = "Live event " + chr(0x2014) + " guide information is currently unavailable."
CURRENT_DESCRIPTION = "Live event. Guide information is currently unavailable."


# --- the shipped-values list ----------------------------------------------------

def test_both_fields_are_protected():
    assert set(ecm_profiles.PROTECTED_TEMPLATE_KEYS) == {
        "fallback_title_template", "fallback_description_template"}


def test_every_protected_key_has_a_shipped_values_list():
    for key in ecm_profiles.PROTECTED_TEMPLATE_KEYS:
        assert ecm_profiles.stock_templates_for(key), f"{key} has no shipped values"


def test_the_current_description_is_listed():
    shipped = ecm_profiles.stock_templates_for("fallback_description_template")
    assert CURRENT_DESCRIPTION in shipped
    assert ecm_profiles._FALLBACK_DESCRIPTION in shipped


def test_the_superseded_em_dash_description_is_listed():
    """Without this an installation keeps the em dash for ever."""
    assert EM_DASH_DESCRIPTION in ecm_profiles.stock_templates_for("fallback_description_template")


def test_the_superseded_channel_name_title_is_listed():
    """Without this an installation shows the literal text {channel_name} for ever."""
    assert "{channel_name}" in ecm_profiles.stock_templates_for("fallback_title_template")


def test_the_current_empty_title_is_listed():
    assert "" in ecm_profiles.stock_templates_for("fallback_title_template")


# --- the decision ---------------------------------------------------------------

def test_an_absent_value_is_plugin_owned():
    assert owned("fallback_description_template", None) is True


def test_the_current_shipped_value_is_plugin_owned():
    assert owned("fallback_description_template", CURRENT_DESCRIPTION) is True


def test_a_superseded_shipped_value_is_plugin_owned_so_it_upgrades():
    assert owned("fallback_description_template", EM_DASH_DESCRIPTION) is True
    assert owned("fallback_title_template", "{channel_name}") is True


def test_operator_wording_is_not_plugin_owned():
    assert owned("fallback_description_template", "Check back at kickoff.") is False


def test_a_near_miss_is_not_plugin_owned():
    """One character different is an edit, not a shipped default."""
    assert owned("fallback_description_template", CURRENT_DESCRIPTION + " ") is False
    assert owned("fallback_description_template", CURRENT_DESCRIPTION.lower()) is False


def test_an_empty_title_is_plugin_owned_because_it_is_the_shipped_value():
    assert owned("fallback_title_template", "") is True


def test_an_emptied_description_is_an_operator_choice():
    """Clearing the description disables the renderer's fallback path deliberately."""
    assert owned("fallback_description_template", "") is False


def test_an_unprotected_key_is_always_plugin_owned():
    """Only the two fallback fields are protected; the rest stay plugin-maintained."""
    assert owned("title_template", "anything the operator typed") is True
    assert owned("output_timezone", "Europe/Oslo") is True


def test_a_non_string_stored_value_is_not_plugin_owned():
    """Whatever it is, it is not a value this plugin shipped, so leave it alone."""
    assert owned("fallback_description_template", 42) is False
    assert owned("fallback_description_template", ["a"]) is False


# --- the write path honours it ---------------------------------------------------

def test_source_props_to_write_preserves_operator_wording():
    desired = dict(ecm_profiles.profile_props(ecm_profiles.US_ET))
    current = dict(desired)
    current["fallback_description_template"] = "Check back at kickoff."
    out = ecm_profiles.source_props_to_write(ecm_profiles.US_ET, current, desired)
    assert out is None or out["fallback_description_template"] == "Check back at kickoff."


def test_source_props_to_write_upgrades_a_superseded_default():
    desired = dict(ecm_profiles.profile_props(ecm_profiles.US_ET))
    current = dict(desired)
    current["fallback_description_template"] = EM_DASH_DESCRIPTION
    out = ecm_profiles.source_props_to_write(ecm_profiles.US_ET, current, desired)
    assert out is not None, "a superseded default must still be upgraded"
    assert out["fallback_description_template"] == ecm_profiles._FALLBACK_DESCRIPTION


def test_source_props_to_write_still_writes_nothing_for_a_user_managed_source():
    desired = dict(ecm_profiles.profile_props(ecm_profiles.US_ET))
    current = dict(desired)
    current["fallback_description_template"] = EM_DASH_DESCRIPTION
    import dataclasses
    profile = dataclasses.replace(ecm_profiles.US_ET, user_managed=True)
    assert ecm_profiles.source_props_to_write(profile, current, desired) is None


def test_source_props_to_write_still_leaves_the_pattern_keys_alone():
    """The existing protection must not be disturbed by the new one."""
    desired = dict(ecm_profiles.profile_props(ecm_profiles.US_ET))
    current = dict(desired)
    current["title_pattern"] = "my own pattern"
    out = ecm_profiles.source_props_to_write(ecm_profiles.US_ET, current, desired)
    assert out is None or out["title_pattern"] == "my own pattern"


def test_an_unprotected_key_has_no_shipped_values():
    assert ecm_profiles.stock_templates_for("title_template") == ()


def test_the_protected_keys_are_derived_from_the_shipped_values():
    """Two hand-maintained lists would drift; one is derived from the other."""
    assert ecm_profiles.PROTECTED_TEMPLATE_KEYS == tuple(
        key for key, _values in ecm_profiles.STOCK_TEMPLATES)
