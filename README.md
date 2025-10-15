# Event Channel Managarr
A Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. It is designed to hide channels that currently have no event information and show channels that do.

## Features
* **Automatic Visibility Control**: Hides channels without active events and shows channels that have them.
* **EPG & Name-Based Detection**: Determines channel activity by checking for "no event" variations in the name, blank names, placeholder characters (e.g., `EVENT:`), and the presence of EPG program data for the current day.
* **Duplicate Channel Handling**: Intelligently detects duplicate channels based on a normalized name (e.g., "PPV EVENT 01"). It keeps the one with the lowest channel number (or longest name as a tie-breaker) visible and hides the others.
* **Targeted Processing**: Configure a specific **Channel Profile** to monitor, with the option to further filter by one or more **Channel Groups**.
* **Flexible Scheduling**: Run scans automatically at specific times each day (e.g., `0600,1300,1800`) with support for different timezones.
* **Custom Ignore Rules**: Use regular expressions to completely exclude certain channels (e.g., `^BACKUP`) or to force-hide channels that match an inactive pattern (e.g., `PLACEHOLDER`).
* **Safe Dry Run Mode**: Preview all proposed visibility changes in a CSV export without modifying your channel lineup.
* **Detailed Reporting**: Both dry runs and applied changes generate a CSV report detailing the action taken for each channel and the reason.

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
| **Dispatcharr URL** | `string` | - | Full URL of your Dispatcharr instance (e.g., `http://127.0.0.1:9191`). |
| **Dispatcharr Admin Username**| `string` | - | Your admin username for the Dispatcharr UI. Required for API access. |
| **Dispatcharr Admin Password**| `password`| - | Your admin password for the Dispatcharr UI. Required for API access. |
| **Timezone** | `string` | `America/Chicago` | Timezone for scheduled runs (e.g., `America/New_York`, `Europe/London`). |
| **Channel Profile Name (Required)** | `string` | - | The Channel Profile containing channels to monitor. This is required. |
| **Channel Groups** | `string` | - | Comma-separated group names to monitor within the profile. Leave empty for all groups. |
| **Regex: Channel Names to Ignore** | `string` | - | Regular expression to match channel names that should be skipped entirely. |
| **Regex: Mark Channel as Inactive** | `string` | - | Regular expression to identify inactive channel names (e.g., `TBD\|COMING SOON`) that should be hidden. |
| **Scheduled Run Times** | `string` | - | Comma-separated times (24-hour HHMM format) to run daily. Leave blank to disable. |

## Usage Guide

### Step-by-Step Workflow
1.  **Configure Authentication & Profile**
    * Enter your Dispatcharr URL, username, and password.
    * Enter the **Channel Profile Name** you want the plugin to manage. This is required.
    * Optionally, specify **Channel Groups** to narrow the scope.
2.  **Set Rules & Schedule**
    * Optionally, add regular expressions to ignore or mark channels as inactive.
    * Enter **Scheduled Run Times** in HHMM format (e.g., `0600,1800`) or leave blank to disable the scheduler.
    * Click **Update Schedule**. This saves all settings and activates the schedule if times are provided.
3.  **Preview Changes (Dry Run)**
    * Click **Run** on `Dry Run (Export to CSV)`.
    * This will not change anything but will generate a CSV file in `/data/exports/`.
    * Review the CSV to see which channels would be hidden or shown and why.
4.  **Apply Changes**
    * When you are satisfied with the preview, click **Run** on `Run Now`.
    * The plugin will immediately apply the visibility changes and generate a final report CSV.

## Channel Visibility Logic
The plugin determines a channel is "inactive" and should be hidden if any of the following are true:
* The channel name is blank or contains a "no event" variation.
* The channel name ends with a placeholder character like `:` or `|` with no text following it.
* The event description in the name (text after a `:` or `|`) is less than 15 characters long.
* The channel name itself is less than 25 characters long and has no separator (`:` or `|`).
* The channel has an EPG assigned, but there is no program data scheduled for the current day.
* The channel name matches the custom `Regex: Mark Channel as Inactive` pattern.

If none of these conditions are met, the channel is considered "active" and will be made visible.

### Duplicate Handling
To prevent multiple versions of the same event channel from being visible, the plugin:
1.  Normalizes channel names by removing event details (e.g., "PPV EVENT 01: Big Fight" becomes "PPV EVENT 01").
2.  Groups all channels with the same normalized name.
3.  Within a group, it sorts the channels by channel number (lowest first) and then by name length (longest first).
4.  It keeps the first channel in the sorted list visible and marks all others in the group to be hidden.

## Action Reference

| Action | Description |
| :--- | :--- |
| **Update Schedule** | Save all settings and update/activate the scheduled run times. |
| **Dry Run (Export to CSV)** | Preview which channels would be hidden or shown without making any changes. |
| **Run Now** | Immediately scan and apply visibility updates based on the current EPG data. |

## File Locations
* **Settings Cache**: `/data/event_channel_managarr_settings.json`
* **Last Run Results**: `/data/event_channel_managarr_results.json`
* **CSV Exports**: `/data/exports/event_channel_managarr_[dryrun|applied]_YYYYMMDD_HHMMSS.csv`

## CSV Export Format

| Column | Description |
| :--- | :--- |
| **channel_id** | Internal Dispatcharr channel ID. |
| **channel_name** | The full name of the channel. |
| **channel_number** | The channel number. |
| **channel_group** | The channel's group name. |
| **current_visibility** | The visibility status before the run (`Visible` or `Hidden`). |
| **action** | The action taken by the plugin (`Show`, `Hide`, `No change`, `Ignored`). |
| **reason** | The reason for the action (e.g., "No EPG program data", "Duplicate channel"). |
| **has_epg** | Indicates if an EPG is assigned to the channel (`Yes` or `No`). |

## Troubleshooting

* **"Channel Profile not found"**: Ensure the name entered in the settings exactly matches the name of a Channel Profile in Dispatcharr (case-insensitive).
* **"No channels found..."**: Verify that the specified profile has channels assigned to it and that the group names (if used) are spelled correctly.
* **Authentication Errors**: Double-check that the Dispatcharr URL, username, and password are correct. The URL should be the one you use in your browser's address bar.
* **Scheduler Not Running**: After changing the schedule, you must click **Update Schedule** to save and activate it. Ensure the times are in `HHMM` format (e.g., `0700` for 7 AM).
