=========================
Event Channel Managarr
=========================

A Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. It is designed to hide channels that currently have no event information and show channels that do.

https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin

-- Features --

* Automatic Visibility Control: Hides channels without active events and shows channels that have them.
* EPG & Name-Based Detection: Determines channel activity by checking for "no event" variations in the name, blank names, placeholder characters (e.g., EVENT:), and the presence of EPG program data for the current day.
* Duplicate Channel Handling: Intelligently detects duplicate channels based on a normalized name (e.g., "PPV EVENT 01"). It keeps the one with the lowest channel number (or longest name as a tie-breaker) visible and hides the others.
* Targeted Processing: Configure a specific Channel Profile to monitor, with the option to further filter by one or more Channel Groups.
* Flexible Scheduling: Run scans automatically at specific times each day (e.g., 0600,1300,1800) with support for different timezones.
* Custom Ignore Rules: Use regular expressions to completely exclude certain channels (e.g., ^BACKUP) or to force-hide channels that match an inactive pattern (e.g., PLACEHOLDER).
* Safe Dry Run Mode: Preview all proposed visibility changes in a CSV export without modifying your channel lineup.
* Detailed Reporting: Both dry runs and applied changes generate a CSV report detailing the action taken for each channel and the reason.


-- Requirements --

* Active Dispatcharr installation
* Admin username and password for API access


-- Installation --

1. Log in to Dispatcharr's web UI.
2. Navigate to Plugins.
3. Click Import Plugin and upload the plugin zip file.
4. Enable the plugin after installation.


-- Settings Reference --

Setting: Dispatcharr URL
 - Type: string
 - Description: Full URL of your Dispatcharr instance (e.g., http://127.0.0.1:9191).

Setting: Dispatcharr Admin Username
 - Type: string
 - Description: Your admin username for the Dispatcharr UI. Required for API access.

Setting: Dispatcharr Admin Password
 - Type: password
 - Description: Your admin password for the Dispatcharr UI. Required for API access.

Setting: Timezone
 - Type: string
 - Default: America/Chicago
 - Description: Timezone for scheduled runs (e.g., America/New_York, Europe/London).

Setting: Channel Profile Name (Required)
 - Type: string
 - Description: The Channel Profile containing channels to monitor. This is required.

Setting: Channel Groups
 - Type: string
 - Description: Comma-separated group names to monitor within the profile. Leave empty for all groups.

Setting: Regex: Channel Names to Ignore
 - Type: string
 - Description: Regular expression to match channel names that should be skipped entirely.

Setting: Regex: Mark Channel as Inactive
 - Type: string
 - Description: Regular expression to identify inactive channel names (e.g., TBD|COMING SOON) that should be hidden.

Setting: Scheduled Run Times
 - Type: string
 - Description: Comma-separated times (24-hour HHMM format) to run daily. Leave blank to disable.


-- Usage Guide --

1. Configure Authentication & Profile
   - Enter your Dispatcharr URL, username, and password.
   - Enter the Channel Profile Name you want the plugin to manage.
   - Optionally, specify Channel Groups to narrow the scope.

2. Set Rules & Schedule
   - Optionally, add regular expressions to ignore or mark channels as inactive.
   - Enter Scheduled Run Times in HHMM format (e.g., 0600,1800) or leave blank.
   - Click "Update Schedule" to save all settings and activate the schedule.

3. Preview Changes (Dry Run)
   - Click Run on "Dry Run (Export to CSV)".
   - This will not change anything but will generate a CSV file in /data/exports/.
   - Review the CSV to see what would be hidden or shown.

4. Apply Changes
   - When you are satisfied, click Run on "Run Now".
   - The plugin will apply visibility changes and generate a final report CSV.


-- Channel Visibility Logic --

A channel is considered "inactive" and will be hidden if any of the following are true:
* The channel name is blank or contains a "no event" variation.
* The channel name ends with a placeholder like ":" or "|" with no text following it.
* The event description in the name (text after a ":" or "|") is less than 15 characters long.
* The channel name itself is less than 25 characters long and has no separator (":" or "|").
* The channel has an EPG assigned, but there is no program data scheduled for the current day.
* The channel name matches the custom "Regex: Mark Channel as Inactive" pattern.

If none of these conditions are met, the channel is "active" and will be made visible.


-- Action Reference --

Action: Update Schedule
 - Description: Save all settings and update/activate the scheduled run times.

Action: Dry Run (Export to CSV)
 - Description: Preview which channels would be hidden or shown without making any changes.

Action: Run Now
 - Description: Immediately scan and apply visibility updates.


-- File Locations --

* Settings Cache: /data/event_channel_managarr_settings.json
* Last Run Results: /data/event_channel_managarr_results.json
* CSV Exports: /data/exports/event_channel_managarr_[dryrun|applied]_YYYYMMDD_HHMMSS.csv


-- Troubleshooting --

* "Channel Profile not found": Ensure the name entered in settings exactly matches a Channel Profile in Dispatcharr.
* "No channels found...": Verify the profile has channels assigned and group names (if used) are spelled correctly.
* Authentication Errors: Double-check the Dispatcharr URL, username, and password.
* Scheduler Not Running: You must click "Update Schedule" after changing schedule times to save and activate them.

