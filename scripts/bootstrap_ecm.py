"""Restore Event-Channel-Managarr configuration on a rebuilt Dispatcharr box.

Run INSIDE the container, AS THE DISPATCH USER.

BOTH docker cp CALLS ARE MANDATORY: this script imports bootstrap_merge, which is
a SEPARATE file. Without it the import is unresolvable and the run dies before
doing anything.

    docker cp scripts/bootstrap_merge.py dispatcharr:/tmp/bootstrap_merge.py
    docker cp scripts/bootstrap_ecm.py    dispatcharr:/tmp/bootstrap_ecm.py
    $env:ECM_SETTINGS_JSON = (Get-Content config\\ecm_settings.template.json -Raw)
    docker exec -u dispatch -e ECM_SETTINGS_JSON -e ECM_BOOTSTRAP_APPLY dispatcharr \\
        sh -c "cd /app && python3 manage.py shell < /tmp/bootstrap_ecm.py"

The redirection is performed by the CONTAINER's shell, inside the quoted sh -c
string. Windows PowerShell 5.1 cannot parse a `<` of its own ("The '<' operator
is reserved for future use") -- and because PowerShell parses a whole block
before executing any of it, a stray `<` silently prevents the preceding
docker cp calls from running too. Verified during this slice's execution.

DEFAULTS TO DRY RUN. Set $env:ECM_BOOTSTRAP_APPLY = "1" to actually write.

WHY THIS EXISTS: plugin CODE returns via docker cp, but everything that makes it
do anything lives in Postgres and /data. manage_dummy_epg defaults to False and
the scheduler only arms from the on-disk settings file, so a fresh box comes up
inert with the code present and nothing configured.

WHAT IT WILL NOT DO: overwrite an existing setting with a REPLACE_ME placeholder;
run as root; rebind a channel outside group 1915; rebind a channel already bound
to a source other than the DAZN GMT source.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/tmp")
try:
    from bootstrap_merge import CREDENTIAL_ENV, merge_settings  # docker cp'd to /tmp -- see docstring
except ImportError:
    raise SystemExit(
        "bootstrap_merge not found on /tmp.\n"
        "Run first:  docker cp scripts/bootstrap_merge.py dispatcharr:/tmp/bootstrap_merge.py"
    )

from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGData, EPGSource  # noqa: E402
from apps.plugins.models import PluginConfig  # noqa: E402

PLUGIN_KEY = "event-channel-managarr"
SETTINGS_FILE = Path("/data/event_channel_managarr_settings.json")
APPLY = os.environ.get("ECM_BOOTSTRAP_APPLY") == "1"

# MUST match ecm_profiles.DAZN_GMT.source_name. Cross-artifact drift is covered
# by tests/unit/test_bootstrap_merge.py::test_bootstrap_and_profile_agree_on_the_dazn_source_name
# and ::test_bootstrap_and_profile_agree_on_the_dazn_props.
DAZN_SOURCE_NAME = "DAZN PPV Dummy (GMT)"
DAZN_GROUP_ID = 1915
DAZN_SLOT_REGEX = r"US: DAZN PPV \d+$"   # anchored: no partial-name capture

DAZN_PROPS = {
    "timezone": "UTC",
    "output_timezone": "America/Chicago",
    "managed_by": "manual-dazn-gmt",
    "title_pattern": r"^(?:Next|End)\s*\|\s*(?<title>.+?)\s*\|",
    "date_pattern": r"\b(?<year>\d{4})-(?<month>\d{1,2})-(?<day>\d{1,2})\b",
    "time_pattern": r"\|\s*(?<hour>\d{1,2}):(?<minute>\d{2})\s*\(GMT\)",
    "title_template": "{title}",
    "upcoming_title_template": "Upcoming at {month}/{day} {starttime} CDT: {title}",
    "ended_title_template": "Ended at {month}/{day} {endtime} CDT: {title}",
    "include_date": False,
    "program_duration": 240,
    "fallback_title_template": "",
    "fallback_description_template": "Live event — guide information is currently unavailable.",
}


def refuse_if_root():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit(
            "REFUSING to run as root. Files this script creates under /data would be\n"
            "root-owned and would silently block the uWSGI workers.\n"
            "Re-run with:  docker exec -i -u dispatch ..."
        )


def load_template():
    raw = os.environ.get("ECM_SETTINGS_JSON")
    if not raw:
        print("[bootstrap] ECM_SETTINGS_JSON not set; skipping settings restore.")
        return None
    return json.loads(raw)


_MISSING = object()

# Keys that carry credential material (see CREDENTIAL_ENV in bootstrap_merge.py).
# dispatcharr_url is included too: it's a private LAN address, and it flows
# through the same CREDENTIAL_ENV mapping as the username/password -- the
# config template's own denylist (tests/contract/test_config_template.py)
# already treats it as not-for-committing. Masking all three is the simpler
# and safer rule.
_SENSITIVE_KEYS = {key for _env, key in CREDENTIAL_ENV}


def _report_deltas(merged, existing):
    """Print the keys a dry run would ADD vs. the keys it would OVERWRITE.

    A key that already exists with a DIFFERENT value is a silent overwrite if
    only "added" is reported -- exactly how a wrong committed value (e.g. a
    hide_rules_priority that doesn't match live) could slip past review. Report
    both lists so a changed value is never invisible in the dry-run output.

    Values for keys in _SENSITIVE_KEYS are masked, never printed -- credentials
    flow through this same `merged`/`existing` dict (CREDENTIAL_ENV maps
    ECM_DISPATCHARR_URL/USERNAME/PASSWORD onto dispatcharr_url/_username/
    _password), so printing old/new values here would echo real secrets (e.g.
    a rotated password) to stdout/terminal scrollback on every dry run. The key
    NAME is still listed in `changed` so the overwrite stays visible.
    """
    added = sorted(k for k in merged if k not in existing)
    changed = sorted(
        k for k, v in merged.items()
        if existing.get(k, _MISSING) not in (_MISSING, v)
    )
    print(f"[bootstrap]   would add keys: {added}")
    print(f"[bootstrap]   would CHANGE existing keys: {changed}")
    if changed:
        for k in changed:
            if k in _SENSITIVE_KEYS:
                print(f"[bootstrap]     {k}: *** -> *** (masked)")
            else:
                print(f"[bootstrap]     {k}: {existing.get(k)!r} -> {merged.get(k)!r}")


def restore_plugin_settings(template):
    if template is None:
        return "skipped"
    cfg = PluginConfig.objects.filter(key=PLUGIN_KEY).first()
    if cfg is None:
        print(f"[bootstrap] ERROR: no PluginConfig row for {PLUGIN_KEY!r}. Run discovery first.")
        return "no-plugin-row"

    existing = cfg.settings or {}
    merged, changed = merge_settings(existing, template, os.environ)
    if not changed:
        return "unchanged"
    if not APPLY:
        _report_deltas(merged, existing)
        return "DRY-RUN would update"
    cfg.settings = merged
    cfg.save(update_fields=["settings"])
    return "updated"


def mirror_settings_file(template):
    if template is None:
        return "skipped"
    existing = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[bootstrap] WARNING: could not parse {SETTINGS_FILE}: {exc}")

    merged, changed = merge_settings(existing, template, os.environ)
    if not changed:
        return "unchanged"
    if not APPLY:
        _report_deltas(merged, existing)
        return "DRY-RUN would update"
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return "updated"


def restore_dazn_source():
    source = EPGSource.objects.filter(name=DAZN_SOURCE_NAME).first()
    if source is None:
        if not APPLY:
            return None, "DRY-RUN would create"
        source = EPGSource.objects.create(
            name=DAZN_SOURCE_NAME, source_type="dummy", is_active=True,
            refresh_interval=0, priority=0, custom_properties=DAZN_PROPS)
        return source, "created"
    if source.custom_properties != DAZN_PROPS:
        if not APPLY:
            return source, "DRY-RUN would update props"
        # Full replacement is INTENTIONAL: this script fully owns this source's
        # custom_properties. Do not "improve" this into a dict merge -- a merge
        # would let a stale field survive a DAZN_PROPS change and reintroduce drift.
        source.custom_properties = DAZN_PROPS
        source.save(update_fields=["custom_properties"])
        return source, "updated props"
    return source, "unchanged"


def rebind_dazn_channels(source):
    """Report/rebind the DAZN slot channels matching DAZN_SLOT_REGEX.

    source is None only in dry-run mode when the EPGSource row doesn't exist
    yet (restore_dazn_source() always creates it before an APPLY run reaches
    here). Query and count the targets in that case too -- an APPLY run would
    create the source and bind every matching channel, so the dry-run report
    must not read "0 targets" in exactly the fresh-box scenario this script
    exists for.
    """
    targets = list(
        Channel.objects.filter(channel_group_id=DAZN_GROUP_ID, name__regex=DAZN_SLOT_REGEX)
        .select_related("epg_data").order_by("id"))

    would_bind, skipped_foreign = 0, 0
    for channel in targets:
        current_source_id = getattr(channel.epg_data, "epg_source_id", None)
        # source is None means the source row doesn't exist yet, so ANY
        # existing binding is necessarily to some other source -- foreign.
        is_foreign = current_source_id is not None and (
            source is None or current_source_id != source.id)
        if is_foreign:
            skipped_foreign += 1
            continue
        if source is None or not APPLY:
            if channel.epg_data_id is None:
                would_bind += 1
            continue
        epg_data, _ = EPGData.objects.get_or_create(
            epg_source=source, tvg_id=f"dazn_gmt_{channel.id}",
            defaults={"name": channel.name})
        if epg_data.name != channel.name:
            epg_data.name = channel.name
            epg_data.save(update_fields=["name"])
        if channel.epg_data_id != epg_data.id:
            channel.epg_data = epg_data
            channel.save(update_fields=["epg_data"])
            would_bind += 1
    return len(targets), would_bind, skipped_foreign


def main():
    refuse_if_root()
    mode = "APPLY" if APPLY else "DRY RUN (set ECM_BOOTSTRAP_APPLY=1 to write)"
    print(f"[bootstrap] Event-Channel-Managarr restore -- {mode}")

    template = load_template()
    print(f"[bootstrap] PluginConfig.settings: {restore_plugin_settings(template)}")
    print(f"[bootstrap] {SETTINGS_FILE}: {mirror_settings_file(template)}")

    source, status = restore_dazn_source()
    sid = source.id if source else "n/a"
    print(f"[bootstrap] EPGSource {DAZN_SOURCE_NAME!r} (id={sid}): {status}")

    total, bound, skipped = rebind_dazn_channels(source)
    print(f"[bootstrap] DAZN slots in group {DAZN_GROUP_ID}: {total}, "
          f"bound: {bound}, skipped (bound elsewhere): {skipped}")
    print("[bootstrap] done. Scheduler arms on next settings load; click any "
          "plugin action to arm immediately.")


main()
