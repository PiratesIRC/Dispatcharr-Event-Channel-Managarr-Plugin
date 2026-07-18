# Source the Scheduler/Display Timezone from Dispatcharr — Design

**Date:** 2026-06-13
**Status:** Approved (pending spec review)
**Plugin:** Event Channel Managarr

## Problem

The plugin owns a **🌍 Timezone** setting (`timezone`, default `America/Chicago`)
that drives the scheduler firing time, day-of-week / date-rule logic
(`PastDate`, `FutureDate`, grace periods, `WrongDayOfWeek`), and the display
timezone for managed dummy-EPG titles. Dispatcharr already has a global
timezone (**General Settings → Time Zone**), so the plugin setting is a
redundant second source of truth the user must keep in sync.

## Goal

Remove the plugin's `timezone` setting entirely and source the
scheduler/display timezone from Dispatcharr's global setting instead.

## Decisions (locked)

1. **Remove the dropdown entirely** — not a default-with-override. The plugin
   no longer exposes a timezone field.
2. **Source:** `apps.dashboard.models.Settings.time_zone` (Dispatcharr's
   General Settings → Time Zone).
3. **Fallback:** when no `Settings` row exists, the field is blank, or the value
   is not a valid tz, fall back to **`UTC`** (matches Dispatcharr's field default
   and Django's `TIME_ZONE = "UTC"`).
4. **`dummy_epg_event_timezone` (📺 Channel Name Event Timezone) is untouched** —
   it describes the timezone *encoded in channel names*, for which Dispatcharr
   has no equivalent.

### Accepted consequence

On installs where Dispatcharr's Time Zone is unset (the common default), the
plugin's effective timezone shifts from `America/Chicago` → `UTC` until the user
sets a zone in Dispatcharr's General Settings. This is expected and accepted.

## Implementation

### Single choke point, injected once per operation

1. **New resolver** `_dispatcharr_timezone()`, split into a DB read and a pure
   validator so the fallback logic is unit-testable without Django:
   - **`ecm_parsing.coerce_timezone(value) -> str`** (pure, Django-free, lives in
     the existing `ecm_parsing.py` sibling module — NOT on the Plugin class,
     because `plugin.py` is not importable in the test harness). Returns
     `value.strip()` if it is a non-empty string accepted by `pytz.timezone(...)`
     (pytz **lazily imported inside the function** so importing `ecm_parsing`
     stays dependency-clean); otherwise `"UTC"`. `plugin.py` already does
     `import ecm_parsing` (line 43), so the resolver calls
     `ecm_parsing.coerce_timezone(...)`.
   - `_dispatcharr_timezone()`: lazily imports `from apps.dashboard.models import
     Settings` *inside the method* (consistent with the plugin's other in-method
     Django imports, e.g. `apps.epg.models` at `:2176`/`:2371`/`:2455`), reads
     `Settings.objects.order_by("id").first()` (explicit ordering — the table is
     a de-facto singleton; lowest-PK row is the canonical one), and returns
     `ecm_parsing.coerce_timezone(getattr(row, "time_zone", None))`. The whole body is wrapped
     in a broad `try/except Exception` (NOT a narrow tuple) so the import failing
     outside Dispatcharr, or any DB error (`OperationalError`/`ProgrammingError`
     during migrations), degrades to `"UTC"` rather than raising.

2. **Inject once per operation** (NOT per channel — avoids ~489 DB hits/scan):
   `settings["timezone"] = self._dispatcharr_timezone()`. Exact placement matters
   (verified against the call graph by QA review):
   - **`_scan_and_update_channels` — as the literal FIRST statement**, before the
     flock/early-returns and well before the per-channel reads at `:1263`/`:1420`/
     `:1486`/`:2650`, the channel loop (`:2677`), and `_localized_template_props`
     (`:2128`). This covers `dry_run`, `run_now`, `on_m3u_refresh`, AND scheduled
     runs — the scheduled path reloads `current_settings` from disk
     (`:1874–1887`) and passes it here (`:1891`), so a stale/absent disk
     `timezone` is overwritten before any read.
   - **`_start_background_scheduler` — inject on the `settings` dict BEFORE the
     thread is created (or as the first line of `scheduler_loop`, before `:1838`).**
     The firing-time `local_tz` is computed once at `:1838–1843` from the
     captured `settings` and never recomputed in the `while` loop; injecting
     inside the loop would be too late. Placing the write inside
     `_start_background_scheduler` itself is the robust choice because the
     scheduler is also started at plugin load (`__init__`), which is NOT one of
     the action entry points.
   - **`update_schedule_action` and `check_scheduler_status_action`** — inject
     before they read the tz for their status/schedule messages (and
     `update_schedule_action` injects before its `_start_background_scheduler`
     call at `:1759`).

3. **Readers stay as-is.** `_get_system_timezone(settings)` remains the in-memory
   reader of `settings["timezone"]`. Its hard-coded default constant
   `DEFAULT_TIMEZONE` changes `"America/Chicago"` → `"UTC"` so every secondary
   "invalid tz" fallback (`pytz.timezone(self.DEFAULT_TIMEZONE)`) is consistent
   with decision #3.

4. **`_localized_template_props`** currently reads `settings.get("timezone")`
   directly (line ~2128). Change it to `self._get_system_timezone(settings)` so
   the display-tz path follows the same resolution. Source tz remains
   `dummy_epg_event_timezone`.

5. **Remove the field** from:
   - `Plugin.fields()` in `plugin.py` (the `{"id": "timezone", ...}` block, ~354)
   - `plugin.json` (`{"id": "timezone", ...}`, line 12)
   - `_load_timezones_from_file()` stays — still used by `dummy_epg_event_timezone`.
   - **Reword the `dummy_epg_event_timezone` help text at `plugin.py:513`** — it
     says "Different from the scheduler timezone **above**", but there is no
     timezone field above it after removal. Change to reference Dispatcharr's
     Time Zone instead.

6. **Leftover saved value:** an existing `timezone` key in a user's saved
   settings JSON / DB is harmless and ignored after this change (nothing reads it
   once injection overwrites it each run). No deletion/migration logic — out of
   scope.

7. **CSV settings-snapshot (`plugin.py:2932`)** keeps printing `settings["timezone"]`,
   which now holds the *resolved effective* zone at scan time (correct). The label
   is mildly misleading (no longer a plugin setting) but behavior is right — leave
   as-is, optionally relabel to `effective_timezone`. Non-blocking.

8. **Dead branch (accepted):** after this change `_localized_template_props`'s
   `display_tz_name` is essentially never empty (always a real zone or `"UTC"`),
   so the `not display_tz_name` branch at `:2141` becomes mostly unreachable. When
   Dispatcharr TZ is unset → display `"UTC"`; if the channel-name source TZ is also
   UTC the `source == display` branch returns `output_timezone=source_tz_name` (no
   conversion). This matches the "accepted consequence" and is covered by the
   in-container smoke test.

## Files touched

- `Event-Channel-Managarr/plugin.py` — resolver, injection points, reader default,
  `_localized_template_props` read, remove field, version bump via `bump_version.py`.
- `Event-Channel-Managarr/plugin.json` — remove field, version bump.
- `README.md` — broader than first scoped (per QA review). All now-false passages:
  - `:35` "with a simple dropdown for timezone selection" (dropdown removed)
  - `:61` the **🌍 Timezone** Settings-Reference row (remove)
  - `:217` "the scheduler **`Timezone`** (`timezone`)"
  - `:221` example row keyed on "scheduler TZ `America/Chicago`"
  - `:225` "**The scheduler `Timezone` setting doubles as the display time zone.**"
  - `:148`, `:197`, `:309` softer references → reword to "Dispatcharr's Time Zone"
  - Replace with: TZ comes from Dispatcharr's **General Settings → Time Zone**
    (falls back to **UTC** when unset).
- `Event-Channel-Managarr/ecm_parsing.py` — add `coerce_timezone(value)`.
- `tests/unit/test_ecm_parsing.py` — add `coerce_timezone` cases.
- `.github/workflows/ci.yml` + `requirements-dev.txt` — add `pytz` so the new
  test runs in CI (CI currently installs only `pytest python-dateutil ruff`;
  `requirements-dev.txt` is empty). `pytz` is already present in the Dispatcharr
  container and the local venv.

## Testing

- **Manifest contract:** removing `timezone` from BOTH `plugin.py` fields and
  `plugin.json` keeps the parity test green. Verify no existing test asserts the
  `timezone` field exists.
- **Resolver fallback:** unit-test the pure `_coerce_tz(value)` helper for the
  blank / `None` / invalid-tz → `"UTC"` cases and the valid-tz passthrough. The
  DB read in `_dispatcharr_timezone()` is not unit-tested (Django unavailable in
  the test env); it is covered by the in-container smoke test below.
- **In-container smoke:** deploy, set Dispatcharr Time Zone to a known value, run
  a dry run, confirm the schedule-status message and CSV reflect that zone; unset
  it and confirm UTC.

## Out of scope

- Changing `dummy_epg_event_timezone`.
- Writing to / managing Dispatcharr's global settings.
- Migrating or deleting the old `timezone` saved value.
- Picking up a Dispatcharr Time Zone change mid-run without a re-save (same
  limitation as today: the scheduler reflects the new tz after the next
  Save Schedule / plugin reload).
