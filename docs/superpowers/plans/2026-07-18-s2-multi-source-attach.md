# S2 — Multi-Source Managed Dummy EPG (attach-only) Implementation Plan — rev 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ECM manage more than one dummy EPGSource and attach each channel to the one whose source timezone matches its provider family — ending the reclaim that un-fixes the DAZN guide every few hours.

**Architecture:** `ecm_profiles` resolves the frozen profile template against live settings and decides each channel's target source. `plugin.py` gains lazy per-profile source creation and a routed attach. **Detach is not modified in any way** — not the formula, not the scope, not the number of sources it runs against.

**Tech Stack:** Python 3, Django ORM (inside Dispatcharr), pytest, Docker.

> **rev 2 — revised after four adversarial reviews of rev 1, which had FIVE Criticals**, three of which would first have executed on production. Sections marked **[REV1 WAS WRONG]** correct a specific error. Read those first if you reviewed rev 1.

## Global Constraints

- **`ecm_profiles.py` stays STDLIB-ONLY**; only non-stdlib import is `regex` inside `try/except ImportError`. No module-level mutable state.
- **DETACH IS UNTOUCHED.** Do not change `keep_ids`. Do not change `_detach_managed_epg`. **Do not loop detach over the new sources** — see the scope section below; rev 1 did this and it would have nulled 94 live channels.
- Patterns are STORED in JS named-group form `(?<name>)` (issue #21).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Commit messages via Bash heredoc + `git commit -F`.
- PowerShell 5.1 **cannot parse `<` redirection** and parses a whole block before executing, so a stray `<` also silently skips preceding `docker cp` lines. Put redirection inside the container's `sh -c`.
- `docker exec` writing under `/data` uses `-u dispatch`.
- Repo: `C:\Users\User\docker\Event-Channel-Managarr`. Branch `feat/s2-multi-source` (exists).
- Code map (authoritative line numbers): `.superpowers/sdd/s2-code-map.md`.

## What rev 1 got wrong — the four scope changes

**[REV1 WAS WRONG] 1. "Detach is frozen" was false.** Rev 1 froze the `keep_ids` *formula* and then looped detach over every managed source. Adopting source 42 makes 99 previously-untouched channels detachable for the first time; **94 of them are currently hidden**, so the first applied pass would null their `epg_data` in one sweep. A contract test that greps a string literal cannot see runtime scope.
→ **S2 does not touch detach at all.** Teardown and the detach call stay exactly as they are, against the default source only. With `manage_dummy_epg` off, non-default sources' channels stay bound — a documented S2 limitation, and the *additive* failure. Multi-source teardown is S3's job, which is where the spec put it.

**[REV1 WAS WRONG] 2. Unclaimed names caused a move.** Rev 1 routed every unclaimed name to the default source. An idle DAZN slot (`NO EVENT STREAMING NOW …`, no `(GMT)`) would therefore be pulled from the GMT source to the ET source, and pushed back when it went live — re-creating the reclaim through the fix itself.
→ **Unclaimed names are STICKY.** Only a *positive* claim moves a channel. A channel already on a managed source whose name matches nothing stays where it is. Unclaimed channels that are *unbound* attach to the default, preserving today's behavior for them.

**[REV1 WAS WRONG] 3. Sources were created unconditionally.** Every marketplace install would get an empty `DAZN PPV Dummy (GMT)` row carrying a hardcoded `CDT` literal, with no cleanup path.
→ **Non-default sources are created lazily**, only when a live channel actually routes to them.

**[REV1 WAS WRONG] 4. `_stock_patterns` was unimplementable and the union was incomplete.** The block rev 1 said to "move verbatim" closes over six locals (`us_title_pattern`, `se_title_pattern`, …) — a guaranteed `NameError` on every applied pass, uncaught by any gate. Separately, the union contains no DAZN-derived entries, so the adopted DAZN source's patterns read as "user-customized" and can **never** auto-upgrade — the exact freeze the mechanism exists to prevent, reproduced for the new family.
→ Sources current defaults from `ecm_profiles`, keeps historical literals local, treats **a profile's own current pattern as stock**, and is covered by a test that CALLS it.

## File Structure

| Path | Responsibility | Change |
|---|---|---|
| `Event-Channel-Managarr/ecm_profiles.py` | `build_profiles`, `resolve_output_timezone`, `attach_targets` (sticky) | Modify |
| `Event-Channel-Managarr/plugin.py` | Import; lazy per-profile sources; routed attach. **No detach changes.** | Modify |
| `tests/unit/test_ecm_profiles.py` | `build_profiles`, `resolve_output_timezone` | Modify |
| `tests/unit/test_s2_routing_semantics.py` | Sticky attach-target semantics | Create |
| `tests/contract/test_s2_plugin_wiring.py` | AST-structural guards (not string greps) | Create |
| `scripts/verify_s2_incontainer.py` | In-container gate incl. a **rolled-back real pass** | Create |

---

## Task 1: `build_profiles` + `resolve_output_timezone`

**Files:** Modify `Event-Channel-Managarr/ecm_profiles.py`, `tests/unit/test_ecm_profiles.py`

**Interfaces produced:**
- `build_profiles(settings) -> tuple[Profile, ...]`
- `resolve_output_timezone(source_tz_name, system_tz_name, date_format="Auto") -> dict`

**[REV1 WAS WRONG]** rev 1 changed `_localized_template_props`'s signature but added **no test for the new branch** — the silent 5-hour error it exists to prevent had zero automated assertion anywhere. Extracting the pure computation makes it unit-testable.

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


def test_build_profiles_se_format_swaps_the_default_pattern_trio():
    built = ecm_profiles.build_profiles({"dummy_epg_channel_format": "SE"})
    default = next(p for p in built if p.is_default)
    assert default.title_pattern == ecm_profiles.SE_TITLE_PATTERN
    assert default.key == "se"


def test_build_profiles_se_default_still_leaves_dazn_its_family():
    """SE's selector is pipe-based and every DAZN name is pipe-delimited. As the
    DEFAULT it is evaluated last, so dazn_gmt keeps its own family. As a
    NON-default ahead of dazn_gmt it would claim 99 and leave dazn_gmt with ZERO."""
    built = ecm_profiles.build_profiles({"dummy_epg_channel_format": "SE"})
    names = _fixture_names()
    assert set(ecm_profiles.route(names, profiles=built)["dazn_gmt"]) == \
        {n for n in names if "(GMT)" in n}


@pytest.mark.parametrize("fmt", ["US", "SE", "", "garbage"])
def test_build_profiles_output_is_always_routable(fmt):
    built = ecm_profiles.build_profiles({"dummy_epg_channel_format": fmt})
    assert sum(1 for p in built if p.is_default) == 1
    ecm_profiles.route(["PPV EVENT 01: X"], profiles=built)   # must not raise


# --- resolve_output_timezone --------------------------------------------------

def test_resolve_output_timezone_converts_and_labels():
    """THE assertion S2's timezone plumbing exists for. A GMT source displayed in
    Chicago must carry output_timezone=America/Chicago and a real abbreviation --
    if it inherits the ET source's config instead, every DAZN time is 5h wrong."""
    got = ecm_profiles.resolve_output_timezone("UTC", "America/Chicago")
    assert got["output_timezone"] == "America/Chicago"
    assert "{starttime}" in got["upcoming_title_template"]
    assert got["upcoming_title_template"].rstrip().endswith(": {title}")


def test_resolve_output_timezone_same_zone_uses_plain_templates():
    got = ecm_profiles.resolve_output_timezone("America/Chicago", "America/Chicago")
    assert got["output_timezone"] == "America/Chicago"
    assert got["upcoming_title_template"] == "Upcoming at {starttime}: {title}"


def test_resolve_output_timezone_blank_source_never_blanks_output():
    """Branch that produced output_timezone='' in the single-source code. A blank
    output_timezone makes the renderer emit the SOURCE's local time unconverted."""
    got = ecm_profiles.resolve_output_timezone("", "America/Chicago")
    assert got["output_timezone"] in ("", "America/Chicago")
    assert set(got) == {"output_timezone", "title_template",
                        "upcoming_title_template", "ended_title_template"}


def test_resolve_output_timezone_bad_zone_degrades_without_raising():
    got = ecm_profiles.resolve_output_timezone("Not/AZone", "America/Chicago")
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

Change the dataclasses import at the top of `ecm_profiles.py`:
```python
from dataclasses import dataclass, replace
```

Append to `ecm_profiles.py`:

```python
SE_TITLE_PATTERN = r"\|\s*(?<title>[^|]+?)\s*\|"
SE_TIME_PATTERN = r"(?<hour>\d{1,2}):(?<minute>\d{2})"
SE_DATE_PATTERN = (
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?<day>\d{1,2})\s+"
    r"(?<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b"
)

DEFAULT_EVENT_TIMEZONE = "US/Eastern"
DEFAULT_DURATION_HOURS = 3

_PLAIN_TEMPLATES = {
    "title_template": "{title}",
    "upcoming_title_template": "Upcoming at {starttime}: {title}",
    "ended_title_template": "Ended at {endtime}: {title}",
}


def resolve_output_timezone(source_tz_name, system_tz_name, date_format="Auto"):
    """Decide output_timezone and the title templates for ONE source.

    Pure: the caller supplies both zone NAMES; this never reads settings or the
    ORM. Extracted so the behavior can be asserted in a unit test -- a wrong
    result here is a silent multi-hour error in every rendered programme title,
    and in the single-source code it had no test at all.

    Returns the same 4 keys on every path so callers can .update() blindly.
    """
    from datetime import datetime           # stdlib, function-local (purity rule)
    try:
        from zoneinfo import ZoneInfo
    except ImportError:                      # pragma: no cover
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

    PROFILES carries provider FACTS (selectors, patterns, dazn_gmt's UTC). This
    resolves what a USER controls, which must keep working exactly as it did
    under the single-source code:
        dummy_epg_event_timezone       -> the DEFAULT profile's source timezone
        dummy_epg_event_duration_hours -> every profile's block length
        dummy_epg_channel_format=SE    -> swaps the DEFAULT's pattern trio
    """
    settings = settings or {}
    duration = _resolve_duration_minutes(settings.get("dummy_epg_event_duration_hours"))
    tz = str(settings.get("dummy_epg_event_timezone") or "").strip() or DEFAULT_EVENT_TIMEZONE
    is_se = str(settings.get("dummy_epg_channel_format") or "").strip().upper() == "SE"

    out = []
    for profile in PROFILES:
        if not profile.is_default:
            out.append(replace(profile, program_duration_minutes=duration))
        elif is_se:
            out.append(replace(
                profile, key="se", title_pattern=SE_TITLE_PATTERN,
                time_pattern=SE_TIME_PATTERN, date_pattern=SE_DATE_PATTERN,
                selector=r"\|", timezone=tz, program_duration_minutes=duration))
        else:
            out.append(replace(profile, timezone=tz, program_duration_minutes=duration))
    return tuple(out)
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/unit/test_ecm_profiles.py tests/contract/test_module_purity.py -v`
Expected: all pass. `datetime`/`zoneinfo` are stdlib and imported inside the function, so the purity guards hold.

- [ ] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/ecm_profiles.py tests/unit/test_ecm_profiles.py
git commit -F /tmp/s2_t1.txt
```
```
feat: build_profiles and resolve_output_timezone

build_profiles resolves the frozen template against live settings so the existing
dummy_epg_event_timezone, _duration_hours and _channel_format keep governing the
default profile exactly as they did under the single-source code. dazn_gmt's UTC
is never resolved from settings -- it is a fact about the provider's data.

resolve_output_timezone extracts the output-timezone/template computation as a
pure function so it can be asserted. In the single-source code this logic had no
test at all, and a wrong result is a silent multi-hour error in every rendered
programme title.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 2: Sticky attach targets

**Files:** Modify `Event-Channel-Managarr/ecm_profiles.py`, create `tests/unit/test_s2_routing_semantics.py`

**Interfaces produced:**
- `attach_targets(names, profiles, current_by_name=None) -> dict[str, str]`

**[REV1 WAS WRONG]** rev 1 sent every unclaimed name to the default source. On live data that pulls idle DAZN slots off the GMT source onto ET, and pushes them back when they go live — the reclaim, re-created by the fix. **Only a positive claim moves a channel.**

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_s2_routing_semantics.py
"""Attach-target semantics.

Two rules, both load-bearing:

1. A name no selector claims does NOT cause a move. If the channel already sits on
   a managed source it stays there ("sticky"). Without this, an idle DAZN slot
   (NO EVENT STREAMING NOW..., no "(GMT)") is pulled to the ET source and pushed
   back when it goes live -- the exact reclaim S2 exists to end, re-created by S2.

2. A name no selector claims that is NOT already managed attaches to the DEFAULT,
   reproducing the single-source behavior where every eligible channel was managed
   regardless of whether a pattern matched (unmatched names render the fallback).
"""

from pathlib import Path

import pytest

import ecm_profiles

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"


def _names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _profiles():
    return ecm_profiles.build_profiles({})


def test_unbound_unclaimed_names_target_the_default():
    profiles = _profiles()
    default = next(p.key for p in profiles if p.is_default)
    targets = ecm_profiles.attach_targets(["NO EVENT STREAMING NOW - | US: DAZN PPV 50"],
                                          profiles, current_by_name={})
    assert targets["NO EVENT STREAMING NOW - | US: DAZN PPV 50"] == default


def test_unclaimed_name_already_on_a_source_STAYS_there():
    """THE STICKY RULE. Rev 1 moved this channel to the default and back on every
    lifecycle transition."""
    name = "NO EVENT STREAMING NOW - | 8K EXCLUSIVE | US: DAZN PPV 50"
    targets = ecm_profiles.attach_targets([name], _profiles(),
                                          current_by_name={name: "dazn_gmt"})
    assert targets[name] == "dazn_gmt"


def test_a_positive_claim_always_wins_over_stickiness():
    """A GMT-tagged name sitting on the ET source MUST move -- that is the repoint
    S2 exists to perform."""
    name = "Next | Foo vs Bar | League | 2026-07-18 | 14:15 (GMT) | US: DAZN PPV 9"
    targets = ecm_profiles.attach_targets([name], _profiles(),
                                          current_by_name={name: "us_et"})
    assert targets[name] == "dazn_gmt"


def test_every_name_gets_a_target():
    names = _names()
    targets = ecm_profiles.attach_targets(names, _profiles(), current_by_name={})
    assert set(targets) == set(names) and all(targets.values())


def test_claimed_gmt_names_target_dazn():
    names = _names()
    targets = ecm_profiles.attach_targets(names, _profiles(), current_by_name={})
    assert all(targets[n] == "dazn_gmt" for n in names if "(GMT)" in n)


def test_target_counts_on_the_real_corpus_when_nothing_is_bound():
    names = _names()
    targets = ecm_profiles.attach_targets(names, _profiles(), current_by_name={})
    counts = {}
    for k in targets.values():
        counts[k] = counts.get(k, 0) + 1
    assert counts == {"dazn_gmt": 48, "us_et": 230}


def test_stickiness_does_not_invent_a_profile():
    """A stale binding to a profile that no longer exists must fall back, not
    produce a target nothing can satisfy."""
    name = "NO EVENT STREAMING NOW - | US: DAZN PPV 50"
    profiles = _profiles()
    default = next(p.key for p in profiles if p.is_default)
    targets = ecm_profiles.attach_targets([name], profiles,
                                          current_by_name={name: "removed_profile"})
    assert targets[name] == default


def test_attach_targets_requires_a_default_profile():
    only = tuple(p for p in _profiles() if not p.is_default)
    with pytest.raises(ValueError, match="default"):
        ecm_profiles.attach_targets(["x"], only, current_by_name={})


def test_no_enabled_name_should_be_unclaimed_is_detectable():
    """Guard for a real hazard: ECM's hide-rule engine and the profile selectors
    are two independent regex systems. If the hide rules SHOW a DAZN event whose
    name the selector does not claim, that channel renders ET-interpreted times.
    unclaimed_names() lets the caller alarm on exactly that."""
    names = _names()
    unclaimed = ecm_profiles.unclaimed_names(names, _profiles())
    assert isinstance(unclaimed, set)
    assert not (unclaimed & {n for n in names if "(GMT)" in n})
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_s2_routing_semantics.py -v`
Expected: FAIL — no attribute `attach_targets`.

- [ ] **Step 3: Implement**

Append to `ecm_profiles.py`:

```python
def unclaimed_names(names, profiles):
    """Names no profile SELECTOR claims. Exposed so callers can alarm on an
    enabled channel landing here -- ECM's hide rules and these selectors are
    independent regex systems and can disagree about whether an event is live."""
    return set(route(names, profiles=profiles)[UNCLAIMED])


def attach_targets(names, profiles, current_by_name=None):
    """Map each channel NAME to the profile key whose source should hold it.

    `current_by_name` maps name -> the profile key of the source the channel is
    CURRENTLY on (omit or pass {} for unbound channels).

    Rules:
      - a positive selector claim always wins; that is the repoint S2 performs
      - an unclaimed name that is already on a known managed profile STAYS there
        (sticky) -- moving it would shuttle idle slots between sources
      - an unclaimed name that is unbound (or on an unknown profile) goes to the
        default, reproducing the single-source behavior

    Raises ValueError without a default profile: an unclaimed unbound name would
    otherwise have no home, and silently dropping it blanks a live guide.
    """
    default = next((p for p in profiles if p.is_default), None)
    if default is None:
        raise ValueError("attach_targets requires exactly one default profile")

    valid = {p.key for p in profiles}
    current_by_name = current_by_name or {}
    routed = route(names, profiles=profiles)

    targets = {}
    for key, bucket in routed.items():
        for name in bucket:
            if key != UNCLAIMED:
                targets[name] = key                       # positive claim wins
                continue
            held = current_by_name.get(name)
            targets[name] = held if held in valid else default.key
    return targets
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/ecm_profiles.py tests/unit/test_s2_routing_semantics.py
git commit -F /tmp/s2_t2.txt
```
```
feat: sticky attach targets -- only a positive claim moves a channel

An earlier draft sent every unclaimed name to the default source. On live data
that pulls idle DAZN slots (NO EVENT STREAMING NOW..., no "(GMT)") off the GMT
source onto the ET source, and pushes them back when they go live -- the exact
reclaim S2 exists to end, re-created by S2 itself.

Now: a positive selector claim always wins, an unclaimed name already on a managed
source stays put, and an unclaimed UNBOUND name goes to the default (preserving
the single-source behavior where every eligible channel was managed regardless of
whether a pattern matched).

unclaimed_names() is exposed so the in-container gate can alarm when an ENABLED
channel lands unclaimed -- ECM's hide rules and these selectors are independent
regex systems and can disagree about whether an event is live.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 3: `_stock_patterns` — extractable, profile-aware, and actually called by a test

**Files:** Modify `Event-Channel-Managarr/plugin.py`, create `tests/contract/test_s2_plugin_wiring.py`

**[REV1 WAS WRONG] twice.** (a) The block rev 1 said to move verbatim closes over six locals — a guaranteed `NameError` on every applied pass, and rev 1's AST-only tests would have passed on the broken version. (b) The union has no DAZN-derived entries, so the adopted DAZN source's patterns read as user-customized and can never auto-upgrade.

- [ ] **Step 1: Add the import**

At `plugin.py` line 44, below `import ecm_parsing`:
```python
import ecm_profiles
```

- [ ] **Step 2: Write the failing test — it must CALL the method**

```python
# tests/contract/test_s2_plugin_wiring.py
"""Structural + runtime guards for S2's plugin.py wiring.

plugin.py imports Django at module scope and cannot be imported outside the
container, so structure is checked with ast. But a purely structural test cannot
catch a NameError, so _stock_patterns -- which an earlier draft extracted in a way
that closed over six locals it no longer had -- is exercised by RUNNING it with a
stub self, not by grepping for its name.
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


def _tree():
    return ast.parse(_source(), filename=str(PLUGIN_PY))


def _fn(name):
    return next((n for n in ast.walk(_tree())
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)


def _extract_callable(name):
    """Compile ONE method out of plugin.py and bind it to a stub, so it can be
    executed without importing Django."""
    src = _source()
    tree = ast.parse(src)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"ecm_profiles": ecm_profiles, "re": re}
    exec(compile(module, str(PLUGIN_PY), "exec"), ns)
    return ns[name]


def test_ecm_profiles_is_imported():
    assert re.search(r"^import ecm_profiles$", _source(), re.M)


def test_stock_patterns_runs_without_closing_over_locals():
    """THE regression test. An earlier draft's extraction referenced
    us_title_pattern/se_title_pattern/... which are locals of a DIFFERENT method,
    producing a guaranteed NameError on every applied pass that no AST test could
    see."""
    fn = _extract_callable("_stock_patterns")
    result = fn(types.SimpleNamespace(), {})
    assert set(result) == {"title_pattern", "time_pattern", "date_pattern"}
    assert all(isinstance(v, set) and v for v in result.values())


def test_stock_patterns_retains_every_historical_default():
    """Dropping a historical entry makes every pre-bug-051 install read as
    user-customized and freezes its patterns permanently."""
    fn = _extract_callable("_stock_patterns")
    titles = fn(types.SimpleNamespace(), {})["title_pattern"]
    assert len(titles) >= 8, f"title stock set shrank to {len(titles)}"
    assert len(fn(types.SimpleNamespace(), {})["time_pattern"]) >= 5
    assert len(fn(types.SimpleNamespace(), {})["date_pattern"]) >= 4


def test_stock_patterns_includes_every_shipped_profile_pattern():
    """Without this, the adopted DAZN source's own patterns read as
    user-customized and can NEVER receive a future regex fix -- reproducing, for
    the new family, the exact freeze this mechanism exists to prevent."""
    fn = _extract_callable("_stock_patterns")
    stock = fn(types.SimpleNamespace(), {})
    for profile in ecm_profiles.PROFILES:
        assert profile.title_pattern in stock["title_pattern"], profile.key
        assert profile.time_pattern in stock["time_pattern"], profile.key
        assert profile.date_pattern in stock["date_pattern"], profile.key


def test_stock_patterns_has_exactly_one_definition():
    assert _source().count("def _stock_patterns") == 1
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_plugin_wiring.py -v`
Expected: FAIL — `StopIteration` / no `_stock_patterns` found.

- [ ] **Step 4: Implement**

Insert above `_get_or_create_managed_epg_source` (plugin.py:2249). Note it takes NO enclosing locals — every current default comes from `ecm_profiles`, and only the historical literals are local:

```python
    PATTERN_KEYS = ("title_pattern", "time_pattern", "date_pattern")

    def _stock_patterns(self, settings):
        """Every pattern default this plugin has EVER shipped, per pattern key.

        A live pattern equal to any of these is treated as untouched and may be
        upgraded; anything else is treated as user-customized and left alone
        (issue #21).

        Two things are load-bearing:
          - the HISTORICAL entries. Dropping one makes every pre-bug-051 install
            read as customized and freezes its patterns permanently.
          - EVERY shipped profile's own current patterns, including non-default
            profiles. Without them an adopted profile source reads as customized
            and can never receive a future regex fix.

        Takes no enclosing locals: current defaults come from ecm_profiles.
        """
        def _py(p):
            return p.replace("(?<", "(?P<")

        _orig_title = (
            r"(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+\s*[:|\s]\s*(?P<title>.+?)"
            r"(?=\s*\(|\s+\d{1,2}:\d{2}\s*[AaPp][Mm]|$)"
        )
        _orig_time = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp][Mm])"
        _prev_us_title = (
            r"(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+\s*[:|\s]\s*"
            r"(?:(?<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
            r"(?<title>.+?)"
            r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
            r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
        )

        stock = {key: set() for key in self.PATTERN_KEYS}

        # Every profile this plugin ships, in both named-group dialects.
        for profile in ecm_profiles.PROFILES:
            for key in self.PATTERN_KEYS:
                value = getattr(profile, key)
                stock[key].add(value)
                stock[key].add(_py(value))

        # The SE trio (a default-only profile, so not in PROFILES itself).
        for key, value in (("title_pattern", ecm_profiles.SE_TITLE_PATTERN),
                           ("time_pattern", ecm_profiles.SE_TIME_PATTERN),
                           ("date_pattern", ecm_profiles.SE_DATE_PATTERN)):
            stock[key].add(value)
            stock[key].add(_py(value))

        # Historical-only defaults, never emitted by current code.
        us_title = ecm_profiles.US_ET.title_pattern
        stock["title_pattern"].update({
            _py(us_title).replace(r"[:|\-\s]", r"[:|\s]"),
            _orig_title, _prev_us_title, _py(_prev_us_title),
        })
        stock["time_pattern"].add(_orig_time)
        return stock
```

Then in `_get_or_create_managed_epg_source`, replace its inline `PATTERN_KEYS`/`stock_patterns` block (plugin.py:2364-2400) with:
```python
        PATTERN_KEYS = self.PATTERN_KEYS
        stock_patterns = self._stock_patterns(settings)
```

- [ ] **Step 5: Run**

Run: `python -m pytest tests/ -q`
Expected: all pass, including the four runtime `_stock_patterns` tests.

- [ ] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_plugin_wiring.py
git commit -F /tmp/s2_t3.txt
```
```
refactor: extract _stock_patterns, profile-aware, exercised by a runtime test

Two defects from an earlier draft, both of which would have first executed on
production:

An extraction that "moved the block verbatim" would have closed over six locals
belonging to a different method -- a guaranteed NameError on every applied pass.
AST-only tests pass on that broken version, so this one COMPILES the method out of
plugin.py and CALLS it with a stub self.

And the union contained no entries from the DAZN profile, so the adopted DAZN
source's patterns read as user-customized and could never receive a future regex
fix -- reproducing, for the new family, the exact freeze this mechanism exists to
prevent. The set is now built from every shipped profile plus the SE trio plus the
historical-only literals.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 4: Lazy per-profile sources

**Files:** Modify `Event-Channel-Managarr/plugin.py`, `tests/contract/test_s2_plugin_wiring.py`

**Interfaces produced:**
- `_managed_props_for_profile(self, profile, settings) -> dict`
- `_get_or_create_managed_epg_sources(self, settings, logger, needed_keys) -> dict[str, EPGSource]`

**[REV1 WAS WRONG]** rev 1 created every profile's source unconditionally, so every marketplace install with no DAZN content got an empty `DAZN PPV Dummy (GMT)` row containing a hardcoded `CDT` literal, with no cleanup path. `needed_keys` makes non-default creation lazy.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_s2_plugin_wiring.py`:

```python
def test_props_builder_and_lazy_source_factory_exist():
    assert _fn("_managed_props_for_profile") is not None
    assert _fn("_get_or_create_managed_epg_sources") is not None


def test_source_factory_takes_needed_keys():
    """Non-default sources must be created lazily. Creating them unconditionally
    puts an empty, single-box-tuned EPGSource in every marketplace install."""
    fn = _fn("_get_or_create_managed_epg_sources")
    assert "needed_keys" in [a.arg for a in fn.args.args]


def test_props_builder_uses_the_profiles_own_timezone():
    """The GMT source's templates must be computed against UTC, not against the
    global setting that belongs to the default profile."""
    fn = _fn("_managed_props_for_profile")
    src = ast.get_source_segment(_source(), fn)
    assert "profile.timezone" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_plugin_wiring.py -v`
Expected: FAIL on the first two.

- [ ] **Step 3: Implement**

Insert above `_stock_patterns`:

```python
    def _managed_props_for_profile(self, profile, settings):
        """Full EPGSource.custom_properties payload for one profile.

        ecm_profiles.profile_props() omits `managed_by` (identity, not renderer
        config), so it is added here. The profile's OWN timezone drives the
        output-timezone/template computation -- using the global setting would give
        the GMT source the ET source's templates and a multi-hour display error.

        NOTE: this OVERWRITES the title templates that profile_props() supplied.
        That is intended: the abbreviation is computed from the live clock, so the
        stored template self-corrects at the CDT/CST boundary instead of carrying a
        frozen literal. It means an adopted source's stored templates WILL be
        rewritten -- a deliberate correction, not drift.
        """
        props = dict(ecm_profiles.profile_props(profile))
        props["managed_by"] = "event-channel-managarr"
        props.update(ecm_profiles.resolve_output_timezone(
            profile.timezone,
            self._get_system_timezone(settings),
            settings.get("date_format", "Auto")))
        return props

    def _get_or_create_managed_epg_sources(self, settings, logger, needed_keys):
        """Create or refresh a dummy EPGSource for each NEEDED profile.

        The default profile's source is always ensured (it is the fallback for
        every unclaimed, unbound channel). A non-default profile's source is
        created only when `needed_keys` says a live channel actually routes to it,
        so an install with no DAZN content never gets an empty DAZN source.

        Returns {profile_key: EPGSource}. A profile whose source cannot be created
        is OMITTED rather than aborting the pass -- aborting would leave every
        profile's channels unmanaged, which is strictly worse.
        """
        from apps.epg.models import EPGSource

        stock = self._stock_patterns(settings)
        sources = {}
        for profile in ecm_profiles.build_profiles(settings):
            if not profile.is_default and profile.key not in (needed_keys or set()):
                continue
            desired = self._managed_props_for_profile(profile, settings)
            try:
                source, created = EPGSource.objects.get_or_create(
                    name=profile.source_name, source_type="dummy",
                    defaults={"custom_properties": desired, "is_active": True,
                              "refresh_interval": 0})
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} Could not get/create EPG source "
                               f"{profile.source_name!r}: {exc}")
                continue

            if not created:
                current = dict(source.custom_properties or {})
                changed = False
                for key, value in desired.items():
                    if key in self.PATTERN_KEYS:
                        cur = current.get(key)
                        if cur is not None and cur not in stock[key]:
                            continue
                    if current.get(key) != value:
                        current[key] = value
                        changed = True
                if changed:
                    source.custom_properties = current
                    source.save(update_fields=["custom_properties"])
                    logger.info(f"{LOG_PREFIX} Refreshed EPG source {profile.source_name!r}")
            else:
                logger.info(f"{LOG_PREFIX} Created EPG source {profile.source_name!r}")
            sources[profile.key] = source
        return sources
```

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/ -q` — all pass.

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_plugin_wiring.py
git commit -F /tmp/s2_t4.txt
```
```
feat: lazy per-profile managed EPG sources

A non-default profile's source is created only when a live channel actually routes
to it. Creating them unconditionally would put an empty, single-box-tuned
"DAZN PPV Dummy (GMT)" row -- carrying a hardcoded CDT literal -- into every
marketplace install that has no DAZN content, with no cleanup path.

Adoption is by NAME: ecm_profiles.DAZN_GMT.source_name equals the hand-made source
already serving those channels in production, so this adopts that row in place and
its bindings are never broken.

_managed_props_for_profile deliberately overwrites the profile's frozen title
templates with a clock-computed equivalent, so an adopted source self-corrects at
the CDT/CST boundary rather than carrying a stale literal.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 5: Routed attach — with detach left completely alone

**Files:** Modify `Event-Channel-Managarr/plugin.py`, `tests/contract/test_s2_plugin_wiring.py`

**[REV1 WAS WRONG]** rev 1 looped detach over every source. That makes 99 previously-untouched channels detachable, and **94 of them are currently hidden**, so the first applied pass would null their `epg_data` in one sweep. S2 does not touch detach.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_s2_plugin_wiring.py`:

```python
def _calls_in(fn_name, method):
    fn = _fn(fn_name)
    return [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == method]


def test_routed_attach_exists():
    assert _fn("_attach_routed") is not None


def test_attach_runs_before_detach_in_the_real_call_path():
    """Structural, not textual: compares CALL line numbers inside the orchestrator,
    so it cannot pass merely because of where the methods are DEFINED."""
    attach = _calls_in("_run_managed_epg_pass", "_attach_routed")
    detach = _calls_in("_run_managed_epg_pass", "_detach_managed_epg")
    assert attach and detach
    assert max(attach) < min(detach)


def test_detach_is_called_exactly_once_and_not_in_a_loop():
    """S2 is attach-only. Looping detach over the new sources would null the
    epg_data of 94 currently-hidden channels on the first applied pass."""
    fn = _fn("_run_managed_epg_pass")
    detach_calls = [n for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_detach_managed_epg"]
    assert len(detach_calls) == 1
    loops = [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.While))]
    for loop in loops:
        inner = [n for n in ast.walk(loop)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_detach_managed_epg"]
        assert not inner, "detach must not be inside a loop -- S2 is attach-only"


def test_keep_ids_is_assigned_once_and_never_mutated():
    """Structural version of the frozen-detach contract: survives reformatting,
    and unlike a substring check it catches a later `keep_ids |= ...`."""
    fn = _fn("_run_managed_epg_pass")
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "keep_ids" for t in n.targets)]
    assert len(assigns) == 1
    mutations = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in ("update", "add", "discard", "remove", "clear")
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "keep_ids"]
    assert not mutations, "keep_ids must never be mutated after assignment"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/contract/test_s2_plugin_wiring.py -v`
Expected: FAIL on `test_routed_attach_exists` and `test_attach_runs_before_detach_in_the_real_call_path`.

- [ ] **Step 3: Implement `_attach_routed`**

Insert below `_attach_managed_epg` (after plugin.py:2520):

```python
    def _attach_routed(self, channels, sources, targets, logger, settings=None,
                       rate_limiter=None, reroute_ids=None, default_key=None):
        """Attach each channel to the source its target profile owns.

        Groups by target source and delegates each group to _attach_managed_epg,
        so transaction / bulk-update / name-resync behavior stays in one place.

        A channel is bound when it has no epg_data, OR its id is in `reroute_ids`
        and it is not already on its target source. The reroute set is required
        because neither existing path can see such a channel: attach considers only
        `epg_data IS NULL`, and _managed_override_ids excludes every dummy source.

        Each group's write is its own transaction. A failure in one group leaves
        earlier groups committed; the pass is re-entrant, so the next scan
        reconciles. Failures are logged per-group rather than propagating, so one
        bad group cannot skip the detach that follows.
        """
        reroute_ids = set(reroute_ids or ())
        by_source = {}
        for channel in channels:
            key = targets.get(channel.name, default_key)
            source = sources.get(key) or sources.get(default_key)
            if source is None:
                logger.warning(f"{LOG_PREFIX} No managed source for profile {key!r}; "
                               f"skipping channel {channel.id}")
                continue
            by_source.setdefault(source.id, (source, []))[1].append(channel)

        attached = []
        for source, group in by_source.values():
            group_reroute = {
                c.id for c in group
                if c.id in reroute_ids
                and getattr(c.epg_data, "epg_source_id", None) != source.id
            }
            try:
                attached.extend(self._attach_managed_epg(
                    group, source, logger, settings=settings,
                    rate_limiter=rate_limiter, override_ids=group_reroute))
            except Exception as exc:
                logger.error(f"{LOG_PREFIX} Attach failed for source "
                             f"{source.name!r}: {exc}")
        return attached
```

- [ ] **Step 4: Rewrite the applied branch**

Replace plugin.py:2657-2690 (the applied-run attach block). **Leave 2692-2695 — the `keep_ids`/`detach_scope`/`_detach_managed_epg` lines — exactly as they are.**

```python
        profiles = ecm_profiles.build_profiles(settings)
        default_key = next((p.key for p in profiles if p.is_default), None)

        # Which profile currently owns each candidate, so unclaimed names can be
        # sticky rather than being shuttled to the default and back.
        name_to_source = {}
        existing = {s.name: s for s in EPGSource.objects.filter(
            name__in=[p.source_name for p in profiles], source_type="dummy")}
        source_id_to_key = {s.id: p.key for p in profiles
                            for s in [existing.get(p.source_name)] if s}

        attached_ids = []
        if toggle_on:
            no_epg_channels = list(Channel.objects.filter(
                id__in=enabled_channel_ids, epg_data__isnull=True))
            on_managed = list(Channel.objects.filter(
                id__in=enabled_channel_ids,
                epg_data__epg_source_id__in=set(source_id_to_key)
            ).select_related("epg_data"))
            for c in on_managed:
                name_to_source[c.name] = source_id_to_key.get(c.epg_data.epg_source_id)

            candidates = {c.id: c for c in no_epg_channels}
            candidates.update({c.id: c for c in on_managed})
            channels_for_epg = list(candidates.values())

            targets = ecm_profiles.attach_targets(
                [c.name for c in channels_for_epg], profiles,
                current_by_name=name_to_source)

            needed = {k for k in targets.values() if k != default_key}
            sources = self._get_or_create_managed_epg_sources(settings, logger, needed)
            if not sources:
                return [], []

            reroute_ids = {
                c.id for c in on_managed
                if sources.get(targets.get(c.name))
                and c.epg_data.epg_source_id != sources[targets[c.name]].id
            }
            rate_limiter = SmartRateLimiter(
                settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING))
            attached_ids = self._attach_routed(
                channels_for_epg, sources, targets, logger, settings=settings,
                rate_limiter=rate_limiter, reroute_ids=reroute_ids,
                default_key=default_key)
            managed_source = sources.get(default_key)
        else:
            managed_source = existing.get(
                next(p.source_name for p in profiles if p.is_default))
            if managed_source is None:
                return [], []
```

The following lines (`keep_ids = ...`, `detach_scope = ...`, `detached_ids = self._detach_managed_epg(managed_source, ...)`) remain **unchanged**, still operating on the single default `managed_source`.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/ -q` — all pass, including the frozen-detach guards.

```bash
git add Event-Channel-Managarr/plugin.py tests/contract/test_s2_plugin_wiring.py
git commit -F /tmp/s2_t5.txt
```
```
feat: route each channel to its target profile's source

_attach_routed groups channels by target source and delegates to the existing
_attach_managed_epg. Group failures are logged rather than propagated, so one bad
group cannot skip the detach that follows.

Detach is untouched: still one call, still the default source only, still
keep_ids = set(enabled_channel_ids). Looping it over the new sources would have
made 99 previously-untouched channels detachable, 94 of them currently hidden --
nulled in a single sweep on the first applied pass. Multi-source teardown is S3.

Guards are AST-structural rather than substring greps: they compare CALL line
numbers inside the orchestrator (not definition order), assert detach is called
exactly once and never inside a loop, and catch a later mutation of keep_ids that
a text match would miss.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 6: In-container gate, including a rolled-back REAL pass

**Files:** Create `scripts/verify_s2_incontainer.py`

**[REV1 WAS WRONG]** rev 1's gate built temp sources from `profile_props()` directly, bypassing `_managed_props_for_profile` — so it validated a literal that production never writes, and the reroute/attach path's first execution would have been on production.

- [ ] **Step 1: Backup (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.backups.tasks import create_backup_task
print(create_backup_task.apply().result)
"
```

- [ ] **Step 2: Write the script**

```python
# scripts/verify_s2_incontainer.py
"""Read-only proof of S2 against LIVE data, including a REAL pass that is rolled back.

    docker cp Event-Channel-Managarr/ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
    docker cp scripts/verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
    docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"

Proof 3 runs the REAL managed-EPG pass inside transaction.atomic() with
set_rollback(True), scoped to one group. Nothing persists. This is the only way to
exercise the reroute/attach ORM path before production.

Caveat: transaction.on_commit hooks do not fire under rollback, so this verifies
ECM's own logic, not Dispatcharr's commit-time side effects.

EXIT CODE: 0 pass, 1 fail.
"""

import sys
import traceback

sys.path.insert(0, "/tmp")
import ecm_profiles  # noqa: E402

from django.db import transaction  # noqa: E402
from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGSource  # noqa: E402
from apps.plugins.models import PluginConfig  # noqa: E402

GROUP_ID = 1915
failures = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main():
    settings = PluginConfig.objects.get(key="event-channel-managarr").settings or {}
    profiles = ecm_profiles.build_profiles(settings)
    default_key = next(p.key for p in profiles if p.is_default)
    print("profiles: " + ", ".join(
        f"{p.key}(tz={p.timezone}{',default' if p.is_default else ''})" for p in profiles))

    chans = list(Channel.objects.filter(channel_group_id=GROUP_ID)
                 .select_related("epg_data__epg_source"))
    names = [c.name for c in chans]
    print(f"live channels in group {GROUP_ID}: {len(chans)}")

    print("\n(1) no ENABLED channel is unclaimed by every selector")
    # ECM's hide rules and these selectors are independent regex systems. An
    # enabled channel landing unclaimed renders the DEFAULT source's timezone.
    unclaimed = ecm_profiles.unclaimed_names(names, profiles)
    enabled_ids = set(Channel.objects.filter(
        channel_group_id=GROUP_ID,
        channelprofilemembership__enabled=True).values_list("id", flat=True))
    bad = [c.name for c in chans if c.id in enabled_ids and c.name in unclaimed]
    check("no enabled channel is unclaimed", not bad, f"{len(bad)}: {bad[:2]}")

    print("\n(2) sticky targets do not move idle slots off their current source")
    cur = {c.name: (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source
                    else None) for c in chans}
    name_to_key = {}
    for p in profiles:
        for n, sname in cur.items():
            if sname == p.source_name:
                name_to_key[n] = p.key
    targets = ecm_profiles.attach_targets(names, profiles, current_by_name=name_to_key)
    moved = [n for n, k in name_to_key.items()
             if targets[n] != k and "(GMT)" not in n and not n.startswith(("Next |", "End |"))]
    check("no unclaimed name is moved off its source", not moved,
          f"{len(moved)}: {moved[:2]}")
    counts = {}
    for k in targets.values():
        counts[k] = counts.get(k, 0) + 1
    print(f"       targets: {counts}")

    print("\n(3) REAL pass, rolled back")
    try:
        with transaction.atomic():
            from apps.plugins.loader import PluginManager
            inst = PluginManager.get().get_plugin_instance("event-channel-managarr")
            scoped = [c.id for c in chans]
            enabled = [c.id for c in chans if c.id in enabled_ids]
            att, det = inst._run_managed_epg_pass(
                settings, __import__("logging").getLogger("ecm-verify"),
                False, enabled, scoped)
            print(f"       attached={len(att)} detached={len(det)}")
            check("detach count is 0 or small", len(det) <= 5, f"detached {len(det)}")

            gmt_ids = [c.id for c in chans if "(GMT)" in c.name and c.id in enabled_ids]
            after = Channel.objects.filter(id__in=gmt_ids).select_related(
                "epg_data__epg_source")
            check("enabled GMT channels landed on the GMT source",
                  all(c.epg_data and c.epg_data.epg_source.name == "DAZN PPV Dummy (GMT)"
                      for c in after),
                  f"of {len(gmt_ids)}")
            nulled = Channel.objects.filter(id__in=scoped, epg_data__isnull=True).count()
            print(f"       channels with no EPG after pass: {nulled}")
            transaction.set_rollback(True)
    except Exception:
        traceback.print_exc()
        failures.append("real pass raised")


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

- [ ] **Step 3: Run the gate (POWERSHELL)**

```powershell
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
docker cp Event-Channel-Managarr\plugin.py dispatcharr:/tmp/plugin_s2_preview.py
docker cp scripts\verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
$out = docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"
$out
if ($out -notmatch 'S2_GATE_RESULT=PASS') { throw "S2 GATE FAILED" }
```

Note: proof 3 runs the CURRENTLY DEPLOYED plugin code. To exercise the new code it must run after Task 7's deploy — so run this gate TWICE: once now (baseline, current behavior) and once immediately after deploy, before re-arming.

- [ ] **Step 4: Confirm nothing persisted (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.channels.models import Channel
print('DAZN source channels:', Channel.objects.filter(epg_data__epg_source__name='DAZN PPV Dummy (GMT)').count())
print('group 1915 with no EPG:', Channel.objects.filter(channel_group_id=1915, epg_data__isnull=True).count())
"
```
Expected: unchanged from before the run.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_s2_incontainer.py
git commit -F /tmp/s2_t6.txt
```

---

## Task 7: Deploy with the automation DISARMED

**[REV1 WAS WRONG]** rev 1 deployed and restarted with the scheduler and `auto_rescan_on_m3u_refresh` still armed, so a scheduled tick or M3U refresh could make S2's first live execution unsupervised and applied, before the operator reached the dry run.

- [ ] **Step 1: Backup (POWERSHELL)** — as Task 6 Step 1. Record the filename.

- [ ] **Step 2: Snapshot every binding (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
import json
from apps.channels.models import Channel
rows = {str(c.id): (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source else None)
        for c in Channel.objects.filter(channel_group_id=1915).select_related('epg_data__epg_source')}
open('/tmp/s2_before.json','w').write(json.dumps(rows))
print('snapshot rows:', len(rows))
"
```

- [ ] **Step 3: DISARM the automation (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.plugins.models import PluginConfig
c = PluginConfig.objects.get(key='event-channel-managarr')
s = dict(c.settings or {})
print('BEFORE manage_dummy_epg=', s.get('manage_dummy_epg'), 'auto_rescan=', s.get('auto_rescan_on_m3u_refresh'))
s['manage_dummy_epg'] = False
s['auto_rescan_on_m3u_refresh'] = False
c.settings = s
c.save(update_fields=['settings'])
print('DISARMED')
"
```
Record the original values — Step 7 restores them.

- [ ] **Step 4: Deploy (POWERSHELL)**

```powershell
docker cp Event-Channel-Managarr\plugin.py dispatcharr:/data/plugins/event-channel-managarr/plugin.py
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/data/plugins/event-channel-managarr/ecm_profiles.py
docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/event-channel-managarr
docker restart dispatcharr
```
Wait for healthy.

- [ ] **Step 5: Re-run the Task 6 gate** — now exercising the NEW code.
Expected: `S2_GATE_RESULT=PASS`, detach count 0 or small, enabled GMT channels on the GMT source.
**If the gate fails, STOP.** Restore settings (Step 7) and report; the deployed code is inert while disarmed.

- [ ] **Step 6: Re-arm and run ONE supervised pass (POWERSHELL)**

Set `manage_dummy_epg=True` only (leave `auto_rescan_on_m3u_refresh=False`), then trigger a dry run from the UI and read the log. Then run one applied pass.

- [ ] **Step 7: Diff every binding (POWERSHELL)**

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
print('MOVED SOURCE:', len(moved))
for m in moved[:10]: print('  ', m)
"
```
**`LOST EPG` must be 0.** Any non-zero value means channels were detached — stop and restore.

- [ ] **Step 8: Restore `auto_rescan_on_m3u_refresh=True`, then the acceptance test**

Force an M3U refresh and confirm the DAZN channels stay on the GMT source. This is the moment the manual fix was undone.

- [ ] **Step 9: Record the outcome** in `.wolf/memory.md` and update the spec's §6 scorecard — mark mode 2 covered ONLY if Step 8 passed.

---

## Definition of Done

- [ ] `python -m pytest tests/ -q` fully green
- [ ] `_stock_patterns` is exercised by a test that CALLS it
- [ ] Detach is called exactly once, outside any loop, with `keep_ids` unmutated
- [ ] The gate printed `S2_GATE_RESULT=PASS` both before and after deploy
- [ ] The binding diff shows **`LOST EPG = 0`**
- [ ] After a forced M3U refresh, DAZN channels are still on the GMT source
- [ ] `auto_rescan_on_m3u_refresh` and `manage_dummy_epg` restored to their original values
