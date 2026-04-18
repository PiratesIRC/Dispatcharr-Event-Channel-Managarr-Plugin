# Managed Dummy EPG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in feature that writes a shared `EPGSource(source_type='dummy')` and binds visible PPV/Live-Event channels with no existing EPG to it, so Dispatcharr's guide shows the scheduled event during its window and "Offline" outside it (or the channel name as a 24h fallback for names with no parseable time).

**Architecture:** One shared `EPGSource` named `ECM Managed Dummy` with regex + template `custom_properties`. Per-channel `EPGData` rows keyed by `channel.uuid` point at that source. Dispatcharr's existing `generate_custom_dummy_programs` renders the guide on demand — no `ProgramData` is pre-generated. Scan-time logic attaches/detaches bindings based on a new toggle and preserves channels that already have real EPG.

**Tech Stack:** Python 3, Django ORM, Dispatcharr `apps.epg.models.EPGSource` / `EPGData`, existing plugin patterns (`bulk_update`, `transaction.atomic`, single `plugin.py` file).

**Spec:** `docs/superpowers/specs/2026-04-17-managed-dummy-epg-design.md`

**Verification harness:** no pytest suite exists. Each task's verification step uses `docker exec dispatcharr python3 /app/manage.py shell -c '...'` against the deployed plugin at `/data/plugins/event-channel-managarr/plugin.py`. Deploy with `docker cp Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py` before each verification run.

---

## File Structure

All changes are confined to one file, matching the project's established single-file pattern:
- **Modify:** `Event-Channel-Managarr/plugin.py`

No new files. Within `plugin.py` the additions cluster by responsibility:
- `PluginConfig` (top of file, ~line 40): new constants.
- `Plugin.DEFAULT_*` aliases (~line 180): re-export the constants.
- `Plugin.fields` (~line 390): four new field dicts appended before the closing `]`.
- `Plugin._save_settings` (~line 854): four new default-fallback lines.
- New helper methods `_get_or_create_managed_epg_source`, `_attach_managed_epg`, `_detach_managed_epg`, `_run_managed_epg_pass` grouped together right after `_handle_duplicates` (~line 1890) since they're scan-helpers of similar weight.
- `Plugin._scan_and_update_channels` (~line 2170+): one new call site between visibility apply and `auto_set_dummy_epg_on_hide`.
- Results/CSV schema (~line 2160+): two added per-channel fields + summary line.

---

## Task 1: Settings plumbing (constants, defaults, fields, save-defaults)

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Add four constants to `PluginConfig`**

Open the file and find the `PluginConfig` block. Add these lines immediately after `DEFAULT_KEEP_DUPLICATES = False` (around line 68):

```python
    # Managed Dummy EPG feature defaults
    DEFAULT_MANAGE_DUMMY_EPG = False
    DEFAULT_EVENT_DURATION_HOURS = "3"
    DEFAULT_OFFLINE_TITLE = "Offline"
    DEFAULT_DUMMY_EPG_TIMEZONE = "US/Eastern"
```

- [ ] **Step 2: Add four aliases on `Plugin` class**

Find the block of `DEFAULT_*` aliases near the top of `class Plugin` (around line 188). Add these right after `DEFAULT_KEEP_DUPLICATES = PluginConfig.DEFAULT_KEEP_DUPLICATES`:

```python
    DEFAULT_MANAGE_DUMMY_EPG = PluginConfig.DEFAULT_MANAGE_DUMMY_EPG
    DEFAULT_EVENT_DURATION_HOURS = PluginConfig.DEFAULT_EVENT_DURATION_HOURS
    DEFAULT_OFFLINE_TITLE = PluginConfig.DEFAULT_OFFLINE_TITLE
    DEFAULT_DUMMY_EPG_TIMEZONE = PluginConfig.DEFAULT_DUMMY_EPG_TIMEZONE
```

- [ ] **Step 3: Add four new field dicts in `fields` property**

Find the `fields` list in the `fields` property. Locate the last field (`enable_scheduled_csv_export`) and insert these four dicts immediately before the closing `]`:

```python
            {
                "id": "manage_dummy_epg",
                "label": "🗓️ Manage Dummy EPG",
                "type": "boolean",
                "default": self.DEFAULT_MANAGE_DUMMY_EPG,
                "help_text": "If enabled, visible channels with no EPG assigned will be bound to a plugin-managed dummy EPG source. The guide shows the extracted event during its time window (and 'Offline' outside it), or the channel name as a 24-hour fallback if no time is parseable.",
            },
            {
                "id": "dummy_epg_event_duration_hours",
                "label": "⏱️ Event Duration (hours)",
                "type": "string",
                "default": self.DEFAULT_EVENT_DURATION_HOURS,
                "placeholder": "3",
                "help_text": "How long each scheduled event should appear in the guide (hours). Before and after this window the guide shows the Offline Title.",
            },
            {
                "id": "dummy_epg_offline_title",
                "label": "💤 Offline Title",
                "type": "string",
                "default": self.DEFAULT_OFFLINE_TITLE,
                "placeholder": "Offline",
                "help_text": "Title shown in the guide before and after the event window. Also used as fallback when the title pattern doesn't match.",
            },
            {
                "id": "dummy_epg_event_timezone",
                "label": "📺 Channel Name Event Timezone",
                "type": "select",
                "default": self.DEFAULT_DUMMY_EPG_TIMEZONE,
                "help_text": "Timezone encoded in the event times inside channel names (e.g., US/Eastern for channels like '(4.17 8:30 PM ET)'). Different from the scheduler timezone above.",
                "options": self._load_timezones_from_file()
            },
```

- [ ] **Step 4: Add save-defaults in `_save_settings`**

Find `_save_settings` (around line 854). After the existing `if "auto_set_dummy_epg_on_hide" not in settings: ...` block, add:

```python
            if "manage_dummy_epg" not in settings:
                settings["manage_dummy_epg"] = self.DEFAULT_MANAGE_DUMMY_EPG
            if "dummy_epg_event_duration_hours" not in settings:
                settings["dummy_epg_event_duration_hours"] = self.DEFAULT_EVENT_DURATION_HOURS
            if "dummy_epg_offline_title" not in settings:
                settings["dummy_epg_offline_title"] = self.DEFAULT_OFFLINE_TITLE
            if "dummy_epg_event_timezone" not in settings:
                settings["dummy_epg_event_timezone"] = self.DEFAULT_DUMMY_EPG_TIMEZONE
```

- [ ] **Step 5: Syntax check + deploy + verify fields are visible**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
p = m.Plugin()
ids = [f['id'] for f in p.fields]
for wanted in ['manage_dummy_epg','dummy_epg_event_duration_hours','dummy_epg_offline_title','dummy_epg_event_timezone']:
    assert wanted in ids, f'Missing field: {wanted}'
print('All 4 fields present in Plugin.fields')
"
```

Expected: `Syntax OK`, then `All 4 fields present in Plugin.fields`.

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: add settings for Managed Dummy EPG feature

Adds four new plugin settings (master toggle, event duration, offline
title, channel-name event timezone) plus PluginConfig defaults. No
runtime behavior yet — wired in subsequent tasks."
```

---

## Task 2: `_get_or_create_managed_epg_source` helper

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Add the helper method**

Find `_handle_duplicates` (around line 1818). Insert the following new method immediately after it (before `_get_channel_visibility`):

```python
    def _get_or_create_managed_epg_source(self, settings, logger):
        """Create (if missing) or refresh the shared plugin-managed dummy EPGSource.

        Returns the EPGSource, or None on error.
        """
        from apps.epg.models import EPGSource

        # Parse duration with fallback
        try:
            duration_hours = int(str(settings.get("dummy_epg_event_duration_hours",
                                                   self.DEFAULT_EVENT_DURATION_HOURS)).strip())
        except (ValueError, TypeError):
            logger.warning(f"{LOG_PREFIX} Invalid dummy_epg_event_duration_hours; using default")
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
        if duration_hours <= 0:
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)

        offline_title = str(settings.get("dummy_epg_offline_title",
                                         self.DEFAULT_OFFLINE_TITLE)).strip() or self.DEFAULT_OFFLINE_TITLE
        tz_value = str(settings.get("dummy_epg_event_timezone",
                                    self.DEFAULT_DUMMY_EPG_TIMEZONE)).strip() or self.DEFAULT_DUMMY_EPG_TIMEZONE

        # Keys the plugin owns. Any other keys on the source are left untouched.
        # Regexes validated against these four real channel names:
        #   "PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)"  → title="Cage Fury FC 153"
        #   "LIVE EVENT 01   9:45am Suslenkov v Mann"          → title="Suslenkov v Mann"
        #   "PPV EVENT 25: OUTDOOR THEATRE Live From Coachella" → title="OUTDOOR THEATRE Live From Coachella"
        #   "PPV02 | UFC 327: English Apr 14 4:30 PM"          → title="UFC 327: English"
        # The title capture stops at the first of: " (", a time token, or a month-name token.
        # leading_time handles names where the time appears BEFORE the event text (LIVE format).
        managed_props = {
            "title_pattern": (
                r"(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+\s*[:|\s]\s*"
                r"(?:(?P<leading_time>\d{1,2}:\d{2}\s*[AaPp][Mm])\s+)?"
                r"(?P<title>.+?)"
                r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
                r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
            ),
            "time_pattern": r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp][Mm])?",
            "date_pattern": r"\b(?P<month>\d{1,2})[./](?P<day>\d{1,2})(?:[./](?P<year>\d{2,4}))?\b",
            "title_template": "{title}",
            "upcoming_title_template": offline_title,
            "ended_title_template": offline_title,
            "fallback_title_template": "{channel_name}",
            "program_duration": duration_hours * 60,
            "timezone": tz_value,
            "include_date": False,
            "managed_by": "event-channel-managarr",
        }

        try:
            source, created = EPGSource.objects.get_or_create(
                name="ECM Managed Dummy",
                defaults={
                    "source_type": "dummy",
                    "is_active": True,
                    "custom_properties": managed_props,
                },
            )
        except Exception as e:
            logger.error(f"{LOG_PREFIX} Failed to get_or_create managed EPGSource: {e}")
            return None

        if created:
            logger.info(f"{LOG_PREFIX} Created managed EPGSource 'ECM Managed Dummy' (id={source.id})")
            return source

        # Existing source: refresh only the plugin-managed keys, preserving any
        # user-added keys.
        current = dict(source.custom_properties or {})
        changed = False
        for k, v in managed_props.items():
            if current.get(k) != v:
                current[k] = v
                changed = True
        if source.source_type != "dummy":
            logger.warning(f"{LOG_PREFIX} 'ECM Managed Dummy' exists but source_type={source.source_type!r}; leaving alone")
            return None
        if changed:
            source.custom_properties = current
            try:
                source.save(update_fields=["custom_properties"])
                logger.info(f"{LOG_PREFIX} Refreshed managed EPGSource custom_properties (id={source.id})")
            except Exception as e:
                logger.error(f"{LOG_PREFIX} Failed to update managed EPGSource: {e}")
                return None
        return source
```

- [ ] **Step 2: Syntax check + deploy + verify**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, logging, re
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logger = logging.getLogger('t'); logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)
from apps.epg.models import EPGSource

# Sanity: EPGSource.name must be unique so get_or_create by name is race-safe
assert EPGSource._meta.get_field('name').unique, 'EPGSource.name is not unique — get_or_create(name=...) is unsafe'
print('[0] EPGSource.name uniqueness confirmed')

settings = {'dummy_epg_event_duration_hours':'3','dummy_epg_offline_title':'Offline','dummy_epg_event_timezone':'US/Eastern'}
p = m.Plugin()

# First call creates
src = p._get_or_create_managed_epg_source(settings, logger)
assert src is not None and src.source_type == 'dummy'
assert src.name == 'ECM Managed Dummy'
assert src.custom_properties['program_duration'] == 180
assert src.custom_properties['timezone'] == 'US/Eastern'
assert src.custom_properties['upcoming_title_template'] == 'Offline'
assert src.custom_properties['managed_by'] == 'event-channel-managarr'
print(f'[1] Created source id={src.id}')

# Second call is idempotent — no new source, no changes
src2 = p._get_or_create_managed_epg_source(settings, logger)
assert src2.id == src.id
print(f'[2] Idempotent: same id {src2.id}')

# Change a setting and verify refresh
settings['dummy_epg_offline_title'] = 'No Events'
src3 = p._get_or_create_managed_epg_source(settings, logger)
assert src3.custom_properties['upcoming_title_template'] == 'No Events'
print(f'[3] Refresh: upcoming_title_template now {src3.custom_properties[\"upcoming_title_template\"]!r}')

# User-added key is preserved across refresh
src.custom_properties['user_extra'] = 'keep me'
src.save()
settings['dummy_epg_offline_title'] = 'Offline'
src4 = p._get_or_create_managed_epg_source(settings, logger)
assert src4.custom_properties.get('user_extra') == 'keep me'
print(f'[4] User extras preserved')

# Regex validation against the four real channel-name shapes
cp = src4.custom_properties
title_re = re.compile(cp['title_pattern'])
time_re = re.compile(cp['time_pattern'])
date_re = re.compile(cp['date_pattern'])
cases = [
    ('PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)', 'Cage Fury FC 153', ('8','30','PM'), ('4','17')),
    ('LIVE EVENT 01   9:45am Suslenkov v Mann',          'Suslenkov v Mann',  ('9','45','am'), None),
    ('PPV EVENT 25: OUTDOOR THEATRE Live From Coachella','OUTDOOR THEATRE Live From Coachella', None, None),
    ('PPV02 | UFC 327: English Apr 14 4:30 PM',          'UFC 327: English',  ('4','30','PM'), None),
]
for name, want_title, want_time, want_date in cases:
    tm = title_re.search(name)
    got_title = tm.group('title').strip() if tm else None
    tim = time_re.search(name); got_time = (tim.group('hour'), tim.group('minute'), tim.group('ampm')) if tim else None
    dm = date_re.search(name);  got_date = (dm.group('month'), dm.group('day')) if dm else None
    assert got_title == want_title, f'Title mismatch for {name!r}: {got_title!r}'
    assert got_time  == want_time,  f'Time  mismatch for {name!r}: {got_time!r}'
    assert got_date  == want_date,  f'Date  mismatch for {name!r}: {got_date!r}'
print('[5] Regex patterns match all 4 real channel names correctly')

# Cleanup for next tasks
EPGSource.objects.filter(name='ECM Managed Dummy').delete()
print('Deleted ECM Managed Dummy for clean state')
" 2>&1 | grep -E '^\[|Deleted|Error|Traceback'
```

Expected lines:
```
[0] EPGSource.name uniqueness confirmed
[1] Created source id=<N>
[2] Idempotent: same id <N>
[3] Refresh: upcoming_title_template now 'No Events'
[4] User extras preserved
[5] Regex patterns match all 4 real channel names correctly
Deleted ECM Managed Dummy for clean state
```

- [ ] **Step 3: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: add managed dummy EPGSource helper

_get_or_create_managed_epg_source creates 'ECM Managed Dummy' on
first use and idempotently refreshes the plugin-owned keys in
custom_properties on subsequent calls. User-added keys are preserved."
```

---

## Task 3: Attach + detach helpers

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Add `_attach_managed_epg` immediately after `_get_or_create_managed_epg_source`**

```python
    def _attach_managed_epg(self, channels, managed_source, logger):
        """Bind each channel in `channels` to the managed dummy source via an EPGData row.

        Only touches channels where epg_data IS NULL. Returns list of channel IDs that
        were attached (for result reporting).
        """
        from apps.epg.models import EPGData

        attached_ids = []
        channels_to_update = []

        for channel in channels:
            if channel.epg_data_id is not None:
                continue
            try:
                epg_data, _ = EPGData.objects.get_or_create(
                    tvg_id=str(channel.uuid),
                    epg_source=managed_source,
                    defaults={"name": channel.name},
                )
                # Keep EPGData.name in sync with the channel name so {channel_name}
                # in the dummy source's fallback template renders correctly.
                if epg_data.name != channel.name:
                    epg_data.name = channel.name
                    epg_data.save(update_fields=["name"])
            except Exception as e:
                logger.warning(f"{LOG_PREFIX} Failed to get_or_create EPGData for channel {channel.id}: {e}")
                continue

            channel.epg_data = epg_data
            channels_to_update.append(channel)
            attached_ids.append(channel.id)

        if channels_to_update:
            with transaction.atomic():
                Channel.objects.bulk_update(channels_to_update, ["epg_data"])
            logger.info(f"{LOG_PREFIX} Attached managed EPG to {len(channels_to_update)} channel(s)")
        return attached_ids
```

- [ ] **Step 2: Add `_detach_managed_epg` immediately after `_attach_managed_epg`**

```python
    def _detach_managed_epg(self, managed_source, keep_channel_ids, logger):
        """Set epg_data=None on any channel currently bound to the managed source
        whose id is NOT in keep_channel_ids. Returns list of detached channel IDs.
        """
        if managed_source is None:
            return []

        stale = list(Channel.objects.filter(
            epg_data__epg_source=managed_source
        ).exclude(id__in=keep_channel_ids))

        if not stale:
            return []

        for ch in stale:
            ch.epg_data = None

        with transaction.atomic():
            Channel.objects.bulk_update(stale, ["epg_data"])

        detached_ids = [ch.id for ch in stale]
        logger.info(f"{LOG_PREFIX} Detached managed EPG from {len(detached_ids)} channel(s)")
        return detached_ids
```

- [ ] **Step 3: Syntax check + deploy + verify**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, logging
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logger = logging.getLogger('t'); logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)
from apps.channels.models import Channel, ChannelProfile, ChannelProfileMembership, ChannelGroup
from apps.epg.models import EPGSource, EPGData

p = m.Plugin()
settings = {'dummy_epg_event_duration_hours':'3','dummy_epg_offline_title':'Offline','dummy_epg_event_timezone':'US/Eastern'}
src = p._get_or_create_managed_epg_source(settings, logger)

# Pick 3 channels from the PPV Live Events group that currently have no EPG
group = ChannelGroup.objects.filter(name__iexact='PPV Live Events').first()
candidates = list(Channel.objects.filter(channel_group=group, epg_data__isnull=True)[:3])
assert len(candidates) >= 2, 'Need at least 2 no-EPG channels for the test'
print(f'Candidates: {[c.id for c in candidates]}')

# Attach
ids = p._attach_managed_epg(candidates, src, logger)
print(f'[1] Attached: {ids}')
# Reload and verify binding
for c in candidates:
    c.refresh_from_db()
    assert c.epg_data is not None and c.epg_data.epg_source_id == src.id
    assert c.epg_data.tvg_id == str(c.uuid)
    assert c.epg_data.name == c.name
print('[2] All candidates bound to managed source with correct tvg_id + name')

# Re-attach is idempotent — no new EPGData rows
before = EPGData.objects.filter(epg_source=src).count()
p._attach_managed_epg(candidates, src, logger)
after = EPGData.objects.filter(epg_source=src).count()
assert after == before, f'EPGData row count changed: {before} -> {after}'
print(f'[3] Idempotent: EPGData count stable at {after}')

# Detach: keep only the first, detach the rest
keep = {candidates[0].id}
detached = p._detach_managed_epg(src, keep, logger)
print(f'[4] Detached: {detached}')
for c in candidates[1:]:
    c.refresh_from_db()
    assert c.epg_data is None
candidates[0].refresh_from_db()
assert candidates[0].epg_data is not None
print('[5] Kept channel still bound; others unbound')

# Full cleanup
p._detach_managed_epg(src, set(), logger)
EPGData.objects.filter(epg_source=src).delete()
EPGSource.objects.filter(name='ECM Managed Dummy').delete()
print('Cleaned up test artifacts')
" 2>&1 | grep -E '^\[|Cleaned|Candidates|Error|Traceback'
```

Expected: `[1]` through `[5]` all PASS, then `Cleaned up test artifacts`.

- [ ] **Step 4: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: add attach/detach helpers for managed dummy EPG

_attach_managed_epg get_or_creates EPGData per channel (keyed by
channel.uuid) and bulk_updates the epg_data FK. _detach_managed_epg
clears epg_data on channels bound to the managed source that aren't
in the keep set. Both idempotent."
```

---

## Task 4: Scan integration

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Add `_run_managed_epg_pass` orchestrator method after `_detach_managed_epg`**

```python
    def _run_managed_epg_pass(self, settings, logger, dry_run, enabled_channel_ids):
        """Attach/detach the plugin's managed dummy EPG based on current settings.

        If the master toggle is off, still runs the detach cleanup so turning the
        feature off reliably un-assigns managed EPG. Returns (attached_ids, detached_ids).

        Dry-run is a pure preview: it NEVER creates the EPGSource row and NEVER writes
        attach/detach changes. It only reports what an applied run would do.
        """
        from apps.epg.models import EPGSource

        toggle_on = self._get_bool_setting(settings, "manage_dummy_epg", False)

        if dry_run:
            # Pure preview — locate existing source only; do not create.
            managed_source = EPGSource.objects.filter(
                name="ECM Managed Dummy", source_type="dummy"
            ).first()
            if managed_source is None:
                return [], []
            if toggle_on:
                attached_ids = list(Channel.objects.filter(
                    id__in=enabled_channel_ids, epg_data__isnull=True
                ).values_list("id", flat=True))
                detached_ids = list(Channel.objects.filter(
                    epg_data__epg_source=managed_source
                ).exclude(id__in=enabled_channel_ids).values_list("id", flat=True))
            else:
                attached_ids = []
                detached_ids = list(Channel.objects.filter(
                    epg_data__epg_source=managed_source
                ).values_list("id", flat=True))
            logger.info(f"{LOG_PREFIX} [dry-run] Managed EPG would attach {len(attached_ids)}, detach {len(detached_ids)}")
            return attached_ids, detached_ids

        # Applied run — may create/refresh the source row.
        if toggle_on:
            managed_source = self._get_or_create_managed_epg_source(settings, logger)
        else:
            managed_source = EPGSource.objects.filter(
                name="ECM Managed Dummy", source_type="dummy"
            ).first()

        if managed_source is None:
            return [], []

        attached_ids = []
        if toggle_on:
            no_epg_channels = list(Channel.objects.filter(
                id__in=enabled_channel_ids, epg_data__isnull=True
            ))
            attached_ids = self._attach_managed_epg(no_epg_channels, managed_source, logger)

        keep_ids = set(enabled_channel_ids) if toggle_on else set()
        detached_ids = self._detach_managed_epg(managed_source, keep_ids, logger)

        return attached_ids, detached_ids
```

- [ ] **Step 2: Wire the pass BEFORE the results-building loop**

The existing `for channel_info in channels_for_duplicate_check:` results loop uses these sets, so the pass must run first. The `duplicate_hide_list` is also needed for the enabled-set computation, so the pass goes immediately after `duplicate_hide_list = self._handle_duplicates(...)`.

**Pre-flight — confirm identifiers.** Before typing the block below, grep the current plugin.py to confirm the dict keys and names referenced here haven't drifted. Run:

```bash
grep -n "channels_for_duplicate_check.append\|channels_to_hide = \[\]\|channels_to_show = \[\]\|duplicate_hide_list = self._handle_duplicates" \
  /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py
```

Expected: `channels_for_duplicate_check.append({...})` shows a dict with keys including `channel_id` and `current_visible`; `channels_to_hide = []` and `channels_to_show = []` exist; `duplicate_hide_list = self._handle_duplicates(...)` exists. If any identifier or dict key has drifted, adjust Step 2a accordingly before typing it — a `KeyError` at runtime is the failure mode.

**2a. Initialize the managed-EPG sets unconditionally** immediately after the `duplicate_hide_list = self._handle_duplicates(...)` call (~line 2216, right before `# Build final results with duplicate information`):

```python
            # Managed Dummy EPG pass — runs before results are built so per-channel
            # result dicts can report managed_epg_assigned / managed_epg_detached.
            # Compute the "enabled after this scan" set from in-memory decisions so
            # dry-run and applied-run paths produce identical attach/detach counts.
            managed_attached_set = set()
            managed_detached_set = set()
            enabled_channel_ids = [
                ch["channel_id"] for ch in channels_for_duplicate_check
                if (
                    (ch["current_visible"] and ch["channel_id"] not in channels_to_hide)
                    or ch["channel_id"] in channels_to_show
                ) and ch["channel_id"] not in duplicate_hide_list
            ]
            managed_attached_ids, managed_detached_ids = self._run_managed_epg_pass(
                settings, logger, dry_run, enabled_channel_ids
            )
            managed_attached_set = set(managed_attached_ids)
            managed_detached_set = set(managed_detached_ids)
```

Indentation is the same as the surrounding code in the method body (four levels of 4-space indent inside `_scan_and_update_channels` — match the existing `duplicate_hide_list = self._handle_duplicates(...)` line). Do NOT put this inside the `if not dry_run` visibility-apply block further down; the sets must exist on every path.

**2b.** The pass no longer takes `profile_ids` (signature in Task 4 Step 1 already dropped it) — don't pass it. `_attach_managed_epg` and `_detach_managed_epg` use `bulk_update` which is already a no-op for an empty list, so no extra guards are needed.

- [ ] **Step 3: Syntax check + deploy + verify the toggle controls the pass end-to-end**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, json, logging
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logger = logging.getLogger('sc'); logger.setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING)
from apps.channels.models import Channel, ChannelProfile, ChannelProfileMembership
from apps.epg.models import EPGSource, EPGData

with open('/data/event_channel_managarr_settings.json') as f:
    s = json.load(f)
s['manage_dummy_epg'] = True
s['dummy_epg_event_duration_hours'] = '3'
s['dummy_epg_offline_title'] = 'Offline'
s['dummy_epg_event_timezone'] = 'US/Eastern'
s['hide_rules_priority'] = '[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]'

p = m.Plugin()

# Count visible channels with no EPG before scan
before_noepg = Channel.objects.filter(
    channelprofilemembership__channel_profile__name__iexact='a',
    channelprofilemembership__enabled=True,
    channel_group__name='PPV Live Events',
    epg_data__isnull=True,
).count()
print(f'[pre] Visible-no-EPG channels in PPV Live Events: {before_noepg}')

# Run applied scan (not dry) — should attach EPG
r = p._scan_and_update_channels(s, logger, dry_run=False, is_scheduled_run=False)
print(f'[scan] status={r.get(\"status\")}')

src = EPGSource.objects.get(name='ECM Managed Dummy')
bound = Channel.objects.filter(epg_data__epg_source=src).count()
print(f'[1] Managed source id={src.id}, channels bound={bound}')
assert bound >= 1

# Toggle off and re-scan — all should be detached
s['manage_dummy_epg'] = False
r2 = p._scan_and_update_channels(s, logger, dry_run=False, is_scheduled_run=False)
print(f'[scan-off] status={r2.get(\"status\")}')
bound2 = Channel.objects.filter(epg_data__epg_source=src).count()
print(f'[2] After toggle-off: channels bound={bound2}')
assert bound2 == 0

# Source still exists
assert EPGSource.objects.filter(id=src.id).exists()
print(f'[3] Managed source preserved after toggle-off')

# Cleanup
EPGData.objects.filter(epg_source=src).delete()
EPGSource.objects.filter(name='ECM Managed Dummy').delete()
print('Cleanup done')
" 2>&1 | grep -E '^\[|Cleanup|Error|Traceback'
```

Expected: `[1]` bound≥1 and passing, `[2]` bound=0, `[3]` source preserved, `Cleanup done`.

- [ ] **Step 4: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: integrate managed dummy EPG pass into scan

Adds _run_managed_epg_pass which attaches managed EPG to visible
channels without EPG (when the toggle is on) and always runs the
detach cleanup so turning the feature off reliably unbinds channels.
Wired in after visibility updates in _scan_and_update_channels, before
auto_set_dummy_epg_on_hide."
```

---

## Task 5: Results and CSV schema

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Extend the per-channel result dicts with managed-EPG flags**

Find the result-building loop `for channel_info in channels_for_duplicate_check:` inside `_scan_and_update_channels` (around line 2215). Locate the `results.append({ ... })` inside that loop. Add two new keys to the dict:

```python
                    "managed_epg_assigned": channel_id in managed_attached_set,
                    "managed_epg_detached": channel_id in managed_detached_set,
```

Do the same (but always `False`) for the two earlier `results.append` calls inside the ignore-regex and force-visible branches — they run before the managed-EPG pass, so they never get attached or detached in the same scan. Add:

```python
                        "managed_epg_assigned": False,
                        "managed_epg_detached": False,
```

to both of those result dicts.

- [ ] **Step 2: Extend CSV fieldnames and rule-stats**

Find the CSV export block inside `_scan_and_update_channels` (around line 2255). Update the `fieldnames` list:

```python
                fieldnames = ['channel_id', 'channel_name', 'channel_number', 'channel_group',
                            'current_visibility', 'action', 'reason', 'hide_rule', 'has_epg',
                            'managed_epg_assigned', 'managed_epg_detached']
```

- [ ] **Step 3: Append a summary line to the user-visible message**

Inside `_scan_and_update_channels`, find the `message_parts = [` block (around line 2380). Add a line immediately after the existing `• Duplicate channels hidden: ...` entry:

```python
                f"• Managed EPG: {len(managed_attached_set)} attached, {len(managed_detached_set)} detached",
```

- [ ] **Step 4: Add counts to the returned `results` dict**

Find the final `return { "status": "success", ... "results": { ... } }` at the end of the method. Add two keys to the inner `results`:

```python
                    "managed_epg_attached": len(managed_attached_set),
                    "managed_epg_detached": len(managed_detached_set),
```

- [ ] **Step 5: Syntax check + deploy + verify CSV and result shape**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, json, logging
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logger = logging.getLogger('r'); logger.setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING)
from apps.epg.models import EPGSource, EPGData

with open('/data/event_channel_managarr_settings.json') as f:
    s = json.load(f)
s['manage_dummy_epg'] = True
s['hide_rules_priority'] = '[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]'
p = m.Plugin()
r = p._scan_and_update_channels(s, logger, dry_run=False, is_scheduled_run=False)
res = r.get('results', {})
print(f'[1] results.managed_epg_attached={res.get(\"managed_epg_attached\")}')
print(f'[2] results.managed_epg_detached={res.get(\"managed_epg_detached\")}')
assert 'managed_epg_attached' in res and 'managed_epg_detached' in res

# Check on-disk results file has per-channel flags
with open('/data/event_channel_managarr_results.json') as f:
    disk = json.load(f)
sample = next((row for row in disk['results'] if row.get('managed_epg_assigned')), None)
print(f'[3] sample row with managed_epg_assigned=True: {sample is not None}')
assert sample is not None
print(f'    keys: {sorted(sample.keys())}')

# Cleanup
src = EPGSource.objects.filter(name='ECM Managed Dummy').first()
if src:
    EPGData.objects.filter(epg_source=src).delete()
    from apps.channels.models import Channel
    Channel.objects.filter(epg_data__epg_source=src).update(epg_data=None)
    src.delete()
print('Cleanup done')
" 2>&1 | grep -E '^\[|Cleanup|keys:|Error|Traceback'
```

Expected: `[1]` and `[2]` return integers; `[3]` True with expected keys listed.

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: add managed-EPG attach/detach counters to results

Per-channel result dicts gain managed_epg_assigned/detached booleans;
CSV fieldnames updated to include both. Summary message and returned
results dict expose attached/detached counts for downstream GUI."
```

---

## Task 6: End-to-end verification matrix

**Files:**
- None (read-only verification).

- [ ] **Step 1: Run the full 10-step matrix from the spec**

Deploy the final plugin.py and run this single script, which walks through all verification scenarios back-to-back:

```bash
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, json, logging
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logger = logging.getLogger('e2e'); logger.setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING)
from apps.channels.models import Channel, ChannelProfile, ChannelProfileMembership, ChannelGroup
from apps.epg.models import EPGSource, EPGData

# Load user's real settings, then overlay test values
with open('/data/event_channel_managarr_settings.json') as f:
    base = json.load(f)
base['hide_rules_priority'] = '[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]'
p = m.Plugin()

# Ensure clean slate
src0 = EPGSource.objects.filter(name='ECM Managed Dummy').first()
if src0:
    EPGData.objects.filter(epg_source=src0).delete()
    Channel.objects.filter(epg_data__epg_source=src0).update(epg_data=None)
    src0.delete()

# Scenario 1: toggle off → no source created
s1 = dict(base); s1['manage_dummy_epg'] = False
p._scan_and_update_channels(s1, logger, dry_run=False, is_scheduled_run=False)
assert not EPGSource.objects.filter(name='ECM Managed Dummy').exists()
print('[1] Toggle off: no ECM source created. PASS')

# Scenario 2: first enable
s2 = dict(base); s2['manage_dummy_epg'] = True
p._scan_and_update_channels(s2, logger, dry_run=False, is_scheduled_run=False)
src = EPGSource.objects.get(name='ECM Managed Dummy')
bound = Channel.objects.filter(epg_data__epg_source=src).count()
assert bound > 0
print(f'[2] First enable: source created, {bound} channels bound. PASS')

# Scenario 3: re-scan idempotent
before_custom = dict(src.custom_properties)
before_epgdata = EPGData.objects.filter(epg_source=src).count()
p._scan_and_update_channels(s2, logger, dry_run=False, is_scheduled_run=False)
src.refresh_from_db()
assert dict(src.custom_properties) == before_custom
assert EPGData.objects.filter(epg_source=src).count() == before_epgdata
print('[3] Re-scan idempotent: custom_properties + EPGData count unchanged. PASS')

# Scenario 4: setting change propagates
s4 = dict(s2); s4['dummy_epg_offline_title'] = 'Channel is Offline'
p._scan_and_update_channels(s4, logger, dry_run=False, is_scheduled_run=False)
src.refresh_from_db()
assert src.custom_properties['upcoming_title_template'] == 'Channel is Offline'
print('[4] Setting change: upcoming_title_template updated. PASS')

# Scenario 5: channel with real EPG is untouched. Wrapped in try/finally so a
# downstream assertion failure can't leave the channel pointing at a fake source
# that the cleanup block will later delete.
victim = Channel.objects.filter(epg_data__epg_source=src).first()
victim_original_epg = victim.epg_data_id
fake_source = None; fake_data = None
try:
    victim.epg_data = None; victim.save()
    fake_source, _ = EPGSource.objects.get_or_create(name='fake-xmltv-for-test', defaults={'source_type': 'xmltv'})
    fake_data, _ = EPGData.objects.get_or_create(tvg_id='fake-id', epg_source=fake_source, defaults={'name': 'fake'})
    victim.epg_data = fake_data; victim.save()
    p._scan_and_update_channels(s4, logger, dry_run=False, is_scheduled_run=False)
    victim.refresh_from_db()
    assert victim.epg_data_id == fake_data.id
    print('[5] Channel with real EPG left alone. PASS')

    # Scenario 6: toggle off detaches
    s6 = dict(s4); s6['manage_dummy_epg'] = False
    p._scan_and_update_channels(s6, logger, dry_run=False, is_scheduled_run=False)
    bound_after = Channel.objects.filter(epg_data__epg_source=src).count()
    assert bound_after == 0
    assert EPGSource.objects.filter(id=src.id).exists()
    print(f'[6] Toggle off: {bound_after} channels bound, source preserved. PASS')

    # Scenario 7: re-enable re-adopts
    p._scan_and_update_channels(s4, logger, dry_run=False, is_scheduled_run=False)
    bound_again = Channel.objects.filter(epg_data__epg_source=src).count()
    assert bound_again > 0
    print(f'[7] Re-enable: {bound_again} channels re-adopted. PASS')

    # Scenario 8: dry run is a pure preview — binding count and source properties
    # must both be unchanged.
    s8 = dict(s4); s8['dummy_epg_offline_title'] = 'DryRunValue'
    src.refresh_from_db()
    before_props = dict(src.custom_properties)
    p._scan_and_update_channels(s8, logger, dry_run=True, is_scheduled_run=False)
    src.refresh_from_db()
    after_props = dict(src.custom_properties)
    bound_after_dry = Channel.objects.filter(epg_data__epg_source=src).count()
    assert bound_after_dry == bound_again, 'Dry run changed binding count'
    assert before_props == after_props, f'Dry run mutated custom_properties: {before_props!r} -> {after_props!r}'
    print(f'[8] Dry run: binding count unchanged ({bound_after_dry}) and source properties unchanged. PASS')
finally:
    # Always unbind victim from the fake source BEFORE deleting it — otherwise
    # the FK on Channel.epg_data may PROTECT or CASCADE unexpectedly.
    if fake_data is not None:
        Channel.objects.filter(epg_data=fake_data).update(epg_data=None)
        fake_data.delete()
    if fake_source is not None:
        fake_source.delete()
    # Restore victim's original epg_data so subsequent runs see the same state.
    if victim_original_epg is not None:
        Channel.objects.filter(id=victim.id).update(epg_data_id=victim_original_epg)

# Leave the user's managed source alone since they may now want it running
print('E2E complete')
" 2>&1 | grep -E '^\[|E2E|Error|Traceback'
```

Expected: `[1]`..`[8]` all PASS, then `E2E complete`.

- [ ] **Step 2: Render the guide for one managed channel and eyeball it**

Pick one visible PPV channel with a parseable time (e.g., `PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)`) and one without (e.g., `LIVE EVENT 01   9:45am Suslenkov v Mann`). Hit Dispatcharr's EPG endpoint or open the guide UI and confirm:

- Dated PPV → guide shows the event title during the 3-hour window starting at 8:30 PM ET (converted to local) and "Offline" outside that window.
- Undated LIVE → guide shows the channel name across 24h (fallback template active).

No assertion script — this is a visual check.

- [ ] **Step 3: Commit the spec + plan**

Only commit now if the user hasn't already: this task produced no code, just confirmed behavior. The spec and plan files should land alongside the final implementation:

```bash
git add docs/superpowers/specs/2026-04-17-managed-dummy-epg-design.md \
        docs/superpowers/plans/2026-04-17-managed-dummy-epg-plan.md
git commit -m "docs: managed dummy EPG design + implementation plan"
```

---

## Self-Review

**Spec coverage:**
- 4 settings → Task 1.
- Shared managed EPGSource + custom_properties → Task 2.
- Per-channel EPGData + epg_data FK attach → Task 3.
- Detach on toggle-off and on channel-no-longer-in-target → Task 3 + Task 4 (the pass always calls detach).
- Integration point (after visibility, before auto EPG removal) → Task 4 step 2.
- Dry-run behavior (skip writes) → Task 4 `_run_managed_epg_pass` handles dry_run.
- CSV / results additions → Task 5.
- Cancellation via `_op_stop_event` — left to rely on the existing scan-loop stop check; no per-channel stop in `_attach_managed_epg`, acceptable because the attach loop is short (< 100 channels for this user). Flagged as a follow-up if the operator ever scans thousands of no-EPG channels.
- 10 verification scenarios → Task 6 covers 1–8. Scenarios 9 (guide render) and 10 (timezone shift) are visual — Task 6 Step 2 covers 9, and 10 requires changing the setting and waiting for EPG cache eviction; documented as manual follow-up.

**Placeholder scan:** no TBDs, no "add appropriate X", no "see Task N", no method references without a defining task.

**Type consistency:** `_attach_managed_epg(channels, managed_source, logger)` signature matches its call site in Task 4 Step 1. `_detach_managed_epg(managed_source, keep_channel_ids, logger)` signature matches. `_run_managed_epg_pass` returns `(attached_ids, detached_ids)` — both a list; Task 5 Step 1 uses `managed_attached_set = set(managed_attached_ids)` compatibly. `channel.uuid` field assumed present on `Channel`; used by Dispatcharr's own `generate_custom_dummy_programs` (`str(channel.uuid)`), so safe.

