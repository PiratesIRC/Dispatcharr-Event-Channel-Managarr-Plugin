
"""
Event Channel Managarr Plugin
Manages channel visibility based on EPG data and channel names
Automatically hides channels with no events and shows channels with events
"""

import logging
import json
import csv
import os
import re
import requests
import time
import threading
import pytz
import urllib.request
import urllib.error

from datetime import datetime, timedelta
from django.utils import timezone

# Django model imports
from apps.channels.models import Channel, ChannelProfileMembership, ChannelProfile
from apps.epg.models import ProgramData

# Setup logging using Dispatcharr's format
LOGGER = logging.getLogger("plugins.event_channel_managarr")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)

# Background scheduling globals
_bg_thread = None
_stop_event = threading.Event()

class Plugin:
    """Event Channel Managarr Plugin"""

    name = "Event Channel Managarr"
    version = "0.4.2"
    description = "Automatically manage channel visibility based on EPG data and channel names. Hides channels with no events and shows channels with active events.\n\nGitHub: https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin"

    @property
    def fields(self):
        """Dynamically generate fields list with version check"""
        # Check for updates from GitHub
        version_message = "Checking for updates..."
        try:
            # Check if we should perform a version check (once per day)
            if self._should_check_for_updates():
                # Perform the version check
                latest_version = self._get_latest_version("PiratesIRC", "Dispatcharr-Event-Channel-Managarr-Plugin")

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
            "id": "dispatcharr_url",
            "label": "🌐 Dispatcharr URL",
            "type": "string",
            "default": "",
            "placeholder": "http://192.168.1.10:9191",
            "help_text": "URL of your Dispatcharr instance (from your browser's address bar). This is required.",
        },

        {
            "id": "dispatcharr_username",
            "label": "👤 Dispatcharr Admin Username",
            "type": "string",
            "help_text": "Your admin username for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "dispatcharr_password",
            "label": "🔑 Dispatcharr Admin Password",
            "type": "string",
            "input_type": "password",
            "help_text": "Your admin password for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "timezone",
            "label": "🌍 Timezone",
            "type": "select",
            "default": "America/Chicago",
            "help_text": "Timezone for scheduled runs. Select the timezone for scheduling. Only one can be selected.",
            "options": [
                {"label": "America/New_York", "value": "America/New_York"},
                {"label": "America/Los_Angeles", "value": "America/Los_Angeles"},
                {"label": "America/Chicago", "value": "America/Chicago"},
                {"label": "Europe/London", "value": "Europe/London"},
                {"label": "Europe/Berlin", "value": "Europe/Berlin"},
                {"label": "Asia/Tokyo", "value": "Asia/Tokyo"},
                {"label": "Australia/Sydney", "value": "Australia/Sydney"}
            ]
        },
        {
            "id": "channel_profile_name",
            "label": "📺 Channel Profile Names (Required)",
            "type": "string",
            "default": "",
            "placeholder": "PPV Events Profile, Sports Profile",
            "help_text": "REQUIRED: Channel Profile(s) containing channels to monitor. Use comma-separated names for multiple profiles.",
        },
        {
            "id": "channel_groups",
            "label": "📂 Channel Groups (comma-separated)",
            "type": "string",
            "default": "",
            "placeholder": "PPV Events, Live Events",
            "help_text": "Specific channel groups to monitor within the profile. Leave blank to monitor all groups in the profile.",
        },
        {
            "id": "name_source",
            "label": "Name Source",
            "type": "select",
            "default": "Channel_Name",
            "help_text": "Select the source of the names to monitor. Only one can be selected.",
            "options": [
                {"label": "Channel Name", "value": "Channel_Name"},
                {"label": "Stream Name", "value": "Stream_Name"}
            ]
        },
        {
            "id": "hide_rules_priority",
            "label": "📜 Hide Rules Priority",
            "type": "string",
            "default": "[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[ShortDescription],[ShortChannelName]",
            "placeholder": "[BlankName],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[ShortDescription],[ShortChannelName]",
            "help_text": "Define rules for hiding channels in priority order (first match wins). Comma-separated tags. Available tags: [NoEPG], [BlankName], [WrongDayOfWeek], [NoEventPattern], [EmptyPlaceholder], [ShortDescription], [ShortChannelName], [NumberOnly], [PastDate:days], [PastDate:days:Xh], [FutureDate:days], [InactiveRegex]. Example: [PastDate:0] hides if event date has passed, [PastDate:0:4h] adds 4 hour grace period, [NumberOnly] hides channels with just prefix+number like 'PPV 12'.",
        },
        {
            "id": "regex_channels_to_ignore",
            "label": "🚫 Regex: Channel Names to Ignore",
            "type": "string",
            "default": "",
            "placeholder": "^BACKUP|^TEST",
            "help_text": "Regular expression to match channel names that should be skipped entirely. Matching channels will not be processed.",
        },
        {
            "id": "regex_mark_inactive",
            "label": "💤 Regex: Mark Channel as Inactive",
            "type": "string",
            "default": "",
            "placeholder": "PLACEHOLDER|TBD|COMING SOON",
            "help_text": "Regular expression to hide channels. This is processed as part of the [InactiveRegex] hide rule.",
        },
        {
            "id": "regex_force_visible",
            "label": "✅ Regex: Force Visible Channels",
            "type": "string",
            "default": "",
            "placeholder": "^NEWS|^WEATHER",
            "help_text": "Regular expression to match channel names that should ALWAYS be visible, overriding any hide rules.",
        },
        {
            "id": "duplicate_strategy",
            "label": "🎭 Duplicate Handling Strategy",
            "type": "select",
            "default": "lowest_number",
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
            "default": False,
            "help_text": "If enabled, duplicate channels will be kept visible instead of being hidden. The duplicate strategy above will be ignored.",
        },
        {
            "id": "past_date_grace_hours",
            "label": "📅 Past Date Grace Period (Hours)",
            "type": "string",
            "default": "4",
            "placeholder": "e.g., 6",
            "help_text": "Hours to wait after midnight before hiding past events. Useful for events that run late.",
        },
        {
            "id": "auto_set_dummy_epg_on_hide",
            "label": "🔌 Auto-Remove EPG on Hide",
            "type": "boolean",
            "default": True,
            "help_text": "If enabled, automatically removes EPG data from a channel when it is hidden by the plugin.",
        },
        {
            "id": "scheduled_times",
            "label": "⏰ Scheduled Run Times (24-hour format)",
            "type": "string",
            "default": "",
            "placeholder": "0600,1300,1800",
            "help_text": "Comma-separated times to run automatically each day (24-hour format). Example: 0600,1300,1800 runs at 6 AM, 1 PM, and 6 PM daily. Leave blank to disable scheduling.",
        },
            {
                "id": "enable_scheduled_csv_export",
                "label": "📄 Enable Scheduled CSV Export",
                "type": "boolean",
                "default": False,
                "help_text": "If enabled, a CSV file of the scan results will be created when the plugin runs on a schedule. If disabled, no CSV will be created for scheduled runs.",
            },
        ]

        return fields_list
    
    # Actions for Dispatcharr UI
    actions = [
        {
            "id": "validate_configuration",
            "label": "✅ Validate Configuration",
            "description": "Test and validate all plugin settings (regex patterns, rules, API connectivity)",
            "confirm": { "required": False }
        },
        {
            "id": "update_schedule",
            "label": "💾 Update Schedule",
            "description": "Save settings and update the scheduled run times. Use this after changing any settings.",
        },
        {
            "id": "dry_run",
            "label": "🧪 Dry Run (Export to CSV)",
            "description": "Preview which channels would be hidden/shown without making changes. Results exported to CSV.",
        },
        {
            "id": "run_now",
            "label": "🚀 Run Now",
            "description": "Immediately scan and update channel visibility based on current EPG data",
            "confirm": { "required": True, "title": "Run Channel Visibility Update?", "message": "This will hide channels without events and show channels with events. Continue?" }
        },
        {
            "id": "remove_epg_from_hidden",
            "label": "🗑️ Remove EPG from Hidden Channels",
            "description": "Remove all EPG data from channels that are disabled/hidden in the selected profile. Results exported to CSV.",
            "confirm": { "required": True, "title": "Remove EPG Data?", "message": "This will permanently delete all EPG data for channels that are currently hidden/disabled in the selected profile. This action cannot be undone. Continue?" }
        },
        {
            "id": "clear_csv_exports",
            "label": "✨ Clear CSV Exports",
            "description": "Delete all CSV export files created by this plugin",
            "confirm": { "required": True, "title": "Delete All CSV Exports?", "message": "This will permanently delete all CSV files created by Event Channel Managarr. This action cannot be undone. Continue?" }
        },
        {
            "id": "cleanup_periodic_tasks",
            "label": "🧹 Cleanup Orphaned Tasks",
            "description": "Remove any orphaned Celery periodic tasks from old plugin versions",
            "confirm": { "required": True, "title": "Cleanup Orphaned Tasks?", "message": "This will remove any old Celery Beat tasks created by previous versions of this plugin. Continue?" }
        },
    ]
    
    def __init__(self):
        self.results_file = "/data/event_channel_managarr_results.json"
        self.settings_file = "/data/event_channel_managarr_settings.json"
        self.version_check_file = "/data/event_channel_managarr_version_check.json"
        self.last_results = []
        self.scan_progress = {"current": 0, "total": 0, "status": "idle", "start_time": None}

        # Version check cache
        self.cached_version_info = None

        # API token cache
        self.cached_api_token = None
        self.token_cache_time = None
        self.token_cache_duration = 1800  # 30 minutes in seconds

        LOGGER.info(f"{self.name} Plugin v{self.version} initialized")

        # Load saved settings and create scheduled tasks
        self._load_settings()
  
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

                        if time_diff.total_seconds() < 86400:  # 24 hours in seconds
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

            # Params may contain action-specific overrides
            if params:
                merged_settings.update(params)

            if action == "load_settings":
                return self.load_settings_action(merged_settings, logger)
            elif action == "update_schedule":
                return self.update_schedule_action(merged_settings, logger)
            elif action == "dry_run":
                return self.dry_run_action(merged_settings, logger)
            elif action == "run_now":
                return self.run_now_action(merged_settings, logger)
            elif action == "remove_epg_from_hidden":
                return self.remove_epg_from_hidden_action(merged_settings, logger)
            elif action == "clear_csv_exports":
                return self.clear_csv_exports_action(merged_settings, logger)
            elif action == "cleanup_periodic_tasks":
                return self.cleanup_periodic_tasks_action(merged_settings, logger)
            elif action == "validate_configuration":
                return self.validate_configuration_action(merged_settings, logger)
            else:
                return {
                    "status": "error",
                    "message": f"Unknown action: {action}",
                    "available_actions": ["validate_configuration", "update_schedule", "dry_run", "run_now", "remove_epg_from_hidden", "clear_csv_exports", "cleanup_periodic_tasks"]
                }
                
        except Exception as e:
            self.scan_progress['status'] = 'idle'
            LOGGER.error(f"Error in plugin run: {str(e)}")
            return {"status": "error", "message": str(e)}

    def validate_configuration_action(self, settings, logger):
        """Validate all plugin configuration settings"""
        validation_results = []
        has_errors = False

        # 1. Validate hide rules
        try:
            hide_rules_text = settings.get("hide_rules_priority", "").strip()
            hide_rules = self._parse_hide_rules(hide_rules_text, logger)
            if hide_rules:
                validation_results.append(f"✅ Hide Rules: Parsed {len(hide_rules)} rules successfully")
            else:
                validation_results.append("⚠️ Hide Rules: No rules configured (will use defaults)")
        except Exception as e:
            validation_results.append(f"❌ Hide Rules: Parse error - {str(e)}")
            has_errors = True

        # 2. Validate regex patterns
        patterns_to_check = [
            ("regex_mark_inactive", "Inactive Regex"),
            ("regex_channels_to_ignore", "Ignore Channels Regex"),
            ("regex_force_visible", "Force Visible Regex")
        ]

        for setting_key, label in patterns_to_check:
            try:
                pattern = settings.get(setting_key, "").strip()
                if pattern:
                    re.compile(pattern, re.IGNORECASE)
                    validation_results.append(f"✅ {label}: Valid pattern")
                else:
                    validation_results.append(f"ℹ️ {label}: Not configured")
            except re.error as e:
                validation_results.append(f"❌ {label}: Invalid - {str(e)}")
                has_errors = True

        # 3. Validate API connectivity
        try:
            token, error = self._get_api_token(settings, logger)
            if error:
                validation_results.append(f"❌ API Authentication: {error}")
                has_errors = True
            else:
                validation_results.append("✅ API Authentication: Success")
        except Exception as e:
            validation_results.append(f"❌ API Connection: {str(e)}")
            has_errors = True

        # 4. Validate schedule
        scheduled_times = settings.get("scheduled_times", "").strip()
        if scheduled_times:
            times_list = [t.strip() for t in scheduled_times.split(',') if t.strip()]
            invalid = [t for t in times_list if len(t) != 4 or not t.isdigit()]
            if invalid:
                validation_results.append(f"❌ Scheduled Times: Invalid format - {', '.join(invalid)}")
                has_errors = True
            else:
                validation_results.append(f"✅ Scheduled Times: {len(times_list)} valid times")
        else:
            validation_results.append("ℹ️ Scheduled Times: Not configured")

        message = "\n".join(validation_results)
        return {
            "status": "warning" if has_errors else "success",
            "message": f"Configuration Validation:\n\n{message}"
        }

    def _save_settings(self, settings):
        """Save settings to disk"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            self.saved_settings = settings
            LOGGER.info("Settings saved successfully")
        except Exception as e:
            LOGGER.error(f"Error saving settings: {e}")

    def _parse_hide_rules(self, rules_text, logger):
        """Parse hide rules priority text into list of rule tuples"""
        if not rules_text or not rules_text.strip():
            # Return default rules if none specified
            default_rules = "[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[ShortDescription],[ShortChannelName]"
            rules_text = default_rules
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
            'SUN': 6
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

    def _extract_date_from_channel_name(self, channel_name, logger):
        """Extract date from channel name using various patterns, including hour if present"""
        if not channel_name:
            return None

        import pytz
        from dateutil import parser as dateutil_parser

        current_year = datetime.now().year
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Pattern 0: start:YYYY-MM-DD HH:MM:SS or stop:YYYY-MM-DD HH:MM:SS
        for prefix in ["start:", "stop:"]:
            pattern0 = re.search(rf'{prefix}(\d{{4}})-(\d{{2}})-(\d{{2}})\s+(\d{{2}}):(\d{{2}}):(\d{{2}})', channel_name)
            if pattern0:
                year, month, day, hour, minute, second = map(int, pattern0.groups())
                try:
                    extracted_date = datetime(year, month, day, hour, minute, second)
                    logger.debug(f"Extracted datetime {extracted_date} from pattern {prefix}YYYY-MM-DD HH:MM:SS in '{channel_name}'")
                    return extracted_date
                except ValueError:
                    pass

        # Pattern 0a: (YYYY-MM-DD HH:MM:SS) in parentheses
        pattern0a = re.search(r'\((\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\)', channel_name)
        if pattern0a:
            year, month, day, hour, minute, second = map(int, pattern0a.groups())
            try:
                extracted_date = datetime(year, month, day, hour, minute, second)
                logger.debug(f"Extracted datetime {extracted_date} from pattern (YYYY-MM-DD HH:MM:SS) in '{channel_name}'")
                return extracted_date
            except ValueError:
                pass

        # Pattern 1: MM/DD/YYYY or MM/DD/YY
        pattern1 = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', channel_name)
        if pattern1:
            month, day, year = map(int, pattern1.groups())
            if year < 100:
                year += 2000
            try:
                extracted_date = datetime(year, month, day)
                logger.debug(f"Extracted date {extracted_date.date()} from pattern MM/DD/YYYY in '{channel_name}'")
                return extracted_date
            except ValueError:
                pass

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

        # Pattern 3: MM.DD e.g., "10.25"
        pattern3 = re.search(r'\b(\d{1,2})\.(\d{1,2})\b', channel_name)
        if pattern3:
            month, day = map(int, pattern3.groups())
            try:
                extracted_date = datetime(current_year, month, day)
                logger.debug(f"Extracted date {extracted_date.date()} from pattern MM.DD in '{channel_name}'")
                return extracted_date
            except ValueError:
                pass

        # Pattern 4: MM/DD without year e.g., "10/27"
        pattern4 = re.search(r'\b(\d{1,2})/(\d{1,2})\b(?!/)', channel_name)
        if pattern4:
            month, day = map(int, pattern4.groups())
            try:
                extracted_date = datetime(current_year, month, day)
                logger.debug(f"Extracted date {extracted_date.date()} from pattern MM/DD in '{channel_name}'")
                return extracted_date
            except ValueError:
                pass

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

            # Get today's day of week (0 = Monday, 6 = Sunday)
            today_day = datetime.now().weekday()

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
            # Ends with colon, pipe, or dash with nothing or only whitespace/very short content after
            colon_match = re.search(r':(.*)$', channel_name)
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
            # Check description length after separators (colon, pipe, or dash)
            colon_match = re.search(r':(.+)$', channel_name)
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

            colon_match = re.search(r':(.+)$', normalized_name)
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
            extracted_date = self._extract_date_from_channel_name(channel_name, logger)
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
                local_tz = pytz.timezone('America/Chicago')

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
            extracted_date = self._extract_date_from_channel_name(channel_name, logger)
            if extracted_date is None:
                return False, None  # Skip rule if no date found
            
            days_threshold = rule_param if rule_param is not None else 14
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            days_diff = (extracted_date - today).days
            
            if days_diff > days_threshold:
                return True, f"[FutureDate:{days_threshold}] Event date {extracted_date.strftime('%m/%d/%Y')} is {days_diff} days in the future"
            
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
            export_dir = "/data/exports"
            
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
            LOGGER.info(f"Using user-specified timezone: {user_tz}")
            return user_tz
        
        # Otherwise use America/Chicago as default
        LOGGER.info("Using default timezone: America/Chicago")
        return "America/Chicago"
        
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
        global _bg_thread
        
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

            # Get timezone from settings
            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                LOGGER.error(f"Unknown timezone: {tz_str}, falling back to America/Chicago")
                local_tz = pytz.timezone('America/Chicago')

            # Initialize last_run_date to current date to prevent immediate execution
            # when scheduler starts at a time that matches a scheduled time
            last_run_date = None

            LOGGER.info(f"Scheduler timezone: {tz_str}")
            LOGGER.info(f"Scheduler initialized - will run at next scheduled time (not immediately)")
            
            while not _stop_event.is_set():
                try:
                    now = datetime.now(local_tz)
                    current_date = now.date()
                    
                    # Check each scheduled time
                    for scheduled_time in scheduled_times:
                        # Create a datetime for the scheduled time today in the local timezone
                        scheduled_dt = local_tz.localize(datetime.combine(current_date, scheduled_time))
                        time_diff = (scheduled_dt - now).total_seconds()
                        
                        # Run if within 30 seconds and have not run today
                        if -30 <= time_diff <= 30 and last_run_date != current_date:
                            LOGGER.info(f"Scheduled scan triggered at {now.strftime('%Y-%m-%d %H:%M %Z')}")
                            try:
                                result = self._scan_and_update_channels(settings, LOGGER, dry_run=False, is_scheduled_run=True)
                                LOGGER.info(f"Scheduled scan completed: {result.get('message', 'Done')}")

                                # Trigger frontend refresh if changes were made
                                if result.get("status") == "success":
                                    results_data = result.get("results", {})
                                    if results_data.get("to_hide", 0) > 0 or results_data.get("to_show", 0) > 0:
                                        self._trigger_frontend_refresh(settings, LOGGER)
                            except Exception as e:
                                LOGGER.error(f"Error in scheduled scan: {e}")
                            last_run_date = current_date
                            break
                    
                    # Sleep for 30 seconds
                    _stop_event.wait(30)
                    
                except Exception as e:
                    LOGGER.error(f"Error in scheduler loop: {e}")
                    _stop_event.wait(60)
        
        _bg_thread = threading.Thread(target=scheduler_loop, name="event-channel-managarr-scheduler", daemon=True)
        _bg_thread.start()
        LOGGER.info(f"Background scheduler started for times: {[t.strftime('%H:%M') for t in scheduled_times]}")

    def _stop_background_scheduler(self):
        """Stop background scheduler thread"""
        global _bg_thread
        if _bg_thread and _bg_thread.is_alive():
            LOGGER.info("Stopping background scheduler")
            _stop_event.set()
            _bg_thread.join(timeout=5)
            _stop_event.clear()
            LOGGER.info("Background scheduler stopped")

    def _get_api_token(self, settings, logger):
        """Get an API access token using username and password."""
        import time

        # Check if we have a valid cached token
        if self.cached_api_token and self.token_cache_time:
            elapsed_time = time.time() - self.token_cache_time
            if elapsed_time < self.token_cache_duration:
                logger.info("Successfully obtained API access via CACHED token")
                return self.cached_api_token, None
            else:
                logger.info("Cached API token expired, requesting new token")

        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        username = settings.get("dispatcharr_username", "")
        password = settings.get("dispatcharr_password", "")

        if not all([dispatcharr_url, username, password]):
            return None, "Dispatcharr URL, Username, and Password must be configured in the plugin settings."

        try:
            url = f"{dispatcharr_url}/api/accounts/token/"
            payload = {"username": username, "password": password}

            logger.info(f"Attempting to authenticate with Dispatcharr at: {url}")
            response = requests.post(url, json=payload, timeout=15)

            if response.status_code == 401:
                logger.error("Authentication failed - invalid credentials")
                return None, "Authentication failed. Please check your username and password in the plugin settings."
            elif response.status_code == 404:
                logger.error(f"API endpoint not found - check Dispatcharr URL: {dispatcharr_url}")
                return None, f"API endpoint not found. Please verify your Dispatcharr URL: {dispatcharr_url}"
            elif response.status_code >= 500:
                logger.error(f"Server error from Dispatcharr: {response.status_code}")
                return None, f"Dispatcharr server error ({response.status_code}). Please check if Dispatcharr is running properly."
            
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access")

            if not access_token:
                logger.error("No access token returned from API")
                return None, "Login successful, but no access token was returned by the API."

            # Cache the token
            import time
            self.cached_api_token = access_token
            self.token_cache_time = time.time()

            logger.info("Successfully obtained new API access token (cached for 30 minutes)")
            return access_token, None
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error: {e}")
            return None, f"Unable to connect to Dispatcharr at {dispatcharr_url}. Please check the URL and ensure Dispatcharr is running."
        except requests.exceptions.Timeout as e:
            logger.error(f"Request timeout: {e}")
            return None, "Request timed out while connecting to Dispatcharr. Please check your network connection."
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            return None, f"Network error occurred while authenticating: {e}"
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}")
            return None, "Invalid response from Dispatcharr API. Please check if the URL is correct."
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            return None, f"Unexpected error during authentication: {e}"

    def _get_api_data(self, endpoint, token, settings, logger):
        """Helper to perform GET requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

        try:
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 401:
                logger.error("API token expired or invalid")
                # Invalidate cached token
                self.cached_api_token = None
                self.token_cache_time = None
                raise Exception("API authentication failed. Token may have expired.")
            elif response.status_code == 403:
                logger.error("API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")
            
            response.raise_for_status()
            
            if not response.text or response.text.strip() == '':
                logger.warning(f"Empty response from {endpoint}, returning empty list")
                return []
            
            try:
                json_data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from {endpoint}: {e}")
                logger.error(f"Response text: {response.text}")
                raise Exception(f"Invalid JSON response from {endpoint}: {e}")
            
            if isinstance(json_data, dict):
                return json_data.get('results', json_data)
            elif isinstance(json_data, list):
                return json_data
            return []
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed for {endpoint}: {e}")
            raise Exception(f"API request failed: {e}")

    def _patch_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform PATCH requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

        try:
            logger.info(f"Making API PATCH request to: {endpoint}")
            response = requests.patch(url, headers=headers, json=payload, timeout=60)

            if response.status_code == 401:
                logger.error("API token expired or invalid")
                # Invalidate cached token
                self.cached_api_token = None
                self.token_cache_time = None
                raise Exception("API authentication failed. Token may have expired.")
            elif response.status_code == 403:
                logger.error("API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API PATCH request failed for {endpoint}: {e}")
            raise Exception(f"API PATCH request failed: {e}")

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
                 logger.info(f"Found {len(channels)} duplicate channels for '{normalized_name} | {event_description}'")
            else:
                 logger.info(f"Found {len(channels)} duplicate channels for base name '{normalized_name}' (no event desc)")
            
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
            
            logger.info(f"Keeping channel {channel_to_keep['id']} (#{channel_to_keep['number']}): {channel_to_keep['name']}")
            
            # Mark the rest for hiding
            for dup in channels_to_hide_in_group:
                logger.info(f"Marking duplicate for hiding: {dup['id']} (#{dup['number']}): {dup['name']}")
                duplicate_hide_list.append(dup['id'])
                
                # Remove from show list if it was going to be shown
                if dup['id'] in channels_to_show:
                    channels_to_show.remove(dup['id'])
                
                # Add to hide list if not already there
                if dup['id'] not in channels_to_hide:
                    channels_to_hide.append(dup['id'])
        
        return duplicate_hide_list

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
            

            
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            # Get Channel Profiles
            logger.info(f"Fetching Channel Profile(s): {', '.join(channel_profile_names)}")
            profiles = self._get_api_data("/api/channels/profiles/", token, settings, logger)
            
            # Find all matching profile IDs
            profile_ids = []
            found_profile_names = []
            for profile_name in channel_profile_names:
                profile_id = None
                for profile in profiles:
                    if profile.get('name', '').strip().upper() == profile_name.upper():
                        profile_id = profile.get('id')
                        found_profile_names.append(profile_name)
                        break
                
                if profile_id:
                    profile_ids.append(profile_id)
                else:
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
            
            # Initialize progress
            self.scan_progress = {"current": 0, "total": total_channels, "status": "running", "start_time": time.time()}
            
            results = []
            channels_to_hide = []
            channels_to_show = []
            channels_ignored = []
            channels_for_duplicate_check = []
            
            # Track channel info for enhanced logging
            channel_info_map = {}
            
            # Process each channel
            for i, channel in enumerate(channels):

                self.scan_progress["current"] = i + 1
                
                channel_name = self._get_effective_name(channel, settings, logger)
                current_visible = self._get_channel_visibility(channel.id, profile_ids, logger)
                
                logger.debug(f"Processing channel {channel.id} using name '{channel_name}' (source={settings.get('name_source', 'Channel_Name')})")

                # Check if channel should be ignored
                if regex_ignore and regex_ignore.search(channel_name):
                    channels_ignored.append(channel.id)
                    results.append({
                        "channel_id": channel.id,
                        "channel_name": channel_name,
                        "channel_number": float(channel.channel_number) if channel.channel_number else None,
                        "channel_group": channel.channel_group.name if channel.channel_group else "No Group",
                        "current_visibility": "Visible" if current_visible else "Hidden",
                        "action": "Ignored",
                        "reason": "Matches ignore regex",
                        "hide_rule": "",
                        "has_epg": "Yes" if channel.epg_data else "No"
                    })
                    continue

                # Check if channel should be forced visible
                if regex_force_visible and regex_force_visible.search(channel_name):
                    if not current_visible:
                        channels_to_show.append(channel.id)
                    
                    results.append({
                        "channel_id": channel.id,
                        "channel_name": channel_name,
                        "channel_number": float(channel.channel_number) if channel.channel_number else None,
                        "channel_group": channel.channel_group.name if channel.channel_group else "No Group",
                        "current_visibility": "Visible" if current_visible else "Hidden",
                        "action": "Forced Visible" if not current_visible else "Visible (Forced)",
                        "reason": "Matches force visible regex",
                        "hide_rule": "[ForceVisible]",
                        "has_epg": "Yes" if channel.epg_data else "No"
                    })
                    continue
                
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
                keep_duplicates=settings.get("keep_duplicates", False)
            )
            
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
                
                logger.info(f"Decision for Channel {channel_id} ('{channel_info['channel_name']}'): Action={final_action}, Reason='{reason}'")

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
                    "has_epg": channel_info['has_epg']
                })
            
            # Mark scan as complete
            self.scan_progress['status'] = 'idle'
            
            total_duplicates_hidden = len(duplicate_hide_list)
            logger.info(f"Scan completed: {len(channels_to_hide)} to hide, {len(channels_to_show)} to show, {len(channels_ignored)} ignored, {total_duplicates_hidden} duplicates hidden")
            
            # Export to CSV
            csv_filepath = None
            should_create_csv = False
            if is_scheduled_run:
                should_create_csv = settings.get("enable_scheduled_csv_export", False)
            else:
                # For manual runs (Dry Run, Run Now), always create the CSV
                should_create_csv = True

            if should_create_csv:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_filename = f"event_channel_managarr_{'dryrun' if dry_run else 'applied'}_{timestamp}.csv"
                csv_filepath = os.path.join("/data/exports", csv_filename)
                os.makedirs("/data/exports", exist_ok=True)

                # Calculate statistics by rule
                rule_stats = {}
                for result in results:
                    rule = result.get('hide_rule', 'N/A')
                    if result.get('action') == 'Hide':
                        rule_stats[rule] = rule_stats.get(rule, 0) + 1

                with open(csv_filepath, 'w', newline='', encoding='utf-8') as csvfile:
                    # Write version header as first line
                    csvfile.write(f"# Event Channel Managarr v{self.version} - {'Dry Run' if dry_run else 'Applied'} - {timestamp}\n")

                    # Write statistics
                    csvfile.write(f"# Total Channels Processed: {len(results)}\n")
                    csvfile.write(f"# Channels to Hide: {len(channels_to_hide)}\n")
                    csvfile.write(f"# Channels to Show: {len(channels_to_show)}\n")
                    csvfile.write(f"# Channels Ignored: {len(channels_ignored)}\n")
                    csvfile.write(f"# Duplicates Hidden: {total_duplicates_hidden}\n")

                    # Write rule effectiveness stats
                    if rule_stats:
                        csvfile.write("# Rule Effectiveness:\n")
                        for rule, count in sorted(rule_stats.items(), key=lambda x: x[1], reverse=True):
                            csvfile.write(f"#   {rule}: {count} channels\n")

                    # Write hide rules priority configuration
                    csvfile.write(f"# Hide Rules Priority: {hide_rules_text_for_export}\n")
                    csvfile.write("#\n")

                    fieldnames = ['channel_id', 'channel_name', 'channel_number', 'channel_group',
                                'current_visibility', 'action', 'reason', 'hide_rule', 'has_epg']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    for result in results:
                        writer.writerow(result)

                logger.info(f"Results exported to {csv_filepath}")
            
            # Apply changes if not dry run
            if not dry_run and (channels_to_hide or channels_to_show):
                # Build bulk update payload
                channels_payload = []
                
                # Log channels being hidden with reasons
                for channel_id in channels_to_hide:
                    channels_payload.append({'channel_id': channel_id, 'enabled': False})
                    if channel_id in channel_info_map:
                        info = channel_info_map[channel_id]
                        # Check if this is a duplicate
                        if channel_id in duplicate_hide_list:
                            reason = "Duplicate channel (keeping better match)"
                        else:
                            reason = info['reason']
                        logger.debug(f"Hiding channel {channel_id} (#{info['channel_number']}) '{info['channel_name']}' - Reason: {reason}")
                
                # Log channels being shown with reasons
                for channel_id in channels_to_show:
                    channels_payload.append({'channel_id': channel_id, 'enabled': True})
                    if channel_id in channel_info_map:
                        info = channel_info_map[channel_id]
                        logger.debug(f"Showing channel {channel_id} (#{info['channel_number']}) '{info['channel_name']}' - Reason: {info['reason']}")
                
                if channels_payload:
                    logger.info(f"Applying visibility changes to {len(channels_payload)} channels across {len(profile_ids)} profile(s)...")
                    payload = {'channels': channels_payload}
                    
                    # Apply changes to each profile
                    for profile_id in profile_ids:
                        self._patch_api_data(f"/api/channels/profiles/{profile_id}/channels/bulk-update/", 
                                            token, payload, settings, logger)
                    
                    logger.info("Visibility changes applied successfully to all profiles")

            # Handle automatic EPG removal if enabled
            if not dry_run and settings.get("auto_set_dummy_epg_on_hide", False) and channels_to_hide:
                logger.info(f"Automatically removing EPG data from {len(channels_to_hide)} hidden channels...")
                channels_to_update_epg = Channel.objects.filter(id__in=channels_to_hide)
                updated_count = 0
                for channel in channels_to_update_epg:
                    if channel.epg_data is not None:
                        channel.epg_data = None
                        channel.save()
                        updated_count += 1
                if updated_count > 0:
                    logger.info(f"EPG removed from {updated_count} channels.")
                    # Trigger a frontend refresh to ensure EPG changes are reflected
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
                    "csv_file": csv_filepath if csv_filepath else "N/A"
                }
            }
            
        except Exception as e:
            logger.error(f"Error scanning channels: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error scanning channels: {str(e)}"}

    def dry_run_action(self, settings, logger):
        """Preview channel visibility changes without applying them"""
        logger.info("Starting dry run scan...")
        result = self._scan_and_update_channels(settings, logger, dry_run=True)
        
        return result

    def run_now_action(self, settings, logger):
        """Immediately scan and update channel visibility"""
        logger.info("Starting channel visibility update...")
        result = self._scan_and_update_channels(settings, logger, dry_run=False)
        
        # Trigger frontend refresh if changes were applied
        if result.get("status") == "success":
            results_data = result.get("results", {})
            if results_data.get("to_hide", 0) > 0 or results_data.get("to_show", 0) > 0:
                self._trigger_frontend_refresh(settings, logger)
        
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
            channels_set_to_dummy = 0
            
            for membership in hidden_memberships:
                channel = membership.channel
                channel_id = channel.id
                channel_name = self._get_effective_name(channel, settings, logger) or 'Unknown'
                channel_number = channel.channel_number or 'N/A'
                
                # Query EPG data for this channel
                epg_count = 0
                deleted_count = 0
                had_epg = False
                
                if channel.epg_data:
                    had_epg = True
                    epg_count = ProgramData.objects.filter(epg=channel.epg_data).count()
                    
                    if epg_count > 0:
                        # Delete all EPG data for this channel
                        deleted_count = ProgramData.objects.filter(epg=channel.epg_data).delete()[0]
                        total_epg_removed += deleted_count
                        logger.info(f"Removed {deleted_count} EPG entries from channel {channel_number} - {channel_name}")
                    
                    # Set channel EPG to null (dummy EPG)
                    channel.epg_data = None
                    channel.save()
                    channels_set_to_dummy += 1
                    logger.info(f"Set channel {channel_number} - {channel_name} to dummy EPG")
                    
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
            
            # Export results to CSV
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_filename = f"epg_removal_{timestamp}.csv"
            csv_filepath = f"/data/exports/{csv_filename}"
            
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(csv_filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['channel_id', 'channel_name', 'channel_number', 'epg_entries_removed', 'status']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for result in results:
                    writer.writerow(result)
            
            logger.info(f"EPG removal results exported to {csv_filepath}")
            
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
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            if channel_layer:
                # Send WebSocket message to trigger frontend refresh
                async_to_sync(channel_layer.group_send)(
                    "dispatcharr_updates",
                    {
                        "type": "channels.updated",
                        "message": "Channel visibility updated by Event Channel Managarr"
                    }
                )
                logger.info("Frontend refresh triggered via WebSocket")
                return True
        except Exception as e:
            logger.warning(f"Could not trigger frontend refresh: {e}")
        return False


# Export for Dispatcharr plugin system

# Create the single plugin instance
plugin = Plugin()

# Export the components the loader needs
# Note: fields is now a property on the instance, not the class
fields = plugin.fields
actions = Plugin.actions

# Define what this module exports

__all__ = ['plugin', 'fields', 'actions']

