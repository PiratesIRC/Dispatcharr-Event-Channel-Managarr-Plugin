# Auto-rescan after M3U refresh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ECM automatically re-run its visibility scan after each M3U refresh (opt-in), so channels that Dispatcharr's Auto Channel Sync re-enables are immediately re-hidden.

**Architecture:** Add an opt-in boolean setting and a dedicated plugin action that declares `"events": ["m3u_refresh"]`. Dispatcharr's `connect.trigger_event("m3u_refresh", payload)` calls the action after every M3U refresh; the handler gates on the setting and reuses the existing scheduled-run scan path (`_scan_and_update_channels(..., dry_run=False, is_scheduled_run=True)`), which already holds the scan-lock and gates CSV output.

**Tech Stack:** Python 3.13, Django (Dispatcharr core), in-container testing via `docker exec`. No external test framework — tests are standalone Python scripts run inside the `dispatcharr` container (plugin.py has top-level `from apps.channels.models import ...`, so it can only be imported where Django is configured).

---

## Reference facts (verified against the running container)

- **Plugin module path in container:** `/data/plugins/event-channel-managarr/plugin.py`
- **Repo source of truth:** `Event-Channel-Managarr/plugin.py` and `Event-Channel-Managarr/plugin.json`
- **The real class is `Plugin`** (constants live in `PluginConfig`, which `Plugin` mirrors). Instantiate `Plugin()` for tests, never `PluginConfig()`.
- **Core scan method:** `_scan_and_update_channels(self, settings, logger, dry_run=True, is_scheduled_run=False)` (plugin.py ~line 2543). Acquires `flock` on `SCAN_LOCK_FILE` internally; the scheduler calls it directly+synchronously at ~line 1979.
- **`run(self, action, params, context)`** (~line 694) merges `params` into `merged_settings`, dispatches via `action_map` (~line 752; the `run_now` entry is at ~line 756), calls `handler(merged_settings, logger)` (~line 773), and **sends a WebSocket notification only when the handler returns a `dict`** (~line 776).
- **`m3u_refresh` is fired *indirectly*:** `apps/m3u/tasks.py` calls `log_system_event(event_type='m3u_refresh', account_name=..., streams_created=..., ...)` → `core/utils.py log_system_event` → `dispatch_event_system` → `connect.trigger_event('m3u_refresh', payload)`. The payload keys come from those kwargs, so the account is under **`account_name`** (there is no `account`/`name` key). `run_now_action` is at ~line 3172 (plan says place the new method "near" it). Line numbers drift by a few lines — trust the anchor text, not the exact number.
- **Helpers:** `_get_bool_setting(self, settings, key, default=False)` (~line 579); `_compact_scan_summary(self, label, result)` (~line 3105).
- **Settings defaulting** for missing keys is done in `_save_settings` (~line 960), following the `DEFAULT_SCHEDULED_CSV_EXPORT` pattern.
- **`docker exec` path mangling under Git Bash:** prefix shell-form commands or pipe scripts via stdin. Patterns that work here:
  - Run a script in-container: `docker exec -i dispatcharr python3 < script.py`
  - Django shell: `docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" < script.py`
  - Copy repo file into container: `docker cp <winpath> dispatcharr:/data/plugins/event-channel-managarr/plugin.py` (set `MSYS_NO_PATHCONV=1` if the container path mangles).

---

## File Structure

- **Modify** `Event-Channel-Managarr/plugin.py`:
  - `PluginConfig` constants block (~line 74): add `DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH = False`.
  - `Plugin` constants mirror (~line 235): add the mirror line.
  - `get_fields`/`fields_list` Scheduling section (~line 511, after `enable_scheduled_csv_export`): add the new setting field.
  - `_save_settings` missing-key defaulting (~line 960): inject the new key when absent.
  - `actions` list (~line 542, after `run_now`): add the `on_m3u_refresh` action.
  - `action_map` in `run()` (~line 760): add `"on_m3u_refresh": self.on_m3u_refresh_action`.
  - New method `on_m3u_refresh_action(self, settings, logger)` (place near `run_now_action`, ~line 3190).
- **Modify** `Event-Channel-Managarr/plugin.json`:
  - `fields` array (after line 37): mirror the new setting.
  - `actions` array (after line 46): mirror the new action.
- **Modify** `README.md`: troubleshooting entry for the Auto Channel Sync collision + this toggle.
- **Test scripts** (throwaway, not committed): `/tmp/ecm_test_*.py` run in-container.

---

## Task 1: Add the default constant

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` (~line 74 and ~line 235)

- [ ] **Step 1: Add the constant to `PluginConfig`**

In the `PluginConfig` constants block, immediately after the `DEFAULT_SCHEDULED_CSV_EXPORT = False` line (~line 74), add:

```python
    # Default CSV export for scheduled runs
    DEFAULT_SCHEDULED_CSV_EXPORT = False

    # Auto-rescan after each M3U refresh (re-hides channels that Dispatcharr's
    # Auto Channel Sync re-enables). Opt-in, default off — no behavior change on upgrade.
    DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH = False
```

- [ ] **Step 2: Mirror it in `Plugin`**

In the `Plugin` class "Reference PluginConfig for all defaults" block, after the `DEFAULT_SCHEDULED_CSV_EXPORT = PluginConfig.DEFAULT_SCHEDULED_CSV_EXPORT` line (~line 235), add:

```python
    DEFAULT_SCHEDULED_CSV_EXPORT = PluginConfig.DEFAULT_SCHEDULED_CSV_EXPORT
    DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH = PluginConfig.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH
```

- [ ] **Step 3: Syntax-check the file**

Run: `docker cp Event-Channel-Managarr/plugin.py dispatcharr:/tmp/plugin_check.py && docker exec dispatcharr python3 -m py_compile /tmp/plugin_check.py && echo OK`
Expected: `OK` (no SyntaxError).

- [ ] **Step 4: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: add DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH constant (opt-in, default off)"
```

---

## Task 2: Add the setting field (plugin.py + plugin.json + default injection)

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` (~line 511 fields, ~line 960 `_save_settings`)
- Modify: `Event-Channel-Managarr/plugin.json` (after line 37)

- [ ] **Step 1: Add the field to `get_fields` in plugin.py**

In `fields_list`, immediately after the `enable_scheduled_csv_export` field dict (closes at ~line 511), insert:

```python
            {
                "id": "auto_rescan_on_m3u_refresh",
                "label": "🔄 Auto-rescan after M3U refresh",
                "type": "boolean",
                "default": self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH,
                "help_text": "If enabled, the plugin re-runs its visibility scan automatically after each M3U account refresh. Dispatcharr's Auto Channel Sync re-enables (un-hides) channels in synced groups on every refresh; this re-hides them right after. Leave off if you do not use Auto Channel Sync.",
            },
```

- [ ] **Step 2: Mirror the field in plugin.json**

In the `fields` array, after the `enable_scheduled_csv_export` entry (line 37), insert:

```json
    {"id": "auto_rescan_on_m3u_refresh", "label": "🔄 Auto-rescan after M3U refresh", "type": "boolean", "default": false},
```

- [ ] **Step 3: Inject the default for missing key in `_save_settings`**

Find the block in `_save_settings` (~line 959) that handles `enable_scheduled_csv_export`:

```python
            if "enable_scheduled_csv_export" not in settings:
                LOGGER.info(f"  Setting missing 'enable_scheduled_csv_export', adding default: {self.DEFAULT_SCHEDULED_CSV_EXPORT}")
                settings["enable_scheduled_csv_export"] = self.DEFAULT_SCHEDULED_CSV_EXPORT
```

Immediately after it, add the parallel block:

```python
            if "auto_rescan_on_m3u_refresh" not in settings:
                LOGGER.info(f"  Setting missing 'auto_rescan_on_m3u_refresh', adding default: {self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH}")
                settings["auto_rescan_on_m3u_refresh"] = self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH
```

- [ ] **Step 4: Validate JSON + Python syntax**

Run: `docker cp Event-Channel-Managarr/plugin.json dispatcharr:/tmp/p.json && docker exec dispatcharr python3 -c "import json; json.load(open('/tmp/p.json')); print('JSON OK')"`
Expected: `JSON OK`
Run: `docker cp Event-Channel-Managarr/plugin.py dispatcharr:/tmp/plugin_check.py && docker exec dispatcharr python3 -m py_compile /tmp/plugin_check.py && echo OK`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json
git commit -m "feat: add 'Auto-rescan after M3U refresh' setting (default off)"
```

---

## Task 3: Add the dedicated action (plugin.py + plugin.json)

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` (`actions` list ~line 542)
- Modify: `Event-Channel-Managarr/plugin.json` (`actions` array after line 46)

> **Authoritative source:** For an *enabled* plugin, `loader._load_plugin` reads
> actions from the instance (`getattr(instance, "actions", [])`, loader.py:322); the
> plugin.json manifest actions are applied only as a fallback when the instance has
> none (`if manifest_actions and not lp.actions`, loader.py:199-200). So the
> `events: ["m3u_refresh"]` binding **must** be in plugin.py's `Plugin.actions`
> (Step 1) for `trigger_event` to dispatch. The plugin.json copy (Step 2) is for the
> disabled-plugin placeholder + documentation/consistency — keep them in sync, but
> editing only plugin.json would NOT wire the event. `PluginActionSerializer.events`
> (a `ListField`) preserves the key through `_normalize_actions`, so it survives discovery.

- [ ] **Step 1: Add the action to the plugin.py `actions` list**

Immediately after the `run_now` action entry (~line 542), insert:

```python
        {"id": "on_m3u_refresh", "label": "Auto-rescan after M3U refresh", "description": "Runs a visibility scan automatically after each M3U refresh when '🔄 Auto-rescan after M3U refresh' is enabled below. Click to rescan now.", "events": ["m3u_refresh"], "button_label": "🔄 Rescan Now", "button_variant": "outline", "button_color": "cyan"},
```

- [ ] **Step 2: Mirror the action in plugin.json**

In the `actions` array, after the `run_now` entry (line 46), insert:

```json
    {"id": "on_m3u_refresh", "label": "Auto-rescan after M3U refresh", "description": "Runs a visibility scan automatically after each M3U refresh when '🔄 Auto-rescan after M3U refresh' is enabled below. Click to rescan now.", "events": ["m3u_refresh"], "button_label": "🔄 Rescan Now", "button_variant": "outline", "button_color": "cyan"},
```

- [ ] **Step 3: Validate JSON + Python syntax**

Run: `docker cp Event-Channel-Managarr/plugin.json dispatcharr:/tmp/p.json && docker exec dispatcharr python3 -c "import json; d=json.load(open('/tmp/p.json')); ids=[a['id'] for a in d['actions']]; assert 'on_m3u_refresh' in ids; assert [a for a in d['actions'] if a['id']=='on_m3u_refresh'][0]['events']==['m3u_refresh']; print('JSON OK', ids)"`
Expected: `JSON OK [...]` including `on_m3u_refresh`
Run: `docker cp Event-Channel-Managarr/plugin.py dispatcharr:/tmp/plugin_check.py && docker exec dispatcharr python3 -m py_compile /tmp/plugin_check.py && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json
git commit -m "feat: add on_m3u_refresh event-bound action (subscribes to m3u_refresh)"
```

---

## Task 4: Implement the handler (TDD, in-container)

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` (`action_map` ~line 760; new method ~line 3190)
- Test: `/tmp/ecm_test_handler.py` (throwaway, run in-container)

The handler must satisfy three behaviors:
1. **Event + toggle OFF** → returns `None` (no scan, no WebSocket notification), logs at debug.
2. **Event + toggle ON** → calls `_scan_and_update_channels(settings, logger, dry_run=False, is_scheduled_run=True)` and returns its result; logs the triggering account at info.
3. **Manual click (no `event` key)** → calls `_scan_and_update_channels(..., dry_run=False, is_scheduled_run=True)`, applies `_compact_scan_summary` to `result["message"]`, returns the dict.

- [ ] **Step 1: Write the failing test**

Create `/tmp/ecm_test_handler.py` locally (it will be piped into the container):

```python
# plugin.py has top-level `from apps.channels.models import ...` and
# `from django.utils import timezone`, so Django MUST be configured before import,
# else this raises ImproperlyConfigured (NOT the AttributeError we want to test).
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")
django.setup()

import importlib.util, sys, types

# Load the live plugin module (Django is now configured).
spec = importlib.util.spec_from_file_location(
    "ecm_plugin", "/data/plugins/event-channel-managarr/plugin.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

import logging
logger = logging.getLogger("ecm_test")

def make_plugin():
    p = mod.Plugin()
    # Record calls to the core scan instead of touching the DB.
    calls = []
    def fake_scan(settings, lg, dry_run=True, is_scheduled_run=False):
        calls.append({"dry_run": dry_run, "is_scheduled_run": is_scheduled_run})
        return {"status": "success", "message": "scan ran",
                "channels_to_hide": 3, "channels_to_show": 0}
    p._scan_and_update_channels = fake_scan
    return p, calls

failures = []

# 1. Event + toggle OFF -> no scan, returns None
p, calls = make_plugin()
res = p.on_m3u_refresh_action(
    {"event": "m3u_refresh", "payload": {"account": "pia"},
     "auto_rescan_on_m3u_refresh": False}, logger)
if calls:
    failures.append(f"OFF: scan should NOT run, got {calls}")
if res is not None:
    failures.append(f"OFF: expected None (suppresses notification), got {res!r}")

# 2. Event + toggle ON -> scan runs with apply + scheduled flags
p, calls = make_plugin()
res = p.on_m3u_refresh_action(
    {"event": "m3u_refresh", "payload": {"account": "pia"},
     "auto_rescan_on_m3u_refresh": True}, logger)
if len(calls) != 1:
    failures.append(f"ON: expected exactly 1 scan call, got {calls}")
elif calls[0] != {"dry_run": False, "is_scheduled_run": True}:
    failures.append(f"ON: wrong scan flags: {calls[0]}")
if not (isinstance(res, dict) and res.get("status") == "success"):
    failures.append(f"ON: expected success dict, got {res!r}")

# 3. Manual click (no 'event' key) -> scan runs regardless of toggle
p, calls = make_plugin()
res = p.on_m3u_refresh_action({"auto_rescan_on_m3u_refresh": False}, logger)
if len(calls) != 1 or calls[0] != {"dry_run": False, "is_scheduled_run": True}:
    failures.append(f"MANUAL: expected 1 apply scan, got {calls}")
if not (isinstance(res, dict) and res.get("status") == "success"):
    failures.append(f"MANUAL: expected success dict, got {res!r}")

if failures:
    print("FAIL"); [print(" -", f) for f in failures]; sys.exit(1)
print("PASS: all 3 handler behaviors correct")
```

- [ ] **Step 2: Run the test to verify it fails**

First deploy the current (handler-less) plugin so the module loads, then run:

```bash
docker cp Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec -i dispatcharr python3 < /tmp/ecm_test_handler.py
```
Expected: FAIL — `AttributeError: 'Plugin' object has no attribute 'on_m3u_refresh_action'` (or a `FAIL` listing). This proves the test exercises the not-yet-written handler. (With the `django.setup()` preamble the import succeeds and the failure happens at the handler call, which is what we want — without it you'd get `ImproperlyConfigured` at import and the test would prove nothing.)

- [ ] **Step 3: Add the `action_map` entry**

In `run()`'s `action_map` dict (~line 752), after the `"run_now": self.run_now_action,` line, add:

```python
                "run_now": self.run_now_action,
                "on_m3u_refresh": self.on_m3u_refresh_action,
```

- [ ] **Step 4: Implement `on_m3u_refresh_action`**

Add this method next to `run_now_action` (~line 3190):

```python
    def on_m3u_refresh_action(self, settings, logger):
        """Re-run the visibility scan after an M3U refresh.

        Wired to Dispatcharr's 'm3u_refresh' connect event via the action's
        "events": ["m3u_refresh"]. Dispatcharr calls run() with
        params={"event": "m3u_refresh", "payload": {...}}, which run() merges
        into settings — so settings.get("event") tells us event vs manual click.

        Event path is gated on the opt-in setting and mirrors the scheduler's
        direct synchronous call to _scan_and_update_channels (the event fires in
        a Celery worker; no UI spinner thread needed). The cross-process
        SCAN_LOCK_FILE flock inside _scan_and_update_channels serializes against
        manual/scheduled runs.
        """
        triggered_by_event = settings.get("event") == "m3u_refresh"

        if triggered_by_event and not self._get_bool_setting(
            settings, "auto_rescan_on_m3u_refresh", self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH
        ):
            # Disabled: no-op. Return None (not a dict) so run() does NOT emit a
            # per-refresh WebSocket notification — avoids UI noise on every refresh.
            logger.debug(f"{LOG_PREFIX} [m3u_refresh] auto-rescan disabled, skipping")
            return None

        if triggered_by_event:
            payload = settings.get("payload") or {}
            # The real m3u_refresh payload (core/utils.py log_system_event ->
            # dispatch_event_system -> trigger_event) carries the account under
            # 'account_name'. Fall back defensively for other shapes.
            account = payload.get("account_name") or payload.get("account") or "unknown"
            logger.info(f"{LOG_PREFIX} [m3u_refresh] Auto-rescan triggered by account '{account}'")
        else:
            logger.info(f"{LOG_PREFIX} Manual rescan (Rescan Now) requested")

        result = self._scan_and_update_channels(
            settings, logger, dry_run=False, is_scheduled_run=True
        )

        if isinstance(result, dict):
            summary = self._compact_scan_summary("M3U Rescan", result)
            if summary:
                result["message"] = summary
        return result
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
docker cp Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec -i dispatcharr python3 < /tmp/ecm_test_handler.py
```
Expected: `PASS: all 3 handler behaviors correct`

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: implement on_m3u_refresh_action with opt-in gate and scheduled-path reuse"
```

---

## Task 5: Live integration verification + docs + version bump

**Files:**
- Modify: `README.md`
- Modify: `Event-Channel-Managarr/plugin.py`, `Event-Channel-Managarr/plugin.json` (version, via bump_version.py)

- [ ] **Step 1: Deploy and restart so Dispatcharr re-discovers the action**

```bash
docker cp Event-Channel-Managarr/plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker cp Event-Channel-Managarr/plugin.json dispatcharr:/data/plugins/event-channel-managarr/plugin.json
docker restart dispatcharr
```
Wait ~20s for startup.

- [ ] **Step 2: Confirm the action is discovered with its event binding**

Create `/tmp/ecm_check_action.py`:

```python
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")
django.setup()
from apps.plugins.loader import PluginManager
pm = PluginManager.get()
pm.discover_plugins(sync_db=False, use_cache=False)
for p in pm.list_plugins():
    if "managarr" in p["key"]:
        acts = {a["id"]: a.get("events", []) for a in p["actions"]}
        print("on_m3u_refresh events:", acts.get("on_m3u_refresh"))
        assert acts.get("on_m3u_refresh") == ["m3u_refresh"], "event binding missing"
        print("OK: action discovered with m3u_refresh binding")
```

Run: `docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" < /tmp/ecm_check_action.py`
Expected: `OK: action discovered with m3u_refresh binding`

- [ ] **Step 3: Enable the toggle, re-enable a known channel, then fire the event end-to-end**

Create `/tmp/ecm_e2e.py` (uses the live connect path, not the handler directly):

```python
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")
django.setup()
import json
from apps.channels.models import Channel, ChannelProfileMembership
from apps.connect.utils import trigger_event

SETTINGS = "/data/event_channel_managarr_settings.json"
s = json.load(open(SETTINGS))
s["auto_rescan_on_m3u_refresh"] = True
json.dump(s, open(SETTINGS, "w"))
print("toggle set ON in", SETTINGS)

# Pick a channel the last scan hid (enabled=False) that is WITHIN a managed group,
# so a rescan will legitimately re-hide it. Selecting from a managed group is
# primary (not a fallback) to avoid a false-negative on an out-of-scope channel.
MANAGED_GROUPS = ["PPV Live Events", "NFL", "US| PPV EVENT"]
m = (ChannelProfileMembership.objects
     .filter(enabled=False, channel__channel_group__name__in=MANAGED_GROUPS)
     .first())
assert m, "no disabled membership in a managed group found to test with"
cid, pid = m.channel_id, m.channel_profile_id
m.enabled = True
m.save(update_fields=["enabled"])
print(f"re-enabled channel_id={cid} profile_id={pid} (simulating auto-sync)")

# Fire the real connect event with the production payload shape (account_name).
# NOTE: trigger_event also dispatches to any enabled connect EventSubscription
# (webhook/script). Production logs show "Found 0 subscription(s)", so this is a
# no-op here — but only run this e2e on a system with no m3u_refresh subscriptions.
trigger_event("m3u_refresh", {"account_name": "pia", "streams_created": 0,
                              "streams_updated": 0, "streams_deleted": 0})

m2 = ChannelProfileMembership.objects.get(channel_id=cid, channel_profile_id=pid)
print("after event, enabled =", m2.enabled)
assert m2.enabled is False, "FAIL: channel was not re-hidden by the auto-rescan"
print("PASS: auto-rescan re-hid the channel via the m3u_refresh event")
```

Run: `docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" < /tmp/ecm_e2e.py`
Expected: ends with `PASS: auto-rescan re-hid the channel via the m3u_refresh event`

Note: this assumes the channel chosen is within ECM's configured groups/profile and still matches a hide rule. If the picked channel is out of scope, re-run selecting one from a managed group (`channel__channel_group__name__in=['PPV Live Events','NFL','US| PPV EVENT']`).

- [ ] **Step 4: Confirm the disabled path is silent**

Set the toggle back off and fire again; confirm no scan runs (no "Auto-rescan triggered" log) and the event returns without a notification.

```bash
docker exec -i dispatcharr sh -c "cd /app && python3 -c \"import json,os; p='/data/event_channel_managarr_settings.json'; s=json.load(open(p)); s['auto_rescan_on_m3u_refresh']=False; json.dump(s,open(p,'w')); print('toggle OFF')\""
docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" <<'PY'
from apps.connect.utils import trigger_event
trigger_event("m3u_refresh", {"account": "pia"})
print("fired with toggle OFF (expect no auto-rescan log line)")
PY
docker logs dispatcharr --since 1m 2>&1 | grep -E "m3u_refresh|Auto-rescan|auto-rescan" | tail -5
```
Expected: no `Auto-rescan triggered` line (debug "auto-rescan disabled, skipping" only appears if debug logging is on).

- [ ] **Step 5: Add README troubleshooting entry**

In `README.md`, add a troubleshooting entry (match the existing troubleshooting format) explaining: Dispatcharr's M3U Auto Channel Sync re-enables channels ECM hid on every refresh; enable **🔄 Auto-rescan after M3U refresh** so ECM re-hides them automatically after each refresh; alternatively turn off Auto Channel Sync for the managed groups.

- [ ] **Step 6: Bump the version**

Run: `python bump_version.py` (maintainer-local tool; updates `plugin.json` + `plugin.py`).
If unavailable, manually increment `"version"` in `Event-Channel-Managarr/plugin.json` and the matching version string in `Event-Channel-Managarr/plugin.py`.

Verify: `docker exec dispatcharr python3 -c "import json; print(json.load(open('/tmp/p.json'))['version'])"` after copying, or `grep '\"version\"' Event-Channel-Managarr/plugin.json`.

- [ ] **Step 7: Commit**

```bash
git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json README.md
git commit -m "docs: document M3U auto-rescan toggle; bump version"
```

- [ ] **Step 8: Update OpenWolf tracking**

- Append a session entry to `.wolf/memory.md`.
- The feature is built; no new bug — bug-024 already records the root cause and references this toggle as the fix.

---

## Release (separate, gated on user)

Per `.claude/rules/release.md`, **before** tagging a release: review open issues + PRs on `PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin` and upstream `Dispatcharr/Plugins` PRs touching `plugins/event-channel-managarr/`, summarize to the user, and confirm scope. Then: tag → GitHub release with ZIP → marketplace PR. Do not start this until the user asks.

---

## Self-Review

- **Spec coverage:**
  - Opt-in setting default off → Task 1 (constant) + Task 2 (field + default injection). ✓
  - No account filtering → handler ignores account except for logging (Task 4). ✓
  - Reuse scheduled-run path (lock + CSV gating + apply) → Task 4 calls `_scan_and_update_channels(..., dry_run=False, is_scheduled_run=True)`. ✓
  - Dedicated action with `events:["m3u_refresh"]` + badge → Task 3. ✓
  - Event vs manual distinction → Task 4 via `settings.get("event")`. ✓
  - Disabled path returns `None` to suppress notification → Task 4 + asserted in Task 4 Step 1 test. ✓
  - Concurrency via existing flock → relied on, noted in handler docstring; not re-implemented. ✓
  - Testing (unit behaviors + live integration + regression) → Task 4 (3 behaviors) + Task 5 (live e2e, disabled-path silence). ✓
  - Docs + version bump + no upstream change → Task 5. ✓
- **Placeholder scan:** No TBD/TODO; all code steps contain full code; all commands have expected output. ✓
- **Type/name consistency:** `auto_rescan_on_m3u_refresh` (setting id), `DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH` (constant), `on_m3u_refresh` (action id), `on_m3u_refresh_action` (method) used identically across Tasks 1–5 and tests. Scan flags `dry_run=False, is_scheduled_run=True` consistent between handler and test assertion. ✓
- **Regression coverage:** Task 5 Step 4 confirms disabled path is silent; manual/Run Now/scheduled paths untouched (handler only adds a new entry, does not modify existing actions or `_scan_and_update_channels`). ✓
