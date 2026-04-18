# Event Channel Managarr
A Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. It hides channels that currently have no event information and shows channels that do — with optional managed dummy EPG so the guide still shows something useful (event title during the window; "Upcoming at <time>: <title>" before; "Ended at <time>: <title>" after) for channels that never have real EPG assigned.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin?include_prereleases&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)

## Features
* **Automatic Visibility Control**: Hides channels without active events and shows channels that have them. Scans ALL channels in the profile (both visible and hidden) to ensure channels with new events are always shown.
* **Prioritized Hide Rules**: A fully customizable, priority-based rule system. You define the order of rules (e.g., `[BlankName]`, `[PastDate:0]`, `[UndatedAge:2]`, `[ShortDescription]`) to determine *why* and *when* a channel should be hidden.
* **Undated-Channel Aging**: The `[UndatedAge:N]` rule tracks per-channel first-seen dates and hides channels whose names carry no parseable date once they've been visible for more than N days. Catches stale placeholder channels that date-only rules can't evaluate.
* **Managed Dummy EPG (new in v1.26.1081141)**: Opt-in. Visible channels with no EPG get bound to a plugin-managed dummy EPG source. Dispatcharr's guide then shows the extracted event title during its time window; before the window it shows `Upcoming at <start-time>: <title>`; after, `Ended at <end-time>: <title>`. For names with no parseable time, a 24-hour program rendering the channel name is used instead. Timezone-aware (channel name time is interpreted in the configured event timezone; the guide renders in the client's local time).
* **Stream Name Selection**: Choose between using the channel name or the stream name for rule matching. When stream name is selected, the plugin uses the first stream in the channel for all rule evaluations.
* **Date-Based Logic**: Use rules like `[PastDate:days]` and `[FutureDate:days]` to hide events that are over or too far in the future. Includes a **grace period** for events that run past midnight.
* **Enhanced Date Format Support**: Recognizes a wide variety of date formats in channel names, including dates with optional times (e.g., "Nov 8 16:00"), slash-separated dates, ISO formats, and more.
* **Day-of-Week Logic**: Use the `[WrongDayOfWeek]` rule to hide channels named for a specific day (e.g., "Saturday Night Fights") when it's not that day.
* **Multi-Profile Support**: Monitor and manage channels across **multiple Channel Profiles** at once (e.g., "PPV Events, Sports Profile").
* **Configurable Duplicate Handling**: Choose your strategy for handling duplicate events: keep the one with the **lowest number**, **highest number**, or **longest name**. Optionally keep all duplicate channels visible.
* **Direct Django ORM Integration**: Operates directly within Dispatcharr's Django environment for fast, reliable channel management without API overhead.
* **WebSocket Progress Updates**: Real-time adaptive progress notifications during scans via WebSocket.
* **Cross-Worker Safe**: A cross-process `fcntl` lock on the scan file ensures at most one scan runs at a time across all uwsgi workers, whether triggered by the scheduler or manually via Run Now / Dry Run.
* **Configurable Rate Limiting (new in v1.26.1081141)**: Select `none` / `low` / `medium` / `high` to pace per-channel ORM writes (0 / 0.05 / 0.2 / 0.5 seconds each). Defaults to `none`; useful when scanning very large profiles on a small database.
* **Sectioned UI (new in v1.26.1081141)**: Settings are grouped into **Scope**, **Hide Rules**, **Duplicates**, **EPG Management**, **Scheduling & Export**, and **Advanced** sections for easier navigation.
* **Force Visibility**: Use a regular expression to **force specific channels** (like news or weather) to remain visible, overriding all hide rules.
* **Flexible Scheduling**: Run scans automatically at specific times each day (e.g., `0600,1300,1800`) with a simple dropdown for timezone selection.
* **Auto-EPG Management**: When a channel is hidden, the plugin can automatically remove its EPG assignment to keep your guide clean.
* **Automatic Update Notifications**: Displays a notification in the plugin settings when a new version is available on GitHub, keeping you informed of the latest features and fixes.
* **Safe Dry Run Mode**: Preview all proposed visibility changes in a CSV export without modifying your channel lineup. Dry runs never create the managed dummy EPG source or write attach/detach bindings — they're pure previews.
* **Maintenance Actions**: Clear accumulated CSV exports and cleanup orphaned tasks from older plugin versions.
* **Detailed Reporting**: Both dry runs and applied changes generate a CSV report detailing the action taken for each channel, the reason, the hide rule triggered, and managed-EPG attach/detach state per channel. CSV headers include summary counts for managed EPG activity and the active rate-limiting level.

## Requirements
* Active Dispatcharr installation (v0.20.0 or newer; declared via `min_dispatcharr_version` in `plugin.json`).

## Installation
1.  Log in to Dispatcharr's web UI.
2.  Navigate to **Plugins**.
3.  Click **Import Plugin** and upload the plugin zip file.
4.  Enable the plugin after installation.

## Settings Reference

Settings are grouped into six sections in the UI.

### 📍 Scope

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🌍 Timezone** | `select` | `America/Chicago` | Timezone for scheduled runs. Select from the dropdown. |
| **📺 Channel Profile Names (Required)** | `text` | — | Channel Profile(s) to monitor. Use comma-separated names for multiple profiles. |
| **📂 Channel Groups** | `text` | — | Comma-separated group names to monitor. Leave empty for all groups in the profile(s). |
| **🔤 Name Source** | `select` | `Channel_Name` | Choose the source for rule matching: `Channel_Name` uses the channel name, `Stream_Name` uses the first stream's name in the channel. |

### 🎯 Hide Rules

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **📜 Hide Rules Priority** | `text` | (see default) | Define rules for hiding channels in priority order. First match wins. See "Hide Rule Logic" below. |
| **🚫 Regex: Channel Names to Ignore** | `text` | — | Regular expression to match channel names that should be skipped entirely. |
| **💤 Regex: Mark Channel as Inactive** | `text` | — | Regular expression to hide channels. Processed as part of the `[InactiveRegex]` hide rule. |
| **✅ Regex: Force Visible Channels** | `text` | — | Regular expression to match channels that should ALWAYS be visible, overriding any hide rules. |
| **📅 Past Date Grace Period (Hours)** | `number` | `4` | Hours to wait after midnight before hiding past events. Used by the `[PastDate]` rule. |

### 🎭 Duplicates

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🎭 Duplicate Handling Strategy** | `select` | `lowest_number` | Strategy to use when multiple channels have the same event. |
| **🔄 Keep Duplicate Channels** | `boolean` | `False` | If enabled, duplicate channels will be kept visible instead of being hidden. The duplicate strategy above will be ignored. |

### 🔌 EPG Management

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🔌 Auto-Remove EPG on Hide** | `boolean` | `True` | If enabled, automatically removes EPG data from a channel when it is hidden by the plugin. |
| **🗓️ Manage Dummy EPG** | `boolean` | `False` | If enabled, visible channels with no EPG get bound to the plugin-managed dummy EPG source. Disables cleanly: toggling off detaches all channels from the managed source on the next scan. |
| **⏱️ Event Duration (hours)** | `number` | `3` | How long each scheduled event appears in the guide. Before this window the guide shows `Upcoming at <start-time>: <title>`; after, `Ended at <end-time>: <title>`. |
| **📺 Channel Name Event Timezone** | `select` | `US/Eastern` | Timezone encoded in event times within channel names (e.g., `US/Eastern` for channels like `(4.17 8:30 PM ET)`). Independent of the scheduler timezone. |

### ⏰ Scheduling & Export

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **⏰ Scheduled Run Times** | `text` | — | Comma-separated times (24-hour HHMM format) to run daily. Leave blank to disable. |
| **📄 Enable Scheduled CSV Export** | `boolean` | `False` | If enabled, a CSV report will be created when the plugin runs on a schedule. |

### ⚙️ Advanced

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🐢 Rate Limiting** | `select` | `none` | Pause between per-channel ORM operations. Options: `None (fastest)` / `Low (~0.05s)` / `Medium (~0.2s)` / `High (~0.5s)` per channel. Useful for very large profiles or constrained databases. |

## Usage Guide

### Step-by-Step Workflow
1.  **Configure Profile(s)**
    * Enter the **Channel Profile Name(s)** you want the plugin to manage (e.g., `PPV Events, Sports Events`). This is required.
    * Optionally, specify **Channel Groups** to narrow the scope.
2.  **Set Rules & Schedule**
    * Configure your **Hide Rules Priority**. The default is a great starting point.
    * Optionally, add regular expressions for ignoring or forcing channels to be visible.
    * Choose your **Duplicate Handling Strategy**.
    * Enter **Scheduled Run Times** in HHMM format (e.g., `0600,1800`) or leave blank.
    * Click **💾 Save Schedule**. This saves all settings and activates the schedule if times are provided.
3.  **(Optional) Enable Managed Dummy EPG**
    * In the **🔌 EPG Management** section, toggle **Manage Dummy EPG** on.
    * Set **Event Duration (hours)** and **Channel Name Event Timezone** to match your event-channel conventions.
    * On the next scan, visible channels with no EPG get bound to the plugin-managed dummy source. The guide will then show `Upcoming at <start-time>: <title>` before the window, the event title during it, and `Ended at <end-time>: <title>` after.
4.  **Preview Changes (Dry Run)**
    * Click **👁️ Dry Run**.
    * This will not change anything but will generate a CSV file in `/data/exports/`.
    * Review the CSV, especially the `reason`, `hide_rule`, `managed_epg_assigned`, and `managed_epg_detached` columns, to see what would happen and why.
5.  **Apply Changes**
    * When you are satisfied with the preview, click **▶️ Run Now** (confirm the dialog).
    * The plugin will immediately apply the visibility changes, attach/detach managed EPG (if enabled), and generate a final report CSV.
6.  **Maintenance (Optional)**
    * **🧹 Remove EPG from Hidden Channels** — delete EPG data from disabled channels (confirmation required; destructive).
    * **🗑️ Clear CSV Exports** — remove accumulated export files (confirmation required).
    * **🧼 Cleanup Orphaned Tasks** — remove leftover Celery Beat tasks from older plugin versions (confirmation required).

## Hide Rule Logic
The plugin checks channels against the **Hide Rules Priority** list in the order you define. The first rule that matches is applied, and the channel is marked to be hidden. If no rules match, the channel is marked to be shown.

**Default Rules:**
`[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedAge:2],[ShortDescription],[ShortChannelName]`

**Available Rule Tags:**

| Rule | Parameter | Description |
| :--- | :--- | :--- |
| **[NoEPG]** | — | Hides if no EPG is assigned OR if the assigned EPG has no program data for the next 24 hours. (Skips custom dummy EPG, including the plugin-managed source.) |
| **[BlankName]** | — | Hides if the channel name is blank. |
| **[WrongDayOfWeek]** | — | Hides if the name contains a day name (e.g., "MONDAY", "Mon", "Saturday", "Sat") and today is not that day. Recognizes full and abbreviated day names. |
| **[NoEventPattern]** | — | Hides if the name contains patterns like "no event", "offline", "no games scheduled". |
| **[EmptyPlaceholder]** | — | Hides if the name ends with a separator (`:`, `\|`, `-`) and has no event title after it. |
| **[ShortDescription]** | — | Hides if the event title (text after a separator) is less than 15 characters long. |
| **[ShortChannelName]** | — | Hides if the *entire name* is less than 25 characters long and has *no* separator. |
| **[NumberOnly]** | — | Hides if the channel name is just a prefix followed by a number (e.g., "PPV 12", "EVENT 15") with no event details. |
| **[PastDate:days]** or **[PastDate:days:Xh]** | `days` (int), optional `Xh` (grace hours) | Hides if the name contains a date that is more than `days` in the past (e.g., `[PastDate:0]` hides yesterday's events). Optionally specify grace period inline like `[PastDate:0:4h]` to override the global grace period setting. |
| **[FutureDate:days]** | `days` (int) | Hides if the name contains a date that is more than `days` in the future (e.g., `[FutureDate:2]` hides events 3+ days from now). |
| **[UndatedAge:days]** | `days` (int) | Hides channels whose names contain **no parseable date** once they've been visible for more than `days` days. Persists per-channel first-seen state in `/data/event_channel_managarr_undated_first_seen.json`. Resets a channel's age when its name changes. |
| **[InactiveRegex]** | — | Hides if the name matches the `Regex: Mark Channel as Inactive` setting. |

### Duplicate Handling
To prevent multiple versions of the same event from being visible, the plugin:
1.  Normalizes channel names *and* event descriptions (e.g., "PPV 1: UFC" and "PPV 2: UFC" are duplicates, but "PPV 1: UFC" and "PPV 1: Boxing" are not).
2.  Groups all channels with the same normalized event.
3.  Within a group, it keeps only one channel visible based on your selected **Duplicate Handling Strategy** and hides all others.

### Supported Date Formats
The plugin can extract dates from channel names in the following formats (checked in priority order):

| Format | Example | Notes |
| :--- | :--- | :--- |
| **start:YYYY-MM-DD HH:MM:SS** | `start:2024-12-25 20:00:00` | Highest priority. Matches exact datetime in channel name. |
| **stop:YYYY-MM-DD HH:MM:SS** | `stop:2024-12-25 23:00:00` | Matches end datetime in channel name. |
| **(YYYY-MM-DD HH:MM:SS)** | `(2025-11-22 15:10:00)` | Matches datetime within parentheses. |
| **MM/DD/YYYY** or **MM/DD/YY** | `12/25/2024` or `12/25/24` | Standard slash-separated date format. |
| **(MONTH DD)** | `(Dec 25)` or `(December 25)` | Month name and day in parentheses. |
| **DDth/st/nd/rd MONTH** | `25th Dec` or `1st January` | Day with ordinal suffix followed by month name. |
| **MONTH DD** | `Dec 25` or `December 25` | Month name followed by day (no parentheses). |
| **YYYY MM DD** | `2024 12 25` | Space-separated year, month, day. |
| **MM.DD** | `12.25` | Dot-separated month and day (assumes current year). |
| **MM/DD** | `12/25` | Slash-separated month and day (assumes current year). |

**Note:** When using `[PastDate]` or `[FutureDate]` rules, the plugin will attempt to extract a date using these formats. If no date is found, the rule will not match and the next rule in your priority list will be checked. The `[UndatedAge]` rule handles the "no date found" case directly.

## Managed Dummy EPG

When **🗓️ Manage Dummy EPG** is enabled:

* A single plugin-managed `EPGSource(source_type='dummy', name='ECM Managed Dummy')` row is created on first use.
* Visible channels in the monitored profile(s) with **no EPG assigned** are bound to it via a per-channel `EPGData` row keyed by `channel.uuid`.
* Channels that already have a real EPG binding (XMLTV, Schedules Direct) are never touched.
* Dispatcharr's `generate_custom_dummy_programs` renders the guide on demand using regex patterns + templates stored in the source's `custom_properties`:
  * **During the event window** (length = Event Duration hours, starting at the time extracted from the channel name in the configured Channel Name Event Timezone): the event title.
  * **Before the event window**: `Upcoming at <start-time>: <title>`.
  * **After the event window**: `Ended at <end-time>: <title>`.
  * **For names with no parseable time**: a 24-hour program with the channel name (fallback template).
* Toggling **Manage Dummy EPG** off cleanly unbinds every channel the plugin attached — on the next scan, `epg_data` is set to `None` for any channel still pointing at the managed source. The source row itself is preserved for cheap re-adoption.

## Action Reference

| Action | Style | Description |
| :--- | :--- | :--- |
| **🔎 Validate** | Outline blue | Test and validate all plugin settings before running. |
| **💾 Save Schedule** | Filled green | Save all settings and update/activate the scheduled run times. |
| **👁️ Dry Run** | Outline cyan | Preview which channels would be hidden or shown without making any changes. Pure preview — never creates/modifies the managed dummy EPG source. Runs synchronously; the button's loading spinner covers the busy state and a single notification appears on completion with a compact one-line summary (`Dry run: N channels \| X hide / Y show \| EPG +A/-D \| CSV: <file>`). Full details land in the CSV header and logs. |
| **▶️ Run Now** | Filled green, with confirm | Immediately scan and apply visibility updates based on the current EPG data. Same synchronous + compact-notification behavior as Dry Run. |
| **🧹 Remove EPG from Hidden** | Filled red, with confirm | Delete all EPG data from channels that are currently hidden/disabled in the selected profile(s). Destructive; requires confirmation. |
| **🗑️ Clear CSV Exports** | Filled red, with confirm | Delete all CSV export files created by this plugin to free up disk space. Requires confirmation. |
| **🧼 Cleanup Orphaned Tasks** | Outline orange, with confirm | Remove any orphaned Celery periodic tasks from old plugin versions. Requires confirmation. |
| **🩺 Check Scheduler** | Outline blue | Display scheduler status. Reports this worker's scheduler thread, configured times, the next upcoming run, container-wide last-run history (from shared file), and whether a scan is currently holding the cross-process lock. Because Dispatcharr runs under multiple uwsgi workers and each has its own scheduler thread, pressing the button twice may reach different workers — coordination is via shared files so each scheduled time fires exactly once regardless. |

## File Locations
* **Settings Cache**: `/data/event_channel_managarr_settings.json`
* **Last Run Results**: `/data/event_channel_managarr_results.json`
* **Last Run Tracker** (scheduled run history, cross-worker safe): `/data/event_channel_managarr_last_run.json`
* **Scan Lock** (cross-worker mutex): `/data/event_channel_managarr_scan.lock`
* **Undated-Channel Tracker** (for `[UndatedAge:N]`): `/data/event_channel_managarr_undated_first_seen.json`
* **Version Check Cache**: `/data/event_channel_managarr_version_check.json`
* **CSV Exports**: `/data/exports/event_channel_managarr_[dryrun|applied]_YYYYMMDD_HHMMSS.csv`
* **EPG Removal Reports**: `/data/exports/epg_removal_YYYYMMDD_HHMMSS.csv`

## CSV Export Format

### Header Lines

Every CSV includes a block of summary header lines (prefixed with `#`) before the column row:

```
# Event Channel Managarr v1.26.1081141 - Dry Run - 20260418_111247
# Total Channels Processed: 103
# Channels to Hide: 50
# Channels to Show: 0
# Channels Ignored: 0
# Duplicates Hidden: 0
# Managed EPG Attached: 0
# Managed EPG Detached: 0
# Rate Limiting: low
# Rule Effectiveness:
#   PastDate:0: 44 channels
#   EmptyPlaceholder: 5 channels
# Hide Rules Priority: [InactiveRegex],[BlankName],…
```

### Columns

| Column | Description |
| :--- | :--- |
| **channel_id** | Internal Dispatcharr channel ID. |
| **channel_name** | The full name of the channel. |
| **channel_number** | The channel number. |
| **channel_group** | The channel's group name. |
| **current_visibility** | The visibility status before the run (`Visible` or `Hidden`). |
| **action** | The action taken by the plugin (`Show`, `Hide`, `Visible`, `No change`, `Ignored`, `Forced Visible`). |
| **reason** | The reason for the action (e.g., "Event date… is 1 days in the past", "Duplicate channel", "No date in name; first seen …"). |
| **hide_rule** | The specific rule tag that triggered the hide action (e.g., `PastDate:0`, `UndatedAge:2`, `ShortDescription`). |
| **has_epg** | Indicates if an EPG is assigned to the channel (`Yes` or `No`). |
| **managed_epg_assigned** | `True` if this scan attached the channel to the plugin-managed dummy EPG source, else `False`. |
| **managed_epg_detached** | `True` if this scan detached the channel from the plugin-managed dummy EPG source, else `False`. |

## Troubleshooting

### General Issues
* **"Channel Profile not found"**: Ensure the name(s) entered in the settings exactly match the names in Dispatcharr. Check for typos or extra spaces if using multiple comma-separated names.
* **"No channels found…"**: Verify that the specified profile(s) have channels assigned and that the group names (if used) are spelled correctly.
* **Scheduler Not Running**: After changing the schedule, you must click **💾 Save Schedule** to save and activate it. Ensure the times are in `HHMM` format (e.g., `0700` for 7 AM).
* **Channels Aren't Hiding/Showing**: Run a **Dry Run** and check the `reason` and `hide_rule` columns for that channel. This will tell you exactly why a decision was made. You may need to adjust your **Hide Rules Priority** list.
* **"Another scan is already running"**: A cross-process lock prevents concurrent scans. Wait for the current scan to finish. Scheduled runs will skip cleanly when a manual scan is in progress.

### Managed Dummy EPG Issues
* **Guide still shows nothing for a channel after enabling Manage Dummy EPG**: Check the CSV; the channel is likely not in `enabled_channel_ids` post-scan (e.g., it was hidden by a rule). Only channels that end up visible are attached.
* **Guide shows the wrong time**: Verify the **Channel Name Event Timezone** setting matches the timezone encoded in channel names. This is separate from the scheduler timezone.
* **Want the managed source gone**: Toggle **Manage Dummy EPG** off and run a scan — every managed binding is detached. The source row itself stays in the DB (inert) for cheap re-adoption later.

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
```
