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
    version = "0.2.1"
    description = "Automatically manage channel visibility based on EPG data and channel names. Hides channels with no events and shows channels with active events."
    
    # Settings rendered by UI
    fields = [
        {
            "id": "dispatcharr_url",
            "label": "Dispatcharr URL",
            "type": "string",
            "default": "",
            "placeholder": "http://192.168.1.10:9191",
            "help_text": "URL of your Dispatcharr instance (from your browser's address bar). Example: http://127.0.0.1:9191",
        },
        {
            "id": "dispatcharr_username",
            "label": "Dispatcharr Admin Username",
            "type": "string",
            "help_text": "Your admin username for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "dispatcharr_password",
            "label": "Dispatcharr Admin Password",
            "type": "string",
            "input_type": "password",
            "help_text": "Your admin password for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "timezone",
            "label": "Timezone",
            "type": "string",
            "default": "America/Chicago",
            "placeholder": "America/Chicago",
            "help_text": "Timezone for scheduled runs. Examples: America/New_York, America/Los_Angeles, Europe/London. Leave as America/Chicago for Central Time.",
        },
        {
            "id": "channel_profile_name",
            "label": "Channel Profile Name (Required)",
            "type": "string",
            "default": "",
            "placeholder": "PPV Events Profile",
            "help_text": "REQUIRED: The Channel Profile containing channels to monitor. Only channels in this profile will be processed.",
        },
        {
            "id": "channel_groups",
            "label": "Channel Groups (comma-separated)",
            "type": "string",
            "default": "",
            "placeholder": "PPV Events, Live Events",
            "help_text": "Specific channel groups to monitor within the profile. Leave blank to monitor all groups in the profile.",
        },
        {
            "id": "regex_channels_to_ignore",
            "label": "Regex: Channel Names to Ignore",
            "type": "string",
            "default": "",
            "placeholder": "^BACKUP|^TEST",
            "help_text": "Regular expression to match channel names that should be skipped entirely. Matching channels will not be processed.",
        },
        {
            "id": "regex_mark_inactive",
            "label": "Regex: Mark Channel as Inactive",
            "type": "string",
            "default": "",
            "placeholder": "PLACEHOLDER|TBD|COMING SOON",
            "help_text": "Regular expression to identify inactive channel names (in addition to blank/no event checks). Matching channels will be hidden.",
        },
        {
            "id": "scheduled_times",
            "label": "Scheduled Run Times (24-hour format)",
            "type": "string",
            "default": "",
            "placeholder": "0600,1300,1800",
            "help_text": "Comma-separated times to run automatically each day (24-hour format). Example: 0600,1300,1800 runs at 6 AM, 1 PM, and 6 PM daily. Leave blank to disable scheduling.",
        },
    ]
    
    # Actions for Dispatcharr UI
    actions = [
        {
            "id": "update_schedule",
            "label": "Update Schedule",
            "description": "Save settings and update the scheduled run times. Use this after changing any settings.",
        },
        {
            "id": "dry_run",
            "label": "Dry Run (Export to CSV)",
            "description": "Preview which channels would be hidden/shown without making changes. Results exported to CSV.",
        },
        {
            "id": "run_now",
            "label": "Run Now",
            "description": "Immediately scan and update channel visibility based on current EPG data",
            "confirm": { "required": True, "title": "Run Channel Visibility Update?", "message": "This will hide channels without events and show channels with events. Continue?" }
        },
        {
            "id": "remove_epg_from_hidden",
            "label": "Remove EPG from Hidden Channels",
            "description": "Remove all EPG data from channels that are disabled/hidden in the selected profile. Results exported to CSV.",
            "confirm": { "required": True, "title": "Remove EPG Data?", "message": "This will permanently delete all EPG data for channels that are currently hidden/disabled in the selected profile. This action cannot be undone. Continue?" }
        },
        {
            "id": "clear_csv_exports",
            "label": "Clear CSV Exports",
            "description": "Delete all CSV export files created by this plugin",
            "confirm": { "required": True, "title": "Delete All CSV Exports?", "message": "This will permanently delete all CSV files created by Event Channel Managarr. This action cannot be undone. Continue?" }
        },
        {
            "id": "cleanup_periodic_tasks",
            "label": "Cleanup Orphaned Tasks",
            "description": "Remove any orphaned Celery periodic tasks from old plugin versions",
            "confirm": { "required": True, "title": "Cleanup Orphaned Tasks?", "message": "This will remove any old Celery Beat tasks created by previous versions of this plugin. Continue?" }
        },
    ]
    
    def __init__(self):
        self.results_file = "/data/event_channel_managarr_results.json"
        self.settings_file = "/data/event_channel_managarr_settings.json"
        self.last_results = []
        self.scan_progress = {"current": 0, "total": 0, "status": "idle", "start_time": None}
        
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

    def _save_settings(self, settings):
        """Save settings to disk"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            self.saved_settings = settings
            LOGGER.info("Settings saved successfully")
        except Exception as e:
            LOGGER.error(f"Error saving settings: {e}")
            
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
            deleted_files = []
            
            for filename in os.listdir(export_dir):
                if ((filename.startswith("event_channel_managarr_") or filename.startswith("epg_removal_")) 
                    and filename.endswith(".csv")):
                    filepath = os.path.join(export_dir, filename)
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        deleted_files.append(filename)
                        logger.info(f"Deleted CSV file: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {filename}: {e}")
            
            if deleted_count == 0:
                return {
                    "status": "success",
                    "message": "No CSV export files found to delete."
                }
            
            message_parts = [
                f"Successfully deleted {deleted_count} CSV export file(s):",
                ""
            ]
            
            # Show first 10 deleted files
            for filename in deleted_files[:10]:
                message_parts.append(f"• {filename}")
            
            if len(deleted_files) > 10:
                message_parts.append(f"• ... and {len(deleted_files) - 10} more files")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error clearing CSV exports: {e}")
            return {"status": "error", "message": f"Error clearing CSV exports: {e}"}

    def update_schedule_action(self, settings, logger):
        """Save settings and update scheduled tasks"""
        try:
            self._save_settings(settings)
            self._start_background_scheduler(settings)
            
            scheduled_times_str = settings.get("scheduled_times", "").strip()
            
            if scheduled_times_str:
                times = self._parse_scheduled_times(scheduled_times_str)
                if times:
                    tz_str = self._get_system_timezone()
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

    def _get_system_timezone(self):
        """Get the system timezone from settings"""
        # First check if user specified a timezone in plugin settings
        if hasattr(self, 'saved_settings') and self.saved_settings.get('timezone'):
            user_tz = self.saved_settings.get('timezone')
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
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    times.append(datetime.strptime(time_str, "%H%M").time())
                else:
                    LOGGER.warning(f"Invalid time format: {time_str} (hour must be 0-23, minute 0-59)")
            else:
                LOGGER.warning(f"Invalid time format: {time_str} (expected HHMM format)")
        
        return times

    def _start_background_scheduler(self, settings):
        """Start background thread for scheduled scans"""
        global _bg_thread
        
        scheduled_times_str = settings.get("scheduled_times", "").strip()
        if not scheduled_times_str:
            LOGGER.info("No scheduled times configured")
            return
        
        scheduled_times = self._parse_scheduled_times(scheduled_times_str)
        if not scheduled_times:
            return
        
        tz_str = self._get_system_timezone()
        LOGGER.info(f"Starting background scheduler with timezone: {tz_str}")
        
        # Stop existing thread if running
        if _bg_thread and _bg_thread.is_alive():
            _stop_event.set()
            _bg_thread.join(timeout=5)
            _stop_event.clear()
        
        # Start new scheduler thread
        def scheduler_loop():
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz_str)
            last_run_date = None
            
            while not _stop_event.is_set():
                try:
                    now = datetime.now(tz)
                    current_time = now.time()
                    current_date = now.date()
                    
                    # Check each scheduled time
                    for scheduled_time in scheduled_times:
                        # Calculate seconds until scheduled time
                        scheduled_dt = datetime.combine(current_date, scheduled_time, tzinfo=tz)
                        time_diff = (scheduled_dt - now).total_seconds()
                        
                        # Run if within 30 seconds and haven't run today
                        if -30 <= time_diff <= 30 and last_run_date != current_date:
                            LOGGER.info(f"Scheduled scan triggered at {now.strftime('%Y-%m-%d %H:%M %Z')}")
                            try:
                                result = self._scan_and_update_channels(settings, LOGGER, dry_run=False)
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
            
            logger.info("Successfully obtained API access token")
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

    def _check_channel_has_event(self, channel, logger):
        """Check if channel has event based on name and EPG data"""
        channel_name = channel.name or ""
        
        # Check for "no event" variations in channel name (case-insensitive)
        no_event_pattern = re.compile(r'\bno[_\s-]?event\b', re.IGNORECASE)
        if no_event_pattern.search(channel_name):
            return False, "Name contains 'no event'"
        
        # Check if channel name is blank or only whitespace
        if not channel_name.strip():
            return False, "Channel name is blank"
        
        # Check for empty event placeholders (ending with : or | with nothing after)
        # Pattern 1: Ends with colon and whitespace/nothing (e.g., "PPV EVENT 20:")
        if re.search(r':\s*$', channel_name):
            return False, "Empty placeholder (ends with colon)"
        
        # Pattern 2: Ends with pipe and whitespace/nothing (e.g., "PPV07 |")
        if re.search(r'\|\s*$', channel_name):
            return False, "Empty placeholder (ends with pipe)"
        
        # Check minimum event description length after separators
        # Pattern 3: Has colon separator - check what comes after
        colon_match = re.search(r':(.+)$', channel_name)
        if colon_match:
            description_after_colon = colon_match.group(1).strip()
            if len(description_after_colon) < 15:
                return False, f"Insufficient event details after colon ({len(description_after_colon)} chars)"
        
        # Pattern 4: Has pipe separator - check what comes after
        pipe_match = re.search(r'\|(.+)$', channel_name)
        if pipe_match:
            description_after_pipe = pipe_match.group(1).strip()
            if len(description_after_pipe) < 15:
                return False, f"Insufficient event details after pipe ({len(description_after_pipe)} chars)"
        
        # Pattern 5: No separator - check total channel name length
        if not colon_match and not pipe_match:
            if len(channel_name.strip()) < 25:
                return False, f"Channel name too short without event details ({len(channel_name.strip())} chars)"
        
        # If channel has EPG assigned, check for program data
        if channel.epg_data:
            # Get today's date range
            today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)
            
            # Check if there is any program data for today
            has_programs = ProgramData.objects.filter(
                epg=channel.epg_data,
                start_time__lt=today_end,
                end_time__gte=today_start
            ).exists()
            
            if not has_programs:
                return False, "No EPG program data for today"
        
        # Channel has event
        return True, "Has event"

    def _normalize_channel_name(self, channel_name):
        """Normalize channel name for duplicate detection by removing event details"""
        if not channel_name:
            return ""
        
        # Extract base name before colon or pipe
        # "PPV EVENT 20: Some Event" -> "PPV EVENT 20"
        # "PPV07 | Some Event" -> "PPV07"
        # "LIVE EVENT 01 6:15PM Event" -> "LIVE EVENT 01"
        
        # Remove everything after colon
        name = re.sub(r':.*$', '', channel_name)
        # Remove everything after pipe
        name = re.sub(r'\|.*$', '', name)
        
        # Normalize whitespace and convert to uppercase for comparison
        name = re.sub(r'\s+', ' ', name).strip().upper()
        
        return name

    def _handle_duplicates(self, channels_to_process, channels_to_hide, channels_to_show, logger):
        """Handle duplicate channels - keep only one visible based on channel_number and name detail"""
        # Group channels by normalized name
        channel_groups = {}
        
        for channel_info in channels_to_process:
            channel_id = channel_info['channel_id']
            channel_name = channel_info['channel_name']
            channel_number = channel_info['channel_number']
            normalized_name = self._normalize_channel_name(channel_name)
            
            if normalized_name not in channel_groups:
                channel_groups[normalized_name] = []
            
            channel_groups[normalized_name].append({
                'id': channel_id,
                'name': channel_name,
                'number': channel_number,
                'name_length': len(channel_name)
            })
        
        # Process each group of duplicates
        duplicate_hide_list = []
        
        for normalized_name, channels in channel_groups.items():
            if len(channels) <= 1:
                continue  # No duplicates, skip
            
            logger.info(f"Found {len(channels)} duplicate channels for '{normalized_name}'")
            
            # Sort by channel number first, then by name length (descending)
            channels_sorted = sorted(channels, key=lambda x: (x['number'] if x['number'] is not None else float('inf'), -x['name_length']))
            
            # Keep the first one (lowest channel number, or longest name if same number)
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

    def _get_channel_visibility(self, channel_id, profile_id, logger):
        """Get current visibility status for a channel in a profile"""
        try:
            membership = ChannelProfileMembership.objects.filter(
                channel_id=channel_id,
                channel_profile_id=profile_id
            ).first()
            
            if membership:
                return membership.enabled
            else:
                # If no membership exists, channel is not visible
                return False
        except Exception as e:
            logger.warning(f"Error getting visibility for channel {channel_id}: {e}")
            return False

    def _scan_and_update_channels(self, settings, logger, dry_run=True):
        """Scan channels and update visibility based on event data"""
        try:
            # Validate required settings
            channel_profile_name = settings.get("channel_profile_name", "").strip()
            if not channel_profile_name:
                return {"status": "error", "message": "Channel Profile Name is required. Please configure it in the plugin settings."}
            
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            # Get Channel Profile
            logger.info(f"Fetching Channel Profile: {channel_profile_name}")
            profiles = self._get_api_data("/api/channels/profiles/", token, settings, logger)
            
            profile_id = None
            for profile in profiles:
                if profile.get('name', '').strip().upper() == channel_profile_name.upper():
                    profile_id = profile.get('id')
                    break
            
            if not profile_id:
                return {"status": "error", "message": f"Channel Profile '{channel_profile_name}' not found. Please check the profile name in settings."}
            
            # Get ALL channels in the profile (both enabled and disabled) via membership
            memberships = ChannelProfileMembership.objects.filter(
                channel_profile_id=profile_id
            ).select_related('channel')
            
            all_channel_ids = [m.channel_id for m in memberships]
            
            if not all_channel_ids:
                return {"status": "error", "message": f"Channel Profile '{channel_profile_name}' has no channels."}
            
            logger.info(f"Found {len(all_channel_ids)} channels in profile '{channel_profile_name}' (including hidden channels)")
            
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
                return {"status": "error", "message": f"No channels found in profile '{channel_profile_name}' with the specified groups."}
            
            logger.info(f"Processing {total_channels} channels...")
            
            # Compile regex patterns
            regex_ignore = None
            regex_ignore_str = settings.get("regex_channels_to_ignore", "").strip()
            if regex_ignore_str:
                try:
                    regex_ignore = re.compile(regex_ignore_str, re.IGNORECASE)
                    logger.info(f"Ignore regex compiled: {regex_ignore_str}")
                except re.error as e:
                    return {"status": "error", "message": f"Invalid 'Regex: Channel Names to Ignore': {e}"}
            
            regex_inactive = None
            regex_inactive_str = settings.get("regex_mark_inactive", "").strip()
            if regex_inactive_str:
                try:
                    regex_inactive = re.compile(regex_inactive_str, re.IGNORECASE)
                    logger.info(f"Inactive regex compiled: {regex_inactive_str}")
                except re.error as e:
                    return {"status": "error", "message": f"Invalid 'Regex: Mark Channel as Inactive': {e}"}
            
            # Initialize progress
            self.scan_progress = {"current": 0, "total": total_channels, "status": "running", "start_time": time.time()}
            
            results = []
            channels_to_hide = []
            channels_to_show = []
            channels_ignored = []
            channels_for_duplicate_check = []
            
            # Process each channel
            for i, channel in enumerate(channels):
                self.scan_progress["current"] = i + 1
                
                channel_name = channel.name or ""
                current_visible = self._get_channel_visibility(channel.id, profile_id, logger)
                
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
                        "has_epg": "Yes" if channel.epg_data else "No"
                    })
                    continue
                
                # Check for additional inactive patterns via regex
                action_needed = None
                reason = None
                
                if regex_inactive and regex_inactive.search(channel_name):
                    action_needed = "hide"
                    reason = "Matches inactive regex"
                else:
                    # Check if channel has event
                    has_event, event_reason = self._check_channel_has_event(channel, logger)
                    
                    if has_event:
                        if not current_visible:
                            action_needed = "show"
                            reason = event_reason
                    else:
                        if current_visible:
                            action_needed = "hide"
                            reason = event_reason
                
                # Store channel info for duplicate detection
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
                logger
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
                    final_action = "No change"
                
                results.append({
                    "channel_id": channel_id,
                    "channel_name": channel_info['channel_name'],
                    "channel_number": channel_info['channel_number'],
                    "channel_group": channel_info['channel_group'],
                    "current_visibility": "Visible" if channel_info['current_visible'] else "Hidden",
                    "action": final_action,
                    "reason": reason or "Has event",
                    "has_epg": channel_info['has_epg']
                })
            
            # Mark scan as complete
            self.scan_progress['status'] = 'idle'
            
            total_duplicates_hidden = len(duplicate_hide_list)
            logger.info(f"Scan completed: {len(channels_to_hide)} to hide, {len(channels_to_show)} to show, {len(channels_ignored)} ignored, {total_duplicates_hidden} duplicates hidden")
            
            # Export to CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"event_channel_managarr_{'dryrun' if dry_run else 'applied'}_{timestamp}.csv"
            csv_filepath = os.path.join("/data/exports", csv_filename)
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(csv_filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['channel_id', 'channel_name', 'channel_number', 'channel_group', 
                            'current_visibility', 'action', 'reason', 'has_epg']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for result in results:
                    writer.writerow(result)
            
            logger.info(f"Results exported to {csv_filepath}")
            
            # Apply changes if not dry run
            if not dry_run and (channels_to_hide or channels_to_show):
                # Build bulk update payload
                channels_payload = []
                
                for channel_id in channels_to_hide:
                    channels_payload.append({'channel_id': channel_id, 'enabled': False})
                
                for channel_id in channels_to_show:
                    channels_payload.append({'channel_id': channel_id, 'enabled': True})
                
                if channels_payload:
                    logger.info(f"Applying visibility changes to {len(channels_payload)} channels...")
                    payload = {'channels': channels_payload}
                    self._patch_api_data(f"/api/channels/profiles/{profile_id}/channels/bulk-update/", 
                                        token, payload, settings, logger)
                    logger.info("Visibility changes applied successfully")

            # These should be OUTSIDE the if block (fix indentation)
            # Save settings on every run
            self._save_settings(settings)

            # Save results
            result_data = {
                "scan_time": datetime.now().isoformat(),
                "dry_run": dry_run,
                "profile_name": channel_profile_name,
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
                f"Results exported to: {csv_filepath}"
            ]
            
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
                    "csv_file": csv_filepath
                }
            }
            
        except Exception as e:
            self.scan_progress['status'] = 'idle'
            logger.error(f"Error during channel scan: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error during channel scan: {str(e)}"}

    def run(self, action, params, context):
        """Main plugin entry point"""
        LOGGER.info(f"Event Channel Managarr run called with action: {action}")
        
        try:
            # Get settings from context
            settings = context.get("settings", {})
            logger = context.get("logger", LOGGER)
            
            if action == "update_schedule":
                return self.update_schedule_action(settings, logger)
            elif action == "dry_run":
                return self.dry_run_action(settings, logger)
            elif action == "run_now":
                return self.run_now_action(settings, logger)
            elif action == "remove_epg_from_hidden":
                return self.remove_epg_from_hidden_action(settings, logger)
            elif action == "clear_csv_exports":
                return self.clear_csv_exports_action(settings, logger)
            elif action == "cleanup_periodic_tasks":
                return self.cleanup_periodic_tasks_action(settings, logger)
            else:
                return {
                    "status": "error",
                    "message": f"Unknown action: {action}",
                    "available_actions": ["update_schedule", "dry_run", "run_now", "remove_epg_from_hidden", "clear_csv_exports", "cleanup_periodic_tasks"]
                }
                
        except Exception as e:
            self.scan_progress['status'] = 'idle'
            LOGGER.error(f"Error in plugin run: {str(e)}")
            return {"status": "error", "message": str(e)}

    def dry_run_action(self, settings, logger):
        """Run scan without applying changes"""
        return self._scan_and_update_channels(settings, logger, dry_run=True)

    def run_now_action(self, settings, logger):
        """Run scan and apply changes immediately"""
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
            channel_profile_name = settings.get("channel_profile_name", "").strip()
            if not channel_profile_name:
                return {
                    "status": "error",
                    "message": "Channel Profile Name is required. Please configure it in settings."
                }
            
            # Get channel profile using Django ORM
            try:
                profile = ChannelProfile.objects.get(name=channel_profile_name)
                profile_id = profile.id
                logger.info(f"Found profile: {channel_profile_name} (ID: {profile_id})")
            except ChannelProfile.DoesNotExist:
                return {
                    "status": "error",
                    "message": f"Channel profile '{channel_profile_name}' not found"
                }
            
            # Get all channel memberships in this profile that are disabled
            hidden_memberships = ChannelProfileMembership.objects.filter(
                channel_profile_id=profile_id,
                enabled=False
            ).select_related('channel')
            
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
                channel_name = channel.name or 'Unknown'
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
fields = Plugin.fields
actions = Plugin.actions

plugin = Plugin()
plugin_instance = Plugin()

event_channel_managarr = Plugin()
EVENT_CHANNEL_MANAGARR = Plugin()

# Export the Celery task function
__all__ = ['Plugin', 'run_event_channel_scan', 'fields', 'actions', 'plugin', 'plugin_instance']