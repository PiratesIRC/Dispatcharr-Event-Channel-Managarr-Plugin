# Dispatcharr Modernization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt Dispatcharr v0.20+ plugin UX conventions already in use by EPG-Janitor, Lineuparr, and Stream-Mapparr: structured action-button styling with confirm dialogs, refined field types (`text` / `number`), section-header dividers to group ECM's now-19 settings into logical panels, and an optional `SmartRateLimiter` to pace per-channel ORM writes.

**Architecture:** UI-metadata changes land in `plugin.json` (which Dispatcharr reads at install time and the frontend renders from). The Python `@property def fields` in `plugin.py` must match — both are kept in sync. `SmartRateLimiter` is a ~40-line helper class alongside `ProgressTracker`, wired into the per-channel loop inside `_scan_and_update_channels` and the attach loop inside `_attach_managed_epg` where real per-item ORM work happens.

**Tech Stack:** JSON (plugin manifest), Python 3, Django ORM, Dispatcharr plugin schema.

**Spec in this doc's predecessor context:** see prior-turn tier breakdown. Short version: Tier 1 = `plugin.json` polish; Tier 2 = rate-limiting feature. ProgressTracker, version-status field, and settings-file flock are already in v0.7.0.

**Verification harness:** no pytest suite. Each task ends with a syntax check + deploy + live load via `docker exec dispatcharr python3 /app/manage.py shell`. Visual UI check is Task 4.

---

## File Structure

- **Modify:** `Event-Channel-Managarr/plugin.json` — field schema, action metadata, version bump.
- **Modify:** `Event-Channel-Managarr/plugin.py` — `PluginConfig` version string, `@property def fields` schema, new `SmartRateLimiter` class, new `rate_limiting` setting, wire-in points.

Single-file convention preserved. No new modules.

---

## Task 1: Overhaul plugin.json — field types, section dividers, action styling, version bump

**Files:**
- Modify: `Event-Channel-Managarr/plugin.json`

- [ ] **Step 1: Rewrite plugin.json**

Open `Event-Channel-Managarr/plugin.json` and replace the entire contents with:

```json
{
  "name": "Event Channel Managarr",
  "version": "0.8.0",
  "description": "Automates channel visibility by hiding channels without events and showing those with events, based on EPG data and channel names. Optionally manages dummy EPG for channels without real EPG.",
  "author": "PiratesIRC",
  "license": "MIT",
  "repo_url": "https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin",
  "min_dispatcharr_version": "v0.20.0",
  "fields": [
    {"id": "_section_scope", "label": "📍 Scope", "type": "info", "description": "Which channels this plugin monitors and how it identifies them."},
    {"id": "timezone", "label": "🌍 Timezone", "type": "select", "default": "America/Chicago"},
    {"id": "channel_profile_name", "label": "📺 Channel Profile Names (Required)", "type": "text", "default": "", "placeholder": "e.g. All, Favorites"},
    {"id": "channel_groups", "label": "📂 Channel Groups", "type": "text", "default": "", "placeholder": "e.g. PPV Live Events, Sports"},
    {"id": "name_source", "label": "🔤 Name Source", "type": "select", "default": "Channel_Name"},

    {"id": "_section_rules", "label": "🎯 Hide Rules", "type": "info", "description": "Priority-ordered rules that decide which channels to hide."},
    {"id": "hide_rules_priority", "label": "📜 Hide Rules Priority", "type": "text", "default": "[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]"},
    {"id": "regex_channels_to_ignore", "label": "🚫 Regex: Channel Names to Ignore", "type": "text", "default": ""},
    {"id": "regex_mark_inactive", "label": "💤 Regex: Mark Channel as Inactive", "type": "text", "default": ""},
    {"id": "regex_force_visible", "label": "✅ Regex: Force Visible Channels", "type": "text", "default": ""},
    {"id": "past_date_grace_hours", "label": "📅 Past Date Grace Period (Hours)", "type": "number", "default": 4},

    {"id": "_section_duplicates", "label": "🎭 Duplicates", "type": "info", "description": "How to handle channels whose events collide."},
    {"id": "duplicate_strategy", "label": "🎭 Duplicate Handling Strategy", "type": "select", "default": "lowest_number"},
    {"id": "keep_duplicates", "label": "🔄 Keep Duplicate Channels", "type": "boolean", "default": false},

    {"id": "_section_epg", "label": "🔌 EPG Management", "type": "info", "description": "Optional automation for EPG assignment on visibility changes and a managed dummy EPG for channels without real EPG."},
    {"id": "auto_set_dummy_epg_on_hide", "label": "🔌 Auto-Remove EPG on Hide", "type": "boolean", "default": true},
    {"id": "manage_dummy_epg", "label": "🗓️ Manage Dummy EPG", "type": "boolean", "default": false},
    {"id": "dummy_epg_event_duration_hours", "label": "⏱️ Event Duration (hours)", "type": "number", "default": 3},
    {"id": "dummy_epg_offline_title", "label": "💤 Offline Title", "type": "text", "default": "Offline"},
    {"id": "dummy_epg_event_timezone", "label": "📺 Channel Name Event Timezone", "type": "select", "default": "US/Eastern"},

    {"id": "_section_scheduling", "label": "⏰ Scheduling & Export", "type": "info", "description": "Scheduled runs and CSV export options."},
    {"id": "scheduled_times", "label": "⏰ Scheduled Run Times (24-hour)", "type": "text", "default": "", "placeholder": "0600,1300,1800"},
    {"id": "enable_scheduled_csv_export", "label": "📄 Enable Scheduled CSV Export", "type": "boolean", "default": false},

    {"id": "_section_advanced", "label": "⚙️ Advanced", "type": "info", "description": "Performance and pacing controls for large channel profiles."},
    {"id": "rate_limiting", "label": "🐢 Rate Limiting", "type": "select", "default": "none"}
  ],
  "actions": [
    {"id": "validate_configuration", "label": "Validate Configuration", "description": "Test and validate all plugin settings", "button_label": "🔎 Validate", "button_variant": "outline", "button_color": "blue"},
    {"id": "update_schedule", "label": "Update Schedule", "description": "Save settings and update the scheduled run times", "button_label": "💾 Save Schedule", "button_variant": "filled", "button_color": "green"},
    {"id": "dry_run", "label": "Dry Run (Export to CSV)", "description": "Preview which channels would be hidden/shown without making changes", "button_label": "👁️ Dry Run", "button_variant": "outline", "button_color": "cyan"},
    {"id": "run_now", "label": "Run Now", "description": "Immediately scan and update channel visibility based on current EPG data", "button_label": "▶️ Run Now", "button_variant": "filled", "button_color": "green", "confirm": {"message": "This will apply visibility changes and (if enabled) attach/detach managed EPG. Continue?"}},
    {"id": "remove_epg_from_hidden", "label": "Remove EPG from Hidden Channels", "description": "Remove all EPG data from channels that are disabled/hidden in the selected profile", "button_label": "🧹 Remove EPG from Hidden", "button_variant": "filled", "button_color": "red", "confirm": {"message": "This will CLEAR EPG data from every hidden channel in the selected profile. Cannot be undone by this plugin. Continue?"}},
    {"id": "clear_csv_exports", "label": "Clear CSV Exports", "description": "Delete all CSV export files created by this plugin", "button_label": "🗑️ Clear CSV Exports", "button_variant": "filled", "button_color": "red", "confirm": {"message": "This will delete every CSV file in /data/exports created by this plugin. Continue?"}},
    {"id": "cleanup_periodic_tasks", "label": "Cleanup Orphaned Tasks", "description": "Remove any orphaned Celery periodic tasks from old plugin versions", "button_label": "🧼 Cleanup Orphaned Tasks", "button_variant": "outline", "button_color": "orange", "confirm": {"message": "This removes orphaned Celery periodic tasks left by older plugin versions. Continue?"}},
    {"id": "check_scheduler_status", "label": "Check Scheduler Status", "description": "Display scheduler thread status and diagnostic information", "button_label": "🩺 Check Scheduler", "button_variant": "outline", "button_color": "blue"}
  ]
}
```

- [ ] **Step 2: Validate JSON**

```bash
python3 -c "import json; d = json.load(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.json')); print(f'OK. {len(d[\"fields\"])} fields, {len(d[\"actions\"])} actions, version {d[\"version\"]!r}, min dispatcharr {d[\"min_dispatcharr_version\"]!r}')"
```

Expected: `OK. 21 fields, 8 actions, version '0.8.0', min dispatcharr 'v0.20.0'`

- [ ] **Step 3: Check section dividers all have unique `id` starting with `_section_` and type `info` with `description`**

```bash
python3 -c "
import json
d = json.load(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.json'))
sections = [f for f in d['fields'] if f['id'].startswith('_section_')]
assert all(f['type'] == 'info' for f in sections), 'All section dividers must be type=info'
assert all('description' in f for f in sections), 'All section dividers must have description'
ids = [f['id'] for f in sections]
assert len(ids) == len(set(ids)), f'Duplicate section ids: {ids}'
print(f'All {len(sections)} section dividers well-formed')
"
```

Expected: `All 6 section dividers well-formed`

- [ ] **Step 4: Deploy plugin.json and confirm Dispatcharr reloads schema without error**

```bash
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.json dispatcharr:/data/plugins/event-channel-managarr/plugin.json
docker exec dispatcharr python3 -c "
import json
with open('/data/plugins/event-channel-managarr/plugin.json') as f:
    d = json.load(f)
print(f'Deployed plugin.json: version={d[\"version\"]}, fields={len(d[\"fields\"])}, actions={len(d[\"actions\"])}')
"
```

Expected: `Deployed plugin.json: version=0.8.0, fields=21, actions=8`

- [ ] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/plugin.json
git commit -m "$(cat <<'EOF'
Modernize plugin.json UI: sections, field types, action styling

Adopts Dispatcharr v0.20+ plugin conventions used by EPG-Janitor,
Lineuparr, and Stream-Mapparr:
- Six section dividers (type: info) group the 21 settings into Scope,
  Hide Rules, Duplicates, EPG Management, Scheduling & Export, and
  Advanced sections.
- Text-heavy fields switch from type: string to type: text, and
  numeric fields (past_date_grace_hours, dummy_epg_event_duration_hours)
  switch to type: number. Placeholders added where helpful.
- Action buttons gain button_label (emoji), button_variant
  (filled/outline), button_color (green/red/blue/cyan/orange), and
  confirm dialogs on destructive or side-effecting actions.
- Adds rate_limiting select field (wiring lands in later commits).
- Bumps plugin version to 0.8.0.

Runtime behavior unchanged — plugin.py still drives settings via its
@property fields. Task to follow aligns plugin.py with this schema.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Align `plugin.py` `@property def fields` with new schema, bump version, add `rate_limiting` handling

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Bump `PluginConfig.PLUGIN_VERSION`**

Find the line `PLUGIN_VERSION = "0.7.0"` at the top of `PluginConfig` and change to:

```python
    PLUGIN_VERSION = "0.8.0"
```

- [ ] **Step 2: Add `DEFAULT_RATE_LIMITING` constant**

Immediately after `DEFAULT_DUMMY_EPG_TIMEZONE = "US/Eastern"` in `PluginConfig`, add:

```python
    # Pacing for per-channel ORM writes ("none", "low", "medium", "high")
    DEFAULT_RATE_LIMITING = "none"
```

Then add the `Plugin.DEFAULT_RATE_LIMITING = PluginConfig.DEFAULT_RATE_LIMITING` alias after the existing `DEFAULT_DUMMY_EPG_TIMEZONE` alias in the `Plugin` class constants block.

- [ ] **Step 3: Rewrite the `fields` property body**

The `@property def fields(self)` builds a list of field dicts. Replace the `fields_list = [ ... ]` literal (from `{"id": "version_status", ...}` through the closing `]` before `return fields_list`) with the following. Keep the preceding version-check logic that computes `version_message` intact.

```python
        fields_list = [
            {
                "id": "version_status",
                "label": "📦 Plugin Version Status",
                "type": "info",
                "help_text": version_message
            },
            {
                "id": "_section_scope",
                "label": "📍 Scope",
                "type": "info",
                "description": "Which channels this plugin monitors and how it identifies them."
            },
            {
                "id": "timezone",
                "label": "🌍 Timezone",
                "type": "select",
                "default": self.DEFAULT_TIMEZONE,
                "help_text": "Timezone for scheduled runs. Select the timezone for scheduling. Only one can be selected.",
                "options": self._load_timezones_from_file()
            },
            {
                "id": "channel_profile_name",
                "label": "📺 Channel Profile Names (Required)",
                "type": "text",
                "default": "",
                "placeholder": "e.g. All, Favorites",
                "help_text": "REQUIRED: Channel Profile(s) containing channels to monitor. Use comma-separated names for multiple profiles.",
            },
            {
                "id": "channel_groups",
                "label": "📂 Channel Groups",
                "type": "text",
                "default": "",
                "placeholder": "e.g. PPV Live Events, Sports",
                "help_text": "Specific channel groups to monitor within the profile. Leave blank to monitor all groups in the profile.",
            },
            {
                "id": "name_source",
                "label": "🔤 Name Source",
                "type": "select",
                "default": self.DEFAULT_NAME_SOURCE,
                "help_text": "Select the source of the names to monitor. Only one can be selected.",
                "options": [
                    {"label": "Channel Name", "value": "Channel_Name"},
                    {"label": "Stream Name", "value": "Stream_Name"}
                ]
            },
            {
                "id": "_section_rules",
                "label": "🎯 Hide Rules",
                "type": "info",
                "description": "Priority-ordered rules that decide which channels to hide."
            },
            {
                "id": "hide_rules_priority",
                "label": "📜 Hide Rules Priority",
                "type": "text",
                "default": self.DEFAULT_HIDE_RULES,
                "placeholder": "[BlankName],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]",
                "help_text": "Define rules for hiding channels in priority order (first match wins). Comma-separated tags. Available tags: [NoEPG], [BlankName], [WrongDayOfWeek], [NoEventPattern], [EmptyPlaceholder], [ShortDescription], [ShortChannelName], [NumberOnly], [PastDate:days], [PastDate:days:Xh], [FutureDate:days], [UndatedAge:days], [InactiveRegex].",
            },
            {
                "id": "regex_channels_to_ignore",
                "label": "🚫 Regex: Channel Names to Ignore",
                "type": "text",
                "default": "",
                "placeholder": "^BACKUP|^TEST",
                "help_text": "Regular expression to match channel names that should be skipped entirely. Matching channels will not be processed.",
            },
            {
                "id": "regex_mark_inactive",
                "label": "💤 Regex: Mark Channel as Inactive",
                "type": "text",
                "default": "",
                "placeholder": "PLACEHOLDER|TBD|COMING SOON",
                "help_text": "Regular expression to hide channels. This is processed as part of the [InactiveRegex] hide rule.",
            },
            {
                "id": "regex_force_visible",
                "label": "✅ Regex: Force Visible Channels",
                "type": "text",
                "default": "",
                "placeholder": "^NEWS|^WEATHER",
                "help_text": "Regular expression to match channel names that should ALWAYS be visible, overriding any hide rules.",
            },
            {
                "id": "past_date_grace_hours",
                "label": "📅 Past Date Grace Period (Hours)",
                "type": "number",
                "default": int(self.DEFAULT_PAST_DATE_GRACE_HOURS),
                "help_text": "Hours to wait after midnight before hiding past events. Useful for events that run late.",
            },
            {
                "id": "_section_duplicates",
                "label": "🎭 Duplicates",
                "type": "info",
                "description": "How to handle channels whose events collide."
            },
            {
                "id": "duplicate_strategy",
                "label": "🎭 Duplicate Handling Strategy",
                "type": "select",
                "default": self.DEFAULT_DUPLICATE_STRATEGY,
                "help_text": "Strategy to use when multiple channels have the same event.",
                "options": [
                    {"label": "Keep Lowest Channel Number", "value": "lowest_number"},
                    {"label": "Keep Highest Channel Number", "value": "highest_number"},
                    {"label": "Keep Longest Channel Name", "value": "longest_name"}
                ]
            },
            {
                "id": "keep_duplicates",
                "label": "🔄 Keep Duplicate Channels",
                "type": "boolean",
                "default": self.DEFAULT_KEEP_DUPLICATES,
                "help_text": "If enabled, duplicate channels will be kept visible instead of being hidden. The duplicate strategy above will be ignored.",
            },
            {
                "id": "_section_epg",
                "label": "🔌 EPG Management",
                "type": "info",
                "description": "Optional automation for EPG assignment on visibility changes and a managed dummy EPG for channels without real EPG."
            },
            {
                "id": "auto_set_dummy_epg_on_hide",
                "label": "🔌 Auto-Remove EPG on Hide",
                "type": "boolean",
                "default": self.DEFAULT_AUTO_REMOVE_EPG,
                "help_text": "If enabled, automatically removes EPG data from a channel when it is hidden by the plugin.",
            },
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
                "type": "number",
                "default": int(self.DEFAULT_EVENT_DURATION_HOURS),
                "help_text": "How long each scheduled event should appear in the guide (hours). Before and after this window the guide shows the Offline Title.",
            },
            {
                "id": "dummy_epg_offline_title",
                "label": "💤 Offline Title",
                "type": "text",
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
            {
                "id": "_section_scheduling",
                "label": "⏰ Scheduling & Export",
                "type": "info",
                "description": "Scheduled runs and CSV export options."
            },
            {
                "id": "scheduled_times",
                "label": "⏰ Scheduled Run Times (24-hour format)",
                "type": "text",
                "default": "",
                "placeholder": "0600,1300,1800",
                "help_text": "Comma-separated times to run automatically each day (24-hour format). Example: 0600,1300,1800 runs at 6 AM, 1 PM, and 6 PM daily. Leave blank to disable scheduling.",
            },
            {
                "id": "enable_scheduled_csv_export",
                "label": "📄 Enable Scheduled CSV Export",
                "type": "boolean",
                "default": self.DEFAULT_SCHEDULED_CSV_EXPORT,
                "help_text": "If enabled, a CSV file of the scan results will be created when the plugin runs on a schedule. If disabled, no CSV will be created for scheduled runs.",
            },
            {
                "id": "_section_advanced",
                "label": "⚙️ Advanced",
                "type": "info",
                "description": "Performance and pacing controls for large channel profiles."
            },
            {
                "id": "rate_limiting",
                "label": "🐢 Rate Limiting",
                "type": "select",
                "default": self.DEFAULT_RATE_LIMITING,
                "help_text": "Pause between per-channel ORM operations. 'none' is fastest; 'low/medium/high' add 0.05/0.2/0.5 seconds per channel. Useful when scanning very large profiles (thousands of channels) on a small DB.",
                "options": [
                    {"label": "None (fastest)", "value": "none"},
                    {"label": "Low (~0.05s / channel)", "value": "low"},
                    {"label": "Medium (~0.2s / channel)", "value": "medium"},
                    {"label": "High (~0.5s / channel)", "value": "high"}
                ]
            },
        ]

        return fields_list
```

- [ ] **Step 4: Add `rate_limiting` default in `_save_settings`**

Inside `_save_settings`, after the existing `if "dummy_epg_event_timezone" not in settings:` block, add:

```python
            if "rate_limiting" not in settings:
                settings["rate_limiting"] = self.DEFAULT_RATE_LIMITING
```

- [ ] **Step 5: Syntax check + deploy + verify fields load**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
p = m.Plugin()
print(f'Version: {p.version}')
assert p.version == '0.8.0'
ids = [f['id'] for f in p.fields]
expected_new = ['_section_scope','_section_rules','_section_duplicates','_section_epg','_section_scheduling','_section_advanced','rate_limiting']
missing = [x for x in expected_new if x not in ids]
assert not missing, f'Missing fields: {missing}'
# Check a few types
by_id = {f['id']: f for f in p.fields}
assert by_id['channel_profile_name']['type'] == 'text', by_id['channel_profile_name']['type']
assert by_id['past_date_grace_hours']['type'] == 'number', by_id['past_date_grace_hours']['type']
assert by_id['past_date_grace_hours']['default'] == 4, by_id['past_date_grace_hours']['default']
assert by_id['dummy_epg_event_duration_hours']['default'] == 3, by_id['dummy_epg_event_duration_hours']['default']
assert by_id['rate_limiting']['default'] == 'none'
print(f'Field count: {len(p.fields)}')
print('All schema assertions passed')
"
```

Expected: `Version: 0.8.0`, `Field count: 29` (version_status + 6 dividers + 21 settings + 1 rate_limiting = matches new fields), `All schema assertions passed`.

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "$(cat <<'EOF'
Align plugin.py fields property with new plugin.json schema

- PluginConfig.PLUGIN_VERSION bumped to 0.8.0.
- Adds DEFAULT_RATE_LIMITING ("none") and the Plugin alias.
- Rewrites the fields property to emit six section-header dividers
  (type: info with description), switches text-heavy fields to
  type: text, switches the two numeric fields to type: number
  (past_date_grace_hours, dummy_epg_event_duration_hours), and
  appends a new rate_limiting select under an Advanced section.
- _save_settings seeds rate_limiting on first save.

No runtime wiring of rate_limiting yet — the SmartRateLimiter class
and its call sites land in the next commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `SmartRateLimiter` helper class

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Add `SmartRateLimiter` class after `ProgressTracker`**

Find `class ProgressTracker:` (around line 97) and locate its closing `@staticmethod def _format_eta(...)` block. Immediately after `ProgressTracker`'s last line (before the module-level `def _read_last_run():`), insert:

```python
class SmartRateLimiter:
    """Optional per-item pacing for bulk ORM loops.

    Sleeps a configurable amount between .wait() calls. Usage:
        limiter = SmartRateLimiter(settings.get("rate_limiting", "none"))
        for item in items:
            ... do one ORM op ...
            limiter.wait()
    """

    _DELAYS = {
        "none": 0.0,
        "low": 0.05,
        "medium": 0.2,
        "high": 0.5,
    }

    def __init__(self, level):
        level_str = str(level).strip().lower() if level is not None else "none"
        self.delay = self._DELAYS.get(level_str, 0.0)
        self.level = level_str if level_str in self._DELAYS else "none"

    def wait(self):
        if self.delay > 0:
            time.sleep(self.delay)

    def is_active(self):
        return self.delay > 0
```

- [ ] **Step 2: Syntax check + deploy + verify class loads and delays dispatch correctly**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, time
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
# none → zero delay
l = m.SmartRateLimiter('none'); assert l.delay == 0.0 and not l.is_active()
# medium → 0.2s
l = m.SmartRateLimiter('medium'); assert l.delay == 0.2 and l.is_active()
# case insensitive + whitespace
l = m.SmartRateLimiter('  HIGH '); assert l.delay == 0.5
# garbage → none
l = m.SmartRateLimiter('bogus'); assert l.delay == 0.0
l = m.SmartRateLimiter(None);    assert l.delay == 0.0
# timing check — wait() on 'low' should take ~0.05s across 10 iterations ~0.5s
l = m.SmartRateLimiter('low')
t0 = time.time()
for _ in range(10): l.wait()
dt = time.time() - t0
assert 0.4 < dt < 0.8, f'10x low wait took {dt:.3f}s (expected ~0.5s)'
print(f'SmartRateLimiter OK — 10x low wait = {dt:.3f}s')
"
```

Expected: `Syntax OK`, `SmartRateLimiter OK — 10x low wait = 0.5XXs`.

- [ ] **Step 3: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "$(cat <<'EOF'
Add SmartRateLimiter helper for per-item ORM pacing

Small stateless class with four levels (none / low / medium / high)
mapping to sleep durations of 0 / 0.05 / 0.2 / 0.5 seconds per wait()
call. Case-insensitive, whitespace-tolerant; unrecognized values fall
back to 'none'. Patterned after Stream-Mapparr's rate limiter.

Call sites land in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `SmartRateLimiter` into per-channel loops and verify

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Wire into `_scan_and_update_channels` main per-channel loop**

Find the loop `for i, channel in enumerate(channels):` inside `_scan_and_update_channels` (around line 2300 after Task 2 bumps; use grep on the exact text). Immediately before the loop starts, initialize the limiter:

```python
            # Optional pacing for large profiles. Reads from settings each scan so
            # toggling the UI select takes effect on the next run.
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING))
            if rate_limiter.is_active():
                logger.info(f"{LOG_PREFIX} Rate limiting active: {rate_limiter.level} ({rate_limiter.delay}s/channel)")
```

Indentation must match the surrounding method body (same as the `# Initialize progress tracker` line). Then, inside the `for i, channel in enumerate(channels):` loop, at the VERY END of the loop body (after the last `elif action_needed == "show": channels_to_show.append(channel.id)` line), add:

```python
                rate_limiter.wait()
```

Must be indented to match the rest of the loop body. It's the last statement in each iteration — deliberately AFTER the tracker-update branches so pacing happens uniformly per channel regardless of what rule fired.

- [ ] **Step 2: Wire into `_attach_managed_epg` loop**

Find `def _attach_managed_epg(self, channels, managed_source, logger):`. The method receives `channels` (a list) and loops `for channel in channels:` inside a `with transaction.atomic():` block. Change the signature to accept an optional limiter:

```python
    def _attach_managed_epg(self, channels, managed_source, logger, rate_limiter=None):
```

Inside the loop, at the very end of each iteration (after `attached_ids.append(channel.id)`), add:

```python
                if rate_limiter is not None:
                    rate_limiter.wait()
```

Match indentation of the preceding `attached_ids.append(...)` line.

- [ ] **Step 3: Pass the limiter from `_run_managed_epg_pass`**

Find `attached_ids = self._attach_managed_epg(no_epg_channels, managed_source, logger)` inside `_run_managed_epg_pass`. Replace with:

```python
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING))
            attached_ids = self._attach_managed_epg(no_epg_channels, managed_source, logger, rate_limiter=rate_limiter)
```

- [ ] **Step 4: Syntax check + deploy + verify no regression on default ('none') and measurable delay on 'medium'**

```bash
python3 -c "import ast; ast.parse(open('/home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py').read()); print('Syntax OK')"
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, json, logging, time
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logger = logging.getLogger('rl'); logger.setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING)

with open('/data/event_channel_managarr_settings.json') as f:
    base = json.load(f)
base['hide_rules_priority'] = '[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]'

p = m.Plugin()

# Baseline: rate_limiting=none
s1 = dict(base); s1['rate_limiting'] = 'none'; s1['manage_dummy_epg'] = False
t0 = time.time(); p._scan_and_update_channels(s1, logger, dry_run=True, is_scheduled_run=False); dt_none = time.time() - t0
print(f'[1] rate_limiting=none dry-run: {dt_none:.2f}s')

# Medium: should be noticeably slower
s2 = dict(base); s2['rate_limiting'] = 'medium'; s2['manage_dummy_epg'] = False
t0 = time.time(); p._scan_and_update_channels(s2, logger, dry_run=True, is_scheduled_run=False); dt_med = time.time() - t0
print(f'[2] rate_limiting=medium dry-run: {dt_med:.2f}s')

# Expect the medium run to be at least N_channels * 0.2s slower (~20s for 100 channels)
# but for a tighter gate we just require it to be SOMEWHAT slower and the scan to complete.
assert dt_med > dt_none, f'Medium ({dt_med}s) should exceed none ({dt_none}s)'
print(f'[3] Pacing correctly slows scan: +{dt_med - dt_none:.2f}s added by medium')
print('All assertions passed')
" 2>&1 | grep -E '^\[|assertions|Error|Traceback'
```

Expected: `[1]`, `[2]`, `[3]` all print; `assertions passed`.

- [ ] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "$(cat <<'EOF'
Wire SmartRateLimiter into per-channel scan and managed-EPG attach

Adds rate_limiter.wait() at the tail of each iteration in the main
_scan_and_update_channels loop and in _attach_managed_epg's per-channel
get_or_create loop. The limiter is constructed from the new
rate_limiting setting on each scan so the UI toggle takes effect on
the next run.

At rate_limiting=none (default), .wait() short-circuits on a float
compare — no measurable overhead. Higher levels add 0.05 / 0.2 / 0.5
seconds per channel, letting users dial in pacing for large profiles
or constrained DBs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Visual UI verification + deploy confirmation

**Files:**
- None (read-only verification).

- [ ] **Step 1: Deploy both files and confirm versions match**

```bash
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.json dispatcharr:/data/plugins/event-channel-managarr/plugin.json
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py   dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, json
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with open('/data/plugins/event-channel-managarr/plugin.json') as f:
    manifest = json.load(f)
print(f'plugin.py version: {m.PluginConfig.PLUGIN_VERSION}')
print(f'plugin.json version: {manifest[\"version\"]}')
assert m.PluginConfig.PLUGIN_VERSION == manifest['version'], 'Version skew'
print('Versions match')
" 2>&1 | grep -E 'version|Versions|Error'
```

Expected: both report `0.8.0`, and `Versions match`.

- [ ] **Step 2: Exercise one scan end-to-end with the feature toggles the user cares about**

```bash
docker exec dispatcharr python3 /app/manage.py shell -c "
import importlib.util, json, logging
spec = importlib.util.spec_from_file_location('ecm', '/data/plugins/event-channel-managarr/plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logger = logging.getLogger('v'); logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

with open('/data/event_channel_managarr_settings.json') as f:
    s = json.load(f)
s['manage_dummy_epg'] = True
s['rate_limiting'] = 'low'
p = m.Plugin()
r = p._scan_and_update_channels(s, logger, dry_run=True, is_scheduled_run=False)
print('status:', r.get('status'))
res = r.get('results', {})
for k in ('total_channels','to_hide','to_show','ignored','duplicates_hidden','managed_epg_attached','managed_epg_detached'):
    print(f'  {k}: {res.get(k)}')
" 2>&1 | grep -E 'status|total|to_hide|to_show|ignored|duplicates|managed|Error|Traceback'
```

Expected: `status: success`, sane counts for all keys.

- [ ] **Step 3: Manual UI eyeball (user will do — no script)**

Open the Dispatcharr web UI → Plugins → Event Channel Managarr → Settings. Visually confirm:

- Six section dividers render as headers (Scope, Hide Rules, Duplicates, EPG Management, Scheduling & Export, Advanced) with their `description` text beneath each.
- `channel_profile_name`, `channel_groups`, regex fields, `hide_rules_priority`, `dummy_epg_offline_title`, `scheduled_times` render as text inputs (not single-line `string` inputs if the frontend differentiates).
- `past_date_grace_hours` and `dummy_epg_event_duration_hours` render as number spinners, defaults 4 and 3 respectively.
- `rate_limiting` is a select with four options, default `None (fastest)`.
- Action buttons: "Validate Configuration" is an outline blue button labelled "🔎 Validate"; "Run Now" is filled green with a confirm dialog; "Remove EPG from Hidden" and "Clear CSV Exports" are filled red with confirm dialogs; "Cleanup Orphaned Tasks" is outline orange with confirm.

Document "PASS" or the specific visual issue in the task report.

- [ ] **Step 4: No commit for this task** — it's pure verification.

---

## Self-Review

**Spec coverage:**
- Tier 1 action styling → Task 1 `actions` block.
- Tier 1 field type refresh → Task 1 `fields` block + Task 2 `@property def fields`.
- Tier 1 section dividers → Task 1 (json) + Task 2 (python).
- Tier 2 `rate_limiting` setting → Task 1 + Task 2.
- Tier 2 `SmartRateLimiter` class → Task 3.
- Tier 2 wire-in → Task 4 (main scan loop + `_attach_managed_epg`).
- Deploy + UI check → Task 5.

**Placeholder scan:** no "TBD", no "similar to Task N", every code step contains the exact code to paste; every verification step contains the exact command and expected output. The only human-judgment step is Task 5 Step 3 (visual UI check), appropriately marked as manual.

**Type consistency:**
- `SmartRateLimiter(level)` ctor signature: Task 3 defines, Task 4 calls with `settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING)` — string or None, both handled.
- `.wait()`, `.is_active()`, `.delay`, `.level` attributes defined in Task 3, all referenced in Task 4.
- `_attach_managed_epg` signature change in Task 4 (`rate_limiter=None` kwarg) is backwards-compatible: existing callers pass 3 args unchanged; new caller in `_run_managed_epg_pass` passes the 4th kwarg.
- `DEFAULT_RATE_LIMITING` defined in Task 2 Step 2 as `"none"`, referenced in Task 2 Step 3 (field default), Task 2 Step 4 (save-defaults), and Task 4 (scan loop init, `_run_managed_epg_pass`).

**Dispatcharr version gate:** `min_dispatcharr_version: v0.20.0` is already in place. The field types (`text`, `number`, `info` with `description`) and action metadata (`button_variant`, `button_color`, `button_label`, `confirm`) require that version, matching what EPG-Janitor and Lineuparr already ship.

**Field count check (Task 2 Step 5):** version_status (1) + Scope divider + 4 scope fields (timezone, channel_profile_name, channel_groups, name_source) + Rules divider + 5 rule fields (hide_rules_priority, 3 regexes, past_date_grace_hours) + Duplicates divider + 2 dup fields + EPG divider + 5 EPG fields + Scheduling divider + 2 scheduling fields + Advanced divider + 1 advanced field = 1 + (1+4) + (1+5) + (1+2) + (1+5) + (1+2) + (1+1) = 29. Matches the assertion.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-17-dispatcharr-modernization-plan.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
