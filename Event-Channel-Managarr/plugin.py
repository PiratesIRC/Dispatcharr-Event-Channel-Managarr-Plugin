
"""
Event Channel Managarr Plugin
Manages channel visibility based on EPG data and channel names
Automatically hides channels with no events and shows channels with events
"""

import logging
import json
import csv
try:
    import fcntl
except ImportError:
    fcntl = None  # Windows — file locking unavailable (not needed outside Docker)
import os
import re
import time
import threading
import pytz

from datetime import datetime, timedelta
from django.utils import timezone

# Django model imports
from apps.channels.models import Channel, ChannelProfileMembership, ChannelProfile, Stream
from apps.epg.models import ProgramData
from django.db import transaction
from core.utils import send_websocket_update

LOGGER = logging.getLogger("plugins.event_channel_managarr")
LOG_PREFIX = "[EventChannelManagarr]"

# Pure parsing logic lives in the Django-free sibling module `ecm_parsing` so it
# can be unit-tested without a running container. Dispatcharr loads plugin.py as a
# submodule but does NOT put the plugin's own directory on sys.path, so add it here
# to make the sibling import resolve regardless of loader internals.
import sys as _sys
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in _sys.path:
    _sys.path.insert(0, _PLUGIN_DIR)
import ecm_parsing
import ecm_profiles

# Backwards-compatible aliases: existing references to these names elsewhere in
# plugin.py (e.g. the [PastDate] stop-time check) keep working, now backed by the
# shared module so the extractor and the rule can never drift apart.
_EVENT_TS_SUFFIX = ecm_parsing.EVENT_TS_SUFFIX
_EVENT_TS_RE = ecm_parsing.EVENT_TS_RE

# Background scheduling globals
_bg_thread = None
_stop_event = threading.Event()
_scheduler_lock = threading.Lock()  # Prevent concurrent scheduler starts


class PluginConfig:
    """Centralized configuration constants for Event Channel Managarr."""

    PLUGIN_VERSION = "1.26.2481329"

    # Fallback timezone when Dispatcharr's global time zone is unset/invalid.
    DEFAULT_TIMEZONE = "UTC"

    # Default name source for channel matching
    DEFAULT_NAME_SOURCE = "Channel_Name"  # Options: "Channel_Name" or "Stream_Name"

    # Default hide rules priority (comma-separated)
    DEFAULT_HIDE_RULES = "[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedEnded],[UndatedAge:2],[ShortDescription],[ShortChannelName]"

    # Default duplicate handling strategy
    DEFAULT_DUPLICATE_STRATEGY = "lowest_number"  # Options: "lowest_number", "highest_number", "longest_name"

    # Default grace period for past date rule (in hours)
    DEFAULT_PAST_DATE_GRACE_HOURS = "4"

    # Default grace period after an undated event's inferred end, in hours. Events
    # overrun, so the channel stays visible for this long past the computed end
    # before [UndatedEnded] hides it.
    DEFAULT_UNDATED_EVENT_GRACE_HOURS = "1"

    # Default automatic EPG removal on hide
    DEFAULT_AUTO_REMOVE_EPG = True

    # Default CSV export for scheduled runs
    DEFAULT_SCHEDULED_CSV_EXPORT = False

    # Auto-rescan after each M3U refresh (re-hides channels that Dispatcharr's
    # Auto Channel Sync re-enables). Opt-in, default off — no behavior change on upgrade.
    DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH = False

    # Default keep duplicates setting
    DEFAULT_KEEP_DUPLICATES = False

    # Managed Dummy EPG feature defaults
    DEFAULT_MANAGE_DUMMY_EPG = False
    # When True, the managed dummy may also take over channels already linked to a
    # non-managed EPG source that currently has no programmes (blank guide). Default
    # OFF so existing real EPG is never overwritten unless the user opts in (bug-043).
    DEFAULT_OVERRIDE_EXISTING_EPG = False
    DEFAULT_EVENT_DURATION_HOURS = "3"
    DEFAULT_DUMMY_EPG_TIMEZONE = "US/Eastern"
    DEFAULT_DUMMY_EPG_CHANNEL_FORMAT = "US"

    # Pacing for per-channel ORM writes ("none", "low", "medium", "high")
    DEFAULT_RATE_LIMITING = "none"

    # Scheduler check interval (in seconds)
    SCHEDULER_CHECK_INTERVAL = 30

    # Scheduler stop timeout (in seconds)
    SCHEDULER_STOP_TIMEOUT = 10

    # File paths
    LAST_RUN_FILE = "/data/event_channel_managarr_last_run.json"
    SCAN_LOCK_FILE = "/data/event_channel_managarr_scan.lock"
    # A real scan finishes in seconds. If the scan flock is held but its file is
    # older than this, the holder is assumed dead/leaked (e.g. an fd inherited by
    # a forked uwsgi/celery worker that never released it) and the lock is broken.
    SCAN_LOCK_STALE_SECONDS = 900  # 15 min
    SETTINGS_FILE = "/data/event_channel_managarr_settings.json"
    RESULTS_FILE = "/data/event_channel_managarr_results.json"
    UNDATED_FIRST_SEEN_FILE = "/data/event_channel_managarr_undated_first_seen.json"
    # Which dummy EPG sources this plugin created for a group mapping (issue 29).
    #
    # THIS CANNOT LIVE IN EPGSource.custom_properties. Dispatcharr's own EPG source
    # editor rebuilds that object from a FIXED key list and submits it whole, and
    # EPGSourceSerializer.update assigns it with setattr rather than merging, so any
    # key the frontend does not know is DELETED on save. Measured 2026-09-02:
    # managed_by appears in no frontend bundle and survives only because the plugin
    # rewrites it on every applied run. A mapped source is deliberately never
    # rewritten, so it would have nothing to repair a marker, and the operator
    # editing its timezone -- the whole point of the feature -- would erase it.
    #
    # A missing or unreadable file means no channel is eligible to move back, so
    # losing it strands channels where they are rather than moving them wrongly.
    GROUP_SOURCE_RECORD_FILE = "/data/event_channel_managarr_group_sources.json"
    EXPORTS_DIR = "/data/exports"
    # An append-only record of what applied runs actually CHANGED, one JSON line
    # each. It exists so a running total can be reported without re-deriving it
    # from the CSV exports, which a plugin action deletes and which scheduled runs
    # only write when the operator has turned that setting on. Dry runs never
    # append, and neither does an applied run that changed nothing, so a line
    # means real work. Rotation keeps one predecessor; at a few hundred bytes per
    # run this is decades away and the reader globs both files.
    LEDGER_FILE = "/data/event_channel_managarr_ledger.jsonl"
    LEDGER_MAX_BYTES = 5 * 1024 * 1024


_LAST_RUN_FILE = PluginConfig.LAST_RUN_FILE
_SCAN_LOCK_FILE = PluginConfig.SCAN_LOCK_FILE


class ProgressTracker:
    """Tracks operation progress with periodic logging and WebSocket updates."""

    def __init__(self, total_items, action_id, logger):
        self.total_items = max(total_items, 1)
        self.action_id = action_id
        self.logger = logger
        self.start_time = time.time()
        self.last_update_time = self.start_time
        # Adaptive interval: shorter for smaller jobs
        self.update_interval = 3 if total_items <= 50 else 5 if total_items <= 200 else 10
        self.processed_items = 0
        logger.info(f"{LOG_PREFIX} [{action_id}] Starting: {total_items} items to process")
        send_websocket_update('updates', 'update', {
            "type": "plugin", "plugin": "Event Channel Managarr",
            "message": f"{action_id}: Starting ({total_items} items)"
        })

    def update(self, items_processed=1):
        self.processed_items += items_processed
        now = time.time()
        if now - self.last_update_time >= self.update_interval:
            self.last_update_time = now
            elapsed = now - self.start_time
            pct = (self.processed_items / self.total_items) * 100
            remaining = (elapsed / self.processed_items) * (self.total_items - self.processed_items) if self.processed_items > 0 else 0
            eta_str = self._format_eta(remaining)
            self.logger.info(f"{LOG_PREFIX} [{self.action_id}] {pct:.0f}% ({self.processed_items}/{self.total_items}) - ETA: {eta_str}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Event Channel Managarr",
                "message": f"{self.action_id}: {pct:.0f}% ({self.processed_items}/{self.total_items}) - ETA: {eta_str}"
            })

    def finish(self):
        elapsed = time.time() - self.start_time
        eta_str = self._format_eta(elapsed)
        self.logger.info(f"{LOG_PREFIX} [{self.action_id}] Complete: {self.processed_items}/{self.total_items} in {eta_str}")
        send_websocket_update('updates', 'update', {
            "type": "plugin", "plugin": "Event Channel Managarr",
            "message": f"{self.action_id}: Complete ({self.processed_items}/{self.total_items}) in {eta_str}"
        })

    @staticmethod
    def _format_eta(seconds):
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m"


class SmartRateLimiter:
    """Optional per-item pacing for bulk ORM loops.

    Sleeps a configurable amount between .wait() calls. Usage:
        limiter = SmartRateLimiter(settings.get("rate_limiting", "none"))
        for item in items:
            ... do one ORM op ...
            limiter.wait()
    """

    _DELAYS = {
        "none": 0.0,
        "low": 0.05,
        "medium": 0.2,
        "high": 0.5,
    }

    def __init__(self, level):
        level_str = str(level).strip().lower() if level is not None else "none"
        self.delay = self._DELAYS.get(level_str, 0.0)
        self.level = level_str if level_str in self._DELAYS else "none"

    def wait(self):
        if self.delay > 0:
            time.sleep(self.delay)

    def is_active(self):
        return self.delay > 0


def _read_last_run():
    """Read the last-run tracker from disk (shared across all uwsgi workers)."""
    try:
        if os.path.exists(_LAST_RUN_FILE):
            with open(_LAST_RUN_FILE, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _write_last_run(data):
    """Write the last-run tracker to disk (shared across all uwsgi workers).
    Must only be called while holding the scan lock.
    Uses atomic write (temp + rename) to prevent corruption from crashes."""
    tmp_file = _LAST_RUN_FILE + ".tmp"
    try:
        with open(tmp_file, 'w') as f:
            json.dump(data, f)
        os.replace(tmp_file, _LAST_RUN_FILE)
    except OSError as e:
        LOGGER.error(f"Failed to write last-run file: {e}")

class Plugin:
    """Event Channel Managarr Plugin"""

    name = "Event Channel Managarr"
    version = PluginConfig.PLUGIN_VERSION
    description = "Automatically manage channel visibility based on EPG data and channel names. Hides channels with no events and shows channels with active events.\n\nGitHub: https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin"

    # Reference PluginConfig for all defaults
    DEFAULT_TIMEZONE = PluginConfig.DEFAULT_TIMEZONE
    DEFAULT_NAME_SOURCE = PluginConfig.DEFAULT_NAME_SOURCE
    DEFAULT_HIDE_RULES = PluginConfig.DEFAULT_HIDE_RULES
    DEFAULT_DUPLICATE_STRATEGY = PluginConfig.DEFAULT_DUPLICATE_STRATEGY
    DEFAULT_PAST_DATE_GRACE_HOURS = PluginConfig.DEFAULT_PAST_DATE_GRACE_HOURS
    DEFAULT_UNDATED_EVENT_GRACE_HOURS = PluginConfig.DEFAULT_UNDATED_EVENT_GRACE_HOURS
    DEFAULT_AUTO_REMOVE_EPG = PluginConfig.DEFAULT_AUTO_REMOVE_EPG
    DEFAULT_SCHEDULED_CSV_EXPORT = PluginConfig.DEFAULT_SCHEDULED_CSV_EXPORT
    DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH = PluginConfig.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH
    DEFAULT_KEEP_DUPLICATES = PluginConfig.DEFAULT_KEEP_DUPLICATES
    DEFAULT_MANAGE_DUMMY_EPG = PluginConfig.DEFAULT_MANAGE_DUMMY_EPG
    DEFAULT_OVERRIDE_EXISTING_EPG = PluginConfig.DEFAULT_OVERRIDE_EXISTING_EPG
    DEFAULT_EVENT_DURATION_HOURS = PluginConfig.DEFAULT_EVENT_DURATION_HOURS
    DEFAULT_DUMMY_EPG_TIMEZONE = PluginConfig.DEFAULT_DUMMY_EPG_TIMEZONE
    DEFAULT_DUMMY_EPG_CHANNEL_FORMAT = PluginConfig.DEFAULT_DUMMY_EPG_CHANNEL_FORMAT
    DEFAULT_RATE_LIMITING = PluginConfig.DEFAULT_RATE_LIMITING
    SCHEDULER_CHECK_INTERVAL = PluginConfig.SCHEDULER_CHECK_INTERVAL
    SCHEDULER_STOP_TIMEOUT = PluginConfig.SCHEDULER_STOP_TIMEOUT

    @staticmethod
    def _load_timezones_from_file():
        """Load timezone list from zone1970.tab file"""
        try:
            timezone_file = "/usr/share/zoneinfo/zone1970.tab"
            timezones = []
            
            with open(timezone_file, 'r') as f:
                for line in f:
                    # Skip comments and empty lines
                    if line.startswith('#') or not line.strip():
                        continue
                    
                    # Parse the tab-delimited format
                    parts = line.strip().split('\t')
                    if len(parts) >= 3:
                        timezone_name = parts[2]
                        timezones.append({"label": timezone_name, "value": timezone_name})
            
            # Sort alphabetically by timezone name
            timezones.sort(key=lambda x: x['label'])
            return timezones
        
        except Exception as e:
            LOGGER.warning(f"Could not load timezones from zone1970.tab: {e}, using fallback list")
            # Fallback to a minimal list if file cannot be read
            return [
                {"label": "America/New_York", "value": "America/New_York"},
                {"label": "America/Los_Angeles", "value": "America/Los_Angeles"},
                {"label": "America/Chicago", "value": "America/Chicago"},
                {"label": "Europe/London", "value": "Europe/London"},
                {"label": "Europe/Berlin", "value": "Europe/Berlin"},
                {"label": "Asia/Tokyo", "value": "Asia/Tokyo"},
                {"label": "Australia/Sydney", "value": "Australia/Sydney"}
            ]
    
    @property
    def fields(self):
        """Build the settings form.

        MUST NOT perform network or blocking I/O. Dispatcharr evaluates this
        property on every settings-page render, so anything slow here delays the
        page, and anything requiring the internet makes the settings unreachable
        when the box is offline. This property once called GitHub from here with
        a five second timeout to look for a newer release; that update check has
        since been removed from the plugin entirely.
        """
        # Build the fields list dynamically
        fields_list = [
            {
                "id": "_section_scope",
                "label": "📍 Scope",
                "type": "info",
                "description": "Which channels this plugin is allowed to touch, and which name it reads on each one."
            },
            {
                "id": "channel_profile_name",
                "label": "📺 Channel Profile Names (Required, comma-separated)",
                "type": "string",
                "default": "",
                "placeholder": "e.g. All, Favorites",
                "help_text": "REQUIRED. The Dispatcharr channel profiles whose channels this plugin manages. Separate several profiles with commas, for example: All, Favorites. Each name must name a profile that exists in Dispatcharr, though capitalisation does not have to match. A channel that has no membership row in one of these profiles is invisible to this plugin and is never scanned.",
            },
            {
                "id": "channel_groups",
                "label": "📂 Channel Groups (comma-separated)",
                "type": "text",
                "default": "",
                "placeholder": "e.g. PPV Live Events, Sports",
                "help_text": "Narrows the scan to these channel groups inside the profiles above. Separate several groups with commas, for example: PPV Live Events, Sports. Capitalisation does not have to match, but the spelling does; a group name that matches nothing is reported by Validate Configuration. Leave blank to scan every group in those profiles.",
            },
            {
                "id": "name_source",
                "label": "🔤 Name Source",
                "type": "select",
                "default": self.DEFAULT_NAME_SOURCE,
                "help_text": "Which text the rules read when they look for an event title, date or time. Channel Name uses the Dispatcharr channel name. Stream Name uses the name of the stream assigned to that channel. Only one source is used at a time.",
                "options": [
                    {"label": "Channel Name", "value": "Channel_Name"},
                    {"label": "Stream Name", "value": "Stream_Name"}
                ]
            },
            {
                "id": "date_format",
                "label": "📅 Date Format in Channel Names",
                "type": "select",
                "default": "Auto",
                "help_text": "How to read a numeric date such as 04/05 found in a name. Auto tries MM/DD first and reads it as DD/MM when the first number is above 12. US always reads MM/DD. EU always reads DD/MM.",
                "options": [
                    {"label": "Auto-detect (recommended)", "value": "Auto"},
                    {"label": "US (MM/DD)", "value": "US"},
                    {"label": "EU (DD/MM)", "value": "EU"}
                ]
            },
            {
                "id": "_section_rules",
                "label": "🎯 Hide Rules",
                "type": "info",
                "description": "The ordered list of rules that decides which channels get hidden, plus the regular expressions that skip, hide or protect a channel by name."
            },
            {
                "id": "hide_rules_priority",
                "label": "📜 Hide Rules Priority",
                "type": "text",
                "default": self.DEFAULT_HIDE_RULES,
                "placeholder": "[BlankName],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]",
                "help_text": "The rules that hide a channel, written as comma-separated tags. They are read left to right and the first tag that matches hides the channel, so put the rules you trust most first. A tag left out of this list is never applied. Some tags take a number after a colon, for example [PastDate:0] or [UndatedAge:2]. Available tags: [NoEPG], [BlankName], [WrongDayOfWeek], [NoEventPattern], [EmptyPlaceholder], [ShortDescription], [ShortDescription:chars], [ShortChannelName], [ShortChannelName:chars], [NumberOnly], [PastDate:days], [PastDate:days:Xh], [FutureDate:days], [UndatedAge:days], [UndatedEnded], [UndatedEnded:hours], [InactiveRegex]. [UndatedEnded] applies to a name that carries a clock time but no date: it hides the channel once the first-seen date plus that time plus the event duration plus the grace period has passed, and uses the Undated Event Grace Period setting unless you give it a number of hours. [ShortDescription] uses 15 characters and [ShortChannelName] 25 unless you give them a number.",
            },
            {
                "id": "regex_channels_to_ignore",
                "label": "🚫 Regex: Channel Names to Ignore",
                "type": "text",
                "default": "",
                "placeholder": "^BACKUP|^TEST",
                "help_text": "A channel whose name matches this pattern is skipped completely: no hide rule runs on it and its visibility is never changed. Case-insensitive regular expression. Separate alternatives with the | character, for example: ^BACKUP|^TEST. Leave blank to skip nothing.",
            },
            {
                "id": "regex_mark_inactive",
                "label": "💤 Regex: Mark Channel as Inactive",
                "type": "text",
                "default": "",
                "placeholder": "CANCELLED|COMING SOON|^TEST|^BACKUP|PLACEHOLDER",
                "help_text": "A channel whose name matches this pattern is hidden, but only while the [InactiveRegex] tag is present in the Hide Rules Priority list above. Case-insensitive regular expression, alternatives separated by the | character, for example: CANCELLED|COMING SOON. Leave blank to disable.",
            },
            {
                "id": "regex_force_visible",
                "label": "✅ Regex: Force Visible Channels",
                "type": "text",
                "default": "",
                "placeholder": "^NEWS|^WEATHER",
                "help_text": "A channel whose name matches this pattern is always left visible and no hide rule can hide it. Case-insensitive regular expression, alternatives separated by the | character, for example: ^NEWS|^WEATHER. Leave blank to disable.",
            },
            {
                "id": "past_date_grace_hours",
                "label": "📅 Past Date Grace Period (Hours)",
                "type": "number",
                "default": int(self.DEFAULT_PAST_DATE_GRACE_HOURS),
                "help_text": "How many whole hours after midnight a channel dated for an earlier day stays visible before the [PastDate] rule hides it. Raise it for events that run past midnight.",
            },
            {
                "id": "undated_event_grace_hours",
                "label": "🕒 Undated Event Grace Period (Hours)",
                "type": "number",
                "default": int(self.DEFAULT_UNDATED_EVENT_GRACE_HOURS),
                "help_text": "How many whole hours past an undated event's inferred end a channel stays visible before the [UndatedEnded] rule hides it. The inferred end is the date the channel was first seen, plus the time read from its name, plus the event duration. Raise it for events that overrun.",
            },
            {
                "id": "_section_duplicates",
                "label": "🎭 Duplicates",
                "type": "info",
                "description": "What to do when several channels carry the same event at the same time."
            },
            {
                "id": "duplicate_strategy",
                "label": "🎭 Duplicate Handling Strategy",
                "type": "select",
                "default": self.DEFAULT_DUPLICATE_STRATEGY,
                "help_text": "When several channels carry the same event, this decides which single channel stays visible. The others are hidden. Ignored while 'Keep Duplicate Channels' below is enabled.",
                "options": [
                    {"label": "Keep Lowest Channel Number", "value": "lowest_number"},
                    {"label": "Keep Highest Channel Number", "value": "highest_number"},
                    {"label": "Keep Longest Channel Name", "value": "longest_name"}
                ]
            },
            {
                "id": "keep_duplicates",
                "label": "🔄 Keep Duplicate Channels",
                "type": "boolean",
                "default": self.DEFAULT_KEEP_DUPLICATES,
                "help_text": "Leave every copy of a duplicated event visible instead of keeping one. While this is on, the Duplicate Handling Strategy above has no effect.",
            },
            {
                "id": "_section_epg",
                "label": "🔌 EPG Management",
                "type": "info",
                "description": "Optional guide-data automation: clearing EPG from channels as they are hidden, and giving visible channels with no guide data a dummy EPG built from their names."
            },
            {
                "id": "auto_set_dummy_epg_on_hide",
                "label": "🔌 Auto-Remove EPG on Hide",
                "type": "boolean",
                "default": self.DEFAULT_AUTO_REMOVE_EPG,
                "help_text": "Clears the EPG assignment from a channel at the moment this plugin hides it. This setting only REMOVES guide data. The setting that creates guide data is 'Manage Dummy EPG' below.",
            },
            {
                "id": "manage_dummy_epg",
                "label": "🗓️ Manage Dummy EPG",
                "type": "boolean",
                "default": self.DEFAULT_MANAGE_DUMMY_EPG,
                "help_text": "Attaches a dummy EPG source, managed by this plugin, to every visible channel that has no EPG assigned. The guide then shows the event read out of the channel name during its time window and 'Offline' outside it. When no time can be read from the name, the guide shows the channel name for the whole day instead. This is the setting that CREATES guide data. '🔌 Auto-Remove EPG on Hide' above only clears it.",
            },
            {
                "id": "override_existing_epg",
                "label": "♻️ Override Empty Existing EPG",
                "type": "boolean",
                "default": self.DEFAULT_OVERRIDE_EXISTING_EPG,
                "help_text": "Requires '🗓️ Manage Dummy EPG'. By default the managed dummy is attached only to visible channels that have NO EPG at all. Turn this on to ALSO take over a visible channel that is linked to a real EPG source carrying no programmes, which shows as a blank guide. A common cause is an event channel the provider mapped to an empty tvg-id. A channel whose linked EPG does have upcoming programmes is never touched. Off by default.",
            },
            {
                "id": "dummy_epg_channel_format",
                "label": "📡 Channel Name Format",
                "type": "select",
                "default": self.DEFAULT_DUMMY_EPG_CHANNEL_FORMAT,
                "help_text": "Which name layout the dummy EPG parser should expect. US reads 'PPV EVENT 12: Title (MM.DD HH:MM AM/PM TZ)'. SE reads 'PREFIX | Event Title | DDD DD Mon HH:MM TZ | extras | channel name', where the last segment, for example 'SE: VIAPLAY PPV 20', becomes the EPG display name so the guide lists the broadcaster rather than the full stream name. Pick the one your provider uses; the wrong choice means no event is read out of the name.",
                "options": [
                    {"label": "US:  PPV/LIVE EVENT ##: Title (MM.DD HH:MM AM/PM TZ)",              "value": "US"},
                    {"label": "SE:  PREFIX | Title | DDD DD Mon HH:MM TZ | extras | channel name", "value": "SE"},
                ]
            },
            {
                "id": "dummy_epg_event_duration_hours",
                "label": "⏱️ Event Duration (hours)",
                "type": "number",
                "default": int(self.DEFAULT_EVENT_DURATION_HOURS),
                "help_text": "How many hours each event fills in the guide, starting at the time read from the channel name. Before that window the guide shows 'Upcoming at <time>: <event>', and after it 'Ended at <time>: <event>'.",
            },
            {
                "id": "dummy_epg_event_timezone",
                "label": "📺 Channel Name Event Timezone",
                "type": "select",
                "default": self.DEFAULT_DUMMY_EPG_TIMEZONE,
                "help_text": "The timezone the times inside channel names are written in, for example US/Eastern for a channel named '(4.17 8:30 PM ET)'. This tells the plugin how to READ those times. It is separate from the timezone Dispatcharr displays the guide in.",
                "options": self._load_timezones_from_file()
            },
            {
                "id": "group_epg_source_map",
                "label": "🗂️ Per-Group EPG Sources (one per line)",
                "type": "text",
                "default": "",
                "placeholder": "NFL Sunday Ticket = ECM - NFL",
                "help_text": "Gives a channel group its own dummy EPG source, so groups needing different timezones, durations or title patterns do not have to share one. Write one mapping per line as Group Name = Source Name, for example: NFL Sunday Ticket = ECM - NFL. Capitalisation of the group does not have to match. A group you do not list keeps the shared source, so leaving this blank changes nothing. The plugin creates a listed source and seeds it from the settings above, then never writes to it again: from that point its timezone, duration, patterns and templates are yours to edit in Dispatcharr's own EPG source editor. The group must also be in scan scope, meaning it appears in Channel Groups above or that field is blank, or the mapping creates a source and moves nothing. Removing a line moves those channels back to the shared source on the next applied run. Run Validate Configuration after editing this.",
            },
            {
                "id": "_section_scheduling",
                "label": "⏰ Scheduling & Export",
                "type": "info",
                "description": "Unattended runs: the times the scan starts by itself, and whether those runs leave a CSV behind."
            },
            {
                "id": "scheduled_times",
                "label": "⏰ Scheduled Run Times (24-hour, comma-separated)",
                "type": "text",
                "default": "",
                "placeholder": "0600,1300,1800",
                "help_text": "Times of day to run the scan automatically, each written as four digits in 24-hour form. Separate several times with commas, for example: 0600,1300,1800 runs at 6 AM, 1 PM and 6 PM every day. Times follow Dispatcharr's own timezone. Leave blank to disable scheduled runs. Click 'Save Schedule' after changing this.",
            },
            {
                "id": "enable_scheduled_csv_export",
                "label": "📄 Enable Scheduled CSV Export",
                "type": "boolean",
                "default": self.DEFAULT_SCHEDULED_CSV_EXPORT,
                "help_text": "Writes a CSV of the results to /data/exports every time a SCHEDULED run finishes. This controls scheduled runs only: Run Now and Dry Run always write a CSV whatever it is set to.",
            },
            {
                "id": "auto_rescan_on_m3u_refresh",
                "label": "🔄 Auto-rescan after M3U refresh",
                "type": "boolean",
                "default": self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH,
                "help_text": "Runs the visibility scan again as soon as an M3U account finishes refreshing. Dispatcharr's Auto Channel Sync un-hides every channel in a synced group on each refresh, and this re-hides them straight afterwards. Leave it off if you do not use Auto Channel Sync.",
            },
            {
                "id": "_section_advanced",
                "label": "⚙️ Advanced",
                "type": "info",
                "description": "Pacing for very large profiles. Leave this alone unless a scan is putting the database under strain."
            },
            {
                "id": "rate_limiting",
                "label": "🐢 Rate Limiting",
                "type": "select",
                "default": self.DEFAULT_RATE_LIMITING,
                "help_text": "How long to pause between the database writes the scan makes for each channel. None is fastest. Low, Medium and High wait about 0.05, 0.2 and 0.5 seconds per channel, which makes a scan of a profile holding thousands of channels much slower but far gentler on a small database.",
                "options": [
                    {"label": "None (fastest)", "value": "none"},
                    {"label": "Low (~0.05s / channel)", "value": "low"},
                    {"label": "Medium (~0.2s / channel)", "value": "medium"},
                    {"label": "High (~0.5s / channel)", "value": "high"}
                ]
            },
        ]

        return fields_list
    
    # Actions for Dispatcharr UI
    # Actions metadata mirrors plugin.json (which drives the Dispatcharr UI).
    # Kept here so code that introspects Plugin.actions sees the same shape.
    actions = [
        {"id": "validate_configuration", "label": "Validate Configuration", "description": "Saves the settings above, then checks them for problems: an invalid regular expression, a profile or channel group that does not exist, a malformed schedule. Changes no channels.", "button_label": "🔎 Validate", "button_variant": "outline", "button_color": "blue"},
        {"id": "update_schedule", "label": "Update Schedule", "description": "Saves the settings above and re-arms the background scheduler with the run times you entered. Use this after editing Scheduled Run Times.", "button_label": "💾 Save Schedule", "button_variant": "filled", "button_color": "green"},
        {"id": "dry_run", "label": "Dry Run (Export to CSV)", "description": "Reports which channels WOULD be hidden or shown and writes the full list to a CSV in /data/exports. Changes nothing.", "button_label": "👁️ Dry Run", "button_variant": "outline", "button_color": "cyan"},
        {"id": "run_now", "label": "Run Now", "description": "Scans now and applies the visibility changes straight away, using the settings as they are currently saved.", "button_label": "▶️ Run Now", "button_variant": "filled", "button_color": "green", "confirm": {"message": "This will apply visibility changes and (if enabled) attach/detach managed EPG. Continue?"}},
        {"id": "on_m3u_refresh", "label": "Auto-rescan after M3U refresh", "description": "Runs a visibility scan automatically after each M3U refresh, but only while '🔄 Auto-rescan after M3U refresh' is enabled in the settings above. There is no button: Dispatcharr triggers it.", "events": ["m3u_refresh"]},
        {"id": "remove_epg_from_hidden", "label": "Remove EPG from Hidden Channels", "description": "Clears the EPG assignment from every channel that is currently hidden in the configured profiles, in one pass. Use it to tidy up channels hidden before 'Auto-Remove EPG on Hide' was turned on.", "button_label": "🧹 Remove EPG from Hidden", "button_variant": "filled", "button_color": "red", "confirm": {"message": "This will CLEAR EPG data from every hidden channel in the selected profile. Cannot be undone by this plugin. Continue?"}},
        {"id": "clear_csv_exports", "label": "Clear CSV Exports", "description": "Deletes every CSV file this plugin has written to /data/exports. No channel or EPG data is touched.", "button_label": "🗑️ Clear CSV Exports", "button_variant": "filled", "button_color": "red", "confirm": {"message": "This will delete every CSV file in /data/exports created by this plugin. Continue?"}},
        {"id": "cleanup_periodic_tasks", "label": "Cleanup Orphaned Tasks", "description": "Removes Celery periodic tasks left behind by older versions of this plugin, which scheduled runs through Celery instead of the built-in scheduler. Harmless to run when there are none.", "button_label": "🧼 Cleanup Orphaned Tasks", "button_variant": "outline", "button_color": "orange", "confirm": {"message": "This removes orphaned Celery periodic tasks left by older plugin versions. Continue?"}},
        {"id": "check_scheduler_status", "label": "Check Scheduler Status", "description": "Shows whether this worker's background scheduler thread is running, which times it is armed for, when the next one is due and when a scan last ran.", "button_label": "🩺 Check Scheduler", "button_variant": "outline", "button_color": "blue"},
    ]
    
    def __init__(self):
        self.results_file = PluginConfig.RESULTS_FILE
        self.settings_file = PluginConfig.SETTINGS_FILE
        self.last_results = []

        # Thread-safe operation locking
        self._thread = None
        self._thread_lock = threading.Lock()
        self._op_stop_event = threading.Event()

        LOGGER.info(f"{LOG_PREFIX} {self.name} Plugin v{self.version} initialized")

        # Load saved settings and create scheduled tasks
        self._load_settings()

    def _try_start_thread(self, target, args):
        """Atomically check if a thread is running and start a new one.
        Returns True if started, False if another operation is running."""
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return False
            self._op_stop_event.clear()
            self._thread = threading.Thread(target=target, args=args, daemon=True)
            self._thread.start()
            return True

    def _get_bool_setting(self, settings, key, default=False):
        """Safely get a boolean setting that might be stored as a string"""
        val = settings.get(key, default)
        LOGGER.debug(f"_get_bool_setting('{key}'): raw_value={val} (type={type(val).__name__}), default={default}")
        if isinstance(val, str):
            result = val.lower() == "true"
            LOGGER.debug(f"  String value '{val}' -> {result}")
            return result
        result = bool(val)
        LOGGER.debug(f"  Non-string value {val} -> {result}")
        return result
  
    def _load_settings(self):
        """Load saved settings from disk"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    self.saved_settings = json.load(f)
                    LOGGER.info("Loaded saved settings")
                    # Start background scheduler with loaded settings
                    self._start_background_scheduler(self.saved_settings)
            else:
                self.saved_settings = {}
        except Exception as e:
            LOGGER.error(f"Error loading settings: {e}")
            self.saved_settings = {}

    def run(self, action, params, context):
        """Main plugin entry point"""
        LOGGER.info(f"Event Channel Managarr run called with action: {action}")

        try:
            # Get live settings from context and params
            live_settings = context.get("settings", {})
            logger = context.get("logger", LOGGER)

            # Log settings for debugging cached values issue
            if action == "update_schedule":
                saved_times = self.saved_settings.get("scheduled_times", "") if self.saved_settings else ""
                live_times = live_settings.get("scheduled_times", "")
                has_key = "scheduled_times" in live_settings
                logger.info(f"[Update Schedule] Saved: '{saved_times}', Live: '{live_times}', Key exists in live_settings: {has_key}")
            elif action == "validate_configuration":
                saved_profiles = self.saved_settings.get("channel_profile_name", "") if self.saved_settings else ""
                live_profiles = live_settings.get("channel_profile_name", "")
                has_profiles_key = "channel_profile_name" in live_settings
                saved_groups = self.saved_settings.get("channel_groups", "") if self.saved_settings else ""
                live_groups = live_settings.get("channel_groups", "")
                has_groups_key = "channel_groups" in live_settings
                logger.info(f"[Validate Config] Profiles - Saved: '{saved_profiles}', Live: '{live_profiles}', Key in live: {has_profiles_key}")
                logger.info(f"[Validate Config] Groups - Saved: '{saved_groups}', Live: '{live_groups}', Key in live: {has_groups_key}")

            # Create a merged settings view
            # Priority order: live_settings (current form) > params (action-specific) > saved_settings (disk cache)
            # Live settings represents the current state of the form, so it should take precedence
            merged_settings = {}

            # Start with saved settings as defaults for any missing keys
            if self.saved_settings:
                merged_settings.update(self.saved_settings)

            # Override with live settings (current form state)
            # This ensures that if a field is cleared in the form, the blank value is used
            if live_settings:
                merged_settings.update(live_settings)

                # WORKAROUND: Dispatcharr may not send empty string fields in live_settings
                # For update_schedule, if scheduled_times is not in live_settings, treat it as blank
                if action == "update_schedule" and "scheduled_times" not in live_settings:
                    logger.info("[Update Schedule] scheduled_times not in live_settings - treating as blank")
                    merged_settings["scheduled_times"] = ""

                # WORKAROUND: For validate_configuration, preserve saved settings for fields not in live_settings
                # Dispatcharr may not send all fields when the form is displayed (only changed fields)
                if action == "validate_configuration":
                    fields_to_preserve = ["channel_profile_name", "channel_groups"]
                    for field in fields_to_preserve:
                        if field not in live_settings and self.saved_settings and field in self.saved_settings:
                            merged_settings[field] = self.saved_settings[field]
                            logger.info(f"[Validate Config] Preserving saved value for '{field}': '{self.saved_settings[field]}'")

            # Params may contain action-specific overrides
            if params:
                merged_settings.update(params)

            action_map = {
                "validate_configuration": self.validate_configuration_action,
                "update_schedule": self.update_schedule_action,
                "dry_run": self.dry_run_action,
                "run_now": self.run_now_action,
                "on_m3u_refresh": self.on_m3u_refresh_action,
                "remove_epg_from_hidden": self.remove_epg_from_hidden_action,
                "clear_csv_exports": self.clear_csv_exports_action,
                "cleanup_periodic_tasks": self.cleanup_periodic_tasks_action,
                "check_scheduler_status": self.check_scheduler_status_action,
            }

            handler = action_map.get(action)
            if not handler:
                logger.warning(f"{LOG_PREFIX} Unknown action: {action}")
                return {
                    "status": "error",
                    "message": f"Unknown action: {action}",
                    "available_actions": list(action_map.keys())
                }

            logger.info(f"{LOG_PREFIX} Action triggered: {action}")
            result = handler(merged_settings, logger)

            # Send WebSocket notification for completed actions
            if isinstance(result, dict):
                status = result.get("status", "?")
                msg = result.get("message", "")[:200]
                emoji = "+" if status == "success" else "-"
                notify_msg = msg.split("\n")[0] if msg else action
                send_websocket_update('updates', 'update', {
                    "type": "plugin", "plugin": self.name,
                    "message": f"[{emoji}] {notify_msg}"
                })

            return result
                
        except Exception as e:
            LOGGER.error(f"{LOG_PREFIX} Error in plugin run: {str(e)}")
            return {"status": "error", "message": str(e)}

    def validate_configuration_action(self, settings, logger):
        """Validate all plugin configuration settings"""
        # Save settings first to ensure any changes in the UI are persisted
        self._save_settings(settings)

        validation_results = []
        has_errors = False

        # 1. Validate hide rules
        try:
            hide_rules_text = settings.get("hide_rules_priority", "").strip()
            hide_rules = self._parse_hide_rules(hide_rules_text, logger)
            if hide_rules:
                validation_results.append(f"✅ Hide Rules: {len(hide_rules)} rules")
            else:
                validation_results.append("⚠️ Hide Rules: Using defaults")
        except Exception as e:
            validation_results.append(f"❌ Hide Rules: {str(e)}")
            has_errors = True

        # 2. Validate regex patterns
        patterns_to_check = [
            ("regex_mark_inactive", "Inactive"),
            ("regex_channels_to_ignore", "Ignore"),
            ("regex_force_visible", "Force Visible")
        ]

        for setting_key, label in patterns_to_check:
            try:
                pattern = settings.get(setting_key, "").strip()
                if pattern:
                    re.compile(pattern, re.IGNORECASE)
                    validation_results.append(f"✅ {label}: Valid")
                else:
                    validation_results.append(f"ℹ️ {label}: Not set")
            except re.error as e:
                validation_results.append(f"❌ {label}: {str(e)}")
                has_errors = True

        # 3. Validate database connectivity
        db_ok = False
        try:
            channel_count = Channel.objects.count()
            profile_count = ChannelProfile.objects.count()
            stream_count = Stream.objects.count()
            validation_results.append(
                f"✅ DB OK ({channel_count} channels, {profile_count} profiles, {stream_count} streams)"
            )
            db_ok = True
        except Exception as e:
            validation_results.append(f"❌ DB error: {str(e)[:50]}")
            has_errors = True

        # 4. Validate channel profile names
        channel_profile_names_str = settings.get("channel_profile_name", "").strip()
        if channel_profile_names_str and db_ok:
            try:
                channel_profile_names = [p.strip() for p in channel_profile_names_str.split(',') if p.strip()]

                found_profiles = []
                missing_profiles = []

                for profile_name in channel_profile_names:
                    if ChannelProfile.objects.filter(name__iexact=profile_name).exists():
                        found_profiles.append(profile_name)
                    else:
                        missing_profiles.append(profile_name)

                if missing_profiles:
                    validation_results.append(f"❌ Profiles: Not found - {', '.join(missing_profiles)}")
                    has_errors = True

                if found_profiles:
                    validation_results.append(f"✅ Profiles: {len(found_profiles)}/{len(channel_profile_names)} - {', '.join(found_profiles)}")

            except Exception as e:
                validation_results.append(f"❌ Profiles: {str(e)}")
                has_errors = True
        elif channel_profile_names_str and not db_ok:
            validation_results.append("⚠️ Profiles: Cannot validate (DB failed)")
        else:
            validation_results.append("❌ Profiles: Required")
            has_errors = True

        # 5. Validate channel groups
        channel_groups_str = settings.get("channel_groups", "").strip()
        if channel_groups_str and db_ok and channel_profile_names_str:
            try:
                from apps.channels.models import ChannelGroup
                group_names = [g.strip() for g in channel_groups_str.split(',') if g.strip()]
                channel_profile_names = [p.strip() for p in channel_profile_names_str.split(',') if p.strip()]

                # Resolve profile IDs case-insensitively — consistent with step 4 and the
                # actual scan (name__iexact). Case-sensitive name__in here used to make
                # Validate contradict Run Now (bug-048).
                profile_ids = []
                for pn in channel_profile_names:
                    profile_ids += list(
                        ChannelProfile.objects.filter(name__iexact=pn).values_list('id', flat=True)
                    )

                if profile_ids:
                    # Group names (casefolded) that actually have >=1 channel in the
                    # configured profile(s).
                    in_profile = {
                        (m.channel.channel_group.name or "").casefold()
                        for m in ChannelProfileMembership.objects.filter(
                            channel_profile_id__in=profile_ids
                        ).select_related('channel', 'channel__channel_group')
                        if m.channel.channel_group
                    }

                    # Distinguish a genuine typo (group absent from Dispatcharr) from a
                    # real group that simply has no channels in this profile (bug-048).
                    found_groups, empty_groups, missing_groups = [], [], []
                    for group_name in group_names:
                        if group_name.casefold() in in_profile:
                            found_groups.append(group_name)
                        elif ChannelGroup.objects.filter(name__iexact=group_name).exists():
                            empty_groups.append(group_name)
                        else:
                            missing_groups.append(group_name)

                    if missing_groups:
                        validation_results.append(f"❌ Groups not found in Dispatcharr: {', '.join(missing_groups)}")
                        has_errors = True
                    if empty_groups:
                        validation_results.append(
                            f"⚠️ Groups with no channels in the selected profile(s) "
                            f"(will match 0 this scan): {', '.join(empty_groups)}")
                    if found_groups:
                        validation_results.append(f"✅ Groups: {len(found_groups)}/{len(group_names)} - {', '.join(found_groups)}")
                else:
                    validation_results.append("⚠️ Groups: Cannot validate (no valid profiles)")

            except Exception as e:
                validation_results.append(f"❌ Groups: {str(e)}")
                has_errors = True
        elif channel_groups_str and not db_ok:
            validation_results.append("⚠️ Groups: Cannot validate (DB failed)")
        elif channel_groups_str and not channel_profile_names_str:
            validation_results.append("⚠️ Groups: Cannot validate (no profiles)")
        else:
            validation_results.append("ℹ️ Groups: Not set (optional)")

        # 5b. Validate the per-group EPG source mapping.
        #
        # Every check here catches a SILENT no-op: a mapping can look configured,
        # create a source, and route nothing for ever. The toast shows roughly 280
        # characters clipped from the middle, and this action already returns 9 to
        # 12 lines, so only a headline and the FIRST problem go into the message.
        # The full list goes to the log, and the effective mapping goes into the
        # CSV header on the next run, which is a file surface that is not clipped.
        raw_map = settings.get("group_epg_source_map", "")
        if str(raw_map or "").strip():
            group_map, map_problems = ecm_profiles.parse_group_source_map(raw_map)
            for problem in map_problems:
                logger.warning(f"{LOG_PREFIX} Group mapping problem: {problem}")

            # Bound locally rather than relying on the names the channel-group
            # block above happens to leave behind: that block only runs when
            # Channel Groups is set, so both ChannelGroup and group_names are
            # UNBOUND when it is blank, and the mapping checks would then be
            # skipped with a misleading "could not be checked".
            scoped_group_names = [g.strip() for g
                                  in channel_groups_str.split(',') if g.strip()]
            notes = []
            if group_map and db_ok:
                try:
                    from apps.channels.models import ChannelGroup as _ChannelGroup
                    from apps.epg.models import EPGSource
                    existing_groups = {
                        (name or "").strip().casefold()
                        for name in _ChannelGroup.objects.values_list("name", flat=True)}
                    unknown = [g for g in group_map if g not in existing_groups]
                    if unknown:
                        notes.append(f"{len(unknown)} group(s) match no channel group")

                    # In scan scope? A group absent from the Channel Groups narrowing
                    # setting never enters the scan, so its mapping moves nothing.
                    if scoped_group_names:
                        scoped = {g.strip().casefold() for g in scoped_group_names}
                        out_of_scope = [g for g in group_map if g not in scoped]
                        if out_of_scope:
                            notes.append(
                                f"{len(out_of_scope)} group(s) are not in Channel "
                                f"Groups above, so they will move nothing")
                            for g in out_of_scope:
                                logger.warning(
                                    f"{LOG_PREFIX} Group mapping problem: {g!r} is "
                                    f"mapped but is not listed in Channel Groups, so "
                                    f"it is never scanned and routes no channels")

                    # A name already used by a non-dummy source can never be created.
                    for source_name in set(group_map.values()):
                        clash = EPGSource.objects.filter(
                            name=source_name).exclude(source_type="dummy").first()
                        if clash is not None:
                            notes.append(f"{source_name!r} is not a dummy source")
                            logger.warning(
                                f"{LOG_PREFIX} Group mapping problem: {source_name!r} "
                                f"already exists as a {clash.source_type!r} source and "
                                f"cannot be used for a channel group")
                except Exception as e:
                    notes.append(f"could not be checked ({e})")

            # Print what the plugin ACTUALLY read. An omitted form field keeps the
            # value cached on disk, so clearing the box may not appear to take
            # effect, and this line is how the operator sees which value won.
            effective = ", ".join(f"{g} -> {s}" for g, s in group_map.items()) or "none"
            if map_problems or notes:
                first = (map_problems[0] if map_problems else notes[0])
                extra = len(map_problems) + len(notes) - 1
                validation_results.append(
                    f"⚠️ Group EPG map ({len(group_map)} in use): {first}"
                    + (f" (+{extra} more, see the log)" if extra > 0 else ""))
                has_errors = True
            else:
                validation_results.append(f"✅ Group EPG map: {effective}")
            logger.info(f"{LOG_PREFIX} Group EPG map in use: {effective}")
        else:
            validation_results.append("ℹ️ Group EPG map: Not set (optional)")

        # 6. Validate schedule
        scheduled_times = settings.get("scheduled_times", "").strip()
        if scheduled_times:
            # Judge the entries with the SAME parser the scheduler arms itself from.
            # The old check here only tested "four digits", which accepts 2400 while
            # the scheduler rejects it, so Validate reported a schedule as good and
            # that run time then never fired.
            rejected = []
            accepted = self._parse_scheduled_times(scheduled_times, rejects=rejected)
            if rejected:
                validation_results.append(
                    "❌ Schedule: " + ", ".join(rejected) + " will be ignored - "
                    "use HHMM between 0000 and 2359 (midnight is 0000, not 2400)")
                has_errors = True
            if accepted:
                validation_results.append(f"✅ Schedule: {len(accepted)} times")
            elif not rejected:
                validation_results.append("⚠️ Schedule: no usable times")
        else:
            validation_results.append("ℹ️ Schedule: Not set")

        message = "\n".join(validation_results)
        return {
            "status": "warning" if has_errors else "success",
            "message": f"Validation:\n{message}"
        }

    def _save_settings(self, settings):
        """Save settings to disk"""
        try:
            # Log what we're about to save
            LOGGER.info("Saving settings to disk:")
            LOGGER.info(f"  enable_scheduled_csv_export: {settings.get('enable_scheduled_csv_export', 'NOT SET')}")
            
            # Ensure boolean defaults are explicitly set if missing
            if "enable_scheduled_csv_export" not in settings:
                LOGGER.info(f"  Setting missing 'enable_scheduled_csv_export', adding default: {self.DEFAULT_SCHEDULED_CSV_EXPORT}")
                settings["enable_scheduled_csv_export"] = self.DEFAULT_SCHEDULED_CSV_EXPORT
            if "auto_rescan_on_m3u_refresh" not in settings:
                LOGGER.info(f"  Setting missing 'auto_rescan_on_m3u_refresh', adding default: {self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH}")
                settings["auto_rescan_on_m3u_refresh"] = self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH
            if "keep_duplicates" not in settings:
                settings["keep_duplicates"] = self.DEFAULT_KEEP_DUPLICATES
            if "auto_set_dummy_epg_on_hide" not in settings:
                settings["auto_set_dummy_epg_on_hide"] = self.DEFAULT_AUTO_REMOVE_EPG
            if "manage_dummy_epg" not in settings:
                settings["manage_dummy_epg"] = self.DEFAULT_MANAGE_DUMMY_EPG
            if "override_existing_epg" not in settings:
                settings["override_existing_epg"] = self.DEFAULT_OVERRIDE_EXISTING_EPG
            if "dummy_epg_event_duration_hours" not in settings:
                settings["dummy_epg_event_duration_hours"] = self.DEFAULT_EVENT_DURATION_HOURS
            if "dummy_epg_event_timezone" not in settings:
                settings["dummy_epg_event_timezone"] = self.DEFAULT_DUMMY_EPG_TIMEZONE
            if "dummy_epg_channel_format" not in settings:
                settings["dummy_epg_channel_format"] = self.DEFAULT_DUMMY_EPG_CHANNEL_FORMAT
            if "rate_limiting" not in settings:
                settings["rate_limiting"] = self.DEFAULT_RATE_LIMITING

            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            self.saved_settings = settings
            LOGGER.info(f"Settings saved successfully to {self.settings_file}")
            LOGGER.info(f"  Final value of enable_scheduled_csv_export: {settings.get('enable_scheduled_csv_export')}")
        except Exception as e:
            LOGGER.error(f"Error saving settings: {e}")

    def _load_undated_tracker(self, logger):
        """Load the undated-channel first-seen tracker from disk."""
        path = PluginConfig.UNDATED_FIRST_SEEN_FILE
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(f"{LOG_PREFIX} Undated tracker at {path} is not a dict; starting fresh.")
                return {}
            return data
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"{LOG_PREFIX} Could not load undated tracker ({e}); starting fresh.")
            return {}

    def _save_undated_tracker(self, tracker, logger):
        """Atomically save the undated-channel first-seen tracker to disk. Returns True on success."""
        path = PluginConfig.UNDATED_FIRST_SEEN_FILE
        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, 'w') as f:
                json.dump(tracker, f, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
            return True
        except OSError as e:
            logger.error(f"{LOG_PREFIX} Failed to save undated tracker: {e}")
            return False

    def _load_group_source_record(self, logger):
        """Load the record of dummy EPG sources this plugin created for a mapping.

        Returns a dict keyed by the exact EPGSource.name. Any failure returns an
        EMPTY dict, which makes every channel ineligible for the reverse move, so
        a lost or corrupt file leaves channels where they are rather than moving
        them somewhere they do not belong.
        """
        path = PluginConfig.GROUP_SOURCE_RECORD_FILE
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(f"{LOG_PREFIX} Group source record at {path} is not a "
                               f"dict; treating as empty, so no channel returns to "
                               f"the shared source this run.")
                return {}
            return data
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"{LOG_PREFIX} Could not load group source record ({e}); "
                           f"treating as empty, so no channel returns to the shared "
                           f"source this run.")
            return {}

    def _save_group_source_record(self, record, logger):
        """Atomically save the group source record. Returns True on success."""
        path = PluginConfig.GROUP_SOURCE_RECORD_FILE
        tmp_path = f"{path}.tmp"
        try:
            with open(tmp_path, 'w') as f:
                json.dump(record, f, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
            return True
        except OSError as e:
            logger.error(f"{LOG_PREFIX} Failed to save group source record: {e}")
            return False

    def _record_group_source(self, source, group_names, logger):
        """Record that this plugin created `source` for a group mapping.

        Written at CREATION only. A source that already existed is not recorded,
        because the plugin did not create it and must not later take channels off
        it; that case is reported by Validate Configuration instead.
        """
        record = self._load_group_source_record(logger)
        if source.name in record:
            return record
        record[source.name] = {
            "created": datetime.now().strftime('%Y-%m-%d'),
            "created_for_groups": sorted(group_names or ()),
            "source_id": source.id,
        }
        self._save_group_source_record(record, logger)
        logger.info(f"{LOG_PREFIX} Recorded {source.name!r} as plugin-created for "
                    f"group(s): {', '.join(sorted(group_names or ())) or 'none'}")
        return record

    def _record_undated_channel(self, tracker, channel_id, channel_name, today_str,
                                now_iso=None):
        """Record/refresh a channel in the undated tracker. Returns the entry.

        `now_iso` stamps the entry with the moment it was created, which [UndatedEnded]
        needs in order to reject an inferred event window that closed before the channel
        was ever seen. It is optional so an entry written by an older version, which
        carries the date only, still loads and is still usable by [UndatedAge:N].
        """
        key = str(channel_id)
        entry = tracker.get(key)
        if not entry or entry.get("name") != channel_name:
            entry = {"first_seen": today_str, "name": channel_name}
            if now_iso:
                entry["first_seen_at"] = now_iso
            tracker[key] = entry
        return entry

    def _append_ledger_entry(self, shown, hidden, is_scheduled_run, logger):
        """Record what one applied run changed. Never raises.

        Called only after the visibility transaction has committed, so a line
        describes changes that really happened rather than changes that were
        planned. `shown` and `hidden` are TRANSITION counts: channels whose
        visibility actually flipped this run. Counting the channels that were
        already hidden would re-count the same channel on every scheduled run
        and turn a total of work done into a meaningless multiple of it.

        A failure here must never fail a scan. The ledger is a reporting
        convenience; the channels are the product.
        """
        try:
            # These constants live on PluginConfig, NOT on Plugin. `self.LEDGER_FILE`
            # raised AttributeError on every applied run that changed something, and
            # the broad `except Exception` below swallowed it into a WARNING nobody
            # reads, so the ledger stayed empty and the badge stayed at 0 while every
            # other signal looked healthy. Every other file path in this class is
            # reached the same way; follow it.
            path = PluginConfig.LEDGER_FILE
            try:
                if os.path.getsize(path) >= PluginConfig.LEDGER_MAX_BYTES:
                    os.replace(path, path + ".1")
            except OSError:
                pass  # Missing file on the first run, or an unreadable size.

            entry = {
                # `timezone` here is django.utils.timezone, not datetime.timezone.
                # timezone.now() is the idiom this module already uses and returns
                # an aware UTC datetime; datetime.timezone.utc is NOT reachable
                # through this name, and django.utils.timezone.utc was removed in
                # Django 5.
                "ts": timezone.now().isoformat(),
                "version": self.version,
                "shown": int(shown),
                "hidden": int(hidden),
                "scheduled": bool(is_scheduled_run),
            }
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} Could not append to the run ledger ({e}); "
                           f"the scan itself is unaffected.")

    def _parse_hide_rules(self, rules_text, logger):
        """Parse hide rules priority text into list of rule tuples"""
        if not rules_text or not rules_text.strip():
            # Return default rules if none specified
            rules_text = self.DEFAULT_HIDE_RULES
            logger.info("No hide rules specified, using defaults")
        
        rules = []
        
        # Check if rules are comma-separated or newline-separated
        # If there are newlines and no commas outside of brackets, use newline splitting
        # Otherwise, use comma splitting (new format)
        if '\n' in rules_text and ',' not in rules_text:
            # Legacy format: newline-separated
            rule_items = rules_text.strip().split('\n')
        else:
            # New format: comma-separated
            # Split by comma, but need to handle commas that might appear in rule content
            rule_items = []
            current_rule = ""
            bracket_depth = 0

            for char in rules_text:
                if char == '[':
                    bracket_depth += 1
                    current_rule += char
                elif char == ']':
                    bracket_depth -= 1
                    current_rule += char
                elif char == ',' and bracket_depth == 0:
                    # This comma is a separator, not part of rule content
                    if current_rule.strip():
                        rule_items.append(current_rule.strip())
                    current_rule = ""
                else:
                    current_rule += char

            # Add the last rule
            if current_rule.strip():
                rule_items.append(current_rule.strip())

        # Parse each rule item
        for line in rule_items:
            line = line.strip()
            if not line or not line.startswith('[') or not line.endswith(']'):
                continue
            
            # Extract rule name and parameter
            rule_content = line[1:-1]  # Remove [ and ]

            if ':' in rule_content:
                parts = rule_content.split(':')
                rule_name = parts[0]

                # Support format: [PastDate:0:4h] for days:grace_hours
                if len(parts) == 3 and parts[2].endswith('h'):
                    try:
                        days_param = int(parts[1])
                        grace_hours = int(parts[2][:-1])  # Remove 'h' and convert
                        rules.append((rule_name, (days_param, grace_hours)))
                    except ValueError:
                        logger.warning(f"Invalid multi-parameter in rule '{line}', skipping")
                        continue
                elif len(parts) == 2:
                    try:
                        param = int(parts[1])
                        rules.append((rule_name, param))
                    except ValueError:
                        logger.warning(f"Invalid parameter in rule '{line}', skipping")
                        continue
                else:
                    logger.warning(f"Invalid rule format '{line}', skipping")
                    continue
            else:
                rules.append((rule_content, None))
        
        logger.info(f"Parsed {len(rules)} hide rules: {[r[0] + (f':{r[1]}' if r[1] is not None else '') for r in rules]}")
        return rules

    def _extract_day_of_week_from_channel_name(self, channel_name, logger):
        """Extract day of week from channel name if present"""
        if not channel_name:
            return None

        # Map day names to day numbers (0 = Monday, 6 = Sunday)
        day_patterns = {
            'MONDAY': 0,
            'TUESDAY': 1,
            'WEDNESDAY': 2,
            'THURSDAY': 3,
            'FRIDAY': 4,
            'SATURDAY': 5,
            'SUNDAY': 6,
            # Short forms
            'MON': 0,
            'TUE': 1,
            'TUES': 1,
            'WED': 2,
            'THU': 3,
            'THUR': 3,
            'THURS': 3,
            'FRI': 4,
            'SAT': 5,
            'SUN': 6,
            # NFL abbreviations
            'MNF': 0,  # Monday Night Football
            'TNF': 3,  # Thursday Night Football
            'SNF': 6   # Sunday Night Football
        }

        # Search for day names in the channel name
        # Use word boundaries to avoid matching parts of other words
        channel_name_upper = channel_name.upper()

        for day_name, day_number in day_patterns.items():
            # Use word boundary to match whole words only
            pattern = r'\b' + day_name + r'\b'
            if re.search(pattern, channel_name_upper):
                logger.debug(f"Found day name '{day_name}' in channel name: '{channel_name}'")
                return day_number

        return None

    def _resolve_numeric_date_pair(self, first, second, current_year, date_format):
        """Resolve a (first, second) numeric pair into a datetime using the configured format.

        date_format: "US" → MM/DD, "EU" → DD/MM, "Auto" → MM/DD with DD/MM fallback if month > 12.
        Returns datetime or None if the pair can't form a valid date. Delegates to ecm_parsing.
        """
        return ecm_parsing.resolve_numeric_date_pair(first, second, current_year, date_format)

    def _name_has_stop_timestamp(self, channel_name):
        """True if the channel name carries an explicit `stop:YYYY-MM-DD HH:MM:SS`
        event-end timestamp. [PastDate] uses this to compare the real end time rather
        than just the calendar date (issue #22). Delegates to ecm_parsing."""
        return ecm_parsing.name_has_stop_timestamp(channel_name)

    def _extract_date_from_channel_name(self, channel_name, logger, settings=None, prefer="start"):
        """Extract date from channel name using various patterns, including hour if present.

        When a name carries both `start:` and `stop:` timestamps, `prefer` selects which one
        Pattern 0 returns: `prefer="start"` (default) for rules asking "when does it start /
        how far out is it" ([FutureDate], [UndatedAge], NoEPG); `prefer="stop"` for [PastDate],
        which asks "has the event ended?" (issue #22). Falls back to the other prefix when the
        preferred one is absent, so single-timestamp names are unaffected.
        """
        date_format = (settings or {}).get("date_format", "Auto")
        return ecm_parsing.extract_date_from_channel_name(
            channel_name, date_format=date_format, prefer=prefer, logger=logger
        )


    def _warn_undated_once(self, logger, key, message):
        """Report a configuration problem once per scan instead of once per channel.

        A scan evaluates every channel in scope, over 1400 on the installation this was
        measured on, so an unguarded warning would repeat a single mistyped setting once
        per channel and bury the log. `key` identifies the problem, not the channel, so
        the same mistake on the same EPG source is reported once however many channels
        it affects.

        The set is created on demand rather than read with a default, so an entry path
        that never primed it still warns. A missing set must not turn into silence: the
        whole point of these messages is that the substitution they describe is otherwise
        invisible (bug-139 is the same shape, where settings-derived instance state read
        with getattr failed silently on an unprimed path).
        """
        seen = getattr(self, '_undated_warned', None)
        if seen is None:
            seen = set()
            self._undated_warned = seen
        if key in seen:
            return
        seen.add(key)
        logger.warning(f"{LOG_PREFIX} {message}")

    def _undated_event_properties(self, channel, settings, logger=None):
        """The time pattern, timezone and duration to use for one undated channel.

        A channel bound to a dummy EPG source is rendered from that source's own
        properties, so an installation running more than one managed source (one per
        provider timezone, for example) must infer the event window from the source the
        channel actually sits on. Anything the source does not supply falls back to the
        plugin settings, which is also the whole answer for a channel that is bound to
        nothing yet.

        Never raises: a missing relation, a missing property or a property of the wrong
        shape falls back rather than failing, because the caller must be able to leave
        the channel visible instead of hiding it on a bad read.

        `logger` is optional only so an existing caller without one keeps working. When
        it is given, every substitution this method makes for a property that IS present
        but unreadable is reported once per scan. An ABSENT property is not reported:
        that is the ordinary case and says nothing about a mistake.
        """
        tz_name = str(settings.get("dummy_epg_event_timezone",
                                   self.DEFAULT_DUMMY_EPG_TIMEZONE)).strip()
        try:
            duration_hours = int(str(settings.get(
                "dummy_epg_event_duration_hours", self.DEFAULT_EVENT_DURATION_HOURS)).strip())
        except (ValueError, TypeError):
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
        if duration_hours <= 0:
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
        duration_minutes = duration_hours * 60
        time_pattern = None

        try:
            source = channel.epg_data.epg_source
        except AttributeError:
            source = None
        if source is not None and getattr(source, "source_type", None) == "dummy":
            props = source.custom_properties
            if isinstance(props, dict):
                if props.get("time_pattern"):
                    time_pattern = props["time_pattern"]
                    problem = ecm_parsing.time_pattern_problem(time_pattern)
                    if problem and logger is not None:
                        self._warn_undated_once(
                            logger, f"time_pattern:{source.id}",
                            f"[UndatedEnded] EPG source {source.id} "
                            f"({getattr(source, 'name', '?')}) has a time pattern that "
                            f"cannot be compiled ({problem}), so the built-in pattern is "
                            f"being used instead of yours. Fix the Time Pattern on that "
                            f"source in Dispatcharr, or this rule reads event times with "
                            f"a pattern you did not choose.")
                if props.get("timezone"):
                    tz_name = str(props["timezone"]).strip()
                raw_duration = props.get("program_duration")
                if raw_duration is not None:
                    try:
                        from_source = int(raw_duration)
                    except (TypeError, ValueError):
                        # Present but unreadable is a typing mistake, not the ordinary
                        # absent case, and the substituted value is reported in the hide
                        # reason as though it were configured. Say so.
                        if logger is not None:
                            self._warn_undated_once(
                                logger, f"program_duration:{source.id}",
                                f"[UndatedEnded] EPG source {source.id} "
                                f"({getattr(source, 'name', '?')}) has a program duration "
                                f"of {raw_duration!r}, which is not a whole number of "
                                f"minutes. Falling back to the Event Duration setting "
                                f"({duration_minutes // 60}h), which may hide channels "
                                f"earlier than you intended.")
                    else:
                        if from_source > 0:
                            duration_minutes = from_source
        return time_pattern, tz_name, duration_minutes


    def _check_hide_rule(self, rule_name, rule_param, channel, channel_name, logger, settings):
        """Check if a single hide rule matches the channel. Returns (matches, reason)"""
        # Safety checks for malformed channel names
        if not channel_name:
            return False, None

        # Truncate extremely long channel names to prevent performance issues
        if len(channel_name) > 500:
            channel_name = channel_name[:500]
            logger.warning(f"Channel name truncated (too long): {channel_name[:50]}...")

        if rule_name == "NoEPG":
            # Hide if no EPG assigned at all
            if not channel.epg_data:
                return True, "[NoEPG] No EPG assigned to channel"

            # Skip check for custom dummy EPG sources (they generate programs on-demand, not stored in DB)
            # Custom dummy EPG is identified by: channel.epg_data.epg_source.source_type == 'dummy'
            try:
                if channel.epg_data.epg_source.source_type == 'dummy':
                    logger.debug(f"Skipping NoEPG check for custom dummy EPG on channel: {channel_name}")
                    return False, None
            except AttributeError:
                # If epg_source or source_type doesn't exist, treat as regular EPG
                pass

            # Hide if EPG is assigned but has no program data for the next 24 hours
            now = timezone.now()
            next_24h = now + timedelta(hours=24)
            has_programs = ProgramData.objects.filter(
                epg=channel.epg_data,
                start_time__lt=next_24h,
                end_time__gte=now
            ).exists()
            if not has_programs:
                return True, "[NoEPG] No EPG program data for next 24 hours"

            return False, None
        
        elif rule_name == "BlankName":
            if not channel_name.strip():
                return True, "[BlankName] Channel name is blank"
            return False, None

        elif rule_name == "WrongDayOfWeek":
            # Hide if channel name contains a day of week that is NOT today
            extracted_day = self._extract_day_of_week_from_channel_name(channel_name, logger)
            if extracted_day is None:
                return False, None  # Skip rule if no day found

            # Get today's day of week using user's timezone (0 = Monday, 6 = Sunday)
            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)

            now_in_tz = datetime.now(local_tz)
            today_day = now_in_tz.weekday()

            # ±1 day tolerance: a channel named for a US/EU day can roll over the
            # viewer's local calendar (e.g. "Monday Night Football" is Tuesday in
            # Australia). Earth's TZ span is UTC-12..UTC+14, so the named day will
            # always be within ±1 of the viewer's day for any live event.
            allowed_days = {(today_day - 1) % 7, today_day, (today_day + 1) % 7}

            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            extracted_day_name = day_names[extracted_day]
            today_day_name = day_names[today_day]

            if extracted_day not in allowed_days:
                return True, f"[WrongDayOfWeek] Channel is for {extracted_day_name}, but today is {today_day_name}"

            if extracted_day != today_day:
                logger.debug(
                    f"[WrongDayOfWeek] allowing '{channel_name}': named day {extracted_day_name} "
                    f"is within ±1 of today ({today_day_name}) in {tz_str} — cross-TZ rollover tolerance"
                )
            return False, None

        elif rule_name == "NoEventPattern":
            # Match variations: no event, no events, offline, no games scheduled, no scheduled event
            no_event_pattern = re.compile(
                r'\b(no[_\s-]?events?|offline|no[_\s-]?games?[_\s-]?scheduled|no[_\s-]?scheduled[_\s-]?events?)\b', 
                re.IGNORECASE
            )
            if no_event_pattern.search(channel_name):
                return True, "[NoEventPattern] Name contains 'no event(s)', 'offline', or 'no games/scheduled'"
            return False, None
        
        elif rule_name == "EmptyPlaceholder":
            # Literal date/time template tokens inside a parenthesized run
            # (e.g. "(MM.DD h:mmAM/PM ET)") indicate an unpopulated stub channel.
            # Scoped to parens so prose like "(times shown AM/PM ET)" in legitimate
            # names doesn't false-trigger.
            if re.search(r'\([^)]*\b(MM[./]DD|DD[./]MM|YYYY|hh?:mm|AM/PM)\b[^)]*\)', channel_name):
                return True, "[EmptyPlaceholder] Channel name contains literal template tokens"

            # Ends with colon, pipe, or dash with nothing or only whitespace/very short content after.
            # The `(?=\s|$)` lookahead after the colon excludes time-colons like "7:00AM" / "9:45am"
            # (colon followed by a digit) while still matching real separator colons like
            # "PPV 12: Title" or trailing-empty-colon "PPV 25:".
            colon_match = re.search(r':(?=\s|$)(.*)$', channel_name)
            if colon_match:
                content_after = colon_match.group(1).strip()
                if not content_after or len(content_after) <= 2:
                    return True, f"[EmptyPlaceholder] Empty or minimal content after colon ({len(content_after)} chars)"

            pipe_match = re.search(r'\|(.*)$', channel_name)
            if pipe_match:
                content_after = pipe_match.group(1).strip()
                if not content_after or len(content_after) <= 2:
                    return True, f"[EmptyPlaceholder] Empty or minimal content after pipe ({len(content_after)} chars)"

            # Match dash as separator (whitespace followed by dash near end of string)
            dash_match = re.search(r'\s-\s*$', channel_name)
            if dash_match:
                # Get content after the last dash
                content_after = channel_name[dash_match.end():].strip()
                if not content_after or len(content_after) <= 2:
                    return True, f"[EmptyPlaceholder] Empty or minimal content after dash ({len(content_after)} chars)"

            return False, None
        
        elif rule_name == "ShortDescription":
            # The cutoff is [ShortDescription:N], defaulting to the 15 this rule
            # always used. The rule parser has always accepted a number here and
            # the rule then ignored it, so a user who wrote [ShortDescription:20]
            # silently got 15 and nothing said otherwise. Measuring lives in
            # ecm_parsing so it can be unit-tested outside the container.
            threshold = rule_param if rule_param is not None else ecm_parsing.SHORT_DESCRIPTION_DEFAULT
            hit = ecm_parsing.short_description_match(channel_name, threshold)
            if hit:
                separator, length = hit
                return True, (f"[ShortDescription:{threshold}] Description after {separator} "
                              f"too short ({length} chars, threshold: {threshold})")

            return False, None

        elif rule_name == "ShortChannelName":
            # The cutoff is [ShortChannelName:N], defaulting to the 25 this rule
            # always used, for the same reason as [ShortDescription:N] above.
            #
            # The measurement (in ecm_parsing) collapses whitespace first, and
            # treats a name as having no separator when it has no colon followed
            # by whitespace, no pipe and no spaced dash. The colon test requires
            # content after it, so a trailing empty colon ("PPV 25:") still counts
            # as "no separator" here, matching the behaviour this rule has always
            # had; [EmptyPlaceholder] catches those earlier in the rule chain.
            threshold = rule_param if rule_param is not None else ecm_parsing.SHORT_CHANNEL_NAME_DEFAULT
            length = ecm_parsing.short_channel_name_match(channel_name, threshold)
            if length is not None:
                return True, (f"[ShortChannelName:{threshold}] Name too short without event "
                              f"details ({length} chars, threshold: {threshold})")

            return False, None

        elif rule_name == "NumberOnly":
            # Hide channels that are just prefix + number (e.g., "PPV 12", "EVENT 15")
            # Match pattern: word(s) followed by whitespace and number(s) only
            try:
                normalized_name = re.sub(r'\s+', ' ', channel_name.strip())

                # Pattern: One or more words, then space(s), then only digits
                number_only_pattern = r'^[A-Za-z\s]+\d+\s*$'

                if re.match(number_only_pattern, normalized_name):
                    # Additional check: make sure there's no colon, pipe, or dash separators
                    if ':' not in normalized_name and '|' not in normalized_name and ' - ' not in normalized_name:
                        return True, f"[NumberOnly] Channel name is just prefix + number: '{normalized_name}'"
            except Exception as e:
                logger.warning(f"Error in NumberOnly rule for '{channel_name}': {str(e)}")

            return False, None

        elif rule_name == "PastDate":
            # Use the event's stop: time when present ("has it ended?"), falling back to
            # start:/other date patterns otherwise (issue #22).
            extracted_date = self._extract_date_from_channel_name(channel_name, logger, settings, prefer="stop")
            if extracted_date is None:
                return False, None  # Skip rule if no date found

            # Handle both single param (days) and tuple param (days, grace_hours)
            if isinstance(rule_param, tuple):
                days_threshold, grace_hours = rule_param
            else:
                days_threshold = rule_param if rule_param is not None else 0
                # Fall back to global grace period setting
                grace_hours_str = settings.get("past_date_grace_hours", "0")
                try:
                    grace_hours = int(grace_hours_str)
                except (ValueError, TypeError):
                    grace_hours = 0

            # Adjust the current time by the grace period and user's timezone
            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)

            now_in_tz = datetime.now(local_tz)
            naive_extracted = extracted_date  # parser returns a naive datetime

            # Make extracted_date timezone-aware for correct comparison if it's naive
            if extracted_date.tzinfo is None:
                extracted_date = local_tz.localize(extracted_date)

            # When the name carries an explicit stop: timestamp, compare the actual event
            # end datetime so an event that ended earlier *today* is hidden once stop: +
            # grace has elapsed, instead of staying visible until the next calendar day
            # (issue #22). extracted_date is already the stop: time here (prefer="stop").
            if self._name_has_stop_timestamp(channel_name):
                cutoff = extracted_date + timedelta(days=days_threshold, hours=grace_hours)
                if now_in_tz > cutoff:
                    return True, f"[PastDate:{days_threshold}] Event ended {extracted_date.strftime('%m/%d/%Y %I:%M %p')} (past stop: + {days_threshold}d/{grace_hours}h grace)"
                return False, None

            # When the name carries a clock time but no stop: (e.g. "(6.19 7:30 PM ET)"),
            # judge by the real event time instead of the calendar date: assume the event
            # ends `dummy_epg_event_duration_hours` after it starts and hide once end +
            # threshold/grace has passed (bug-046). The name's clock is in the event
            # timezone (dummy_epg_event_timezone). This stops a 10pm event from being
            # hidden minutes into its broadcast just because the calendar rolled past
            # midnight. Names with no parseable time keep the day-granularity path below.
            if naive_extracted.hour != 0 or naive_extracted.minute != 0:
                try:
                    event_tz = pytz.timezone(str(settings.get(
                        "dummy_epg_event_timezone", self.DEFAULT_DUMMY_EPG_TIMEZONE)).strip())
                except Exception:
                    event_tz = local_tz
                try:
                    duration_hours = int(str(settings.get(
                        "dummy_epg_event_duration_hours", self.DEFAULT_EVENT_DURATION_HOURS)).strip())
                except (ValueError, TypeError):
                    duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
                if duration_hours <= 0:
                    duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
                start_aware = event_tz.localize(naive_extracted)
                cutoff = start_aware + timedelta(hours=duration_hours, days=days_threshold) + timedelta(hours=grace_hours)
                if now_in_tz > cutoff:
                    return True, f"[PastDate:{days_threshold}] Event ended {start_aware.strftime('%m/%d %I:%M %p %Z')} (+{duration_hours}h dur, {days_threshold}d/{grace_hours}h grace)"
                return False, None

            now_adjusted = now_in_tz - timedelta(hours=grace_hours)
            days_diff = (now_adjusted.date() - extracted_date.date()).days

            if days_diff > days_threshold:
                return True, f"[PastDate:{days_threshold}] Event date {extracted_date.strftime('%m/%d/%Y')} is {days_diff} days in the past (grace period: {grace_hours}h)"

            return False, None
        
        elif rule_name == "FutureDate":
            extracted_date = self._extract_date_from_channel_name(channel_name, logger, settings)
            if extracted_date is None:
                return False, None  # Skip rule if no date found
            
            days_threshold = rule_param if rule_param is not None else 14
            # Resolve "today" in the configured Dispatcharr timezone, consistent with
            # [PastDate]/[WrongDayOfWeek]/[UndatedAge]; a naive datetime.now() here used
            # the container's wall clock (often UTC) and could shift the future-day
            # boundary by a day for non-UTC users (bug: FutureDate naive now).
            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)
            today = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
            days_diff = (extracted_date - today).days

            if days_diff > days_threshold:
                return True, f"[FutureDate:{days_threshold}] Event date {extracted_date.strftime('%m/%d/%Y')} is {days_diff} days in the future"
            
            return False, None
        
        elif rule_name == "UndatedAge":
            tracker = getattr(self, '_undated_tracker', None) or {}
            entry = tracker.get(str(channel.id))
            if not entry:
                return False, None
            try:
                first_seen = datetime.strptime(entry['first_seen'], '%Y-%m-%d').date()
            except (KeyError, ValueError, TypeError):
                return False, None

            # Accept [UndatedAge:N] or, defensively, [UndatedAge:N:Xh] (grace hours ignored —
            # undated age is day-granular).
            if isinstance(rule_param, tuple):
                threshold = rule_param[0]
            else:
                threshold = rule_param if rule_param is not None else 2

            today_str = getattr(self, '_undated_today_str', None)
            if today_str:
                today = datetime.strptime(today_str, '%Y-%m-%d').date()
            else:
                tz_str = self._get_system_timezone(settings)
                try:
                    local_tz = pytz.timezone(tz_str)
                except pytz.exceptions.UnknownTimeZoneError:
                    local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)
                today = datetime.now(local_tz).date()

            age_days = (today - first_seen).days
            if age_days > threshold:
                return True, f"[UndatedAge:{threshold}] No date in name; first seen {first_seen.isoformat()} ({age_days} days ago, threshold: {threshold})"
            return False, None

        elif rule_name == "UndatedEnded":
            # A name with a clock time but no date. [UndatedAge:N] can only count whole
            # calendar days, so it hides a late event at midnight or keeps a finished one
            # until tomorrow. This builds the real window instead: the date the channel
            # was first seen, plus the time in the name, plus the programme duration and
            # the configured grace period.
            #
            # Fails open at every step. A channel with no record, no parseable time or an
            # unusable timezone returns no match, which leaves it visible and lets
            # [UndatedAge:N] later in the rule list make the decision it makes today.
            tracker = getattr(self, '_undated_tracker', None) or {}
            entry = tracker.get(str(channel.id))
            if not entry:
                return False, None
            try:
                first_seen = datetime.strptime(entry['first_seen'], '%Y-%m-%d').date()
            except (KeyError, ValueError, TypeError):
                return False, None

            time_pattern, tz_name, duration_minutes = self._undated_event_properties(
                channel, settings, logger)
            parsed_time = ecm_parsing.extract_time_of_day(channel_name, time_pattern)
            if parsed_time is None:
                return False, None

            # The tag may carry its own hour count, as [UndatedEnded:2]. Without one the
            # rule reads the setting, which is what the requester asked for in issue 28.
            if isinstance(rule_param, tuple):
                grace_hours = rule_param[0]
            elif rule_param is not None:
                grace_hours = rule_param
            else:
                raw_grace = settings.get("undated_event_grace_hours",
                                         self.DEFAULT_UNDATED_EVENT_GRACE_HOURS)
                try:
                    # Read through float first. The field is declared as a number, so a
                    # value like 12.5 can be stored, and int() on that string raises. It
                    # used to fall back to the shipped default of 1 hour, turning a
                    # deliberate 12 hour grace period into an 11.5 hour early hide.
                    grace_hours = int(float(str(raw_grace).strip()))
                except (ValueError, TypeError):
                    grace_hours = int(self.DEFAULT_UNDATED_EVENT_GRACE_HOURS)
                    self._warn_undated_once(
                        logger, "grace_setting",
                        f"[UndatedEnded] Undated Event Grace Period is {raw_grace!r}, "
                        f"which is not a number of hours. Using the default of "
                        f"{grace_hours}h instead, which may hide channels earlier than "
                        f"you intended.")

            window = ecm_parsing.infer_undated_event_window(
                first_seen, parsed_time[0], parsed_time[1], tz_name,
                duration_minutes, grace_hours)
            if window is None:
                # The timezone is the input most likely to be wrong, and it is the one a
                # person types. A mistyped zone also gives Dispatcharr's own renderer the
                # wrong programme times, so this rule may be the only thing that notices.
                self._warn_undated_once(
                    logger, f"window:{tz_name}",
                    f"[UndatedEnded] Cannot build an event window using timezone "
                    f"{tz_name!r}. Channels with an undated event time are being left "
                    f"visible for this rule to avoid hiding them wrongly. Check the "
                    f"Timezone on the dummy EPG source, or the Channel Name Event "
                    f"Timezone setting.")
                return False, None
            start, hide_after = window

            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)

            # The moment this channel was first recorded, when the record carries one.
            # A record written before this stamp existed carries the date only and
            # applies no such check, which keeps the rule working for it.
            first_seen_at = None
            raw_first_seen_at = entry.get('first_seen_at')
            if raw_first_seen_at:
                try:
                    first_seen_at = datetime.fromisoformat(raw_first_seen_at)
                except (TypeError, ValueError):
                    first_seen_at = None

            if ecm_parsing.undated_event_has_ended(
                    datetime.now(local_tz), hide_after, first_seen_at):
                return True, (
                    f"[UndatedEnded] No date in name; first seen {first_seen.isoformat()}, "
                    f"inferred start {start.strftime('%m/%d %I:%M %p %Z')} "
                    f"(+{duration_minutes // 60}h duration, {grace_hours}h grace)")
            return False, None

        elif rule_name == "InactiveRegex":
            regex_inactive_str = settings.get("regex_mark_inactive", "").strip()
            logger.debug(f"[InactiveRegex] Checking pattern '{regex_inactive_str}' against channel name '{channel_name}'")
            if regex_inactive_str:
                try:
                    # Un-escape backslashes from the JSON string before compiling
                    unescaped_regex_str = bytes(regex_inactive_str, "utf-8").decode("unicode_escape")
                    logger.debug(f"[InactiveRegex] Compiling unescaped pattern: '{unescaped_regex_str}'")
                    regex_inactive = re.compile(unescaped_regex_str, re.IGNORECASE)
                    if regex_inactive.search(channel_name):
                        return True, f"[InactiveRegex] Matches pattern: {regex_inactive_str}"
                except re.error as e:
                    logger.warning(f"Invalid InactiveRegex pattern '{regex_inactive_str}': {e}")
            
            return False, None
        
        else:
            logger.warning(f"Unknown hide rule: {rule_name}")
            return False, None

    def _get_effective_name(self, channel, settings, logger):
        """
        Returns the correct name to use for pattern matching.
        If 'Stream Name' is selected in settings, it retrieves the associated stream name.
        Otherwise, it uses the channel name.
        """

        try:
            name_source = settings.get("name_source", "Channel_Name")
            effective_name = channel.name or ""

            if name_source == "Stream_Name":
                streams = getattr(channel, "streams", None)
                if streams:
                    ordered_streams = streams.order_by("channelstream__order")
                    if ordered_streams.exists():
                        first_stream = ordered_streams.first()
                        if first_stream and getattr(first_stream, "name", None):
                            effective_name = first_stream.name
                            logger.debug(f"Using stream name for channel {channel.id}: {effective_name}")
                        else:
                            logger.debug(f"Channel {channel.id} has streams but no valid stream.name")
                    else:
                        logger.debug(f"Channel {channel.id} has no ordered streams")
                else:
                    logger.debug(f"Channel {channel.id} has no 'streams' relation")

            return effective_name

        except Exception as e:
            logger.warning(f"Error fetching effective name for channel {getattr(channel, 'id', '?')}: {e}")
            return channel.name or ""



    def _check_channel_should_hide(self, channel, hide_rules, logger, settings):
        """Check if channel should be hidden based on hide rules priority. Returns (should_hide, reason)"""
        channel_name = self._get_effective_name(channel, settings, logger)

        # Process rules in order - first match wins
        for rule_name, rule_param in hide_rules:
            matches, reason = self._check_hide_rule(rule_name, rule_param, channel, channel_name, logger, settings)
            if matches:
                return True, reason

        # No rules matched - channel should be visible
        return False, "Has event"
            
    def cleanup_periodic_tasks_action(self, settings, logger):
        """Remove orphaned Celery periodic tasks from old plugin versions"""
        try:
            from django_celery_beat.models import PeriodicTask
            
            # Find all periodic tasks created by this plugin
            tasks = PeriodicTask.objects.filter(name__startswith='event_channel_managarr_')
            task_count = tasks.count()
            
            if task_count == 0:
                return {
                    "status": "success",
                    "message": "No orphaned periodic tasks found. Database is clean!"
                }
            
            # Get task names before deletion
            task_names = list(tasks.values_list('name', flat=True))
            
            # Delete the tasks
            deleted = tasks.delete()
            
            logger.info(f"Deleted {deleted[0]} orphaned periodic tasks")
            
            message_parts = [
                f"Successfully removed {task_count} orphaned Celery periodic task(s):",
                ""
            ]
            
            # Show deleted task names
            for task_name in task_names[:10]:
                message_parts.append(f"• {task_name}")
            
            if len(task_names) > 10:
                message_parts.append(f"• ... and {len(task_names) - 10} more tasks")
            
            message_parts.append("")
            message_parts.append("These were leftover from older plugin versions that used Celery scheduling.")
            message_parts.append("The plugin now uses background threading instead.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except ImportError:
            return {
                "status": "error",
                "message": "django_celery_beat not available. No cleanup needed."
            }
        except Exception as e:
            logger.error(f"Error cleaning up periodic tasks: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error cleaning up periodic tasks: {e}"}
        
    def clear_csv_exports_action(self, settings, logger):
        """Delete all CSV export files created by this plugin"""
        try:
            export_dir = PluginConfig.EXPORTS_DIR
            
            if not os.path.exists(export_dir):
                return {
                    "status": "success",
                    "message": "No export directory found. No files to delete."
                }
            
            # Find all CSV files created by this plugin
            deleted_count = 0
            
            for filename in os.listdir(export_dir):
                if ((filename.startswith("event_channel_managarr_") or filename.startswith("epg_removal_")) 
                    and filename.endswith(".csv")):
                    filepath = os.path.join(export_dir, filename)
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        logger.info(f"Deleted CSV file: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {filename}: {e}")
            
            if deleted_count == 0:
                return {
                    "status": "success",
                    "message": "No CSV export files found to delete."
                }
            
            return {
                "status": "success",
                "message": f"Successfully deleted {deleted_count} CSV export file(s)."
            }
            
        except Exception as e:
            logger.error(f"Error clearing CSV exports: {e}")
            return {"status": "error", "message": f"Error clearing CSV exports: {e}"}

    def check_scheduler_status_action(self, settings, logger):
        """Display scheduler status and diagnostic information.

        NOTE ON SCOPE: Dispatcharr runs under uwsgi with multiple worker processes,
        and each worker loads the plugin independently and starts its own scheduler
        thread. `threading.enumerate()` only sees threads in the single worker that
        handled this HTTP request, so the "Threads in this worker" count below is
        per-worker, not container-wide. Coordination across workers is via the
        shared files /data/event_channel_managarr_last_run.json (pre-run check)
        and /data/event_channel_managarr_scan.lock (flock during scan) — those
        guarantee each scheduled time fires exactly once no matter how many
        worker threads exist.
        """
        global _bg_thread
        try:
            settings["timezone"] = self._dispatcharr_timezone()
            # --- This worker's scheduler thread ---
            worker_pid = os.getpid()
            scheduler_threads = [t for t in threading.enumerate() if "event-channel-managarr-scheduler" in t.name]
            running = bool(_bg_thread and _bg_thread.is_alive())
            n = len(scheduler_threads)
            if n > 1:
                thread_state = f"⚠️ {n} threads in one worker (leak)"
            elif running:
                thread_state = "running"
            else:
                thread_state = "not running"

            # --- Configured schedule + next run ---
            schedule_line = "Schedule: none configured"
            scheduled_times_str = settings.get("scheduled_times", "").strip()
            if scheduled_times_str:
                times = self._parse_scheduled_times(scheduled_times_str)
                if times:
                    tz_str = self._get_system_timezone(settings)
                    try:
                        local_tz = pytz.timezone(tz_str)
                    except pytz.exceptions.UnknownTimeZoneError:
                        local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)
                    now = datetime.now(local_tz)
                    upcoming = []
                    for t in times:
                        today_dt = local_tz.localize(datetime.combine(now.date(), t))
                        tomorrow_dt = local_tz.localize(datetime.combine(now.date() + timedelta(days=1), t))
                        upcoming.append(today_dt if today_dt > now else tomorrow_dt)
                    next_run = min(upcoming)
                    delta = next_run - now
                    hours, rem = divmod(int(delta.total_seconds()), 3600)
                    minutes = rem // 60
                    times_fmt = ",".join(t.strftime("%H:%M") for t in times)
                    schedule_line = f"Schedule: {times_fmt} {tz_str} | next {next_run.strftime('%H:%M')} in {hours}h{minutes:02d}m"
                else:
                    schedule_line = "Schedule: ⚠️ invalid times"

            # --- Last runs (shared file, container-wide) ---
            last_run_data = _read_last_run()
            last_runs_line = (
                "Last runs: " + ", ".join(f"{k}={v}" for k, v in sorted(last_run_data.items()))
                if last_run_data else "Last runs: none yet"
            )

            # --- Scan lock probe ---
            scan_lock_path = PluginConfig.SCAN_LOCK_FILE
            if os.path.exists(scan_lock_path) and fcntl:
                try:
                    with open(scan_lock_path, 'r') as probe:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(probe, fcntl.LOCK_UN)
                    lock_str = "free"
                except (OSError, IOError):
                    lock_str = "HELD"
            else:
                lock_str = "none"

            return {
                "status": "success",
                "message": (
                    f"Scheduler [PID {worker_pid}]: {thread_state} | lock: {lock_str}\n"
                    f"{schedule_line}\n"
                    f"{last_runs_line}\n"
                    f"(per-worker view; coordination via shared files)"
                )
            }

        except Exception as e:
            logger.error(f"Error checking scheduler status: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error checking scheduler status: {e}"}



    def update_schedule_action(self, settings, logger):
        """Save settings and update scheduled tasks"""
        try:
            settings["timezone"] = self._dispatcharr_timezone()
            scheduled_times_str = settings.get("scheduled_times", "").strip()
            logger.info(f"Update Schedule - scheduled_times value: '{scheduled_times_str}'")

            self._save_settings(settings)
            self._start_background_scheduler(settings)
            
            if scheduled_times_str:
                rejected = []
                times = self._parse_scheduled_times(scheduled_times_str, rejects=rejected)
                if times:
                    tz_str = self._get_system_timezone(settings)
                    time_list = [t.strftime('%H:%M') for t in times]
                    # Name the entries that were thrown away. Dropping them silently
                    # meant a run time the user had typed never fired and the message
                    # still read as a complete success.
                    warn = ""
                    if rejected:
                        warn = ("\n\nIgnored (use HHMM between 0000 and 2359; midnight "
                                "is 0000, not 2400): " + ", ".join(rejected))
                    return {
                        "status": "success",
                        "message": f"Schedule updated successfully!\n\nScheduled to run daily at: {', '.join(time_list)} ({tz_str})\n\nBackground scheduler is running.{warn}"
                    }
                else:
                    return {
                        "status": "error",
                        "message": "Invalid time format. Please use HHMM format (e.g., 0600,1300,1800)"
                    }
            else:
                self._stop_background_scheduler()
                return {
                    "status": "success",
                    "message": "Scheduled times cleared. Background scheduler stopped."
                }
        except Exception as e:
            logger.error(f"Error updating schedule: {e}")
            return {"status": "error", "message": f"Error updating schedule: {e}"}

    def _dispatcharr_timezone(self):
        """Resolve the effective timezone from Dispatcharr's global setting.

        Reads Dispatcharr's General Settings -> Time Zone, which is stored in
        core.models.CoreSettings under the "system_settings" group (NOT the
        unused apps.dashboard.models.Settings table). Uses the official
        CoreSettings.get_system_time_zone() accessor, which itself falls back
        to Django's TIME_ZONE then "UTC". Returns "UTC" when the value is
        missing, blank, or invalid, or if anything raises (e.g. running
        outside Dispatcharr, or the DB is unavailable during migrations).
        Validation and the UTC fallback live in ecm_parsing.coerce_timezone
        (Django-free, unit-tested).
        """
        try:
            from core.models import CoreSettings
            return ecm_parsing.coerce_timezone(CoreSettings.get_system_time_zone())
        except Exception as e:
            LOGGER.debug(f"{LOG_PREFIX} Could not read Dispatcharr timezone, using UTC: {e}")
            return "UTC"

    def _get_system_timezone(self, settings):
        """Get the system timezone from settings"""
        # First check if user specified a timezone in plugin settings
        if settings.get('timezone'):
            user_tz = settings.get('timezone')
            LOGGER.debug(f"Using user-specified timezone: {user_tz}")
            return user_tz
        
        # Otherwise use default timezone
        LOGGER.debug(f"Using default timezone: {self.DEFAULT_TIMEZONE}")
        return self.DEFAULT_TIMEZONE
        
    def _parse_scheduled_times(self, scheduled_times_str, rejects=None):
        """Parse scheduled times string into list of datetime.time objects.

        Pass a list as `rejects` to collect the entries that were thrown away. An
        entry that is not four digits, or whose hour or minute is out of range, was
        dropped with no record anywhere, so a run time the user had configured never
        fired and nothing reported it. `2400` is the common case: it is four digits,
        so a naive format check accepts it, but there is no hour 24.
        """
        times, rejected = ecm_parsing.parse_scheduled_times(scheduled_times_str)
        if rejects is not None:
            rejects.extend(rejected)
        return times

    def _start_background_scheduler(self, settings):
        """Start background scheduler thread"""
        global _bg_thread, _scheduler_lock

        # Source the timezone from Dispatcharr BEFORE the thread captures it:
        # scheduler_loop computes local_tz once from this dict and never re-reads.
        settings["timezone"] = self._dispatcharr_timezone()

        # Use lock to prevent concurrent scheduler starts
        with _scheduler_lock:
            # Stop existing scheduler if running
            self._stop_background_scheduler()

            # Parse scheduled times
            scheduled_times_str = settings.get("scheduled_times", "").strip()
            if not scheduled_times_str:
                LOGGER.info("No scheduled times configured, scheduler not started")
                return

            scheduled_times = self._parse_scheduled_times(scheduled_times_str)
            if not scheduled_times:
                LOGGER.info("No valid scheduled times, scheduler not started")
                return

            # Start new scheduler thread
            def scheduler_loop():
                import pytz
                thread_id = threading.current_thread().name

                # Get timezone from settings
                tz_str = self._get_system_timezone(settings)
                try:
                    local_tz = pytz.timezone(tz_str)
                except pytz.exceptions.UnknownTimeZoneError:
                    LOGGER.error(f"Unknown timezone: {tz_str}, falling back to {self.DEFAULT_TIMEZONE}")
                    local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)

                LOGGER.info(f"[{thread_id}] Scheduler timezone: {tz_str}")
                LOGGER.info(f"[{thread_id}] Scheduler initialized - will run at next scheduled time (not immediately)")

                while not _stop_event.is_set():
                    try:
                        now = datetime.now(local_tz)
                        current_date = now.date()

                        # Check each scheduled time
                        for scheduled_time in scheduled_times:
                            # Create a datetime for the scheduled time today in the local timezone
                            scheduled_dt = local_tz.localize(datetime.combine(current_date, scheduled_time))
                            time_diff = (scheduled_dt - now).total_seconds()

                            # Run if within 30 seconds and have not run today for this time
                            # Use file-based tracking shared across all uwsgi workers
                            time_key = scheduled_time.strftime('%H:%M')
                            last_run_data = _read_last_run()
                            already_ran = last_run_data.get(time_key) == str(current_date)

                            if -30 <= time_diff <= 30 and not already_ran:
                                # Cross-process concurrency is enforced inside _scan_and_update_channels
                                # (flock on SCAN_LOCK_FILE). This covers manual Run Now / Dry Run too,
                                # which the old scheduler-only flock did not.
                                try:
                                    LOGGER.info(f"[{thread_id}] Scheduled scan triggered at {now.strftime('%Y-%m-%d %H:%M %Z')}")

                                    # Reload settings from disk to get the latest configuration
                                    # This ensures changes made via "Update Schedule" or "Validate" are picked up
                                    try:
                                        if os.path.exists(self.settings_file):
                                            with open(self.settings_file, 'r') as f:
                                                current_settings = json.load(f)
                                            LOGGER.info(f"[{thread_id}] Reloaded settings from disk: {self.settings_file}")
                                            LOGGER.info(f"[{thread_id}]   enable_scheduled_csv_export from file: {current_settings.get('enable_scheduled_csv_export', 'NOT SET')}")
                                        else:
                                            current_settings = self.saved_settings.copy() if self.saved_settings else settings
                                            LOGGER.info(f"[{thread_id}] Settings file not found, using in-memory settings")
                                            LOGGER.info(f"[{thread_id}]   enable_scheduled_csv_export from memory: {current_settings.get('enable_scheduled_csv_export', 'NOT SET')}")
                                    except Exception as e:
                                        LOGGER.warning(f"[{thread_id}] Error reloading settings from disk: {e}, using in-memory settings")
                                        current_settings = self.saved_settings.copy() if self.saved_settings else settings
                                        LOGGER.info(f"[{thread_id}]   enable_scheduled_csv_export from memory (error): {current_settings.get('enable_scheduled_csv_export', 'NOT SET')}")

                                    LOGGER.info(f"[{thread_id}] Using current settings for scheduled run")

                                    result = self._scan_and_update_channels(current_settings, LOGGER, dry_run=False, is_scheduled_run=True)
                                    LOGGER.info(f"[{thread_id}] Scheduled scan completed: {result.get('message', 'Done')}")

                                    # Trigger frontend refresh if changes were made
                                    if result.get("status") == "success":
                                        results_data = result.get("results", {})
                                        if results_data.get("to_hide", 0) > 0 or results_data.get("to_show", 0) > 0:
                                            self._trigger_frontend_refresh(current_settings, LOGGER)

                                    # If _scan_and_update_channels skipped because another worker
                                    # was scanning, don't mark this slot as executed — let that worker
                                    # (or the next scheduler tick) do it.
                                    if result.get("skipped_due_to_lock"):
                                        LOGGER.info(f"[{thread_id}] Skipped due to active scan in another worker; not marking {time_key} as executed")
                                        break
                                except Exception as e:
                                    LOGGER.error(f"[{thread_id}] Error in scheduled scan: {e}")

                                    # Mark as executed for today's date in shared file tracker
                                    # (even on failure, to prevent retry storms that caused the original bug)
                                    last_run_data = _read_last_run()
                                    last_run_data[time_key] = str(current_date)
                                    _write_last_run(last_run_data)
                                    LOGGER.info(f"[{thread_id}] Marked {time_key} as executed for {current_date} (after error)")
                                else:
                                    # Mark as executed on success
                                    last_run_data = _read_last_run()
                                    last_run_data[time_key] = str(current_date)
                                    _write_last_run(last_run_data)
                                    LOGGER.info(f"[{thread_id}] Marked {time_key} as executed for {current_date}")

                                break

                        # Sleep for configured interval
                        _stop_event.wait(self.SCHEDULER_CHECK_INTERVAL)

                    except Exception as e:
                        LOGGER.error(f"[{thread_id}] Error in scheduler loop: {e}")
                        _stop_event.wait(60)

                LOGGER.info(f"[{thread_id}] Scheduler thread exiting")

            _bg_thread = threading.Thread(target=scheduler_loop, name="event-channel-managarr-scheduler", daemon=True)
            _bg_thread.start()
            LOGGER.info(f"Background scheduler started for times: {[t.strftime('%H:%M') for t in scheduled_times]}")



    def _stop_background_scheduler(self):
        """Stop background scheduler thread"""
        global _bg_thread
        if _bg_thread and _bg_thread.is_alive():
            LOGGER.info(f"Stopping background scheduler (thread: {_bg_thread.name})")
            _stop_event.set()
            _bg_thread.join(timeout=self.SCHEDULER_STOP_TIMEOUT)

            if _bg_thread.is_alive():
                LOGGER.warning(f"Background scheduler thread did not stop within timeout - may still be running!")
            else:
                LOGGER.info("Background scheduler stopped successfully")

            _stop_event.clear()

    def _export_csv(self, filename, rows, fieldnames, logger, header_lines=None):
        """Export data to a CSV file in the exports directory.
        Args:
            filename: CSV filename (will be placed in exports dir)
            rows: List of dicts to write
            fieldnames: Column names for the CSV
            logger: Logger instance
            header_lines: Optional list of comment lines to prepend (without '#' prefix)
        Returns:
            Full filepath of the written CSV, or None on error.
        """
        try:
            os.makedirs(PluginConfig.EXPORTS_DIR, exist_ok=True)
            filepath = os.path.join(PluginConfig.EXPORTS_DIR, filename)

            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                if header_lines:
                    for line in header_lines:
                        csvfile.write(f"# {line}\n")
                    csvfile.write("#\n")

                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)

            logger.info(f"{LOG_PREFIX} CSV exported: {filepath} ({len(rows)} rows)")
            return filepath
        except Exception as e:
            logger.error(f"{LOG_PREFIX} CSV export error: {e}")
            return None

    def _normalize_channel_name(self, channel_name):
        """Normalize channel name for duplicate detection by removing event details"""
        if not channel_name:
            return ""

        # Extract base name before colon, pipe, or dash separators
        name = re.sub(r':.*$', '', channel_name)
        name = re.sub(r'\|.*$', '', name)
        name = re.sub(r'\s-\s.*$', '', name)  # Remove dash separator and everything after

        # Normalize whitespace and convert to uppercase for comparison
        name = re.sub(r'\s+', ' ', name).strip().upper()

        return name

    def _get_event_description(self, channel_name):
        """Extract event description part of the channel name"""
        if not channel_name:
            return ""

        description = ""
        # Find description after colon, pipe, or dash
        colon_match = re.search(r':(.+)$', channel_name)
        if colon_match:
            description = colon_match.group(1)

        pipe_match = re.search(r'\|(.+)$', channel_name)
        if pipe_match:
            description = pipe_match.group(1)

        # Match dash as separator (whitespace followed by dash)
        dash_match = re.search(r'\s-\s*(.*)$', channel_name)
        if dash_match:
            description = dash_match.group(1)

        # Normalize whitespace and convert to uppercase for comparison
        description = re.sub(r'\s+', ' ', description).strip().upper()
        return description
    
    def _handle_duplicates(self, channels_to_process, channels_to_hide, channels_to_show, logger, strategy="lowest_number", keep_duplicates=False):
        """Handle duplicate channels - keep only one visible based on the selected strategy."""
        # If keep_duplicates is enabled, skip duplicate handling entirely
        if keep_duplicates:
            logger.info("Keep duplicates is enabled - skipping duplicate detection")
            return []

        # Group channels by normalized name AND event description
        channel_groups = {}
        
        for channel_info in channels_to_process:
            channel_id = channel_info['channel_id']
            channel_name = channel_info['channel_name']
            channel_number = channel_info['channel_number']
            
            normalized_name = self._normalize_channel_name(channel_name)
            event_description = self._get_event_description(channel_name)
            
            # Group key is now a tuple of (base_name, event_description)
            group_key = (normalized_name, event_description)
            
            if group_key not in channel_groups:
                channel_groups[group_key] = []
            
            channel_groups[group_key].append({
                'id': channel_id,
                'name': channel_name,
                'number': channel_number,
                'name_length': len(channel_name)
            })
        
        # Process each group of duplicates
        duplicate_hide_list = []
        
        for (normalized_name, event_description), channels in channel_groups.items():
            if len(channels) <= 1:
                continue  # No duplicates in this group, skip
            
            # Only log if it's a "real" event (has a description)
            if event_description:
                 logger.debug(f"Found {len(channels)} duplicate channels for '{normalized_name} | {event_description}'")
            else:
                 logger.debug(f"Found {len(channels)} duplicate channels for base name '{normalized_name}' (no event desc)")
            
            # Sort channels based on the selected strategy
            if strategy == "highest_number":
                channels_sorted = sorted(channels, key=lambda x: (x['number'] if x['number'] is not None else float('-inf')), reverse=True)
            elif strategy == "longest_name":
                channels_sorted = sorted(channels, key=lambda x: x['name_length'], reverse=True)
            else:  # Default to "lowest_number"
                channels_sorted = sorted(channels, key=lambda x: (x['number'] if x['number'] is not None else float('inf'), -x['name_length']))
            
            # Keep the first one (which is the best according to the sort)
            channel_to_keep = channels_sorted[0]
            channels_to_hide_in_group = channels_sorted[1:]
            
            logger.debug(f"Keeping channel {channel_to_keep['id']} (#{channel_to_keep['number']}): {channel_to_keep['name']}")
            
            # Mark the rest for hiding
            for dup in channels_to_hide_in_group:
                logger.debug(f"Marking duplicate for hiding: {dup['id']} (#{dup['number']}): {dup['name']}")
                duplicate_hide_list.append(dup['id'])
                
                # Remove from show list if it was going to be shown
                if dup['id'] in channels_to_show:
                    channels_to_show.remove(dup['id'])
                
                # Add to hide list if not already there
                if dup['id'] not in channels_to_hide:
                    channels_to_hide.append(dup['id'])
        
        return duplicate_hide_list

    def _localized_template_props(self, settings):
        """
        Returns overrides for the three rewritable title templates plus
        `output_timezone` for the managed dummy EPG source.

        - When source TZ is invalid/empty: returns DEFAULTS (plain templates,
          `output_timezone=""`) so any previously-saved value is cleared
          (the diff-and-save loop never deletes keys).
        - When display TZ is empty or equal to source TZ: returns plain
          templates but with `output_timezone=source_tz_name` so Dispatcharr
          converts {starttime}/{endtime} into the display timezone.
        - Otherwise: returns localized templates with the date placeholder
          driven by `date_format` (US/Auto -> {month}/{day};
          EU -> {day}/{month}) and a TZ abbreviation suffix computed for
          "now" in the display TZ. If %Z returns a numeric offset
          (e.g., +0530), the suffix is omitted but time conversion still
          happens via Dispatcharr's output_timezone.

        Time-of-day placeholder: Dispatcharr's dummy EPG renderer only ever
        formats {starttime}/{endtime} as 12-hour AM/PM — it does NOT infer
        12h vs 24h from output_timezone or locale. This plugin instead picks
        the placeholder itself based on `dummy_epg_channel_format`: SE names
        already carry 24-hour times (e.g. "19:55"), so SE uses
        {starttime24}/{endtime24}; US names carry native AM/PM times, so US
        keeps {starttime}/{endtime}.

        `fallback_title_template` is set in the base `managed_props` and
        is never overridden here.
        """
        channel_format = str(settings.get("dummy_epg_channel_format",
                                          self.DEFAULT_DUMMY_EPG_CHANNEL_FORMAT)).strip().upper()
        start_ph = "{starttime24}" if channel_format == "SE" else "{starttime}"
        end_ph = "{endtime24}" if channel_format == "SE" else "{endtime}"

        DEFAULTS = {
            "output_timezone": "",
            "title_template": "{title}",
            "upcoming_title_template": f"Upcoming at {start_ph}: {{title}}",
            "ended_title_template": f"Ended at {end_ph}: {{title}}",
        }

        source_tz_name = str(settings.get("dummy_epg_event_timezone", "")).strip()
        # Display tz comes from Dispatcharr (already injected into settings by the
        # caller via _dispatcharr_timezone); _get_system_timezone is the reader.
        display_tz_name = self._get_system_timezone(settings)

        if not source_tz_name:
            return DEFAULTS

        try:
            pytz.timezone(source_tz_name)  # validate only; renderer resolves source TZ itself
        except pytz.exceptions.UnknownTimeZoneError:
            return DEFAULTS

        # No display TZ configured, or same as source: no time conversion needed,
        # but still pass output_timezone so Dispatcharr converts {starttime}/{endtime}
        # into the display timezone (the SE/US placeholder choice above still applies).
        if not display_tz_name or source_tz_name == display_tz_name:
            return {**DEFAULTS, "output_timezone": source_tz_name}

        try:
            display_tz = pytz.timezone(display_tz_name)
        except pytz.exceptions.UnknownTimeZoneError:
            return {**DEFAULTS, "output_timezone": source_tz_name}

        abbrev = datetime.now(display_tz).strftime("%Z")
        suffix = f" {abbrev}" if abbrev and abbrev.isalpha() else ""

        fmt = str(settings.get("date_format", "Auto")).strip().upper()
        date_ph = "{day}/{month}" if fmt == "EU" else "{month}/{day}"

        # The main (currently-live) title stays plain "{title}". The inline
        # {month}/{day} {starttime} placeholders only resolve when the channel
        # name carries a parseable date AND time; event channels without one
        # (e.g. "LIVE EVENT 31 - GOBI Live From Coachella 2026") would otherwise
        # render the literal placeholder text. The program's start/end slot is
        # still TZ-converted via output_timezone, so the guide shows the right
        # time column. upcoming/ended templates keep the localized date/time —
        # they only render when date_info AND time_info both matched, so their
        # placeholders are always filled.
        return {
            "output_timezone": display_tz_name,
            "title_template": "{title}",
            "upcoming_title_template": f"Upcoming at {date_ph} {start_ph}{suffix}: {{title}}",
            "ended_title_template": f"Ended at {date_ph} {end_ph}{suffix}: {{title}}",
        }

    def _epg_binding_is_reroutable(self, channel, logger=None):
        """May this channel's EPG binding be moved to another source?

        Only when it holds NOTHING, a dummy source, or a real source with no
        programme in the next 24h.

        A name claim alone is NOT sufficient. `Next:` and `(GMT)` are standard EPG
        conventions, not DAZN-specific, so a claim can match a channel carrying a
        legitimately populated real EPG on some other install. Moving that would
        silently destroy a working guide. This mirrors the guard
        _managed_override_ids already applies (bug-043).

        Fails CLOSED: any error resolves to False (not reroutable), i.e. leave the
        channel exactly where it is -- the pre-existing behavior -- rather than risk
        rerouting a channel whose guide status could not actually be confirmed.
        """
        from datetime import timedelta
        from django.utils import timezone as djtz

        epg_data = channel.epg_data
        if epg_data is None or epg_data.epg_source is None:
            return True
        if getattr(epg_data.epg_source, "source_type", None) == "dummy":
            return True
        now = djtz.now()
        try:
            return not ProgramData.objects.filter(
                epg_id=epg_data.id, start_time__lt=now + timedelta(hours=24),
                end_time__gte=now).exists()
        except Exception as exc:
            if logger is not None:
                logger.warning(f"{LOG_PREFIX} Reroutable check failed for channel "
                               f"{channel.id!r}; leaving binding in place: {exc}")
            return False

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

        Returns None on failure -- the caller then leaves those channels alone,
        which is the pre-S2 behavior.

        A `user_managed` profile (one built from the operator's group mapping) is
        SEEDED once here and never written again, so its timezone, duration,
        templates and patterns belong to the operator from that moment on. The
        decision itself is ecm_profiles.source_props_to_write, deliberately a pure
        function: an inverted comparison would freeze the shared source and rewrite
        every mapped one, and no test that reads this source text could tell the
        two apart.

        One consequence worth stating: `managed_by` also stops being repaired on a
        mapped source, because the plugin no longer writes that source at all. The
        record of what the plugin created lives in GROUP_SOURCE_RECORD_FILE instead,
        which Dispatcharr's EPG source editor cannot reach.
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
            if getattr(profile, "user_managed", False):
                self._record_group_source(source, profile.group_names, logger)
            return source

        to_write = ecm_profiles.source_props_to_write(
            profile, source.custom_properties or {}, desired)
        if to_write is None:
            return source
        source.custom_properties = to_write
        try:
            source.save(update_fields=["custom_properties"])
            logger.info(f"{LOG_PREFIX} Refreshed EPG source {profile.source_name!r}")
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} Could not refresh EPG source "
                           f"{profile.source_name!r}: {exc}")
            return None
        return source

    def _reroute_claimed_channels(self, settings, logger, dry_run, enabled_channel_ids):
        """Move every channel that is on the wrong EPG source onto the right one.

        Runs at the TOP of BOTH of _run_managed_epg_pass's branches, before that
        branch's own attach and detach -- not after, despite how this might read
        at first glance. It reads pre-pass state (the channel's *current*
        epg_data/epg_source), and moves any claimed channel straight to its
        destination profile source. The NULL-only attach that follows re-queries
        Channel.objects fresh, so a channel this step already bound no longer
        shows epg_data__isnull=True and is silently skipped by that attach --
        no double-write, no race. The detach that follows is scoped to the
        DEFAULT source and excludes every enabled channel outright, so a
        rerouted (still-enabled) channel was never a detach candidate either way.

        Going first also avoids a wasted write: when ECM hides an event-less
        slot, auto_set_dummy_epg_on_hide nulls its epg_data, so the channel
        looks NULL to this same pass. Running reroute after the attach would
        let the NULL-only attach bind it to the DEFAULT source first, then
        immediately re-point it to the claimed profile source on this step --
        one throwaway EPGData row (and the orphan-reap that follows it) per
        reclaimed channel, every cycle. Going first, the channel is written
        exactly once.

        THE DESTINATION DECISION IS NOT HERE. It is
        ecm_profiles.routing_destinations, a pure function, because this method
        cannot be imported outside the container and a test that reads its source
        text cannot tell a correct comparison from an inverted one. This method
        gathers the inputs, performs the writes, and nothing else.

        One direction of movement is new (issue 29): a channel whose group no
        longer maps anywhere returns to the shared source. Three things keep that
        safe, and all three are decided by the pure function above:
          - the mapping must have parsed with NO problems, or no channel returns
            at all, because "maps nowhere" is exactly what a typo produces
          - the channel must be on a source the plugin RECORDED creating, so the
            five channels sitting on hand-made dummy sources on this installation
            cannot be taken
          - the shared source must exist, or the channel would be unbound

        Safety here:
          - _epg_binding_is_reroutable vetoes any channel holding a populated real
            EPG, so a claim cannot destroy a working guide. Note it returns True
            for ANY dummy source with no ownership check, which is why the
            plugin-created record above is a separate and necessary guard.
          - it never detaches; a channel is only ever re-pointed
          - an uncreatable destination source leaves those channels where they are
          - LOAD BEARING, and previously an accident: a rerouted channel cannot be
            detached in the same pass because the detach keeps every id in
            enabled_channel_ids and every channel considered here comes from that
            same set. Do not narrow one without narrowing the other.

        Returns the ids moved, or under dry_run the ids that WOULD move.
        """
        from apps.epg.models import EPGData, EPGSource

        if not enabled_channel_ids:
            return []

        profiles = ecm_profiles.build_profiles(settings)
        group_profiles, mapping_problems = ecm_profiles.build_group_profiles(settings)
        if not group_profiles and not any(not p.is_default for p in profiles):
            return []
        if mapping_problems:
            logger.warning(
                f"{LOG_PREFIX} The group to source mapping has "
                f"{len(mapping_problems)} problem(s), so no channel will return to "
                f"the shared source this run. First: {mapping_problems[0]}")

        candidates = list(
            Channel.objects.filter(id__in=enabled_channel_ids)
            .select_related("channel_group", "epg_data", "epg_data__epg_source"))
        if not candidates:
            return []

        created_record = self._load_group_source_record(logger)
        # Derived from the default profile rather than written as a fourth copy of
        # the literal "ECM Managed Dummy". One of the three existing copies sits in
        # a method whose body is hash-pinned by tests/contract/test_s2_wiring.py, so
        # introducing a shared constant would move that pin for no behaviour change.
        default_source_name = next(
            (p.source_name for p in profiles if p.is_default), None)

        bindings = []
        for channel in candidates:
            epg_source = getattr(channel.epg_data, "epg_source", None)
            source_name = getattr(epg_source, "name", None)
            bindings.append(ecm_profiles.ChannelBinding(
                id=channel.id,
                name=channel.name,
                group_name=(channel.channel_group.name
                            if channel.channel_group else None),
                source_name=source_name,
                source_is_plugin_created=bool(
                    source_name and source_name in created_record)))

        destinations = ecm_profiles.routing_destinations(
            bindings, group_profiles, profiles, default_source_name,
            not mapping_problems)
        if not destinations:
            return []

        # One profile per destination source name, so the ensure step below knows
        # which properties to seed. The shared source has no entry here on purpose:
        # it is created and refreshed by _get_or_create_managed_epg_source, which
        # honours the US or SE format setting. Calling _ensure_profile_source for it
        # would write US properties unconditionally and the two would fight on every
        # run of an installation using the SE format.
        profile_by_source = {p.source_name: p
                             for p in tuple(group_profiles) + tuple(profiles)
                             if not p.is_default}
        by_id = {c.id: c for c in candidates}

        moved = []
        for source_name in sorted(set(destinations.values())):
            group = [by_id[cid] for cid, dest in destinations.items()
                     if dest == source_name and cid in by_id]
            group = [c for c in group
                     if self._epg_binding_is_reroutable(c, logger=logger)]
            if not group:
                continue

            profile = profile_by_source.get(source_name)

            if dry_run:
                existing = EPGSource.objects.filter(
                    name=source_name, source_type="dummy").first()
                if existing is None and profile is None:
                    # The shared source does not exist yet and this step will not
                    # create it, so an applied run would not move these either.
                    continue
                moved.extend(c.id for c in group)
                continue

            if profile is not None:
                source = self._ensure_profile_source(profile, settings, logger)
            else:
                source = EPGSource.objects.filter(
                    name=source_name, source_type="dummy").first()
            if source is None:
                logger.warning(f"{LOG_PREFIX} No source named {source_name!r}; "
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
                        f"{source_name!r}")

            # Rows left behind on the source(s) we moved off are orphaned NOW; the
            # existing reaper is scoped to the default source and already ran this
            # pass, so reap them here. This covers BOTH directions of movement.
            for vacated_source in vacated | {source}:
                self._reap_orphaned_epg_data(vacated_source, logger)
        return moved

    def _get_or_create_managed_epg_source(self, settings, logger):
        """Create (if missing) or refresh the shared plugin-managed dummy EPGSource.

        Returns the EPGSource, or None on error.
        """
        from apps.epg.models import EPGSource

        # Parse duration with fallback
        try:
            duration_hours = int(str(settings.get("dummy_epg_event_duration_hours",
                                                   self.DEFAULT_EVENT_DURATION_HOURS)).strip())
        except (ValueError, TypeError):
            logger.warning(f"{LOG_PREFIX} Invalid dummy_epg_event_duration_hours; using default")
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
        if duration_hours <= 0:
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)

        tz_value = str(settings.get("dummy_epg_event_timezone",
                                    self.DEFAULT_DUMMY_EPG_TIMEZONE)).strip() or self.DEFAULT_DUMMY_EPG_TIMEZONE

        # Keys the plugin owns. Any other keys on the source are left untouched.
        #
        # Named groups use JS-style (?<name>) rather than Python (?P<name>): Dispatcharr's
        # frontend Pattern Configuration validator is JavaScript and rejects (?P<name>) with
        # "Invalid group" (issue #21), while its renderer converts (?<name>) -> (?P<name>)
        # server-side. The renderer accepts either form; the JS form keeps the UI test panel
        # happy so users can validate their own patterns.
        #
        # Format: "US" (default). Regexes validated against these real channel names:
        #   "PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)"  -> title="Cage Fury FC 153"
        #   "LIVE EVENT 01   9:45am Suslenkov v Mann"           -> title="Suslenkov v Mann"
        #   "PPV EVENT 25: OUTDOOR THEATRE Live From Coachella" -> title="OUTDOOR THEATRE Live From Coachella"
        #   "PPV02 | UFC 327: English Apr 14 4:30 PM"           -> title="UFC 327: English"
        #   "LIVE EVENT 31 - GOBI Live From Coachella 2026"     -> title="GOBI Live From Coachella 2026"
        # The title capture stops at the first of: " (", a time token, or a month-name token.
        # leading_time handles names where the time appears BEFORE the event text (LIVE format).
        # The separator class includes '-' so " - " between the event number and the
        # title is consumed (otherwise the leading dash leaks into {title}).
        # The prefix accepts "PPV EVENT N", "LIVE EVENT N", "PPV N", "LIVE N", a bare
        # "EVENT N" with NO PPV/LIVE prefix (e.g. "EVENT 21: Dirt Zone (6.19 7:30 PM ET)")
        # so those providers capture a real {title} instead of the static fallback
        # (bug-051), and a bare slot NUMBER with no keyword at all (e.g. "07 - 8/14 7pm
        # Broncos at Falcons"), which is how at least one provider names its NFL slots.
        #
        # The leading lookahead is what makes the keyword-less form safe. Accepting a bare
        # number outright strips the number off ordinary channel names -- "60 Minutes"
        # extracts the title "Minutes" and the guide silently renames the channel, which
        # is exactly what the keyword requirement was guarding against. So a keyword-less
        # name qualifies only when the number is followed by an EXPLICIT separator
        # character and then a date or a clock time. "60 Minutes", "48 Hours" and
        # "100 Huntley Street" have neither and stay on the fallback, as before.
        #
        # The negative lookahead before the slot number stops the match beginning INSIDE
        # an air time. Without it, a name whose slot number is followed by text rather
        # than by a date or a time -- "Boxing 3 : MOSES vs HRGOVIC  4:00pm" -- was skipped
        # at the number and matched at the time instead: "4" read as the slot, the time's
        # own colon as the separator, and "00pm" captured as the title. Four live channels
        # rendered the guide entry "Ended at 8/29 7 PM CDT: 00pm" on 2026-08-29. Such a
        # name carries no parseable slot, so no match is the correct outcome and the
        # renderer fallback handles it.
        #
        # This literal is duplicated as ecm_profiles.US_ET.title_pattern; the renderer
        # reads THIS one. tests/contract/test_us_pattern_parity.py keeps them equal.
        us_title_pattern = (
            r"(?=(?:PPV|LIVE|EVENT)|"
            r"\d+\s*[:|\-]\s*(?:\d{1,2}[./]\d{1,2}|\d{1,2}(?::\d{2})?\s*[AaPp][Mm]))"
            r"(?:(?:PPV|LIVE)\s*(?:EVENT\s*)?|EVENT\s*)?"
            r"(?!\d{1,2}:\d{2}\s*[AaPp][Mm])\d+\s*[:|\-\s]\s*"
            r"(?:(?<datepart>\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\s+)?"
            r"(?:(?<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
            r"(?<title>.+?)"
            r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
            r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
        )
        # The trailing (?![A-Za-z]) is load-bearing: without it the am/pm marker matches the
        # first two letters of an ordinary word, so "PPV 12 AMERICAN LEGENDS" reads as
        # midnight and "ALI vs 8 AMATEUR BOUTS" as 8 o'clock. That was harmless while the
        # pattern only titled a guide entry, but [UndatedEnded] hides a channel on the time
        # it returns. The leading (?<![\d:]) stops a match beginning inside a longer number
        # or inside a clock time, the same hazard bug-146 fixed for the title pattern.
        us_time_pattern = r"(?<![\d:])(?<hour>\d{1,2})(?::(?<minute>\d{2}))?\s*(?<ampm>[AaPp][Mm])(?![A-Za-z])"
        us_date_pattern = r"\b(?<month>\d{1,2})[./](?<day>\d{1,2})(?:[./](?<year>\d{2,4}))?\b"

        # Format: "SE" (pipe-delimited, 24h time, named month):
        #   "LIVE | GIRONA - REAL SOCIEDAD | Thu 14 May 19:55 CEST (SE) | 8K EXCLUSIVE | SE: TV4 PLAY PPV 7"
        #    prefix ^  title ^              ^ air time                   ^ extras        ^ channel name
        #     -> title="GIRONA - REAL SOCIEDAD"
        # The date pattern's day/month groups feed Dispatcharr's renderer, which accepts
        # a textual month under the "month" group name.
        se_title_pattern = r"\|\s*(?<title>[^|]+?)\s*\|"
        se_time_pattern = r"(?<hour>\d{1,2}):(?<minute>\d{2})"
        se_date_pattern = (
            r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
            r"(?<day>\d{1,2})\s+"
            r"(?<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b"
        )

        channel_format = str(settings.get("dummy_epg_channel_format",
                                          self.DEFAULT_DUMMY_EPG_CHANNEL_FORMAT)).strip().upper()
        if channel_format == "SE":
            title_pattern, time_pattern, date_pattern = se_title_pattern, se_time_pattern, se_date_pattern
        else:
            title_pattern, time_pattern, date_pattern = us_title_pattern, us_time_pattern, us_date_pattern

        # Dispatcharr's dummy renderer always formats {starttime}/{endtime} as
        # 12-hour AM/PM — it never infers 12h vs 24h on its own. SE channel
        # names already carry 24-hour times, so use the {starttime24}/
        # {endtime24} placeholders for SE; US names carry native AM/PM, so
        # keep the 12-hour placeholders there. (_localized_template_props
        # below re-derives this same choice and overrides these two keys
        # whenever a valid dummy_epg_event_timezone is set — these values
        # are just the fallback for when it isn't.)
        start_ph = "{starttime24}" if channel_format == "SE" else "{starttime}"
        end_ph = "{endtime24}" if channel_format == "SE" else "{endtime}"

        managed_props = {
            "title_pattern": title_pattern,
            "time_pattern": time_pattern,
            "date_pattern": date_pattern,
            "title_template": "{title}",
            # Informative pre/post-event titles using Dispatcharr's
            # auto-computed {starttime}/{endtime} placeholders plus the
            # extracted {title}. Examples at render time:
            #   US:  Upcoming at 8:00 PM: Cage Fury FC 153
            #   SE:  Upcoming at 19:55: GIRONA - REAL SOCIEDAD
            "upcoming_title_template": f"Upcoming at {start_ph}: {{title}}",
            "ended_title_template": f"Ended at {end_ph}: {{title}}",
            # Dispatcharr's dummy renderer uses fallback_title_template VERBATIM —
            # it never substitutes {channel_name} (see apps/output/views.py
            # generate_fallback_programs: `title = fallback_title if fallback_title
            # else channel_name`). An empty title therefore makes the renderer fall
            # back to the real channel name. A non-empty description is required to
            # enter the fallback path at all, because generate_dummy_programs gates on
            # `if fallback_title or fallback_description`. So: empty title (-> real
            # name) + static description.
            "fallback_title_template": "",
            "fallback_description_template": "Live event — guide information is currently unavailable.",
            "program_duration": duration_hours * 60,
            "timezone": tz_value,
            "include_date": False,
            "managed_by": "event-channel-managarr",
        }

        managed_props.update(self._localized_template_props(settings))

        # Pattern keys are user-customizable via Dispatcharr's Pattern Configuration UI.
        # Issue #21: enforcing them on every run clobbered users whose channel names don't
        # match the PPV/LIVE default. On refresh we only (re)apply our default to a pattern
        # the user hasn't touched — one that is absent or still equals a default this plugin
        # has shipped. `stock_patterns` must therefore list EVERY historically-shipped
        # default (across both the US and SE channel-name formats) so stock installs (the
        # source is created once, very early for some users) still auto-upgrade — including
        # on a US<->SE format switch — while genuine user customizations are preserved
        # across runs. _py_named() covers the (?P<name>) variants of the current defaults;
        # the pre-'-'-separator title and the original mandatory-:minute title/time defaults
        # are listed explicitly. When the defaults change, append the previous default here.
        PATTERN_KEYS = ("title_pattern", "time_pattern", "date_pattern")

        def _py_named(p):
            # Rewrites a JavaScript named group (?<name> into the Python (?P<name> form
            # while leaving a lookbehind (?<= or (?<! alone. A blunt string replace would
            # turn (?<! into (?P<! and put a pattern in stock_patterns that could never
            # match a real stored value, which would silently stop that value being
            # recognised as a plugin default and therefore stop it being upgraded.
            return re.sub(r"\(\?<(?![=!])", "(?P<", p)

        # Original shipped defaults (commit b1ef257-era): mandatory ":minute" leading time
        # and optional am/pm. Carried by ~22 early releases' source rows.
        _orig_title = (
            r"(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+\s*[:|\s]\s*"
            r"(?:(?P<leading_time>\d{1,2}:\d{2}\s*[AaPp][Mm])\s+)?"
            r"(?P<title>.+?)"
            r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
            r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
        )
        _orig_time = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp][Mm])?"

        # Previous default, shipped through 1.26.2422156: no boundary either side of the
        # clock time, so the am/pm marker could match the opening letters of a word.
        _prev_us_time_unbounded = r"(?<hour>\d{1,2})(?::(?<minute>\d{2}))?\s*(?<ampm>[AaPp][Mm])"

        # Previous default (pre bug-051, JS form as stored on live sources): required a
        # PPV/LIVE prefix, so bare "EVENT N:" names fell to the renderer fallback. Listed
        # here so sources still carrying it auto-upgrade to the prefix-optional default.
        _prev_us_title = (
            r"(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+\s*[:|\-\s]\s*"
            r"(?:(?<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
            r"(?<title>.+?)"
            r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
            r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
        )

        # Superseded on 2026-08-14: required a PPV, LIVE or EVENT keyword before the slot
        # number, so a provider naming its slots "07 - 8/14 7pm Broncos at Falcons" got
        # the renderer's static fallback and never an upcoming or ended title. Listed here
        # so sources still carrying it auto-upgrade to the keyword-optional default.
        _prev_us_title_keyword_required = (
            r"(?:(?:PPV|LIVE)\s*(?:EVENT\s*)?|EVENT\s*)\d+\s*[:|\-\s]\s*"
            r"(?:(?<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
            r"(?<title>.+?)"
            r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
            r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
        )

        # Superseded on 2026-08-29: the keyword-less branch let a clock time act as the
        # slot number and separator, so "Boxing 3 : MOSES vs HRGOVIC  4:00pm" rendered the
        # guide title "00pm" (bug-146). Listed here so sources still carrying it
        # auto-upgrade to the guarded default.
        _prev_us_title_unguarded_clock_time = (
            r"(?=(?:PPV|LIVE|EVENT)|"
            r"\d+\s*[:|\-]\s*(?:\d{1,2}[./]\d{1,2}|\d{1,2}(?::\d{2})?\s*[AaPp][Mm]))"
            r"(?:(?:PPV|LIVE)\s*(?:EVENT\s*)?|EVENT\s*)?\d+\s*[:|\-\s]\s*"
            r"(?:(?<datepart>\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\s+)?"
            r"(?:(?<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
            r"(?<title>.+?)"
            r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
            r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
        )

        stock_patterns = {
            "title_pattern": {us_title_pattern, _py_named(us_title_pattern),
                              _py_named(us_title_pattern).replace(r"[:|\-\s]", r"[:|\s]"),
                              _orig_title, _prev_us_title, _py_named(_prev_us_title),
                              _prev_us_title_keyword_required,
                              _py_named(_prev_us_title_keyword_required),
                              _prev_us_title_unguarded_clock_time,
                              _py_named(_prev_us_title_unguarded_clock_time),
                              se_title_pattern, _py_named(se_title_pattern)},
            "time_pattern": {us_time_pattern, _py_named(us_time_pattern), _orig_time,
                             _prev_us_time_unbounded, _py_named(_prev_us_time_unbounded),
                             se_time_pattern, _py_named(se_time_pattern)},
            "date_pattern": {us_date_pattern, _py_named(us_date_pattern),
                             se_date_pattern, _py_named(se_date_pattern)},
        }

        try:
            source, created = EPGSource.objects.get_or_create(
                name="ECM Managed Dummy",
                defaults={
                    "source_type": "dummy",
                    "is_active": True,
                    "custom_properties": managed_props,
                },
            )
        except Exception as e:
            logger.error(f"{LOG_PREFIX} Failed to get_or_create managed EPGSource: {e}")
            return None

        if created:
            logger.info(f"{LOG_PREFIX} Created managed EPGSource 'ECM Managed Dummy' (id={source.id})")
            return source

        # Existing source: refresh only the plugin-managed keys, preserving any
        # user-added keys.
        current = dict(source.custom_properties or {})
        changed = False
        for k, v in managed_props.items():
            if k in PATTERN_KEYS:
                cur = current.get(k)
                # Preserve a user-customized pattern; only (re)apply our default to a
                # pattern that is unset or still on a plugin-shipped default (issue #21).
                if cur is not None and cur not in stock_patterns[k]:
                    continue
            if current.get(k) != v:
                current[k] = v
                changed = True
        if source.source_type != "dummy":
            logger.warning(f"{LOG_PREFIX} 'ECM Managed Dummy' exists but source_type={source.source_type!r}; leaving alone")
            return None
        if changed:
            source.custom_properties = current
            try:
                source.save(update_fields=["custom_properties"])
                logger.info(f"{LOG_PREFIX} Refreshed managed EPGSource custom_properties (id={source.id})")
            except Exception as e:
                logger.error(f"{LOG_PREFIX} Failed to update managed EPGSource: {e}")
                return None
        return source

    def _extract_se_display_name(self, channel_name):
        """Return the last pipe-separated segment of an SE-format channel name.
        Falls back to the full name if no pipe is found (e.g. already renamed)."""
        m = re.search(r'\|\s*([^|]+?)\s*$', channel_name)
        return m.group(1) if m else channel_name

    def _attach_managed_epg(self, channels, managed_source, logger, settings=None, rate_limiter=None,
                            override_ids=None):
        """Bind each channel in `channels` to the managed dummy source via an EPGData row,
        and keep EPGData.name in sync with the desired display name.

        For US format, the desired name is the full channel name. For SE format, it's
        the last pipe-segment (broadcaster name, e.g. "SE: VIAPLAY PPV 20") via
        `_extract_se_display_name`, so the guide's channel list shows the broadcaster
        instead of the full stream name.

        Channels with epg_data already set are only checked for a name update (no new
        EPGData row is created) UNLESS their id is in `override_ids` (the opt-in
        override-existing-EPG set, bug-043), in which case their link is re-pointed to the
        managed dummy. Returns list of channel IDs newly attached/re-pointed.
        """
        from apps.epg.models import EPGData
        override_ids = override_ids or set()

        channel_format = str((settings or {}).get(
            "dummy_epg_channel_format", self.DEFAULT_DUMMY_EPG_CHANNEL_FORMAT)).strip().upper()

        attached_ids = []
        channels_to_update = []
        epg_data_to_update = []

        # Wrap the entire get_or_create + bulk_update cycle in one transaction so a
        # bulk_update failure doesn't leave orphan EPGData rows pointing nowhere.
        with transaction.atomic():
            for channel in channels:
                desired_name = (self._extract_se_display_name(channel.name)
                                 if channel_format == "SE" else channel.name)

                # Re-point an override channel only if it isn't already on the managed source.
                repoint = (channel.id in override_ids and
                           getattr(channel.epg_data, "epg_source_id", None) != managed_source.id)
                if channel.epg_data_id is None or repoint:
                    try:
                        epg_data, _ = EPGData.objects.get_or_create(
                            tvg_id=str(channel.uuid),
                            epg_source=managed_source,
                            defaults={"name": desired_name},
                        )
                    except Exception as e:
                        logger.warning(f"{LOG_PREFIX} Failed to get_or_create EPGData for channel {channel.id}: {e}")
                        continue

                    channel.epg_data = epg_data
                    channels_to_update.append(channel)
                    attached_ids.append(channel.id)

                    if rate_limiter is not None:
                        rate_limiter.wait()
                else:
                    epg_data = channel.epg_data

                # Keep EPGData.name in sync with the desired display name so
                # {channel_name} in the dummy source's fallback template, and the
                # guide's channel list, render correctly.
                if epg_data.name != desired_name:
                    epg_data.name = desired_name
                    epg_data_to_update.append(epg_data)

            if channels_to_update:
                Channel.objects.bulk_update(channels_to_update, ["epg_data"])
                logger.info(f"{LOG_PREFIX} Attached managed EPG to {len(channels_to_update)} channel(s)")
            if epg_data_to_update:
                EPGData.objects.bulk_update(epg_data_to_update, ["name"])
                logger.info(f"{LOG_PREFIX} Updated EPG display name for {len(epg_data_to_update)} channel(s)")
        return attached_ids

    def _detach_managed_epg(self, managed_source, keep_channel_ids, logger, scope_ids=None):
        """Set epg_data=None on any channel currently bound to the managed source
        whose id is NOT in keep_channel_ids. Returns list of detached channel IDs.

        When `scope_ids` is provided, only channels within that id set are considered —
        so a group-filtered scan only de-manages channels it actually looked at and never
        strips the managed dummy off channels in other groups (bug-045). `scope_ids=None`
        means the whole source (used for the toggle-off full teardown).
        """
        if managed_source is None:
            return []

        stale_q = Channel.objects.filter(
            epg_data__epg_source=managed_source
        ).exclude(id__in=keep_channel_ids)
        if scope_ids is not None:
            stale_q = stale_q.filter(id__in=scope_ids)
        stale = list(stale_q)

        if not stale:
            return []

        for ch in stale:
            ch.epg_data = None

        with transaction.atomic():
            Channel.objects.bulk_update(stale, ["epg_data"])

        detached_ids = [ch.id for ch in stale]
        logger.info(f"{LOG_PREFIX} Detached managed EPG from {len(detached_ids)} channel(s)")

        # Reap managed EPGData rows orphaned by this (and prior) detaches. _attach_
        # creates one EPGData per channel (tvg_id=str(channel.uuid)); detach only
        # nulled the channel link, leaving the row behind forever (bug-044). A managed
        # row is "live" only while some Channel.epg_data points at it; get_or_create
        # re-creates it on re-attach, so deleting unreferenced rows is safe. The UUID
        # regex restricts deletion to attach-created rows and spares the source's own
        # representative row (non-UUID tvg_id, e.g. 'dummy_ecm_managed_dummy').
        try:
            from apps.epg.models import EPGData
            referenced_ids = set(
                Channel.objects.filter(epg_data__epg_source=managed_source)
                .values_list("epg_data_id", flat=True)
            )
            orphans = (EPGData.objects.filter(epg_source=managed_source)
                       .exclude(id__in=referenced_ids)
                       .filter(tvg_id__regex=r'^[0-9a-fA-F-]{36}$'))
            orphan_count = orphans.count()
            if orphan_count:
                orphans.delete()
                logger.info(f"{LOG_PREFIX} Reaped {orphan_count} orphaned managed EPGData row(s)")
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} Orphan EPGData cleanup skipped: {e}")

        return detached_ids

    def _managed_override_ids(self, settings, managed_source, enabled_channel_ids, logger):
        """Opt-in (override_existing_epg): return the subset of enabled channels whose
        current EPG is a NON-managed, NON-dummy source with NO programme in the next 24h
        (a blank guide). These are eligible to be re-pointed to the managed dummy. Returns
        [] when the toggle is off, the managed source is missing, or nothing qualifies
        (bug-043). Channels whose linked EPG actually has upcoming programmes are excluded."""
        if managed_source is None or not self._get_bool_setting(settings, "override_existing_epg", False):
            return []
        try:
            from apps.epg.models import ProgramData
            from django.utils import timezone as _djtz
            cand = list(Channel.objects.filter(
                id__in=enabled_channel_ids, epg_data__isnull=False
            ).exclude(epg_data__epg_source=managed_source).select_related("epg_data__epg_source"))
            cand = [c for c in cand
                    if getattr(c.epg_data.epg_source, "source_type", None) != "dummy"]
            if not cand:
                return []
            now = _djtz.now()
            window_end = now + timedelta(hours=24)
            ed_ids = [c.epg_data_id for c in cand]
            with_progs = set(ProgramData.objects.filter(
                epg_id__in=ed_ids, start_time__lt=window_end, end_time__gte=now
            ).values_list("epg_id", flat=True))
            override = [c.id for c in cand if c.epg_data_id not in with_progs]
            if override:
                logger.info(f"{LOG_PREFIX} override_existing_epg: {len(override)} visible channel(s) "
                            f"with a blank existing EPG eligible for the managed dummy")
            return override
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} override_existing_epg check skipped: {e}")
            return []

    @staticmethod
    def _epg_source_name(channel):
        """The name of the EPG source this channel is bound to, or an empty string.

        Reported per channel in the CSV. Without it the export can show that a
        channel HAS a guide but not which source serves it, which is the one thing
        a reader needs when a per-group mapping moved something unexpectedly.
        """
        epg_data = getattr(channel, "epg_data", None)
        source = getattr(epg_data, "epg_source", None)
        return getattr(source, "name", "") or ""

    def _precreate_mapped_sources(self, settings, logger):
        """Create a dummy EPGSource for every mapped group, channels or not.

        The operator needs to configure a source BEFORE its event channels appear,
        which they cannot do if the source is only created once a channel claims it.

        Creating a source is NOT evidence that the mapping will move anything: a
        group mapped here but absent from the `channel_groups` narrowing setting
        never enters the scan, so it routes nothing for ever. Validate Configuration
        reports that case; this method deliberately does not claim success.

        Returns the number of sources created. Never raises.
        """
        from apps.epg.models import EPGSource

        group_profiles, problems = ecm_profiles.build_group_profiles(settings)
        if not group_profiles:
            return 0

        created = 0
        for profile in group_profiles:
            # A name already taken by a NON-dummy source cannot be created: the name
            # column is unique, so get_or_create would raise an integrity error that
            # _ensure_profile_source catches and swallows, leaving the mapping broken
            # on every run for ever with only a log line to show for it.
            clash = EPGSource.objects.filter(
                name=profile.source_name).exclude(source_type="dummy").first()
            if clash is not None:
                logger.warning(
                    f"{LOG_PREFIX} {profile.source_name!r} is already an EPG source "
                    f"of type {clash.source_type!r}, so it cannot be used for a "
                    f"channel group. Choose another name in the group mapping.")
                continue

            existed = EPGSource.objects.filter(
                name=profile.source_name, source_type="dummy").exists()
            source = self._ensure_profile_source(profile, settings, logger)
            if source is not None and not existed:
                created += 1
        if created:
            logger.info(f"{LOG_PREFIX} Created {created} mapped EPG source(s). A "
                        f"source is created so it can be configured; it moves "
                        f"channels only once its group is in scan scope.")
        if problems:
            logger.warning(f"{LOG_PREFIX} {len(problems)} group mapping problem(s); "
                           f"run Validate Configuration to see them all.")
        return created

    def _run_managed_epg_pass(self, settings, logger, dry_run, enabled_channel_ids, scanned_channel_ids=None):
        """Attach/detach the plugin's managed dummy EPG based on current settings.

        `scanned_channel_ids` is the full in-scope universe this scan looked at (profile +
        group filtered). When the feature is ON, the detach is restricted to that set so a
        narrow channel_groups run can't strip the managed dummy off channels in other
        groups (bug-045). When the feature is OFF, the detach is global (full teardown).

        If the master toggle is off, still runs the detach cleanup so turning the
        feature off reliably un-assigns managed EPG. Returns (attached_ids, detached_ids).

        Dry-run is a pure preview: it NEVER creates the EPGSource row and NEVER writes
        attach/detach changes. It only reports what an applied run would do.
        """
        from apps.epg.models import EPGSource

        toggle_on = self._get_bool_setting(settings, "manage_dummy_epg", False)

        if dry_run:
            # Runs before the managed_source lookup below: the reroute step never
            # depends on the DEFAULT "ECM Managed Dummy" source, so it must still
            # preview even when that source doesn't exist yet (i.e. even on the
            # early "managed_source is None" exit a few lines down).
            rerouted_ids = self._reroute_claimed_channels(
                settings, logger, True, enabled_channel_ids if toggle_on else [])
            if rerouted_ids:
                logger.info(f"{LOG_PREFIX} [dry-run] Reroute would move "
                            f"{len(rerouted_ids)} channel(s)")

            # Pure preview — locate existing source only; do not create.
            managed_source = EPGSource.objects.filter(
                name="ECM Managed Dummy", source_type="dummy"
            ).first()
            if managed_source is None:
                return [], []
            if toggle_on:
                null_ids = list(Channel.objects.filter(
                    id__in=enabled_channel_ids, epg_data__isnull=True
                ).values_list("id", flat=True))
                override_ids = self._managed_override_ids(settings, managed_source, enabled_channel_ids, logger)
                attached_ids = list(dict.fromkeys(list(null_ids) + override_ids))
                detach_q = Channel.objects.filter(
                    epg_data__epg_source=managed_source
                ).exclude(id__in=enabled_channel_ids)
                if scanned_channel_ids is not None:
                    detach_q = detach_q.filter(id__in=scanned_channel_ids)
                detached_ids = list(detach_q.values_list("id", flat=True))
            else:
                attached_ids = []
                detached_ids = list(Channel.objects.filter(
                    epg_data__epg_source=managed_source
                ).values_list("id", flat=True))
            logger.info(f"{LOG_PREFIX} [dry-run] Managed EPG would attach {len(attached_ids)}, detach {len(detached_ids)}")
            return attached_ids, detached_ids

        # Applied run — may create/refresh the source row.
        # Same reasoning as the dry-run call above: runs before the managed_source
        # lookup so it still fires even on the applied branch's own early
        # "managed_source is None" exit a few lines down.
        if toggle_on:
            # Inside the toggle deliberately: an operator with the managed dummy EPG
            # feature switched off must not have EPG sources created for them. And
            # after the dry-run return above, because a dry run never writes a row.
            self._precreate_mapped_sources(settings, logger)
        rerouted_ids = self._reroute_claimed_channels(
            settings, logger, False, enabled_channel_ids if toggle_on else [])
        if rerouted_ids:
            logger.info(f"{LOG_PREFIX} Reroute moved {len(rerouted_ids)} channel(s)")

        if toggle_on:
            managed_source = self._get_or_create_managed_epg_source(settings, logger)
        else:
            managed_source = EPGSource.objects.filter(
                name="ECM Managed Dummy", source_type="dummy"
            ).first()

        if managed_source is None:
            return [], []

        attached_ids = []
        if toggle_on:
            no_epg_channels = list(Channel.objects.filter(
                id__in=enabled_channel_ids, epg_data__isnull=True
            ))
            # Opt-in: also take over visible channels linked to a blank non-managed EPG.
            override_ids = set(self._managed_override_ids(settings, managed_source, enabled_channel_ids, logger))
            override_channels = (list(Channel.objects.filter(id__in=override_ids).select_related("epg_data"))
                                 if override_ids else [])
            channels_for_epg = no_epg_channels + override_channels
            channel_format = str(settings.get(
                "dummy_epg_channel_format", self.DEFAULT_DUMMY_EPG_CHANNEL_FORMAT)).strip().upper()
            if channel_format == "SE":
                # SE display names are derived from the live channel name, which can
                # change between runs (e.g. a different broadcaster pipe-segment) —
                # also resync EPGData.name for channels already bound to managed_source.
                already_attached = list(Channel.objects.filter(
                    id__in=enabled_channel_ids, epg_data__epg_source=managed_source
                ).select_related("epg_data"))
                channels_for_epg = no_epg_channels + override_channels + already_attached
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING))
            attached_ids = self._attach_managed_epg(channels_for_epg, managed_source, logger,
                                                       settings=settings, rate_limiter=rate_limiter,
                                                       override_ids=override_ids)

        # ON: de-manage only within the scanned scope (bug-045). OFF: full teardown.
        keep_ids = set(enabled_channel_ids) if toggle_on else set()
        detach_scope = scanned_channel_ids if toggle_on else None
        detached_ids = self._detach_managed_epg(managed_source, keep_ids, logger, scope_ids=detach_scope)

        return attached_ids, detached_ids

    def _get_channel_visibility(self, channel_id, profile_ids, logger):
        """Get current visibility status for a channel in profiles - returns True if enabled in ANY profile"""
        try:
            # Check if channel is enabled in any of the profiles
            membership = ChannelProfileMembership.objects.filter(
                channel_id=channel_id,
                channel_profile_id__in=profile_ids,
                enabled=True
            ).first()
            
            return membership is not None
        except Exception as e:
            logger.warning(f"Error getting visibility for channel {channel_id}: {e}")
            return False

    def _acquire_scan_lock(self, logger):
        """Acquire the cross-worker scan flock, breaking a stale/leaked lock.

        Returns an open, flock-held file object on success, or None if a *live*
        scan currently holds the lock. If the lock is held but its file mtime is
        older than SCAN_LOCK_STALE_SECONDS, the holder is assumed dead or leaked
        (e.g. an fd inherited by a forked uwsgi/celery worker that never released
        it) and the lock is forcibly broken by unlinking the file and acquiring
        on a fresh inode. The old, orphaned flock then refers to an unlinked
        inode and blocks nothing.

        The lock file is opened in append mode (never truncates, and opening does
        not touch mtime), so a failed acquire by a waiter does NOT reset the
        staleness clock. On success we stamp mtime to mark this holder's start.
        """
        path = PluginConfig.SCAN_LOCK_FILE
        for attempt in (1, 2):
            try:
                fd = open(path, 'a')
            except OSError as e:
                logger.warning(f"{LOG_PREFIX} Could not open scan lock file {path}: {e}")
                return None
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, IOError):
                fd.close()
                stale = False
                age = None
                try:
                    mtime = os.path.getmtime(path)
                    now = time.time()
                    age = now - mtime
                    stale = ecm_parsing.lock_is_stale(
                        mtime, now, PluginConfig.SCAN_LOCK_STALE_SECONDS
                    )
                except OSError:
                    stale = False
                if attempt == 1 and stale:
                    logger.warning(
                        f"{LOG_PREFIX} Breaking stale scan lock (age {age:.0f}s > "
                        f"{PluginConfig.SCAN_LOCK_STALE_SECONDS}s); previous holder "
                        f"likely crashed or leaked the lock fd"
                    )
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                    continue  # retry once on a fresh inode
                return None
            # Acquired. Stamp mtime so staleness reflects THIS holder's start time
            # (a leaked holder never stamps again, so its lock ages out).
            # NOTE: mtime is stamped once here and NOT refreshed during the scan.
            # A real scan finishes in seconds, so SCAN_LOCK_STALE_SECONDS (900s)
            # is never reached by a live scan. If the scan body ever gains slow
            # work (e.g. an external HTTP/EPG fetch) that could exceed that, add a
            # periodic os.utime(path) heartbeat or raise the threshold — otherwise
            # a waiter could break a still-running scan and run a second one.
            try:
                os.utime(path, None)
            except OSError:
                pass
            return fd
        return None

    @staticmethod
    def _group_name_q(field, group_names):
        """Build a case-insensitive OR filter matching `field` against any name in
        `group_names`. Django has no `__in` + `iexact` combo, so this ORs per-name
        `__iexact` lookups. Mirrors the profile lookup (name__iexact) so configured
        channel-group names match regardless of case (bug-049) — provider group names
        carry exotic unicode/casing that is trivial to mistype."""
        from django.db.models import Q
        q = Q()
        for name in group_names:
            q |= Q(**{f"{field}__iexact": name})
        return q

    def _scan_and_update_channels(self, settings, logger, dry_run=True, is_scheduled_run=False):
        """Scan channels and update visibility based on hide rules priority"""
        # Source the timezone from Dispatcharr's global setting (overwrites any
        # stale/absent disk value). MUST stay first: every per-channel date rule
        # and _localized_template_props below reads settings["timezone"].
        settings["timezone"] = self._dispatcharr_timezone()
        # Cross-worker serialization: one scan at a time across all uwsgi workers.
        # Covers manual Run Now / Dry Run as well as scheduled runs.
        lock_fd = None
        if fcntl:
            lock_fd = self._acquire_scan_lock(logger)
            if lock_fd is None:
                msg = "Another scan is already running in this or another worker. Skipping."
                if is_scheduled_run:
                    logger.info(f"{LOG_PREFIX} {msg}")
                    return {"status": "success", "message": msg, "skipped_due_to_lock": True}
                logger.warning(f"{LOG_PREFIX} {msg}")
                return {"status": "error", "message": msg, "skipped_due_to_lock": True}

        try:
            # Validate required settings
            channel_profile_names_str = settings.get("channel_profile_name", "").strip()
            if not channel_profile_names_str:
                return {"status": "error", "message": "Channel Profile Name is required. Please configure it in the plugin settings."}
            
            # Parse multiple profile names
            channel_profile_names = [name.strip() for name in channel_profile_names_str.split(',') if name.strip()]
            
            # Parse hide rules
            hide_rules_text = settings.get("hide_rules_priority", "").strip()
            hide_rules = self._parse_hide_rules(hide_rules_text, logger)

            if not hide_rules:
                return {"status": "error", "message": "No valid hide rules configured. Please check Hide Rules Priority field."}

            # Reconstruct rules text for CSV export (includes defaults if original was empty)
            hide_rules_text_for_export = ','.join([
                f'[{r[0]}:{r[1]}]' if r[1] is not None and not isinstance(r[1], tuple)
                else f'[{r[0]}:{r[1][0]}:{r[1][1]}h]' if isinstance(r[1], tuple)
                else f'[{r[0]}]'
                for r in hide_rules
            ])
            

            
            # Get Channel Profiles via ORM
            logger.info(f"Fetching Channel Profile(s): {', '.join(channel_profile_names)}")
            profile_ids = []
            found_profile_names = []
            for profile_name in channel_profile_names:
                try:
                    profile = ChannelProfile.objects.get(name__iexact=profile_name.strip())
                    profile_ids.append(profile.id)
                    found_profile_names.append(profile_name)
                except ChannelProfile.DoesNotExist:
                    logger.warning(f"Channel Profile '{profile_name}' not found")
            
            if not profile_ids:
                return {"status": "error", "message": f"None of the specified Channel Profiles were found: {channel_profile_names_str}. Please check the profile names in settings."}
            
            logger.info(f"Found {len(profile_ids)} profile(s): {', '.join(found_profile_names)}")
            
            # Get ALL channels in the profiles (both enabled and disabled) via membership
            memberships = ChannelProfileMembership.objects.filter(
                channel_profile_id__in=profile_ids
            ).select_related('channel')
            
            all_channel_ids = [m.channel_id for m in memberships]
            
            if not all_channel_ids:
                return {"status": "error", "message": f"Channel Profile(s) '{', '.join(found_profile_names)}' have no channels."}
            
            logger.info(f"Found {len(all_channel_ids)} channels in profile(s) '{', '.join(found_profile_names)}' (including hidden channels)")
            
            # Get channels query - now includes both visible and hidden channels
            channels_query = Channel.objects.filter(id__in=all_channel_ids).select_related('channel_group', 'epg_data')
            
            # Apply group filter if specified. Group names are matched
            # case-insensitively (like profile names) so minor case differences and
            # exotic provider unicode still match (bug-049).
            channel_groups_str = settings.get("channel_groups", "").strip()
            group_names = []
            if channel_groups_str:
                group_names = [g.strip() for g in channel_groups_str.split(',') if g.strip()]
                channels_query = channels_query.filter(self._group_name_q("channel_group__name", group_names))
                logger.info(f"Filtering to groups: {', '.join(group_names)}")

            channels = list(channels_query)
            total_channels = len(channels)

            # Surface configured group names that matched no channel in scope, instead
            # of silently dropping them (a typo/case/unicode mismatch otherwise looks
            # like the plugin "did nothing") (bug-049).
            unmatched_groups = []
            if group_names:
                present = {(c.channel_group.name or "").strip().casefold()
                           for c in channels if c.channel_group}
                unmatched_groups = [g for g in group_names if g.casefold() not in present]
                if unmatched_groups:
                    logger.warning(
                        f"{LOG_PREFIX} Configured channel group(s) matched no channels in "
                        f"profile(s) '{', '.join(found_profile_names)}': "
                        f"{', '.join(unmatched_groups)} — check spelling/case/unicode.")

            # A group entry containing "|" is a user carrying the alternation character
            # over from the three regex fields into this comma-separated one. It glues
            # several real group names into a single name that matches nothing, so a
            # whole slice of the configuration drops out of scope while the run still
            # reports success. Say so explicitly rather than leaving them to spot it.
            piped_groups = [g for g in unmatched_groups if "|" in g]
            separator_hint = (
                "Channel Groups is comma-separated; the | character belongs only in "
                "the regex fields." if piped_groups else "")

            if total_channels == 0:
                extra = (f" Unmatched group name(s): {', '.join(unmatched_groups)}."
                         if unmatched_groups else "")
                if separator_hint:
                    extra += f" {separator_hint}"
                return {"status": "error", "message": f"No channels found in profile(s) '{', '.join(found_profile_names)}' with the specified groups.{extra}"}
            
            logger.info(f"Processing {total_channels} channels...")
            
            # Compile regex for ignore pattern
            regex_ignore = None
            regex_ignore_str = settings.get("regex_channels_to_ignore", "").strip()
            if regex_ignore_str:
                try:
                    regex_ignore = re.compile(regex_ignore_str, re.IGNORECASE)
                    logger.info(f"Ignore regex compiled: {regex_ignore_str}")
                except re.error as e:
                    return {"status": "error", "message": f"Invalid 'Regex: Channel Names to Ignore': {e}"}

            regex_force_visible = None
            regex_force_visible_str = settings.get("regex_force_visible", "").strip()
            if regex_force_visible_str:
                try:
                    regex_force_visible = re.compile(regex_force_visible_str, re.IGNORECASE)
                    logger.info(f"Force visible regex compiled: {regex_force_visible_str}")
                except re.error as e:
                    return {"status": "error", "message": f"Invalid 'Regex: Force Visible Channels': {e}"}
            
            # Initialize progress tracker
            progress = ProgressTracker(total_channels, "Channel Scan", logger)

            # Load undated-channel first-seen tracker (used by [UndatedAge:N] rule)
            self._undated_tracker = self._load_undated_tracker(logger)
            tracker_before = len(self._undated_tracker)
            # Cleared per scan so a configuration problem is reported once on every run
            # rather than once ever, which would hide it from every later run's log.
            self._undated_warned = set()
            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)
            # Capture once per scan so records and rule evaluations agree even if
            # the scan crosses local midnight.
            scan_started_at = datetime.now(local_tz)
            self._undated_today_str = scan_started_at.date().isoformat()
            today_str = self._undated_today_str
            # The moment a first-seen record is stamped with, kept beside the date so
            # [UndatedEnded] can tell that an inferred event window closed before the
            # channel was ever visible. A date alone cannot express that.
            self._undated_now_iso = scan_started_at.isoformat()
            tracked_this_scan = set()

            results = []
            channels_to_hide = []
            channels_to_show = []
            channels_ignored = []
            channels_for_duplicate_check = []
            # Channels claimed by the force-visible regex. Recorded separately
            # because that branch returns to the top of the loop without adding
            # anything to channels_for_duplicate_check, which is how they used to
            # fall out of the managed-EPG enabled set entirely (bug-175).
            force_visible_channel_ids = []

            # Track channel info for enhanced logging
            channel_info_map = {}

            # Optional pacing for large profiles. Reads from settings each scan so
            # toggling the UI select takes effect on the next run.
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING))
            if rate_limiter.is_active():
                logger.info(f"{LOG_PREFIX} Rate limiting active: {rate_limiter.level} ({rate_limiter.delay}s/channel)")

            # Process each channel
            for i, channel in enumerate(channels):
                if self._op_stop_event.is_set():
                    logger.info(f"{LOG_PREFIX} Scan cancelled by user.")
                    return {"status": "success", "message": "Scan cancelled."}

                progress.update()

                channel_name = self._get_effective_name(channel, settings, logger)
                current_visible = self._get_channel_visibility(channel.id, profile_ids, logger)
                
                logger.debug(f"Processing channel {channel.id} using name '{channel_name}' (source={settings.get('name_source', 'Channel_Name')})")

                # Check if channel should be ignored
                if regex_ignore and regex_ignore.search(channel_name):
                    channels_ignored.append(channel.id)
                    # Preserve any existing undated-tracker entry for this channel so first_seen
                    # doesn't reset if the user later removes the ignore regex.
                    tracked_this_scan.add(str(channel.id))
                    results.append({
                        "channel_id": channel.id,
                        "channel_name": channel_name,
                        "channel_number": float(channel.channel_number) if channel.channel_number else None,
                        "channel_group": channel.channel_group.name if channel.channel_group else "No Group",
                        "current_visibility": "Visible" if current_visible else "Hidden",
                        "action": "Ignored",
                        "reason": "Matches ignore regex",
                        "hide_rule": "",
                        "has_epg": "Yes" if channel.epg_data else "No",
                        "epg_source": self._epg_source_name(channel),
                        "managed_epg_assigned": False,
                        "managed_epg_detached": False,
                    })
                    rate_limiter.wait()
                    continue

                # Check if channel should be forced visible
                if regex_force_visible and regex_force_visible.search(channel_name):
                    if not current_visible:
                        channels_to_show.append(channel.id)
                    force_visible_channel_ids.append(channel.id)

                    # Preserve any existing undated-tracker entry — same reason as above.
                    tracked_this_scan.add(str(channel.id))
                    results.append({
                        "channel_id": channel.id,
                        "channel_name": channel_name,
                        "channel_number": float(channel.channel_number) if channel.channel_number else None,
                        "channel_group": channel.channel_group.name if channel.channel_group else "No Group",
                        "current_visibility": "Visible" if current_visible else "Hidden",
                        "action": "Forced Visible" if not current_visible else "Visible (Forced)",
                        "reason": "Matches force visible regex",
                        "hide_rule": "[ForceVisible]",
                        "has_epg": "Yes" if channel.epg_data else "No",
                        "epg_source": self._epg_source_name(channel),
                        "managed_epg_assigned": False,
                        "managed_epg_detached": False,
                    })
                    rate_limiter.wait()
                    continue

                # Update undated-channel tracker: record channels with no extractable date,
                # drop those that now have a date.
                if self._extract_date_from_channel_name(channel_name, logger, settings) is None:
                    self._record_undated_channel(self._undated_tracker, channel.id, channel_name, today_str,
                                                 now_iso=getattr(self, '_undated_now_iso', None))
                    tracked_this_scan.add(str(channel.id))
                else:
                    self._undated_tracker.pop(str(channel.id), None)

                # Check hide rules
                should_hide, reason = self._check_channel_should_hide(channel, hide_rules, logger, settings)
                
                action_needed = None
                if should_hide:
                    if current_visible:
                        action_needed = "hide"
                else:
                    if not current_visible:
                        action_needed = "show"
                
                # Store channel info for duplicate detection and logging
                channel_info_map[channel.id] = {
                    'channel_name': channel_name,
                    'channel_number': float(channel.channel_number) if channel.channel_number else None,
                    'reason': reason,
                    'current_visible': current_visible
                }
                
                channels_for_duplicate_check.append({
                    'channel_id': channel.id,
                    'channel_name': channel_name,
                    'channel_number': float(channel.channel_number) if channel.channel_number else None,
                    'action_needed': action_needed,
                    'reason': reason,
                    'current_visible': current_visible,
                    'channel_group': channel.channel_group.name if channel.channel_group else "No Group",
                    'has_epg': "Yes" if channel.epg_data else "No",
                    'epg_source': self._epg_source_name(channel),
                })
                
                # Determine initial action (will be refined by duplicate handling)
                if action_needed == "hide":
                    channels_to_hide.append(channel.id)
                elif action_needed == "show":
                    channels_to_show.append(channel.id)

                rate_limiter.wait()

            # Prune undated tracker: drop entries for channels not evaluated this scan
            # (deleted or now dated). Ignored/force-visible channels are preserved if they
            # already have entries — see the per-channel loop above.
            pruned = [k for k in self._undated_tracker if k not in tracked_this_scan]
            for k in pruned:
                self._undated_tracker.pop(k, None)
            saved = self._save_undated_tracker(self._undated_tracker, logger)
            save_status = "saved" if saved else "save FAILED (see errors above)"
            logger.info(f"{LOG_PREFIX} Undated tracker: {tracker_before} loaded, {len(tracked_this_scan)} tracked, {len(pruned)} pruned, {len(self._undated_tracker)} {save_status}")

            # Handle duplicates - only process channels that would be visible
            logger.info("Checking for duplicate channels...")
            # Filter to only channels that would be visible (either currently visible or about to be shown)
            potentially_visible_channels = [
                ch for ch in channels_for_duplicate_check 
                if (ch['current_visible'] and ch['channel_id'] not in channels_to_hide) 
                or ch['channel_id'] in channels_to_show
            ]
            
            duplicate_hide_list = self._handle_duplicates(
                potentially_visible_channels,
                channels_to_hide,
                channels_to_show,
                logger,
                strategy=settings.get("duplicate_strategy", "lowest_number"),
                keep_duplicates=self._get_bool_setting(settings, "keep_duplicates", False)
            )

            # Managed Dummy EPG pass — runs before results are built so per-channel
            # result dicts can report managed_epg_assigned / managed_epg_detached.
            # Compute the "enabled after this scan" set from in-memory decisions so
            # dry-run and applied-run paths produce identical attach/detach counts.
            managed_attached_set = set()
            managed_detached_set = set()
            # The decision itself is ecm_profiles.managed_epg_enabled_ids, a pure
            # function, so it can be unit-tested without a container and so a new
            # early branch in this loop cannot silently drop channels out of the
            # attach set and the detach keep-set at the same time (bug-175).
            enabled_channel_ids = ecm_profiles.managed_epg_enabled_ids(
                [(ch["channel_id"], ch["current_visible"])
                 for ch in channels_for_duplicate_check],
                force_visible_channel_ids,
                channels_to_hide,
                channels_to_show,
                duplicate_hide_list,
            )
            # The in-scope universe this scan considered (profile + group filtered,
            # visible AND hidden). The managed-EPG detach is scoped to this so narrowing
            # channel_groups can't strip the dummy off channels in other groups (bug-045).
            # Channels matched by the ignore regex are removed from it: the plugin never
            # attaches to them, so leaving them in meant it could only take EPG away from
            # a channel it was told to leave alone.
            scanned_channel_ids = ecm_profiles.managed_epg_detach_scope(
                [c.id for c in channels], channels_ignored)
            managed_attached_ids, managed_detached_ids = self._run_managed_epg_pass(
                settings, logger, dry_run, enabled_channel_ids, scanned_channel_ids
            )
            managed_attached_set = set(managed_attached_ids)
            managed_detached_set = set(managed_detached_ids)

            # Patch result rows appended by the ignored-regex / force-visible
            # early-exit branches earlier in this method — they hardcode the
            # managed-EPG flags to False because the pass hadn't run yet. The
            # detach pass may have cleared their epg_data (if they were bound
            # in a prior scan), so re-sync the report with actual set state.
            if managed_detached_set or managed_attached_set:
                for row in results:
                    cid = row.get("channel_id")
                    if cid in managed_detached_set:
                        row["managed_epg_detached"] = True
                        row["has_epg"] = "No"   # detached this run -> no longer linked
                    if cid in managed_attached_set:
                        row["managed_epg_assigned"] = True
                        row["has_epg"] = "Yes"  # attached this run -> now linked

            # Build final results with duplicate information
            for channel_info in channels_for_duplicate_check:
                channel_id = channel_info['channel_id']
                action_needed = channel_info['action_needed']
                reason = channel_info['reason']
                
                # Check if this channel was marked for hiding due to duplicates
                if channel_id in duplicate_hide_list:
                    final_action = "Hide"
                    reason = "Duplicate channel (keeping better match)"
                elif action_needed == "hide":
                    final_action = "Hide"
                elif action_needed == "show":
                    final_action = "Show"
                else:
                    # No action needed - distinguish between visible and hidden
                    if channel_info['current_visible']:
                        final_action = "Visible"
                    else:
                        final_action = "No change"
                
                logger.debug(f"Decision for Channel {channel_id} ('{channel_info['channel_name']}'): Action={final_action}, Reason='{reason}'")

                # Extract rule tag from reason for easier filtering
                hide_rule = ""
                if reason and reason.startswith("["):
                    # Extract text between brackets, e.g., "[PastDate:0]" from "[PastDate:0] Event date..."
                    bracket_end = reason.find("]")
                    if bracket_end > 0:
                        hide_rule = reason[1:bracket_end]
                
                # has_epg was captured before the managed pass; reconcile it with this
                # run's attach/detach so the CSV doesn't show e.g. has_epg=No alongside
                # managed_epg_assigned=True (bug-050).
                post_has_epg = channel_info['has_epg'] == "Yes"
                if channel_id in managed_attached_set:
                    post_has_epg = True
                elif channel_id in managed_detached_set:
                    post_has_epg = False

                results.append({
                    "channel_id": channel_id,
                    "channel_name": channel_info['channel_name'],
                    "channel_number": channel_info['channel_number'],
                    "channel_group": channel_info['channel_group'],
                    "current_visibility": "Visible" if channel_info['current_visible'] else "Hidden",
                    "action": final_action,
                    "reason": reason,
                    "hide_rule": hide_rule,
                    "has_epg": "Yes" if post_has_epg else "No",
                    "epg_source": channel_info.get("epg_source", ""),
                    "managed_epg_assigned": channel_id in managed_attached_set,
                    "managed_epg_detached": channel_id in managed_detached_set,
                })
            
            # Mark scan as complete
            progress.finish()

            total_duplicates_hidden = len(duplicate_hide_list)
            logger.info(f"Scan completed: {len(channels_to_hide)} to hide, {len(channels_to_show)} to show, {len(channels_ignored)} ignored, {total_duplicates_hidden} duplicates hidden")

            # Count what each of the three regex fields actually matched. A field the
            # user filled in that matched nothing is the single clearest sign the
            # pattern is wrong, and previously nothing anywhere reported it: the run
            # looked identical to one where the pattern was doing its job. The counts
            # come from the decisions already recorded in `results`, so this adds no
            # extra pass over the channels.
            regex_field_counts = []
            _inactive_str = settings.get("regex_mark_inactive", "").strip()
            if regex_ignore_str:
                regex_field_counts.append(("Regex: Channel Names to Ignore", len(channels_ignored)))
            if _inactive_str:
                # Count by re-testing the names rather than by counting [InactiveRegex]
                # decisions. Hide rules are first-match-wins, so a channel this pattern
                # genuinely matches can be hidden by a rule placed earlier in the
                # priority list, and a decision count would then report "matched
                # nothing" for a pattern that is working. Compiled the same way the
                # rule itself compiles it, including the unicode_escape step.
                try:
                    _inactive_re = re.compile(
                        bytes(_inactive_str, "utf-8").decode("unicode_escape"), re.IGNORECASE)
                    regex_field_counts.append((
                        "Regex: Mark Channel as Inactive",
                        sum(1 for r in results if _inactive_re.search(r.get("channel_name") or ""))))
                except re.error:
                    # An invalid pattern is already reported by Validate Configuration
                    # and warned about per channel; do not add a misleading zero here.
                    pass
            if regex_force_visible_str:
                regex_field_counts.append((
                    "Regex: Force Visible Channels",
                    sum(1 for r in results if r.get("hide_rule") == "[ForceVisible]")))
            unmatched_regex_fields = [label for label, count in regex_field_counts if count == 0]
            for label, count in regex_field_counts:
                if count == 0:
                    logger.warning(
                        f"{LOG_PREFIX} '{label}' is set but matched no channels this scan. "
                        f"These fields are matched against the channel or stream name only, "
                        f"never against guide programme titles.")

            # Export to CSV
            csv_filepath = None
            should_create_csv = False
            if is_scheduled_run:
                should_create_csv = self._get_bool_setting(settings, "enable_scheduled_csv_export", False)
                logger.info(f"{LOG_PREFIX} Scheduled run - CSV export: {'ENABLED' if should_create_csv else 'DISABLED'}")
            else:
                should_create_csv = True

            if should_create_csv:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_filename = f"event_channel_managarr_{'dryrun' if dry_run else 'applied'}_{timestamp}.csv"

                # Calculate statistics by rule
                rule_stats = {}
                for result in results:
                    rule = result.get('hide_rule', 'N/A')
                    if result.get('action') == 'Hide':
                        rule_stats[rule] = rule_stats.get(rule, 0) + 1

                header_lines = [
                    f"Event Channel Managarr v{self.version} - {'Dry Run' if dry_run else 'Applied'} - {timestamp}",
                    f"Total Channels Processed: {len(results)}",
                    f"Channels to Hide: {len(channels_to_hide)}",
                    f"Channels to Show: {len(channels_to_show)}",
                    f"Channels Ignored: {len(channels_ignored)}",
                    f"Duplicates Hidden: {total_duplicates_hidden}",
                    f"Managed EPG Attached: {len(managed_attached_set)}",
                    f"Managed EPG Detached: {len(managed_detached_set)}",
                    f"Rate Limiting: {settings.get('rate_limiting', self.DEFAULT_RATE_LIMITING)}",
                ]
                if rule_stats:
                    header_lines.append("Rule Effectiveness:")
                    for rule, count in sorted(rule_stats.items(), key=lambda x: x[1], reverse=True):
                        header_lines.append(f"  {rule}: {count} channels")
                if regex_field_counts:
                    header_lines.append("Regex Field Matches:")
                    for label, count in regex_field_counts:
                        note = "  <- matched nothing" if count == 0 else ""
                        header_lines.append(f"  {label}: {count} channels{note}")
                if unmatched_groups:
                    header_lines.append(
                        f"Channel Groups that matched no channels: {', '.join(unmatched_groups)}")
                    if separator_hint:
                        header_lines.append(f"  {separator_hint}")
                header_lines.append(f"Hide Rules Priority: {hide_rules_text_for_export}")

                # Full settings snapshot so a CSV is self-describing. Skip legacy keys
                # that may hold credentials (dispatcharr_username/password from pre-ORM
                # versions) and already-exported lines (rate_limiting, hide_rules_priority).
                settings_keys = [
                    "timezone",
                    "channel_profile_name",
                    "channel_groups",
                    "name_source",
                    "regex_channels_to_ignore",
                    "regex_mark_inactive",
                    "regex_force_visible",
                    "past_date_grace_hours",
                    "undated_event_grace_hours",
                    "duplicate_strategy",
                    "keep_duplicates",
                    "auto_set_dummy_epg_on_hide",
                    "manage_dummy_epg",
                    "dummy_epg_event_duration_hours",
                    "dummy_epg_event_timezone",
                    "dummy_epg_channel_format",
                    "group_epg_source_map",
                    "scheduled_times",
                    "enable_scheduled_csv_export",
                ]
                # The plugin no longer owns a timezone setting; the scheduler/display
                # timezone is sourced from Dispatcharr's General Settings -> Time Zone
                # (injected into settings["timezone"] at scan start). Label it so the
                # self-describing CSV makes the source obvious.
                settings_labels = {"timezone": "timezone (from Dispatcharr)"}
                header_lines.append("Settings:")
                for k in settings_keys:
                    v = settings.get(k, "")
                    if v == "" or v is None:
                        v_str = "(empty)"
                    else:
                        v_str = str(v)
                    header_lines.append(f"  {settings_labels.get(k, k)}: {v_str}")

                fieldnames = ['channel_id', 'channel_name', 'channel_number', 'channel_group',
                            'current_visibility', 'action', 'reason', 'hide_rule', 'has_epg',
                            'epg_source', 'managed_epg_assigned', 'managed_epg_detached']
                csv_filepath = self._export_csv(csv_filename, results, fieldnames, logger, header_lines)
            
            # Apply changes if not dry run
            if not dry_run and (channels_to_hide or channels_to_show):
                # Log channels being hidden with reasons
                for channel_id in channels_to_hide:
                    if channel_id in channel_info_map:
                        info = channel_info_map[channel_id]
                        if channel_id in duplicate_hide_list:
                            reason = "Duplicate channel (keeping better match)"
                        else:
                            reason = info['reason']
                        logger.debug(f"Hiding channel {channel_id} (#{info['channel_number']}) '{info['channel_name']}' - Reason: {reason}")

                # Log channels being shown with reasons
                for channel_id in channels_to_show:
                    if channel_id in channel_info_map:
                        info = channel_info_map[channel_id]
                        logger.debug(f"Showing channel {channel_id} (#{info['channel_number']}) '{info['channel_name']}' - Reason: {info['reason']}")

                # Apply visibility changes via ORM
                total_changes = len(channels_to_hide) + len(channels_to_show)
                logger.info(f"Applying visibility changes to {total_changes} channels across {len(profile_ids)} profile(s)...")

                with transaction.atomic():
                    if channels_to_hide:
                        ChannelProfileMembership.objects.filter(
                            channel_id__in=channels_to_hide,
                            channel_profile_id__in=profile_ids
                        ).update(enabled=False)

                    if channels_to_show:
                        ChannelProfileMembership.objects.filter(
                            channel_id__in=channels_to_show,
                            channel_profile_id__in=profile_ids
                        ).update(enabled=True)

                logger.info("Visibility changes applied successfully to all profiles")

                # Record the run only now: the transaction above has committed, so
                # these are changes that happened rather than changes that were
                # planned. Nothing below may depend on this succeeding.
                self._append_ledger_entry(
                    shown=len(channels_to_show),
                    hidden=len(channels_to_hide),
                    is_scheduled_run=is_scheduled_run,
                    logger=logger,
                )

            # Handle automatic EPG removal if enabled (bulk update)
            if not dry_run and self._get_bool_setting(settings, "auto_set_dummy_epg_on_hide", False) and channels_to_hide:
                logger.info(f"{LOG_PREFIX} Bulk-removing EPG data from {len(channels_to_hide)} hidden channels...")
                channels_with_epg = list(Channel.objects.filter(id__in=channels_to_hide, epg_data__isnull=False))
                if channels_with_epg:
                    for ch in channels_with_epg:
                        ch.epg_data = None
                    with transaction.atomic():
                        Channel.objects.bulk_update(channels_with_epg, ['epg_data'])
                    logger.info(f"{LOG_PREFIX} EPG bulk-removed from {len(channels_with_epg)} channels.")
                    self._trigger_frontend_refresh(settings, logger)

            # Save settings on every run
            self._save_settings(settings)

            # Save results
            result_data = {
                "scan_time": datetime.now().isoformat(),
                "dry_run": dry_run,
                "profile_names": ', '.join(found_profile_names),
                "total_channels": total_channels,
                "channels_to_hide": len(channels_to_hide),
                "channels_to_show": len(channels_to_show),
                "channels_ignored": len(channels_ignored),
                "results": results
            }
            
            with open(self.results_file, 'w') as f:
                json.dump(result_data, f, indent=2)
            
            self.last_results = results
            
            # Build summary message
            mode_text = "Dry Run" if dry_run else "Applied"
            
            # Configuration problems go FIRST. Dispatcharr's toast shows roughly seven
            # lines and clips from the MIDDLE, so a warning appended at the end is the
            # part most likely to be cut. Until now these two only reached the
            # container log, where a user never looks, and the run read as a success.
            message_parts = []
            if unmatched_groups:
                message_parts.append(
                    f"⚠️ {len(unmatched_groups)} configured channel group(s) matched no "
                    f"channels: {', '.join(unmatched_groups)}")
                if separator_hint:
                    message_parts.append(separator_hint)
            if unmatched_regex_fields:
                message_parts.append(
                    f"⚠️ Set but matched nothing: {', '.join(unmatched_regex_fields)}. "
                    f"These read the channel or stream name, never guide programme titles.")
            if message_parts:
                message_parts.append("")

            message_parts += [
                f"Channel Visibility Scan {mode_text}:",
                f"• Total channels processed: {total_channels}",
                f"• Channels to hide: {len(channels_to_hide)}",
                f"• Channels to show: {len(channels_to_show)}",
                f"• Channels ignored: {len(channels_ignored)}",
                f"• Duplicate channels hidden: {total_duplicates_hidden}",
                f"• Managed EPG: {len(managed_attached_set)} attached, {len(managed_detached_set)} detached",
                f"",
            ]
            if csv_filepath:
                message_parts.append(f"Results exported to: {csv_filepath}")
            else:
                message_parts.append(f"CSV export disabled for this run.")
            
            # Add scheduler status
            scheduled_times_str = settings.get("scheduled_times", "").strip()
            if scheduled_times_str:
                times = self._parse_scheduled_times(scheduled_times_str)
                time_list = [t.strftime('%H:%M') for t in times]
                message_parts.append(f"")
                message_parts.append(f"Scheduler active - runs daily at: {', '.join(time_list)}")
            
            if dry_run:
                message_parts.append("")
                message_parts.append("Use 'Run Now' to apply these changes.")
            else:
                message_parts.append("")
                message_parts.append("Changes applied successfully - GUI should update shortly.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts),
                "results": {
                    "total_channels": total_channels,
                    "to_hide": len(channels_to_hide),
                    "to_show": len(channels_to_show),
                    "ignored": len(channels_ignored),
                    "duplicates_hidden": total_duplicates_hidden,
                    "managed_epg_attached": len(managed_attached_set),
                    "managed_epg_detached": len(managed_detached_set),
                    "csv_file": csv_filepath if csv_filepath else "N/A"
                }
            }
            
        except Exception as e:
            logger.error(f"Error scanning channels: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error scanning channels: {str(e)}"}
        finally:
            if lock_fd:
                try:
                    if fcntl:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    lock_fd.close()
                except OSError:
                    pass

    def _compact_scan_summary(self, label, result):
        """Build a single-line toast-sized summary from a scan result dict.

        The full multi-line `message` returned by `_scan_and_update_channels`
        is kept in logs and in the CSV header; the short form below is what
        the Dispatcharr notification window shows.
        """
        if not isinstance(result, dict) or result.get("status") != "success":
            return None
        res = result.get("results") or {}
        total = res.get("total_channels", 0)
        to_hide = res.get("to_hide", 0)
        to_show = res.get("to_show", 0)
        attached = res.get("managed_epg_attached", 0)
        detached = res.get("managed_epg_detached", 0)
        parts = [f"{label}: {total} channels"]
        if to_hide or to_show:
            parts.append(f"{to_hide} hide / {to_show} show")
        if attached or detached:
            parts.append(f"EPG +{attached}/-{detached}")
        csv_file = res.get("csv_file")
        if csv_file and csv_file != "N/A":
            parts.append(f"CSV: {os.path.basename(csv_file)}")
        return " | ".join(parts)

    def _dry_run_bg(self, settings, logger, result_holder):
        """Background wrapper for dry_run; stores the result for the synchronous caller."""
        try:
            result_holder['result'] = self._scan_and_update_channels(settings, logger, dry_run=True)
        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Dry run error: {e}")
            result_holder['result'] = {"status": "error", "message": f"Dry run error: {e}"}

    def dry_run_action(self, settings, logger):
        """Preview channel visibility changes without applying them.

        Runs synchronously. Dispatcharr's action-button loading spinner is
        the busy indicator; the HTTP response carries a compact one-line
        summary for the completion notification. The full multi-line
        `message` that `_scan_and_update_channels` produces stays in logs
        and CSV headers for diagnostics.
        """
        result_holder = {}
        if not self._try_start_thread(self._dry_run_bg, (dict(settings), logger, result_holder)):
            return {"status": "error", "message": "Another operation is already running. Please wait for it to finish."}
        logger.info(f"{LOG_PREFIX} Starting dry run scan...")
        self._thread.join()
        result = result_holder.get('result', {"status": "error", "message": "Dry run produced no result."})
        summary = self._compact_scan_summary("Dry run", result)
        if summary:
            result["message"] = summary
        return result

    def _run_now_bg(self, settings, logger, result_holder):
        """Background wrapper for run_now; stores the result for the synchronous caller."""
        try:
            result = self._scan_and_update_channels(settings, logger, dry_run=False)
            if result.get("status") == "success":
                rs = result.get("results", {})
                if rs.get("to_hide", 0) > 0 or rs.get("to_show", 0) > 0:
                    self._trigger_frontend_refresh(settings, logger)
            result_holder['result'] = result
            logger.info(f"{LOG_PREFIX} Run Now completed: {result.get('message', 'Done')}")
        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Run Now error: {e}")
            result_holder['result'] = {"status": "error", "message": f"Run Now error: {e}"}

    def run_now_action(self, settings, logger):
        """Immediately scan and update channel visibility, synchronously.

        Same pattern as dry_run_action: synchronous thread.join so the
        action-button spinner covers the busy state, and the HTTP response
        returns a compact one-line summary that renders cleanly in the
        Dispatcharr notification window.
        """
        result_holder = {}
        if not self._try_start_thread(self._run_now_bg, (dict(settings), logger, result_holder)):
            return {"status": "error", "message": "Another operation is already running. Please wait for it to finish."}
        logger.info(f"{LOG_PREFIX} Starting Run Now scan...")
        self._thread.join()
        result = result_holder.get('result', {"status": "error", "message": "Run Now produced no result."})
        summary = self._compact_scan_summary("Run Now", result)
        if summary:
            result["message"] = summary
        return result

    def on_m3u_refresh_action(self, settings, logger):
        """Re-run the visibility scan after an M3U refresh.

        Wired to Dispatcharr's 'm3u_refresh' connect event via the action's
        "events": ["m3u_refresh"]. Dispatcharr calls run() with
        params={"event": "m3u_refresh", "payload": {...}}, which run() merges
        into settings -- so settings.get("event") tells us event vs manual click.

        Event path is gated on the opt-in setting and mirrors the scheduler's
        direct synchronous call to _scan_and_update_channels (the event fires in
        a Celery worker; no UI spinner thread needed). The cross-process
        SCAN_LOCK_FILE flock inside _scan_and_update_channels serializes against
        manual/scheduled runs.
        """
        triggered_by_event = settings.get("event") == "m3u_refresh"

        if triggered_by_event and not self._get_bool_setting(
            settings, "auto_rescan_on_m3u_refresh", self.DEFAULT_AUTO_RESCAN_ON_M3U_REFRESH
        ):
            # Disabled: no-op. Return None (not a dict) so run() does NOT emit a
            # per-refresh WebSocket notification -- avoids UI noise on every refresh.
            logger.debug(f"{LOG_PREFIX} [m3u_refresh] auto-rescan disabled, skipping")
            return None

        if triggered_by_event:
            payload = settings.get("payload") or {}
            # The real m3u_refresh payload carries the account under 'account_name'.
            account = payload.get("account_name") or payload.get("account") or "unknown"
            logger.info(f"{LOG_PREFIX} [m3u_refresh] Auto-rescan triggered by account '{account}'")
        else:
            logger.info(f"{LOG_PREFIX} Manual rescan (Rescan Now) requested")

        result = self._scan_and_update_channels(
            settings, logger, dry_run=False, is_scheduled_run=True
        )

        if isinstance(result, dict):
            summary = self._compact_scan_summary("M3U Rescan", result)
            if summary:
                result["message"] = summary
        return result

    def remove_epg_from_hidden_action(self, settings, logger):
        """Remove EPG data from all hidden/disabled channels in the selected profile and set to dummy EPG"""
        try:
            logger.info("Starting EPG removal from hidden channels...")
            
            # Validate required settings
            channel_profile_names_str = settings.get("channel_profile_name", "").strip()
            if not channel_profile_names_str:
                return {
                    "status": "error",
                    "message": "Channel Profile Name is required. Please configure it in settings."
                }
            
            # Parse multiple profile names
            channel_profile_names = [name.strip() for name in channel_profile_names_str.split(',') if name.strip()]
            if not channel_profile_names:
                return {
                    "status": "error",
                    "message": "Channel Profile Name is required. Please configure it in settings."
                }
            
            # Get channel profiles using Django ORM
            profile_ids = []
            found_profile_names = []
            for profile_name in channel_profile_names:
                try:
                    profile = ChannelProfile.objects.get(name=profile_name)
                    profile_ids.append(profile.id)
                    found_profile_names.append(profile_name)
                    logger.info(f"Found profile: {profile_name} (ID: {profile.id})")
                except ChannelProfile.DoesNotExist:
                    logger.warning(f"Channel profile '{profile_name}' not found")
            
            if not profile_ids:
                return {
                    "status": "error",
                    "message": f"None of the specified Channel Profiles were found: {channel_profile_names_str}"
                }
            
            # Get all channel memberships in these profiles that are disabled
            hidden_memberships = ChannelProfileMembership.objects.filter(
                channel_profile_id__in=profile_ids,
                enabled=False
            ).select_related('channel')

            # Apply group filter if specified
            channel_groups_str = settings.get("channel_groups", "").strip()
            if channel_groups_str:
                group_names = [g.strip() for g in channel_groups_str.split(',') if g.strip()]
                if group_names:
                    hidden_memberships = hidden_memberships.filter(
                        self._group_name_q("channel__channel_group__name", group_names))
                    logger.info(f"Filtering EPG removal to groups: {', '.join(group_names)}")
            
            if not hidden_memberships.exists():
                return {
                    "status": "success",
                    "message": "No hidden channels found in the selected profile. No EPG data to remove."
                }
            
            hidden_count = hidden_memberships.count()
            logger.info(f"Found {hidden_count} hidden channels")
            
            # Collect EPG removal results
            results = []
            total_epg_removed = 0
            channels_to_bulk_clear = []

            for membership in hidden_memberships:
                channel = membership.channel
                channel_id = channel.id
                channel_name = self._get_effective_name(channel, settings, logger) or 'Unknown'
                channel_number = channel.channel_number or 'N/A'

                if channel.epg_data:
                    epg_count = ProgramData.objects.filter(epg=channel.epg_data).count()
                    deleted_count = 0

                    if epg_count > 0:
                        deleted_count = ProgramData.objects.filter(epg=channel.epg_data).delete()[0]
                        total_epg_removed += deleted_count
                        logger.debug(f"Removed {deleted_count} EPG entries from channel {channel_number} - {channel_name}")

                    channel.epg_data = None
                    channels_to_bulk_clear.append(channel)

                    results.append({
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'channel_number': channel_number,
                        'epg_entries_removed': deleted_count,
                        'status': 'set_to_dummy'
                    })
                else:
                    results.append({
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'channel_number': channel_number,
                        'epg_entries_removed': 0,
                        'status': 'already_dummy'
                    })

            # Bulk update all channels that had EPG cleared
            if channels_to_bulk_clear:
                with transaction.atomic():
                    Channel.objects.bulk_update(channels_to_bulk_clear, ['epg_data'])
                logger.info(f"{LOG_PREFIX} Bulk-cleared EPG from {len(channels_to_bulk_clear)} channels")
            channels_set_to_dummy = len(channels_to_bulk_clear)
            
            # Export results to CSV
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_filename = f"epg_removal_{timestamp}.csv"
            fieldnames = ['channel_id', 'channel_name', 'channel_number', 'epg_entries_removed', 'status']
            csv_filepath = self._export_csv(csv_filename, results, fieldnames, logger)
            
            # Trigger frontend refresh
            self._trigger_frontend_refresh(settings, logger)
            
            # Build summary message
            message_parts = [
                f"EPG Removal Complete:",
                f"• Hidden channels processed: {hidden_count}",
                f"• Channels set to dummy EPG: {channels_set_to_dummy}",
                f"• Total EPG entries removed: {total_epg_removed}",
                f"• Channels already using dummy EPG: {sum(1 for r in results if r['status'] == 'already_dummy')}",
                f"",
                f"Results exported to: {csv_filepath}",
                f"",
                f"Frontend refresh triggered - GUI should update shortly."
            ]
            
            return {
                "status": "success",
                "message": "\n".join(message_parts),
                "results": {
                    "hidden_channels": hidden_count,
                    "channels_set_to_dummy": channels_set_to_dummy,
                    "total_epg_removed": total_epg_removed,
                    "csv_file": csv_filepath
                }
            }
            
        except Exception as e:
            logger.error(f"Error removing EPG from hidden channels: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error removing EPG: {str(e)}"}


    def _trigger_frontend_refresh(self, settings, logger):
        """Trigger frontend channel list refresh via WebSocket"""
        try:
            send_websocket_update('updates', 'update', {
                "type": "plugin",
                "plugin": self.name,
                "message": "Channels updated"
            })
            logger.info("Frontend refresh triggered via WebSocket")
            return True
        except Exception as e:
            logger.warning(f"Could not trigger frontend refresh: {e}")
        return False

    def stop(self, context):
        """Clean shutdown: stop scheduler and any running operations."""
        logger = context.get("logger", LOGGER)
        self._stop_background_scheduler()
        self._op_stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info(f"{LOG_PREFIX} Plugin stopped.")
