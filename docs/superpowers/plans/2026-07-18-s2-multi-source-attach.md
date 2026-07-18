# S2 — Multi-Source Managed Dummy EPG (attach-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ECM manage MORE THAN ONE dummy EPGSource and attach each channel to the one whose source timezone matches its provider family — ending the reclaim that currently un-fixes the DAZN guide every few hours.

**Architecture:** `ecm_profiles.build_profiles(settings)` resolves the frozen `PROFILES` template against live settings (so the existing `dummy_epg_event_timezone` and `dummy_epg_channel_format` still govern the default profile). `plugin.py` gains a plural `_get_or_create_managed_epg_sources()` returning `{profile_key: EPGSource}`, and `_run_managed_epg_pass` routes the scanned channels once, then attaches each channel to its routed profile's source. Detach behavior is deliberately **frozen**: `keep_ids` stays `set(enabled_channel_ids)`, so nothing that is not already detachable becomes detachable.

**Tech Stack:** Python 3, Django ORM (inside Dispatcharr), pytest, Docker.

## Global Constraints

- **`ecm_profiles.py` stays STDLIB-ONLY.** No `apps.*`, `django.*`, `core.utils`. Only non-stdlib import permitted is `regex`, inside `try/except ImportError`. Enforced by `tests/contract/test_module_purity.py`.
- **No module-level mutable state in `ecm_profiles.py`** — the loader re-imports sibling modules constantly.
- **DETACH IS FROZEN.** Do not change `keep_ids`, do not change `_detach_managed_epg`'s signature or query, do not change `scanned_channel_ids`. The ONLY detach change permitted is looping the existing toggle-OFF teardown over all managed sources (Task 6).
- **Patterns are STORED in JS named-group form `(?<name>)`** (issue #21).
- **`stock_patterns` historical union must not shrink.** It holds 8 title / 5 time / 4 date entries, of which `_orig_title`, `_orig_time`, `_prev_us_title` (+ py-named form) are historical-only. Dropping any permanently freezes pattern upgrades on existing installs.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Write commit messages via a Bash heredoc + `git commit -F <file>` (PowerShell 5.1 mangles `-m` here-strings).
- PowerShell 5.1 **cannot parse `<` redirection**, and parses a whole block before executing, so a stray `<` also silently skips preceding `docker cp` lines. Put redirection inside the container's `sh -c`.
- Any `docker exec` writing under `/data` uses `-u dispatch`.
- Repo: `C:\Users\User\docker\Event-Channel-Managarr`. Branch: create `feat/s2-multi-source` off `main`.
- Code map (authoritative line numbers): `.superpowers/sdd/s2-code-map.md`.

## THE DECISION THAT SHAPES THIS SLICE — read before Task 1

`ecm_profiles.route()` returns an `UNCLAIMED` bucket. On the live box that bucket holds **126 of 278** channels in group 1915 (idle DAZN slots, UFC/Boxing, BOX OFFICE, headers). **Today every eligible channel is attached to the single managed source regardless of whether any pattern matches it** — unmatched names simply render via the fallback template (their real name).

If S2 attached only routed channels, those 126 would get **no `epg_data` at all** and their guide entries would disappear. That is a regression, and it would be introduced by a slice whose stated property is "strictly additive."

**Therefore: UNCLAIMED channels attach to the DEFAULT profile's source.** This reproduces today's behavior exactly — they land on `ECM Managed Dummy`, their patterns don't match, and they render the fallback. Routing changes only WHICH source a *claimed* channel goes to; it never removes a channel from management. Task 5 pins this with a test.

## File Structure

| Path | Responsibility | Change |
|---|---|---|
| `Event-Channel-Managarr/ecm_profiles.py` | Add `build_profiles(settings)` resolving the template against live settings | Modify |
| `Event-Channel-Managarr/plugin.py` | Import the module; plural source management; routed attach; multi-source teardown/preview | Modify |
| `tests/unit/test_ecm_profiles.py` | `build_profiles` behavior | Modify |
| `tests/unit/test_s2_routing_semantics.py` | Pure tests for the attach-target decision (unclaimed→default, reroute set) | Create |
| `scripts/verify_s2_incontainer.py` | Read-only in-container proof of the S2 pass | Create |

---

## Task 1: `build_profiles(settings)` — resolve the template against live settings

**Files:**
- Modify: `Event-Channel-Managarr/ecm_profiles.py`
- Modify: `tests/unit/test_ecm_profiles.py`

**Interfaces:**
- Consumes: `Profile`, `PROFILES`, `UNCLAIMED` (existing)
- Produces: `build_profiles(settings: dict) -> tuple[Profile, ...]` — same order as `PROFILES`, with the default profile's `timezone`, `program_duration_minutes` and pattern trio resolved from settings.

**Why:** `US_ET.timezone` is currently hardcoded `"America/New_York"`, but ECM ships a user-facing `dummy_epg_event_timezone` setting. Hardcoding would silently break every install whose event names are not Eastern. Likewise `dummy_epg_channel_format=SE` must still swap in the SE pattern trio, and `dummy_epg_event_duration_hours` must still govern block length.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ecm_profiles.py`:

```python
# --- build_profiles: resolve the frozen template against live settings ----------

def test_build_profiles_defaults_to_the_frozen_template():
    """No settings -> the shipped values, unchanged."""
    built = ecm_profiles.build_profiles({})
    assert [p.key for p in built] == [p.key for p in ecm_profiles.PROFILES]


def test_build_profiles_honours_the_event_timezone_setting():
    """The user-facing dummy_epg_event_timezone must still govern the DEFAULT
    profile. Hardcoding America/New_York would silently break every install
    whose event names are not Eastern."""
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Europe/Stockholm"})
    default = next(p for p in built if p.is_default)
    assert default.timezone == "Europe/Stockholm"


def test_build_profiles_never_changes_the_dazn_timezone():
    """dazn_gmt is UTC by provider fact, not by user preference."""
    built = ecm_profiles.build_profiles({"dummy_epg_event_timezone": "Europe/Stockholm"})
    dazn = next(p for p in built if p.key == "dazn_gmt")
    assert dazn.timezone == "UTC"


def test_build_profiles_honours_the_duration_setting():
    built = ecm_profiles.build_profiles({"dummy_epg_event_duration_hours": 2})
    default = next(p for p in built if p.is_default)
    assert default.program_duration_minutes == 120


@pytest.mark.parametrize("bad", ["", "nonsense", 0, -3, None])
def test_build_profiles_falls_back_on_a_bad_duration(bad):
    """A bad setting must never produce a zero-length or negative programme."""
    built = ecm_profiles.build_profiles({"dummy_epg_event_duration_hours": bad})
    default = next(p for p in built if p.is_default)
    assert default.program_duration_minutes > 0


def test_build_profiles_se_format_swaps_the_default_pattern_trio():
    """dummy_epg_channel_format=SE must still select the SE patterns, exactly as
    the single-source code did."""
    built = ecm_profiles.build_profiles({"dummy_epg_channel_format": "SE"})
    default = next(p for p in built if p.is_default)
    assert default.title_pattern == ecm_profiles.SE_TITLE_PATTERN
    assert default.key == "se"


def test_build_profiles_se_default_is_still_evaluated_last():
    """The SE selector is pipe-based and every DAZN name is pipe-delimited. As the
    DEFAULT it is evaluated last, so dazn_gmt still wins its own family. If SE were
    ever a NON-default ahead of dazn_gmt, dazn_gmt would claim ZERO."""
    built = ecm_profiles.build_profiles({"dummy_epg_channel_format": "SE"})
    names = _fixture_names()
    routed = ecm_profiles.route(names, profiles=built)
    assert set(routed["dazn_gmt"]) == {n for n in names if "(GMT)" in n}


def test_build_profiles_returns_exactly_one_default():
    for fmt in ("US", "SE", "", "garbage"):
        built = ecm_profiles.build_profiles({"dummy_epg_channel_format": fmt})
        assert sum(1 for p in built if p.is_default) == 1, fmt


def test_build_profiles_output_is_routable():
    """Guards against build_profiles emitting duplicate keys or a sentinel collision,
    which route() rejects."""
    for fmt in ("US", "SE"):
        built = ecm_profiles.build_profiles({"dummy_epg_channel_format": fmt})
        ecm_profiles.route(["PPV EVENT 01: X"], profiles=built)   # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_ecm_profiles.py -k build_profiles -v`
Expected: FAIL — `AttributeError: module 'ecm_profiles' has no attribute 'build_profiles'`.

- [ ] **Step 3: Implement**

In `Event-Channel-Managarr/ecm_profiles.py`, add the SE pattern constants next to the existing profile definitions, then `build_profiles` below `PROFILES`:

```python
# The SE channel-name format (pipe-delimited, 24h clock, named month). Kept as a
# DEFAULT-only profile: its selector is pipe-based and every DAZN name is
# pipe-delimited, so as a NON-default ahead of dazn_gmt it would claim the whole
# DAZN family and leave dazn_gmt with ZERO. As the default it is evaluated last.
SE_TITLE_PATTERN = r"\|\s*(?<title>[^|]+?)\s*\|"
SE_TIME_PATTERN = r"(?<hour>\d{1,2}):(?<minute>\d{2})"
SE_DATE_PATTERN = (
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?<day>\d{1,2})\s+"
    r"(?<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b"
)

DEFAULT_EVENT_TIMEZONE = "US/Eastern"
DEFAULT_DURATION_HOURS = 3


def _resolve_duration_minutes(raw):
    """Minutes per programme block, from the hours setting. Never <= 0."""
    try:
        hours = int(str(raw).strip())
    except (ValueError, TypeError, AttributeError):
        hours = DEFAULT_DURATION_HOURS
    if hours <= 0:
        hours = DEFAULT_DURATION_HOURS
    return hours * 60


def build_profiles(settings):
    """Resolve the frozen PROFILES template against live plugin settings.

    PROFILES carries provider FACTS (selectors, patterns, and dazn_gmt's UTC).
    This resolves the values a USER controls, which today are global settings and
    which must keep working exactly as they did under the single-source code:

      dummy_epg_event_timezone       -> the DEFAULT profile's source timezone
      dummy_epg_event_duration_hours -> every profile's block length
      dummy_epg_channel_format=SE    -> swaps the DEFAULT profile's pattern trio

    dazn_gmt's timezone is NEVER resolved from settings -- it is UTC because the
    provider stamps (GMT) in the channel name, which is a fact about the data, not
    a user preference.

    Returns a tuple in the same order as PROFILES (non-default first, default last
    -- route() re-sorts anyway, but keeping the order stable keeps logs readable).
    """
    settings = settings or {}
    duration = _resolve_duration_minutes(settings.get("dummy_epg_event_duration_hours"))

    tz = str(settings.get("dummy_epg_event_timezone") or "").strip() or DEFAULT_EVENT_TIMEZONE
    is_se = str(settings.get("dummy_epg_channel_format") or "").strip().upper() == "SE"

    resolved = []
    for profile in PROFILES:
        if not profile.is_default:
            resolved.append(replace(profile, program_duration_minutes=duration))
            continue
        if is_se:
            resolved.append(replace(
                profile,
                key="se",
                title_pattern=SE_TITLE_PATTERN,
                time_pattern=SE_TIME_PATTERN,
                date_pattern=SE_DATE_PATTERN,
                # An SE install's names are pipe-delimited; claim on that shape.
                selector=r"\|",
                timezone=tz,
                program_duration_minutes=duration,
            ))
        else:
            resolved.append(replace(profile, timezone=tz, program_duration_minutes=duration))
    return tuple(resolved)
```

Add `replace` to the existing dataclasses import at the top of the file:

```python
from dataclasses import dataclass, replace
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_ecm_profiles.py -v`
Expected: all pass (existing tests plus the 9 new ones).

- [ ] **Step 5: Confirm purity guards still hold**

Run: `python -m pytest tests/contract/test_module_purity.py -v`
Expected: all pass. `replace` is stdlib `dataclasses`; the new module-level constants are strings and ints, which the mutable-state guard permits.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/User/docker/Event-Channel-Managarr
git add Event-Channel-Managarr/ecm_profiles.py tests/unit/test_ecm_profiles.py
git commit -F /tmp/s2_t1.txt
```
with `/tmp/s2_t1.txt`:
```
feat: build_profiles resolves the profile template against live settings

PROFILES carries provider facts; the values a USER controls stay in settings.
dummy_epg_event_timezone governs the DEFAULT profile's source timezone,
dummy_epg_event_duration_hours the block length, and dummy_epg_channel_format=SE
still swaps the default's pattern trio -- all exactly as the single-source code
did. Hardcoding the timezone would have silently broken every install whose
event names are not Eastern.

dazn_gmt's UTC is never resolved from settings: it is UTC because the provider
stamps (GMT) in the name, which is a fact about the data.

SE is a DEFAULT-only profile. Its selector is pipe-based and every DAZN name is
pipe-delimited, so as a non-default ahead of dazn_gmt it would claim the entire
DAZN family and leave dazn_gmt with zero -- a defect that shipped in two earlier
draft designs. As the default it is evaluated last and dazn_gmt keeps its family.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 2: Pure attach-target semantics

**Files:**
- Create: `tests/unit/test_s2_routing_semantics.py`
- Modify: `Event-Channel-Managarr/ecm_profiles.py`

**Interfaces:**
- Consumes: `build_profiles`, `route`, `UNCLAIMED`
- Produces: `attach_targets(names, profiles) -> dict[str, str]` — maps each channel NAME to the profile key whose source should hold it. Every name gets a key; `UNCLAIMED` names map to the default profile's key.

**Why a separate function:** the "unclaimed falls back to the default" rule is the single most behavior-critical decision in S2 (see the header section). Putting it in a pure function means it is unit-tested rather than buried in an ORM loop.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_s2_routing_semantics.py
"""Attach-target semantics for S2.

route() partitions names and leaves non-matching names in UNCLAIMED. But today
EVERY eligible channel is attached to the single managed source regardless of
whether any pattern matches it -- unmatched names simply render the fallback
template (their real name). If S2 attached only ROUTED channels, the 126 of 278
live channels that match no selector would lose their epg_data entirely and their
guide entries would vanish.

So: UNCLAIMED attaches to the DEFAULT profile's source. Routing changes only WHICH
source a claimed channel goes to; it never removes a channel from management.
"""

from pathlib import Path

import pytest

import ecm_profiles

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"


def _fixture_names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def test_every_name_gets_a_target():
    """No name may fall through without a source -- that is the regression this
    whole module exists to prevent."""
    names = _fixture_names()
    targets = ecm_profiles.attach_targets(names, ecm_profiles.build_profiles({}))
    assert set(targets) == set(names)
    assert all(v for v in targets.values())


def test_unclaimed_names_target_the_default_profile():
    names = _fixture_names()
    profiles = ecm_profiles.build_profiles({})
    default_key = next(p.key for p in profiles if p.is_default)
    routed = ecm_profiles.route(names, profiles=profiles)
    targets = ecm_profiles.attach_targets(names, profiles)
    for name in routed[ecm_profiles.UNCLAIMED]:
        assert targets[name] == default_key


def test_claimed_names_target_their_own_profile():
    names = _fixture_names()
    profiles = ecm_profiles.build_profiles({})
    targets = ecm_profiles.attach_targets(names, profiles)
    for name in names:
        if "(GMT)" in name:
            assert targets[name] == "dazn_gmt", name


def test_no_name_targets_a_profile_that_is_not_in_the_chain():
    names = _fixture_names()
    profiles = ecm_profiles.build_profiles({})
    valid = {p.key for p in profiles}
    assert set(ecm_profiles.attach_targets(names, profiles).values()) <= valid


def test_target_counts_on_the_real_corpus():
    """dazn_gmt keeps its 48; everything else lands on the default source -- which
    is exactly what the single-source code did for all 278."""
    names = _fixture_names()
    profiles = ecm_profiles.build_profiles({})
    targets = ecm_profiles.attach_targets(names, profiles)
    counts = {}
    for key in targets.values():
        counts[key] = counts.get(key, 0) + 1
    assert counts["dazn_gmt"] == 48
    assert counts["us_et"] == 230       # 104 claimed + 126 unclaimed
    assert sum(counts.values()) == 278


def test_attach_targets_requires_a_default_profile():
    """Without a default there is no fallback, so an unclaimed name would have no
    home -- fail loudly rather than silently dropping it."""
    only = tuple(p for p in ecm_profiles.build_profiles({}) if not p.is_default)
    with pytest.raises(ValueError, match="default"):
        ecm_profiles.attach_targets(["nothing matches this"], only)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_s2_routing_semantics.py -v`
Expected: FAIL — `AttributeError: module 'ecm_profiles' has no attribute 'attach_targets'`.

- [ ] **Step 3: Implement**

Append to `Event-Channel-Managarr/ecm_profiles.py`:

```python
def attach_targets(names, profiles):
    """Map each channel NAME to the profile key whose source should hold it.

    Every name gets a target. Names no selector claimed fall back to the DEFAULT
    profile, reproducing the single-source behavior where every eligible channel
    was attached regardless of whether any pattern matched it (unmatched names
    render the fallback template -- their real name).

    Raises ValueError if there is no default profile: without one an unclaimed
    name would have no home, and silently dropping it would blank a live guide.
    """
    default = next((p for p in profiles if p.is_default), None)
    if default is None:
        raise ValueError("attach_targets requires exactly one default profile")

    routed = route(names, profiles=profiles)
    targets = {}
    for key, bucket in routed.items():
        resolved = default.key if key == UNCLAIMED else key
        for name in bucket:
            targets[name] = resolved
    return targets
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_s2_routing_semantics.py -v`
Expected: 6 passed.

- [ ] **Step 5: Full suite + purity**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/ecm_profiles.py tests/unit/test_s2_routing_semantics.py
git commit -F /tmp/s2_t2.txt
```
with:
```
feat: attach_targets -- unclaimed names fall back to the default profile

route() leaves non-matching names in UNCLAIMED, but the single-source code
attached EVERY eligible channel regardless of whether a pattern matched; unmatched
names simply rendered the fallback template. On live data 126 of 278 channels
match no selector, so attaching only routed channels would have deleted their
epg_data and blanked their guide entries -- a regression introduced by a slice
whose stated property is "strictly additive".

attach_targets makes the fallback explicit and unit-tested: routing changes only
WHICH source a claimed channel goes to, never whether it is managed at all.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 3: Per-profile timezone in `_localized_template_props`

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py:2174` (signature) and `:2203` (the read)

**Interfaces:**
- Produces: `_localized_template_props(self, settings, source_tz_name=None)` — when `source_tz_name` is given it is used as the source timezone instead of the global setting.

**Why:** the function computes `output_timezone` and the TZ-abbreviation suffix by comparing the SOURCE timezone against Dispatcharr's system timezone. Under multi-source, the GMT source must be compared using **UTC**, not the global `dummy_epg_event_timezone`. If it is not, the GMT source inherits templates computed for the ET source — and when the global setting is empty or invalid, branch 1/2 returns `output_timezone: ""`, blanking the GMT source's output timezone and rendering `22:00 (GMT)` as 22:00 instead of 17:00 Central. A silent 5-hour error triggered by a setting belonging to the *other* profile.

- [ ] **Step 1: Read the current function**

Read `Event-Channel-Managarr/plugin.py` lines 2174-2247 in full before editing. Note it has FOUR return branches and every one returns the same 4 keys.

- [ ] **Step 2: Change the signature and the source-tz read**

At line 2174, change:
```python
    def _localized_template_props(self, settings):
```
to:
```python
    def _localized_template_props(self, settings, source_tz_name=None):
```

At line 2203, change:
```python
        source_tz_name = str(settings.get("dummy_epg_event_timezone", "")).strip()
```
to:
```python
        # Under multi-source, each profile supplies its OWN source timezone: the GMT
        # source must be compared against UTC, not against the global setting that
        # belongs to the default profile. Falling back to the setting keeps the
        # single-source call sites behaving identically.
        if source_tz_name is None:
            source_tz_name = str(settings.get("dummy_epg_event_timezone", "")).strip()
        else:
            source_tz_name = str(source_tz_name or "").strip()
```

- [ ] **Step 3: Verify no behavior change for existing callers**

Run: `python -m pytest tests/ -q`
Expected: all pass. The only existing call site (`plugin.py:2351`) passes no third argument, so it takes the `None` branch and reads the setting exactly as before.

- [ ] **Step 4: Verify by inspection**

Confirm the only call site of `_localized_template_props` in `plugin.py` is line 2351, and that it is unchanged in this task. Task 4 changes it.

- [ ] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/plugin.py
git commit -F /tmp/s2_t3.txt
```
with:
```
refactor: _localized_template_props accepts an explicit source timezone

It computes output_timezone and the TZ-abbreviation suffix by comparing the SOURCE
timezone against Dispatcharr's system timezone. Under multi-source the GMT source
must be compared using UTC, not the global dummy_epg_event_timezone that belongs to
the default profile -- otherwise the GMT source inherits the ET source's templates,
and when the global setting is empty or invalid it inherits output_timezone="",
rendering 22:00 (GMT) as 22:00 instead of 17:00 Central. A silent five-hour error
triggered by a setting belonging to the other profile.

Defaults to the existing setting when the argument is omitted, so the current call
site is byte-for-byte unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 4: Plural source create/refresh

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` — add `import ecm_profiles` at line 44, add `_managed_props_for_profile` and `_get_or_create_managed_epg_sources`

**Interfaces:**
- Consumes: `ecm_profiles.build_profiles`, `ecm_profiles.profile_props`
- Produces:
  - `_managed_props_for_profile(self, profile, settings) -> dict` — the full `custom_properties` payload for one profile
  - `_get_or_create_managed_epg_sources(self, settings, logger) -> dict[str, EPGSource]` — keyed by profile key; a profile whose source cannot be created is OMITTED (partial success beats total failure)

**Critical:** the existing singular `_get_or_create_managed_epg_source` is NOT deleted in this task. Leave it in place; Task 6 removes its remaining call sites. This keeps every intermediate commit working.

- [ ] **Step 1: Add the import**

At `Event-Channel-Managarr/plugin.py` line 44 (directly below `import ecm_parsing`):
```python
import ecm_profiles
```

- [ ] **Step 2: Write the failing test**

Create `tests/contract/test_s2_plugin_wiring.py`:

```python
"""Contract tests for S2's plugin.py wiring, parsed with ast -- never imported.

plugin.py imports Django at module scope and cannot be imported outside the
container, so these assert on source structure. That is weaker than behavioral
testing, and deliberately so: the behavior is verified in-container by
scripts/verify_s2_incontainer.py. These guard the things a static check CAN
catch -- that the wiring exists, and that the frozen detach contract is intact.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"


def _source():
    return PLUGIN_PY.read_text(encoding="utf-8")


def _functions():
    tree = ast.parse(_source(), filename=str(PLUGIN_PY))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_ecm_profiles_is_imported():
    assert re.search(r"^import ecm_profiles$", _source(), re.M), \
        "plugin.py must import the pure profile module"


def test_plural_source_factory_exists():
    assert "_get_or_create_managed_epg_sources" in _functions()


def test_props_builder_exists():
    assert "_managed_props_for_profile" in _functions()


def test_detach_contract_is_frozen():
    """S2 is attach-only. keep_ids must remain every enabled channel, so that a
    routing mistake can never detach a real channel. If this line changes, S2 has
    silently become S3."""
    assert "keep_ids = set(enabled_channel_ids) if toggle_on else set()" in _source(), \
        "the frozen detach contract was modified -- S2 must not change keep_ids"


def test_detach_helper_signature_unchanged():
    fn = _functions()["_detach_managed_epg"]
    args = [a.arg for a in fn.args.args]
    assert args == ["self", "managed_source", "keep_channel_ids", "logger", "scope_ids"]
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_plugin_wiring.py -v`
Expected: FAIL on `test_plural_source_factory_exists` and `test_props_builder_exists` (the import test passes once Step 1 is done).

- [ ] **Step 4: Implement both methods**

Insert directly ABOVE the existing `def _get_or_create_managed_epg_source` (plugin.py:2249):

```python
    def _managed_props_for_profile(self, profile, settings):
        """Full EPGSource.custom_properties payload for one profile.

        ecm_profiles.profile_props() deliberately omits `managed_by` -- that is an
        identity breadcrumb, not renderer config -- so it is added here, the same
        way the single-source builder does.

        The profile's OWN timezone is passed to _localized_template_props so the
        GMT source's templates are computed against UTC rather than against the
        global setting, which belongs to the default profile.
        """
        props = dict(ecm_profiles.profile_props(profile))
        props["managed_by"] = "event-channel-managarr"
        props.update(self._localized_template_props(settings, profile.timezone))
        return props

    def _get_or_create_managed_epg_sources(self, settings, logger):
        """Create or refresh one dummy EPGSource per profile.

        Returns {profile_key: EPGSource}. A profile whose source cannot be created
        or is name-collided with a non-dummy row is OMITTED rather than aborting the
        whole pass -- partial success beats total failure, because aborting would
        leave every profile's channels unmanaged.

        Adoption is by NAME, deliberately: ecm_profiles.DAZN_GMT.source_name equals
        the name of the hand-made source already serving those channels in
        production, so this adopts that row in place and its 99 channel bindings
        are never broken. The row simply becomes plugin-owned.
        """
        from apps.epg.models import EPGSource

        # Built ONCE: the historical union is ~17 string constructions and is
        # consulted per pattern key per profile.
        stock = self._stock_patterns(settings)

        sources = {}
        for profile in ecm_profiles.build_profiles(settings):
            desired = self._managed_props_for_profile(profile, settings)
            try:
                source, created = EPGSource.objects.get_or_create(
                    name=profile.source_name,
                    source_type="dummy",
                    defaults={
                        "custom_properties": desired,
                        "is_active": True,
                        "refresh_interval": 0,
                    },
                )
            except Exception as exc:
                logger.warning(
                    f"{LOG_PREFIX} Could not get/create EPG source "
                    f"{profile.source_name!r} for profile {profile.key!r}: {exc}")
                continue

            if created:
                logger.info(
                    f"{LOG_PREFIX} Created managed EPG source {profile.source_name!r} "
                    f"for profile {profile.key!r}")
                sources[profile.key] = source
                continue

            current = dict(source.custom_properties or {})
            changed = False
            for key, value in desired.items():
                if key in self.PATTERN_KEYS:
                    cur = current.get(key)
                    # A pattern the user edited in Dispatcharr's UI is never
                    # overwritten (issue #21). "Edited" means: present, and not
                    # equal to any default this plugin has ever shipped.
                    if cur is not None and cur not in stock[key]:
                        continue
                if current.get(key) != value:
                    current[key] = value
                    changed = True
            if changed:
                source.custom_properties = current
                source.save(update_fields=["custom_properties"])
                logger.info(
                    f"{LOG_PREFIX} Refreshed managed EPG source {profile.source_name!r}")
            sources[profile.key] = source

        return sources
```

- [ ] **Step 5: Extract the stock-pattern set so both builders share it**

The plural builder above calls `self._stock_patterns(settings)` and `self.PATTERN_KEYS`. Extract these from the existing singular method rather than duplicating them — the historical union must have exactly ONE definition or the two copies will drift and silently freeze pattern upgrades.

In `_get_or_create_managed_epg_source`, the block currently building `PATTERN_KEYS` and `stock_patterns` (plugin.py:2364-2400) becomes a new method placed directly above `_managed_props_for_profile`:

```python
    PATTERN_KEYS = ("title_pattern", "time_pattern", "date_pattern")

    def _stock_patterns(self, settings):
        """Every pattern default this plugin has EVER shipped, per pattern key.

        A live pattern equal to any of these is treated as untouched and may be
        upgraded; anything else is treated as user-customized and left alone
        (issue #21). The historical entries are load-bearing: dropping them would
        make every pre-bug-051 install read as customized and freeze its patterns
        permanently. There must be exactly one definition of this set.
        """
        # ... move the EXISTING body from plugin.py:2364-2400 here verbatim,
        # returning the `stock_patterns` dict it already builds.
```

Then in `_get_or_create_managed_epg_source`, replace the moved block with:
```python
        PATTERN_KEYS = self.PATTERN_KEYS
        stock_patterns = self._stock_patterns(settings)
```

- [ ] **Step 6: Add a test pinning the shared stock set**

Append to `tests/contract/test_s2_plugin_wiring.py`:

```python
def test_stock_patterns_has_exactly_one_definition():
    """Two copies of the historical union would drift, and a shrunken union
    permanently freezes pattern upgrades on every pre-bug-051 install."""
    assert _source().count("def _stock_patterns") == 1


def test_stock_pattern_union_sizes_are_preserved():
    """8 title / 5 time / 4 date entries, of which several are historical-only.
    A refactor that drops any of them is silent and irreversible."""
    src = _source()
    fn_start = src.index("def _stock_patterns")
    fn_end = src.index("def _managed_props_for_profile")
    body = src[fn_start:fn_end]
    for name in ("_orig_title", "_orig_time", "_prev_us_title"):
        assert name in body, f"historical default {name} was dropped from the union"
```

- [ ] **Step 7: Run**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_plugin_wiring.py
git commit -F /tmp/s2_t4.txt
```
with:
```
feat: per-profile managed EPG source creation

_get_or_create_managed_epg_sources creates or refreshes one dummy EPGSource per
profile and returns {profile_key: EPGSource}. A profile whose source cannot be
created is omitted rather than aborting the pass -- aborting would leave every
profile's channels unmanaged, which is worse than one profile being unmanaged.

Adoption is by NAME deliberately: ecm_profiles.DAZN_GMT.source_name equals the
hand-made source already serving those channels in production, so this adopts that
row in place. Its 99 bindings are never broken; the row becomes plugin-owned, which
is what ends the reclaim.

The stock-pattern historical union is EXTRACTED rather than duplicated. Two copies
would drift, and a shrunken union silently freezes pattern upgrades on every
pre-bug-051 install.

The singular _get_or_create_managed_epg_source is left in place; its call sites are
migrated in a later task so every intermediate commit works.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 5: Routed attach

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` — `_attach_managed_epg` gains a target map; `_run_managed_epg_pass`'s applied branch routes

**Interfaces:**
- Consumes: `ecm_profiles.attach_targets`, `_get_or_create_managed_epg_sources`
- Produces: `_attach_routed(self, channels, sources, targets, logger, settings, rate_limiter, reroute_ids) -> list[int]`

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_s2_plugin_wiring.py`:

```python
def test_routed_attach_exists():
    assert "_attach_routed" in _functions()


def test_reroute_set_is_computed():
    """A channel bound to managed source A but routed to B must be re-pointed.
    Without an explicit reroute set it is invisible: attach only considers
    epg_data IS NULL, and _managed_override_ids excludes every dummy source."""
    assert "reroute_ids" in _source()


def test_attach_still_runs_before_detach():
    """Ordering is load-bearing. With attach first, a re-routed channel already
    points at its new source by detach time, so the old source's detach query no
    longer matches it. Detach-first would open a window where it is unbound."""
    src = _source()
    assert src.index("_attach_routed(") < src.index("_detach_managed_epg(")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_plugin_wiring.py -v`
Expected: FAIL on `test_routed_attach_exists`.

- [ ] **Step 3: Implement `_attach_routed`**

Insert directly below the existing `_attach_managed_epg` (after plugin.py:2520):

```python
    def _attach_routed(self, channels, sources, targets, logger, settings=None,
                       rate_limiter=None, reroute_ids=None):
        """Attach each channel to the source its routed profile owns.

        `targets` maps channel NAME -> profile key (from ecm_profiles.attach_targets,
        which already folds unclaimed names onto the default profile). `sources` maps
        profile key -> EPGSource.

        A channel is bound when it has no epg_data at all, OR its id is in
        `reroute_ids` and it is not already on its target source. The reroute set is
        required because neither existing path can see such a channel: attach
        considers only `epg_data IS NULL`, and _managed_override_ids excludes every
        dummy source.

        Delegates the actual write to _attach_managed_epg, once per target source,
        so the transaction/bulk-update/name-resync behavior stays in one place.
        """
        reroute_ids = set(reroute_ids or ())
        default_key = next(
            (p.key for p in ecm_profiles.build_profiles(settings or {}) if p.is_default),
            None)

        by_source = {}
        for channel in channels:
            key = targets.get(channel.name, default_key)
            source = sources.get(key) or sources.get(default_key)
            if source is None:
                logger.warning(
                    f"{LOG_PREFIX} No managed source for profile {key!r}; "
                    f"skipping channel {channel.id}")
                continue
            by_source.setdefault(source.id, (source, []))[1].append(channel)

        attached = []
        for source, group in by_source.values():
            # Re-point only channels whose CURRENT source differs from this target.
            group_reroute = {
                c.id for c in group
                if c.id in reroute_ids
                and getattr(c.epg_data, "epg_source_id", None) != source.id
            }
            attached.extend(self._attach_managed_epg(
                group, source, logger, settings=settings,
                rate_limiter=rate_limiter, override_ids=group_reroute))
        return attached
```

- [ ] **Step 4: Wire it into `_run_managed_epg_pass`'s applied branch**

Replace the applied-run attach block (plugin.py:2657-2690) so it uses the plural sources and routed attach. The detach lines at 2692-2695 are NOT touched.

```python
        if toggle_on:
            sources = self._get_or_create_managed_epg_sources(settings, logger)
        else:
            sources = {
                p.key: s for p, s in (
                    (p, EPGSource.objects.filter(
                        name=p.source_name, source_type="dummy").first())
                    for p in ecm_profiles.build_profiles(settings))
                if s is not None
            }
        if not sources:
            return [], []

        managed_source_ids = {s.id for s in sources.values()}
        attached_ids = []
        if toggle_on:
            no_epg_channels = list(Channel.objects.filter(
                id__in=enabled_channel_ids, epg_data__isnull=True))

            override_ids = set(self._managed_override_ids(
                settings, sources.get(default_key), enabled_channel_ids, logger))
            override_channels = (list(Channel.objects.filter(id__in=override_ids)
                                      .select_related("epg_data"))
                                 if override_ids else [])

            # Channels already on one of OUR sources but routed to a DIFFERENT one.
            # Invisible to both other paths, so computed explicitly.
            on_managed = list(Channel.objects.filter(
                id__in=enabled_channel_ids,
                epg_data__epg_source_id__in=managed_source_ids
            ).select_related("epg_data"))

            candidates = no_epg_channels + override_channels + on_managed
            seen = set()
            channels_for_epg = []
            for c in candidates:
                if c.id not in seen:
                    seen.add(c.id)
                    channels_for_epg.append(c)

            targets = ecm_profiles.attach_targets(
                [c.name for c in channels_for_epg],
                ecm_profiles.build_profiles(settings))

            reroute_ids = set(override_ids) | {
                c.id for c in on_managed
                if sources.get(targets.get(c.name))
                and c.epg_data.epg_source_id != sources[targets[c.name]].id
            }

            rate_limiter = SmartRateLimiter(
                settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING))
            attached_ids = self._attach_routed(
                channels_for_epg, sources, targets, logger,
                settings=settings, rate_limiter=rate_limiter, reroute_ids=reroute_ids)
```

Define `default_key` just above that block:
```python
        default_key = next(
            (p.key for p in ecm_profiles.build_profiles(settings) if p.is_default), None)
```

- [ ] **Step 5: Run**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_plugin_wiring.py
git commit -F /tmp/s2_t5.txt
```
with:
```
feat: attach each channel to its routed profile's source

_attach_routed groups channels by target source and delegates each group to the
existing _attach_managed_epg, so transaction, bulk-update and name-resync behavior
stays in one place.

The reroute set is the load-bearing part: a channel bound to managed source A but
routed to B is invisible to both existing paths -- attach considers only
epg_data IS NULL, and _managed_override_ids excludes every dummy source. Without
an explicit set it would sit on the wrong-timezone source forever.

Attach still runs before detach. With attach first, a re-routed channel already
points at its new source by detach time, so the old source's detach query no longer
matches it; detach-first would open a window where the channel is unbound.

Detach is untouched: keep_ids remains set(enabled_channel_ids).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 6: Multi-source teardown and dry-run preview

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` — the dry-run branch (2629-2654) and the detach call (2692-2695)

**Why:** both the dry-run preview and the toggle-OFF teardown look the source up by the literal `"ECM Managed Dummy"`. With N sources, unticking `manage_dummy_epg` would tear down only the default source and leave every other profile's channels bound to a source ECM no longer manages — turning the feature off would stop turning it off. The preview would likewise under-report every non-default profile.

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_s2_plugin_wiring.py`:

```python
def test_no_hardcoded_source_name_lookups_remain():
    """Both the dry-run preview and the toggle-OFF teardown used to look the source
    up by this literal. With N sources that tears down only one and silently orphans
    the rest -- turning the feature off would stop turning it off."""
    src = _source()
    body_start = src.index("def _run_managed_epg_pass")
    body = src[body_start:]
    assert 'name="ECM Managed Dummy"' not in body, \
        "_run_managed_epg_pass still looks a source up by hardcoded name"


def test_teardown_loops_over_every_managed_source():
    src = _source()
    body_start = src.index("def _run_managed_epg_pass")
    body = src[body_start:]
    assert "for _key, _src in sources.items()" in body, \
        "teardown must iterate every managed source, not just one"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_plugin_wiring.py -v`
Expected: FAIL on both.

- [ ] **Step 3: Replace the dry-run branch**

Replace plugin.py:2629-2654 with:

```python
        if dry_run:
            sources = {
                p.key: s for p, s in (
                    (p, EPGSource.objects.filter(
                        name=p.source_name, source_type="dummy").first())
                    for p in ecm_profiles.build_profiles(settings))
                if s is not None
            }
            if not sources:
                return [], []
            managed_source_ids = {s.id for s in sources.values()}

            if toggle_on:
                null_ids = list(Channel.objects.filter(
                    id__in=enabled_channel_ids, epg_data__isnull=True
                ).values_list("id", flat=True))
                default_key = next(
                    (p.key for p in ecm_profiles.build_profiles(settings) if p.is_default),
                    None)
                override_ids = self._managed_override_ids(
                    settings, sources.get(default_key), enabled_channel_ids, logger)
                attached_ids = list(dict.fromkeys(list(null_ids) + list(override_ids)))

                detach_q = Channel.objects.filter(
                    epg_data__epg_source_id__in=managed_source_ids
                ).exclude(id__in=enabled_channel_ids)
                if scanned_channel_ids is not None:
                    detach_q = detach_q.filter(id__in=scanned_channel_ids)
                detached_ids = list(detach_q.values_list("id", flat=True))
            else:
                attached_ids = []
                detached_ids = list(Channel.objects.filter(
                    epg_data__epg_source_id__in=managed_source_ids
                ).values_list("id", flat=True))
            logger.info(
                f"{LOG_PREFIX} [dry-run] Managed EPG would attach {len(attached_ids)}, "
                f"detach {len(detached_ids)} across {len(sources)} source(s)")
            return attached_ids, detached_ids
```

- [ ] **Step 4: Replace the detach call**

Replace plugin.py:2692-2695 with a loop. **`keep_ids` is unchanged** — the frozen contract:

```python
        keep_ids = set(enabled_channel_ids) if toggle_on else set()
        detach_scope = scanned_channel_ids if toggle_on else None
        detached_ids = []
        for _key, _src in sources.items():
            detached_ids.extend(self._detach_managed_epg(
                _src, keep_ids, logger, scope_ids=detach_scope))
```

- [ ] **Step 5: Run**

Run: `python -m pytest tests/ -q`
Expected: all pass, including `test_detach_contract_is_frozen` from Task 4 (the `keep_ids` line is unchanged).

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_plugin_wiring.py
git commit -F /tmp/s2_t6.txt
```
with:
```
fix: dry-run preview and toggle-OFF teardown span every managed source

Both looked the source up by the literal "ECM Managed Dummy". With N managed
sources that tears down only the default one and silently orphans every other
profile's channels on a source ECM no longer manages -- unticking manage_dummy_epg
would stop turning the feature off, with no code path that ever detaches them
again. The preview under-reported the same way.

keep_ids is deliberately unchanged: still set(enabled_channel_ids), so nothing
that was not already detachable becomes detachable. S2 remains attach-only.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 7: Read-only in-container proof

**Files:**
- Create: `scripts/verify_s2_incontainer.py`

**This is the S2 gate.** It proves the routed attach produces the right target map against LIVE data and that both sources render correctly — WITHOUT running a real pass.

- [ ] **Step 1: Take a backup (POWERSHELL)**

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
"""Read-only proof of S2's routing against LIVE data.

    docker cp Event-Channel-Managarr/ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
    docker cp scripts/verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
    docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"

WHAT IT PROVES
  1. attach_targets covers EVERY live channel in the group -- none falls through
     without a source (the regression that would blank 126 guides).
  2. The GMT-bearing names target dazn_gmt and everything else targets the default.
  3. Both profiles' resolved props render correct local times through Dispatcharr's
     REAL renderer, via TEMPORARY UNBOUND EPGSources.

WHAT IT WRITES
  Two temporary EPGSources, deleted in a finally block keyed on NAME. No channel is
  repointed. Dispatcharr's post_save signal auto-creates one EPGData row per temp
  source; both go away by CASCADE on delete.

EXIT CODE: 0 pass, 1 fail.
"""

import sys
import traceback

sys.path.insert(0, "/tmp")
import ecm_profiles  # noqa: E402

from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGSource  # noqa: E402
from apps.output import epg as epg_renderer  # noqa: E402
from apps.plugins.models import PluginConfig  # noqa: E402

GROUP_ID = 1915
TEMP_PREFIX = "__ecm_s2_verify__"
failures = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main():
    settings = (PluginConfig.objects.get(key="event-channel-managarr").settings or {})
    profiles = ecm_profiles.build_profiles(settings)
    print(f"\nresolved profiles: " + ", ".join(
        f"{p.key}(tz={p.timezone}{', default' if p.is_default else ''})" for p in profiles))

    names = list(Channel.objects.filter(channel_group_id=GROUP_ID)
                 .order_by("id").values_list("name", flat=True))
    print(f"live channel names in group {GROUP_ID}: {len(names)}")

    print("\n(1) attach_targets covers every channel")
    targets = ecm_profiles.attach_targets(names, profiles)
    check("every name has a target", set(targets) == set(names),
          f"{len(targets)} targets vs {len(set(names))} distinct names")

    default_key = next(p.key for p in profiles if p.is_default)
    gmt = {n for n in names if "(GMT)" in n}
    check("dazn_gmt targets exactly the GMT-bearing names",
          {n for n, k in targets.items() if k == "dazn_gmt"} == gmt)
    check("everything else targets the default",
          all(k == default_key for n, k in targets.items() if n not in gmt))

    counts = {}
    for k in targets.values():
        counts[k] = counts.get(k, 0) + 1
    print(f"       targets: {counts}")

    print("\n(2) both profiles render correctly through the real renderer")
    for profile in profiles:
        temp_name = f"{TEMP_PREFIX}{profile.key}"
        try:
            EPGSource.objects.filter(name=temp_name).delete()
            props = dict(ecm_profiles.profile_props(profile))
            EPGSource.objects.create(
                name=temp_name, source_type="dummy", is_active=False,
                refresh_interval=0, priority=0, custom_properties=props)
            temp = EPGSource.objects.get(name=temp_name)

            sample = [n for n, k in targets.items() if k == profile.key][:3]
            check(f"{profile.key}: has names to render", len(sample) > 0)
            for name in sample:
                progs = epg_renderer.generate_dummy_programs(
                    999999, name, num_days=1, program_length_hours=4, epg_source=temp)
                title = progs[0].get("title") if progs else None
                print(f"       {name[:52]}")
                print(f"         -> {str(title)[:72]}")
        finally:
            deleted, _ = EPGSource.objects.filter(name=temp_name).delete()
            print(f"       (temp {temp_name} cleanup: {deleted} row(s))")


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception during verification")

print("\n" + "=" * 70)
print(f"S2_GATE_RESULT={'FAIL' if failures else 'PASS'}")
if failures:
    for f in failures:
        print(f"  - {f}")
print("=" * 70)
sys.exit(1 if failures else 0)
```

- [ ] **Step 3: Run the gate (POWERSHELL)**

```powershell
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
docker cp scripts\verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
$out = docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"
$out
if ($out -notmatch 'S2_GATE_RESULT=PASS') { throw "S2 GATE FAILED" }
```
Expected: `S2_GATE_RESULT=PASS`; dazn_gmt targets 48, the default targets 230; DAZN samples render `Upcoming at … CDT: <title>`; legacy samples render their own extracted titles.

If the gate fails, STOP and report. Do not adjust expectations to match output.

- [ ] **Step 4: Confirm nothing was left behind (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.epg.models import EPGSource
from apps.channels.models import Channel
print('temp sources:', EPGSource.objects.filter(name__startswith='__ecm_s2_verify__').count())
print('DAZN source channels:', Channel.objects.filter(epg_data__epg_source__name='DAZN PPV Dummy (GMT)').count())
"
```
Expected: `0`, and the DAZN count unchanged from before the run.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_s2_incontainer.py
git commit -F /tmp/s2_t7.txt
```
with:
```
feat: read-only in-container gate for S2 routing

Proves attach_targets covers every live channel (none falls through without a
source -- the regression that would blank 126 guide entries), that GMT names
target dazn_gmt and everything else targets the default, and that both profiles'
resolved props render correct local times through Dispatcharr's real renderer via
temporary unbound EPGSources.

No channel is repointed and no real pass is run.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 8: Live acceptance — deploy and watch a real pass

**This is the only task that changes live behavior.** Everything before it is additive code and read-only proof.

- [ ] **Step 1: Backup (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.backups.tasks import create_backup_task
print(create_backup_task.apply().result)
"
```

- [ ] **Step 2: Record the pre-deploy state (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.channels.models import Channel
from apps.epg.models import EPGSource
for s in EPGSource.objects.filter(source_type='dummy').order_by('id'):
    print(s.id, s.name, '| channels', Channel.objects.filter(epg_data__epg_source=s).count())
"
```
Record the numbers. `DAZN PPV Dummy (GMT)` should show 99.

- [ ] **Step 3: Deploy (POWERSHELL)**

```powershell
docker cp Event-Channel-Managarr\plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/data/plugins/event-channel-managarr/ecm_profiles.py
docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/event-channel-managarr
docker restart dispatcharr
```

Wait for the container to report healthy before continuing.

- [ ] **Step 4: Run a DRY RUN first (POWERSHELL)**

Trigger the plugin's dry-run action from the Dispatcharr UI, or via the plugin manager. Read the log line `[dry-run] Managed EPG would attach N, detach M across K source(s)`.

Expected: `K = 2`. **If `detach` is larger than a handful, STOP** — S2 is attach-only and a large detach count means something is wrong.

- [ ] **Step 5: Run a real pass and verify (POWERSHELL)**

After the applied run, re-run the Step 2 query.

Expected: `DAZN PPV Dummy (GMT)` still holds its DAZN channels (now ECM-managed), `ECM Managed Dummy` holds the rest, and the totals reconcile with the Step 2 numbers.

Then confirm the guide:
```powershell
docker exec dispatcharr python manage.py shell -c "
import logging; logging.disable(logging.CRITICAL)
from apps.channels.models import Channel
from apps.output import epg as E
c = Channel.objects.select_related('epg_data__epg_source').filter(name__startswith='Next |').first()
s = c.epg_data.epg_source
p = E.generate_dummy_programs(c.id, c.name, num_days=1, program_length_hours=4, epg_source=s)
print('bound to:', s.name)
print('renders :', p[0].get('title') if p else None)
"
```
Expected: bound to `DAZN PPV Dummy (GMT)`, rendering a converted local time.

- [ ] **Step 6: THE ACCEPTANCE TEST — survive a reclaim trigger**

This is what S2 exists for. Force the condition that undid the manual fix:

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.m3u.models import M3UAccount
from apps.m3u.tasks import refresh_single_m3u_account
a = M3UAccount.objects.filter(is_active=True, name__icontains='[redacted-provider-host]').first()
print('refreshing', a.id, a.name)
refresh_single_m3u_account(a.id)
"
```

Then wait for ECM's `on_m3u_refresh` rescan to complete and re-run the Step 5 verification.

**Expected: the DAZN channels are STILL on the GMT source and STILL rendering local times.** Under the pre-S2 code this is exactly the point at which they were reclaimed onto the ET source.

If they were reclaimed, S2 has not achieved its goal — report BLOCKED with the log output rather than patching around it.

- [ ] **Step 7: Record the outcome**

Append the result to `.wolf/memory.md`, and update the spec's failure-mode scorecard (§6) to mark mode 2 covered ONLY if Step 6 passed.

---

## Definition of Done

- [ ] `python -m pytest tests/ -q` fully green
- [ ] `test_detach_contract_is_frozen` passes — `keep_ids` is unchanged
- [ ] The in-container gate printed `S2_GATE_RESULT=PASS`
- [ ] A dry run reports `across 2 source(s)` with a small detach count
- [ ] After a real pass, DAZN channels are on the GMT source rendering local times
- [ ] **After a forced M3U refresh, they are STILL there** — the acceptance test
- [ ] No temp EPGSource rows remain
