"""Restore Event-Channel-Managarr configuration on a rebuilt Dispatcharr box.

Run INSIDE the container, AS THE DISPATCH USER.

STEP 1 IS MANDATORY: this script imports bootstrap_merge, which is a SEPARATE
file. Piping only this script via stdin leaves that import unresolvable and the
run dies before doing anything.

    docker cp scripts/bootstrap_merge.py dispatcharr:/tmp/bootstrap_merge.py
    $env:ECM_SETTINGS_JSON = (Get-Content config\\ecm_settings.template.json -Raw)
    docker exec -i -u dispatch -e ECM_SETTINGS_JSON -e ECM_BOOTSTRAP_APPLY dispatcharr \\
        sh -c "cd /app && python3 manage.py shell" < scripts/bootstrap_ecm.py

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
    from bootstrap_merge import merge_settings  # docker cp'd to /tmp -- see docstring
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

# MUST match ecm_profiles.DAZN_GMT.source_name. (A cross-artifact drift test
# for this is planned for a later task -- not present at this commit.)
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


def restore_plugin_settings(template):
    if template is None:
        return "skipped"
    cfg = PluginConfig.objects.filter(key=PLUGIN_KEY).first()
    if cfg is None:
        print(f"[bootstrap] ERROR: no PluginConfig row for {PLUGIN_KEY!r}. Run discovery first.")
        return "no-plugin-row"

    merged, changed = merge_settings(cfg.settings or {}, template, os.environ)
    if not changed:
        return "unchanged"
    if not APPLY:
        added = set(merged) - set(cfg.settings or {})
        print(f"[bootstrap]   would add/update keys: {sorted(added)}")
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
