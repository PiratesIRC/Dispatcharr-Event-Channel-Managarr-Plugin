# Managed Dummy EPG — Design

**Status:** Approved, ready for planning.
**Author:** PiratesIRC
**Date:** 2026-04-17
**Target plugin version:** next minor after 0.7.0

## Problem

Visible PPV / Live Event channels in the monitored profile currently show no EPG because the channels are never bound to any EPG source. Clients display nothing useful in the guide. The user wants the guide to show the scheduled event during its airtime and an "Offline" marker outside it — or, for channels whose names carry no parseable time, a 24-hour program with the channel name.

## Non-goals

- Managing EPG for channels that already have a real EPG binding (XMLTV, Schedules Direct).
- Per-channel-group overrides for offline title or event duration.
- Pre-generating `ProgramData` rows. Dispatcharr's custom dummy EPG generates on-demand at guide-request time.
- Timezone translation inside plugin code. Dispatcharr's generator already handles source→UTC conversion using the source's `timezone` custom property.
- Deleting the managed `EPGSource` row.

## Design

### Settings

Four new fields in the plugin UI, listed after the existing `auto_set_dummy_epg_on_hide` field.

| id | Label | Type | Default |
|---|---|---|---|
| `manage_dummy_epg` | 🗓️ Manage Dummy EPG | boolean | `false` |
| `dummy_epg_event_duration_hours` | ⏱️ Event Duration (hours) | string | `"3"` |
| `dummy_epg_offline_title` | 💤 Offline Title | string | `"Offline"` |
| `dummy_epg_event_timezone` | 📺 Channel Name Event Timezone | select (same IANA list as scheduler) | `"US/Eastern"` |

Defaults exposed on `PluginConfig` as `DEFAULT_MANAGE_DUMMY_EPG`, `DEFAULT_EVENT_DURATION_HOURS`, `DEFAULT_OFFLINE_TITLE`, `DEFAULT_EVENT_TIMEZONE`.

### Shared managed EPGSource

One `EPGSource(source_type='dummy', name='ECM Managed Dummy')` row, created on first use (`get_or_create`). Never deleted, even on toggle-off — cheap to re-adopt.

`custom_properties` fields the plugin manages (overwritten on every scan while the toggle is on, so users editing the source directly are transient):

| key | value |
|---|---|
| `title_pattern` | Regex matching the plugin's recognized name shapes with named group `(?P<title>…)` (optionally `(?P<leading_time>…)` for LIVE-style names where the time precedes the title). |
| `time_pattern` | Separate regex with named groups `hour`, `minute`, `ampm` (Dispatcharr's generator extracts time from this). |
| `date_pattern` | Separate regex with named groups `month`, `day`, `year` (optional). |
| `title_template` | `{title}` |
| `upcoming_title_template` | the user's configured offline title |
| `ended_title_template` | the user's configured offline title |
| `fallback_title_template` | `{channel_name}` — used only when `title_pattern` fails to match at all (safety net for future channel formats the plugin doesn't recognize). For the user's current four name shapes the title pattern matches, so this fallback is dead-code on current data but preserved for resilience. |
| `program_duration` | `event_duration_hours * 60` (minutes) |
| `timezone` | user's `dummy_epg_event_timezone` setting |
| `include_date` | `false` (avoid date suffix in rendered title) |
| `managed_by` | `"event-channel-managarr"` — sentinel so we can identify the source later without name-matching |

If the user has edited other keys on this source, the plugin leaves them alone.

### Per-channel EPGData

For each managed channel: one `EPGData` row keyed by `tvg_id = str(channel.uuid)` and `epg_source = ECM Managed Dummy`. Rows are created with `get_or_create`. `EPGData.name` is set to `channel.name` so Dispatcharr's `{channel_name}` template has the right value.

The channel's `epg_data` FK is pointed at that row.

### Integration in `_scan_and_update_channels`

A new pass runs after visibility changes are applied and before the existing `auto_set_dummy_epg_on_hide` cleanup. The pass:

1. Short-circuits if `manage_dummy_epg` is false — then falls through to the existing "toggle-off cleanup" below.
2. `get_or_create` the managed `EPGSource`; if created, seed `custom_properties` from settings. If it exists, refresh the managed keys listed in the table above (idempotent).
3. Compute the target set: channels that, post-visibility-update, are enabled in the profile and have `epg_data IS NULL`.
4. For each target channel, `get_or_create` its `EPGData(tvg_id=str(channel.uuid), epg_source=managed_source)`, set `channel.epg_data = that row`.
5. `bulk_update` channels (mirrors existing bulk-update pattern for EPG removal).

**Dry-run behavior:** dry runs are pure previews. The pass does NOT call `get_or_create` on the source row; it looks up the managed `EPGSource` with `filter(...).first()` and returns empty sets if it doesn't exist. No `EPGData` rows are created, no `channel.epg_data` writes occur, and `custom_properties` is not refreshed. Results still report what an applied run *would* have attached/detached based on the existing source's bindings.

**Ordering note:** the managed-EPG pass must run *before* the per-channel results-building loop so each row can report `managed_epg_assigned` / `managed_epg_detached` correctly. The pass computes its "would be enabled after this scan" set from `channels_for_duplicate_check` — identical inputs for dry-run and applied runs, so both paths report the same counts for the same state.

**Toggle-off cleanup (always runs regardless of toggle state):**

After step 5 (or if the toggle is off, as a standalone pass), find every channel whose current `epg_data.epg_source.custom_properties.get('managed_by') == 'event-channel-managarr'` AND does not appear in the current target set. Set `epg_data = None` and `bulk_update`. This detaches both (a) channels removed from the profile/group and (b) every managed channel when the toggle is flipped off.

Identifying "our" channels via `managed_by` (not source name) avoids breaking if a user renames the source in the UI.

### Interaction with existing features

- `auto_set_dummy_epg_on_hide`: already sets `epg_data = None` for hidden channels. Runs *after* the new managed-EPG pass, so hiding still wins over managing. Order: visibility → managed EPG attach → auto-remove on hide.
- Hide rules: unchanged. `[NoEPG]` already skips custom-dummy sources via `epg_source.source_type == 'dummy'`, so self-assignment won't trigger it.
- `remove_epg_from_hidden_action`: unchanged. User-initiated bulk clear continues to work.

### CSV / results

Results JSON and CSV gain two new per-channel fields: `managed_epg_assigned` (bool) and `managed_epg_detached` (bool). Summary line appends "Managed EPG: X attached, Y detached".

### Cancellation and errors

- The ORM operations wrap in `transaction.atomic()` consistent with existing patterns.
- The new pass honors `self._op_stop_event.is_set()` before each per-channel update loop.
- If `EPGSource.objects.get_or_create` raises, log the error and skip the pass — scan continues and returns normally. The toggle-off cleanup only runs if we could resolve the managed source.

## Verification

No test harness exists in the project. Verify manually via `/app/manage.py shell` inside the Dispatcharr container, following the same pattern used for the `[UndatedAge:N]` feature verification:

1. **Fresh install** — toggle off, scan. No `ECM Managed Dummy` source created. Channels' `epg_data` unchanged.
2. **First enable** — toggle on, scan. Source created. Visible channels with no EPG bound to source. Source's `custom_properties` populated from settings. CSV shows `managed_epg_assigned: True` for those channels.
3. **Re-scan** — source's `custom_properties` still match settings (idempotent refresh). No duplicate `EPGData` rows.
4. **Setting change** — change offline title, re-scan. Source's `upcoming_title_template` / `ended_title_template` updated. Existing channel assignments unchanged.
5. **Channel hidden mid-event** — scan with `[PastDate:0]` firing on a channel. Channel becomes hidden AND `auto_set_dummy_epg_on_hide=true` → `epg_data=None`. Cleanup pass is a no-op for it.
6. **Channel with real EPG** — set one channel's `epg_data` to a real XMLTV source before the scan. Scan leaves it alone.
7. **Toggle off** — turn manage_dummy_epg off, re-scan. All channels previously bound to managed source get `epg_data=None`. Source row is preserved.
8. **Re-enable** — toggle on again, re-scan. Channels re-adopted; source re-used; no new source row.
9. **Guide render** — hit Dispatcharr's EPG endpoint for a dated PPV channel and confirm the guide shows the event title during the window and the offline title outside it. Repeat for an undated "Coachella" channel and confirm the channel name shows 24h.
10. **Timezone setting** — change `dummy_epg_event_timezone` from `US/Eastern` to `US/Pacific` and verify the rendered event times shift by 3 hours.

## Out of scope / follow-ups

- Regex escape hatch to force-replace existing EPG on specified channels. Add if/when needed.
- Grouped settings sections in the UI. Current `fields` list is flat; regrouping can happen separately.
- Support for channel name formats beyond PPV/LIVE event conventions (tune `title_pattern` in a later patch).
