"""The bootstrap script is the only thing in this slice that writes to Postgres
and /data. Its decision logic is pure and therefore testable; only the I/O is not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bootstrap_merge import merge_settings, PLACEHOLDER_PREFIX  # noqa: E402


def test_merge_is_idempotent():
    tmpl = {"manage_dummy_epg": True}
    once, changed1 = merge_settings({"runtime_key": 1}, tmpl, {})
    twice, changed2 = merge_settings(once, tmpl, {})
    assert changed1 is True and changed2 is False
    assert once == twice


def test_merge_preserves_runtime_only_keys():
    """The on-disk settings file carries keys the template deliberately omits."""
    merged, _ = merge_settings({"event": "m3u_refresh", "payload": {"a": 1}},
                               {"manage_dummy_epg": True}, {})
    assert merged["event"] == "m3u_refresh"
    assert merged["payload"] == {"a": 1}


def test_placeholders_never_overwrite_an_existing_value():
    """THE CRITICAL ONE. A REPLACE_ME value must not clobber working config -
    doing so takes the plugin out of scope for every group it manages, and the
    next scheduled pass then detaches every channel it owns."""
    existing = {"channel_groups": "US: PPV", "channel_profile_name": "a"}
    tmpl = {"channel_groups": f"{PLACEHOLDER_PREFIX}_groups",
            "channel_profile_name": f"{PLACEHOLDER_PREFIX}_profile"}
    merged, changed = merge_settings(existing, tmpl, {})
    assert merged["channel_groups"] == "US: PPV"
    assert merged["channel_profile_name"] == "a"
    assert changed is False


def test_placeholder_fills_an_absent_key_but_is_reported():
    merged, changed = merge_settings({}, {"channel_groups": f"{PLACEHOLDER_PREFIX}_g"}, {})
    assert merged["channel_groups"].startswith(PLACEHOLDER_PREFIX)
    assert changed is True


def test_credentials_come_only_from_env():
    merged, _ = merge_settings({}, {"manage_dummy_epg": True},
                               {"ECM_DISPATCHARR_PASSWORD": "s3cret"})
    assert merged["dispatcharr_password"] == "s3cret"
    plain, _ = merge_settings({}, {"manage_dummy_epg": True}, {})
    assert "dispatcharr_password" not in plain


def test_existing_credentials_are_never_dropped():
    merged, _ = merge_settings({"dispatcharr_password": "keep-me"},
                               {"manage_dummy_epg": True}, {})
    assert merged["dispatcharr_password"] == "keep-me"
