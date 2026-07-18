# Timezone-Localized Dummy EPG Titles — Design

**Date:** 2026-05-09
**Status:** Approved (pre-implementation)
**Plugin:** Event-Channel-Managarr (ECM)
**Target file:** `Event-Channel-Managarr/plugin.py`

## Goal

When the source TZ encoded in channel names differs from the user's local viewing TZ, render the program's local time + zone abbreviation directly in the dummy-EPG program title.

**Example:** Channel `Boxing 5/9 12:00 PM` (source TZ `US/Eastern`), display TZ `America/Chicago` → guide shows `Boxing 5/9 11:00 AM CST`.

## Non-goals

- Adding new settings (no `dummy_epg_display_timezone`, no 12/24-hour toggle, no ISO-date toggle).
- Changing date or time *parsing* from channel names — only display.
- Modifying the fallback (no parseable time) path — `{channel_name}` stays as-is.
- Modifying actual program scheduling — Dispatcharr already converts UTC↔TZ correctly.
- Adding a dedicated `dummy_epg_display_timezone` setting (deferred; scheduler `timezone` is reused — see limitation #2).

## Settings (all existing — no new fields)

| Setting | Role |
|---|---|
| `dummy_epg_event_timezone` | Source TZ — where the time text in channel names lives. |
| `timezone` (scheduler) | Reused as display TZ. Coupling documented in README. |
| `date_format` (US/EU/Auto) | Now also drives rendered date format inside titles. Auto → US. |

## Behavior

- **Source TZ == display TZ (or either invalid):** plain templates restored — exactly today's behavior. Reverting to no-op cleanly overwrites any previously-set localized templates *and* clears the previously-saved `output_timezone` (by writing `""`, which Dispatcharr's renderer treats as "not set" — `views.py:518`).
- **Source TZ ≠ display TZ:** inject `output_timezone` into the managed source's `custom_properties` (Dispatcharr's renderer in `/app/apps/output/views.py` already supports this and converts `{starttime}`, `{endtime}`, `{date}`, `{month}`, `{day}`, `{year}`). Rewrite three of the four templates to include date + time + abbreviation.

### Rendered titles

| State | Emitted template | Example (`date_format=US`, source `US/Eastern`, display `America/Chicago`, standard time) |
|---|---|---|
| Active | `{title} {month}/{day} {starttime}{abbrev_suffix}` | `Cage Fury FC 153 5/9 11:00 PM CST` |
| Upcoming | `Upcoming at {month}/{day} {starttime}{abbrev_suffix}: {title}` | `Upcoming at 5/9 11:00 PM CST: Cage Fury FC 153` |
| Ended | `Ended at {month}/{day} {endtime}{abbrev_suffix}: {title}` | `Ended at 5/10 1:00 AM CST: Cage Fury FC 153` |
| Fallback | `{channel_name}` | unchanged |

EU → date pair becomes `{day}/{month}`. DST → abbreviation becomes `CDT` automatically because it is recomputed each ECM run. Time is rendered 12-hour with AM/PM via Dispatcharr's `{starttime}` / `{endtime}` placeholders (the `{starttime24}` form is unused). The `fallback_title_template` (set in the base `managed_props`) is left untouched in both branches.

`abbrev_suffix` is `" CST"` (with leading space) when `%Z` returns alphabetic; empty string when `%Z` is numeric (e.g., `+0530`, `-05`) — time is still TZ-converted, just unlabeled.

## Implementation

### New helper

A single private method on the `Plugin` class:

```python
def _localized_template_props(self, settings):
    """
    Returns overrides for the three rewritable title templates plus
    `output_timezone`. `fallback_title_template` is set in the base
    `managed_props` and is never overridden here. The DEFAULTS branch
    explicitly writes `output_timezone=""` so a previously-saved value
    is cleared on revert (the diff-and-save loop never deletes keys).
    """
    DEFAULTS = {
        "output_timezone": "",
        "title_template": "{title}",
        "upcoming_title_template": "Upcoming at {starttime}: {title}",
        "ended_title_template": "Ended at {endtime}: {title}",
    }

    source_tz_name = str(settings.get("dummy_epg_event_timezone", "")).strip()
    display_tz_name = str(settings.get("timezone", "")).strip()

    if not source_tz_name or not display_tz_name or source_tz_name == display_tz_name:
        return DEFAULTS

    try:
        pytz.timezone(source_tz_name)
        display_tz = pytz.timezone(display_tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        return DEFAULTS

    abbrev = datetime.now(display_tz).strftime("%Z")
    suffix = f" {abbrev}" if abbrev and abbrev.isalpha() else ""

    fmt = str(settings.get("date_format", "Auto")).strip()
    date_ph = "{day}/{month}" if fmt == "EU" else "{month}/{day}"

    return {
        "output_timezone": display_tz_name,
        "title_template": f"{{title}} {date_ph} {{starttime}}{suffix}",
        "upcoming_title_template": f"Upcoming at {date_ph} {{starttime}}{suffix}: {{title}}",
        "ended_title_template": f"Ended at {date_ph} {{endtime}}{suffix}: {{title}}",
    }
```

`pytz` and `datetime` are already imported at `plugin.py:19,23`. No new imports.

### Wiring

In `_get_or_create_managed_epg_source` (`plugin.py:2125`), immediately after the closing `}` of the `managed_props` dict and before the `EPGSource.objects.get_or_create(...)` call at line 2178, add:

```python
managed_props.update(self._localized_template_props(settings))
```

The existing diff-and-save block (`plugin.py:2197–2213`) already detects template changes and persists, so DST transitions auto-refresh on the next ECM run.

### Why `pytz` and not `zoneinfo`

The Dispatcharr renderer resolves `output_timezone` via `pytz.timezone()`. Using the same library on both sides avoids name-resolution drift between pytz's bundled tzdata and the host's `/usr/share/zoneinfo` (a real risk: a zone valid in one library can be invalid in the other).

## Edge cases

| Case | Behavior |
|---|---|
| Source TZ == display TZ | DEFAULTS returned; plain templates restored AND `output_timezone` written as `""` so any previously-localized state is fully cleared |
| Either TZ unparseable | DEFAULTS returned; degrades silently to plain templates |
| `%Z` returns numeric (e.g., `Etc/GMT+5` → `-05`) | Time still TZ-converted; abbrev label omitted |
| Cross-midnight in display TZ | Dispatcharr's `output_timezone` shifts `{date}/{month}/{day}` correctly; channel-name date and rendered date may differ by ±1 day (documented) |
| DST transition between ECM runs | Abbreviation wrong until next ECM run (documented; manual run fixes it) |
| Time matched but date NOT matched | Renderer fills `{month}/{day}` from "now in display TZ" — confirmed safe in `/app/apps/output/views.py` |
| `date_format = Auto` | Treated as US for rendering (Auto applies to parse-side ambiguity, not display) |
| `date_format = EU` | Date placeholder becomes `{day}/{month}` |

## Known limitations (documented, not fixed)

1. **DST refresh requires a run.** ECM only refreshes the abbreviation literal when it executes. Users who disable scheduling and don't run ECM across a DST transition will see a stale abbreviation. Mitigation: README guidance + manual run. (Auto-refresh on plugin load was considered and rejected to keep ECM runs explicit/scheduled-only.)
2. **Scheduler TZ doubles as display TZ.** A user who set the scheduler TZ to `UTC` for predictable cron will get UTC-displayed titles. Mitigation: README guidance. A dedicated `dummy_epg_display_timezone` setting is a future option if this bites users.
3. **Abbreviation collisions.** `CST` is ambiguous (US Central / China Standard / Cuba). The IANA name resolves correctly behind the scenes; the label is informational.
4. **Channel-name date vs displayed date can differ at far-east display TZs.** A `5/9 11:30 PM ET` channel displayed in `Asia/Tokyo` shows `5/10` because the converted local date rolled over.

## Out-of-scope follow-ups

- **Deferred — not blocking this release:** Upstream Dispatcharr PR adding `{tzname}` / `{tzabbrev}` placeholders (eliminates limitation #1).
- Dedicated `dummy_epg_display_timezone` setting (eliminates limitation #2 and decouples from scheduler).
- 12-hour vs 24-hour toggle.
- ISO date format option for non-US/EU locales.

## Testing

Manual verification only (no unit-test framework in plugin today):

1. `dummy_epg_event_timezone = US/Eastern`, `timezone = America/Chicago`, `date_format = US`.
2. Test channel `PPV EVENT 12: Boxing (5/9 12:00 PM ET)`.
3. `manage_dummy_epg = true` → run ECM → guide shows `Boxing 5/9 11:00 AM CST`.
4. Flip `timezone` back to `US/Eastern` → re-run → title returns to plain `Boxing`. Verify `EPGSource.custom_properties` no longer contains a non-empty `output_timezone` value (inspect via Django admin or `manage.py shell`).
5. `date_format = EU` → `5/9` becomes `9/5`.
6. `timezone = Etc/GMT+5` → time converts, no abbrev suffix.
7. `timezone = bogus/zone` → plain templates render (graceful degrade).

## Documentation

README additions:
- New "Localized Time in EPG Titles" section under EPG features.
- Note that `timezone` (scheduler section) doubles as display TZ.
- Note that `date_format` also affects rendered title dates.
- DST caveat: run ECM after a DST change for immediate refresh.

## Rollout

Patch version bump via `bump_version.py`. Standard ECM release flow per existing project memory: commit → tag → GitHub release ZIP → upstream PR to `Dispatcharr/Plugins`.
