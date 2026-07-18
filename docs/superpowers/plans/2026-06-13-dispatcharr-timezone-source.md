# Source Timezone from Dispatcharr — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the plugin's own 🌍 Timezone setting and source the scheduler/display timezone from Dispatcharr's global `apps.dashboard.models.Settings.time_zone`, falling back to `UTC`.

**Architecture:** A Django-free pure validator `coerce_timezone()` in `ecm_parsing.py` (unit-tested) plus a thin `_dispatcharr_timezone()` resolver in `plugin.py` that reads Dispatcharr's global setting. The resolved zone is injected once into `settings["timezone"]` at each operation entry point, so every existing reader (`_get_system_timezone`, `_localized_template_props`) works unchanged with no per-channel DB hits. The plugin's `timezone` field is deleted from both manifest files.

**Tech Stack:** Python 3, Django (plugin runtime only), pytz, pytest. The deployable code is the INNER folder `Event-Channel-Managarr/Event-Channel-Managarr/`.

**Spec:** `docs/superpowers/specs/2026-06-13-dispatcharr-timezone-source-design.md`

**Conventions (from CLAUDE.md):**
- Pure/testable logic goes in `ecm_parsing.py` (Django-free). `plugin.py` is NOT importable in tests.
- `pythonpath = ["Event-Channel-Managarr"]` (pyproject.toml) → tests do `import ecm_parsing`.
- Version bumps ONLY via `python bump_version.py` (keeps plugin.py + plugin.json in sync). Never hand-edit version strings.
- Hot-reload fires on `plugin.json` mtime — deploy BOTH files.

---

## File Structure

- `Event-Channel-Managarr/ecm_parsing.py` — **modify**: add `coerce_timezone(value) -> str`.
- `tests/unit/test_ecm_parsing.py` — **modify**: add `coerce_timezone` cases.
- `.github/workflows/ci.yml` — **modify**: add `pytz` to the install step.
- `requirements-dev.txt` — **modify**: populate with `pytest`, `python-dateutil`, `pytz`.
- `Event-Channel-Managarr/plugin.py` — **modify**: resolver, injection ×4, `DEFAULT_TIMEZONE`, `_localized_template_props`, remove field, help text.
- `Event-Channel-Managarr/plugin.json` — **modify**: remove `timezone` field; version bump.
- `README.md` — **modify**: remove/reword timezone passages.

---

## Task 1: Add `coerce_timezone()` to ecm_parsing.py (TDD)

**Files:**
- Test: `tests/unit/test_ecm_parsing.py`
- Modify: `Event-Channel-Managarr/ecm_parsing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_ecm_parsing.py` (and add `coerce_timezone` to the existing `from ecm_parsing import (...)` block at the top):

```python
# ---------------------------------------------------------------------------
# coerce_timezone — validate Dispatcharr's global tz, fall back to UTC
# ---------------------------------------------------------------------------

COERCE_TZ_CASES = [
    ("America/New_York", "America/New_York"),
    ("Europe/Stockholm", "Europe/Stockholm"),
    ("  Europe/Stockholm  ", "Europe/Stockholm"),  # trimmed
    ("UTC", "UTC"),
    ("", "UTC"),            # blank
    ("   ", "UTC"),         # whitespace only
    (None, "UTC"),          # missing row -> getattr default
    ("Not/AZone", "UTC"),   # invalid name
    (123, "UTC"),           # non-string
]


@pytest.mark.parametrize("value,expected", COERCE_TZ_CASES)
def test_coerce_timezone(value, expected):
    assert coerce_timezone(value) == expected
```

Update the import block at the top of the file to:

```python
from ecm_parsing import (
    apply_meridiem,
    coerce_timezone,
    extract_date_from_channel_name,
    name_has_stop_timestamp,
    resolve_numeric_date_pair,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_ecm_parsing.py -k coerce_timezone -v`
Expected: FAIL — `ImportError: cannot import name 'coerce_timezone'`.

- [ ] **Step 3: Write minimal implementation**

Append to `Event-Channel-Managarr/ecm_parsing.py`:

```python
def coerce_timezone(value):
    """Return a valid IANA timezone name, or ``"UTC"`` as a safe fallback.

    `value` is whatever Dispatcharr has stored for its global time zone — it may
    be ``None`` (no settings row), blank, non-string, or an invalid name. pytz is
    imported lazily so importing this module carries no hard pytz dependency.
    """
    if not isinstance(value, str) or not value.strip():
        return "UTC"
    candidate = value.strip()
    try:
        import pytz
        pytz.timezone(candidate)
    except Exception:
        return "UTC"
    return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_ecm_parsing.py -k coerce_timezone -v`
Expected: PASS (9 cases).

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `python -m pytest -q`
Expected: all pass (existing ecm_parsing + manifest tests + 9 new).

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/ecm_parsing.py tests/unit/test_ecm_parsing.py
git commit -m "feat: add ecm_parsing.coerce_timezone (Django-free tz validator)"
```

---

## Task 2: Add `pytz` to CI and requirements-dev

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `requirements-dev.txt`

- [ ] **Step 1: Add pytz to the CI install step**

In `.github/workflows/ci.yml`, change the install line:

```yaml
        run: pip install pytest python-dateutil ruff
```

to:

```yaml
        run: pip install pytest python-dateutil pytz ruff
```

- [ ] **Step 2: Populate requirements-dev.txt**

Write `requirements-dev.txt` (currently empty) with:

```
pytest
python-dateutil
pytz
```

- [ ] **Step 3: Verify the new test runs against these deps locally**

Run: `python -m pytest tests/unit/test_ecm_parsing.py -k coerce_timezone -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml requirements-dev.txt
git commit -m "ci: add pytz so coerce_timezone test runs in CI"
```

---

## Task 3: Add `_dispatcharr_timezone()` resolver + flip DEFAULT_TIMEZONE to UTC

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

`plugin.py` is not unit-testable (Django) — verification is `py_compile` + the in-container smoke test in Task 7.

- [ ] **Step 1: Flip the fallback constant to UTC**

Find (line ~62-63):

```python
    # Default timezone for scheduling
    DEFAULT_TIMEZONE = "America/Chicago"
```

Replace with:

```python
    # Fallback timezone when Dispatcharr's global time zone is unset/invalid.
    DEFAULT_TIMEZONE = "UTC"
```

- [ ] **Step 2: Add the resolver method directly above `_get_system_timezone`**

Find (line ~1785):

```python
    def _get_system_timezone(self, settings):
        """Get the system timezone from settings"""
```

Insert this method immediately BEFORE it:

```python
    def _dispatcharr_timezone(self):
        """Resolve the effective timezone from Dispatcharr's global setting.

        Reads apps.dashboard.models.Settings.time_zone (Dispatcharr's
        General Settings -> Time Zone). Returns "UTC" when the row is missing,
        blank, or invalid, or if anything raises (e.g. running outside
        Dispatcharr, or the DB is unavailable during migrations). Validation
        and the UTC fallback live in ecm_parsing.coerce_timezone (Django-free,
        unit-tested).
        """
        try:
            from apps.dashboard.models import Settings
            row = Settings.objects.order_by("id").first()
            return ecm_parsing.coerce_timezone(getattr(row, "time_zone", None))
        except Exception as e:
            LOGGER.debug(f"{LOG_PREFIX} Could not read Dispatcharr timezone, using UTC: {e}")
            return "UTC"

```

- [ ] **Step 3: Compile check**

Run: `python -m py_compile Event-Channel-Managarr/plugin.py`
Expected: no output (success).

- [ ] **Step 4: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: add _dispatcharr_timezone resolver; DEFAULT_TIMEZONE -> UTC"
```

---

## Task 4: Inject the resolved tz at the 4 entry points + fix `_localized_template_props`

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`

Injection is idempotent (`settings["timezone"] = self._dispatcharr_timezone()`); double-injection across nested calls is harmless.

- [ ] **Step 1: Inject as the first statement of `_scan_and_update_channels`**

Find (line ~2532-2536):

```python
    def _scan_and_update_channels(self, settings, logger, dry_run=True, is_scheduled_run=False):
        """Scan channels and update visibility based on hide rules priority"""
        # Cross-worker serialization: one scan at a time across all uwsgi workers.
        # Covers manual Run Now / Dry Run as well as scheduled runs.
        lock_fd = None
```

Replace with:

```python
    def _scan_and_update_channels(self, settings, logger, dry_run=True, is_scheduled_run=False):
        """Scan channels and update visibility based on hide rules priority"""
        # Source the timezone from Dispatcharr's global setting (overwrites any
        # stale/absent disk value). MUST stay first: every per-channel date rule
        # and _localized_template_props below reads settings["timezone"].
        settings["timezone"] = self._dispatcharr_timezone()
        # Cross-worker serialization: one scan at a time across all uwsgi workers.
        # Covers manual Run Now / Dry Run as well as scheduled runs.
        lock_fd = None
```

- [ ] **Step 2: Inject as the first statement of `_start_background_scheduler`**

Find (line ~1812-1817):

```python
    def _start_background_scheduler(self, settings):
        """Start background scheduler thread"""
        global _bg_thread, _scheduler_lock

        # Use lock to prevent concurrent scheduler starts
        with _scheduler_lock:
```

Replace with:

```python
    def _start_background_scheduler(self, settings):
        """Start background scheduler thread"""
        global _bg_thread, _scheduler_lock

        # Source the timezone from Dispatcharr BEFORE the thread captures it:
        # scheduler_loop computes local_tz once from this dict and never re-reads.
        settings["timezone"] = self._dispatcharr_timezone()

        # Use lock to prevent concurrent scheduler starts
        with _scheduler_lock:
```

- [ ] **Step 3: Inject as the first statement of `update_schedule_action`**

Find (line ~1752-1755):

```python
    def update_schedule_action(self, settings, logger):
        """Save settings and update scheduled tasks"""
        try:
            scheduled_times_str = settings.get("scheduled_times", "").strip()
```

Replace with:

```python
    def update_schedule_action(self, settings, logger):
        """Save settings and update scheduled tasks"""
        try:
            settings["timezone"] = self._dispatcharr_timezone()
            scheduled_times_str = settings.get("scheduled_times", "").strip()
```

- [ ] **Step 4: Inject as the first statement of `check_scheduler_status_action`**

Find (line ~1674-1677):

```python
        global _bg_thread
        try:
            # --- This worker's scheduler thread ---
            worker_pid = os.getpid()
```

Replace with:

```python
        global _bg_thread
        try:
            settings["timezone"] = self._dispatcharr_timezone()
            # --- This worker's scheduler thread ---
            worker_pid = os.getpid()
```

- [ ] **Step 5: Route `_localized_template_props` display tz through the resolver path**

Find (line ~2128):

```python
        display_tz_name = str(settings.get("timezone", "")).strip()
```

Replace with:

```python
        # Display tz comes from Dispatcharr (already injected into settings by the
        # caller via _dispatcharr_timezone); _get_system_timezone is the reader.
        display_tz_name = self._get_system_timezone(settings)
```

- [ ] **Step 6: Compile check**

Run: `python -m py_compile Event-Channel-Managarr/plugin.py`
Expected: no output (success).

- [ ] **Step 7: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -m "feat: inject Dispatcharr tz at scan/scheduler/status entry points"
```

---

## Task 5: Remove the `timezone` field from both manifests + reword help text

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`
- Modify: `Event-Channel-Managarr/plugin.json`

- [ ] **Step 1: Remove the field block from `plugin.py` `fields()`**

Find (line ~353-360):

```python
            {
                "id": "timezone",
                "label": "🌍 Timezone",
                "type": "select",
                "default": self.DEFAULT_TIMEZONE,
                "help_text": "Timezone for scheduled runs. Select the timezone for scheduling. Only one can be selected.",
                "options": self._load_timezones_from_file()
            },
```

Delete that entire block (the `channel_profile_name` block follows it directly).

- [ ] **Step 2: Reword the `dummy_epg_event_timezone` help text in `plugin.py`**

Find (line ~513):

```python
                "help_text": "Timezone encoded in the event times inside channel names (e.g., US/Eastern for channels like '(4.17 8:30 PM ET)'). Different from the scheduler timezone above.",
```

Replace with:

```python
                "help_text": "Timezone encoded in the event times inside channel names (e.g., US/Eastern for channels like '(4.17 8:30 PM ET)'). Independent of Dispatcharr's display time zone.",
```

- [ ] **Step 3: Remove the field from `plugin.json`**

Find (line ~12):

```json
    {"id": "timezone", "label": "🌍 Timezone", "type": "select", "default": "America/Chicago"},
```

Delete that line.

- [ ] **Step 4: Run the manifest-parity + full test suite**

Run: `python -m pytest -q`
Expected: all pass (the contract test checks actions + version, not fields, so removing the field keeps it green).

- [ ] **Step 5: Compile check**

Run: `python -m py_compile Event-Channel-Managarr/plugin.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json
git commit -m "feat: remove plugin timezone field (now sourced from Dispatcharr)"
```

---

## Task 6: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Features list — drop the timezone-dropdown mention (line ~35)**

Find:

```markdown
* **Flexible Scheduling**: Run scans automatically at specific times each day (e.g., `0600,1300,1800`) with a simple dropdown for timezone selection.
```

Replace with:

```markdown
* **Flexible Scheduling**: Run scans automatically at specific times each day (e.g., `0600,1300,1800`). Scheduled-run and guide-display times use Dispatcharr's global time zone (General Settings → Time Zone).
```

- [ ] **Step 2: Settings Reference — remove the 🌍 Timezone row (line ~61)**

Find and DELETE this row:

```markdown
| **🌍 Timezone** | `select` | `America/Chicago` | Timezone for scheduled runs. Select from the dropdown. |
```

- [ ] **Step 3: WrongDayOfWeek rule — reword "your timezone" (line ~148)**

Find `... is not yesterday, today, or tomorrow in your timezone.` and replace `in your timezone` with `in Dispatcharr's time zone`:

```markdown
| **[WrongDayOfWeek]** | — | Hides if the name contains a day name (e.g., "MONDAY", "Mon", "Saturday", "Sat") and the named day is not yesterday, today, or tomorrow in Dispatcharr's time zone. The ±1 day tolerance keeps US/EU named channels visible to viewers in distant timezones (e.g., Australia seeing "Monday Night Football" on local Tuesday). Recognizes full/abbreviated day names plus MNF/TNF/SNF. |
```

- [ ] **Step 4: Localized Time section — rewrite the intro + the "doubles as" paragraph (lines ~217 and ~225)**

Find (line ~217):

```markdown
When **`Event Timezone`** (`dummy_epg_event_timezone`) and the scheduler **`Timezone`** (`timezone`) are different, ECM rewrites the dummy EPG titles to show the program's local time and zone abbreviation:
```

Replace with:

```markdown
When **`Event Timezone`** (`dummy_epg_event_timezone`) and **Dispatcharr's global time zone** (General Settings → Time Zone) are different, ECM rewrites the dummy EPG titles to show the program's local time and zone abbreviation:
```

Find the table rows (lines ~221-223) and update the "scheduler TZ" wording:

```markdown
| Event TZ `US/Eastern`, Dispatcharr TZ `America/Chicago` (DST active, e.g., May) | `Boxing 5/9 8:30 PM ET` | `Boxing 5/9 7:30 PM CDT` |
| Same setup, standard time (e.g., November) | `Boxing 11/9 8:30 PM ET` | `Boxing 11/9 7:30 PM CST` |
| Event TZ == Dispatcharr TZ | (any) | `Boxing` (plain) |
```

Find (line ~225):

```markdown
**The scheduler `Timezone` setting doubles as the display time zone.** If you set it to `UTC` for predictable scheduled runs, EPG titles will show UTC times. If this becomes a problem, open an issue — a separate `dummy_epg_display_timezone` setting is on the deferred list.
```

Replace with:

```markdown
**The display time zone comes from Dispatcharr's General Settings → Time Zone** (it also drives when scheduled runs fire and the day-of-week/date rules). When that is unset, ECM falls back to `UTC`, so EPG titles will show UTC times until you set a zone in Dispatcharr.
```

- [ ] **Step 5: Troubleshooting — reword "separate from the scheduler timezone" (line ~309)**

Find:

```markdown
* **Guide shows the wrong time**: Verify the **Channel Name Event Timezone** setting matches the timezone encoded in channel names. This is separate from the scheduler timezone.
```

Replace with:

```markdown
* **Guide shows the wrong time**: Verify the **Channel Name Event Timezone** setting matches the timezone encoded in channel names, and that Dispatcharr's **General Settings → Time Zone** is set to your display zone (ECM uses it for guide display; it falls back to UTC when unset).
```

- [ ] **Step 6: Sanity-check no stale references remain**

Run: `grep -n "scheduler timezone\|scheduler \`Timezone\`\|🌍 Timezone\|America/Chicago" README.md`
Expected: no matches referencing a plugin-owned timezone setting (the `🌍 Timezone` settings row and "doubles as" line are gone).

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: timezone now sourced from Dispatcharr (remove plugin setting refs)"
```

---

## Task 7: Bump version, verify, commit, deploy + smoke test

**Files:**
- Modify (via script): `Event-Channel-Managarr/plugin.json`, `Event-Channel-Managarr/plugin.py`

- [ ] **Step 1: Bump the version (script only — never hand-edit)**

Run: `python bump_version.py`
Expected: `bumped <old> -> 1.26.<DDDHHMM>`; both files updated in sync.

- [ ] **Step 2: Full verification**

Run: `python -m pytest -q`
Expected: all pass.

Run: `python -m py_compile Event-Channel-Managarr/plugin.py Event-Channel-Managarr/ecm_parsing.py`
Expected: no output.

- [ ] **Step 3: Commit the bump**

```bash
git add Event-Channel-Managarr/plugin.json Event-Channel-Managarr/plugin.py
git commit -m "chore: bump version for Dispatcharr-sourced timezone"
```

- [ ] **Step 4: Deploy to the container** (uses the deploy-plugin skill steps)

```bash
MSYS_NO_PATHCONV=1 docker cp Event-Channel-Managarr/plugin.py     dispatcharr:/data/plugins/event-channel-managarr/plugin.py
MSYS_NO_PATHCONV=1 docker cp Event-Channel-Managarr/plugin.json   dispatcharr:/data/plugins/event-channel-managarr/plugin.json
MSYS_NO_PATHCONV=1 docker cp Event-Channel-Managarr/ecm_parsing.py dispatcharr:/data/plugins/event-channel-managarr/ecm_parsing.py
docker restart dispatcharr
```

- [ ] **Step 5: Smoke test — confirm load + tz resolution**

After ~18s, run:

```bash
docker logs dispatcharr --since 40s | grep "Plugin v"
```

Expected: `Event Channel Managarr Plugin v1.26.<new> initialized`.

Then verify the resolver against Dispatcharr's setting and a dry run:

```bash
docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" <<'PYEOF'
from apps.dashboard.models import Settings
from apps.plugins.loader import PluginManager
row = Settings.objects.order_by("id").first()
print("Dispatcharr time_zone:", getattr(row, "time_zone", None))
res = PluginManager.get().run_action("event-channel-managarr", "dry_run", {})
print("dry_run status:", res.get("status"))
print("dry_run msg:", res.get("message"))
PYEOF
```

Expected: `status: success`; the CSV/scheduler messages reflect Dispatcharr's zone (or `UTC` if Dispatcharr's is unset). No tracebacks.

- [ ] **Step 6: (Optional) confirm the field is gone from the UI manifest**

```bash
docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" <<'PYEOF'
from apps.plugins.models import PluginConfig
cfg = PluginConfig.objects.get(key="event-channel-managarr")
# fields live on the loaded module; check the JSON manifest in the container
import json
m = json.load(open("/data/plugins/event-channel-managarr/plugin.json"))
print("has timezone field:", any(f.get("id") == "timezone" for f in m["fields"]))
PYEOF
```

Expected: `has timezone field: False`.

---

## Self-Review

**Spec coverage:**
- Remove dropdown → Task 5. ✅
- Source from `Settings.time_zone`, fallback UTC → Tasks 1 (validator) + 3 (resolver). ✅
- `dummy_epg_event_timezone` untouched → only its help text reworded (Task 5 Step 2). ✅
- Inject once at 4 entry points → Task 4 Steps 1-4. ✅
- `_localized_template_props` switch → Task 4 Step 5. ✅
- `DEFAULT_TIMEZONE` → UTC → Task 3 Step 1. ✅
- README scope (lines 35, 61, 148, 197, 217-225, 309) → Task 6. (Line 197 "Channel Name Event Timezone" is about the channel-name TZ, correct as-is — intentionally NOT changed.) ✅
- `coerce_timezone` in ecm_parsing + test + CI pytz → Tasks 1, 2. ✅
- Leftover saved `timezone` key harmless → injection overwrites it each run; no migration. ✅
- Version bump via script → Task 7. ✅

**Placeholder scan:** No TBD/TODO; every code step has complete code.

**Type/name consistency:** `coerce_timezone` (Task 1) ↔ called in `_dispatcharr_timezone` (Task 3) ↔ injected via `settings["timezone"]` read by `_get_system_timezone` / `_localized_template_props` (Task 4). `_dispatcharr_timezone` signature `(self)` consistent across call sites. Names match.
