"""Both write paths must ask who owns a fallback template before overwriting it.

The plugin writes the managed EPG source's properties from two places:
_get_or_create_managed_epg_source in plugin.py, for the shared source, and
ecm_profiles.source_props_to_write, for a per-profile source. Until 2026-09-05
both rewrote fallback_title_template and fallback_description_template
unconditionally, so an operator who edited either in Dispatcharr's own EPG source
editor lost their wording on the next scan.

If only one path asks, the two disagree about who owns the field and the answer
depends on which source a channel happens to be on. That is the failure this file
prevents.

The decision itself is unit-tested in tests/unit/test_template_ownership.py.
plugin.py imports Django at module scope and cannot be imported outside the
container, so its structure is read with ast.
"""

import ast
from pathlib import Path

import ecm_profiles  # resolves via pyproject.toml pythonpath
import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
PROFILES_PY = ROOT / "Event-Channel-Managarr" / "ecm_profiles.py"

PLUGIN_TREE = ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))
PROFILES_TREE = ast.parse(PROFILES_PY.read_text(encoding="utf-8"))

DECISION = "template_is_plugin_owned"


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    pytest.fail(f"no function named {name}")


def _calls_to(node, dotted):
    return [c for c in ast.walk(node)
            if isinstance(c, ast.Call) and ast.unparse(c.func) == dotted]


def test_the_shared_source_path_asks_who_owns_the_field():
    fn = _function(PLUGIN_TREE, "_get_or_create_managed_epg_source")
    assert _calls_to(fn, f"ecm_profiles.{DECISION}"), (
        "_get_or_create_managed_epg_source must consult ecm_profiles."
        + DECISION + " or it overwrites an operator's wording every run")


def test_the_per_profile_path_asks_who_owns_the_field():
    fn = _function(PROFILES_TREE, "source_props_to_write")
    assert _calls_to(fn, DECISION), (
        "source_props_to_write must consult " + DECISION)


def test_the_shared_source_path_asks_about_the_stored_value():
    """Asking about the value being written instead would always answer yes."""
    fn = _function(PLUGIN_TREE, "_get_or_create_managed_epg_source")
    call = _calls_to(fn, f"ecm_profiles.{DECISION}")[0]
    rendered = [ast.unparse(a) for a in call.args]
    assert rendered == ["k", "current.get(k)"], (
        f"expected the stored value to be passed, got {rendered}")


def test_the_per_profile_path_asks_about_the_stored_value():
    fn = _function(PROFILES_TREE, "source_props_to_write")
    call = _calls_to(fn, DECISION)[0]
    rendered = [ast.unparse(a) for a in call.args]
    assert rendered == ["key", "merged.get(key)"], (
        f"expected the stored value to be passed, got {rendered}")


def test_the_pattern_protection_is_still_in_place():
    """The new protection sits beside the older one, it does not replace it."""
    fn = _function(PLUGIN_TREE, "_get_or_create_managed_epg_source")
    assert "stock_patterns" in ast.unparse(fn)
    profiles_fn = _function(PROFILES_TREE, "source_props_to_write")
    assert "PATTERN_PROPERTY_KEYS" in ast.unparse(profiles_fn)


def test_every_shipped_value_of_every_protected_key_is_recognised():
    """A typo in the list would silently hand a shipped default to the operator."""
    for key in ecm_profiles.PROTECTED_TEMPLATE_KEYS:
        for value in ecm_profiles.stock_templates_for(key):
            assert ecm_profiles.template_is_plugin_owned(key, value) is True, (
                f"{key} does not recognise its own shipped value {value!r}")


def test_the_current_profile_values_are_recognised_as_shipped():
    """What the plugin writes today must be recognised tomorrow, or it fights itself.

    Without this, a run would see its own value as an operator edit, stop writing
    it, and the field would freeze at whatever happened to be there first.
    """
    for profile in (ecm_profiles.US_ET, ecm_profiles.DAZN_GMT):
        props = ecm_profiles.profile_props(profile)
        for key in ecm_profiles.PROTECTED_TEMPLATE_KEYS:
            assert ecm_profiles.template_is_plugin_owned(key, props[key]) is True, (
                f"{profile.key} writes a {key} value that is not in the shipped list")
