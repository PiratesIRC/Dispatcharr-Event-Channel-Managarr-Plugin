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
from collections import namedtuple
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
    # Added for issue 29. Both carry defaults so every existing construction of
    # this frozen dataclass, and every dataclasses.replace call, keeps working.
    #
    # user_managed True means the plugin seeds the source once when it CREATES it
    # and never writes to it again, so the operator owns its timezone, duration,
    # patterns and templates from that moment on. That is the whole point of the
    # per-group feature, and it is why such a source cannot carry plugin state in
    # its custom_properties: Dispatcharr's own EPG source editor rebuilds that
    # object from a fixed key list and drops anything it does not know, and there
    # is no longer a rewrite on each run to repair it.
    user_managed: bool = False
    # Casefolded channel group names routed to this profile. Empty on the two
    # profiles defined in code, which select by a regex on the channel NAME.
    group_names: tuple = ()


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

_FALLBACK_DESCRIPTION = "Live event. Guide information is currently unavailable."

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
    time_pattern=r"(?<![\d:])(?<hour>\d{1,2})(?::(?<minute>\d{2}))?\s*(?<ampm>[AaPp][Mm])(?![A-Za-z])",
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


# --- per-group managed sources (issue 29) -------------------------------------
#
# The operator maps a channel GROUP to its own dummy EPGSource, one mapping per
# line, so that groups needing different timezones, durations or title patterns
# stop competing for the single shared source.
#
# These two names cannot be group targets. The first is the shared source every
# unmapped group lives on, so seeding it and then abandoning its properties (the
# lifecycle a mapped source gets) would strand every group that is NOT mapped.
# The second is owned by the code profile above and is selected by a channel-name
# regex, so pointing a group at it would give one source two different owners.
RESERVED_SOURCE_NAMES = ("ECM Managed Dummy", "DAZN PPV Dummy (GMT)")


def parse_group_source_map(raw):
    """Parse the group-to-source setting. Returns (mapping, problems).

    `mapping` is an ordered dict of casefolded group name -> source name EXACTLY
    as typed. The group side is casefolded because group names are matched
    case-insensitively everywhere else in this plugin; the source side is not,
    because EPGSource.name is unique and case-SENSITIVE in Postgres, so altering
    it would create a differently cased duplicate row.

    `problems` is a list of plain-language strings, one per rejected line.

    THIS FUNCTION NEVER RAISES. It runs on the scan path, and a malformed line in
    a settings box must not be able to stop a scan. It is also the gate on the
    reverse move: a non-empty `problems` list suppresses that move for the whole
    run, so that one typo cannot rebind a whole channel group.
    """
    mapping = {}
    problems = []
    if not raw:
        return mapping, problems

    try:
        text = str(raw)
    except Exception:
        return mapping, ["the group to source mapping could not be read as text"]

    # Local, not module level: a module-level mutable is a purity violation per
    # tests/contract/test_module_purity.py.
    reserved = {name.casefold() for name in RESERVED_SOURCE_NAMES}
    sources_seen = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            problems.append(
                f"{line!r} has no equals sign; write it as "
                f"Group Name = Source Name")
            continue

        group_part, _, source_part = line.partition("=")
        group = group_part.strip()
        source = source_part.strip()

        if not group or not source:
            problems.append(
                f"{line!r} is missing the group name or the source name; "
                f"write it as Group Name = Source Name")
            continue

        group_key = group.casefold()
        if group_key in mapping:
            problems.append(
                f"the group {group!r} is mapped more than once; keeping "
                f"{mapping[group_key]!r} and ignoring {source!r}")
            continue

        source_key = source.casefold()
        if source_key in reserved:
            problems.append(
                f"{source!r} is managed by the plugin itself and cannot be a "
                f"group target; choose another source name for {group!r}")
            continue

        first_spelling = sources_seen.get(source_key)
        if first_spelling is not None and first_spelling != source:
            problems.append(
                f"{source!r} differs from {first_spelling!r} only in "
                f"capitalisation; EPG source names are case sensitive, so this "
                f"would create two sources. Ignoring the mapping for {group!r}")
            continue

        sources_seen[source_key] = source
        mapping[group_key] = source

    return mapping, problems


# Prefix on every group profile key. It contains a colon, which no key defined in
# code uses and which cannot appear in the UNCLAIMED sentinel, so a mapped source
# named "us_et" or "__unclaimed__" still produces a distinct key. That matters:
# route() RAISES on a duplicate key or a sentinel collision, and an unhandled
# raise on the scan path is an outage rather than a fail-safe.
GROUP_PROFILE_KEY_PREFIX = "group:"


def group_profile_key(source_name):
    """The routing key for the profile serving `source_name`. Pure."""
    return GROUP_PROFILE_KEY_PREFIX + str(source_name).casefold()


def build_group_profiles(settings):
    """Build one profile per mapped EPG source. Returns (profiles, problems).

    Each profile is a copy of the RESOLVED default profile, so a mapped source is
    seeded with the operator's global timezone and duration plus the shipped US
    patterns, and is then theirs to edit. The global US or SE format choice is not
    carried into the seed; the operator edits patterns in Dispatcharr's own EPG
    source editor anyway, so a seeded pattern is a starting point.

    The selector is empty. compile_pattern returns None for it and both route()
    and claimed_targets() skip a None selector, so a group profile can never claim
    a channel by its NAME. Selection by group happens in routing_destinations.

    `problems` is passed straight through from the parser, because the caller
    gates the reverse move on it: if anything failed to parse, no channel is moved
    back, so one typo cannot rebind a whole group.
    """
    settings = settings or {}
    mapping, problems = parse_group_source_map(settings.get("group_epg_source_map"))
    if not mapping:
        return (), problems

    default = next((p for p in build_profiles(settings) if p.is_default), None)
    if default is None:                                   # pragma: no cover
        return (), problems + ["no default profile to seed a group source from"]

    # Group several groups mapping to one source into a single profile: the unit
    # is the SOURCE, not the group, or two groups sharing a source would produce
    # two profiles with the same key and route() would raise.
    groups_by_source = {}
    for group_key, source_name in mapping.items():
        groups_by_source.setdefault(source_name, []).append(group_key)

    profiles = []
    keys_used = set()
    for source_name, group_keys in groups_by_source.items():
        key = group_profile_key(source_name)
        if key in keys_used:                              # defensive
            problems.append(
                f"two mapped sources produced the same routing key for "
                f"{source_name!r}; ignoring the second")
            continue
        keys_used.add(key)
        profiles.append(replace(
            default,
            key=key,
            source_name=source_name,
            selector="",
            is_default=False,
            user_managed=True,
            group_names=tuple(group_keys)))
    return tuple(profiles), problems


# The three pattern keys are never overwritten once they differ from a default the
# plugin has shipped. That is the issue 21 behaviour and it predates this feature.
PATTERN_PROPERTY_KEYS = ("title_pattern", "time_pattern", "date_pattern")


def source_props_to_write(profile, current_props, desired_props):
    """The properties to store on this source, or None meaning write nothing. Pure.

    THE OWNERSHIP RULE: a `user_managed` source is seeded once when the plugin
    creates it and is never written again, so the operator owns its timezone,
    duration, templates and patterns from that moment on. That is the entire point
    of the per-group feature. Every other profile keeps the existing behaviour, in
    which the plugin restores the properties it owns on each applied run.

    This is a separate pure function rather than a branch inside the database code
    because an inverted comparison here would freeze the SHARED source and rewrite
    every MAPPED one, which is the exact opposite of the feature, and no test that
    reads the source text could tell the two apart.

    Keys the plugin does not own, such as `category` and `channel_logo_url`, are
    carried through untouched, so they belong to the operator on every source.

    THE TITLE AND DESCRIPTION TEMPLATES ARE NOT IN THAT GROUP. An earlier version
    of this docstring said the description templates were never written, and that
    was measured wrong on 2026-09-05: `profile_props` includes
    `fallback_title_template` and `fallback_description_template`, and only
    PATTERN_PROPERTY_KEYS is skipped below, so both templates ARE rewritten on
    every applied run of a source that is not user_managed. The practical effect
    is that changing the shipped default propagates to every installation by
    itself, and equally that an operator's own wording in those two fields is
    replaced on the next run unless the source is user_managed.
    """
    if getattr(profile, "user_managed", False):
        return None

    merged = dict(current_props or {})
    changed = False
    for key, value in (desired_props or {}).items():
        if key in PATTERN_PROPERTY_KEYS:
            continue
        if merged.get(key) != value:
            merged[key] = value
            changed = True
    return merged if changed else None


# What routing needs to know about one channel. Plain data, so this module stays
# free of Django and the decision below stays unit-testable outside the container.
ChannelBinding = namedtuple(
    "ChannelBinding", "id name group_name source_name source_is_plugin_created")


def normalize_group_name(name):
    """Casefolded, edge-stripped group name, or the empty string. Pure.

    Matches the comparison the scan already performs on the data side. NOTE that
    the scan's DATABASE filter uses Django's iexact, which is not identical: the
    two diverge on characters where casefold expands, such as the German sharp s.
    A group can therefore be in scope by the query and unmatched here. Prefer
    plain ASCII group names until one comparison serves all three call sites.
    """
    return str(name or "").strip().casefold()


def routing_destinations(bindings, group_profiles, code_profiles,
                         default_source_name, mapping_is_clean):
    """Decide the EPG source every channel belongs on. Returns {id: source name}.

    A channel appears in the result ONLY when it must move, so every entry is a
    database write and an empty result is a clean run. Absence is the safety
    property, exactly as it is for claimed_targets.

    ONE function serves both directions. A move is "desired is not current",
    whether that carries a channel onto a mapped source or back off one. Computing
    the two separately is how a design ends up moving a channel forward and then
    immediately back, writing twice per channel on every run.

    Precedence: a GROUP mapping the operator typed beats a channel-NAME selector
    shipped in code. Implemented by consulting group profiles first rather than by
    relying on the order of either sequence, because route()'s own docstring
    records that an ordering invariant resting on list order shipped as a silent
    no-op twice.

    Two rules keep the reverse move safe, and both are load bearing:

    - `mapping_is_clean` is False whenever the mapping failed to parse cleanly, and
      no reverse move is produced at all. "This group maps nowhere" is exactly what
      a typo produces, so without this a single malformed line would rebind every
      visible channel of a group and shift its rendered guide times by hours.
    - `source_is_plugin_created` must be True. The plugin only takes a channel back
      off a source it recorded creating. Measured on the live installation: three
      hand-made dummy sources hold five channels between them, and the existing
      reroutability guard returns True for ANY dummy source with no ownership
      check, so this is the only thing standing between this feature and those
      channels.

    A missing `default_source_name` suppresses every reverse move, because moving
    a channel to a source that does not exist would unbind it.
    """
    group_lookup = {}
    for profile in group_profiles or ():
        for group_key in profile.group_names:
            group_lookup.setdefault(group_key, profile.source_name)

    named = [(p, compile_pattern(p.selector))
             for p in (code_profiles or ()) if not p.is_default]

    destinations = {}
    for binding in bindings:
        desired = group_lookup.get(normalize_group_name(binding.group_name))

        if desired is None:
            for profile, selector in named:
                if selector is not None and selector.search(str(binding.name or "")):
                    desired = profile.source_name
                    break

        if desired is None:
            # Nothing claims it. It returns to the shared source only if the plugin
            # put it where it is, and only if the mapping can be trusted this run.
            if (mapping_is_clean and binding.source_is_plugin_created
                    and default_source_name):
                desired = default_source_name
            else:
                continue

        if desired != binding.source_name:
            destinations[binding.id] = desired
    return destinations


def managed_epg_enabled_ids(evaluated, forced_visible_ids, hide_ids, show_ids,
                            duplicate_hide_ids):
    """Return the channel ids that are visible once this scan's decisions apply.

    This set does double duty in the managed dummy EPG pass. It is the set the
    pass ATTACHES the managed source to, and it is the keep-set the DETACH step
    protects. A channel missing from it therefore gets no guide data and also
    has any managed EPG stripped from it on every single run, so an omission
    here is not a missed improvement, it is active damage.

    That is exactly what happened to channels matched by the Regex: Force
    Visible Channels setting (bug-175). The per-channel loop handles them in an
    early branch that returns to the top of the loop before the channel is
    recorded anywhere, so they were absent from the caller's inline version of
    this computation. Measured on the live installation on 2026-09-05: 17
    visible channels with no EPG row at all, and a run reporting zero attaches
    while the managed dummy EPG feature was switched on.

    Arguments:
      evaluated             (channel_id, currently_visible) for every channel
                            that reached a normal visibility decision.
      forced_visible_ids    ids the force-visible regex claimed. These are
                            UNCONDITIONALLY enabled: the setting exists to
                            overrule the hide rules, so no later list may take
                            one back. They are listed separately rather than
                            folded into `evaluated` because the caller's early
                            branch has no visibility decision to report.
      hide_ids              ids this scan decided to hide.
      show_ids              ids this scan decided to show.
      duplicate_hide_ids    ids the duplicate handler decided to hide.

    Order is stable, evaluated first and then forced, and the result never
    repeats an id. Both matter to the caller: the ids flow straight into an
    ORM filter and into the counts the run reports.
    """
    hide = set(hide_ids or ())
    show = set(show_ids or ())
    duplicates = set(duplicate_hide_ids or ())
    forced = list(forced_visible_ids or ())
    forced_set = set(forced)

    enabled = []
    seen = set()
    for channel_id, currently_visible in evaluated or ():
        if channel_id in seen:
            continue
        if channel_id in forced_set:
            # Handled below so a forced channel keeps its forced meaning even if
            # the caller also recorded an ordinary decision for it.
            continue
        if channel_id in duplicates or channel_id in hide:
            continue
        if currently_visible or channel_id in show:
            enabled.append(channel_id)
            seen.add(channel_id)

    for channel_id in forced:
        if channel_id not in seen:
            enabled.append(channel_id)
            seen.add(channel_id)
    return enabled


def managed_epg_detach_scope(scanned_ids, ignored_ids):
    """Return the channel ids the managed EPG pass may take the managed source off.

    The scope exists so that narrowing the Channel Groups setting cannot strip
    the managed source from channels in groups this run never looked at
    (bug-045). It is a restriction, never a permission: a channel inside the
    scope is only detached if it is also absent from the enabled set.

    Channels matched by the Regex: Channels to Ignore setting are removed here.
    The plugin makes no visibility decision for them and never attaches the
    managed source to them, so leaving them in the scope meant it could only
    ever take EPG AWAY from a channel the operator asked it to leave alone.
    Removing them makes ignore mean ignore in both directions, and the failure
    direction is the safe one: an ignored channel keeps whatever it has.
    """
    ignored = set(ignored_ids or ())
    return [channel_id for channel_id in (scanned_ids or ())
            if channel_id not in ignored]
