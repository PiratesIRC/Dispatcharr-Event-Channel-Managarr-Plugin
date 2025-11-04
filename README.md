# Event Channel Managarr
A Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. It is designed to hide channels that currently have no event information and show channels that do.
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)

## Features
* **Automatic Visibility Control**: Hides channels without active events and shows channels that have them. Scans ALL channels in the profile (both visible and hidden) to ensure channels with new events are always shown.
* **Prioritized Hide Rules**: A fully customizable, priority-based rule system. You define the order of rules (e.g., `[BlankName]`, `[PastDate:0]`, `[ShortDescription]`) to determine *why* and *when* a channel should be hidden.
* **Date-Based Logic**: Use rules like `[PastDate:days]` and `[FutureDate:days]` to hide events that are over or too far in the future. Includes a **grace period** for events that run past midnight.
* **Day-of-Week Logic**: Use the `[WrongDayOfWeek]` rule to hide channels named for a specific day (e.g., "Saturday Night Fights") when it's not that day.
* **Multi-Profile Support**: Monitor and manage channels across **multiple Channel Profiles** at once (e.g., "PPV Events, Sports Profile").
* **Configurable Duplicate Handling**: Choose your strategy for handling duplicate events: keep the one with the **lowest number**, **highest number**, or **longest name**.
* **Force Visibility**: Use a regular expression to **force specific channels** (like news or weather) to remain visible, overriding all hide rules.
* **Flexible Scheduling**: Run scans automatically at specific times each day (e.g., `0600,1300,1800`) with a simple dropdown for timezone selection.
* **Auto-EPG Management**: When a channel is hidden, the plugin can automatically remove its EPG assignment to keep your guide clean.
* **Safe Dry Run Mode**: Preview all proposed visibility changes in a CSV export without modifying your channel lineup.
* **Maintenance Actions**: Clear accumulated CSV exports and cleanup orphaned tasks from older plugin versions.
* **Detailed Reporting**: Both dry runs and applied changes generate a CSV report detailing the action taken for each channel, the reason, and which hide rule was triggered.

## Requirements
* Active Dispatcharr installation
* Admin username and password for API access

## Installation
1.  Log in to Dispatcharr's web UI.
2.  Navigate to **Plugins**.
3.  Click **Import Plugin** and upload the plugin zip file.
4.  Enable the plugin after installation.

## Settings Reference

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🌐 Dispatcharr URL** | `string` | - | Full URL of your Dispatcharr instance (e.g., `http://127.0.0.1:9191`). |
| **👤 Dispatcharr Admin Username**| `string` | - | Your admin username for the Dispatcharr UI. Required for API access. |
| **🔑 Dispatcharr Admin Password**| `password`| - | Your admin password for the Dispatcharr UI. Required for API access. |
| **🌍 Timezone** | `select` | `America/Chicago` | Timezone for scheduled runs. Select from the dropdown. |
| **📺 Channel Profile Names (Required)** | `string` | - | Channel Profile(s) to monitor. Use comma-separated names for multiple profiles. |
| **📂 Channel Groups** | `string` | - | Comma-separated group names to monitor. Leave empty for all groups in the profile(s). |
| **📜 Hide Rules Priority** | `string` | (see default) | Define rules for hiding channels in priority order. First match wins. See "Hide Rule Logic" below. |
| **🚫 Regex: Channel Names to Ignore** | `string` | - | Regular expression to match channel names that should be skipped entirely. |
| **💤 Regex: Mark Channel as Inactive** | `string` | - | Regular expression to hide channels. Processed as part of the `[InactiveRegex]` hide rule. |
| **✅ Regex: Force Visible Channels** | `string` | - | Regular expression to match channels that should ALWAYS be visible, overriding any hide rules. |
| **🎭 Duplicate Handling Strategy**| `select` | `lowest_number` | Strategy to use when multiple channels have the same event. |
| **📅 Past Date Grace Period (Hours)**| `string` | `4` | Hours to wait after midnight before hiding past events. Used by the `[PastDate]` rule. |
| **🔌 Auto-Remove EPG on Hide** | `boolean` | `True` | If enabled, automatically removes EPG data from a channel when it is hidden by the plugin. |
| **⏰ Scheduled Run Times** | `string` | - | Comma-separated times (24-hour HHMM format) to run daily. Leave blank to disable. |
| **📄 Enable Scheduled CSV Export** | `boolean` | `False`| If enabled, a CSV report will be created when the plugin runs on a schedule. |

## Usage Guide

### Step-by-Step Workflow
1.  **Configure Authentication & Profile(s)**
    * Enter your Dispatcharr URL, username, and password.
    * Enter the **Channel Profile Name(s)** you want the plugin to manage (e.g., `PPV Events, Sports Events`). This is required.
    * Optionally, specify **Channel Groups** to narrow the scope.
2.  **Set Rules & Schedule**
    * Configure your **Hide Rules Priority**. The default is a great starting point.
    * Optionally, add regular expressions for ignoring or forcing channels to be visible.
    * Choose your **Duplicate Handling Strategy**.
    * Enter **Scheduled Run Times** in HHMM format (e.g., `0600,1800`) or leave blank.
    * Click **Update Schedule**. This saves all settings and activates the schedule if times are provided.
3.  **Preview Changes (Dry Run)**
    * Click **Run** on `Dry Run (Export to CSV)`.
    * This will not change anything but will generate a CSV file in `/data/exports/`.
    * Review the CSV, especially the `reason` and `hide_rule` columns, to see what would happen and why.
4.  **Apply Changes**
    * When you are satisfied with the preview, click **Run** on `Run Now`.
    * The plugin will immediately apply the visibility changes and generate a final report CSV.
5.  **Maintenance (Optional)**
    * Use **Remove EPG from Hidden Channels** to delete EPG data from disabled channels.
    * Use **Clear CSV Exports** to remove accumulated export files.
    * Use **Cleanup Orphaned Tasks** to remove leftover tasks from older plugin versions.

## Hide Rule Logic
The plugin checks channels against the **Hide Rules Priority** list in the order you define. The first rule that matches is applied, and the channel is marked to be hidden. If no rules match, the channel is marked to be shown.

**Default Rules:**
`[InactiveRegex],[BlankName],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[ShortDescription],[ShortChannelName]`

**Available Rule Tags:**

| Rule | Parameter | Description |
| :--- | :--- | :--- |
| **[NoEPG]** | - | Hides if no EPG is assigned OR if the assigned EPG has no program data for the next 24 hours. |
| **[BlankName]** | - | Hides if the channel name is blank. |
| **[WrongDayOfWeek]** | - | Hides if the name contains a day (e.g., "Saturday") and today is not that day. |
| **[NoEventPattern]** | - | Hides if the name contains patterns like "no event", "offline", "no games scheduled". |
| **[EmptyPlaceholder]** | - | Hides if the name ends with a separator (`:`, `\|`, `-`) and has no event title after it. |
| **[ShortDescription]** | - | Hides if the event title (text after a separator) is less than 15 characters long. |
| **[ShortChannelName]**| - | Hides if the *entire name* is less than 25 characters long and has *no* separator. |
| **[PastDate:days]** | `days` (int) | Hides if the name contains a date that is more than `days` in the past (e.g., `[PastDate:0]` hides yesterday's events). Obeys the grace period setting. |
| **[FutureDate:days]**| `days` (int) | Hides if the name contains a date that is more than `days` in the future (e.g., `[FutureDate:2]` hides events 3+ days from now). |
| **[InactiveRegex]** | - | Hides if the name matches the `Regex: Mark Channel as Inactive` setting. |

### Duplicate Handling
To prevent multiple versions of the same event from being visible, the plugin:
1.  Normalizes channel names *and* event descriptions (e.g., "PPV 1: UFC" and "PPV 2: UFC" are duplicates, but "PPV 1: UFC" and "PPV 1: Boxing" are not).
2.  Groups all channels with the same normalized event.
3.  Within a group, it keeps only one channel visible based on your selected **Duplicate Handling Strategy** and hides all others.

## Action Reference

| Action | Description |
| :--- | :--- |
| **💾 Update Schedule** | Save all settings and update/activate the scheduled run times. |
| **🧪 Dry Run (Export to CSV)** | Preview which channels would be hidden or shown without making any changes. |
| **🚀 Run Now** | Immediately scan and apply visibility updates based on the current EPG data. |
| **🗑️ Remove EPG from Hidden Channels** | Delete all EPG data from channels that are currently hidden/disabled in the selected profile(s). |
| **✨ Clear CSV Exports** | Delete all CSV export files created by this plugin to free up disk space. |
| **🧹 Cleanup Orphaned Tasks** | Remove any orphaned Celery periodic tasks from old plugin versions. |

## File Locations
* **Settings Cache**: `/data/event_channel_managarr_settings.json`
* **Last Run Results**: `/data/event_channel_managarr_results.json`
* **CSV Exports**: `/data/exports/event_channel_managarr_[dryrun|applied]_YYYYMMDD_HHMMSS.csv`
* **EPG Removal Reports**: `/data/exports/epg_removal_YYYYMMDD_HHMMSS.csv`

## CSV Export Format

| Column | Description |
| :--- | :--- |
| **channel_id** | Internal Dispatcharr channel ID. |
| **channel_name** | The full name of the channel. |
| **channel_number** | The channel number. |
| **channel_group** | The channel's group name. |
| **current_visibility**| The visibility status before the run (`Visible` or `Hidden`). |
| **action** | The action taken by the plugin (`Show`, `Hide`, `Visible`, `No change`, `Ignored`). |
| **reason** | The reason for the action (e.g., "Event date... is 1 days in the past", "Duplicate channel"). |
| **hide_rule** | The specific rule tag that triggered the hide action (e.g., `PastDate:0`, `ShortDescription`). |
| **has_epg** | Indicates if an EPG is assigned to the channel (`Yes` or `No`). |

## Troubleshooting

### General Issues
* **"Channel Profile not found"**: Ensure the name(s) entered in the settings exactly match the names in Dispatcharr. Check for typos or extra spaces if using multiple comma-separated names.
* **"No channels found..."**: Verify that the specified profile(s) have channels assigned and that the group names (if used) are spelled correctly.
* **Authentication Errors**: Double-check that the Dispatcharr URL, username, and password are correct. The URL should be the one you use in your browser's address bar.
* **Scheduler Not Running**: After changing the schedule, you must click **Update Schedule** to save and activate it. Ensure the times are in `HHMM` format (e.g., `0700` for 7 AM).
* **Channels Aren't Hiding/Showing**: Run a **Dry Run** and check the `reason` and `hide_rule` columns for that channel. This will tell you exactly why a decision was made. You may need to adjust your **Hide Rules Priority** list.

### CSV Export Issues
* Ensure `/data/exports/` directory exists and is writable
* Check available disk space
* Verify no permission issues with the Dispatcharr data directory

## Updating the Plugin

To update Event Channel Managarr from a previous version:

### 1. Remove Old Version
* Navigate to **Plugins** in Dispatcharr
* Click the trash icon next to the old Event Channel Managarr plugin
* Confirm deletion

### 2. Restart Dispatcharr
* Log out of Dispatcharr
* Restart the Docker container:

```bash
docker restart dispatcharr
