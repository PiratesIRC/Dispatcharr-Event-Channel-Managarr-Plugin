# TZ-Localized Dummy EPG Titles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the channel-name source TZ differs from the user's display TZ, render the program's localized time + zone abbreviation directly in the dummy-EPG title (e.g., `Boxing 5/9 11:00 PM CST`).

**Architecture:** Add one private helper `_localized_template_props(self, settings)` to the `Plugin` class that returns a dict of `output_timezone` + three rewritable title templates. Call it from `_get_or_create_managed_epg_source` and merge into `managed_props`. Dispatcharr's existing renderer (`output_timezone` custom property) does the actual time conversion; ECM bakes the abbreviation as a literal in the template strings, recomputed each ECM run.

**Tech Stack:** Python 3.11+, `pytz` (already imported), Django (Dispatcharr framework), no new dependencies, no test framework in plugin (manual verification).

**Spec:** `docs/superpowers/specs/2026-05-09-tz-localized-dummy-epg-design.md`

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `Event-Channel-Managarr/plugin.py` | Modify | Add helper + one-line wiring in `_get_or_create_managed_epg_source` |
| `README.md` | Modify | Document the feature, scheduler-TZ coupling, DST caveat |
| `Event-Channel-Managarr/plugin.json` | Modify (auto) | Version bump via `bump_version.py` |
| `.wolf/memory.md` | Append | One-line entry per OpenWolf protocol |
| `.wolf/anatomy.md` | Update token estimates | Auto-maintained, but plugin.py grew |

No new files.

---

## Task 1: Add `_localized_template_props` helper

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` (insert new method on `Plugin` class, immediately before `_get_or_create_managed_epg_source` at line 2125)

- [ ] **Step 1: Verify pre-conditions**

Run:
```bash
grep -nE "^import pytz|^from datetime import" /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py
```
Expected output (line numbers may vary, content must match):
```
19:import pytz
23:from datetime import datetime, timedelta
```
If either import is missing, STOP and report — the spec assumes both are already present.

- [ ] **Step 2: Insert the helper method**

Insert this method into the `Plugin` class (the class starts at `plugin.py:212`), placed immediately before the existing `_get_or_create_managed_epg_source` method. Use exact indentation (4 spaces — the method is on the class body):

```python
    def _localized_template_props(self, settings):
        """
        Returns overrides for the three rewritable title templates plus
        `output_timezone` for the managed dummy EPG source.

        - When source TZ == display TZ, or either TZ is invalid/empty:
          returns DEFAULTS (plain templates) and writes
          `output_timezone=""` so any previously-saved value is cleared
          (the diff-and-save loop never deletes keys).
        - Otherwise: returns localized templates with the date placeholder
          driven by `date_format` (US/Auto -> {month}/{day};
          EU -> {day}/{month}) and a TZ abbreviation suffix computed for
          "now" in the display TZ. If %Z returns a numeric offset
          (e.g., +0530), the suffix is omitted but time conversion still
          happens via Dispatcharr's output_timezone.

        `fallback_title_template` is set in the base `managed_props` and
        is never overridden here.
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

- [ ] **Step 3: Smoke-test the helper in isolation via Python REPL**

The plugin is not unit-tested; smoke-test the helper directly. From the Dispatcharr container so `pytz` is available:

```bash
docker exec -i dispatcharr python3 - <<'PY'
import sys; sys.path.insert(0, "/data/plugins/event-channel-managarr")
# Bypass full plugin import (Django imports require app context):
# instead, exercise the helper logic inline with the same code.
import pytz
from datetime import datetime

def helper(settings):
    DEFAULTS = {
        "output_timezone": "",
        "title_template": "{title}",
        "upcoming_title_template": "Upcoming at {starttime}: {title}",
        "ended_title_template": "Ended at {endtime}: {title}",
    }
    src = str(settings.get("dummy_epg_event_timezone", "")).strip()
    dsp = str(settings.get("timezone", "")).strip()
    if not src or not dsp or src == dsp:
        return DEFAULTS
    try:
        pytz.timezone(src); dtz = pytz.timezone(dsp)
    except pytz.exceptions.UnknownTimeZoneError:
        return DEFAULTS
    abbrev = datetime.now(dtz).strftime("%Z")
    suffix = f" {abbrev}" if abbrev and abbrev.isalpha() else ""
    fmt = str(settings.get("date_format", "Auto")).strip()
    date_ph = "{day}/{month}" if fmt == "EU" else "{month}/{day}"
    return {
        "output_timezone": dsp,
        "title_template": f"{{title}} {date_ph} {{starttime}}{suffix}",
        "upcoming_title_template": f"Upcoming at {date_ph} {{starttime}}{suffix}: {{title}}",
        "ended_title_template": f"Ended at {date_ph} {{endtime}}{suffix}: {{title}}",
    }

# Case 1: equal TZs -> DEFAULTS
r = helper({"dummy_epg_event_timezone": "US/Eastern", "timezone": "US/Eastern"})
assert r["output_timezone"] == "", f"expected cleared, got {r}"
assert r["title_template"] == "{title}"
print("OK case1 equal-TZ -> DEFAULTS with cleared output_timezone")

# Case 2: localized US
r = helper({"dummy_epg_event_timezone": "US/Eastern", "timezone": "America/Chicago", "date_format": "US"})
assert r["output_timezone"] == "America/Chicago"
assert r["title_template"].endswith(" CST") or r["title_template"].endswith(" CDT"), r["title_template"]
assert "{month}/{day}" in r["title_template"]
print("OK case2 localized US ->", r["title_template"])

# Case 3: localized EU
r = helper({"dummy_epg_event_timezone": "US/Eastern", "timezone": "Europe/London", "date_format": "EU"})
assert "{day}/{month}" in r["title_template"]
print("OK case3 EU ->", r["title_template"])

# Case 4: bogus display TZ -> DEFAULTS
r = helper({"dummy_epg_event_timezone": "US/Eastern", "timezone": "Bogus/Zone"})
assert r["output_timezone"] == ""
print("OK case4 bogus -> DEFAULTS")

# Case 5: numeric %Z -> no suffix
r = helper({"dummy_epg_event_timezone": "US/Eastern", "timezone": "Etc/GMT+5"})
# Etc/GMT+5 abbrev is "-05" (numeric) -> suffix omitted
assert not r["title_template"].endswith(("CST","CDT","EST","EDT")), r["title_template"]
assert r["title_template"].endswith("{starttime}"), r["title_template"]
print("OK case5 numeric %Z ->", r["title_template"])

# Case 6: empty source TZ -> DEFAULTS
r = helper({"dummy_epg_event_timezone": "", "timezone": "America/Chicago"})
assert r["output_timezone"] == ""
print("OK case6 empty source -> DEFAULTS")

print("ALL HELPER CASES PASS")
PY
```
Expected: ends with `ALL HELPER CASES PASS`. If any case fails, fix the helper before continuing.

- [ ] **Step 4: Commit**

```bash
cd /home/user/docker/Event-Channel-Managarr
git add Event-Channel-Managarr/plugin.py
git commit -m "$(cat <<'EOF'
feat: add _localized_template_props helper for TZ-localized EPG titles

Returns output_timezone + three title-template overrides for the managed
dummy EPG source. DEFAULTS branch explicitly clears output_timezone so
reverting cleanly overwrites previously-localized state.

Helper not yet wired into _get_or_create_managed_epg_source.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire the helper into `_get_or_create_managed_epg_source`

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` around line 2176 (the closing `}` of `managed_props`)

- [ ] **Step 1: Locate the insertion point**

Run:
```bash
grep -n "managed_by.*event-channel-managarr\|EPGSource.objects.get_or_create" /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py | head -5
```
Expected: a `"managed_by": "event-channel-managarr",` line followed a few lines later by `EPGSource.objects.get_or_create(`. Confirm `managed_props = { ... }` ends with `}` immediately before a blank line and the `try:` that wraps `get_or_create`. The wiring goes between the closing `}` and the `try:`.

- [ ] **Step 2: Add the wiring line**

Use Edit to insert one line. The closing `}` of `managed_props` is followed by a blank line and `try:`. Find this exact block:

```python
            "managed_by": "event-channel-managarr",
        }

        try:
            source, created = EPGSource.objects.get_or_create(
```

Replace with:

```python
            "managed_by": "event-channel-managarr",
        }

        managed_props.update(self._localized_template_props(settings))

        try:
            source, created = EPGSource.objects.get_or_create(
```

- [ ] **Step 3: Syntax-check by compiling the file**

Run:
```bash
docker exec dispatcharr python3 -m py_compile /data/plugins/event-channel-managarr/plugin.py 2>&1 || \
  python3 -m py_compile /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py
```
Expected: no output (success). If a `SyntaxError` is reported, fix indentation before continuing.

- [ ] **Step 4: Deploy and verify the helper is reachable**

The deploy path uses hyphens. Refresh the deployed copy and clear Dispatcharr's plugin cache (per existing memory `reference_dispatcharr_docker.md`):

```bash
docker cp /home/user/docker/Event-Channel-Managarr/Event-Channel-Managarr/plugin.py \
  dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker exec dispatcharr find /data/plugins/event-channel-managarr -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
docker restart dispatcharr
```

Wait ~10s for Dispatcharr to come up, then:

```bash
docker exec dispatcharr python3 -c "
from plugins.event_channel_managarr.plugin import Plugin
p = Plugin()
r = p._localized_template_props({'dummy_epg_event_timezone':'US/Eastern','timezone':'America/Chicago','date_format':'US'})
print(r)
assert 'output_timezone' in r and r['output_timezone'] == 'America/Chicago'
print('WIRING OK')
"
```
Expected: prints the dict and `WIRING OK`. If `ModuleNotFoundError`, the deploy path or import slug is wrong — check `find /data/plugins -maxdepth 2 -name plugin.py`.

- [ ] **Step 5: Commit**

```bash
cd /home/user/docker/Event-Channel-Managarr
git add Event-Channel-Managarr/plugin.py
git commit -m "$(cat <<'EOF'
feat: wire _localized_template_props into managed dummy EPG source

managed_props now picks up output_timezone + localized title templates
when source TZ != display TZ. Existing diff-and-save loop persists the
overrides on every ECM run, refreshing the DST abbreviation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: End-to-end verification in Dispatcharr

No automated tests exist for this plugin. Run all 7 scenarios from the spec.

**Files:**
- None modified (verification only)

- [ ] **Step 1: Set up the baseline test channel**

Open Dispatcharr UI → Channels. Create or rename a test channel to:

```
PPV EVENT 12: Boxing (5/9 12:00 PM ET)
```

This matches ECM's existing `title_pattern` regex (the regex was validated against this exact format — see `plugin.py` comment around line 2147).

- [ ] **Step 2: Set ECM to localized config and run**

In Dispatcharr UI → Plugins → Event Channel Managarr settings, set:
- `dummy_epg_event_timezone = US/Eastern`
- `timezone = America/Chicago`
- `date_format = US`
- `manage_dummy_epg = true`

Click the run/apply action.

- [ ] **Step 3: Verify the rendered title**

Open the Dispatcharr TV Guide and locate the test channel. Expected title (assuming current = standard time): `Boxing 5/9 11:00 AM CST`. If DST is active: `Boxing 5/9 11:00 AM CDT`.

Also verify by inspecting `EPGSource.custom_properties`:
```bash
docker exec dispatcharr python3 -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dispatcharr.settings')
django.setup()
from apps.epg.models import EPGSource
s = EPGSource.objects.get(name='ECM Managed Dummy')
import json; print(json.dumps(s.custom_properties, indent=2))
"
```
Expected: `output_timezone == "America/Chicago"`, `title_template` ends with ` CST` or ` CDT`, `title_template` contains `{month}/{day}`.

- [ ] **Step 4: Verify revert clears `output_timezone`**

In settings, change `timezone` back to `US/Eastern` (so source == display). Re-run ECM. Re-inspect:
```bash
docker exec dispatcharr python3 -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dispatcharr.settings')
django.setup()
from apps.epg.models import EPGSource
s = EPGSource.objects.get(name='ECM Managed Dummy')
print('output_timezone =', repr(s.custom_properties.get('output_timezone')))
print('title_template =', repr(s.custom_properties.get('title_template')))
"
```
Expected: `output_timezone = ''` (empty string) and `title_template = '{title}'`. If `output_timezone` is still `'America/Chicago'`, the DEFAULTS-clearing fix is broken — debug Task 1 helper.

- [ ] **Step 5: Verify EU date format**

Set `timezone = America/Chicago`, `date_format = EU`. Re-run. Check: `title_template` contains `{day}/{month}` (not `{month}/{day}`). Guide title should now show `Boxing 9/5 11:00 AM CST`.

- [ ] **Step 6: Verify numeric-offset TZ omits abbreviation**

Set `timezone = Etc/GMT+5`. Re-run. Check `custom_properties`:
- `title_template` should end with `{starttime}` (no trailing alphabetic abbreviation).
- Time should still convert (e.g., `Boxing 5/9 7:00 AM` since `Etc/GMT+5` is UTC-5, ET is UTC-5 standard or UTC-4 DST — exact value depends on DST).

- [ ] **Step 7: Verify graceful degrade on bogus TZ**

Temporarily set `timezone = Bogus/Zone` (you may need to bypass UI validation; if the UI rejects it, edit the saved value via `manage.py shell`). Re-run. Expected: ECM does not error; templates revert to DEFAULTS; `output_timezone == ""`.

Restore `timezone = America/Chicago` and confirm everything works again.

- [ ] **Step 8: No commit (verification only)**

If any scenario fails, return to Task 1 or 2 and fix. If all pass, proceed.

---

## Task 4: README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the EPG features section**

Run:
```bash
grep -nE "^## |^### " /home/user/docker/Event-Channel-Managarr/README.md | head -30
```
Identify the section that documents `dummy_epg_event_timezone` / `manage_dummy_epg`. The new content goes immediately after that section.

- [ ] **Step 2: Add the "Localized Time in EPG Titles" subsection**

Append this subsection after the existing dummy-EPG documentation (use Edit; choose an existing heading line as the anchor):

```markdown
### Localized Time in EPG Titles

When **`Event Time Zone`** (`dummy_epg_event_timezone`) and the scheduler **`Time Zone`** (`timezone`) are different, ECM rewrites the dummy EPG titles to show the program's local time and zone abbreviation:

| Setup | Title in guide |
|---|---|
| Event TZ `US/Eastern`, scheduler TZ `America/Chicago` | `Boxing 5/9 11:00 PM CST` |
| Same as above, after DST starts | `Boxing 5/9 11:00 PM CDT` |
| Event TZ == scheduler TZ | `Boxing` (plain — today's behavior) |

**The scheduler `Time Zone` setting doubles as the display time zone.** If you set it to `UTC` for predictable scheduled runs, EPG titles will show UTC times. If this becomes a problem, open an issue — a separate `dummy_epg_display_timezone` setting is on the deferred list.

**DST caveat:** the abbreviation (`CST` vs `CDT`) is recomputed every time ECM runs. If you disable scheduling and don't trigger a manual run after a DST transition, the abbreviation will be stale (the *time itself* is always correct, only the trailing label lags). Run ECM once after a DST change to refresh.

**Date format inside titles** follows your existing `Date Format` setting:
- `US` or `Auto` → `M/D` (e.g., `5/9`)
- `EU` → `D/M` (e.g., `9/5`)

**Numeric-offset zones** (e.g., `Etc/GMT+5`) suppress the abbreviation suffix — ECM still converts the time but writes no trailing label, since `+0500` would look wrong in a title.
```

- [ ] **Step 3: Commit**

```bash
cd /home/user/docker/Event-Channel-Managarr
git add README.md
git commit -m "$(cat <<'EOF'
docs: document TZ-localized dummy EPG titles

Covers the new behavior, scheduler-TZ-as-display-TZ coupling, DST
refresh caveat, date_format effect on rendered titles, and numeric-
offset zone handling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Version bump and release prep

**Files:**
- Modify (auto): `Event-Channel-Managarr/plugin.py` version field, `Event-Channel-Managarr/plugin.json`

- [ ] **Step 1: Run the bump script**

Per `bump_version.py` (described in `.serena/memories` and project memory): version format is `MAJOR.MINOR.YYMMDDHHMM`-style (project uses a date-coded patch). Run:

```bash
cd /home/user/docker/Event-Channel-Managarr
python3 bump_version.py
```
Expected: prints the old and new versions; updates both `plugin.py` and `plugin.json`. Capture the new version string for the commit message.

- [ ] **Step 2: Verify both files updated**

```bash
cd /home/user/docker/Event-Channel-Managarr
grep -nE "version" Event-Channel-Managarr/plugin.json
grep -nE "^VERSION|^__version__|version\s*=" Event-Channel-Managarr/plugin.py | head -3
```
Expected: both files show the same new version string.

- [ ] **Step 3: Commit**

Replace `<NEW_VERSION>` with the actual string printed by bump_version.py:

```bash
cd /home/user/docker/Event-Channel-Managarr
git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json
git commit -m "$(cat <<'EOF'
Bump to v<NEW_VERSION>: TZ-localized dummy EPG titles

When source TZ != display TZ, the managed dummy EPG titles now show
the program's local time + zone abbreviation (e.g., 'Boxing 5/9
11:00 PM CST'). Reuses scheduler timezone as display TZ. Date format
in title follows date_format setting. Reverting source==display
cleanly clears output_timezone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: OpenWolf housekeeping**

Per `CLAUDE.md` / `.wolf/OPENWOLF.md`, append a one-line entry to `.wolf/memory.md`:

```bash
TS=$(date +%H:%M)
echo "| $TS | tz-localized dummy epg titles shipped | plugin.py, README.md, plugin.json | v<NEW_VERSION> released | ~2k |" >> /home/user/docker/Event-Channel-Managarr/.wolf/memory.md
```

(`.wolf/` is gitignored or auto-managed; do not add to git.)

- [ ] **Step 5: Push and tag (do NOT do automatically — confirm with user)**

The release flow (tag + push + GitHub release ZIP + upstream PR to `Dispatcharr/Plugins`) is documented in `GEMINI.md` and project memory `S15`. **Do not run any of the following without an explicit user "go" — these are user-visible/upstream-affecting actions.**

When the user approves, the next steps are (NOT part of this plan's auto-execution):
- `git push origin main`
- `git tag v<NEW_VERSION> && git push origin v<NEW_VERSION>`
- Build release ZIP via `zip.cmd` (Windows-only — reference only, do not run from Linux)
- `gh release create v<NEW_VERSION> ...`
- Open upstream PR against `Dispatcharr/Plugins`

This plan ends at the local commit. The user drives release.

---

## Self-Review

**Spec coverage:**
- Goal & example → Tasks 1, 2, 3 (Step 3) ✓
- Settings (no new fields) → Task 1 (helper reads existing keys) ✓
- Behavior (source==display & either invalid → DEFAULTS with cleared `output_timezone`) → Task 1 Step 2, Task 3 Step 4 ✓
- Rendered titles table → Task 3 Steps 3 & 5 ✓
- Helper code (verbatim) → Task 1 Step 2 ✓
- Wiring at line 2178 → Task 2 Step 2 ✓
- `pytz` not `zoneinfo` → Task 1 Step 2 (uses `pytz.timezone`) ✓
- Edge cases: numeric `%Z`, both-TZ validation, no fallback override → Task 1 Step 3 cases 4–6, Task 3 Steps 6–7 ✓
- DST behavior → README in Task 4 ✓
- Limitations documented → Task 4 ✓
- Manual test plan (7 scenarios) → Task 3 Steps 1–7 ✓
- README updates (4 bullets) → Task 4 Step 2 ✓
- Patch version bump + standard release flow → Task 5 ✓

**Placeholder scan:** No `TBD`/`TODO` in the plan. Every step shows commands and expected output. The only template token is `<NEW_VERSION>` in Task 5 commit messages, which is filled by Step 1's script output — explicitly noted.

**Type consistency:** Helper signature `_localized_template_props(self, settings) -> dict` is identical in Task 1 and used identically in Task 2. Returned keys (`output_timezone`, `title_template`, `upcoming_title_template`, `ended_title_template`) match the spec and the verification commands in Task 3.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-tz-localized-dummy-epg-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
