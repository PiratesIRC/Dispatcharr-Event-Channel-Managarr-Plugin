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
from datetime import datetime

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

    # Pattern 2b: MONTH DD e.g., "Nov 8" or "Nov 8 16:00"
    pattern2b = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})(?:\s+(\d{1,2}:\d{2}))?', channel_name, re.IGNORECASE)
    if pattern2b:
        month_str, day, hour_minute = pattern2b.groups()
        try:
            date_str = f"{month_str} {day} {current_year}"
            if hour_minute:
                date_str += f" {hour_minute}"
            temp_date = dateutil_parser.parse(date_str)
            extracted_date = datetime(temp_date.year, temp_date.month, temp_date.day, temp_date.hour, temp_date.minute)
            if (today - extracted_date).days > 180:
                extracted_date = datetime(current_year + 1, temp_date.month, temp_date.day, temp_date.hour, temp_date.minute)
            log.debug(f"Extracted date {extracted_date} from pattern MONTH DD[ HH:MM] in '{channel_name}'")
            return extracted_date
        except (ValueError, dateutil_parser.ParserError):
            pass

    # Pattern 3: M.D without year e.g., "10.25" — interpreted per date_format setting.
    pattern3 = re.search(r'\b(\d{1,2})\.(\d{1,2})\b', channel_name)
    if pattern3:
        first, second = map(int, pattern3.groups())
        extracted_date = resolve_numeric_date_pair(first, second, current_year, date_format)
        if extracted_date is not None:
            log.debug(f"Extracted date {extracted_date.date()} from pattern M.D ({date_format}) in '{channel_name}'")
            return extracted_date

    # Pattern 4: M/D without year e.g., "10/27" or "15/04" — interpreted per date_format setting.
    # Lookahead excludes "/" (year follows, handled by Pattern 1) and ":" (time
    # range like "1/3:30pm" — second number is hours, not a day).
    pattern4 = re.search(r'\b(\d{1,2})/(\d{1,2})\b(?![/:])', channel_name)
    if pattern4:
        first, second = map(int, pattern4.groups())
        extracted_date = resolve_numeric_date_pair(first, second, current_year, date_format)
        if extracted_date is not None:
            log.debug(f"Extracted date {extracted_date.date()} from pattern M/D ({date_format}) in '{channel_name}'")
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
