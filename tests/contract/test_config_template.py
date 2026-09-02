"""The committed settings template must be restorable AND credential-free.

/data/event_channel_managarr_settings.json holds a plaintext dispatcharr_password
and dispatcharr_username. This repository is public.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "config" / "ecm_settings.template.json"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"

DENYLIST = {
    "dispatcharr_password", "dispatcharr_username", "dispatcharr_url",
    "timezone", "event", "payload",
}

# Keys whose value is environment-specific and MUST ship as a placeholder, so
# that running bootstrap without editing them cannot silently overwrite a
# working config with someone else's values.
MUST_BE_PLACEHOLDER = {"channel_profile_name", "channel_groups"}
PLACEHOLDER_PREFIX = "REPLACE_ME"


def _template():
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _plugin_field_ids():
    data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    return {f["id"] for f in data["fields"]
            if "id" in f and not f["id"].startswith("_section_")}


def test_template_exists():
    assert TEMPLATE.exists(), f"missing {TEMPLATE}"


def test_no_denylisted_keys():
    leaked = set(_template()) & DENYLIST
    assert not leaked, f"denylisted keys in committed template: {sorted(leaked)}"


def test_no_credential_shaped_keys():
    suspicious = [k for k in _template()
                  if any(t in k.lower() for t in ("pass", "secret", "token", "auth", "cred"))]
    assert not suspicious, f"credential-shaped keys: {suspicious}"


def _looks_like_a_secret(value):
    """Mixed-case + digit, no separators, >=12 chars.

    Calibrated against the real template AND real credential shapes. An earlier
    version used fullmatch(r"[A-Za-z0-9+/=_-]{16,}") which flagged
    "America/New_York" -- exactly 16 chars of that class -- so the test failed on
    the very template this task tells you to write. Timezone names, prose and
    CSV values are excluded by the separator check; a 15-char password like
    "2NhqS8vGw4HwYeg" is still caught.
    """
    if not isinstance(value, str) or len(value) < 12:
        return False
    if value.startswith(PLACEHOLDER_PREFIX):
        return False
    if any(sep in value for sep in ("/", " ", ",")):
        return False
    return bool(re.search(r"[a-z]", value)
                and re.search(r"[A-Z]", value)
                and re.search(r"\d", value))


def test_no_value_looks_like_a_secret():
    """Defence in depth: a high-entropy opaque string under an innocent key."""
    bad = [k for k, v in _template().items() if _looks_like_a_secret(v)]
    assert not bad, f"values that look like secrets: {bad}"


def test_the_secret_heuristic_actually_bites():
    """A guard that never fires is not a guard."""
    assert _looks_like_a_secret("2NhqS8vGw4HwYeg")
    assert _looks_like_a_secret("ghp_A1b2C3d4E5f6")
    assert not _looks_like_a_secret("America/New_York")
    assert not _looks_like_a_secret("lowest_number")


def test_all_keys_are_real_plugin_fields():
    unknown = set(_template()) - _plugin_field_ids()
    assert not unknown, f"template keys that are not plugin.json field ids: {sorted(unknown)}"


def test_environment_specific_keys_are_placeholders():
    """These MUST NOT carry real values. bootstrap refuses to write a placeholder,
    so shipping a real value here is what would let it clobber a working config."""
    t = _template()
    for key in MUST_BE_PLACEHOLDER:
        assert key in t, f"template missing {key}"
        assert str(t[key]).startswith(PLACEHOLDER_PREFIX), (
            f"{key} must ship as a {PLACEHOLDER_PREFIX}* placeholder, got {t[key]!r}"
        )


def test_template_covers_the_epg_critical_settings():
    missing = {"manage_dummy_epg", "dummy_epg_event_timezone",
               "dummy_epg_event_duration_hours", "channel_groups",
               "channel_profile_name", "scheduled_times"} - set(_template())
    assert not missing, f"template missing required settings: {sorted(missing)}"


def test_the_group_epg_source_map_ships_empty_rather_than_as_a_placeholder():
    """A deliberate exception to the placeholder rule, and the reason it is one.

    The mapping IS environment-specific, which is normally what puts a key in
    MUST_BE_PLACEHOLDER above. But bootstrap refuses to write a placeholder, and an
    empty mapping is the correct, complete configuration for every installation that
    does not use per-group EPG sources: it means the feature is off and every group
    keeps the shared source. Shipping a placeholder would make bootstrap decline to
    restore a setting whose right value is already known.

    Shipping a real value here would be the actual hazard, since it would create EPG
    sources named after somebody else's channel groups.
    """
    t = _template()
    assert "group_epg_source_map" in t, "the template must cover every real setting"
    assert t["group_epg_source_map"] == "", (
        "ship it empty: a real value would create sources named after another "
        "installation's channel groups")
