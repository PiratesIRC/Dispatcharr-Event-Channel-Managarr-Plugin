# Auto-rescan after M3U refresh — Design

**Date:** 2026-06-10
**Status:** Approved (design)
**Component:** `Event-Channel-Managarr/plugin.py`, `Event-Channel-Managarr/plugin.json`
**Related:** buglog `bug-024`

## Problem

ECM hides event channels by setting `ChannelProfileMembership.enabled = False` for the
configured profile(s). Dispatcharr's **Auto Channel Sync** (run on every M3U account
refresh) reconciles profile memberships in `apps/m3u/tasks.py` (~line 2627) and
**unconditionally flips every disabled membership in a synced group back to
`enabled = True`** — for *all* existing channels in the group, not just changed ones.

Result: ECM hides channels on its schedule; the next M3U refresh re-enables them; the
channels are visible again until ECM's next scheduled scan. Observed in production:
per-run hide counts flapped `26 → 221 → 185 → 0` across hourly scans, and the visible
count grew `23 (06-08) → 56 (06-10)`.

Turning off Auto Channel Sync for the managed groups is the immediate workaround. This
feature provides a durable fix for users who want to keep Auto Channel Sync **on**:
ECM re-runs its visibility scan automatically right after each M3U refresh.

## Mechanism (verified in the running container)

Dispatcharr's plugin system supports event-subscribed actions as a first-class feature:

- `PluginActionSerializer` (`apps/plugins/serializers.py`) defines an optional
  `events = ListField(child=CharField())` on plugin actions, so the `events` key
  survives plugin discovery / normalization.
- `apps/connect/utils.py::trigger_event(event_name, payload)` is called on every
  M3U refresh with `event_name = "m3u_refresh"` (confirmed in logs:
  *"Found 0 connect subscription(s) for event 'm3u_refresh'"*). It iterates enabled
  plugins and, for any action whose `events` list contains the fired event, calls:
  ```python
  pm.run_action(key, action_id, {"event": event_name, "payload": payload})
  ```
- `PluginManager.run_action` invokes the plugin's `run(action_id, params, context)`.
  ECM's `run()` merges `params` into `merged_settings` and dispatches via `action_map`,
  calling `handler(merged_settings, logger)`. So the handler can read
  `settings.get("event")` and `settings.get("payload")`.

**Timing is correct.** The `m3u_refresh` event fires *after* Auto Channel Sync completes
(production logs: sync complete `09:00:56.612`, event `09:00:56.629`). So by the time
ECM's rescan runs, the re-enabling has already happened and the rescan re-hides it.

## Design decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Control | Opt-in boolean setting, **default OFF**. No behavior change on upgrade. |
| 2 | Account filtering | **None.** Rescan on any `m3u_refresh`; the scan is already scoped to configured groups/profile and is cheap (~1s for ~500 channels). Fewer ways to silently misconfigure. |
| 3 | Scan path | **Reuse the scheduled-run path** (`is_scheduled_run=True`): same `SCAN_LOCK_FILE`, same CSV gating, applies changes (non-dry-run), distinct logging. |
| 4 | UI surface | **Dedicated action** (its own row + "M3U Refreshed" trigger badge), not attached to "Run Now". |

## Behavior flow

```
M3U refresh completes
  → Auto Channel Sync re-enables memberships
  → Dispatcharr fires m3u_refresh connect event
  → ECM run("on_m3u_refresh", {"event":"m3u_refresh","payload":{...}}, ctx)
      → on_m3u_refresh_action(merged_settings, logger)
          if settings.get("event") == "m3u_refresh":          # event-triggered
              if not auto_rescan_on_m3u_refresh:
                  log.debug("[m3u_refresh] auto-rescan disabled, skipping")
                  return None   # None suppresses run()'s per-result WebSocket
                                # notification (it only notifies on dict results),
                                # avoiding UI-notification spam every refresh
              log.info("[m3u_refresh] Auto-rescan triggered by account '<payload.account_name>'")
              return _scan_and_update_channels(settings, logger,
                                               dry_run=False, is_scheduled_run=True)
          else:                                                # manual button click
              return _scan_and_update_channels(settings, logger,
                                               dry_run=False, is_scheduled_run=True)
                     # (compact summary applied to result["message"])
```

The handler mirrors the **scheduler's** direct synchronous call to
`_scan_and_update_channels` (plugin.py ~line 1979), *not* `run_now_action`'s
thread+join pattern — that spinner pattern exists only for the UI button context; the
event fires inside a Celery worker where a synchronous call is correct.

## Changes

### 1. New setting (`plugin.json` + `plugin.py` fields list)
Added to the existing **"Scheduled runs and CSV export"** section:

- `id`: `auto_rescan_on_m3u_refresh`
- `type`: `boolean`
- `default`: `False`
- `label`: `🔄 Auto-rescan after M3U refresh`
- `help_text`: Explains that Dispatcharr's M3U Auto Channel Sync can re-show channels
  ECM hid, and that enabling this re-runs the visibility scan automatically after each
  M3U refresh to re-hide them. Note it only applies changes when the plugin is otherwise
  configured to run.

### 2. New action (`actions` list)
```python
{
    "id": "on_m3u_refresh",
    "label": "Auto-rescan after M3U refresh",
    "description": ("Runs a visibility scan automatically after each M3U refresh when "
                    "'🔄 Auto-rescan after M3U refresh' is enabled below. Click to rescan now."),
    "events": ["m3u_refresh"],
    "button_label": "🔄 Rescan Now",
    "button_variant": "outline",
    "button_color": "cyan",
}
```
Dispatcharr renders this as a row with the action label/description, a green
**"M3U Refreshed"** event-trigger badge, and a manual button. This is standard
rendering, expected.

### 3. New handler + `action_map` entry
- Add `"on_m3u_refresh": self.on_m3u_refresh_action` to `action_map` in `run()`.
- Implement `on_m3u_refresh_action(self, settings, logger)` per the behavior flow above.
- Use `_get_bool_setting(settings, "auto_rescan_on_m3u_refresh", False)` for the gate
  (matches existing boolean-setting access).
- Use `_compact_scan_summary(...)` for the manual-path message (matches Run Now).

### 4. Default constant
Add `DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH = False` to `PluginConfig` and mirror in
`Plugin`, following the existing `DEFAULT_SCHEDULED_CSV_EXPORT` pattern: a constant plus
missing-key default injection in `_save_settings` (plugin.py ~line 960).

## Concurrency & safety

- `_scan_and_update_channels` already acquires an exclusive `flock` on `SCAN_LOCK_FILE`.
  An `m3u_refresh` that lands during a manual/scheduled scan hits the existing
  *"Another scan is already running … Skipping"* path and returns harmlessly. No new
  locking is introduced.
- CSV output remains gated behind `enable_scheduled_csv_export` (default off) because the
  rescan passes `is_scheduled_run=True`, so frequent refreshes do not spam CSV files.
- The event fires in a Celery worker; the scan runs synchronously there. No cross-process
  in-memory state is shared except via the file lock.

## Testing

**Unit/in-container (via `docker exec -i dispatcharr ... manage.py shell`):**
1. Toggle OFF, invoke handler with `{"event":"m3u_refresh"}` → returns
   `skipped (disabled)`, no DB writes.
2. Toggle ON, deliberately re-enable a known event channel's membership
   (`enabled=True`), invoke handler with `{"event":"m3u_refresh"}` → channel returns to
   `enabled=False`; result reports it hidden.
3. Manual invocation (no `event` key) → runs apply-scan and returns a compact summary.

**Integration:**
4. With toggle ON, trigger a real refresh of account "pia"; confirm logs show the
   auto-rescan firing *after* "Auto channel sync complete", and that channels re-enabled
   by sync are re-hidden.

**Regression:**
5. Run Now, Dry Run, scheduled runs, and the scan lock behave unchanged.

## Out of scope (YAGNI)

- Per-account / per-group filtering of which refresh triggers a rescan (decision #2).
- Debounce/coalescing of rapid successive refreshes (the file lock already serializes;
  a skipped scan is harmless because the next refresh's event re-triggers).
- Any change to Dispatcharr core or to Auto Channel Sync behavior.

## Rollout

- Implement in `plugin.py` + `plugin.json` only; no upstream change.
- README troubleshooting entry: document the Auto Channel Sync collision and this toggle.
- Version bump; GitHub release; marketplace PR per the project release runbook
  (check open issues/PRs first).
