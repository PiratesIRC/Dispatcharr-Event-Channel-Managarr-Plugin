# Event Channel Managarr
A Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. It hides channels that currently have no event information and shows channels that do — with optional managed dummy EPG so the guide still shows something useful (event title during the window; "Upcoming at <time>: <title>" before; "Ended at <time>: <title>" after) for channels that never have real EPG assigned.

> [!TIP]
> **New to Dispatcharr plugins?** Start with the **[Dispatcharr Plugin Workflow guide](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/)**.
> It explains what each plugin and tool does, where they overlap, and what order to use them in.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
[![Workflow Guide](https://img.shields.io/badge/%F0%9F%93%96-Workflow_Guide-1F6FEB?style=flat)](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/workflow/05-event-channel-managarr/)
[![Discord](https://img.shields.io/badge/Discord-Discussion-5865F2?logo=discord&logoColor=white)](https://discord.gg/Sp45V5BcxU)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/PiratesIRC)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin?include_prereleases&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases)
[![Events surfaced](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/PiratesIRC/6d203e81e83657ee1cbc6e77f5c03d65/raw/event-channel-managarr-events.json)](#run-ledger)

<sub>The **events surfaced** badge is the number of event channels this plugin has switched from hidden to visible on the maintainer's own installation, counted from its run ledger and refreshed twice a day. It counts channels that actually changed, so a channel that stays visible across many scheduled runs is counted once, when it appeared. Channels hidden, channels merely scanned, and dry runs are all excluded, so the number is work done rather than activity. The ledger starts when it was first deployed rather than at the plugin's first release, so this is a total since then, not a lifetime one. It is one installation's total, not a project metric.</sub>

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)

## Features
* **Automatic Visibility Control**: Hides channels without active events and shows channels that have them. Scans ALL channels in the profile (both visible and hidden) to ensure channels with new events are always shown.
* **Prioritized Hide Rules**: A fully customizable, priority-based rule system. You define the order of rules (e.g., `[BlankName]`, `[PastDate:0]`, `[UndatedAge:2]`, `[ShortDescription]`) to determine *why* and *when* a channel should be hidden.
* **Undated-Channel Aging**: The `[UndatedAge:N]` rule tracks per-channel first-seen dates and hides channels whose names carry no parseable date once they've been visible for more than N days. Catches stale placeholder channels that date-only rules can't evaluate.
* **Managed Dummy EPG (new in v1.26.1081141)**: Opt-in. Visible channels with no EPG get bound to a plugin-managed dummy EPG source. Dispatcharr's guide then shows the extracted event title during its time window; before the window it shows `Upcoming at <start-time>: <title>`; after, `Ended at <end-time>: <title>`. For names with no parseable time, a 24-hour program rendering the channel name is used instead. Timezone-aware (channel name time is interpreted in the configured event timezone; the guide renders in the client's local time). The title parser also recognizes bare `EVENT ##:` names (no `PPV`/`LIVE` prefix required) as of v1.26.1711623.
* **Override Empty Existing EPG (new in v1.26.1711623)**: Opt-in companion to Manage Dummy EPG. Normally the managed dummy only attaches to channels with **no** EPG at all. Enable this to also take over visible channels already linked to a real EPG source that currently has **no programmes** (a blank guide) — e.g. event channels the provider mapped to an empty tvg-id. Channels whose linked EPG has real upcoming programmes are never touched. Default off.
* **US / SE Channel Name Formats (new in v1.26.1621359)**: A **📡 Channel Name Format** selector tells the dummy-EPG parser how event names are laid out. `US` handles `PPV/LIVE EVENT ##: Title (MM.DD HH:MM AM/PM TZ)`; `SE` handles Swedish pipe-delimited names (`PREFIX \| Title \| DDD DD Mon HH:MM TZ \| … \| channel name`, 24-hour time, textual month) and uses the broadcaster segment as the guide display name. See [Channel Name Formats](#channel-name-formats).
* **Per-timezone dummy EPG sources (new in v1.26.2241846)**: Channels whose names positively claim a timezone other than the default now get their own managed dummy EPG source for that zone, instead of being repeatedly pulled back onto the default one. Before this, such a channel's guide times were corrected and then un-corrected every few hours. Nothing to configure: it happens inside the managed pass, only for channels whose current EPG binding is safe to move, and a channel that claims nothing stays exactly where it was.
* **24-hour guide titles for the SE format (new in v1.26.2241846)**: With **📡 Channel Name Format** set to `SE`, the `Upcoming at …` and `Ended at …` guide titles now render times as `19:55` instead of `7:55 PM`, matching the 24-hour times the channel names themselves carry. `US` is unchanged and keeps 12-hour AM/PM. Contributed by [@hulidan](https://github.com/hulidan) in [#27](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/pull/27).
* **Clearer settings text, and no update checker (new in v1.26.2251616)**: Every field help text and action description was rewritten to say what the input format is, give an example, and state what leaving a box blank does. Three labels now carry the format themselves: **Channel Profile Names (Required, comma-separated)**, **Channel Groups (comma-separated)** and **Scheduled Run Times (24-hour, comma-separated)**. The **📦 Plugin Version Status** line was removed along with the code behind it, so the plugin makes no outbound network request at all. No setting was added, removed or renamed, and scanning behaviour is unchanged.
* **Stream Name Selection**: Choose between using the channel name or the stream name for rule matching. When stream name is selected, the plugin uses the first stream in the channel for all rule evaluations.
* **Date-Based Logic**: Use rules like `[PastDate:days]` and `[FutureDate:days]` to hide events that are over or too far in the future. Includes a **grace period** for events that run past midnight. When a name carries a clock time (e.g. `(6.19 7:30 PM ET)`), `[PastDate]` judges by the **actual event time** (start + Event Duration, in the configured event timezone) instead of the calendar day, so a still-live evening event isn't hidden the moment the date rolls past midnight (new in v1.26.1711623).
* **Enhanced Date Format Support**: Recognizes a wide variety of date formats in channel names, including dates with optional times (e.g., "Nov 8 16:00"), slash-separated dates, ISO formats, and more.
* **Day-of-Week Logic**: Use the `[WrongDayOfWeek]` rule to hide channels named for a specific day (e.g., "Saturday Night Fights") when it's not that day.
* **Multi-Profile Support**: Monitor and manage channels across **multiple Channel Profiles** at once (e.g., "PPV Events, Sports Profile").
* **Configurable Duplicate Handling**: Choose your strategy for handling duplicate events: keep the one with the **lowest number**, **highest number**, or **longest name**. Optionally keep all duplicate channels visible.
* **Direct Django ORM Integration**: Operates directly within Dispatcharr's Django environment for fast, reliable channel management without API overhead.
* **WebSocket Progress Updates**: Real-time adaptive progress notifications during scans via WebSocket.
* **Cross-Worker Safe**: A cross-process `fcntl` lock on the scan file ensures at most one scan runs at a time across all uwsgi workers, whether triggered by the scheduler or manually via Run Now / Dry Run. The lock self-heals: if a previous scan's lock is ever leaked or orphaned (e.g. inherited by a forked worker), a stale lock older than 15 minutes is automatically broken and re-acquired, so scans can never wedge permanently.
* **Configurable Rate Limiting (new in v1.26.1081141)**: Select `none` / `low` / `medium` / `high` to pace per-channel ORM writes (0 / 0.05 / 0.2 / 0.5 seconds each). Defaults to `none`; useful when scanning very large profiles on a small database.
* **Sectioned UI (new in v1.26.1081141)**: Settings are grouped into **Scope**, **Hide Rules**, **Duplicates**, **EPG Management**, **Scheduling & Export**, and **Advanced** sections for easier navigation.
* **Force Visibility**: Use a regular expression to **force specific channels** (like news or weather) to remain visible, overriding all hide rules.
* **Flexible Scheduling**: Run scans automatically at specific times each day (e.g., `0600,1300,1800`). Scheduled-run and guide-display times use Dispatcharr's global time zone (General Settings → Time Zone).
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

> **Submitting upstream?** See [CONTRIBUTING](#contributing) for the upstream `Dispatcharr/Plugins` PR requirements (title format, version bump, etc.).

## Settings Reference

Settings are grouped into six sections in the UI.

### 📍 Scope

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **📺 Channel Profile Names (Required, comma-separated)** | `text` | — | Channel Profile(s) to monitor. Use comma-separated names for multiple profiles. |
| **📂 Channel Groups (comma-separated)** | `text` | — | Comma-separated group names to monitor. Leave empty for all groups in the profile(s). Matched **case-insensitively** (as of v1.26.1711623). ⚠️ **Separate these with commas, not `\|`.** The `\|` character belongs only in the three regex fields below; using it here glues several group names into one name that matches nothing, silently dropping them from the scan. Any configured group name that matches no channels is now named in the result message and the CSV header on **every** run, not only when the scan finds nothing at all. |
| **🔤 Name Source** | `select` | `Channel_Name` | Choose the source for rule matching: `Channel_Name` uses the channel name, `Stream_Name` uses the first stream's name in the channel. |

### 🎯 Hide Rules

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **📜 Hide Rules Priority** | `text` | (see default) | Define rules for hiding channels in priority order. First match wins. See "Hide Rule Logic" below. |
| **🚫 Regex: Channel Names to Ignore** | `text` | — | Regular expression to match channel names that should be skipped entirely. |
| **💤 Regex: Mark Channel as Inactive** | `text` | — | Regular expression to hide channels. Processed as part of the `[InactiveRegex]` hide rule. |
| **✅ Regex: Force Visible Channels** | `text` | — | Regular expression to match channels that should ALWAYS be visible, overriding any hide rules. Use this for year-round channels that sit in an event group, such as a league's RedZone or Network feed, which carry no date and are otherwise hidden by `[UndatedAge:N]`. |

**All three regex fields above are matched against the channel or stream name only** (whichever the **Name Source** setting selects). They are never matched against guide programme titles or descriptions, so text copied out of the TV Guide will not match anything. Separate alternatives with `|`, for example `NFL REDZONE|NFL NETWORK`. A regex field that you have filled in but that matched no channels is reported in the result message and counted in the CSV header under **Regex Field Matches**, so a pattern that can never fire is visible rather than looking like one that works.
| **📅 Past Date Grace Period (Hours)** | `number` | `4` | Extra hours to wait before hiding a past event, used by the `[PastDate]` rule. For day-only names this is the wait after midnight; for names carrying a clock time it is added on top of the event's start + Event Duration. |

### 🎭 Duplicates

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🎭 Duplicate Handling Strategy** | `select` | `lowest_number` | Strategy to use when multiple channels have the same event. |
| **🔄 Keep Duplicate Channels** | `boolean` | `False` | If enabled, duplicate channels will be kept visible instead of being hidden. The duplicate strategy above will be ignored. |

### 🔌 EPG Management

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🔌 Auto-Remove EPG on Hide** | `boolean` | `True` | If enabled, **removes** EPG data from a channel when the plugin hides it (clears stale guide data). This does *not* create EPG — to give visible channels a placeholder guide use **🗓️ Manage Dummy EPG** below. |
| **🗓️ Manage Dummy EPG** | `boolean` | `False` | The setting that **creates** guide data: visible channels with no EPG get bound to the plugin-managed dummy EPG source. Disables cleanly: toggling off detaches all channels from the managed source on the next scan. |
| **♻️ Override Empty Existing EPG** | `boolean` | `False` | Requires **🗓️ Manage Dummy EPG**. Lets the managed dummy also take over visible channels already linked to a real (non-managed) EPG source that currently has **no programmes** in the next 24h (a blank guide). Channels whose linked EPG has real upcoming programmes are never touched. Default off so real EPG is never overwritten unless you opt in. (new in v1.26.1711623) |
| **📡 Channel Name Format** | `select` | `US` | How channel names are structured for the dummy EPG parser. `US` = `PPV/LIVE EVENT ##: Title (MM.DD HH:MM AM/PM TZ)`, bare `EVENT ##: Title (…)` (no `PPV`/`LIVE` prefix required, as of v1.26.1711623), and a bare slot number followed by a date or a time such as `07 - 8/14 7pm Broncos at Falcons` (v1.26.2261346). `SE` = pipe-delimited `PREFIX \| Title \| DDD DD Mon HH:MM TZ \| extras \| channel name` (24-hour time, textual month); the last pipe segment (e.g. `SE: VIAPLAY PPV 20`) is stored as the EPG display name so the guide's channel list shows the broadcaster instead of the full stream name. |
| **⏱️ Event Duration (hours)** | `number` | `3` | How long each scheduled event appears in the guide. Before this window the guide shows `Upcoming at <start-time>: <title>`; after, `Ended at <end-time>: <title>`. |
| **📺 Channel Name Event Timezone** | `select` | `US/Eastern` | Timezone encoded in event times within channel names (e.g., `US/Eastern` for channels like `(4.17 8:30 PM ET)`). Independent of Dispatcharr's global time zone. |

### ⏰ Scheduling & Export

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **⏰ Scheduled Run Times (24-hour, comma-separated)** | `text` | — | Comma-separated times (24-hour HHMM format) to run daily. Leave blank to disable. |
| **📄 Enable Scheduled CSV Export** | `boolean` | `False` | If enabled, a CSV report will be created when the plugin runs on a schedule. |
| **🔄 Auto-rescan after M3U refresh** | `boolean` | `False` | If enabled, the plugin re-runs its visibility scan automatically after each M3U account refresh. Dispatcharr's Auto Channel Sync re-enables (un-hides) channels in synced groups on every refresh; this re-hides them right after. Leave off if you do not use Auto Channel Sync. |

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
    * Pick the **Channel Name Format** (`US` or `SE`) that matches how your provider names event channels — see [Channel Name Formats](#channel-name-formats) below.
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
| **[WrongDayOfWeek]** | — | Hides if the name contains a day name (e.g., "MONDAY", "Mon", "Saturday", "Sat") and the named day is not yesterday, today, or tomorrow in Dispatcharr's time zone. The ±1 day tolerance keeps US/EU named channels visible to viewers in distant timezones (e.g., Australia seeing "Monday Night Football" on local Tuesday). Recognizes full/abbreviated day names plus MNF/TNF/SNF. |
| **[NoEventPattern]** | — | Hides if the name contains patterns like "no event", "offline", "no games scheduled". |
| **[EmptyPlaceholder]** | — | Hides if the name ends with a separator (`:`, `\|`, `-`) and has no event title after it, OR if the name contains a parenthesized literal template token like `(MM.DD h:mmAM/PM ET)` indicating an unpopulated stub channel. |
| **[ShortDescription]** or **[ShortDescription:chars]** | optional `chars` (int) | Hides if the event title (the text after a `:`, `\|` or ` - ` separator) is shorter than `chars` characters. Defaults to **15** when no number is given, which is the value this rule always used. Give it a number to move the line: on one provider's channels `NCAAF 25: FS1 [1080p]` is hidden (11 characters after the colon) while `NCAAF 26: SEC NETWORK [1080p]` stays visible (19), and `[ShortDescription:25]` would catch both. The threshold applied is recorded in the CSV `reason` column. |
| **[ShortChannelName]** or **[ShortChannelName:chars]** | optional `chars` (int) | Hides if the *entire name* is shorter than `chars` characters and has *no* separator. Defaults to **25** when no number is given, which is the value this rule always used. |
| **[NumberOnly]** | — | Hides if the channel name is just a prefix followed by a number (e.g., "PPV 12", "EVENT 15") with no event details. |
| **[PastDate:days]** or **[PastDate:days:Xh]** | `days` (int), optional `Xh` (grace hours) | Hides if the name contains a date that is more than `days` in the past (e.g., `[PastDate:0]` hides yesterday's events). Optionally specify grace period inline like `[PastDate:0:4h]` to override the global grace period setting. **Time-aware matching (v1.26.1711623):** if the name carries an explicit `stop:YYYY-MM-DD HH:MM:SS` end timestamp, the rule compares the **actual end time** (`stop:` + `days`/grace); if it carries a clock time but no `stop:` (e.g. `(6.19 7:30 PM ET)`), the event is assumed to end **Event Duration hours** after its start (localized in the **Channel Name Event Timezone**), and the rule hides it once that end + `days`/grace has elapsed. Day-only names (no parseable time) keep the original calendar-day behavior. |
| **[FutureDate:days]** | `days` (int) | Hides if the name contains a date that is more than `days` in the future (e.g., `[FutureDate:2]` hides events 3+ days from now). "Today" is resolved in Dispatcharr's time zone, consistent with the other date rules (v1.26.1711623). |
| **[UndatedAge:days]** | `days` (int) | Hides channels whose names contain **no parseable date** once they've been visible for more than `days` days. Persists per-channel first-seen state in `/data/event_channel_managarr_undated_first_seen.json`. Resets a channel's age when its name changes. |
| **[InactiveRegex]** | — | Hides if the name matches the `Regex: Mark Channel as Inactive` setting. |

#### How far ahead should a channel appear? (`[FutureDate:days]`)

This is the most commonly misread rule, because a channel named for a game that has not started yet is *supposed* to be visible under the default.

`[FutureDate:days]` hides a channel only when its date is **more than** `days` ahead. With the default `[FutureDate:2]`, a channel named `NFL : 15 - 8/22 10pm Cowboys at Cardinals` is visible all day on 8/20, 8/21 and 8/22, and is hidden only while the date is three or more days out. If you see event channels with no game on right now, check their names: if the date is tomorrow, this is the rule working as configured.

| You want | Use |
| :--- | :--- |
| The channel visible only on the day of the event | `[FutureDate:0]` |
| Visible from the day before | `[FutureDate:1]` |
| Visible up to two days ahead (default) | `[FutureDate:2]` |

`[PastDate:0]` handles the other end, removing the channel once the event has finished plus the **Past Date Grace Period**.

### Duplicate Handling
To prevent multiple versions of the same event from being visible, the plugin:
1.  Normalizes channel names *and* event descriptions (e.g., "PPV 1: UFC" and "PPV 2: UFC" are duplicates, but "PPV 1: UFC" and "PPV 1: Boxing" are not).
2.  Groups all channels with the same normalized event.
3.  Within a group, it keeps only one channel visible based on your selected **Duplicate Handling Strategy** and hides all others.

### Supported Date Formats
The plugin can extract dates from channel names in the following formats (checked in priority order):

| Format | Example | Notes |
| :--- | :--- | :--- |
| **start:YYYY-MM-DD HH:MM:SS[ AM/PM]** | `start:2024-12-25 20:00:00` or `start:2024-12-25 08:00:00 PM` | Highest priority. Matches exact datetime in channel name. A trailing 12-hour `AM`/`PM` is optional and converted to 24-hour time. |
| **stop:YYYY-MM-DD HH:MM:SS[ AM/PM]** | `stop:2024-12-25 23:00:00` or `stop:2024-12-25 11:00:00 PM` | Matches end datetime in channel name. Optional `AM`/`PM` supported. |
| **(YYYY-MM-DD HH:MM:SS[ AM/PM])** | `(2025-11-22 15:10:00)` or `(2026-05-01 02:20:00 PM)` | Matches datetime within parentheses. A trailing 12-hour `AM`/`PM` is optional (e.g. `02:20:00 PM` → 14:20); `12 AM` → 00:00, `12 PM` → 12:00. |
| **M/D/YYYY** or **M/D/YY** | `12/25/2024` or `15/04/2026` | Slash-separated date with year. Interpreted per the **Date Format in Channel Names** setting (Auto / US / EU). |
| **(MONTH DD)** | `(Dec 25)` or `(December 25)` | Month name and day in parentheses. |
| **DDth/st/nd/rd MONTH** | `25th Dec` or `1st January` | Day with ordinal suffix followed by month name. |
| **MONTH DD[ HH:MM[:SS][ AM/PM]]** | `Dec 25`, `Nov 8 16:00`, or `Jun 20 4:00 PM` | Month name followed by day (no parentheses), with an optional time. A 24-hour time works as before; a 12-hour time with `AM`/`PM` is now honored and optional seconds are tolerated (v1.26.1711623). |
| **YYYY MM DD** | `2024 12 25` | Space-separated year, month, day. |
| **M.D[ h:mm AM/PM]** | `12.25` or `6.19 7:30 PM` | Dot-separated date (assumes current year). Interpreted per the **Date Format in Channel Names** setting. An optional trailing 12-hour time is captured and attached to the date (v1.26.1711623), so `[PastDate]` can judge by the real event time. |
| **M/D[ h:mm AM/PM]** | `12/25` or `6/19 8:00 PM` | Slash-separated date (assumes current year). Interpreted per the **Date Format in Channel Names** setting. Skipped when followed by a colon (e.g. `1/3:30pm`) so time ranges aren't misread as dates. An optional trailing 12-hour time is captured as above. |

**Date format setting:** The 📅 **Date Format in Channel Names** setting (default `Auto`) controls how numeric `M/D`, `M.D`, and `M/D/YYYY` patterns are interpreted:

* **Auto (recommended)** — try MM/DD first; if the month is invalid (> 12), retry as DD/MM. Handles most regional data without configuration.
* **US (MM/DD)** — always month first. Use this if you want to force US-style parsing (e.g. ambiguous `04/05` always means April 5).
* **EU (DD/MM)** — always day first (e.g. `15/04` = April 15, ambiguous `04/05` means May 4).

**Note:** When using `[PastDate]` or `[FutureDate]` rules, the plugin will attempt to extract a date using these formats. If no date is found, the rule will not match and the next rule in your priority list will be checked. The `[UndatedAge]` rule handles the "no date found" case directly.

## Managed Dummy EPG

When **🗓️ Manage Dummy EPG** is enabled:

* A single plugin-managed `EPGSource(source_type='dummy', name='ECM Managed Dummy')` row is created on first use.
* Visible channels in the monitored profile(s) with **no EPG assigned** are bound to it via a per-channel `EPGData` row keyed by `channel.uuid`.
* Channels that already have a real EPG binding (XMLTV, Schedules Direct) are never touched — **unless** you enable **♻️ Override Empty Existing EPG**, which extends the takeover to channels linked to a non-managed source that has **no programmes in the next 24h** (a blank guide). Channels whose linked EPG has real upcoming programmes are still never touched. (v1.26.1711623)
* The title parser matches `PPV EVENT ##:`, `LIVE EVENT ##`, bare `EVENT ##:` names (the `PPV`/`LIVE` prefix is optional as of v1.26.1711623), and, as of v1.26.2261346, a bare slot number with no keyword at all when it is followed by a separator and then a date or a time (`07 - 8/14 7pm Broncos at Falcons`). A number that is *not* followed by a date or a time is still ignored, so an ordinary channel called `60 Minutes` keeps its full name.
* When the managed EPG is detached from a channel, its now-unreferenced managed `EPGData` row is deleted, so the managed source doesn't accumulate orphan rows over time (v1.26.1711623).
* The detach is **scoped to the groups you actually scan** (v1.26.1711720): running with a narrow **Channel Groups** filter only de-manages channels in those groups and never strips the managed dummy off channels in other groups. Toggling **Manage Dummy EPG** off still performs a full teardown across the whole source.
* **Channels that claim a different timezone get their own source** (v1.26.2241846). A channel whose name positively claims a timezone other than the configured default is re-pointed onto a managed dummy source dedicated to that zone, created on first use. Previously every managed channel shared one source, so a channel needing different guide times was corrected and then reclaimed by the default source within hours, repeatedly. The step runs at the end of the managed pass, moves a channel only when its current EPG binding is safe to move, and leaves any channel that claims nothing exactly where it is. Sources left with no channels are reaped.
* **The `Upcoming at …` and `Ended at …` times follow the channel-name format** (v1.26.2241846). Dispatcharr's dummy renderer offers both 12-hour and 24-hour placeholders and never chooses between them itself, so the plugin picks: `SE` uses the 24-hour form to match the 24-hour times its channel names carry, `US` keeps 12-hour AM/PM. Setting a display timezone converts the *instant*, not the clock format, so it can never produce a 24-hour title on its own.
* Dispatcharr's `generate_custom_dummy_programs` renders the guide on demand using regex patterns + templates stored in the source's `custom_properties`:
  * **During the event window** (length = Event Duration hours, starting at the time extracted from the channel name in the configured Channel Name Event Timezone): the event title.
  * **Before the event window**: `Upcoming at <start-time>: <title>`.
  * **After the event window**: `Ended at <end-time>: <title>`.
  * **For names with no parseable time**: a 24-hour program with the channel name (fallback template).
* Toggling **Manage Dummy EPG** off cleanly unbinds every channel the plugin attached — on the next scan, `epg_data` is set to `None` for any channel still pointing at the managed source. The source row itself is preserved for cheap re-adoption.

### ⚠️ Guide titles come from the CHANNEL name, even when Name Source is Stream Name

This catches out anyone whose provider puts the event details in the **stream** name while the **channel** name stays fixed, for example a channel called `NFL : 15 - [1080p]` fed by a stream called `NFL : 15 - 8/22 10pm Cowboys at Cardinals [1080p]`.

Setting **Name Source** to `Stream_Name` changes what **this plugin's hide rules read**, and nothing else. Dispatcharr renders dummy guide entries itself, from the channel's own name, and a stream name is never available to it. So in that setup:

* **Hiding and showing work correctly.** The rules see the game, the date and the time.
* **The guide entry cannot show the game.** The channel name does not match the event title pattern, so the renderer falls back to the channel name plus the static description `Live event — guide information is currently unavailable.`

This is a limit of dummy guide data, not a fault, and re-running a scan will not change it. To get event titles into the guide, the **channel** names have to carry the event text, which is a Dispatcharr channel-naming matter rather than a plugin setting.

### Channel Name Formats

The **📡 Channel Name Format** setting tells the parser how event titles, times, and dates are laid out in your channel names. The parser ships regex defaults for each format and stores them on the managed source's `custom_properties` (you can override them in Dispatcharr's Pattern Configuration; the plugin only auto-refreshes patterns you haven't customized).

| Format | Example channel name | Parsed title | Notes |
|---|---|---|---|
| **US** (default) | `PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)` | `Cage Fury FC 153` | 12-hour AM/PM time, numeric `MM.DD` date. Also handles `LIVE EVENT ##`, leading-time variants, bare `EVENT ##: Title (…)` with no `PPV`/`LIVE` prefix (v1.26.1711623), and a bare slot number followed by a date or a time, such as `07 - 8/14 7pm Broncos at Falcons` (v1.26.2261346). |
| **SE** | `LIVE \| GIRONA - REAL SOCIEDAD \| Thu 14 May 19:55 CEST (SE) \| 8K EXCLUSIVE \| SE: TV4 PLAY PPV 7` | `GIRONA - REAL SOCIEDAD` | Pipe-delimited; 24-hour time, textual month (`14 May`). The **last** pipe segment (`SE: TV4 PLAY PPV 7`) becomes the EPG display name, so the guide's channel list shows the broadcaster rather than the full stream name. |

* **SE display names resync every run.** Because the broadcaster segment can change between M3U refreshes, SE mode re-checks and updates `EPGData.name` for already-attached channels on each scan (US mode only sets it on first attach).
* **Switching formats** auto-refreshes the stock patterns (both formats' historical defaults are recognized), so changing `US` ⇄ `SE` and re-scanning picks up the right patterns without manual edits — unless you've customized a pattern, which is always preserved.

### Localized Time in EPG Titles

When **`Event Timezone`** (`dummy_epg_event_timezone`) and **Dispatcharr's global time zone** (General Settings → Time Zone) are different, ECM rewrites the dummy EPG titles to show the program's local time and zone abbreviation:

| Setup | Channel name | Title in guide |
|---|---|---|
| Event TZ `US/Eastern`, Dispatcharr TZ `America/Chicago` (DST active, e.g., May) | `Boxing 5/9 8:30 PM ET` | `Boxing 5/9 7:30 PM CDT` |
| Same setup, standard time (e.g., November) | `Boxing 11/9 8:30 PM ET` | `Boxing 11/9 7:30 PM CST` |
| Event TZ == Dispatcharr TZ | (any) | `Boxing` (plain) |

**The display time zone comes from Dispatcharr's General Settings → Time Zone** (it also drives when scheduled runs fire and the day-of-week/date rules). When that is unset, ECM falls back to `UTC`, so EPG titles will show UTC times until you set a zone in Dispatcharr.

**DST caveat:** the abbreviation (`CST` vs `CDT`) is recomputed every time ECM runs. If you disable scheduling and don't trigger a manual run after a DST transition, the abbreviation will be stale (the *time itself* is always correct, only the trailing label lags). Run ECM once after a DST change to refresh.

**Date format inside titles** follows your existing `Date Format` setting:
- `US` or `Auto` → `M/D` (e.g., `5/9`)
- `EU` → `D/M` (e.g., `9/5`)

**Numeric-offset zones** (e.g., `Etc/GMT+5`) suppress the abbreviation suffix — ECM still converts the time but writes no trailing label, since `+0500` would look wrong in a title.

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
* **Run Ledger**: `/data/event_channel_managarr_ledger.jsonl` (rotates once to `.jsonl.1` at 5 MB)
* **CSV Exports**: `/data/exports/event_channel_managarr_[dryrun|applied]_YYYYMMDD_HHMMSS.csv`
* **EPG Removal Reports**: `/data/exports/epg_removal_YYYYMMDD_HHMMSS.csv`

### Run Ledger

An append-only record of what applied runs actually **changed**, one JSON line each:

```json
{"ts": "2026-08-22T16:45:00+00:00", "version": "1.26.2341433", "shown": 11, "hidden": 100, "scheduled": true}
```

`shown` and `hidden` are **transition** counts: channels whose visibility actually flipped on that run. They are not the number of channels currently visible or hidden, and not the number processed. The plugin looks at every channel in scope on every scheduled run, so a running total of anything other than transitions would re-count the same channel indefinitely.

A line is written only after the database transaction that applied the changes has committed. **Dry runs never write a line**, and neither does an applied run that found nothing to change, so every line represents real work. A failure to write the ledger is logged as a warning and never fails a scan.

Nothing in the plugin reads this file; it exists so a running total can be reported without re-deriving it from the CSV exports, which the **🗑️ Clear CSV Exports** action deletes and which scheduled runs write only when **📄 Enable Scheduled CSV Export** is enabled.

## CSV Export Format

### Header Lines

Every CSV includes a block of summary header lines (prefixed with `#`) before the column row. After the counts and rule effectiveness, a `Settings:` snapshot records the configuration the scan ran with, so each export is self-describing. The display/scheduler time zone is sourced from Dispatcharr's General Settings → Time Zone and is reported as `timezone (from Dispatcharr)`:

```
# Event Channel Managarr v1.26.1711720 - Applied - 20260620_182324
# Total Channels Processed: 489
# Channels to Hide: 55
# Channels to Show: 0
# Channels Ignored: 0
# Duplicates Hidden: 0
# Managed EPG Attached: 73
# Managed EPG Detached: 0
# Rate Limiting: none
# Rule Effectiveness:
#   NoEventPattern: 20 channels
#   UndatedAge:2: 19 channels
#   EmptyPlaceholder: 13 channels
#   PastDate:0: 2 channels
#   WrongDayOfWeek: 1 channels
# Hide Rules Priority: [InactiveRegex],[BlankName],…
# Settings:
#   timezone (from Dispatcharr): America/Chicago
#   channel_profile_name: …
#   …
#   dummy_epg_event_timezone: America/New_York
#   scheduled_times: 0400,1000,1100,1200
#   enable_scheduled_csv_export: False
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
| **hide_rule** | The specific rule tag that triggered the hide action (e.g., `PastDate:0`, `UndatedAge:2`, `ShortDescription:15`). Rules with a threshold report it here, so `[ShortDescription]` and `[ShortChannelName]` now appear as `ShortDescription:15` and `ShortChannelName:25` rather than bare names. If you group or filter CSV rows by this column, expect that change. |
| **has_epg** | Indicates whether an EPG source is *linked* to the channel (`Yes` or `No`) — note this reflects linkage, not whether that source actually has programmes. Reconciled with this run's attach/detach, so a channel attached this run reads `Yes` and one detached reads `No` (v1.26.1711623). |
| **managed_epg_assigned** | `True` if this scan attached the channel to the plugin-managed dummy EPG source, else `False`. |
| **managed_epg_detached** | `True` if this scan detached the channel from the plugin-managed dummy EPG source, else `False`. |

## Client Setup (Jellyfin, Plex, Emby)

Hiding a channel changes what Dispatcharr **serves**. It does not reach into a client that has already imported a channel list, and it only affects the outputs that are scoped to a profile. Most "the plugin isn't hiding anything" reports turn out to be one of the three points below.

### 1. Point the client at the PROFILE's URLs, not the default ones

Open Dispatcharr's **Channels** page, select the profile the plugin manages in the dropdown at the top, and copy the links from there. They look like this, where `Sports` is the profile name:

| Client field | URL to use |
| :--- | :--- |
| Tuner / M3U playlist | `http://<dispatcharr>:<port>/output/m3u/<Profile>` (or the HDHR link, `/hdhr/<Profile>`) |
| Guide / XMLTV provider | `http://<dispatcharr>:<port>/output/epg/<Profile>` |

Both of these exclude channels the plugin has hidden in that profile. The unscoped default URLs do not.

**The single most common mistake is putting the M3U URL into the guide provider field.** An XMLTV parser cannot read an M3U playlist, so the client ends up with no guide data at all and keeps showing whatever channel list it imported previously. The guide URL contains `/output/epg/`, not `/output/m3u/`.

### 2. Refresh the client's guide

Clients cache what they imported. Until the client refreshes, it will keep listing channels Dispatcharr has already stopped serving.

* **Jellyfin**: Dashboard → Scheduled Tasks → **Refresh Guide**. Setting this to run every 1 to 2 hours is reasonable; there is no benefit in going below the plugin's own run frequency.
* **Plex**: DVR settings → refresh the guide.

If hidden channels survive a guide refresh, remove the tuner in the client and add it again, which rebuilds its channel list from scratch.

### 3. A third-party bridge may bypass all of this

The hides exist only in Dispatcharr's own profile-scoped outputs. If a plugin or proxy in front of Dispatcharr (an Xtream-codes bridge, for example) builds its channel list from somewhere else, nothing this plugin does can reach it. Test with the two URLs above directly before assuming the plugin is at fault.

### Checking your work inside Dispatcharr

Dispatcharr's own **TV Guide** page has a profile filter. With it set to **All Profiles** the page deliberately shows every channel, hidden or not, so a correctly hidden channel still appears there. Select the managed profile to see what your clients actually receive.

## Troubleshooting

### General Issues
* **"Channel Profile not found"**: Ensure the name(s) entered in the settings exactly match the names in Dispatcharr. Check for typos or extra spaces if using multiple comma-separated names.
* **"No channels found…"**: Verify that the specified profile(s) have channels assigned and that the group names (if used) are spelled correctly. Run **🔎 Validate** — as of v1.26.1711720 it distinguishes a misspelled group ("not found in Dispatcharr") from a real group that simply has no channels in the selected profile(s) ("will match 0 this scan"), and matches profile names case-insensitively (consistent with Run Now). Group names in the **Channel Groups** box are also matched case-insensitively.
* **Scheduler Not Running**: After changing the schedule, you must click **💾 Save Schedule** to save and activate it. Ensure the times are in `HHMM` format (e.g., `0700` for 7 AM).
* **One of my scheduled times never runs**: Valid entries are `0000` to `2359`. **Midnight is `0000`, not `2400`** — `2400` is four digits but is not a real time, and it used to be discarded without a word, so that run simply never happened. Both **🔎 Validate** and **💾 Save Schedule** now name any entry they are going to ignore, and Validate judges the times with the same parser the scheduler arms itself from, so the two can no longer disagree.
* **Fewer channels were processed than I expected / a group I listed did nothing**: Check the result message and the CSV header for **"Channel Groups that matched no channels"**. The usual cause is separating group names in **Channel Groups** with `|` instead of commas: that field is comma-separated, `|` belongs only in the three regex fields, and using it glues several real group names into one name that exists nowhere. Group names are matched case-insensitively, but the spelling must match.
* **A regex field I filled in seems to do nothing**: The CSV header's **Regex Field Matches** block counts what each one matched, and a field that matched zero channels is called out in the result message. The three regex fields are matched against the channel or stream name only, never against guide programme titles, so text copied out of the TV Guide will never match. Filler text such as a "guide information is currently unavailable" line is a programme description generated by Dispatcharr, not a channel name.
* **Event channels are visible when nothing is playing right now**: Look at the channel names. If they carry a date in the next day or two, this is `[FutureDate:2]` behaving as configured — see "How far ahead should a channel appear?" above, and use `[FutureDate:0]` if you want a channel to appear only on the day of its event.
* **Two similar channels get different treatment and it looks arbitrary**: Check the `reason` column in the CSV. `[ShortDescription]` measures the characters after the separator against a cutoff, so `NCAAF 25: FS1 [1080p]` (11 characters after the colon) is hidden while `NCAAF 26: SEC NETWORK [1080p]` (19) is not. The reason line reports both the measured length and the cutoff. Move the cutoff by writing a number in the tag, for example `[ShortDescription:25]`, or drop the tag from **Hide Rules Priority** if the rule does not suit your channel names.
* **A year-round channel in an event group keeps getting hidden**: Channels like a league's RedZone or Network feed carry no date, so `[UndatedAge:N]` hides them once they have been around for N days. Put them in **✅ Regex: Force Visible Channels**, for example `NFL REDZONE\|NFL NETWORK`.
* **Hidden channels still show in Dispatcharr's TV Guide page**: That page's profile filter is probably set to **All Profiles**, which shows every channel by design. Select the managed profile instead.
* **A run reports "Channels to Hide: 0" and "Channels to Show: 0"**: That reads identically to a run where no channels entered scope at all, so check scope before rules. In order: does the group exist and have channels; do those channels have a membership row in the configured **profile** (a channel with no membership row is invisible both to Dispatcharr and to this plugin); and only then look at the hide rules. A group named in your settings but absent from the CSV means zero channels in scope, not zero changes.
* **Channels Aren't Hiding/Showing**: Run a **Dry Run** and check the `reason` and `hide_rule` columns for that channel. This will tell you exactly why a decision was made. You may need to adjust your **Hide Rules Priority** list.
* **"Another scan is already running"**: A cross-process lock prevents concurrent scans. Wait for the current scan to finish. Scheduled runs will skip cleanly when a manual scan is in progress.
* **Hidden channels reappear after a while / after an M3U refresh**: Dispatcharr's **Auto Channel Sync** re-enables every channel in a synced group on each M3U refresh, overriding the plugin's hide. To fix this, enable **🔄 Auto-rescan after M3U refresh** (in the **⏰ Scheduling & Export** section) so the plugin re-runs its scan automatically right after each M3U refresh and re-hides affected channels. Alternatively, turn off Auto Channel Sync for the managed groups in Dispatcharr's M3U account settings.

### Managed Dummy EPG Issues
* **Guide still shows nothing for a channel after enabling Manage Dummy EPG**: Check the CSV. Two common causes: (1) the channel didn't end up **visible** post-scan (e.g., a rule hid it) — only visible channels are attached; or (2) `has_epg` is `Yes` but `managed_epg_assigned` is `False`, meaning the channel is **already linked to another EPG source** that simply has no programmes for it. By default the managed dummy never overwrites an existing link — enable **♻️ Override Empty Existing EPG** to let it take over channels whose linked EPG is blank, then re-run a scan. (Alternatively, clear that channel's EPG in Dispatcharr so it has none, and re-run.)
* **Guide shows the wrong time**: Verify the **Channel Name Event Timezone** setting matches the timezone encoded in channel names, and that Dispatcharr's **General Settings → Time Zone** is set to your display zone (ECM uses it for guide display; it falls back to UTC when unset).
* **Swedish (pipe-delimited) channels show no title, or the wrong guide name**: Set **📡 Channel Name Format** to `SE` and re-run a scan. `SE` parses `… \| Title \| DDD DD Mon HH:MM TZ \| … \| channel name` and stores the last pipe segment as the broadcaster display name. If you'd left it on `US`, the PPV/LIVE pattern won't match and the channel falls back to its plain name. (Switching formats auto-refreshes the patterns unless you've customized them.)
* **Want the managed source gone**: Toggle **Manage Dummy EPG** off and run a scan — every managed binding is detached. The source row itself stays in the DB (inert) for cheap re-adoption later.
* **Guide shows the literal text `{channel_name}` as the programme title** (e.g. in Emby/Jellyfin EPG): **fixed.** This affected managed channels whose names don't match the event title pattern, so they fall back to `fallback_title_template`. Dispatcharr's dummy-EPG renderer uses that template *verbatim* — it never substitutes `{channel_name}` (the description only showed the real name because ECM left the description template empty, triggering the renderer's built-in default). ECM now sets `fallback_title_template = ""`, which makes the renderer fall back to the real channel name, plus a static `fallback_description_template`. If you still see the literal text, re-run a scan so the plugin rewrites the managed source's templates, then refresh your EPG.
* **Guide shows literal `{month}/{day}` or `{starttime}` in the programme title** (e.g. `GOBI Live From Coachella 2026 {month}/{day} {starttime} CDT`): **fixed.** This hit *matched* event channels whose name has no parseable date **and** time (a bare year like `2026` is not a date). The timezone-localized title template embedded those placeholders, and the renderer leaves any placeholder it can't fill as literal text. ECM now uses a plain `{title}` for the live title — timed channels still land in the correct, timezone-converted guide slot, and the `Upcoming…`/`Ended…` titles keep the localized date/time (they only render when a date and time were parsed). A related fix lets the event-number separator be `-` (e.g. `LIVE EVENT 31 - GOBI …`) so the leading `- ` no longer leaks into the title. Re-run a scan and refresh your EPG if you still see the old behavior.
* **My channel names don't match the default pattern**: the default `title_pattern` matches `PPV EVENT ##`, `LIVE EVENT ##`, bare `EVENT ##:` names (the `PPV`/`LIVE` prefix is optional as of v1.26.1711623), and a bare slot number followed by a separator and then a date or a time, such as `07 - 8/14 7pm Broncos at Falcons` (v1.26.2261346). Other formats that lack both the `EVENT` keyword and a date or time after the number (e.g. `USA NBA 01: …`, `Pay Per View 19: …`) still fall back to the plain channel name. You can set your own regex in Dispatcharr under **EPG Sources → ECM Managed Dummy → Pattern Configuration**. **Your custom `title_pattern` / `time_pattern` / `date_pattern` now persist across plugin runs** — the plugin no longer overwrites a pattern you've changed (it only refreshes patterns left at a default it shipped). Use JS-style named groups `(?<title>…)` / `(?<hour>…)` / `(?<month>…)` (the UI validator rejects Python-style `(?P<…>)`; Dispatcharr converts JS groups server-side). The date group must be named `month` even when matching a text month (e.g. `(?<month>Jan|Feb|…)`).

### CSV Export Issues
* Ensure `/data/exports/` directory exists and is writable
* Check available disk space
* Verify no permission issues with the Dispatcharr data directory

## Updating the Plugin

The plugin no longer checks GitHub for a newer release, and the settings page no longer shows a
version status line. Earlier versions checked on every settings-page render, which made the page
depend on outbound internet access; the check was moved behind a button in v1.26.2241846 and removed
outright afterwards. The installed version is shown by Dispatcharr on the Plugins page, and releases
are listed on the GitHub releases page and in the Dispatcharr Plugin Hub.

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

## Disclaimer

**Event Channel Managarr provides no television content of any kind.** It supplies no channels, no
playlists, no streams, no electronic programme guide data and no provider accounts, and it contains
no list of where to obtain any of those. It bundles no reference data at all: everything it works on
already exists in **your** Dispatcharr installation.

What it reads is channel *names* and the programme data already stored against your channels. It
parses event titles, dates and times out of those names in order to decide which channels currently
have an event. **It never opens, reads, decodes, records, restreams or redistributes a stream**, and
it never reads a stream URL. It makes exactly one outbound network request, and only when you click
**Validate Configuration**: a check to the GitHub releases API for this plugin, to tell you whether a
newer version exists.

What it writes is confined to your own Dispatcharr database and its data directory: channel
visibility in the profiles you select, bindings to a dummy EPG source it manages, and CSV exports.
The main scan has a **Dry Run** that writes nothing and exports what it *would* do to CSV — run that
first. The other actions that change data (**Run Now**, **Remove EPG from Hidden Channels**, **Clear
CSV Exports**, **Cleanup Orphaned Tasks**) each ask for confirmation before they act.

**You are responsible for what you connect Dispatcharr to.** Whether a particular provider,
subscription, playlist or stream is lawful for you to use depends on your agreement with that
provider and on the law where you live. Use only sources you are authorised to use. Nothing in this
project is intended to enable, encourage or assist access to content you have no right to access.

All product names, channel names, network names, trademarks and registered trademarks mentioned in
this project, or appearing in its examples, are the property of their respective owners. This project
is an independent, community-built plugin. It is not affiliated with, endorsed by, or sponsored by
any television network, broadcaster, streaming service or IPTV provider, and it is not affiliated
with the Dispatcharr project beyond being a plugin written for it.

## Sponsor

This plugin is free and always will be. If it saves you time and you would like
to support the work, you can sponsor it at
[github.com/sponsors/PiratesIRC](https://github.com/sponsors/PiratesIRC).

Sponsoring buys no priority, no private support and no influence over what gets
built. Bug reports and pull requests are just as welcome from everyone.

## Contributing

Pull requests welcome. To submit changes:

### To this repo (`PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin`)

0. **Check open issues and PRs first** — review open issues + PRs on this repo (and any open `[event-channel-managarr]` PRs on `Dispatcharr/Plugins`) before cutting a release, so in-flight reports/fixes are included and nothing conflicts or duplicates.
1. Bump version: `python3 bump_version.py` (auto-stamps with current UTC day-of-year + HHMM).
2. Commit, push, tag, and release:
   ```bash
   git tag <version> && git push origin <version>
   gh release create <version> --title "v<version>" --notes "..."
   gh release upload <version> Event-Channel-Managarr.zip
   ```

### To the upstream marketplace (`Dispatcharr/Plugins`)

Updates also need to be PR'd to `Dispatcharr/Plugins` so the plugin updates in users' Dispatcharr UIs. The repo's GitHub Actions validator enforces strict rules — failing any blocks the merge:

| Check | Requirement |
| :--- | :--- |
| **PR title** | Must match `[event-channel-managarr]: <description>`. The `validate-title` job fails on any other format. **Most common trip-up.** |
| **Version bump** | `plugin.json` `version` must be greater than the version on upstream `main` for any code/asset change. Metadata-only edits are exempt. |
| **Required `plugin.json` fields** | `name`, `version`, `description`, `author`, `license` (SPDX). |
| **Authorship** | PR author's GitHub username must appear in `author` or `maintainers`, or the `close-unauthorized` job auto-closes the PR. |
| **Folder name** | `plugins/event-channel-managarr/` (lowercase-kebab) — note this differs from the `Event-Channel-Managarr/` capitalization used in this repo's zip. |

Workflow:

```bash
# In your fork of Dispatcharr/Plugins:
git fetch upstream && git checkout main && git merge upstream/main --ff-only && git push origin main
git checkout -b ecm-v<version>
cp <this-repo>/Event-Channel-Managarr/plugin.{py,json} plugins/event-channel-managarr/
git commit -am "[event-channel-managarr]: ..."
git push -u origin ecm-v<version>
gh pr create --repo Dispatcharr/Plugins --base main \
    --title "[event-channel-managarr]: Bump to v<version> — <summary>" \
    --body "..."
```

On merge, upstream automation builds the zip + checksums and updates `manifest.json` on the `releases` branch — do not touch that branch manually.
