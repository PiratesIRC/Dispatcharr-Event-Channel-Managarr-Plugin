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


def extract_time_of_day(channel_name, time_pattern=None):
    """Return (hour, minute) on a 24 hour clock for the first clock time in the name.

    Returns None when the name carries no time the pattern can read. The pattern may
    use either the JavaScript (?<name>) or the Python (?P<name>) named-group form and
    is expected to provide an `hour` group plus optional `minute` and `ampm` groups.

    A supplied pattern that does not compile, or that matches nothing, falls back to
    the built-in default rather than raising or giving up, because the pattern comes
    from an EPG source property the user is free to edit.
    """
    if not channel_name:
        return None
    match = _compile_time_pattern(time_pattern).search(channel_name)
    if match is None and time_pattern:
        match = re.compile(_DEFAULT_TIME_OF_DAY).search(channel_name)
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
        minute = 0
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

    Returns None when `tz_name` is not a zone this installation knows, or when the
    hour and minute do not make a real time. The caller then has no window and makes
    no hide decision, which leaves the channel visible.
    """
    import pytz  # local, matching coerce_timezone: the module imports without it

    try:
        event_tz = pytz.timezone(str(tz_name).strip())
    except Exception:
        return None
    try:
        naive_start = datetime.combine(
            first_seen_date, time(hour=int(hour), minute=int(minute)))
    except (TypeError, ValueError):
        return None
    start = event_tz.localize(naive_start)
    try:
        duration = max(int(duration_minutes), 0)
    except (TypeError, ValueError):
        duration = 0
    try:
        grace = max(int(grace_hours), 0)
    except (TypeError, ValueError):
        grace = 0
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
