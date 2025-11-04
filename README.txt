=========================
Event Channel Managarr
=========================

Version: 0.4.0
A Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. It is designed to hide channels that currently have no event information and show channels that do.

GitHub Repository: https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin


-- Features --

* Automatic Visibility Control: Hides channels without active events and shows channels that have them.
* Flexible Hide Rules: A customizable, priority-based rule system to determine which channels to hide.
* Force-Visible Allow List: A regular expression to guarantee specific channels (e.g., news, weather) are never hidden.
* EPG & Name-Based Detection: Rules can check for blank names, placeholder text, date mismatches, and EPG data presence.
* Configurable Duplicate Handling: Choose whether to keep the channel with the lowest number, highest number, or longest name when duplicates are found.
* Past Event Grace Period: Set a grace period in hours to prevent events that run past midnight from being hidden too early.
* Automatic EPG Removal: Optionally, automatically remove EPG data from channels at the moment they are hidden.
* Targeted Processing: Configure specific Channel Profiles and Groups to monitor.
* Flexible Scheduling: Run scans automatically at specific times each day with support for different timezones.
* Safe Dry Run Mode: Preview all proposed visibility changes in a CSV export without modifying your channel lineup.
* Detailed Reporting: Both dry runs and applied changes can generate a CSV report detailing the action taken for each channel and the reason.



-- Requirements --

* Active Dispatcharr installation
* Admin username and password for API access


-- Installation --

1. Log in to Dispatcharr's web UI.
2. Navigate to Plugins.
3. Click Import Plugin and upload the plugin zip file.
4. Enable the plugin after installation.


-- Settings Reference --

Setting: 👤 Dispatcharr Admin Username
 - Description: Your admin username for the Dispatcharr UI. This is automatically populated with the current user's name but can be overridden.

Setting: 🔑 Dispatcharr Admin Password
 - Description: Your admin password for the Dispatcharr UI. Required for API access.

Setting: 🌍 Timezone
 - Type: Select
 - Description: Timezone for scheduled runs (e.g., America/New_York, Europe/London).

Setting: 📺 Channel Profile Names (Required)
 - Type: Text
 - Description: Comma-separated list of Channel Profile(s) containing channels to monitor.

Setting: 📂 Channel Groups (comma-separated)
 - Type: Text
 - Description: Comma-separated group names to monitor within the profile. Leave empty for all groups.

Setting: 📜 Hide Rules Priority
 - Type: String
 - Description: A comma-separated list of rules that determine which channels to hide. The first rule that matches a channel will be applied. See the "Hide Rules Priority Guide" below for a full list of tags.
 - Default: [BlankName],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[ShortDescription],[ShortChannelName]

Setting: 🚫 Regex: Channel Names to Ignore
 - Type: string
 - Description: Regular expression to match channel names that should be skipped entirely.

Setting: 💤 Regex: Mark Channel as Inactive
 - Type: string
 - Description: Regular expression to hide channels. This is processed as part of the [InactiveRegex] hide rule.

Setting: ✅ Regex: Force Visible Channels
 - Type: string
 - Description: Regular expression to match channel names that should ALWAYS be visible, overriding any hide rules.

Setting: 🎭 Duplicate Handling Strategy
 - Type: Select
 - Description: Strategy to use when multiple channels have the same event. See "Duplicate Handling Guide" below.

Setting: 📅 Past Date Grace Period (Hours)
 - Type: String (Number)
 - Default: 0
 - Description: Hours to wait after midnight before hiding past events. Useful for events that run late.

Setting: 🔌 Auto-Remove EPG on Hide
 - Type: Boolean
 - Default: Disabled
 - Description: If enabled, automatically removes EPG data from a channel when it is hidden by the plugin.

Setting: ⏰ Scheduled Run Times
 - Type: string
 - Description: Comma-separated times (24-hour HHMM format) to run daily. Leave blank to disable.

Setting: 📄 Enable Scheduled CSV Export
 - Type: Boolean
 - Default: Enabled
 - Description: If enabled, a CSV report is generated during scheduled runs.


-- Hide Rules Priority Guide --

The "Hide Rules Priority" field defines a sequence of rules to hide channels. The plugin processes the rules in the order you specify. The first rule that matches a channel is applied, and no other rules are checked for that channel.

Available Tags:

[NoEPG]
 - Hides the channel if it has no EPG data assigned, OR if it has EPG data but no programs scheduled for the next 24 hours.
 - Note: This rule does not work for custom dummy EPGs.

[BlankName]
 - Hides the channel if its name is empty or contains only whitespace.

[WrongDayOfWeek]
 - Hides the channel if its name contains a day of the week (e.g., "Monday", "TUE") that is not today.
 - Example: A channel named "Saturday Night Fights" will be hidden on any day that isn't Saturday.

[NoEventPattern]
 - Hides the channel if its name contains common "no event" phrases.
 - Matches: "no event", "no events", "offline", "no games scheduled", "no scheduled event".
 - Example: "PPV 01: No Event" will be hidden.

[EmptyPlaceholder]
 - Hides the channel if its name suggests it's a placeholder waiting for an event description. It checks for a colon, pipe, or dash at the end of the name with little or no text following it.
 - Example: "EVENT 01:", "PPV Main | ", "UFC Night -" will all be hidden.

[ShortDescription]
 - Hides the channel if the event description (the text after a ':', '|', or '-') is shorter than 15 characters.
 - Example: "Boxing: Ali vs Fr" will be hidden because "Ali vs Fr" is too short.

[ShortChannelName]
 - Hides the channel if the entire name is shorter than 25 characters AND it does not contain a ':', '|', or '-' separator. This helps hide generic channels that don't have specific event details.
 - Example: "Main Event Backup" will be hidden.

[PastDate:days]
 - Hides the channel if its name contains a date that is more than 'days' in the past. If 'days' is 0, it hides if the date is yesterday or earlier.
 - Note: This rule is affected by the "Past Date Grace Period" setting.
 - The plugin can extract dates in formats like MM/DD/YYYY, MM/DD, MMM DD, etc.
 - Example Rule: [PastDate:1]
 - Example Channel: A channel named "Event 10/25/2025" will be hidden on 10/27/2025 or later.

[FutureDate:days]
 - Hides the channel if its name contains a date that is more than 'days' in the future.
 - Example Rule: [FutureDate:7]
 - Example Channel: A channel named "UFC 350 (Dec 25)" will be hidden if today's date is more than 7 days before December 25th.

[InactiveRegex]
 - Hides the channel if its name matches the regular expression provided in the "Regex: Mark Channel as Inactive" setting. This allows you to define your own custom patterns for inactive channels.


-- Duplicate Handling Guide --

When multiple channels are found for the same event, this setting determines which one to keep visible.

Keep Lowest Channel Number (Default)
 - Sorts duplicate channels by their number from lowest to highest and keeps the first one. This is the default and most common strategy.

Keep Highest Channel Number
 - Sorts duplicate channels by their number from highest to lowest and keeps the first one. Useful if your provider places HD or 4K feeds on higher channel numbers.

Keep Longest Channel Name
 - Sorts duplicate channels by the length of their name and keeps the longest one. This assumes a longer name is more descriptive or of higher quality.


-- Usage Guide --

1. Configure Authentication & Profile
   - Enter your Dispatcharr Admin Password. The username will be auto-filled.
   - Enter the comma-separated Channel Profile Name(s) you want the plugin to manage.
   - Optionally, specify Channel Groups to narrow the scope.

2. Set Rules & Schedule
   - Configure the new features like Force Visible Regex, Duplicate Handling, and Grace Period to your liking.
   - Customize the "Hide Rules Priority" field to control which channels get hidden.
   - Enter Scheduled Run Times in HHMM format (e.g., 0600,1800) or leave blank.
   - Click "💾 Update Schedule" to save all settings and activate the schedule.

3. Preview Changes (Dry Run)
   - Click "🧪 Dry Run (Export to CSV)".
   - This will not change anything but will generate a CSV file in /data/exports/.
   - Review the CSV to see what would be hidden or shown and why.

4. Apply Changes
   - When you are satisfied, click "🚀 Run Now".
   - The plugin will apply visibility changes and generate a final report CSV (if enabled).


-- Action Reference --

Action: 💾 Update Schedule
 - Description: Save all settings and update/activate the scheduled run times.

Action: 🧪 Dry Run (Export to CSV)
 - Description: Preview which channels would be hidden or shown without making any changes.

Action: 🚀 Run Now
 - Description: Immediately scan and apply visibility updates.

Action: 🗑️ Remove EPG from Hidden Channels
 - Description: Remove all EPG data from channels that are disabled/hidden in the selected profile and/or groups.

Action: ✨ Clear CSV Exports
 - Description: Delete all CSV export files created by this plugin.

Action: 🧹 Cleanup Orphaned Tasks
 - Description: Remove any orphaned Celery periodic tasks from old plugin versions.


-- File Locations --

* Settings Cache: /data/event_channel_managarr_settings.json
* Last Run Results: /data/event_channel_managarr_results.json
* CSV Exports: /data/exports/event_channel_managarr_[dryrun|applied]_YYYYMMDD_HHMMSS.csv


-- Troubleshooting --

* "Channel Profile not found": Ensure the name(s) entered in settings exactly match a Channel Profile in Dispatcharr.
* "No channels found...": Verify the profile has channels assigned and group names (if used) are spelled correctly.
* Authentication Errors: Double-check your admin password.
* Scheduler Not Running: You must click "Update Schedule" after changing schedule times to save and activate them.
