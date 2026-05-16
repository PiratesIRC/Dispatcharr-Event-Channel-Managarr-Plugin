
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
import urllib.request
import urllib.error

from datetime import datetime, timedelta
from django.utils import timezone

# Django model imports
from apps.channels.models import Channel, ChannelProfileMembership, ChannelProfile, Stream
from apps.epg.models import ProgramData
from django.db import transaction
from core.utils import send_websocket_update

LOGGER = logging.getLogger("plugins.event_channel_managarr")
LOG_PREFIX = "[EventChannelManagarr]"

# Background scheduling globals
_bg_thread = None
_stop_event = threading.Event()
_scheduler_lock = threading.Lock()  # Prevent concurrent scheduler starts


class PluginConfig:
    """Centralized configuration constants for Event Channel Managarr."""

    PLUGIN_VERSION = "1.26.1362004"

    # Default timezone for scheduling
    DEFAULT_TIMEZONE = "America/Chicago"

    # Default name source for channel matching
    DEFAULT_NAME_SOURCE = "Channel_Name"  # Options: "Channel_Name" or "Stream_Name"

    # Default hide rules priority (comma-separated)
    DEFAULT_HIDE_RULES = "[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]"

    # Default duplicate handling strategy
    DEFAULT_DUPLICATE_STRATEGY = "lowest_number"  # Options: "lowest_number", "highest_number", "longest_name"

    # Default grace period for past date rule (in hours)
    DEFAULT_PAST_DATE_GRACE_HOURS = "4"

    # Default automatic EPG removal on hide
    DEFAULT_AUTO_REMOVE_EPG = True

    # Default CSV export for scheduled runs
    DEFAULT_SCHEDULED_CSV_EXPORT = False

    # Default keep duplicates setting
    DEFAULT_KEEP_DUPLICATES = False

    # Managed Dummy EPG feature defaults
    DEFAULT_MANAGE_DUMMY_EPG = False
    DEFAULT_EVENT_DURATION_HOURS = "3"
    DEFAULT_DUMMY_EPG_TIMEZONE = "US/Eastern"

    # Pacing for per-channel ORM writes ("none", "low", "medium", "high")
    DEFAULT_RATE_LIMITING = "none"

    # Version check interval (in seconds)
    VERSION_CHECK_INTERVAL = 86400  # 24 hours

    # Scheduler check interval (in seconds)
    SCHEDULER_CHECK_INTERVAL = 30

    # Scheduler stop timeout (in seconds)
    SCHEDULER_STOP_TIMEOUT = 10

    # File paths
    LAST_RUN_FILE = "/data/event_channel_managarr_last_run.json"
    SCAN_LOCK_FILE = "/data/event_channel_managarr_scan.lock"
    SETTINGS_FILE = "/data/event_channel_managarr_settings.json"
    RESULTS_FILE = "/data/event_channel_managarr_results.json"
    VERSION_CHECK_FILE = "/data/event_channel_managarr_version_check.json"
    UNDATED_FIRST_SEEN_FILE = "/data/event_channel_managarr_undated_first_seen.json"
    EXPORTS_DIR = "/data/exports"

    # GitHub repo for version checks
    GITHUB_OWNER = "PiratesIRC"
    GITHUB_REPO = "Dispatcharr-Event-Channel-Managarr-Plugin"


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
    DEFAULT_AUTO_REMOVE_EPG = PluginConfig.DEFAULT_AUTO_REMOVE_EPG
    DEFAULT_SCHEDULED_CSV_EXPORT = PluginConfig.DEFAULT_SCHEDULED_CSV_EXPORT
    DEFAULT_KEEP_DUPLICATES = PluginConfig.DEFAULT_KEEP_DUPLICATES
    DEFAULT_MANAGE_DUMMY_EPG = PluginConfig.DEFAULT_MANAGE_DUMMY_EPG
    DEFAULT_EVENT_DURATION_HOURS = PluginConfig.DEFAULT_EVENT_DURATION_HOURS
    DEFAULT_DUMMY_EPG_TIMEZONE = PluginConfig.DEFAULT_DUMMY_EPG_TIMEZONE
    DEFAULT_RATE_LIMITING = PluginConfig.DEFAULT_RATE_LIMITING
    VERSION_CHECK_INTERVAL = PluginConfig.VERSION_CHECK_INTERVAL
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
        """Dynamically generate fields list with version check"""
        # Check for updates from GitHub
        version_message = "Checking for updates..."
        try:
            # Check if we should perform a version check (once per day)
            if self._should_check_for_updates():
                # Perform the version check
                latest_version = self._get_latest_version(PluginConfig.GITHUB_OWNER, PluginConfig.GITHUB_REPO)

                # Check if it's an error message
                if latest_version.startswith("Error"):
                    version_message = f"⚠️ Could not check for updates: {latest_version}"
                else:
                    # Save the check result
                    self._save_version_check(latest_version)

                    # Compare versions
                    current = self.version
                    # Remove 'v' prefix if present in latest_version
                    latest_clean = latest_version.lstrip('v')

                    if current == latest_clean:
                        version_message = f"✅ You are up to date (v{current})"
                    else:
                        version_message = f"🔔 Update available! Current: v{current} → Latest: {latest_version}"
            else:
                # Use cached version info
                if self.cached_version_info:
                    latest_version = self.cached_version_info['latest_version']
                    current = self.version
                    latest_clean = latest_version.lstrip('v')

                    if current == latest_clean:
                        version_message = f"✅ You are up to date (v{current})"
                    else:
                        version_message = f"🔔 Update available! Current: v{current} → Latest: {latest_version}"
                else:
                    version_message = "ℹ️ Version check will run on next page load"
        except Exception as e:
            LOGGER.debug(f"Error during version check: {e}")
            version_message = f"⚠️ Error checking for updates: {str(e)}"

        # Build the fields list dynamically
        fields_list = [
            {
                "id": "version_status",
                "label": "📦 Plugin Version Status",
                "type": "info",
                "help_text": version_message
            },
            {
                "id": "_section_scope",
                "label": "📍 Scope",
                "type": "info",
                "description": "Which channels this plugin monitors and how it identifies them."
            },
            {
                "id": "timezone",
                "label": "🌍 Timezone",
                "type": "select",
                "default": self.DEFAULT_TIMEZONE,
                "help_text": "Timezone for scheduled runs. Select the timezone for scheduling. Only one can be selected.",
                "options": self._load_timezones_from_file()
            },
            {
                "id": "channel_profile_name",
                "label": "📺 Channel Profile Names (Required)",
                "type": "string",
                "default": "",
                "placeholder": "e.g. All, Favorites",
                "help_text": "REQUIRED: Channel Profile(s) containing channels to monitor. Use comma-separated names for multiple profiles.",
            },
            {
                "id": "channel_groups",
                "label": "📂 Channel Groups",
                "type": "text",
                "default": "",
                "placeholder": "e.g. PPV Live Events, Sports",
                "help_text": "Specific channel groups to monitor within the profile. Leave blank to monitor all groups in the profile.",
            },
            {
                "id": "name_source",
                "label": "🔤 Name Source",
                "type": "select",
                "default": self.DEFAULT_NAME_SOURCE,
                "help_text": "Select the source of the names to monitor. Only one can be selected.",
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
                "help_text": "How to interpret numeric dates like 04/05 in channel names. Auto = try MM/DD first; if month > 12, treat as DD/MM (handles most regional data). US = always MM/DD. EU = always DD/MM.",
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
                "description": "Priority-ordered rules that decide which channels to hide."
            },
            {
                "id": "hide_rules_priority",
                "label": "📜 Hide Rules Priority",
                "type": "text",
                "default": self.DEFAULT_HIDE_RULES,
                "placeholder": "[BlankName],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]",
                "help_text": "Define rules for hiding channels in priority order (first match wins). Comma-separated tags. Available tags: [NoEPG], [BlankName], [WrongDayOfWeek], [NoEventPattern], [EmptyPlaceholder], [ShortDescription], [ShortChannelName], [NumberOnly], [PastDate:days], [PastDate:days:Xh], [FutureDate:days], [UndatedAge:days], [InactiveRegex].",
            },
            {
                "id": "regex_channels_to_ignore",
                "label": "🚫 Regex: Channel Names to Ignore",
                "type": "text",
                "default": "",
                "placeholder": "^BACKUP|^TEST",
                "help_text": "Regular expression to match channel names that should be skipped entirely. Matching channels will not be processed.",
            },
            {
                "id": "regex_mark_inactive",
                "label": "💤 Regex: Mark Channel as Inactive",
                "type": "text",
                "default": "",
                "placeholder": "CANCELLED|COMING SOON|^TEST|^BACKUP|PLACEHOLDER",
                "help_text": "Regular expression to hide channels. This is processed as part of the [InactiveRegex] hide rule.",
            },
            {
                "id": "regex_force_visible",
                "label": "✅ Regex: Force Visible Channels",
                "type": "text",
                "default": "",
                "placeholder": "^NEWS|^WEATHER",
                "help_text": "Regular expression to match channel names that should ALWAYS be visible, overriding any hide rules.",
            },
            {
                "id": "past_date_grace_hours",
                "label": "📅 Past Date Grace Period (Hours)",
                "type": "number",
                "default": int(self.DEFAULT_PAST_DATE_GRACE_HOURS),
                "help_text": "Hours to wait after midnight before hiding past events. Useful for events that run late.",
            },
            {
                "id": "_section_duplicates",
                "label": "🎭 Duplicates",
                "type": "info",
                "description": "How to handle channels whose events collide."
            },
            {
                "id": "duplicate_strategy",
                "label": "🎭 Duplicate Handling Strategy",
                "type": "select",
                "default": self.DEFAULT_DUPLICATE_STRATEGY,
                "help_text": "Strategy to use when multiple channels have the same event.",
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
                "help_text": "If enabled, duplicate channels will be kept visible instead of being hidden. The duplicate strategy above will be ignored.",
            },
            {
                "id": "_section_epg",
                "label": "🔌 EPG Management",
                "type": "info",
                "description": "Optional automation for EPG assignment on visibility changes and a managed dummy EPG for channels without real EPG."
            },
            {
                "id": "auto_set_dummy_epg_on_hide",
                "label": "🔌 Auto-Remove EPG on Hide",
                "type": "boolean",
                "default": self.DEFAULT_AUTO_REMOVE_EPG,
                "help_text": "If enabled, automatically removes EPG data from a channel when it is hidden by the plugin.",
            },
            {
                "id": "manage_dummy_epg",
                "label": "🗓️ Manage Dummy EPG",
                "type": "boolean",
                "default": self.DEFAULT_MANAGE_DUMMY_EPG,
                "help_text": "If enabled, visible channels with no EPG assigned will be bound to a plugin-managed dummy EPG source. The guide shows the extracted event during its time window (and 'Offline' outside it), or the channel name as a 24-hour fallback if no time is parseable.",
            },
            {
                "id": "dummy_epg_event_duration_hours",
                "label": "⏱️ Event Duration (hours)",
                "type": "number",
                "default": int(self.DEFAULT_EVENT_DURATION_HOURS),
                "help_text": "How long each scheduled event should appear in the guide (hours). Before this window the guide shows 'Upcoming at <time>: <event>'; after, 'Ended at <time>: <event>'.",
            },
            {
                "id": "dummy_epg_event_timezone",
                "label": "📺 Channel Name Event Timezone",
                "type": "select",
                "default": self.DEFAULT_DUMMY_EPG_TIMEZONE,
                "help_text": "Timezone encoded in the event times inside channel names (e.g., US/Eastern for channels like '(4.17 8:30 PM ET)'). Different from the scheduler timezone above.",
                "options": self._load_timezones_from_file()
            },
            {
                "id": "_section_scheduling",
                "label": "⏰ Scheduling & Export",
                "type": "info",
                "description": "Scheduled runs and CSV export options."
            },
            {
                "id": "scheduled_times",
                "label": "⏰ Scheduled Run Times (24-hour format)",
                "type": "text",
                "default": "",
                "placeholder": "0600,1300,1800",
                "help_text": "Comma-separated times to run automatically each day (24-hour format). Example: 0600,1300,1800 runs at 6 AM, 1 PM, and 6 PM daily. Leave blank to disable scheduling.",
            },
            {
                "id": "enable_scheduled_csv_export",
                "label": "📄 Enable Scheduled CSV Export",
                "type": "boolean",
                "default": self.DEFAULT_SCHEDULED_CSV_EXPORT,
                "help_text": "If enabled, a CSV file of the scan results will be created when the plugin runs on a schedule. If disabled, no CSV will be created for scheduled runs.",
            },
            {
                "id": "_section_advanced",
                "label": "⚙️ Advanced",
                "type": "info",
                "description": "Performance and pacing controls for large channel profiles."
            },
            {
                "id": "rate_limiting",
                "label": "🐢 Rate Limiting",
                "type": "select",
                "default": self.DEFAULT_RATE_LIMITING,
                "help_text": "Pause between per-channel ORM operations. 'none' is fastest; 'low/medium/high' add 0.05/0.2/0.5 seconds per channel. Useful when scanning very large profiles (thousands of channels) on a small DB.",
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
        {"id": "validate_configuration", "label": "Validate Configuration", "description": "Test and validate all plugin settings", "button_label": "🔎 Validate", "button_variant": "outline", "button_color": "blue"},
        {"id": "update_schedule", "label": "Update Schedule", "description": "Save settings and update the scheduled run times", "button_label": "💾 Save Schedule", "button_variant": "filled", "button_color": "green"},
        {"id": "dry_run", "label": "Dry Run (Export to CSV)", "description": "Preview which channels would be hidden/shown without making changes", "button_label": "👁️ Dry Run", "button_variant": "outline", "button_color": "cyan"},
        {"id": "run_now", "label": "Run Now", "description": "Immediately scan and update channel visibility based on current EPG data", "button_label": "▶️ Run Now", "button_variant": "filled", "button_color": "green", "confirm": {"message": "This will apply visibility changes and (if enabled) attach/detach managed EPG. Continue?"}},
        {"id": "remove_epg_from_hidden", "label": "Remove EPG from Hidden Channels", "description": "Remove all EPG data from channels that are disabled/hidden in the selected profile", "button_label": "🧹 Remove EPG from Hidden", "button_variant": "filled", "button_color": "red", "confirm": {"message": "This will CLEAR EPG data from every hidden channel in the selected profile. Cannot be undone by this plugin. Continue?"}},
        {"id": "clear_csv_exports", "label": "Clear CSV Exports", "description": "Delete all CSV export files created by this plugin", "button_label": "🗑️ Clear CSV Exports", "button_variant": "filled", "button_color": "red", "confirm": {"message": "This will delete every CSV file in /data/exports created by this plugin. Continue?"}},
        {"id": "cleanup_periodic_tasks", "label": "Cleanup Orphaned Tasks", "description": "Remove any orphaned Celery periodic tasks from old plugin versions", "button_label": "🧼 Cleanup Orphaned Tasks", "button_variant": "outline", "button_color": "orange", "confirm": {"message": "This removes orphaned Celery periodic tasks left by older plugin versions. Continue?"}},
        {"id": "check_scheduler_status", "label": "Check Scheduler Status", "description": "Display scheduler thread status and diagnostic information", "button_label": "🩺 Check Scheduler", "button_variant": "outline", "button_color": "blue"},
    ]
    
    def __init__(self):
        self.results_file = PluginConfig.RESULTS_FILE
        self.settings_file = PluginConfig.SETTINGS_FILE
        self.version_check_file = PluginConfig.VERSION_CHECK_FILE
        self.last_results = []

        # Thread-safe operation locking
        self._thread = None
        self._thread_lock = threading.Lock()
        self._op_stop_event = threading.Event()

        # Version check cache
        self.cached_version_info = None

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

    def _get_latest_version(self, owner, repo):
        """
        Fetches the latest release tag name from GitHub using only Python's standard library.
        Returns the version string or an error message.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

        # Add a user-agent to avoid potential 403 Forbidden errors
        headers = {
            'User-Agent': 'Dispatcharr-Plugin-Version-Checker'
        }

        try:
            # Create a request object with headers
            req = urllib.request.Request(url, headers=headers)

            # Make the request and open the URL with a timeout
            with urllib.request.urlopen(req, timeout=5) as response:
                # Read the response and decode it as UTF-8
                data = response.read().decode('utf-8')

                # Parse the JSON string
                json_data = json.loads(data)

                # Get the tag name
                latest_version = json_data.get("tag_name")

                if latest_version:
                    return latest_version
                else:
                    return "Error: 'tag_name' key not found."

        except urllib.error.HTTPError as http_err:
            if http_err.code == 404:
                return f"Error: Repo not found or has no releases."
            else:
                return f"HTTP error: {http_err.code}"
        except Exception as e:
            # Catch other errors like timeouts
            return f"Error: {str(e)}"

    def _should_check_for_updates(self):
        """
        Check if we should perform a version check (once per day).
        Returns True if we should check, False otherwise.
        Also loads and caches the last check data.
        """
        try:
            if os.path.exists(self.version_check_file):
                with open(self.version_check_file, 'r') as f:
                    data = json.load(f)
                    last_check_time = data.get('last_check_time')
                    cached_latest_version = data.get('latest_version')

                    if last_check_time and cached_latest_version:
                        # Check if last check was within 24 hours
                        last_check_dt = datetime.fromisoformat(last_check_time)
                        now = datetime.now()
                        time_diff = now - last_check_dt

                        if time_diff.total_seconds() < self.VERSION_CHECK_INTERVAL:
                            # Use cached data
                            self.cached_version_info = {
                                'latest_version': cached_latest_version,
                                'last_check_time': last_check_time
                            }
                            return False  # Don't check again

            # Either file doesn't exist, or it's been more than 24 hours
            return True

        except Exception as e:
            LOGGER.debug(f"Error checking version check time: {e}")
            return True  # Check if there's an error

    def _save_version_check(self, latest_version):
        """Save the version check result to disk with timestamp"""
        try:
            data = {
                'latest_version': latest_version,
                'last_check_time': datetime.now().isoformat()
            }
            with open(self.version_check_file, 'w') as f:
                json.dump(data, f, indent=2)
            LOGGER.debug(f"Saved version check: {latest_version}")
        except Exception as e:
            LOGGER.debug(f"Error saving version check: {e}")

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
                group_names = [g.strip() for g in channel_groups_str.split(',') if g.strip()]
                channel_profile_names = [p.strip() for p in channel_profile_names_str.split(',') if p.strip()]

                # Find matching profile IDs via ORM
                profile_ids = list(
                    ChannelProfile.objects.filter(
                        name__in=channel_profile_names
                    ).values_list('id', flat=True)
                )

                if profile_ids:
                    # Get all channels in the profiles
                    memberships = ChannelProfileMembership.objects.filter(
                        channel_profile_id__in=profile_ids
                    ).select_related('channel', 'channel__channel_group')

                    # Get unique group names
                    available_groups = set()
                    for membership in memberships:
                        if membership.channel.channel_group:
                            available_groups.add(membership.channel.channel_group.name)

                    # Check which groups exist
                    found_groups = []
                    missing_groups = []

                    for group_name in group_names:
                        if group_name in available_groups:
                            found_groups.append(group_name)
                        else:
                            missing_groups.append(group_name)

                    # Report results
                    if missing_groups:
                        validation_results.append(f"❌ Groups: Not found - {', '.join(missing_groups)}")
                        has_errors = True

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

        # 6. Validate schedule
        scheduled_times = settings.get("scheduled_times", "").strip()
        if scheduled_times:
            times_list = [t.strip() for t in scheduled_times.split(',') if t.strip()]
            invalid = [t for t in times_list if len(t) != 4 or not t.isdigit()]
            if invalid:
                validation_results.append(f"❌ Schedule: Invalid - {', '.join(invalid)}")
                has_errors = True
            else:
                validation_results.append(f"✅ Schedule: {len(times_list)} times")
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
            if "keep_duplicates" not in settings:
                settings["keep_duplicates"] = self.DEFAULT_KEEP_DUPLICATES
            if "auto_set_dummy_epg_on_hide" not in settings:
                settings["auto_set_dummy_epg_on_hide"] = self.DEFAULT_AUTO_REMOVE_EPG
            if "manage_dummy_epg" not in settings:
                settings["manage_dummy_epg"] = self.DEFAULT_MANAGE_DUMMY_EPG
            if "dummy_epg_event_duration_hours" not in settings:
                settings["dummy_epg_event_duration_hours"] = self.DEFAULT_EVENT_DURATION_HOURS
            if "dummy_epg_event_timezone" not in settings:
                settings["dummy_epg_event_timezone"] = self.DEFAULT_DUMMY_EPG_TIMEZONE
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

    def _record_undated_channel(self, tracker, channel_id, channel_name, today_str):
        """Record/refresh a channel in the undated tracker. Returns the entry."""
        key = str(channel_id)
        entry = tracker.get(key)
        if not entry or entry.get("name") != channel_name:
            entry = {"first_seen": today_str, "name": channel_name}
            tracker[key] = entry
        return entry

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
        Returns datetime or None if the pair can't form a valid date.
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

    def _extract_date_from_channel_name(self, channel_name, logger, settings=None):
        """Extract date from channel name using various patterns, including hour if present"""
        if not channel_name:
            return None
        from dateutil import parser as dateutil_parser

        current_year = datetime.now().year
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        date_format = (settings or {}).get("date_format", "Auto")

        def _apply_meridiem(hour, meridiem):
            """Convert a 12-hour clock hour to 24-hour given an optional AM/PM token."""
            if not meridiem:
                return hour
            meridiem = meridiem.upper()
            if meridiem == "AM":
                return 0 if hour == 12 else hour
            else:
                return hour if hour == 12 else hour + 12

        # Pattern 0: start:YYYY-MM-DD HH:MM:SS[ AM/PM] or stop:YYYY-MM-DD HH:MM:SS[ AM/PM]
        for prefix in ["start:", "stop:"]:
            pattern0 = re.search(rf'{prefix}(\d{{4}})-(\d{{2}})-(\d{{2}})\s+(\d{{1,2}}):(\d{{2}}):(\d{{2}})\s*(?P<ap>[AaPp][Mm])?', channel_name)
            if pattern0:
                year, month, day, hour, minute, second = map(int, pattern0.groups()[:6])
                hour = _apply_meridiem(hour, pattern0.group("ap"))
                try:
                    extracted_date = datetime(year, month, day, hour, minute, second)
                    logger.debug(f"Extracted datetime {extracted_date} from pattern {prefix}YYYY-MM-DD HH:MM:SS[ AM/PM] in '{channel_name}'")
                    return extracted_date
                except ValueError:
                    pass

        # Pattern 0a: (YYYY-MM-DD HH:MM:SS[ AM/PM]) in parentheses
        pattern0a = re.search(r'\((\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(?P<ap>[AaPp][Mm])?\)', channel_name)
        if pattern0a:
            year, month, day, hour, minute, second = map(int, pattern0a.groups()[:6])
            hour = _apply_meridiem(hour, pattern0a.group("ap"))
            try:
                extracted_date = datetime(year, month, day, hour, minute, second)
                logger.debug(f"Extracted datetime {extracted_date} from pattern (YYYY-MM-DD HH:MM:SS[ AM/PM]) in '{channel_name}'")
                return extracted_date
            except ValueError:
                pass

        # Pattern 1: M/D/YYYY or M/D/YY — interpreted per date_format setting.
        pattern1 = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', channel_name)
        if pattern1:
            first, second, year = map(int, pattern1.groups())
            if year < 100:
                year += 2000
            extracted_date = self._resolve_numeric_date_pair(first, second, year, date_format)
            if extracted_date is not None:
                logger.debug(f"Extracted date {extracted_date.date()} from pattern M/D/YYYY ({date_format}) in '{channel_name}'")
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
                logger.debug(f"Extracted date {extracted_date.date()} from pattern DDth MONTH in '{channel_name}'")
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
                logger.debug(f"Extracted date {extracted_date} from pattern MONTH DD[ HH:MM] in '{channel_name}'")
                return extracted_date
            except (ValueError, dateutil_parser.ParserError):
                pass

        # Pattern 3: M.D without year e.g., "10.25" — interpreted per date_format setting.
        pattern3 = re.search(r'\b(\d{1,2})\.(\d{1,2})\b', channel_name)
        if pattern3:
            first, second = map(int, pattern3.groups())
            extracted_date = self._resolve_numeric_date_pair(first, second, current_year, date_format)
            if extracted_date is not None:
                logger.debug(f"Extracted date {extracted_date.date()} from pattern M.D ({date_format}) in '{channel_name}'")
                return extracted_date

        # Pattern 4: M/D without year e.g., "10/27" or "15/04" — interpreted per date_format setting.
        # Lookahead excludes "/" (year follows, handled by Pattern 1) and ":" (time
        # range like "1/3:30pm" — second number is hours, not a day).
        pattern4 = re.search(r'\b(\d{1,2})/(\d{1,2})\b(?![/:])', channel_name)
        if pattern4:
            first, second = map(int, pattern4.groups())
            extracted_date = self._resolve_numeric_date_pair(first, second, current_year, date_format)
            if extracted_date is not None:
                logger.debug(f"Extracted date {extracted_date.date()} from pattern M/D ({date_format}) in '{channel_name}'")
                return extracted_date

        logger.debug(f"No date found in channel name: '{channel_name}'")
        return None


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

            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            extracted_day_name = day_names[extracted_day]
            today_day_name = day_names[today_day]

            if extracted_day != today_day:
                return True, f"[WrongDayOfWeek] Channel is for {extracted_day_name}, but today is {today_day_name}"

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
            # Check description length after separators (colon, pipe, or dash).
            # The `(?=\s)` lookahead after the colon excludes time-colons like "7:00AM"
            # so only real separator colons like "PPV 12: Title" are measured.
            colon_match = re.search(r':(?=\s)(.+)$', channel_name)
            if colon_match:
                description = colon_match.group(1).strip()
                if len(description) < 15:
                    return True, f"[ShortDescription] Description after colon too short ({len(description)} chars)"

            pipe_match = re.search(r'\|(.+)$', channel_name)
            if pipe_match:
                description = pipe_match.group(1).strip()
                if len(description) < 15:
                    return True, f"[ShortDescription] Description after pipe too short ({len(description)} chars)"

            # Match dash as separator (whitespace followed by dash)
            # Find the rightmost occurrence to get the actual description
            dash_match = re.search(r'\s-\s*(.*)$', channel_name)
            if dash_match:
                description = dash_match.group(1).strip()
                if len(description) < 15:
                    return True, f"[ShortDescription] Description after dash too short ({len(description)} chars)"

            return False, None
        
        elif rule_name == "ShortChannelName":
            # Check total name length if no separator (colon, pipe, or dash)
            # Normalize whitespace first to handle multiple spaces, tabs, etc.
            normalized_name = re.sub(r'\s+', ' ', channel_name.strip())

            # `(?=\s)` excludes time-colons (7:00, 9:45) so a channel like "LIVE 10:30"
            # is correctly seen as having NO real separator. Also requires content after
            # the colon so trailing-empty colons ("PPV 25:") still count as "no separator"
            # here — matching pre-fix behavior. [EmptyPlaceholder] catches those cases
            # earlier in the rule chain.
            colon_match = re.search(r':(?=\s)(.+)$', normalized_name)
            pipe_match = re.search(r'\|(.+)$', normalized_name)
            dash_match = re.search(r'\s-\s', normalized_name)  # Dash with surrounding spaces

            if not colon_match and not pipe_match and not dash_match:
                if len(normalized_name) < 25:
                    return True, f"[ShortChannelName] Name too short without event details ({len(normalized_name)} chars)"

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
            extracted_date = self._extract_date_from_channel_name(channel_name, logger, settings)
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
            now_adjusted = now_in_tz - timedelta(hours=grace_hours)
            today = now_adjusted.date()

            # Make extracted_date timezone-aware for correct comparison if it's naive
            if extracted_date.tzinfo is None:
                extracted_date = local_tz.localize(extracted_date)

            days_diff = (now_adjusted.date() - extracted_date.date()).days
            
            if days_diff > days_threshold:
                return True, f"[PastDate:{days_threshold}] Event date {extracted_date.strftime('%m/%d/%Y')} is {days_diff} days in the past (grace period: {grace_hours}h)"
            
            return False, None
        
        elif rule_name == "FutureDate":
            extracted_date = self._extract_date_from_channel_name(channel_name, logger, settings)
            if extracted_date is None:
                return False, None  # Skip rule if no date found
            
            days_threshold = rule_param if rule_param is not None else 14
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
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
            scheduled_times_str = settings.get("scheduled_times", "").strip()
            logger.info(f"Update Schedule - scheduled_times value: '{scheduled_times_str}'")

            self._save_settings(settings)
            self._start_background_scheduler(settings)
            
            if scheduled_times_str:
                times = self._parse_scheduled_times(scheduled_times_str)
                if times:
                    tz_str = self._get_system_timezone(settings)
                    time_list = [t.strftime('%H:%M') for t in times]
                    return {
                        "status": "success",
                        "message": f"Schedule updated successfully!\n\nScheduled to run daily at: {', '.join(time_list)} ({tz_str})\n\nBackground scheduler is running."
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
        
    def _parse_scheduled_times(self, scheduled_times_str):
        """Parse scheduled times string into list of datetime.time objects"""
        if not scheduled_times_str or not scheduled_times_str.strip():
            return []
        
        times = []
        for time_str in scheduled_times_str.split(','):
            time_str = time_str.strip()
            if len(time_str) == 4 and time_str.isdigit():
                hour = int(time_str[:2])
                minute = int(time_str[2:])
                if 0 <= hour < 24 and 0 <= minute < 60:
                    times.append(datetime.strptime(time_str, '%H%M').time())
        return times

    def _start_background_scheduler(self, settings):
        """Start background scheduler thread"""
        global _bg_thread, _scheduler_lock

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

        - When source TZ == display TZ, or either TZ is invalid/empty:
          returns DEFAULTS (plain templates) and writes
          `output_timezone=""` so any previously-saved value is cleared
          (the diff-and-save loop never deletes keys).
        - Otherwise: returns localized templates with the date placeholder
          driven by `date_format` (US/Auto -> {month}/{day};
          EU -> {day}/{month}) and a TZ abbreviation suffix computed for
          "now" in the display TZ. If %Z returns a numeric offset
          (e.g., +0530), the suffix is omitted but time conversion still
          happens via Dispatcharr's output_timezone.

        `fallback_title_template` is set in the base `managed_props` and
        is never overridden here.
        """
        DEFAULTS = {
            "output_timezone": "",
            "title_template": "{title}",
            "upcoming_title_template": "Upcoming at {starttime}: {title}",
            "ended_title_template": "Ended at {endtime}: {title}",
        }

        source_tz_name = str(settings.get("dummy_epg_event_timezone", "")).strip()
        display_tz_name = str(settings.get("timezone", "")).strip()

        if not source_tz_name or not display_tz_name or source_tz_name == display_tz_name:
            return DEFAULTS

        try:
            pytz.timezone(source_tz_name)  # validate only; renderer resolves source TZ itself
            display_tz = pytz.timezone(display_tz_name)
        except pytz.exceptions.UnknownTimeZoneError:
            return DEFAULTS

        abbrev = datetime.now(display_tz).strftime("%Z")
        suffix = f" {abbrev}" if abbrev and abbrev.isalpha() else ""

        fmt = str(settings.get("date_format", "Auto")).strip().upper()
        date_ph = "{day}/{month}" if fmt == "EU" else "{month}/{day}"

        return {
            "output_timezone": display_tz_name,
            "title_template": f"{{title}} {date_ph} {{starttime}}{suffix}",
            "upcoming_title_template": f"Upcoming at {date_ph} {{starttime}}{suffix}: {{title}}",
            "ended_title_template": f"Ended at {date_ph} {{endtime}}{suffix}: {{title}}",
        }

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
        # Regexes validated against these four real channel names:
        #   "PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)"  -> title="Cage Fury FC 153"
        #   "LIVE EVENT 01   9:45am Suslenkov v Mann"           -> title="Suslenkov v Mann"
        #   "PPV EVENT 25: OUTDOOR THEATRE Live From Coachella" -> title="OUTDOOR THEATRE Live From Coachella"
        #   "PPV02 | UFC 327: English Apr 14 4:30 PM"           -> title="UFC 327: English"
        # The title capture stops at the first of: " (", a time token, or a month-name token.
        # leading_time handles names where the time appears BEFORE the event text (LIVE format).
        managed_props = {
            "title_pattern": (
                r"(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+\s*[:|\s]\s*"
                r"(?:(?P<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
                r"(?P<title>.+?)"
                r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
                r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
            ),
            "time_pattern": r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[AaPp][Mm])",
            "date_pattern": r"\b(?P<month>\d{1,2})[./](?P<day>\d{1,2})(?:[./](?P<year>\d{2,4}))?\b",
            "title_template": "{title}",
            # Informative pre/post-event titles using Dispatcharr's
            # auto-computed {starttime}/{endtime} placeholders plus the
            # extracted {title}. Examples at render time:
            #   Upcoming at 8:00 PM: Cage Fury FC 153
            #   Ended at 11:00 PM: Cage Fury FC 153
            "upcoming_title_template": "Upcoming at {starttime}: {title}",
            "ended_title_template": "Ended at {endtime}: {title}",
            "fallback_title_template": "{channel_name}",
            "program_duration": duration_hours * 60,
            "timezone": tz_value,
            "include_date": False,
            "managed_by": "event-channel-managarr",
        }

        managed_props.update(self._localized_template_props(settings))

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

    def _attach_managed_epg(self, channels, managed_source, logger, rate_limiter=None):
        """Bind each channel in `channels` to the managed dummy source via an EPGData row.

        Only touches channels where epg_data IS NULL. Returns list of channel IDs that
        were attached (for result reporting).
        """
        from apps.epg.models import EPGData

        attached_ids = []
        channels_to_update = []

        # Wrap the entire get_or_create + bulk_update cycle in one transaction so a
        # bulk_update failure doesn't leave orphan EPGData rows pointing nowhere.
        with transaction.atomic():
            for channel in channels:
                if channel.epg_data_id is not None:
                    continue
                try:
                    epg_data, _ = EPGData.objects.get_or_create(
                        tvg_id=str(channel.uuid),
                        epg_source=managed_source,
                        defaults={"name": channel.name},
                    )
                    # Keep EPGData.name in sync with the channel name so {channel_name}
                    # in the dummy source's fallback template renders correctly.
                    if epg_data.name != channel.name:
                        epg_data.name = channel.name
                        epg_data.save(update_fields=["name"])
                except Exception as e:
                    logger.warning(f"{LOG_PREFIX} Failed to get_or_create EPGData for channel {channel.id}: {e}")
                    continue

                channel.epg_data = epg_data
                channels_to_update.append(channel)
                attached_ids.append(channel.id)

                if rate_limiter is not None:
                    rate_limiter.wait()

            if channels_to_update:
                Channel.objects.bulk_update(channels_to_update, ["epg_data"])
                logger.info(f"{LOG_PREFIX} Attached managed EPG to {len(channels_to_update)} channel(s)")
        return attached_ids

    def _detach_managed_epg(self, managed_source, keep_channel_ids, logger):
        """Set epg_data=None on any channel currently bound to the managed source
        whose id is NOT in keep_channel_ids. Returns list of detached channel IDs.
        """
        if managed_source is None:
            return []

        stale = list(Channel.objects.filter(
            epg_data__epg_source=managed_source
        ).exclude(id__in=keep_channel_ids))

        if not stale:
            return []

        for ch in stale:
            ch.epg_data = None

        with transaction.atomic():
            Channel.objects.bulk_update(stale, ["epg_data"])

        detached_ids = [ch.id for ch in stale]
        logger.info(f"{LOG_PREFIX} Detached managed EPG from {len(detached_ids)} channel(s)")
        return detached_ids

    def _run_managed_epg_pass(self, settings, logger, dry_run, enabled_channel_ids):
        """Attach/detach the plugin's managed dummy EPG based on current settings.

        If the master toggle is off, still runs the detach cleanup so turning the
        feature off reliably un-assigns managed EPG. Returns (attached_ids, detached_ids).

        Dry-run is a pure preview: it NEVER creates the EPGSource row and NEVER writes
        attach/detach changes. It only reports what an applied run would do.
        """
        from apps.epg.models import EPGSource

        toggle_on = self._get_bool_setting(settings, "manage_dummy_epg", False)

        if dry_run:
            # Pure preview — locate existing source only; do not create.
            managed_source = EPGSource.objects.filter(
                name="ECM Managed Dummy", source_type="dummy"
            ).first()
            if managed_source is None:
                return [], []
            if toggle_on:
                attached_ids = list(Channel.objects.filter(
                    id__in=enabled_channel_ids, epg_data__isnull=True
                ).values_list("id", flat=True))
                detached_ids = list(Channel.objects.filter(
                    epg_data__epg_source=managed_source
                ).exclude(id__in=enabled_channel_ids).values_list("id", flat=True))
            else:
                attached_ids = []
                detached_ids = list(Channel.objects.filter(
                    epg_data__epg_source=managed_source
                ).values_list("id", flat=True))
            logger.info(f"{LOG_PREFIX} [dry-run] Managed EPG would attach {len(attached_ids)}, detach {len(detached_ids)}")
            return attached_ids, detached_ids

        # Applied run — may create/refresh the source row.
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
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", self.DEFAULT_RATE_LIMITING))
            attached_ids = self._attach_managed_epg(no_epg_channels, managed_source, logger, rate_limiter=rate_limiter)

        keep_ids = set(enabled_channel_ids) if toggle_on else set()
        detached_ids = self._detach_managed_epg(managed_source, keep_ids, logger)

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

    def _scan_and_update_channels(self, settings, logger, dry_run=True, is_scheduled_run=False):
        """Scan channels and update visibility based on hide rules priority"""
        # Cross-worker serialization: one scan at a time across all uwsgi workers.
        # Covers manual Run Now / Dry Run as well as scheduled runs.
        lock_fd = None
        if fcntl:
            try:
                lock_fd = open(PluginConfig.SCAN_LOCK_FILE, 'w')
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, IOError):
                if lock_fd:
                    lock_fd.close()
                lock_fd = None
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
            
            # Apply group filter if specified
            channel_groups_str = settings.get("channel_groups", "").strip()
            if channel_groups_str:
                group_names = [g.strip() for g in channel_groups_str.split(',') if g.strip()]
                channels_query = channels_query.filter(channel_group__name__in=group_names)
                logger.info(f"Filtering to groups: {', '.join(group_names)}")
            
            channels = list(channels_query)
            total_channels = len(channels)
            
            if total_channels == 0:
                return {"status": "error", "message": f"No channels found in profile(s) '{', '.join(found_profile_names)}' with the specified groups."}
            
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
            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)
            # Capture once per scan so records and rule evaluations agree even if
            # the scan crosses local midnight.
            self._undated_today_str = datetime.now(local_tz).date().isoformat()
            today_str = self._undated_today_str
            tracked_this_scan = set()

            results = []
            channels_to_hide = []
            channels_to_show = []
            channels_ignored = []
            channels_for_duplicate_check = []

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
                        "managed_epg_assigned": False,
                        "managed_epg_detached": False,
                    })
                    rate_limiter.wait()
                    continue

                # Check if channel should be forced visible
                if regex_force_visible and regex_force_visible.search(channel_name):
                    if not current_visible:
                        channels_to_show.append(channel.id)

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
                        "managed_epg_assigned": False,
                        "managed_epg_detached": False,
                    })
                    rate_limiter.wait()
                    continue

                # Update undated-channel tracker: record channels with no extractable date,
                # drop those that now have a date.
                if self._extract_date_from_channel_name(channel_name, logger, settings) is None:
                    self._record_undated_channel(self._undated_tracker, channel.id, channel_name, today_str)
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
                    'has_epg': "Yes" if channel.epg_data else "No"
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
            enabled_channel_ids = [
                ch["channel_id"] for ch in channels_for_duplicate_check
                if (
                    (ch["current_visible"] and ch["channel_id"] not in channels_to_hide)
                    or ch["channel_id"] in channels_to_show
                ) and ch["channel_id"] not in duplicate_hide_list
            ]
            managed_attached_ids, managed_detached_ids = self._run_managed_epg_pass(
                settings, logger, dry_run, enabled_channel_ids
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
                    if cid in managed_attached_set:
                        row["managed_epg_assigned"] = True

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
                
                results.append({
                    "channel_id": channel_id,
                    "channel_name": channel_info['channel_name'],
                    "channel_number": channel_info['channel_number'],
                    "channel_group": channel_info['channel_group'],
                    "current_visibility": "Visible" if channel_info['current_visible'] else "Hidden",
                    "action": final_action,
                    "reason": reason,
                    "hide_rule": hide_rule,
                    "has_epg": channel_info['has_epg'],
                    "managed_epg_assigned": channel_id in managed_attached_set,
                    "managed_epg_detached": channel_id in managed_detached_set,
                })
            
            # Mark scan as complete
            progress.finish()

            total_duplicates_hidden = len(duplicate_hide_list)
            logger.info(f"Scan completed: {len(channels_to_hide)} to hide, {len(channels_to_show)} to show, {len(channels_ignored)} ignored, {total_duplicates_hidden} duplicates hidden")
            
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
                    "duplicate_strategy",
                    "keep_duplicates",
                    "auto_set_dummy_epg_on_hide",
                    "manage_dummy_epg",
                    "dummy_epg_event_duration_hours",
                    "dummy_epg_event_timezone",
                    "scheduled_times",
                    "enable_scheduled_csv_export",
                ]
                header_lines.append("Settings:")
                for k in settings_keys:
                    v = settings.get(k, "")
                    if v == "" or v is None:
                        v_str = "(empty)"
                    else:
                        v_str = str(v)
                    header_lines.append(f"  {k}: {v_str}")

                fieldnames = ['channel_id', 'channel_name', 'channel_number', 'channel_group',
                            'current_visibility', 'action', 'reason', 'hide_rule', 'has_epg',
                            'managed_epg_assigned', 'managed_epg_detached']
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
            
            message_parts = [
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
                    hidden_memberships = hidden_memberships.filter(channel__channel_group__name__in=group_names)
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
