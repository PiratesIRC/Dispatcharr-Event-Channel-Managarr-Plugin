"""Pure, Django-free parsing helpers for Event Channel Managarr.

This module contains the channel-name date/time extraction logic — the most
bug-prone part of the plugin (see issues #19, #22 and the EU/US date-format
work). It deliberately imports NOTHING from Django or Dispatcharr so it can be
unit-tested on plain CI without a running container.

`plugin.py` imports this module and its `Plugin` methods delegate here, so all
existing call sites keep working unchanged while the logic lives in one testable
place.

Determinism: `extract_date_from_channel_name` accepts an injectable `now` so
tests can pin "today" instead of depending on the wall clock.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta

LOG = logging.getLogger("event_channel_managarr.parsing")

# Single source of truth for the `start:`/`stop:YYYY-MM-DD HH:MM:SS[ AM/PM]`
# event timestamps. Compiled once and shared by the date extractor (Pattern 0)
# and the [PastDate] stop-time check so the two can never drift apart.
EVENT_TS_SUFFIX = r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(?P<ap>[AaPp][Mm])?"
EVENT_TS_RE = {
    "start:": re.compile("start:" + EVENT_TS_SUFFIX),
    "stop:": re.compile("stop:" + EVENT_TS_SUFFIX),
}


def apply_meridiem(hour, meridiem):
    """Convert a 12-hour clock hour to 24-hour given an optional AM/PM token."""
    if not meridiem:
        return hour
    meridiem = meridiem.upper()
    if meridiem == "AM":
        return 0 if hour == 12 else hour
    return hour if hour == 12 else hour + 12


def _attach_clock_time(dt, hh, mm, ap):
    """Return `dt` with an explicit 12-hour clock time applied when one was parsed
    (hour + AM/PM present); otherwise return `dt` unchanged at midnight. Used by the
    numeric M.D / M/D patterns so a trailing '(6.19 7:30 PM ET)' time is preserved
    instead of dropped, letting [PastDate] judge by the real event time (bug-046)."""
    if hh and ap:
        return dt.replace(hour=apply_meridiem(int(hh), ap), minute=int(mm) if mm else 0)
    return dt


def resolve_numeric_date_pair(first, second, current_year, date_format):
    """Resolve a (first, second) numeric pair into a datetime using the configured format.

    date_format: "US" -> MM/DD, "EU" -> DD/MM, "Auto" -> MM/DD with DD/MM
    fallback if month > 12. Returns datetime or None if the pair can't form a
    valid date.
    """
    fmt = (date_format or "Auto").strip()
    if fmt == "EU":
        day, month = first, second
        try:
            return datetime(current_year, month, day)
        except ValueError:
            return None
    if fmt == "US":
        month, day = first, second
        try:
            return datetime(current_year, month, day)
        except ValueError:
            return None
    # Auto: MM/DD first; if month > 12 (or invalid), retry DD/MM.
    try:
        return datetime(current_year, first, second)
    except ValueError:
        try:
            return datetime(current_year, second, first)
        except ValueError:
            return None


def name_has_stop_timestamp(channel_name):
    """True if the channel name carries an explicit `stop:YYYY-MM-DD HH:MM:SS`
    event-end timestamp. [PastDate] uses this to compare the real end time
    rather than just the calendar date (issue #22)."""
    if not channel_name:
        return False
    return bool(EVENT_TS_RE["stop:"].search(channel_name))


def extract_date_from_channel_name(channel_name, date_format="Auto", prefer="start",
                                   now=None, logger=None):
    """Extract a date (with time if present) from a channel name.

    When a name carries both `start:` and `stop:` timestamps, `prefer` selects
    which one Pattern 0 returns: ``"start"`` (default) for "when does it start /
    how far out is it" rules ([FutureDate], [UndatedAge], NoEPG); ``"stop"`` for
    [PastDate] ("has the event ended?", issue #22). Falls back to the other
    prefix when the preferred one is absent, so single-timestamp names are
    unaffected.

    `now` is injectable for deterministic testing (defaults to ``datetime.now()``).
    Returns a ``datetime`` or ``None``.
    """
    log = logger or LOG
    if not channel_name:
        return None
    from dateutil import parser as dateutil_parser

    now = now or datetime.now()
    current_year = now.year
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    date_format = date_format or "Auto"

    # Pattern 0: start:/stop:YYYY-MM-DD HH:MM:SS[ AM/PM].
    # Order by caller preference so [PastDate] can evaluate against stop: (issue #22).
    prefixes = ["stop:", "start:"] if prefer == "stop" else ["start:", "stop:"]
    for prefix in prefixes:
        pattern0 = EVENT_TS_RE[prefix].search(channel_name)
        if pattern0:
            year, month, day, hour, minute, second = map(int, pattern0.groups()[:6])
            hour = apply_meridiem(hour, pattern0.group("ap"))
            try:
                extracted_date = datetime(year, month, day, hour, minute, second)
                log.debug(f"Extracted datetime {extracted_date} from pattern {prefix}YYYY-MM-DD HH:MM:SS[ AM/PM] in '{channel_name}'")
                return extracted_date
            except ValueError:
                pass

    # Pattern 0a: (YYYY-MM-DD HH:MM:SS[ AM/PM]) in parentheses
    pattern0a = re.search(r'\((\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(?P<ap>[AaPp][Mm])?\)', channel_name)
    if pattern0a:
        year, month, day, hour, minute, second = map(int, pattern0a.groups()[:6])
        hour = apply_meridiem(hour, pattern0a.group("ap"))
        try:
            extracted_date = datetime(year, month, day, hour, minute, second)
            log.debug(f"Extracted datetime {extracted_date} from pattern (YYYY-MM-DD HH:MM:SS[ AM/PM]) in '{channel_name}'")
            return extracted_date
        except ValueError:
            pass

    # Pattern 1: M/D/YYYY or M/D/YY — interpreted per date_format setting.
    pattern1 = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', channel_name)
    if pattern1:
        first, second, year = map(int, pattern1.groups())
        if year < 100:
            year += 2000
        extracted_date = resolve_numeric_date_pair(first, second, year, date_format)
        if extracted_date is not None:
            log.debug(f"Extracted date {extracted_date.date()} from pattern M/D/YYYY ({date_format}) in '{channel_name}'")
            return extracted_date

    # Pattern 2c: DDth MONTH e.g., "28th Apr"
    pattern2c = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b', channel_name, re.IGNORECASE)
    if pattern2c:
        day, month_str = pattern2c.groups()
        try:
            temp_date = dateutil_parser.parse(f"{month_str} {day} {current_year}")
            extracted_date = datetime(temp_date.year, temp_date.month, temp_date.day)
            if (today - extracted_date).days > 180:
                extracted_date = datetime(current_year + 1, temp_date.month, temp_date.day)
            log.debug(f"Extracted date {extracted_date.date()} from pattern DDth MONTH in '{channel_name}'")
            return extracted_date
        except (ValueError, dateutil_parser.ParserError):
            pass

    # Pattern 2b: MONTH DD e.g., "Nov 8", "Nov 8 16:00", or "Jun 20 4:00 PM".
    # The time is parsed component-wise (optional :SS, optional AM/PM) and the
    # meridiem applied via apply_meridiem — dateutil is used only for the date so
    # a trailing "PM" can't be silently dropped (bug-047).
    pattern2b = re.search(
        r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})'
        r'(?:\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*(?P<ap>[AaPp][Mm])?)?',
        channel_name, re.IGNORECASE)
    if pattern2b:
        month_str, day = pattern2b.group(1), pattern2b.group(2)
        hh, mm, ap = pattern2b.group(3), pattern2b.group(4), pattern2b.group("ap")
        try:
            temp_date = dateutil_parser.parse(f"{month_str} {day} {current_year}")
            hour = apply_meridiem(int(hh), ap) if hh else 0
            minute = int(mm) if mm else 0
            extracted_date = datetime(temp_date.year, temp_date.month, temp_date.day, hour, minute)
            if (today - extracted_date).days > 180:
                extracted_date = datetime(current_year + 1, temp_date.month, temp_date.day, hour, minute)
            log.debug(f"Extracted date {extracted_date} from pattern MONTH DD[ HH:MM[:SS] AM/PM] in '{channel_name}'")
            return extracted_date
        except (ValueError, dateutil_parser.ParserError):
            pass

    # Pattern 3: M.D without year e.g., "10.25" — interpreted per date_format setting.
    # An optional trailing 12-hour time ("6.19 7:30 PM") is captured and applied so the
    # event's clock time isn't dropped (bug-046).
    pattern3 = re.search(r'\b(\d{1,2})\.(\d{1,2})\b(?:\s+(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm]))?', channel_name)
    if pattern3:
        first, second = int(pattern3.group(1)), int(pattern3.group(2))
        extracted_date = resolve_numeric_date_pair(first, second, current_year, date_format)
        if extracted_date is not None:
            extracted_date = _attach_clock_time(extracted_date, pattern3.group(3), pattern3.group(4), pattern3.group(5))
            log.debug(f"Extracted date {extracted_date} from pattern M.D[ HH:MM AM/PM] ({date_format}) in '{channel_name}'")
            return extracted_date

    # Pattern 4: M/D without year e.g., "10/27" or "15/04" — interpreted per date_format setting.
    # Lookahead excludes "/" (year follows, handled by Pattern 1) and ":" (time
    # range like "1/3:30pm" — second number is hours, not a day). An optional trailing
    # 12-hour time is captured and applied (bug-046).
    pattern4 = re.search(r'\b(\d{1,2})/(\d{1,2})\b(?![/:])(?:\s+(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm]))?', channel_name)
    if pattern4:
        first, second = int(pattern4.group(1)), int(pattern4.group(2))
        extracted_date = resolve_numeric_date_pair(first, second, current_year, date_format)
        if extracted_date is not None:
            extracted_date = _attach_clock_time(extracted_date, pattern4.group(3), pattern4.group(4), pattern4.group(5))
            log.debug(f"Extracted date {extracted_date} from pattern M/D[ HH:MM AM/PM] ({date_format}) in '{channel_name}'")
            return extracted_date

    log.debug(f"No date found in channel name: '{channel_name}'")
    return None


def coerce_timezone(value):
    """Return a valid IANA timezone name, or ``"UTC"`` as a safe fallback.

    Accepts whatever Dispatcharr has stored for its global time zone — ``None``
    (no settings row), blank, non-string, or an invalid name all return ``"UTC"``.
    The returned string is always stripped of surrounding whitespace. pytz is
    imported lazily so importing this module carries no hard pytz dependency.
    """
    if not isinstance(value, str) or not value.strip():
        return "UTC"
    candidate = value.strip()
    try:
        import pytz
        pytz.timezone(candidate)
    except Exception:
        # Catches both pytz.exceptions.UnknownTimeZoneError (bad name) and
        # ImportError (pytz not installed in the current environment).
        return "UTC"
    return candidate


def lock_is_stale(mtime, now, max_age_seconds):
    """Return True if a lock acquired at ``mtime`` is older than ``max_age_seconds``.

    Used to decide whether a held scan flock has been leaked/abandoned (e.g. an
    fd inherited by a forked worker that never released it). A real scan finishes
    in seconds, so a lock far older than any plausible scan is treated as stale
    and may be broken. Boundary is exclusive: age == max_age is NOT stale.

    ``mtime`` and ``now`` are epoch seconds (floats). Non-numeric input returns
    False (fail safe: never break a lock we cannot reason about).
    """
    try:
        return (now - mtime) > max_age_seconds
    except TypeError:
        return False


def parse_scheduled_times(scheduled_times_str):
    """Split a comma-separated HHMM schedule into accepted times and rejected text.

    Returns ``(times, rejects)``. ``times`` holds ``datetime.time`` objects in the
    order given; ``rejects`` holds the raw text of every entry that could not be
    used, so a caller can tell the user which of their run times will never fire.

    The rejected entries used to be discarded here with no record anywhere. A
    schedule of "0600,2400" therefore armed one run time instead of two, and both
    the settings form and the scheduler reported success. ``2400`` is the common
    case: it is four digits, so a check that only tests the shape accepts it, but
    there is no hour 24 and midnight is written ``0000``.
    """
    times = []
    rejects = []
    if not scheduled_times_str or not scheduled_times_str.strip():
        return times, rejects

    for time_str in scheduled_times_str.split(","):
        time_str = time_str.strip()
        if not time_str:
            continue
        if len(time_str) == 4 and time_str.isdigit():
            hour = int(time_str[:2])
            minute = int(time_str[2:])
            if 0 <= hour < 24 and 0 <= minute < 60:
                times.append(datetime.strptime(time_str, "%H%M").time())
                continue
        rejects.append(time_str)
    return times, rejects


# Default lengths for the two "this name is too short to be an event" rules.
# They are the values those rules used when the numbers were written into the
# rule bodies, so a bare [ShortDescription] or [ShortChannelName] tag behaves
# exactly as it always has.
SHORT_DESCRIPTION_DEFAULT = 15
SHORT_CHANNEL_NAME_DEFAULT = 25

# A colon only separates a name from its description when whitespace follows it.
# Without that lookahead a clock time ("LIVE 10:30") reads as a separator.
_COLON_SEPARATOR_RE = re.compile(r":(?=\s)(.+)$")
_PIPE_SEPARATOR_RE = re.compile(r"\|(.+)$")
_DASH_SEPARATOR_RE = re.compile(r"\s-\s*(.*)$")
_DASH_PRESENT_RE = re.compile(r"\s-\s")


def short_description_match(channel_name, threshold=SHORT_DESCRIPTION_DEFAULT):
    """Return (separator, length) when the text after a separator is too short.

    Returns None when the name has no separator, or when the description after
    every separator present is at least ``threshold`` characters long.

    The threshold used to be written into the rule body as a bare 15, so a
    channel called "NCAAF 25: FS1 [1080p]" was hidden (11 characters after the
    colon) while "NCAAF 26: SEC NETWORK [1080p]" stayed visible (19), and there
    was no way to move the line except to stop using the rule. Callers pass the
    number from the rule tag, for example [ShortDescription:20].
    """
    for label, pattern in (("colon", _COLON_SEPARATOR_RE),
                           ("pipe", _PIPE_SEPARATOR_RE),
                           ("dash", _DASH_SEPARATOR_RE)):
        match = pattern.search(channel_name or "")
        if match:
            length = len(match.group(1).strip())
            if length < threshold:
                return label, length
    return None


def short_channel_name_match(channel_name, threshold=SHORT_CHANNEL_NAME_DEFAULT):
    """Return the normalized length when a separator-less name is too short.

    Returns None when the name carries any separator at all, or when it is at
    least ``threshold`` characters long. Whitespace is collapsed first so runs
    of spaces and tabs do not inflate the measurement.
    """
    normalized = re.sub(r"\s+", " ", (channel_name or "").strip())
    if (_COLON_SEPARATOR_RE.search(normalized)
            or _PIPE_SEPARATOR_RE.search(normalized)
            or _DASH_PRESENT_RE.search(normalized)):
        return None
    if len(normalized) < threshold:
        return len(normalized)
    return None


# --- undated event windows ------------------------------------------------------
#
# A channel name carrying a clock time but no date, such as
# "Boxing 3 : MOSES vs HRGOVIC  4:00pm". [UndatedAge:N] can only count whole
# calendar days for such a name, so it hides a late event at midnight and keeps a
# finished one until the next day. These two helpers build the real window instead:
# the date the channel was first seen, the time read from the name, the programme
# duration, and a grace period for an event that overruns (issue 28).

# The way a US event channel name writes a clock time: an hour, an optional
# :minute, and an am or pm marker. The marker is REQUIRED here so a bare slot
# number, such as the 07 in "PPV 07 - Main Card", is not read as 7 o'clock.
#
# Both guards are load-bearing. Without the trailing (?![A-Za-z]) the marker matches
# the opening letters of an ordinary word, so "PPV 12 AMERICAN LEGENDS" reads as
# midnight and "ALI vs 8 AMATEUR BOUTS" as 8 o'clock, and [UndatedEnded] would hide
# such a channel on a time that is not in its name at all. The leading (?<![\d:])
# stops a match beginning inside a longer number or inside a clock time.
#
# This must stay equal to `us_time_pattern` in plugin.py, which is the copy written
# onto the managed EPG source and therefore the one used for any channel bound to it.
# tests/contract/test_us_pattern_parity.py holds the two together.
_DEFAULT_TIME_OF_DAY = (
    r"(?<![\d:])(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[AaPp][Mm])(?![A-Za-z])")

# Rewrites a JavaScript named group (?<name> into the Python (?P<name> form while
# leaving a lookbehind (?<= or (?<! alone. Dispatcharr stores its patterns in the
# JavaScript form because its frontend validator rejects the Python one (issue 21).
_JS_NAMED_GROUP_RE = re.compile(r"\(\?<(?![=!])([^>]+)>")


def _compile_time_pattern(time_pattern):
    """Compile a stored time pattern, or return the built-in default. Never raises."""
    if time_pattern:
        for candidate in (time_pattern,
                          _JS_NAMED_GROUP_RE.sub(r"(?P<\1>", time_pattern)):
            try:
                return re.compile(candidate)
            except re.error:
                continue
    return re.compile(_DEFAULT_TIME_OF_DAY)


def time_pattern_problem(time_pattern):
    """Describe why a stored time pattern cannot be used, or return None when it can.

    A pattern that does not compile is silently replaced by the built-in default, which
    is the right thing to do at the point of use because a channel must not go unread
    over a typing mistake. It is the wrong thing to do QUIETLY: the person who typed the
    pattern gets the built-in behaviour instead of theirs with nothing to tell them so.
    The caller has a logger and this gives it something to say.

    Trying the JavaScript to Python conversion is routine rather than a problem, because
    Dispatcharr stores its patterns in the JavaScript form (issue 21), so only a pattern
    that fails BOTH forms is reported.
    """
    if not time_pattern:
        return None
    reason = None
    for candidate in (time_pattern,
                      _JS_NAMED_GROUP_RE.sub(r"(?P<\1>", time_pattern)):
        try:
            re.compile(candidate)
            return None
        except re.error as exc:
            reason = str(exc)
    return reason or "the pattern could not be compiled"


def extract_time_of_day(channel_name, time_pattern=None):
    """Return (hour, minute) on a 24 hour clock for the first clock time in the name.

    Returns None when the name carries no time the pattern can read. The pattern may
    use either the JavaScript (?<name>) or the Python (?P<name>) named-group form and
    is expected to provide an `hour` group plus optional `minute` and `ampm` groups.

    A supplied pattern that does not COMPILE falls back to the built-in default rather
    than raising, because the pattern comes from an EPG source property the user is free
    to edit and a typing mistake must not stop the name being read at all. Call
    `time_pattern_problem` to report that substitution rather than making it silently.

    A supplied pattern that compiles and MATCHES NOTHING is respected: the answer is
    that this name carries no time. It used to fall back to the built-in default here,
    which silently reverted a deliberate narrowing. Narrowing a time pattern so that a
    slot number is not read as an hour is exactly what this plugin had to do to its own
    title pattern (bug-146), so it is a thing users do on purpose.
    """
    if not channel_name:
        return None
    match = _compile_time_pattern(time_pattern).search(channel_name)
    if match is None:
        return None
    groups = match.groupdict()
    try:
        hour = int(groups.get("hour"))
    except (TypeError, ValueError):
        return None
    try:
        minute = int(groups.get("minute") or 0)
    except (TypeError, ValueError):
        # Give up rather than assume o'clock. A minute this function cannot read means
        # it does not know when the event starts, and quietly moving the start up to
        # 59 minutes earlier would move the end of the window with it.
        return None
    hour = apply_meridiem(hour, groups.get("ampm"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute


def infer_undated_event_window(first_seen_date, hour, minute, tz_name,
                               duration_minutes, grace_hours):
    """Build (start, hide_after) for an event whose name carries a time but no date.

    `start` is the first-seen date at the parsed time, read in `tz_name`. `hide_after`
    is that start plus the programme duration plus the grace period, so a caller hides
    the channel once the current time passes it.

    Returns None when any input cannot be read: an unknown timezone, an hour and minute
    that do not make a real time, or a duration or grace period that is not a number.
    The caller then has no window, makes no hide decision, and the channel stays visible.

    Every failure gives up rather than substituting a smaller number. An unreadable
    duration used to become zero minutes and an unreadable grace period zero hours,
    which did not abandon the calculation but SHORTENED it, so the channel was hidden
    earlier than any configured value asked for. A shorter window is not a safe
    degradation for a rule whose effect is to remove a channel from the lineup.
    """
    # Imported inside the function, matching coerce_timezone, so the module stays
    # importable on a machine without pytz. The import is inside the try because an
    # ImportError here must produce the same "no window" answer as a bad timezone
    # rather than escaping into the caller, which has no try around its rule loop.
    try:
        import pytz
        event_tz = pytz.timezone(str(tz_name).strip())
    except ImportError:
        return None
    except (pytz.exceptions.UnknownTimeZoneError, AttributeError, TypeError, ValueError):
        return None
    try:
        naive_start = datetime.combine(
            first_seen_date, time(hour=int(hour), minute=int(minute)))
    except (TypeError, ValueError):
        return None
    try:
        duration = max(int(duration_minutes), 0)
        grace = max(int(grace_hours), 0)
    except (TypeError, ValueError):
        return None
    start = event_tz.localize(naive_start)
    return start, start + timedelta(minutes=duration, hours=grace)


def undated_event_has_ended(now, hide_after, first_seen_at=None):
    """Decide whether an undated event channel should be hidden.

    True only when `now` is past `hide_after`. Split out of the rule in plugin.py so the
    decision itself is unit-testable: plugin.py imports Django at module scope and cannot
    be imported outside the container, so anything left in it can only be tested by
    reading its source, which cannot tell a working comparison from a broken one.

    `first_seen_at` is the moment the channel was first recorded. When the inferred window
    closes at or before that moment, the window is not describing an event this channel can
    have been carrying: a channel that appears at 23:00 named for a 1:00am event is named
    for the NEXT 1:00am, not the one seventeen hours before anybody saw it. Hiding on such
    a window would remove a channel whose event has not started. This returns False in that
    case, leaving the channel visible and the decision to [UndatedAge:N].

    A missing `first_seen_at`, which is what a record written by an earlier version carries,
    applies no such check. That preserves the older behaviour for those records rather than
    disabling the rule for them, and each one gains the stamp as soon as its name changes.
    """
    if now is None or hide_after is None:
        return False
    if first_seen_at is not None and hide_after <= first_seen_at:
        return False
    return now > hide_after


# --- regex settings fields: alternatives that do not mean what they look like ----

_MAX_REGEX_ALTERNATIVE_PROBLEMS = 10


def _split_top_level_alternatives(pattern):
    """Yield the alternative lists of every level of the pattern that alternates.

    A level is the top of the pattern or the inside of one group. Only a level
    that actually contains an unescaped pipe is yielded, so an ordinary pattern
    with no alternation produces nothing to check.

    The pipe is not special inside a character class and is a literal when it is
    escaped, so both are skipped. Group construct prefixes such as (?:, (?i:,
    (?=, (?<! and (?P<name> are consumed with the opener rather than becoming
    the first characters of an alternative.
    """
    levels = []
    stack = [{"start": 0, "alts": [], "has_pipe": False}]
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            index += 1
            if index < length and pattern[index] == "^":
                index += 1
            if index < length and pattern[index] == "]":
                index += 1
            while index < length and pattern[index] != "]":
                index += 2 if pattern[index] == "\\" else 1
            index += 1
            continue
        if char == "(":
            index += 1
            index = _skip_group_prefix(pattern, index)
            stack.append({"start": index, "alts": [], "has_pipe": False})
            continue
        if char == ")" and len(stack) > 1:
            level = stack.pop()
            level["alts"].append(pattern[level["start"]:index])
            if level["has_pipe"]:
                levels.append(level["alts"])
            index += 1
            continue
        if char == "|":
            level = stack[-1]
            level["alts"].append(pattern[level["start"]:index])
            level["has_pipe"] = True
            level["start"] = index + 1
            index += 1
            continue
        index += 1

    root = stack[0]
    root["alts"].append(pattern[root["start"]:length])
    if root["has_pipe"]:
        levels.append(root["alts"])
    return levels


def _skip_group_prefix(pattern, index):
    """Return the index just past a group construct prefix, if there is one."""
    if index >= len(pattern) or pattern[index] != "?":
        return index
    rest = pattern[index:]
    if rest.startswith("?P<") or rest.startswith("?P="):
        closer = pattern.find(">" if rest.startswith("?P<") else ")", index)
        return index + 3 if closer == -1 else closer + 1
    if rest.startswith("?<=") or rest.startswith("?<!"):
        return index + 3
    if rest.startswith("?<"):
        closer = pattern.find(">", index)
        return index + 2 if closer == -1 else closer + 1
    if len(rest) > 1 and rest[1] in ":=!>":
        return index + 2
    # An inline flag group, either (?i) or (?i:...). Consume up to the colon when
    # one comes before the closing parenthesis, otherwise leave the index alone.
    colon = pattern.find(":", index)
    closer = pattern.find(")", index)
    if colon != -1 and (closer == -1 or colon < closer):
        return colon + 1
    return index


def regex_alternative_problems(pattern):
    """Describe alternatives in a regex settings field that will surprise the operator.

    Returns a list of plain descriptions, empty when there is nothing to say.
    Compilation is checked separately by the caller, and a pattern that does not
    compile returns nothing here so the same field is not reported twice.

    Two shapes are reported, and neither is a syntax error, which is the whole
    point: the pattern compiles and does the wrong thing silently.

    An alternative that begins or ends with a space almost always means a name
    containing a pipe was pasted into the field. A user typed four channel GROUP
    names into Regex: Channel Names to Ignore, separated by pipes, but each group
    name already contained a pipe with spaces around it, so the alternatives the
    engine saw included a bare "USA " and every channel name containing that text
    was skipped.

    LENGTH IS DELIBERATELY NOT A SIGNAL. Flagging a short alternative would
    report "NFL|NHL|NBA", where three-character alternatives are exactly what the
    operator meant. Every alternative in the pasted pattern above carries a
    leading or trailing space, so the whitespace catches the real mistake without
    the false positives.

    An empty alternative is reported because it makes the whole pattern match
    every name, which for the ignore field means nothing is ever scanned.
    """
    if not pattern:
        return []
    try:
        re.compile(pattern)
    except re.error:
        return []

    found = []
    for kind, alternative in _regex_alternative_findings(pattern):
        if kind == "empty":
            found.append(
                "one alternative is empty, so this pattern matches every "
                "name. Remove the stray pipe character.")
        else:
            found.append(
                f"the alternative {alternative!r} begins or ends with a "
                "space. The pipe character separates alternatives, so this "
                "matches any name containing that text on its own. This "
                "usually means a name that already contained a pipe was "
                "pasted into this field.")
        if len(found) >= _MAX_REGEX_ALTERNATIVE_PROBLEMS:
            break
    return found


def _regex_alternative_findings(pattern):
    """Return (kind, alternative) for every alternative worth reporting.

    kind is "empty" or "space". Shared by the two public functions so the
    classification exists once. Not capped: the caller decides how much to show.
    """
    if not pattern:
        return []
    try:
        re.compile(pattern)
    except re.error:
        return []
    findings = []
    for alternatives in _split_top_level_alternatives(pattern):
        for alternative in alternatives:
            if not alternative.strip():
                findings.append(("empty", alternative))
            elif alternative != alternative.strip():
                findings.append(("space", alternative))
    return findings


def regex_alternative_summary(pattern):
    """One short line naming the problem, or None when there is nothing to say.

    Dispatcharr clips an action toast at roughly 280 characters, from the MIDDLE,
    with no visual marker that anything was cut, and it collapses newlines into
    one paragraph. The full descriptions from regex_alternative_problems belong in
    the log; this is the line the operator actually reads. It is deliberately
    capped well under the clip so the rest of the validation readout survives.
    """
    findings = _regex_alternative_findings(pattern)
    if not findings:
        return None
    count = len(findings)
    noun = "alternative" if count == 1 else "alternatives"
    verb = "looks" if count == 1 else "look"
    kind, alternative = findings[0]
    if kind == "empty":
        return (f"{count} {noun} in this pattern {verb} wrong, the first is "
                "empty so the pattern matches every name")
    shown = alternative if len(alternative) <= 20 else alternative[:20] + "..."
    return (f"{count} {noun} in this pattern {verb} wrong, the first is "
            f"{shown!r} which begins or ends with a space")


# --- the CSV's Rule Effectiveness tally, and the tag it groups by ---------------

# The reason written on a channel hidden because another channel carries the same
# event. It leads with a bracketed tag like every other hide reason, which is what
# makes it appear as "Duplicate" in the hide_rule column and in the Rule
# Effectiveness tally. Before that tag existed a duplicate hide produced no tag at
# all, and the tally counted ten of them under an empty label, printing
# "  : 10 channels" in a user's export (bug-177).
DUPLICATE_HIDE_REASON = "[Duplicate] Another channel has the same event and was kept"

# Printed instead of a blank label when a hidden channel carries no tag. A hide
# path added later without one is then visible in the readout rather than silent.
UNTAGGED_RULE_LABEL = "(hidden with no rule tag)"


def hide_rule_tag(reason):
    """Return the bracketed tag a hide reason leads with, or the empty string.

    A reason reads "[PastDate:0] Event date ... is 6 days in the past", and the
    tag is what the CSV's hide_rule column and the Rule Effectiveness tally group
    by. Only a tag at the very START counts: a bracket later in the text is
    ordinary prose, not a rule name.
    """
    if not reason or not reason.startswith("["):
        return ""
    end = reason.find("]")
    if end <= 1:
        return ""
    return reason[1:end]


def rule_effectiveness(results):
    """Count hidden channels per rule tag. Returns [(label, count)], largest first.

    Ties are broken by label so the CSV header is byte-stable between two runs
    that hid the same channels, which matters because these files get diffed.

    NO HIDDEN CHANNEL IS EVER COUNTED UNDER A BLANK LABEL. That is the whole
    reason this is a function rather than a dict comprehension at the call site.
    The previous version read the hide_rule column with `dict.get(key, "N/A")`,
    whose default fires only when the key is ABSENT, and a duplicate hide had the
    key present and empty. So ten hidden channels were reported as
    "  : 10 channels", which reads as a broken CSV rather than as the answer it
    actually was.
    """
    counts = {}
    for result in results or ():
        if (result.get("action") or "") != "Hide":
            continue
        label = (result.get("hide_rule") or "").strip() or UNTAGGED_RULE_LABEL
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


# --- age-based cleanup of this plugin's CSV exports ------------------------------
#
# /data/exports IS SHARED. Measured on the live installation, six plugins write
# there: stream_mapparr, epg_janitor, event_channel_managarr, lineuparr,
# iptv_checker and channel_mapparr, 90 files between them. Selecting on the .csv
# suffix alone, or globbing *.csv, deletes other projects' data, and with a seven
# day rule that is most of the directory on the first run. Both the prefix AND the
# suffix must match.
#
# THIS PLUGIN OWNS TWO PREFIXES. plugin.py writes event_channel_managarr_* for the
# scan exports and epg_removal_* for the "Remove EPG from Hidden Channels" export.
# Checked against every sibling project on 2026-09-05: none writes either prefix,
# and EPG-Janitor's own removal export is epg_janitor_removal_, which does not
# start with epg_removal_. plugin.py's Clear CSV Exports action reads these same
# two constants so the two cannot come to disagree about what "ours" means.
CSV_EXPORT_PREFIXES = ("event_channel_managarr_", "epg_removal_")
CSV_EXPORT_SUFFIX = ".csv"

SECONDS_PER_DAY = 86400.0


def _is_our_export(name):
    """True when this filename is one of this plugin's CSV exports."""
    return (isinstance(name, str)
            and name.startswith(CSV_EXPORT_PREFIXES)
            and name.endswith(CSV_EXPORT_SUFFIX))


def csv_exports_to_delete(entries, retention_days, now, protect=None):
    """Which of this plugin's CSV exports are old enough to remove. Pure.

    `entries` is a sequence of (filename, modification time) pairs, normally the
    whole shared export directory. Returns the names to delete, sorted.

    Four rules, each of which exists because of a way this can go wrong.

    OFF UNLESS CONFIGURED. Anything that is not a positive whole number of days,
    including 0, a negative, blank, None and unparseable text, deletes nothing, so
    nobody loses files merely by upgrading.

    THE FILE JUST WRITTEN IS NEVER DELETED, whatever the age arithmetic says.

    ONE OF OURS ALWAYS SURVIVES, so a small number typed into the setting cannot
    empty the directory. The file just written is the natural survivor; otherwise
    it is the most recent.

    A MODIFICATION TIME THAT IS NOT A NUMBER IS SKIPPED ENTIRELY rather than kept.
    Keeping it looks harmless and is not: every comparison against it is false, so
    it wins the "which is newest" test, becomes the survivor, and every readable
    file is deleted instead of one being kept.

    The age comparison is strict, so a file exactly N days old is not older than
    N days.
    """
    try:
        days = int(str(retention_days).strip())
    except (TypeError, ValueError, AttributeError):
        return []
    if days <= 0:
        return []

    ours = []
    for name, mtime in entries or ():
        if not _is_our_export(name):
            continue
        try:
            stamp = float(mtime)
        except (TypeError, ValueError):
            continue
        if stamp != stamp:  # a NaN: its age cannot be compared
            continue
        ours.append((name, stamp))
    if not ours:
        return []

    survivor = protect if any(name == protect for name, _ in ours) else None
    if survivor is None:
        survivor = max(ours, key=lambda pair: (pair[1], pair[0]))[0]

    cutoff = float(now) - days * SECONDS_PER_DAY
    return sorted(name for name, stamp in ours
                  if stamp < cutoff and name != survivor)


def prune_csv_exports(directory, retention_days, now=None, protect=None,
                      logger=None, listdir=None, getmtime=None, remove=None):
    """Delete this plugin's CSV exports in `directory` older than retention_days.

    Returns how many were removed. NEVER RAISES: this runs immediately after a
    successful export, and a failure to tidy up must not turn that export into a
    reported error.

    The three directory calls are arguments so this half is testable with no
    filesystem, the same reason compile_pattern takes an injectable engine. The
    decision itself is csv_exports_to_delete, which touches nothing.
    """
    import os
    import time

    listdir = os.listdir if listdir is None else listdir
    getmtime = os.path.getmtime if getmtime is None else getmtime
    remove = os.remove if remove is None else remove
    now = time.time() if now is None else now

    try:
        names = listdir(directory)
    except OSError:
        return 0

    entries = []
    for name in names:
        try:
            entries.append((name, getmtime(os.path.join(directory, name))))
        except OSError:
            # It vanished between listing and asking, so there is nothing to delete.
            continue

    removed = 0
    for name in csv_exports_to_delete(entries, retention_days, now, protect):
        try:
            remove(os.path.join(directory, name))
            removed += 1
            if logger is not None:
                logger.info(f"Deleted CSV export older than {retention_days} days: {name}")
        except OSError as exc:
            if logger is not None:
                logger.warning(f"Could not delete old CSV export {name}: {exc}")
    return removed


# --- the preamble on the CSV reports this plugin writes --------------------------
#
# WHOLE FILE PLAIN ASCII ON PURPOSE. A spreadsheet opening a CSV under another
# codepage turns any other character into mojibake, so nothing this plugin writes
# into the preamble may leave ASCII. A value the OPERATOR typed, such as a channel
# group name carrying an emoji, is passed through untouched: it is their text and
# mangling it would be worse than the codepage risk.

REPORT_INTRO_LINES = (
    "Event Channel Managarr channel visibility report.",
    "Every line starting with # is a preamble. Tell your spreadsheet to skip "
    "these lines, or delete them, before reading the columns below.",
)

# How each setting is written in the interface, and how to render its value.
# THE LABEL IS THE ONE ON THE SETTINGS FORM, so a reader who wants to change what
# they are looking at can find it. The internal ids are no help there and one of
# them is actively misleading: auto_set_dummy_epg_on_hide says set and it REMOVES.
#
# kind: "yesno" for a checkbox, "hours" for a number of hours, "plain" otherwise.
SETTINGS_REPORT = (
    ("timezone", "Timezone (read from Dispatcharr, not a plugin setting)", "plain"),
    ("channel_profile_name", "Channel Profile Names", "plain"),
    ("channel_groups", "Channel Groups", "plain"),
    ("name_source", "Name Source", "plain"),
    ("date_format", "Date Format in Channel Names", "plain"),
    ("hide_rules_priority", "Hide Rules Priority", "plain"),
    ("regex_channels_to_ignore", "Regex: Channel Names to Ignore", "plain"),
    ("regex_mark_inactive", "Regex: Mark Channel as Inactive", "plain"),
    ("regex_force_visible", "Regex: Force Visible Channels", "plain"),
    ("past_date_grace_hours", "Past Date Grace Period", "hours"),
    ("undated_event_grace_hours", "Undated Event Grace Period", "hours"),
    ("duplicate_strategy", "Duplicate Handling Strategy", "plain"),
    ("keep_duplicates", "Keep Duplicate Channels", "yesno"),
    ("auto_set_dummy_epg_on_hide", "Auto-Remove EPG on Hide", "yesno"),
    ("manage_dummy_epg", "Manage Dummy EPG", "yesno"),
    ("override_existing_epg", "Override Empty Existing EPG", "yesno"),
    ("dummy_epg_channel_format", "Channel Name Format", "plain"),
    ("dummy_epg_event_duration_hours", "Event Duration", "hours"),
    ("dummy_epg_event_timezone", "Channel Name Event Timezone", "plain"),
    ("group_epg_source_map", "Per-Group EPG Sources", "plain"),
    ("scheduled_times", "Scheduled Run Times", "plain"),
    ("enable_scheduled_csv_export", "Enable Scheduled CSV Export", "yesno"),
    ("csv_retention_days", "Delete CSV Exports Older Than", "days"),
    ("auto_rescan_on_m3u_refresh", "Auto-rescan after M3U refresh", "yesno"),
    ("rate_limiting", "Rate Limiting", "plain"),
)

_TRUE_WORDS = ("true", "yes", "on", "1", "enabled")
_FALSE_WORDS = ("false", "no", "off", "0", "disabled", "")


def yes_no(value):
    """Render a stored checkbox as Yes or No. Pure.

    DISPATCHARR STORES SOME OF THESE BOOLEANS AS THE STRING "true", so a report
    that only handled real booleans would show two spellings for one state. An
    unset value reads as No, because that is what the plugin acts on. A value
    that is neither is returned unchanged rather than guessed at, so a surprising
    stored value is visible instead of being flattened into a confident No.
    """
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None:
        return "No"
    if isinstance(value, (int, float)):
        return "Yes" if value else "No"
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return "Yes"
    if text in _FALSE_WORDS:
        return "No"
    return str(value)


# How a stored choice is written in the interface. The report uses the interface
# wording, because "lowest_number" is not a phrase anyone can find on the settings
# form. tests/contract/test_report_value_labels.py holds this equal to the options
# the form actually offers, so the two cannot drift.
SETTING_VALUE_LABELS = {
    "name_source": (("Channel_Name", "Channel Name"), ("Stream_Name", "Stream Name")),
    "date_format": (("Auto", "Auto-detect (recommended)"), ("US", "US (MM/DD)"),
                    ("EU", "EU (DD/MM)")),
    "duplicate_strategy": (("lowest_number", "Keep Lowest Channel Number"),
                           ("highest_number", "Keep Highest Channel Number"),
                           ("longest_name", "Keep Longest Channel Name")),
    "dummy_epg_channel_format": (
        ("US", "US:  PPV/LIVE EVENT ##: Title (MM.DD HH:MM AM/PM TZ)"),
        ("SE", "SE:  PREFIX | Title | DDD DD Mon HH:MM TZ | extras | channel name")),
    "rate_limiting": (("none", "None (fastest)"), ("low", "Low (~0.05s / channel)"),
                      ("medium", "Medium (~0.2s / channel)"),
                      ("high", "High (~0.5s / channel)")),
}


def value_label(setting_id, value):
    """The interface wording for a stored choice, or the stored value unchanged.

    An unrecognised value is returned as it is rather than hidden, so a stored
    value the plugin does not know about is visible in the report instead of
    being quietly translated into something it is not.
    """
    for stored, label in SETTING_VALUE_LABELS.get(setting_id, ()):
        if stored == value:
            return label
    return value


def _plural(text, unit):
    """"1 hour" rather than "1 hours", without pretending to know English."""
    try:
        one = float(text) == 1
    except (TypeError, ValueError):
        one = False
    return f"{text} {unit}" if one else f"{text} {unit}s"


def _render(setting_id, kind, value):
    if kind == "yesno":
        return yes_no(value)
    unset = value is None or str(value).strip() == ""
    if kind == "hours":
        # The built-in default is NOT repeated here. Naming a number in two places
        # is how a report comes to state a default the code no longer uses.
        return "not set, the built-in default applies" if unset else _plural(str(value).strip(), "hour")
    if kind == "days":
        if unset or str(value).strip() in ("0", "0.0"):
            return "not set, which keeps every export"
        return _plural(str(value).strip(), "day")
    if unset:
        return "(empty)"
    return str(value_label(setting_id, str(value).strip()))


def settings_report_lines(settings):
    """The indented "  Label: value" lines for the report preamble. Pure.

    Every setting that changes what a run does is listed, including the ones that
    are off, because a reader working out why a run behaved as it did needs to see
    that a setting was off rather than find it missing and wonder.
    """
    settings = settings or {}
    return [f"  {label}: {_render(sid, kind, settings.get(sid))}"
            for sid, label, kind in SETTINGS_REPORT]
