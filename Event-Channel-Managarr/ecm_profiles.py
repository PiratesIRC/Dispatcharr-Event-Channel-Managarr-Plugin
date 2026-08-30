"""Pure, Django-free timezone-profile definitions for Event Channel Managarr.

WHY THIS EXISTS
---------------
Dispatcharr renders dummy EPG programmes from the CHANNEL NAME using regex
patterns plus a timezone stored on the EPGSource. It reads that timezone ONCE
PER SOURCE (apps/output/epg.py:305) with no per-channel hook. So a channel group
containing provider families in DIFFERENT source timezones cannot be served by a
single dummy EPGSource -- the timezone field itself must differ.

This module decides WHICH profile owns a given channel name. It performs no I/O
and imports nothing outside the stdlib, so it is unit-testable without a
container. See docs/superpowers/specs/2026-07-18-durable-multi-timezone-epg-design.md

CONSTRAINTS
-----------
- stdlib only; no apps.*, django.*, core.utils
- no module-level mutable state: Dispatcharr's loader re-imports this module on
  nearly every streaming event, so caches are wiped and are pointless anyway
  (routing all 278 names measures well under a millisecond)
- patterns are STORED in JS named-group form (?<name>) because Dispatcharr's
  JavaScript frontend validator rejects the Python (?P<name>) form (issue #21)
"""

import re
from dataclasses import dataclass, replace

try:  # Dispatcharr ships `regex`; dev machines generally do not.
    import regex as _re  # accepts (?<name>) natively
    _NEEDS_CONVERSION = False
except ImportError:
    _re = re
    _NEEDS_CONVERSION = True


# Matches a JS named-group opener (?<name> but NOT a lookbehind (?<= or (?<!
# Same guard Dispatcharr's own renderer uses (apps/output/epg.py:357).
_JS_NAMED_GROUP = re.compile(r"\(\?<(?![=!])([^>]+)>")

UNCLAIMED = "__unclaimed__"


@dataclass(frozen=True)
class Profile:
    """One provider name-family and the EPGSource settings it needs."""

    key: str
    source_name: str
    selector: str
    title_pattern: str
    date_pattern: str
    time_pattern: str
    timezone: str
    output_timezone: str
    program_duration_minutes: int
    include_date: bool
    title_template: str
    upcoming_title_template: str
    ended_title_template: str
    fallback_title_template: str
    fallback_description_template: str
    is_default: bool


def to_python_named(pattern):
    """Convert JS named groups (?<n>) to Python (?P<n>), preserving lookbehinds."""
    return _JS_NAMED_GROUP.sub(r"(?P<\1>", pattern)


def compile_pattern(pattern, engine=None, convert=None):
    """Compile a stored pattern. Returns None on failure -- never raises.

    engine/convert are injectable so BOTH dialect branches are testable on a
    machine that has only one of them installed. Production (container) has
    `regex` and takes convert=False; dev machines take convert=True.
    """
    if not pattern:
        return None
    engine = _re if engine is None else engine
    convert = _NEEDS_CONVERSION if convert is None else convert
    candidate = to_python_named(pattern) if convert else pattern
    try:
        return engine.compile(candidate)
    except Exception:
        try:
            return engine.compile(to_python_named(pattern))
        except Exception:
            return None


def profile_props(profile):
    """The EPGSource.custom_properties payload for this profile. Pure."""
    return {
        "timezone": profile.timezone,
        "output_timezone": profile.output_timezone,
        "title_pattern": profile.title_pattern,
        "date_pattern": profile.date_pattern,
        "time_pattern": profile.time_pattern,
        "title_template": profile.title_template,
        "upcoming_title_template": profile.upcoming_title_template,
        "ended_title_template": profile.ended_title_template,
        "program_duration": profile.program_duration_minutes,
        "include_date": profile.include_date,
        "fallback_title_template": profile.fallback_title_template,
        "fallback_description_template": profile.fallback_description_template,
    }


def route(names, profiles=None):
    """Assign each channel name to exactly one profile bucket.

    Non-default profiles are evaluated in declaration order; the default profile
    is evaluated LAST and claims only what nothing else claimed. Names no profile
    claims land in UNCLAIMED.

    THE DEFAULT-LAST RULE IS AN INVARIANT, not an artifact of list order: a broad
    default declared first must still lose to a specific profile. That is how two
    earlier revisions of this design shipped a silent no-op.

    NOTE: this invariant alone is NOT sufficient. Any non-default profile whose
    selector is broad enough to claim another family's names re-creates the same
    failure, which is why every selector is asserted against the real fixture.

    Returns dict[profile_key -> list[name]] plus UNCLAIMED, partitioning `names`
    exactly: every name appears in exactly one bucket, in input order.
    """
    profiles = PROFILES if profiles is None else profiles

    keys = [p.key for p in profiles]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate profile keys would silently merge buckets: {keys}")
    if UNCLAIMED in keys:
        raise ValueError(f"a profile key collides with the {UNCLAIMED!r} sentinel")
    if sum(1 for p in profiles if p.is_default) > 1:
        raise ValueError("more than one default profile; ordering would be ambiguous")

    ordered = [p for p in profiles if not p.is_default] + [p for p in profiles if p.is_default]
    compiled = [(p, compile_pattern(p.selector)) for p in ordered]

    buckets = {p.key: [] for p in profiles}
    buckets[UNCLAIMED] = []

    for name in names:
        for profile, selector in compiled:
            if selector is not None and selector.search(name):
                buckets[profile.key].append(name)
                break
        else:
            buckets[UNCLAIMED].append(name)

    return buckets


# --- The profile chain --------------------------------------------------------
#
# ORDER MATTERS: route() evaluates non-defaults in this order, default last.
#
# There is deliberately NO "se" profile. The SE title pattern is pipe-based
# (\|\s*(?<title>[^|]+?)\s*\|), every DAZN name is pipe-delimited, and an SE
# profile ahead of dazn_gmt claims 99 names while dazn_gmt claims ZERO --
# silently re-creating the very no-op this design exists to fix. SE remains the
# existing global dummy_epg_channel_format behavior, outside the profile chain.

_FALLBACK_DESCRIPTION = "Live event — guide information is currently unavailable."

DAZN_GMT = Profile(
    key="dazn_gmt",
    # PINNED to the live source. Cross-checked against bootstrap_ecm.DAZN_SOURCE_NAME.
    source_name="DAZN PPV Dummy (GMT)",
    selector=r"^(?:Next|End)\s*\|.*\(GMT\)",
    title_pattern=r"^(?:Next|End)\s*\|\s*(?<title>.+?)\s*\|",
    date_pattern=r"\b(?<year>\d{4})-(?<month>\d{1,2})-(?<day>\d{1,2})\b",
    time_pattern=r"\|\s*(?<hour>\d{1,2}):(?<minute>\d{2})\s*\(GMT\)",
    timezone="UTC",
    output_timezone="America/Chicago",
    program_duration_minutes=240,
    include_date=False,
    title_template="{title}",
    # NOTE: the literal " CDT" mirrors the live source exactly so this profile is
    # a faithful capture. It is WRONG for ~5 months a year (America/Chicago is CST
    # in winter). plugin.py computes the abbreviation dynamically via strftime("%Z")
    # precisely to avoid this; the hand-made source did not. Recorded as a known
    # defect of the captured config -- fix it in S2, not by diverging here.
    upcoming_title_template="Upcoming at {month}/{day} {starttime} CDT: {title}",
    ended_title_template="Ended at {month}/{day} {endtime} CDT: {title}",
    fallback_title_template="",
    fallback_description_template=_FALLBACK_DESCRIPTION,
    is_default=False,
)

US_ET = Profile(
    key="us_et",
    # PINNED: plugin.py:2404/2632/2661 all use this literal.
    source_name="ECM Managed Dummy",
    # ANCHORED. The unanchored form (?:PPV|LIVE|EVENT)\s*\d+ also matches the
    # TRAILING slot label "US: DAZN PPV 46" on every DAZN name, which routed all
    # 48 DAZN channels to the ET source. Anchoring drops exactly 51 names, all
    # idle DAZN slots that never should have matched. Both branches are
    # independently ^-anchored (verified: top-level alternation).
    selector=r"^\s*(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+|^\s*EVENT\s*\d+",
    # PINNED to plugin.py's us_title_pattern literal; enforced by
    # tests/contract/test_us_pattern_parity.py, because the renderer reads the
    # plugin.py value and only routing reads this one.
    #
    # The PPV/LIVE/EVENT keyword is OPTIONAL. A provider that names its slots
    # "07 - 8/14 7pm Broncos at Falcons" carries no keyword at all, and the
    # keyword-required form left every such channel on the renderer's static
    # fallback instead of an upcoming or ended title.
    #
    # The leading lookahead is what makes that safe. Accepting a bare number
    # outright would strip the number off ordinary channel names -- "60 Minutes"
    # extracts the title "Minutes" -- which is the failure the keyword
    # requirement was guarding against (bug-051). So a keyword-less name
    # qualifies only when the number is followed by an EXPLICIT separator
    # character and then a date or a clock time. "60 Minutes" has neither and is
    # left alone.
    #
    # The negative lookahead before the slot number stops the match beginning INSIDE an
    # air time. Without it "Boxing 3 : MOSES vs HRGOVIC  4:00pm" matched at the time
    # rather than at the slot number and captured the title "00pm" (bug-146).
    title_pattern=(
        r"(?=(?:PPV|LIVE|EVENT)|"
        r"\d+\s*[:|\-]\s*(?:\d{1,2}[./]\d{1,2}|\d{1,2}(?::\d{2})?\s*[AaPp][Mm]))"
        r"(?:(?:PPV|LIVE)\s*(?:EVENT\s*)?|EVENT\s*)?"
        r"(?!\d{1,2}:\d{2}\s*[AaPp][Mm])\d+\s*[:|\-\s]\s*"
        r"(?:(?<datepart>\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\s+)?"
        r"(?:(?<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
        r"(?<title>.+?)"
        r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
        r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
    ),
    date_pattern=r"\b(?<month>\d{1,2})[./](?<day>\d{1,2})(?:[./](?<year>\d{2,4}))?\b",
    time_pattern=r"(?<hour>\d{1,2})(?::(?<minute>\d{2}))?\s*(?<ampm>[AaPp][Mm])",
    timezone="America/New_York",
    output_timezone="America/Chicago",
    program_duration_minutes=240,
    include_date=False,
    title_template="{title}",
    upcoming_title_template="Upcoming at {starttime}: {title}",
    ended_title_template="Ended at {endtime}: {title}",
    fallback_title_template="",
    fallback_description_template=_FALLBACK_DESCRIPTION,
    is_default=True,
)

PROFILES = (DAZN_GMT, US_ET)


# --- settings and timezone resolution (pure) ------------------------------------

DEFAULT_EVENT_TIMEZONE = "US/Eastern"
DEFAULT_DURATION_HOURS = 3

def resolve_output_timezone(source_tz_name, system_tz_name, date_format="Auto"):
    """Decide output_timezone and title templates for ONE source.

    Pure: the caller supplies both zone NAMES. Extracted so it can be asserted --
    in the single-source code this logic had no test, and a wrong result is a
    silent multi-hour error in every rendered title. NOTE the parameter order is
    (source, system): transposing them raises nothing.
    """
    from datetime import datetime
    # Local, not module-level: a module-level dict literal is mutable state per
    # tests/contract/test_module_purity.py's check_no_module_level_mutable_state.
    _PLAIN_TEMPLATES = {
        "title_template": "{title}",
        "upcoming_title_template": "Upcoming at {starttime}: {title}",
        "ended_title_template": "Ended at {endtime}: {title}",
    }
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
