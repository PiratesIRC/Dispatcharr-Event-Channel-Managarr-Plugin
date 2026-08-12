# scripts/s2_targeted_repair.py
"""Restore channel EPG bindings from a pre-change snapshot.

Use this INSTEAD of a full pg_restore when a binding diff shows LOST EPG > 0. A
full restore rolls the entire database back, discarding every unrelated M3U
refresh, channel edit and user action since the backup, to fix a defect scoped to
one channel group. Dummy sources render on the fly from custom_properties and
carry no ProgramData, so re-pointing loses nothing.

    docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/s2_repair.py"
"""

import json

from apps.channels.models import Channel
from apps.epg.models import EPGData, EPGSource

SNAPSHOT = "/tmp/s2_before.json"

before = json.load(open(SNAPSHOT))
repaired = missing_source = 0
for cid, src_name in before.items():
    if not src_name:
        continue
    try:
        channel = Channel.objects.select_related("epg_data").get(id=int(cid))
    except Channel.DoesNotExist:
        continue
    if channel.epg_data is not None:
        continue
    source = EPGSource.objects.filter(name=src_name).first()
    if source is None:
        missing_source += 1
        continue
    epg_data, _ = EPGData.objects.get_or_create(
        tvg_id=str(channel.uuid), epg_source=source,
        defaults={"name": channel.name})
    channel.epg_data = epg_data
    channel.save(update_fields=["epg_data"])
    repaired += 1

print(f"repaired {repaired} binding(s); {missing_source} had a missing source")
