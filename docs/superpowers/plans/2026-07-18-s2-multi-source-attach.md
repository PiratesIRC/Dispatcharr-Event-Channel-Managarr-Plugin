# S2 — Claimed-Channel Reroute Implementation Plan (reroute design, rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End the reclaim that un-fixes the DAZN guide every few hours, by moving channels whose names positively claim a non-default timezone profile — **and whose current EPG is safe to move** — onto that profile's own dummy EPGSource.

**Architecture:** `_run_managed_epg_pass`'s existing machinery is not modified. A new additive step runs at the end of BOTH its exits and re-points only claimed, safe-to-move channels.

**Tech Stack:** Python 3, Django ORM (inside Dispatcharr), pytest, Docker.

> **rev 2 of the reroute design.** The additive-reroute architecture survived its first review round — three reviewers independently confirmed the five existing methods stay untouched, detach cannot reach the new source, and the same-pass correction genuinely works for applied runs. But that round found **four more Criticals**, fixed here. Sections marked **[REV1 WAS WRONG]** correct a specific error. Across three drafts this slice has now had seven reviews and ten Criticals; treat every claim in it as provisional until a gate proves it.

## Global Constraints

- **`plugin.py`: the ONLY permitted changes are new methods, TWO call sites (one per exit of `_run_managed_epg_pass`), and threading one extra return value.** Do not modify the bodies of `_attach_managed_epg`, `_detach_managed_epg`, `_managed_override_ids`, `_get_or_create_managed_epg_source`, or `_localized_template_props`. Task 3 hash-pins those bodies.
- **`ecm_profiles.py` stays STDLIB-ONLY** (only `regex` inside `try/except ImportError`), no module-level mutable state.
- Patterns are STORED in JS named-group form `(?<name>)` (issue #21).
- Commit trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; messages via Bash heredoc + `git commit -F`.
- PowerShell 5.1 **cannot parse `<` redirection** and parses whole blocks before executing, so a stray `<` also silently skips preceding `docker cp` lines. Put redirection inside the container's `sh -c`.
- `docker exec` writing under `/data` uses `-u dispatch`.
- Repo `C:\Users\User\docker\Event-Channel-Managarr`, branch `feat/s2-multi-source`. Code map: `.superpowers/sdd/s2-code-map.md`.

## What the first review round found — the four fixes

**[REV1 WAS WRONG] 1. The reroute could steal a channel's REAL, populated EPG.** Claims were made purely by name regex, and `Next:` / `(GMT)` are standard EPG conventions, not DAZN-specific. On an install with a legitimately-populated UK/EU feed named that way, the first applied pass would rip the binding off and replace it with a dummy's fallback text. The adjacent `_managed_override_ids` already solves exactly this (bug-043) and rev 1 did not replicate its guard. **The "0 currently misplaced" headline was a fact about this box's data, not about the code.**
→ **Task 3 adds `_epg_binding_is_reroutable`.** A channel is moved only off NOTHING, off a dummy source, or off a real source with no programme in the next 24h.

**[REV1 WAS WRONG] 2. The reroute step was unreachable during a Dry Run.** `_run_managed_epg_pass` has TWO `return attached_ids, detached_ids` statements; the dry-run branch returns early at plugin.py:2654. Rev 1's single call site sat before the *final* return, inside the applied branch. So the "👁️ Dry Run" button would never report the reroute an applied run performs — in a workflow whose entire safety culture is preview-then-apply. Every test rev 1 proposed passed anyway.
→ **Task 4 calls it from BOTH exits**, and the gate exercises `dry_run=True` behaviorally.

**[REV1 WAS WRONG] 3. The disarm procedure caused the blackout its own check could not see.** Rev 1 disarmed by setting `manage_dummy_epg=False`. But with that toggle off the pass performs a **full unscoped teardown** (`keep_ids=set()`, `detach_scope=None`) — nulling all 72 channels on the default source. And the background scheduler thread runs *independently of that flag*, so a `0400/1000/1100/1200` tick during the window fires it for real. Then the Step-5 re-arm re-attaches everything to the same source, so the binding diff reports `LOST EPG = 0` while the guide was dark for the whole window.
→ **Task 6 never touches `manage_dummy_epg`.** It stops the scheduler thread and disables `auto_rescan_on_m3u_refresh` only, so no teardown path is ever entered.

**[REV1 WAS WRONG] 4. The rolled-back gate leaked a real websocket broadcast.** `create_dummy_epg_data` calls `send_websocket_update` **synchronously inside `.save()`**, not via `transaction.on_commit`, so rev 1's caveat ("on_commit hooks don't fire under rollback") understated it. Creating a profile source inside the gate broadcasts `epg_data_created` to every connected browser, then rolls the row back underneath it.
→ **Task 5 monkeypatches `send_websocket_update` and asserts nothing escaped.**

Also fixed from the same round: orphan `EPGData` rows on the new source were architecturally unreapable (the reaper has one call site, hardcoded to the default source); `rerouted_ids` was folded into `attached_ids`, corrupting the CSV/notification audit trail; the dry-run branch compared `None == None` and under-reported never-bound channels; frozen-*signature* tests could not catch a changed method *body*; and full `pg_restore` was the documented rollback for a defect scoped to 278 channels.

## Verified ground truth (live, 2026-07-18)

```
group 1915 "US: PPV"                 278 channels
positive dazn_gmt claims              46      of which 5 enabled, 41 hidden
  currently on the wrong source        0      <- a fact about THIS box's data only
  currently unbound                    0
channels with epg_data IS NULL       107      (0 of them enabled)
EPGSource 18 "ECM Managed Dummy"      72 channels, 15 orphan EPGData (14 UUID-shaped,
                                                   all DAZN-named -- pre-existing leak)
EPGSource 42 "DAZN PPV Dummy (GMT)"   99 channels, managed_by=manual-dazn-gmt
scheduled_times                       0400,1000,1100,1200   (scheduler thread, independent
                                                             of manage_dummy_epg)
```

## File Structure

| Path | Responsibility | Change |
|---|---|---|
| `Event-Channel-Managarr/ecm_profiles.py` | `build_profiles`, `resolve_output_timezone`, `claimed_targets` | Modify |
| `Event-Channel-Managarr/plugin.py` | New methods; two call sites; a third return value | Modify |
| `tests/unit/test_ecm_profiles.py` | Settings + timezone resolution | Modify |
| `tests/unit/test_claimed_targets.py` | Positive-claim semantics | Create |
| `tests/contract/test_s2_wiring.py` | Runtime guards + hash-pinned frozen bodies | Create |
| `scripts/verify_s2_incontainer.py` | Gate: rolled-back real pass, dry-run behavior, props read-back | Create |
| `scripts/s2_targeted_repair.py` | Targeted rollback (not a full pg_restore) | Create |

---

## Task 1: Settings and timezone resolution (pure)

Unchanged from the previous revision. **Files:** `Event-Channel-Managarr/ecm_profiles.py`, `tests/unit/test_ecm_profiles.py`

**Interfaces produced:** `build_profiles(settings)`, `resolve_output_timezone(source_tz_name, system_tz_name, date_format="Auto")`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ecm_profiles.py`:

```python
def test_build_profiles_honours_the_event_timezone_setting():
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Europe/Stockholm"})
    assert next(p for p in built if p.is_default).timezone == "Europe/Stockholm"


def test_build_profiles_never_changes_the_dazn_timezone():
    """UTC is a fact about the provider's data, not a user preference."""
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Europe/Stockholm"})
    assert next(p for p in built if p.key == "dazn_gmt").timezone == "UTC"


@pytest.mark.parametrize("bad", ["", "nonsense", 0, -3, None])
def test_build_profiles_falls_back_on_a_bad_duration(bad):
    built = ecm_profiles.build_profiles({"dummy_epg_event_duration_hours": bad})
    assert next(p for p in built if p.is_default).program_duration_minutes > 0


def test_build_profiles_preserves_dazn_selector_and_patterns():
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Asia/Tokyo"})
    dazn = next(p for p in built if p.key == "dazn_gmt")
    assert dazn.selector == ecm_profiles.DAZN_GMT.selector
    assert dazn.title_pattern == ecm_profiles.DAZN_GMT.title_pattern


def test_resolve_output_timezone_converts_and_labels():
    """THE assertion this plumbing exists for. If the GMT source inherits the ET
    source's config, every DAZN time renders five hours wrong."""
    got = ecm_profiles.resolve_output_timezone("UTC", "America/Chicago")
    assert got["output_timezone"] == "America/Chicago"
    assert "{starttime}" in got["upcoming_title_template"]


def test_resolve_output_timezone_is_not_symmetric():
    """Guards a swapped-argument bug: both parameters are plain strings, so
    transposing them raises nothing and silently renders times wrong."""
    a = ecm_profiles.resolve_output_timezone("UTC", "America/Chicago")
    b = ecm_profiles.resolve_output_timezone("America/Chicago", "UTC")
    assert a != b


def test_resolve_output_timezone_same_zone_uses_plain_templates():
    got = ecm_profiles.resolve_output_timezone("America/Chicago", "America/Chicago")
    assert got["upcoming_title_template"] == "Upcoming at {starttime}: {title}"


@pytest.mark.parametrize("src,sys_tz", [("", "America/Chicago"),
                                        ("Not/AZone", "America/Chicago"),
                                        ("UTC", "Not/AZone")])
def test_resolve_output_timezone_degrades_without_raising(src, sys_tz):
    got = ecm_profiles.resolve_output_timezone(src, sys_tz)
    assert set(got) == {"output_timezone", "title_template",
                        "upcoming_title_template", "ended_title_template"}
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/unit/test_ecm_profiles.py -k "build_profiles or resolve_output" -v` → `AttributeError`.

- [ ] **Step 3: Implement**

Change the import to `from dataclasses import dataclass, replace`, then append to `ecm_profiles.py`:

```python
DEFAULT_EVENT_TIMEZONE = "US/Eastern"
DEFAULT_DURATION_HOURS = 3

_PLAIN_TEMPLATES = {
    "title_template": "{title}",
    "upcoming_title_template": "Upcoming at {starttime}: {title}",
    "ended_title_template": "Ended at {endtime}: {title}",
}


def resolve_output_timezone(source_tz_name, system_tz_name, date_format="Auto"):
    """Decide output_timezone and title templates for ONE source.

    Pure: the caller supplies both zone NAMES. Extracted so it can be asserted --
    in the single-source code this logic had no test, and a wrong result is a
    silent multi-hour error in every rendered title. NOTE the parameter order is
    (source, system): transposing them raises nothing.
    """
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
    except ImportError:                       # pragma: no cover
        return dict(_PLAIN_TEMPLATES, output_timezone="")

    source_tz_name = str(source_tz_name or "").strip()
    system_tz_name = str(system_tz_name or "").strip()
    if not source_tz_name:
        return dict(_PLAIN_TEMPLATES, output_timezone="")
    try:
        ZoneInfo(source_tz_name)
    except Exception:
        return dict(_PLAIN_TEMPLATES, output_timezone="")
    if not system_tz_name or source_tz_name == system_tz_name:
        return dict(_PLAIN_TEMPLATES, output_timezone=source_tz_name)
    try:
        display = ZoneInfo(system_tz_name)
    except Exception:
        return dict(_PLAIN_TEMPLATES, output_timezone=source_tz_name)

    abbrev = datetime.now(display).strftime("%Z")
    suffix = f" {abbrev}" if abbrev.isalpha() else ""
    date_ph = "{day}/{month}" if str(date_format).strip().upper() == "EU" else "{month}/{day}"
    return {
        "output_timezone": system_tz_name,
        "title_template": "{title}",
        "upcoming_title_template": f"Upcoming at {date_ph} {{starttime}}{suffix}: {{title}}",
        "ended_title_template": f"Ended at {date_ph} {{endtime}}{suffix}: {{title}}",
    }


def _resolve_duration_minutes(raw):
    try:
        hours = int(str(raw).strip())
    except (ValueError, TypeError, AttributeError):
        hours = DEFAULT_DURATION_HOURS
    return (hours if hours > 0 else DEFAULT_DURATION_HOURS) * 60


def build_profiles(settings):
    """Resolve the frozen PROFILES template against live plugin settings.

    PROFILES carries provider FACTS. This resolves only what a USER controls, so
    existing settings keep working. dazn_gmt's timezone is never resolved from
    settings -- it is UTC because the provider stamps (GMT) in the name.

    dummy_epg_channel_format is deliberately NOT handled: this plan does not touch
    the default source, so the existing code keeps owning the US/SE choice.
    """
    settings = settings or {}
    duration = _resolve_duration_minutes(settings.get("dummy_epg_event_duration_hours"))
    tz = str(settings.get("dummy_epg_event_timezone") or "").strip() or DEFAULT_EVENT_TIMEZONE
    return tuple(
        replace(p, timezone=tz, program_duration_minutes=duration) if p.is_default
        else replace(p, program_duration_minutes=duration)
        for p in PROFILES)
```

- [ ] **Step 4: Run** — `python -m pytest tests/unit/test_ecm_profiles.py tests/contract/test_module_purity.py -v`, all pass.

- [ ] **Step 5: Commit**

```
feat: build_profiles and resolve_output_timezone

Resolves the frozen template against live settings so dummy_epg_event_timezone and
_duration_hours keep governing the default profile. dazn_gmt's UTC is never
resolved from settings -- it is a fact about the data.

resolve_output_timezone extracts the output-timezone computation as a pure
function so it can be asserted; in the single-source code it had no test at all.
Its parameter order is (source, system) and transposing them raises nothing, so a
non-symmetry test pins it.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 2: Positive-claim semantics

Unchanged. **Files:** `Event-Channel-Managarr/ecm_profiles.py`, `tests/unit/test_claimed_targets.py`

**Interfaces produced:** `claimed_targets(names, profiles) -> dict[str, str]` — name → NON-DEFAULT profile key. Names claimed by nothing, or only by the default, are **absent**. Absence is the safety property: nothing can move a name that is not in this mapping.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_claimed_targets.py
"""Positive-claim semantics.

claimed_targets returns ONLY names a non-default selector positively claims.
Everything else is ABSENT, and absence is the safety property: the reroute step
can act only on names present here, so unclaimed and default-family channels
cannot be moved at all.

NOTE: a claim is necessary but NOT sufficient to move a channel -- the caller must
also check the binding is safe to move (see _epg_binding_is_reroutable, Task 3).
A name claim says "this belongs to profile X"; it says nothing about whether the
channel currently holds a real, populated EPG that must not be destroyed.
"""

from pathlib import Path

import pytest

import ecm_profiles

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"


def _names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _profiles():
    return ecm_profiles.build_profiles({})


def test_gmt_names_are_claimed_by_dazn():
    names = _names()
    claims = ecm_profiles.claimed_targets(names, _profiles())
    assert all(claims.get(n) == "dazn_gmt" for n in names if "(GMT)" in n)


def test_unclaimed_names_are_absent_not_defaulted():
    idle = "NO EVENT STREAMING NOW - | 8K EXCLUSIVE | US: DAZN PPV 50"
    assert idle not in ecm_profiles.claimed_targets([idle], _profiles())


def test_default_family_names_are_absent():
    legacy = "PPV EVENT 07: MARS Late Models at Farmer City (7.17 7:30 PM ET)"
    assert legacy not in ecm_profiles.claimed_targets([legacy], _profiles())


def test_no_default_key_ever_appears_as_a_value():
    claims = ecm_profiles.claimed_targets(_names(), _profiles())
    assert next(p.key for p in _profiles() if p.is_default) not in set(claims.values())


def test_claim_count_on_the_real_corpus():
    """46 of 278 -- the maximum blast radius before the safety guard narrows it."""
    claims = ecm_profiles.claimed_targets(_names(), _profiles())
    assert len(claims) == 46
    assert set(claims.values()) == {"dazn_gmt"}


def test_empty_input_and_default_only_profiles_yield_no_claims():
    assert ecm_profiles.claimed_targets([], _profiles()) == {}
    only_default = tuple(p for p in _profiles() if p.is_default)
    assert ecm_profiles.claimed_targets(_names(), only_default) == {}


def test_a_broken_selector_claims_nothing_rather_than_raising():
    broken = ecm_profiles.Profile(
        key="broken", source_name="B", selector=r"(?<unclosed",
        title_pattern="", date_pattern="", time_pattern="", timezone="UTC",
        output_timezone="UTC", program_duration_minutes=60, include_date=False,
        title_template="{title}", upcoming_title_template="", ended_title_template="",
        fallback_title_template="", fallback_description_template="", is_default=False)
    default = next(p for p in _profiles() if p.is_default)
    assert ecm_profiles.claimed_targets(["anything"], (broken, default)) == {}
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** — append to `ecm_profiles.py`:

```python
def claimed_targets(names, profiles):
    """Map name -> NON-DEFAULT profile key, for names positively claimed.

    Names claimed by no selector, or only by the default, are ABSENT. That absence
    is the safety property: a caller can act only on names present here.

    A claim is NECESSARY BUT NOT SUFFICIENT to move a channel. It says nothing
    about whether the channel currently holds a real, populated EPG -- the caller
    must check that separately.
    """
    claims = {}
    compiled = [(p, compile_pattern(p.selector)) for p in profiles if not p.is_default]
    for name in names:
        for profile, selector in compiled:
            if selector is not None and selector.search(name):
                claims[name] = profile.key
                break
    return claims
```

- [ ] **Step 4: Run** — `python -m pytest tests/ -q`, all pass.

- [ ] **Step 5: Commit** (message as in the previous revision, plus a line noting a claim is necessary-but-not-sufficient).

---

## Task 3: Source provisioning, the reroutability guard, and frozen-body pins

**Files:** Modify `Event-Channel-Managarr/plugin.py`, create `tests/contract/test_s2_wiring.py`

**Interfaces produced:**
- `_managed_props_for_profile(self, profile, settings) -> dict`
- `_ensure_profile_source(self, profile, settings, logger) -> EPGSource | None`
- `_epg_binding_is_reroutable(self, channel) -> bool`
- `_reap_orphaned_epg_data(self, source, logger) -> int`

- [ ] **Step 1: Add the import** — at `plugin.py` line 44, below `import ecm_parsing`: `import ecm_profiles`

- [ ] **Step 2: Record the frozen-body baseline**

Before changing anything, capture the hashes the guard will pin:

```bash
cd /c/Users/User/docker/Event-Channel-Managarr
python - <<'PY'
import ast, hashlib
src = open("Event-Channel-Managarr/plugin.py", encoding="utf-8").read()
tree = ast.parse(src)
for name in ("_attach_managed_epg", "_detach_managed_epg", "_managed_override_ids",
             "_get_or_create_managed_epg_source", "_localized_template_props"):
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name)
    print(f'    "{name}": "{hashlib.sha256(ast.dump(fn).encode()).hexdigest()}",')
PY
```
Paste the output into `FROZEN_BODIES` in the test file below.

- [ ] **Step 3: Write the failing tests**

```python
# tests/contract/test_s2_wiring.py
"""Guards for S2's plugin.py wiring.

plugin.py imports Django at module scope and cannot be imported outside the
container, so structure is checked with ast. Where structure is too weak, methods
are COMPILED OUT and CALLED with stubs.

The most important guards here are NEGATIVE: this slice's safety argument is that
the existing pass is unmodified. Signature pinning is NOT enough -- someone could
reintroduce bug-045 (global detach ignoring scope_ids) inside _detach_managed_epg
while keeping its exact five arguments. So the BODIES are hash-pinned, reusing the
pattern scripts/core_manifest.json already uses for matching_core.py.
"""

import ast
import hashlib
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
sys.path.insert(0, str(ROOT / "Event-Channel-Managarr"))
import ecm_profiles  # noqa: E402

# Recorded from the pre-S2 baseline (Task 3 Step 2). If one of these changes, this
# slice has modified machinery it promised not to touch.
FROZEN_BODIES = {
    # "_attach_managed_epg": "<sha256>",
    # "_detach_managed_epg": "<sha256>",
    # "_managed_override_ids": "<sha256>",
    # "_get_or_create_managed_epg_source": "<sha256>",
    # "_localized_template_props": "<sha256>",
}


def _source():
    return PLUGIN_PY.read_text(encoding="utf-8")


def _fn(name):
    return next((n for n in ast.walk(ast.parse(_source()))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)


def test_frozen_baseline_was_recorded():
    """A empty FROZEN_BODIES would make every pin below vacuous."""
    assert len(FROZEN_BODIES) == 5, "record the baseline hashes (Task 3 Step 2)"


@pytest.mark.parametrize("name", sorted(FROZEN_BODIES))
def test_frozen_method_body_is_unchanged(name):
    digest = hashlib.sha256(ast.dump(_fn(name)).encode()).hexdigest()
    assert digest == FROZEN_BODIES[name], (
        f"{name}'s BODY changed. This slice's entire safety argument is that the "
        f"existing pass is untouched.")


def test_ecm_profiles_is_imported():
    assert re.search(r"^import ecm_profiles$", _source(), re.M)


def test_new_methods_exist():
    for name in ("_managed_props_for_profile", "_ensure_profile_source",
                 "_epg_binding_is_reroutable", "_reap_orphaned_epg_data"):
        assert _fn(name) is not None, name


def test_props_builder_passes_the_profiles_own_timezone_FIRST():
    """resolve_output_timezone(source, system) is not symmetric and both args are
    plain strings -- a transposition raises nothing and renders times wrong."""
    src = ast.get_source_segment(_source(), _fn("_managed_props_for_profile"))
    call = re.search(r"resolve_output_timezone\(\s*([^,]+),", src)
    assert call and "profile.timezone" in call.group(1), \
        "profile.timezone must be the FIRST argument (the source zone)"
```

- [ ] **Step 4: Run to verify it fails** — `test_new_methods_exist` fails; the frozen-body pins should PASS (nothing modified yet), which is the baseline.

- [ ] **Step 5: Implement**

Insert above `_get_or_create_managed_epg_source` (plugin.py:2249):

```python
    def _epg_binding_is_reroutable(self, channel):
        """May this channel's EPG binding be moved to another source?

        Only when it holds NOTHING, a dummy source, or a real source with no
        programme in the next 24h.

        A name claim alone is NOT sufficient. `Next:` and `(GMT)` are standard EPG
        conventions, not DAZN-specific, so a claim can match a channel carrying a
        legitimately populated real EPG on some other install. Moving that would
        silently destroy a working guide. This mirrors the guard
        _managed_override_ids already applies (bug-043).
        """
        from datetime import timedelta
        from django.utils import timezone as djtz
        from apps.epg.models import ProgramData

        epg_data = channel.epg_data
        if epg_data is None or epg_data.epg_source is None:
            return True
        if getattr(epg_data.epg_source, "source_type", None) == "dummy":
            return True
        now = djtz.now()
        return not ProgramData.objects.filter(
            epg_id=epg_data.id, start_time__lt=now + timedelta(hours=24),
            end_time__gte=now).exists()

    def _reap_orphaned_epg_data(self, source, logger):
        """Delete attach-created EPGData rows on `source` that no channel references.

        The existing reaper lives inside _detach_managed_epg, has exactly one call
        site, and is always scoped to the default source -- so rows this slice
        creates on a profile source would otherwise be unreapable by construction.
        Live evidence that the gap is real: the DEFAULT source already carries 14
        orphaned DAZN-named rows today.

        The UUID-shaped tvg_id filter spares each source's own representative row.
        """
        from apps.epg.models import EPGData
        try:
            referenced = set(Channel.objects.filter(epg_data__epg_source=source)
                             .values_list("epg_data_id", flat=True))
            orphans = (EPGData.objects.filter(epg_source=source)
                       .exclude(id__in=referenced)
                       .filter(tvg_id__regex=r'^[0-9a-fA-F-]{36}$'))
            count = orphans.count()
            if count:
                orphans.delete()
                logger.info(f"{LOG_PREFIX} Reaped {count} orphaned EPGData row(s) "
                            f"from {source.name!r}")
            return count
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} Orphan reap failed for {source.name!r}: {exc}")
            return 0

    def _managed_props_for_profile(self, profile, settings):
        """EPGSource.custom_properties payload for one non-default profile.

        profile.timezone is the FIRST argument to resolve_output_timezone (the
        SOURCE zone). Both parameters are plain strings, so transposing them raises
        nothing and renders every time wrong.

        This overwrites the profile's frozen title templates with a clock-computed
        equivalent, so a stored template self-corrects at the CDT/CST boundary. An
        adopted source's templates and its `managed_by` WILL be rewritten -- a
        deliberate correction, not drift.
        """
        props = dict(ecm_profiles.profile_props(profile))
        props["managed_by"] = "event-channel-managarr"
        props.update(ecm_profiles.resolve_output_timezone(
            profile.timezone,
            self._get_system_timezone(settings),
            settings.get("date_format", "Auto")))
        return props

    def _ensure_profile_source(self, profile, settings, logger):
        """Get or create the dummy EPGSource for ONE non-default profile.

        Called only when a channel actually claims this profile, so a source is
        never created speculatively. Returns None on failure -- the caller then
        leaves those channels alone, which is the pre-S2 behavior.
        """
        from apps.epg.models import EPGSource

        desired = self._managed_props_for_profile(profile, settings)
        try:
            source, created = EPGSource.objects.get_or_create(
                name=profile.source_name, source_type="dummy",
                defaults={"custom_properties": desired, "is_active": True,
                          "refresh_interval": 0})
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} Could not get/create EPG source "
                           f"{profile.source_name!r}: {exc}")
            return None

        if created:
            logger.info(f"{LOG_PREFIX} Created EPG source {profile.source_name!r}")
            return source

        # Refresh non-pattern keys only. Pattern keys are left alone: this slice
        # does not own the user-customization question for an adopted source, and
        # overwriting a UI-edited pattern is the issue-21 regression.
        current = dict(source.custom_properties or {})
        changed = False
        for key, value in desired.items():
            if key in ("title_pattern", "time_pattern", "date_pattern"):
                continue
            if current.get(key) != value:
                current[key] = value
                changed = True
        if changed:
            source.custom_properties = current
            source.save(update_fields=["custom_properties"])
            logger.info(f"{LOG_PREFIX} Refreshed EPG source {profile.source_name!r}")
        return source
```

- [ ] **Step 6: Run and commit**

Run: `python -m pytest tests/ -q` — all pass, including the five frozen-body pins.

```
feat: reroutability guard, orphan reaper, and lazy source provisioning

_epg_binding_is_reroutable is the fix for the review's most serious finding: an
earlier draft claimed channels purely by NAME regex and moved them unconditionally.
"Next:" and "(GMT)" are standard EPG conventions, not DAZN-specific, so on an
install with a legitimately populated UK/EU feed named that way the first applied
pass would have destroyed a working guide. This mirrors the guard
_managed_override_ids already applies (bug-043): move only off nothing, off a
dummy, or off a real source with no programme in the next 24h.

_reap_orphaned_epg_data closes a gap that was architecturally unfixable before:
the existing reaper has one call site, always scoped to the default source, so
rows created on a profile source could never be cleaned. The default source
already carries 14 orphaned DAZN-named rows today, proving the leak is real.

The contract tests hash-pin the BODIES of the five methods this slice must not
touch. Signature pinning was not enough -- bug-045 could be reintroduced inside
_detach_managed_epg with its five arguments unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 4: The reroute step, called from BOTH exits

**Files:** Modify `Event-Channel-Managarr/plugin.py`, `tests/contract/test_s2_wiring.py`

**Interfaces produced:** `_reroute_claimed_channels(self, settings, logger, dry_run, enabled_channel_ids) -> list[int]`

**[REV1 WAS WRONG]** `_run_managed_epg_pass` has TWO returns. A single call site before the final one is unreachable during a Dry Run. And folding the result into `attached_ids` corrupts the CSV/notification audit trail.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_s2_wiring.py`:

```python
def _calls(fn_name, method):
    fn = _fn(fn_name)
    return [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == method]


def test_reroute_is_called_from_BOTH_exits():
    """_run_managed_epg_pass returns twice -- the dry-run branch exits early. A
    single call site before the final return is unreachable during a Dry Run, so
    the preview would never report a reroute the applied run performs."""
    fn = _fn("_run_managed_epg_pass")
    returns = sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Return))
    calls = sorted(_calls("_run_managed_epg_pass", "_reroute_claimed_channels"))
    assert len(returns) >= 2, "expected an early dry-run return plus a final return"
    assert len(calls) >= 2, "reroute must be called on BOTH exit paths"
    assert any(c < returns[0] for c in calls), "no reroute call before the early return"
    assert any(c > returns[0] for c in calls), "no reroute call on the applied path"


def test_reroute_result_is_not_folded_into_attached_ids():
    """attached_ids feeds the CSV header, the per-row managed_epg_assigned flag and
    the scan notification. Merging reroutes into it makes a channel that merely
    MOVED indistinguishable from one that had no EPG at all."""
    fn = _fn("_run_managed_epg_pass")
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "attached_ids" for t in node.targets):
            seg = ast.dump(node.value)
            assert "rerouted" not in seg, "rerouted ids must not be merged into attached_ids"


def test_reroute_consults_the_reroutability_guard():
    src = ast.get_source_segment(_source(), _fn("_reroute_claimed_channels"))
    assert "_epg_binding_is_reroutable" in src, \
        "reroute must never move a channel off a populated real EPG"


def test_reroute_reaps_orphans_on_sources_it_touched():
    src = ast.get_source_segment(_source(), _fn("_reroute_claimed_channels"))
    assert "_reap_orphaned_epg_data" in src
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** — insert below `_ensure_profile_source`:

```python
    def _reroute_claimed_channels(self, settings, logger, dry_run, enabled_channel_ids):
        """Move claimed, safe-to-move channels onto their profile's own EPGSource.

        Runs at the end of BOTH of _run_managed_epg_pass's exits, so a Dry Run
        previews exactly what an applied run will do.

        Why this ends the reclaim: when ECM hides an event-less slot,
        auto_set_dummy_epg_on_hide nulls its epg_data; the next pass's NULL-only
        attach binds it to the DEFAULT source; this step then moves it to the
        profile its name claims -- in the same synchronous pass.

        Safety:
          - only names in claimed_targets() are considered; unclaimed and
            default-family names are absent from that mapping entirely
          - _epg_binding_is_reroutable vetoes any channel holding a populated real
            EPG, so a name collision cannot destroy a working guide
          - it never detaches; a channel is only ever re-pointed
          - an uncreatable profile source leaves those channels where they are

        Returns the ids moved, or under dry_run the ids that WOULD move.
        """
        from apps.epg.models import EPGData, EPGSource

        profiles = ecm_profiles.build_profiles(settings)
        if not any(not p.is_default for p in profiles) or not enabled_channel_ids:
            return []

        candidates = list(Channel.objects.filter(id__in=enabled_channel_ids)
                          .select_related("epg_data", "epg_data__epg_source"))
        claims = ecm_profiles.claimed_targets([c.name for c in candidates], profiles)
        if not claims:
            return []

        by_key = {p.key: p for p in profiles}
        moved = []
        for key in sorted(set(claims.values())):
            profile = by_key.get(key)
            if profile is None:
                continue
            group = [c for c in candidates
                     if claims.get(c.name) == key and self._epg_binding_is_reroutable(c)]
            if not group:
                continue

            if dry_run:
                existing = EPGSource.objects.filter(
                    name=profile.source_name, source_type="dummy").first()
                # Sentinel, not None: a never-bound channel has epg_source_id None,
                # and None == None would silently under-report it as "no move".
                target_id = existing.id if existing else object()
                moved.extend(c.id for c in group
                             if getattr(c.epg_data, "epg_source_id", None) != target_id)
                continue

            source = self._ensure_profile_source(profile, settings, logger)
            if source is None:
                logger.warning(f"{LOG_PREFIX} No source for profile {key!r}; "
                               f"leaving {len(group)} channel(s) in place")
                continue

            to_move = [c for c in group
                       if getattr(c.epg_data, "epg_source_id", None) != source.id]
            if not to_move:
                continue

            vacated = {c.epg_data.epg_source for c in to_move
                       if c.epg_data and c.epg_data.epg_source}
            with transaction.atomic():
                for channel in to_move:
                    epg_data, _ = EPGData.objects.get_or_create(
                        tvg_id=str(channel.uuid), epg_source=source,
                        defaults={"name": channel.name})
                    if epg_data.name != channel.name:
                        epg_data.name = channel.name
                        epg_data.save(update_fields=["name"])
                    channel.epg_data = epg_data
                Channel.objects.bulk_update(to_move, ["epg_data"])
            moved.extend(c.id for c in to_move)
            logger.info(f"{LOG_PREFIX} Rerouted {len(to_move)} channel(s) to "
                        f"{profile.source_name!r}")

            # Rows left behind on the source(s) we moved off are orphaned NOW; the
            # existing reaper is scoped to the default source and already ran this
            # pass, so reap them here.
            for vacated_source in vacated | {source}:
                self._reap_orphaned_epg_data(vacated_source, logger)
        return moved
```

- [ ] **Step 4: Add BOTH call sites**

In the dry-run branch, immediately before its `return attached_ids, detached_ids` (plugin.py:2654):
```python
            rerouted_ids = self._reroute_claimed_channels(
                settings, logger, True, enabled_channel_ids if toggle_on else [])
            if rerouted_ids:
                logger.info(f"{LOG_PREFIX} [dry-run] Reroute would move "
                            f"{len(rerouted_ids)} channel(s)")
```

And before the final return (plugin.py:2697):
```python
        rerouted_ids = self._reroute_claimed_channels(
            settings, logger, False, enabled_channel_ids if toggle_on else [])
        if rerouted_ids:
            logger.info(f"{LOG_PREFIX} Reroute moved {len(rerouted_ids)} channel(s)")
```

**Do NOT merge `rerouted_ids` into `attached_ids`.** Both returns keep their existing two-tuple shape, so no downstream consumer changes. The reroute count is reported via the log only — a separate result column is deferred to a later slice rather than altering the established return contract here.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q` — all pass, including the five frozen-body pins.

```
feat: reroute claimed channels, called from both exits

_run_managed_epg_pass returns twice; the dry-run branch exits early. An earlier
draft's single call site sat before the FINAL return, so the "Dry Run" button would
never have reported a reroute an applied run performs -- in a workflow whose whole
safety culture is preview-then-apply, and with every proposed test passing anyway.

rerouted ids are deliberately NOT merged into attached_ids: that value feeds the
CSV header, the per-row managed_epg_assigned flag and the scan notification, and
merging would make a channel that merely MOVED indistinguishable from one that had
no EPG at all. Both returns keep their existing shape.

The dry-run branch uses a sentinel rather than None for "source does not exist
yet", so a never-bound channel is not silently under-reported by None == None.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 5: In-container gate

**Files:** Create `scripts/verify_s2_incontainer.py`, `scripts/s2_targeted_repair.py`

- [ ] **Step 1: Backup (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.backups.tasks import create_backup_task
print(create_backup_task.apply().result)
"
```

- [ ] **Step 2: Write the gate**

```python
# scripts/verify_s2_incontainer.py
"""Prove S2 against LIVE data. The real pass runs inside a rolled-back transaction.

    docker cp Event-Channel-Managarr/ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
    docker cp scripts/verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
    docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"

IMPORTANT: a DB rollback does NOT undo side effects that are not deferred to
commit. Dispatcharr's create_dummy_epg_data signal calls send_websocket_update
SYNCHRONOUSLY inside .save(), so creating a source inside this gate would broadcast
to every connected browser and then roll the row back underneath it. Proof 4
monkeypatches that function and asserts nothing escaped.

EXIT CODE: 0 pass, 1 fail.
"""

import logging
import sys
import traceback

sys.path.insert(0, "/tmp")
import ecm_profiles  # noqa: E402

from django.db import transaction  # noqa: E402
from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGSource  # noqa: E402
from apps.plugins.loader import PluginManager  # noqa: E402
from apps.plugins.models import PluginConfig  # noqa: E402
import apps.epg.signals as epg_signals  # noqa: E402

GROUP_ID = 1915
GMT_SOURCE = "DAZN PPV Dummy (GMT)"
failures = []
log = logging.getLogger("ecm-verify")


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def snapshot():
    return {c.id: (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source
                   else None)
            for c in Channel.objects.filter(channel_group_id=GROUP_ID)
                                    .select_related("epg_data__epg_source")}


def main():
    settings = PluginConfig.objects.get(key="event-channel-managarr").settings or {}
    profiles = ecm_profiles.build_profiles(settings)
    inst = PluginManager.get().get_plugin_instance("event-channel-managarr")
    chans = list(Channel.objects.filter(channel_group_id=GROUP_ID)
                 .select_related("epg_data__epg_source"))
    enabled = list(Channel.objects.filter(
        channel_group_id=GROUP_ID, channelprofilemembership__enabled=True
    ).values_list("id", flat=True).distinct())
    claims = ecm_profiles.claimed_targets([c.name for c in chans], profiles)
    print(f"channels={len(chans)} enabled={len(enabled)} claimed={len(claims)}")

    print("\n(1) the reroutability guard vetoes populated real EPGs")
    protected = [c for c in chans
                 if c.name in claims and not inst._epg_binding_is_reroutable(c)]
    print(f"       claimed but PROTECTED from moving: {len(protected)}")
    for c in protected[:3]:
        print(f"         {c.id} {c.name[:48]} -> {c.epg_data.epg_source.name}")
    check("guard is callable and returns bools",
          all(isinstance(inst._epg_binding_is_reroutable(c), bool) for c in chans[:20]))

    print("\n(2) dry-run reroute writes NOTHING and reports the same set")
    before = snapshot()
    dry_ids = inst._reroute_claimed_channels(settings, log, True, enabled)
    check("dry run wrote nothing", snapshot() == before, "bindings changed")
    print(f"       dry run predicts {len(dry_ids)} move(s)")

    print("\n(3) REAL pass, rolled back")
    ws_calls = []
    original_ws = epg_signals.send_websocket_update
    epg_signals.send_websocket_update = lambda *a, **k: ws_calls.append((a, k))
    try:
        with transaction.atomic():
            att, det = inst._run_managed_epg_pass(
                settings, log, False, enabled, [c.id for c in chans])
            after = snapshot()

            lost = [cid for cid, src in before.items() if src and not after.get(cid)]
            check("NO channel lost its EPG", not lost, f"{len(lost)}: {lost[:5]}")

            moved = {cid: (before[cid], after[cid]) for cid in before
                     if after.get(cid) and before[cid] != after[cid]}
            print(f"       attached={len(att)} detached={len(det)} moved={len(moved)}")
            for cid, (b, a) in list(moved.items())[:8]:
                print(f"         {cid}: {b} -> {a}")

            claimed_ids = {c.id for c in chans if c.name in claims}
            protected_ids = {c.id for c in protected}
            expect_gmt = (claimed_ids & set(enabled)) - protected_ids
            on_gmt = [cid for cid in expect_gmt if after.get(cid) == GMT_SOURCE]
            check("every enabled, unprotected, claimed channel is on the GMT source",
                  len(on_gmt) == len(expect_gmt), f"{len(on_gmt)}/{len(expect_gmt)}")

            check("no protected channel was moved",
                  not (protected_ids & set(moved)), f"{protected_ids & set(moved)}")
            check("no UNCLAIMED channel was moved",
                  not (set(moved) - claimed_ids), f"{list(set(moved) - claimed_ids)[:5]}")
            check("dry run predicted the same set the real pass moved",
                  set(dry_ids) == set(moved) or not moved,
                  f"dry={len(dry_ids)} real={len(moved)}")
            transaction.set_rollback(True)
    except Exception:
        traceback.print_exc()
        failures.append("real pass raised")
    finally:
        epg_signals.send_websocket_update = original_ws

    print("\n(4) no side effect escaped the rollback")
    check("no websocket broadcast escaped", not ws_calls, f"{len(ws_calls)}: {ws_calls[:2]}")
    check("bindings identical to before the run", snapshot() == before)

    print("\n(5) the GMT source's rendering properties are correct")
    gmt = EPGSource.objects.filter(name=GMT_SOURCE, source_type="dummy").first()
    if gmt is None:
        check("GMT source exists", False, "not found")
    else:
        expected = ecm_profiles.resolve_output_timezone(
            "UTC", inst._get_system_timezone(settings),
            settings.get("date_format", "Auto"))
        props = gmt.custom_properties or {}
        check("timezone is UTC", props.get("timezone") == "UTC", repr(props.get("timezone")))
        # Catches a transposed resolve_output_timezone(system, source) call, which
        # raises nothing and renders every time wrong while all binding checks pass.
        check("output_timezone matches the independently-computed value",
              props.get("output_timezone") == expected["output_timezone"],
              f"{props.get('output_timezone')!r} vs {expected['output_timezone']!r}")


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception during verification")

print("\n" + "=" * 70)
print(f"S2_GATE_RESULT={'FAIL' if failures else 'PASS'}")
for f in failures:
    print(f"  - {f}")
print("=" * 70)
sys.exit(1 if failures else 0)
```

- [ ] **Step 3: Write the targeted repair script**

```python
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
```

- [ ] **Step 4: Run the gate against the CURRENT code (baseline)**

```powershell
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
docker cp scripts\verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
$out = docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"
$out
```
Proofs 1-2 will fail against the current code (the methods do not exist yet) — that is expected. **"NO channel lost its EPG", "no websocket broadcast escaped" and "bindings identical" must PASS.** Record the baseline.

- [ ] **Step 5: Commit both scripts.**

---

## Task 6: Deploy — scheduler stopped, `manage_dummy_epg` left ON

**[REV1 WAS WRONG]** rev 1 disarmed by setting `manage_dummy_epg=False`. That is the **teardown** path: `keep_ids=set()`, `detach_scope=None`, a full unscoped detach of all 72 channels on the default source. And the background scheduler thread runs independently of that flag, so a `0400/1000/1100/1200` tick fires it for real. The Step-5 re-arm then re-attaches everything to the same source, so the binding diff reports `LOST EPG = 0` while the guide was dark for the entire window.

- [ ] **Step 1: Backup and snapshot (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.backups.tasks import create_backup_task
print(create_backup_task.apply().result)
"
docker exec -u dispatch dispatcharr python manage.py shell -c "
import json
from apps.channels.models import Channel
rows = {str(c.id): (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source else None)
        for c in Channel.objects.filter(channel_group_id=1915).select_related('epg_data__epg_source')}
open('/tmp/s2_before.json','w').write(json.dumps(rows))
print('snapshot rows:', len(rows))
"
```

- [ ] **Step 2: Disable auto-rescan ONLY (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.plugins.models import PluginConfig
c = PluginConfig.objects.get(key='event-channel-managarr')
s = dict(c.settings or {})
print('ORIGINAL auto_rescan=', s.get('auto_rescan_on_m3u_refresh'),
      'manage_dummy_epg=', s.get('manage_dummy_epg'))
s['auto_rescan_on_m3u_refresh'] = False
c.settings = s; c.save(update_fields=['settings'])
print('auto-rescan disabled; manage_dummy_epg LEFT ON deliberately')
"
```
**Do not touch `manage_dummy_epg`.** Turning it off triggers the full teardown.

- [ ] **Step 3: Deploy, which restarts the container and therefore the scheduler thread (POWERSHELL)**

```powershell
docker cp Event-Channel-Managarr\plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/data/plugins/event-channel-managarr/ecm_profiles.py
docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/event-channel-managarr
docker restart dispatcharr
```
Wait for healthy.

- [ ] **Step 4: Stop the scheduler thread (POWERSHELL)**

The thread is independent of every setting flag. Stop it so no tick fires an unsupervised applied pass while you verify.

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.plugins.loader import PluginManager
inst = PluginManager.get().get_plugin_instance('event-channel-managarr')
inst._stop_background_scheduler()
print('scheduler stopped')
"
```

- [ ] **Step 5: Interim snapshot, then run the gate**

```powershell
docker exec -u dispatch dispatcharr python manage.py shell -c "
import json
from apps.channels.models import Channel
rows = {str(c.id): (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source else None)
        for c in Channel.objects.filter(channel_group_id=1915).select_related('epg_data__epg_source')}
open('/tmp/s2_interim.json','w').write(json.dumps(rows))
lost = sum(1 for v in rows.values() if not v)
print('interim: channels with NO epg =', lost)
"
```
Compare against Step 1 — a jump means a tick fired during the window. Then run the Task 5 gate; expect `S2_GATE_RESULT=PASS`.
**If the gate fails, STOP**, restore `auto_rescan_on_m3u_refresh`, restart the container, and report.

- [ ] **Step 6: One supervised DRY RUN, then one applied pass**

Trigger the "👁️ Dry Run" action and read the log for `[dry-run] Reroute would move N channel(s)`. It must be non-zero if and only if channels are misplaced. Then trigger one applied run and diff:

```powershell
docker exec dispatcharr python manage.py shell -c "
import json
from apps.channels.models import Channel
before = json.load(open('/tmp/s2_before.json'))
after = {str(c.id): (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source else None)
         for c in Channel.objects.filter(channel_group_id=1915).select_related('epg_data__epg_source')}
lost = [k for k in before if before[k] and not after.get(k)]
moved = [(k, before[k], after[k]) for k in before if after.get(k) and before[k] != after[k]]
print('LOST EPG (must be 0):', len(lost), lost[:5])
print('MOVED:', len(moved))
for m in moved[:10]: print('  ', m)
"
```
**`LOST EPG` must be 0.** If not: run `scripts/s2_targeted_repair.py` (NOT a full pg_restore), then stop and report.

- [ ] **Step 7: Restart to bring the scheduler back, restore auto-rescan, then the acceptance test**

```powershell
docker restart dispatcharr
docker exec dispatcharr python manage.py shell -c "
from apps.plugins.models import PluginConfig
c = PluginConfig.objects.get(key='event-channel-managarr')
s = dict(c.settings or {}); s['auto_rescan_on_m3u_refresh'] = True
c.settings = s; c.save(update_fields=['settings']); print('auto-rescan restored')
"
```

Then force an M3U refresh and confirm the DAZN channels are STILL on the GMT source rendering local times. **This is the exact moment the manual fix was undone.** If they are reclaimed, report BLOCKED rather than patching.

- [ ] **Step 8: Record the outcome** in `.wolf/memory.md`; update the spec's §6 scorecard to mark mode 2 covered ONLY if Step 7 passed.

---

## Definition of Done

- [ ] `python -m pytest tests/ -q` fully green
- [ ] All five frozen-body hashes match — the existing pass is provably unmodified
- [ ] The gate printed `S2_GATE_RESULT=PASS`, including "no websocket broadcast escaped" and the `output_timezone` read-back
- [ ] The dry run predicted the same set the applied run moved
- [ ] No protected (populated-real-EPG) channel was moved
- [ ] The binding diff shows **`LOST EPG = 0`**
- [ ] After a forced M3U refresh, DAZN channels are still on the GMT source
- [ ] The scheduler is running again and `auto_rescan_on_m3u_refresh` is restored
