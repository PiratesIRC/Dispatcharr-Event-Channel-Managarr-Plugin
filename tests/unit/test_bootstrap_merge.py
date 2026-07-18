"""The bootstrap script is the only thing in this slice that writes to Postgres
and /data. Its decision logic is pure and therefore testable; only the I/O is not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bootstrap_merge import CREDENTIAL_ENV, merge_settings, PLACEHOLDER_PREFIX  # noqa: E402


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


def _bootstrap_source():
    """Extract DAZN_SOURCE_NAME and DAZN_PROPS from bootstrap_ecm.py via ast.

    Parsed, never imported: bootstrap_ecm.py imports Django models at module
    scope and cannot be imported outside the container.
    """
    import ast
    src = (Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_ecm.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("DAZN_SOURCE_NAME", "DAZN_PROPS"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    assert "DAZN_SOURCE_NAME" in found, "DAZN_SOURCE_NAME not found in bootstrap_ecm.py"
    assert "DAZN_PROPS" in found, "DAZN_PROPS not found in bootstrap_ecm.py"
    return found


def test_bootstrap_and_profile_agree_on_the_dazn_source_name():
    """Two committed artifacts describing ONE source. If they disagree, a restore
    and the profile model create two different EPGSource rows for the same
    profile - and rev 1 shipped exactly that divergence."""
    import ecm_profiles
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    name = _bootstrap_source()["DAZN_SOURCE_NAME"]
    assert name == dazn.source_name, (
        f"bootstrap says {name!r}, profile says {dazn.source_name!r}")


def _extract_report_deltas_callable():
    """Extract `_MISSING`, `_SENSITIVE_KEYS`, and `_report_deltas` out of
    bootstrap_ecm.py via ast and exec them into an isolated namespace, so the
    dry-run credential-masking behavior can be exercised WITHOUT importing the
    module (it imports Django models at module scope and cannot be imported
    outside the container -- see _bootstrap_source() above).

    Returns the live `_report_deltas` function, bound to a real `_SENSITIVE_KEYS`
    derived from the real `CREDENTIAL_ENV`, so this is a genuine behavioral
    test of the masking logic, not just a source-text grep.
    """
    import ast

    src = (Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_ecm.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)

    wanted = {"_MISSING", "_SENSITIVE_KEYS"}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in wanted:
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_report_deltas":
            nodes.append(node)

    found_names = {
        n.targets[0].id for n in nodes if isinstance(n, ast.Assign)
    }
    assert found_names == wanted, (
        f"expected {wanted} in bootstrap_ecm.py, found {found_names}")
    assert any(isinstance(n, ast.FunctionDef) for n in nodes), \
        "_report_deltas function not found in bootstrap_ecm.py"

    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {"CREDENTIAL_ENV": CREDENTIAL_ENV}
    exec(compile(module, filename="<bootstrap_ecm._report_deltas extract>", mode="exec"),
         namespace)

    assert namespace["_SENSITIVE_KEYS"] == {key for _env, key in CREDENTIAL_ENV}, (
        "_SENSITIVE_KEYS must be derived from CREDENTIAL_ENV, the single "
        "source of truth for which settings keys carry credential material")
    return namespace["_report_deltas"]


def test_report_deltas_masks_credential_values(capsys):
    """THE REGRESSION TEST. A dry run must never print a real credential value.

    Before this fix, _report_deltas printed
    `f"{k}: {existing.get(k)!r} -> {merged.get(k)!r}"` unconditionally, so
    rotating ECM_DISPATCHARR_PASSWORD echoed both the OLD and NEW plaintext
    password to stdout/terminal scrollback on every dry run. If the masking
    is removed or bypassed, this test fails because the raw secret strings
    below reappear in captured stdout.
    """
    report_deltas = _extract_report_deltas_callable()

    old_password = "old-s3cret-hunter2"
    new_password = "new-s3cret-hunter2"
    old_url = "http://192.168.211.53:9191"
    new_url = "http://192.168.211.99:9191"

    existing = {
        "dispatcharr_password": old_password,
        "dispatcharr_url": old_url,
        "channel_groups": "US: PPV",
    }
    merged = {
        "dispatcharr_password": new_password,
        "dispatcharr_url": new_url,
        "channel_groups": "US: PPV Updated",
    }

    report_deltas(merged, existing)
    out = capsys.readouterr().out

    for secret in (old_password, new_password, old_url, new_url):
        assert secret not in out, (
            f"credential value {secret!r} leaked into dry-run stdout:\n{out}")

    # The key names must still be visible -- masking must not hide that an
    # overwrite is happening, only the values.
    assert "dispatcharr_password" in out
    assert "dispatcharr_url" in out
    assert "channel_groups" in out
    # A non-credential value IS still printed in full (masking is scoped).
    assert "US: PPV" in out
    assert "US: PPV Updated" in out
    # The masked lines carry a visible marker instead of the raw value.
    assert "masked" in out


def test_bootstrap_and_profile_agree_on_the_dazn_props():
    """The name is not the only thing that can drift. If the restore script writes
    different custom_properties than the profile models, the restored source
    renders differently from what the tests verified -- silently.

    bootstrap additionally carries `managed_by` (source identity, which the
    profile model does not own); every OTHER key must match exactly.
    """
    import ecm_profiles
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    boot = dict(_bootstrap_source()["DAZN_PROPS"])
    boot.pop("managed_by", None)
    assert boot == ecm_profiles.profile_props(dazn), (
        "bootstrap DAZN_PROPS and profile_props(dazn_gmt) have diverged:\n"
        f"  only in bootstrap: {sorted(set(boot) - set(ecm_profiles.profile_props(dazn)))}\n"
        f"  only in profile:   {sorted(set(ecm_profiles.profile_props(dazn)) - set(boot))}\n"
        f"  differing values:  "
        f"{sorted(k for k in set(boot) & set(ecm_profiles.profile_props(dazn)) if boot[k] != ecm_profiles.profile_props(dazn)[k])}")
