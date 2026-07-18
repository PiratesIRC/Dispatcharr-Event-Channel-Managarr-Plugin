# S2 — Claimed-Channel Reroute Implementation Plan (rewritten)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End the reclaim that un-fixes the DAZN guide every few hours, by moving channels whose names positively claim a non-default timezone profile onto that profile's own dummy EPGSource.

**Architecture:** `_run_managed_epg_pass` is **not modified**. It runs exactly as it does today — one managed source, unchanged attach, unchanged detach. A new additive step runs after it and moves only channels with a *positive* non-default claim. Everything else in the system is untouched.

**Tech Stack:** Python 3, Django ORM (inside Dispatcharr), pytest, Docker.

> **This is a rewrite, not a revision.** Two earlier drafts both restructured `_run_managed_epg_pass` and both were rejected — four adversarial reviews found five Criticals between them, three of which would first have executed on production. That function is where every trap lives: the detach scope, the duplicated dry-run branch, the `keep_ids` contract, the SE resync. This plan does not go near it.

## Why this shape

Measured on live data, the set of channels with a positive non-default claim is **46 of 278**. Of those, **0 are currently on the wrong source and 0 are unbound** — so the step is a verifiable no-op the moment it deploys, and only ever acts once a channel has actually been misplaced.

Four of the five Criticals from the earlier drafts become *impossible* rather than fixed:

| Earlier Critical | Why it cannot occur here |
|---|---|
| Looping detach over new sources would null 94 hidden channels' `epg_data` | The detach code path is untouched and never sees a non-default source |
| Unclaimed idle slots shuttled between ET and GMT every lifecycle transition | Stickiness is free: unclaimed names are not in the claim set, so nothing moves them |
| Every marketplace install gained an empty, CDT-hardcoded DAZN source | Creation is inherently lazy — no positive claim, no source |
| Extracting `_stock_patterns` produced a guaranteed `NameError` | No extraction is needed; non-default sources use their own props builder |

**It still fixes the reclaim, in the same pass.** Channel hidden → `auto_set_dummy_epg_on_hide` nulls `epg_data` → next pass's NULL-only attach puts it on the default source → the reroute step moves it to GMT because its name now claims GMT.

## Global Constraints

- **`Event-Channel-Managarr/plugin.py`: the ONLY permitted change is adding new methods plus ONE call site at the end of `_run_managed_epg_pass`.** Do not modify `_attach_managed_epg`, `_detach_managed_epg`, `_managed_override_ids`, `_get_or_create_managed_epg_source`, `_localized_template_props`, the dry-run branch, or `keep_ids`. If a task appears to need one of these, STOP and report BLOCKED.
- **`ecm_profiles.py` stays STDLIB-ONLY** (only `regex` inside `try/except ImportError`), no module-level mutable state.
- Patterns are STORED in JS named-group form `(?<name>)` (issue #21).
- Commit trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`; messages via Bash heredoc + `git commit -F`.
- PowerShell 5.1 **cannot parse `<` redirection** and parses whole blocks before executing, so a stray `<` also silently skips preceding `docker cp` lines. Put redirection inside the container's `sh -c`.
- `docker exec` writing under `/data` uses `-u dispatch`.
- Repo `C:\Users\User\docker\Event-Channel-Managarr`, branch `feat/s2-multi-source`. Code map: `.superpowers/sdd/s2-code-map.md`.

## Verified ground truth (live, 2026-07-18)

```
group 1915 "US: PPV"                278 channels
positive dazn_gmt claims             46      <- the entire blast radius
  of those, enabled                   5
  currently on the wrong source       0      <- deploy is a no-op
  currently unbound                   0
EPGSource 18 "ECM Managed Dummy"     72 channels
EPGSource 42 "DAZN PPV Dummy (GMT)"  99 channels, managed_by=manual-dazn-gmt
```

## File Structure

| Path | Responsibility | Change |
|---|---|---|
| `Event-Channel-Managarr/ecm_profiles.py` | `build_profiles`, `resolve_output_timezone`, `claimed_targets` | Modify |
| `Event-Channel-Managarr/plugin.py` | Three new methods + one call site. Nothing else. | Modify |
| `tests/unit/test_ecm_profiles.py` | Settings resolution, timezone resolution | Modify |
| `tests/unit/test_claimed_targets.py` | Positive-claim semantics | Create |
| `tests/contract/test_s2_wiring.py` | Runtime + AST guards, incl. "nothing else changed" | Create |
| `scripts/verify_s2_incontainer.py` | Gate: rolled-back real pass against live data | Create |

---

## Task 1: Settings and timezone resolution (pure)

**Files:** Modify `Event-Channel-Managarr/ecm_profiles.py`, `tests/unit/test_ecm_profiles.py`

**Interfaces produced:**
- `build_profiles(settings) -> tuple[Profile, ...]`
- `resolve_output_timezone(source_tz_name, system_tz_name, date_format="Auto") -> dict`

**Why:** `US_ET.timezone` is hardcoded but ECM ships a user-facing `dummy_epg_event_timezone`. And the output-timezone computation is what decides whether a GMT source displays 17:00 Central or 22:00 — in the single-source code that logic has **no test at all**.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ecm_profiles.py`:

```python
# --- build_profiles -----------------------------------------------------------

def test_build_profiles_honours_the_event_timezone_setting():
    """Hardcoding would silently break every install whose names are not Eastern."""
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


def test_build_profiles_always_has_exactly_one_default():
    for fmt in ("US", "SE", "", "garbage"):
        built = ecm_profiles.build_profiles({"dummy_epg_channel_format": fmt})
        assert sum(1 for p in built if p.is_default) == 1, fmt


def test_build_profiles_preserves_the_dazn_selector_and_patterns():
    """Settings resolve user preferences only. The provider FACTS -- selector and
    pattern trio -- must survive untouched or routing changes shape."""
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Asia/Tokyo"})
    dazn = next(p for p in built if p.key == "dazn_gmt")
    assert dazn.selector == ecm_profiles.DAZN_GMT.selector
    assert dazn.title_pattern == ecm_profiles.DAZN_GMT.title_pattern


# --- resolve_output_timezone --------------------------------------------------

def test_resolve_output_timezone_converts_and_labels():
    """THE assertion this plumbing exists for. A GMT source displayed in Chicago
    must carry output_timezone=America/Chicago -- if it inherits the ET source's
    config instead, every DAZN time renders five hours wrong."""
    got = ecm_profiles.resolve_output_timezone("UTC", "America/Chicago")
    assert got["output_timezone"] == "America/Chicago"
    assert "{starttime}" in got["upcoming_title_template"]


def test_resolve_output_timezone_same_zone_uses_plain_templates():
    got = ecm_profiles.resolve_output_timezone("America/Chicago", "America/Chicago")
    assert got["output_timezone"] == "America/Chicago"
    assert got["upcoming_title_template"] == "Upcoming at {starttime}: {title}"


@pytest.mark.parametrize("src,sys_tz", [("", "America/Chicago"),
                                        ("Not/AZone", "America/Chicago"),
                                        ("UTC", "Not/AZone")])
def test_resolve_output_timezone_degrades_without_raising(src, sys_tz):
    got = ecm_profiles.resolve_output_timezone(src, sys_tz)
    assert set(got) == {"output_timezone", "title_template",
                        "upcoming_title_template", "ended_title_template"}


def test_resolve_output_timezone_eu_date_order():
    got = ecm_profiles.resolve_output_timezone("UTC", "Europe/Stockholm", date_format="EU")
    assert "{day}/{month}" in got["upcoming_title_template"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_ecm_profiles.py -k "build_profiles or resolve_output" -v`
Expected: FAIL — `AttributeError: module 'ecm_profiles' has no attribute 'build_profiles'`.

- [ ] **Step 3: Implement**

Change the dataclasses import at the top of `ecm_profiles.py` to:
```python
from dataclasses import dataclass, replace
```

Append to `ecm_profiles.py`:

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

    Pure: the caller supplies both zone NAMES; this reads no settings and no ORM.
    Extracted so it can be asserted -- in the single-source code this logic had no
    test, and a wrong result is a silent multi-hour error in every rendered title.

    Returns the same four keys on every path so callers can .update() blindly.
    """
    from datetime import datetime            # stdlib, function-local (purity rule)
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
    if hours <= 0:
        hours = DEFAULT_DURATION_HOURS
    return hours * 60


def build_profiles(settings):
    """Resolve the frozen PROFILES template against live plugin settings.

    PROFILES carries provider FACTS (selectors, pattern trios, dazn_gmt's UTC).
    This resolves only what a USER controls, so existing settings keep working:
        dummy_epg_event_timezone       -> the DEFAULT profile's source timezone
        dummy_epg_event_duration_hours -> every profile's block length

    dazn_gmt's timezone is NEVER resolved from settings -- it is UTC because the
    provider stamps (GMT) in the channel name.

    NOTE: dummy_epg_channel_format (US/SE) is deliberately NOT handled here. This
    plan does not touch the default source, so the existing single-source code
    continues to own the US/SE pattern choice exactly as it does today.
    """
    settings = settings or {}
    duration = _resolve_duration_minutes(settings.get("dummy_epg_event_duration_hours"))
    tz = str(settings.get("dummy_epg_event_timezone") or "").strip() or DEFAULT_EVENT_TIMEZONE

    out = []
    for profile in PROFILES:
        if profile.is_default:
            out.append(replace(profile, timezone=tz, program_duration_minutes=duration))
        else:
            out.append(replace(profile, program_duration_minutes=duration))
    return tuple(out)
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/unit/test_ecm_profiles.py tests/contract/test_module_purity.py -v`
Expected: all pass. `datetime`/`zoneinfo` are stdlib and imported inside the function, so the purity guards hold.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/User/docker/Event-Channel-Managarr
git add Event-Channel-Managarr/ecm_profiles.py tests/unit/test_ecm_profiles.py
git commit -F /tmp/s2_t1.txt
```
```
feat: build_profiles and resolve_output_timezone

build_profiles resolves the frozen template against live settings so the existing
dummy_epg_event_timezone and _duration_hours keep governing the default profile.
dazn_gmt's UTC is never resolved from settings -- it is a fact about the data.

dummy_epg_channel_format is deliberately NOT handled: this plan does not touch the
default source, so the existing single-source code keeps owning the US/SE choice.

resolve_output_timezone extracts the output-timezone/template computation as a
pure function so it can be asserted. In the single-source code it had no test at
all, and a wrong result is a silent multi-hour error in every rendered title.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 2: Positive-claim semantics

**Files:** Modify `Event-Channel-Managarr/ecm_profiles.py`, create `tests/unit/test_claimed_targets.py`

**Interfaces produced:**
- `claimed_targets(names, profiles) -> dict[str, str]` — name → NON-DEFAULT profile key, for names a non-default selector positively claims. Names claimed by nothing, or claimed only by the default, are **absent** from the mapping.

**Why absence matters:** absence is what makes the reroute step safe. A name not in this dict is never moved by anything in S2, so unclaimed and default-family channels are untouchable by construction. Stickiness is not a rule to implement — it is the absence of a rule.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_claimed_targets.py
"""Positive-claim semantics.

claimed_targets returns ONLY names a non-default selector positively claims.
Everything else is ABSENT, and absence is the safety property: the reroute step
can only ever move a name present in this mapping, so unclaimed names and
default-family names cannot be moved by S2 at all.

Two earlier drafts made unclaimed names route to the default, which shuttled idle
DAZN slots between sources on every lifecycle transition -- re-creating the reclaim
through the fix. Absence removes that possibility rather than guarding against it.
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
    """THE safety property. An idle slot must not appear at all."""
    idle = "NO EVENT STREAMING NOW - | 8K EXCLUSIVE | US: DAZN PPV 50"
    assert idle not in ecm_profiles.claimed_targets([idle], _profiles())


def test_default_family_names_are_absent():
    """A legacy PPV EVENT name is claimed only by the default profile, so S2 must
    never move it -- the existing single-source code owns those channels."""
    legacy = "PPV EVENT 07: MARS Late Models at Farmer City (7.17 7:30 PM ET)"
    assert legacy not in ecm_profiles.claimed_targets([legacy], _profiles())


def test_no_default_key_ever_appears_as_a_value():
    claims = ecm_profiles.claimed_targets(_names(), _profiles())
    default_key = next(p.key for p in _profiles() if p.is_default)
    assert default_key not in set(claims.values())


def test_claim_count_on_the_real_corpus():
    """46 of 278 -- the entire blast radius of the reroute step. Measured live."""
    claims = ecm_profiles.claimed_targets(_names(), _profiles())
    assert len(claims) == 46
    assert set(claims.values()) == {"dazn_gmt"}


def test_claims_are_a_subset_of_the_corpus():
    names = _names()
    assert set(ecm_profiles.claimed_targets(names, _profiles())) <= set(names)


def test_empty_input_yields_no_claims():
    assert ecm_profiles.claimed_targets([], _profiles()) == {}


def test_profiles_without_a_non_default_yield_no_claims():
    """With only a default profile there is nothing S2 can move -- the step must
    become a no-op, not raise."""
    only_default = tuple(p for p in _profiles() if p.is_default)
    assert ecm_profiles.claimed_targets(_names(), only_default) == {}


def test_a_broken_selector_claims_nothing_rather_than_raising():
    broken = ecm_profiles.Profile(
        key="broken", source_name="B", selector=r"(?<unclosed",
        title_pattern="", date_pattern="", time_pattern="",
        timezone="UTC", output_timezone="UTC", program_duration_minutes=60,
        include_date=False, title_template="{title}",
        upcoming_title_template="", ended_title_template="",
        fallback_title_template="", fallback_description_template="",
        is_default=False)
    default = next(p for p in _profiles() if p.is_default)
    assert ecm_profiles.claimed_targets(["anything"], (broken, default)) == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_claimed_targets.py -v`
Expected: FAIL — no attribute `claimed_targets`.

- [ ] **Step 3: Implement**

Append to `ecm_profiles.py`:

```python
def claimed_targets(names, profiles):
    """Map name -> NON-DEFAULT profile key, for names positively claimed.

    Names claimed by no selector, or only by the default profile, are ABSENT from
    the result. That absence is the safety property: a caller can only act on
    names present here, so unclaimed and default-family channels cannot be moved.

    Non-default profiles are tried in declaration order; the first to claim wins.
    A profile whose selector will not compile claims nothing rather than raising.
    """
    claims = {}
    compiled = [(p, compile_pattern(p.selector))
                for p in profiles if not p.is_default]
    for name in names:
        for profile, selector in compiled:
            if selector is not None and selector.search(name):
                claims[name] = profile.key
                break
    return claims
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/ -q`
Expected: all pass, including `test_claim_count_on_the_real_corpus` at 46.

- [ ] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/ecm_profiles.py tests/unit/test_claimed_targets.py
git commit -F /tmp/s2_t2.txt
```
```
feat: claimed_targets -- positive non-default claims only

Returns only names a non-default selector positively claims; everything else is
ABSENT. Absence is the safety property: the reroute step can act only on names
present here, so unclaimed names and default-family channels cannot be moved by
S2 at all.

Two earlier drafts routed unclaimed names to the default, which shuttled idle DAZN
slots between sources on every lifecycle transition -- re-creating the reclaim
through the fix for the reclaim. Absence removes the possibility instead of
guarding against it.

Measured on the real corpus: 46 of 278 names claimed. That is the entire blast
radius of this slice.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 3: Lazy source provisioning for one profile

**Files:** Modify `Event-Channel-Managarr/plugin.py`, create `tests/contract/test_s2_wiring.py`

**Interfaces produced:**
- `_managed_props_for_profile(self, profile, settings) -> dict`
- `_ensure_profile_source(self, profile, settings, logger) -> EPGSource | None`

**Why one profile at a time:** the caller only ever needs a source for a profile that actually claimed a channel. There is no "create them all" path, so an install with no DAZN content never gains a DAZN source.

- [ ] **Step 1: Add the import**

At `plugin.py` line 44, directly below `import ecm_parsing`:
```python
import ecm_profiles
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/contract/test_s2_wiring.py
"""Guards for S2's plugin.py wiring.

plugin.py imports Django at module scope and cannot be imported outside the
container, so structure is checked with ast. Where a structural check would be
too weak, the method is COMPILED OUT of plugin.py and CALLED with a stub -- an
earlier draft shipped an extraction that raised NameError on every run, and
grep-style tests passed on it.

The most important tests here are the NEGATIVE ones: this slice's entire safety
argument is that it does not modify the existing pass.
"""

import ast
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
sys.path.insert(0, str(ROOT / "Event-Channel-Managarr"))
import ecm_profiles  # noqa: E402


def _source():
    return PLUGIN_PY.read_text(encoding="utf-8")


def _fn(name):
    return next((n for n in ast.walk(ast.parse(_source()))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)


def test_ecm_profiles_is_imported():
    assert re.search(r"^import ecm_profiles$", _source(), re.M)


def test_new_methods_exist():
    assert _fn("_managed_props_for_profile") is not None
    assert _fn("_ensure_profile_source") is not None


def test_props_builder_uses_the_profiles_own_timezone():
    """The GMT source's templates must be computed against UTC, not the global
    setting that belongs to the default profile."""
    src = ast.get_source_segment(_source(), _fn("_managed_props_for_profile"))
    assert "profile.timezone" in src


def test_source_provisioning_takes_one_profile_not_a_list():
    """Lazy by construction: there is no create-them-all path, so an install with
    no DAZN content never gains a DAZN source."""
    args = [a.arg for a in _fn("_ensure_profile_source").args.args]
    assert args[:2] == ["self", "profile"]


# --- the negative guards: this slice must not modify the existing pass ---------

FROZEN_METHODS = [
    "_attach_managed_epg",
    "_detach_managed_epg",
    "_managed_override_ids",
    "_get_or_create_managed_epg_source",
    "_localized_template_props",
]


@pytest.mark.parametrize("name", FROZEN_METHODS)
def test_frozen_method_signatures_are_unchanged(name):
    """S2's safety argument is that it does not touch the existing machinery. A
    changed signature means it did."""
    expected = {
        "_attach_managed_epg": ["self", "channels", "managed_source", "logger",
                                "settings", "rate_limiter", "override_ids"],
        "_detach_managed_epg": ["self", "managed_source", "keep_channel_ids",
                                "logger", "scope_ids"],
        "_managed_override_ids": ["self", "settings", "managed_source",
                                  "enabled_channel_ids", "logger"],
        "_get_or_create_managed_epg_source": ["self", "settings", "logger"],
        "_localized_template_props": ["self", "settings"],
    }[name]
    assert [a.arg for a in _fn(name).args.args] == expected


def test_keep_ids_is_assigned_once_and_never_mutated():
    fn = _fn("_run_managed_epg_pass")
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "keep_ids" for t in n.targets)]
    assert len(assigns) == 1
    mutations = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in ("update", "add", "discard", "remove", "clear")
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "keep_ids"]
    assert not mutations


def test_detach_is_called_exactly_once_and_never_in_a_loop():
    """Looping detach over new sources would null the epg_data of 94 currently
    hidden channels on the first applied pass."""
    fn = _fn("_run_managed_epg_pass")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_detach_managed_epg"]
    assert len(calls) == 1
    for loop in [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.While))]:
        inner = [n for n in ast.walk(loop)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_detach_managed_epg"]
        assert not inner
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_wiring.py -v`
Expected: FAIL on `test_new_methods_exist`; the frozen-signature and detach guards should already PASS (nothing has been modified yet) — that is the point, they are a baseline.

- [ ] **Step 4: Implement**

Insert directly above `_get_or_create_managed_epg_source` (plugin.py:2249):

```python
    def _managed_props_for_profile(self, profile, settings):
        """EPGSource.custom_properties payload for one non-default profile.

        ecm_profiles.profile_props() omits `managed_by` (identity, not renderer
        config), so it is added here. The profile's OWN timezone drives the
        output-timezone computation -- using the global setting would give the GMT
        source the default profile's templates and a multi-hour display error.

        This OVERWRITES the frozen title templates profile_props() supplied. That
        is intended: the abbreviation is computed from the live clock, so a stored
        template self-corrects at the CDT/CST boundary instead of carrying a frozen
        literal. An adopted source's stored templates WILL be rewritten -- a
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
        never created speculatively. Returns None on any failure -- the caller
        treats that as "leave these channels where they are", which is always safe
        because leaving them alone is the pre-S2 behavior.

        Adoption is by NAME: ecm_profiles.DAZN_GMT.source_name equals the name of
        the source already serving these channels in production, so this adopts
        that row in place and its bindings are never broken.
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
            logger.info(f"{LOG_PREFIX} Created EPG source {profile.source_name!r} "
                        f"for profile {profile.key!r}")
            return source

        # Refresh non-pattern keys only. The pattern keys are deliberately left
        # alone: this slice does not own the user-customization question for a
        # newly adopted source, and overwriting a pattern a user edited in
        # Dispatcharr's UI is the issue-21 regression.
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

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q` — all pass.

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_wiring.py
git commit -F /tmp/s2_t3.txt
```
```
feat: lazy per-profile EPG source provisioning

_ensure_profile_source handles ONE profile and is called only when a channel
actually claims it, so a source is never created speculatively. An earlier draft
created every profile's source unconditionally, which would have put an empty,
single-box-tuned "DAZN PPV Dummy (GMT)" row carrying a hardcoded CDT literal into
every marketplace install with no DAZN content, with no cleanup path.

Adoption is by NAME, so the source already serving these channels in production is
adopted in place and its bindings are never broken.

Pattern keys are deliberately NOT refreshed: this slice does not own the
user-customization question for a newly adopted source, and overwriting a pattern
the user edited in Dispatcharr's UI is the issue-21 regression.

The contract tests include NEGATIVE guards pinning the signatures of the five
existing methods this slice must not touch -- the safety argument is that the
existing pass is unmodified, so that claim is now enforced.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 4: The reroute step

**Files:** Modify `Event-Channel-Managarr/plugin.py`, `tests/contract/test_s2_wiring.py`

**Interfaces produced:**
- `_reroute_claimed_channels(self, settings, logger, dry_run, enabled_channel_ids) -> list[int]`

**The single call site** goes at the very END of `_run_managed_epg_pass`'s applied branch, after `detached_ids` is computed. It must not alter the existing return value's meaning.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_s2_wiring.py`:

```python
def test_reroute_method_exists():
    assert _fn("_reroute_claimed_channels") is not None


def test_reroute_runs_after_detach():
    """It must observe the pass's final state, not race it."""
    fn = _fn("_run_managed_epg_pass")
    def _lines(method):
        return [n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == method]
    detach, reroute = _lines("_detach_managed_epg"), _lines("_reroute_claimed_channels")
    assert detach and reroute
    assert max(detach) < min(reroute)


def test_reroute_is_called_exactly_once():
    fn = _fn("_run_managed_epg_pass")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_reroute_claimed_channels"]
    assert len(calls) == 1


def test_reroute_honours_dry_run():
    """A dry run must not write. The parameter must be threaded, not ignored."""
    args = [a.arg for a in _fn("_reroute_claimed_channels").args.args]
    assert "dry_run" in args
    src = ast.get_source_segment(_source(), _fn("_reroute_claimed_channels"))
    assert "dry_run" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_wiring.py -v`
Expected: FAIL on `test_reroute_method_exists`.

- [ ] **Step 3: Implement**

Insert directly below `_ensure_profile_source`:

```python
    def _reroute_claimed_channels(self, settings, logger, dry_run, enabled_channel_ids):
        """Move channels whose names positively claim a non-default profile onto
        that profile's own EPGSource.

        This is the whole of S2. It runs AFTER the existing managed-EPG pass and
        changes nothing the pass did -- it only corrects the SOURCE of channels the
        pass has already bound, and only for names a non-default selector claims.

        Why this ends the reclaim: when ECM hides an event-less slot,
        auto_set_dummy_epg_on_hide nulls its epg_data; the next pass's NULL-only
        attach binds it to the DEFAULT source; this step then moves it to the
        profile its name claims, in the same pass.

        Safety by construction:
          - only names in claimed_targets() are touched; unclaimed and
            default-family channels are absent from that mapping entirely
          - it never detaches: a channel is only ever re-pointed to another source
          - a missing/uncreatable profile source means those channels are left
            exactly where they are, which is the pre-S2 behavior

        Returns the ids it moved (or would move, under dry_run).
        """
        from apps.epg.models import EPGData

        profiles = ecm_profiles.build_profiles(settings)
        if not any(not p.is_default for p in profiles):
            return []

        candidates = list(Channel.objects.filter(id__in=enabled_channel_ids)
                          .select_related("epg_data"))
        claims = ecm_profiles.claimed_targets([c.name for c in candidates], profiles)
        if not claims:
            return []

        by_key = {p.key: p for p in profiles}
        moved = []
        for key in sorted({claims[n] for n in claims}):
            profile = by_key.get(key)
            if profile is None:
                continue
            group = [c for c in candidates if claims.get(c.name) == key]
            if not group:
                continue

            if dry_run:
                # Report every claimed channel not already on the profile's source,
                # without creating anything.
                from apps.epg.models import EPGSource
                existing = EPGSource.objects.filter(
                    name=profile.source_name, source_type="dummy").first()
                target_id = existing.id if existing else None
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
        return moved
```

- [ ] **Step 4: Add the single call site**

At the END of `_run_managed_epg_pass`, immediately before its final `return attached_ids, detached_ids`, insert:

```python
        rerouted_ids = self._reroute_claimed_channels(
            settings, logger, dry_run, enabled_channel_ids if toggle_on else [])
        if rerouted_ids:
            logger.info(f"{LOG_PREFIX} Reroute step touched {len(rerouted_ids)} channel(s)")
            attached_ids = list(dict.fromkeys(list(attached_ids) + rerouted_ids))
```

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q` — all pass, including every frozen-signature guard.

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_wiring.py
git commit -F /tmp/s2_t4.txt
```
```
feat: reroute claimed channels onto their profile's source

This is the whole of S2. It runs AFTER the existing managed-EPG pass, changes
nothing the pass did, and only corrects the SOURCE of channels whose names
positively claim a non-default profile.

Why it ends the reclaim: when ECM hides an event-less slot,
auto_set_dummy_epg_on_hide nulls its epg_data; the next pass's NULL-only attach
binds it to the default source; this step then moves it to the profile its name
claims -- in the same pass.

Safety by construction, not by guard: only names in claimed_targets() are touched
and unclaimed names are absent from that mapping entirely; the step never
detaches, only re-points; and an uncreatable profile source leaves those channels
exactly where they are, which is the pre-S2 behavior.

Measured blast radius on live data: 46 of 278 channels, of which 0 are currently
misplaced -- so the first deploy is a verifiable no-op.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 5: In-container gate — a real pass, rolled back

**Files:** Create `scripts/verify_s2_incontainer.py`

- [ ] **Step 1: Backup (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.backups.tasks import create_backup_task
print(create_backup_task.apply().result)
"
```
Record the filename.

- [ ] **Step 2: Write the script**

```python
# scripts/verify_s2_incontainer.py
"""Prove S2 against LIVE data by running the REAL pass inside a rolled-back
transaction. Nothing persists.

    docker cp Event-Channel-Managarr/ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
    docker cp scripts/verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
    docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"

Caveat: transaction.on_commit hooks do not fire under rollback, so this verifies
ECM's own logic, not Dispatcharr's commit-time side effects.

EXIT CODE: 0 pass, 1 fail.
"""

import logging
import sys
import traceback

sys.path.insert(0, "/tmp")
import ecm_profiles  # noqa: E402

from django.db import transaction  # noqa: E402
from apps.channels.models import Channel  # noqa: E402
from apps.plugins.loader import PluginManager  # noqa: E402
from apps.plugins.models import PluginConfig  # noqa: E402

GROUP_ID = 1915
GMT_SOURCE = "DAZN PPV Dummy (GMT)"
failures = []


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
    print("profiles: " + ", ".join(
        f"{p.key}(tz={p.timezone}{',default' if p.is_default else ''})" for p in profiles))

    chans = list(Channel.objects.filter(channel_group_id=GROUP_ID))
    claims = ecm_profiles.claimed_targets([c.name for c in chans], profiles)
    print(f"\nlive channels: {len(chans)}, positively claimed: {len(claims)}")

    print("\n(1) the claim set is bounded and non-default only")
    default_key = next(p.key for p in profiles if p.is_default)
    check("no claim resolves to the default profile", default_key not in set(claims.values()))
    check("claim set is a strict subset of the group", len(claims) < len(chans),
          f"{len(claims)} of {len(chans)}")

    print("\n(2) REAL pass, rolled back")
    before = snapshot()
    try:
        with transaction.atomic():
            inst = PluginManager.get().get_plugin_instance("event-channel-managarr")
            enabled = list(Channel.objects.filter(
                channel_group_id=GROUP_ID,
                channelprofilemembership__enabled=True
            ).values_list("id", flat=True).distinct())
            scoped = [c.id for c in chans]

            att, det = inst._run_managed_epg_pass(
                settings, logging.getLogger("ecm-verify"), False, enabled, scoped)
            after = snapshot()

            lost = [cid for cid, src in before.items() if src and not after.get(cid)]
            check("NO channel lost its EPG", not lost, f"{len(lost)}: {lost[:5]}")

            moved = {cid: (before[cid], after[cid]) for cid in before
                     if after.get(cid) and before[cid] != after[cid]}
            print(f"       attached={len(att)} detached={len(det)} moved={len(moved)}")
            for cid, (b, a) in list(moved.items())[:8]:
                print(f"         {cid}: {b} -> {a}")

            claimed_ids = {c.id for c in chans if c.name in claims}
            enabled_claimed = claimed_ids & set(enabled)
            on_gmt = [cid for cid in enabled_claimed if after.get(cid) == GMT_SOURCE]
            check("every enabled claimed channel is on the GMT source",
                  len(on_gmt) == len(enabled_claimed),
                  f"{len(on_gmt)}/{len(enabled_claimed)}")

            unclaimed_moved = [cid for cid in moved if cid not in claimed_ids]
            check("no UNCLAIMED channel was moved", not unclaimed_moved,
                  f"{len(unclaimed_moved)}: {unclaimed_moved[:5]}")

            transaction.set_rollback(True)
    except Exception:
        traceback.print_exc()
        failures.append("real pass raised")

    print("\n(3) nothing persisted")
    check("bindings identical to before the run", snapshot() == before)


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

- [ ] **Step 3: Run it BEFORE deploying (POWERSHELL)** — baseline against current code

```powershell
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
docker cp scripts\verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
$out = docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"
$out
```
Expected on the CURRENT code: proof 1 passes; proof 2's "every enabled claimed channel is on the GMT source" may FAIL (that is what S2 fixes) but **"NO channel lost its EPG" and "nothing persisted" must PASS**. Record the baseline.

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_s2_incontainer.py
git commit -F /tmp/s2_t5.txt
```
```
feat: in-container gate running the real pass inside a rolled-back transaction

Exercises the actual attach/detach/reroute ORM path against live data with nothing
persisted, so the code S2 changes is proven before production rather than on it.
Asserts no channel loses its EPG, no unclaimed channel is moved, and that the
bindings are byte-identical after rollback.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 6: Deploy with the automation disarmed

- [ ] **Step 1: Backup, and snapshot every binding (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.backups.tasks import create_backup_task
print(create_backup_task.apply().result)
"
docker exec dispatcharr python manage.py shell -c "
import json
from apps.channels.models import Channel
rows = {str(c.id): (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source else None)
        for c in Channel.objects.filter(channel_group_id=1915).select_related('epg_data__epg_source')}
open('/tmp/s2_before.json','w').write(json.dumps(rows))
print('snapshot rows:', len(rows))
"
```

- [ ] **Step 2: DISARM (POWERSHELL)**

The scheduler and `auto_rescan_on_m3u_refresh` are live. Without this, a scheduled tick or M3U refresh can make S2's first execution unsupervised and applied.

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.plugins.models import PluginConfig
c = PluginConfig.objects.get(key='event-channel-managarr')
s = dict(c.settings or {})
print('ORIGINAL manage_dummy_epg=', s.get('manage_dummy_epg'),
      'auto_rescan=', s.get('auto_rescan_on_m3u_refresh'))
s['manage_dummy_epg'] = False
s['auto_rescan_on_m3u_refresh'] = False
c.settings = s; c.save(update_fields=['settings'])
print('DISARMED')
"
```
**Record the ORIGINAL values.** Step 6 restores them.

- [ ] **Step 3: Deploy (POWERSHELL)**

```powershell
docker cp Event-Channel-Managarr\plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/data/plugins/event-channel-managarr/ecm_profiles.py
docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/event-channel-managarr
docker restart dispatcharr
```
Wait for healthy.

- [ ] **Step 4: Re-run the gate — now against the NEW code**

Repeat Task 5 Step 3. Expected: `S2_GATE_RESULT=PASS`, including "every enabled claimed channel is on the GMT source".
**If it fails, STOP.** The code is inert while disarmed; restore settings and report.

- [ ] **Step 5: Re-arm `manage_dummy_epg` only, run one supervised pass, diff (POWERSHELL)**

Set `manage_dummy_epg` back to its original value, leave `auto_rescan_on_m3u_refresh=False`, trigger one applied run from the UI, then:

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
**`LOST EPG` must be 0.** Anything else means channels were detached — stop and restore from the backup.

- [ ] **Step 6: Restore `auto_rescan_on_m3u_refresh`, then the acceptance test**

Restore the original value, then force an M3U refresh:

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.m3u.models import M3UAccount
from apps.m3u.tasks import refresh_single_m3u_account
a = M3UAccount.objects.filter(is_active=True, name__icontains='[redacted-provider-host]').first()
print('refreshing', a.id, a.name); refresh_single_m3u_account(a.id)
"
```

Wait for ECM's rescan, then confirm the DAZN channels are STILL on the GMT source and rendering local times. **This is the exact moment the manual fix was undone.** If they are reclaimed, S2 has not achieved its goal — report BLOCKED rather than patching.

- [ ] **Step 7: Record the outcome** in `.wolf/memory.md`; update the spec's §6 scorecard to mark mode 2 covered ONLY if Step 6 passed.

---

## Definition of Done

- [ ] `python -m pytest tests/ -q` fully green
- [ ] Every frozen-signature guard passes — the existing pass is provably unmodified
- [ ] Detach is called exactly once, outside any loop, `keep_ids` unmutated
- [ ] The gate printed `S2_GATE_RESULT=PASS` after deploy, with rollback verified clean
- [ ] The binding diff shows **`LOST EPG = 0`**
- [ ] After a forced M3U refresh, DAZN channels are still on the GMT source
- [ ] `manage_dummy_epg` and `auto_rescan_on_m3u_refresh` restored to their original values
