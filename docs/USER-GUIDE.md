# Event Channel Managarr user guide

Everything needed to run the plugin: what each setting does, how the hide rules
decide, what the managed dummy EPG can and cannot show, how to point Jellyfin,
Plex or Emby at the right URLs, where every file is written, and a troubleshooting
section arranged by symptom.

The **[project front page](../README.md)** describes what the plugin is and what it
does. The **[changelog](CHANGELOG.md)** lists what changed in each version.

---

## Settings Reference

Settings are grouped into six sections in the UI.

### 📍 Scope

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **📺 Channel Profile Names (Required, comma-separated)** | `text` | - | Channel Profile(s) to monitor. Use comma-separated names for multiple profiles. |
| **📂 Channel Groups (comma-separated)** | `text` | - | Comma-separated group names to monitor. Leave empty for all groups in the profile(s). Matched **case-insensitively** (as of v1.26.1711623). ⚠️ **Separate these with commas, not `\|`.** The `\|` character belongs only in the three regex fields below; using it here glues several group names into one name that matches nothing, silently dropping them from the scan. Any configured group name that matches no channels is now named in the result message and the CSV header on **every** run, not only when the scan finds nothing at all. |
| **🔤 Name Source** | `select` | `Channel_Name` | Choose the source for rule matching: `Channel_Name` uses the channel name, `Stream_Name` uses the first stream's name in the channel. |

### 🎯 Hide Rules

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **📜 Hide Rules Priority** | `text` | (see default) | Define rules for hiding channels in priority order. First match wins. See "Hide Rule Logic" below. |
| **🚫 Regex: Channel Names to Ignore** | `text` | - | Regular expression to match channel names that should be skipped entirely. |
| **💤 Regex: Mark Channel as Inactive** | `text` | - | Regular expression to hide channels. Processed as part of the `[InactiveRegex]` hide rule. |
| **✅ Regex: Force Visible Channels** | `text` | - | Regular expression to match channels that should ALWAYS be visible, overriding any hide rules. Use this for year-round channels that sit in an event group, such as a league's RedZone or Network feed, which carry no date and are otherwise hidden by `[UndatedAge:N]`. |
| **📅 Past Date Grace Period (Hours)** | `number` | `4` | Extra hours to wait before hiding a past event, used by the `[PastDate]` rule. For day-only names this is the wait after midnight; for names carrying a clock time it is added on top of the event's start + Event Duration. |
| **🕒 Undated Event Grace Period (Hours)** | `number` | `1` | Extra hours to wait past an undated event's inferred end before hiding it, used by the `[UndatedEnded]` rule. Raise it for events that overrun. `[UndatedEnded:hours]` overrides it for one rule list. |

**All three regex fields above are matched against the channel or stream name only** (whichever the **Name Source** setting selects). They are never matched against guide programme titles or descriptions, so text copied out of the TV Guide will not match anything. Separate alternatives with `|`, for example `NFL REDZONE|NFL NETWORK`. A regex field that you have filled in but that matched no channels is reported in the result message and counted in the CSV header under **Regex Field Matches**, so a pattern that can never fire is visible rather than looking like one that works.

**A common mistake, and what warns you about it.** Because `|` separates alternatives, pasting names that already contain a `|` into one of these fields quietly turns each fragment into its own alternative. Typing four channel group names such as `USA | Sports` and `USA | Kids` into the ignore field gives the pattern the alternatives `USA `, ` Sports `, `USA `, ` Kids ` and so on, so every channel whose name contains the text `USA ` is skipped. The pattern is valid, it just does not mean what it looks like. **Validate Configuration** now reports this: an alternative that begins or ends with a space, or an empty alternative left by a stray `|`, is called out with the offending text, and the full explanation for each one is written to the Dispatcharr log. Short alternatives with no surrounding space, such as `NFL|NHL|NBA`, are not flagged, because that is the normal way to write a list of league codes.

### 🎭 Duplicates

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🎭 Duplicate Handling Strategy** | `select` | `lowest_number` | Strategy to use when multiple channels have the same event. |
| **🔄 Keep Duplicate Channels** | `boolean` | `False` | If enabled, duplicate channels will be kept visible instead of being hidden. The duplicate strategy above will be ignored. |

### 🔌 EPG Management

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **🔌 Auto-Remove EPG on Hide** | `boolean` | `True` | If enabled, **removes** EPG data from a channel when the plugin hides it (clears stale guide data). This does *not* create EPG. To give visible channels a placeholder guide use **🗓️ Manage Dummy EPG** below. |
| **🗓️ Manage Dummy EPG** | `boolean` | `False` | The setting that **creates** guide data: visible channels with no EPG get bound to the plugin-managed dummy EPG source. Disables cleanly: toggling off detaches all channels from the managed source on the next scan. |
| **♻️ Override Empty Existing EPG** | `boolean` | `False` | Requires **🗓️ Manage Dummy EPG**. Lets the managed dummy also take over visible channels already linked to a real (non-managed) EPG source that currently has **no programmes** in the next 24h (a blank guide). Channels whose linked EPG has real upcoming programmes are never touched. Default off so real EPG is never overwritten unless you opt in. (new in v1.26.1711623) |
| **📡 Channel Name Format** | `select` | `US` | How channel names are structured for the dummy EPG parser. `US` = `PPV/LIVE EVENT ##: Title (MM.DD HH:MM AM/PM TZ)`, bare `EVENT ##: Title (…)` (no `PPV`/`LIVE` prefix required, as of v1.26.1711623), and a bare slot number followed by a date or a time such as `07 - 8/14 7pm Broncos at Falcons` (v1.26.2261346). `SE` = pipe-delimited `PREFIX \| Title \| DDD DD Mon HH:MM TZ \| extras \| channel name` (24-hour time, textual month); the last pipe segment (e.g. `SE: VIAPLAY PPV 20`) is stored as the EPG display name so the guide's channel list shows the broadcaster instead of the full stream name. |
| **⏱️ Event Duration (hours)** | `number` | `3` | How long each scheduled event appears in the guide. Before this window the guide shows `Upcoming at <start-time>: <title>`; after, `Ended at <end-time>: <title>`. |
| **📺 Channel Name Event Timezone** | `select` | `US/Eastern` | Timezone encoded in event times within channel names (e.g., `US/Eastern` for channels like `(4.17 8:30 PM ET)`). Independent of Dispatcharr's global time zone. |
| **🗂️ Per-Group EPG Sources** | `text` | *(empty)* | Gives a channel group its own dummy EPG source, one mapping per line as `Group Name = Source Name`. A group you do not list keeps the shared source, so leaving this blank changes nothing. See [Per-Group EPG Sources](#per-group-epg-sources) below. (new in v1.26.2451734) |

### ⏰ Scheduling & Export

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **⏰ Scheduled Run Times (24-hour, comma-separated)** | `text` | - | Comma-separated times (24-hour HHMM format) to run daily. Leave blank to disable. |
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
    * Pick the **Channel Name Format** (`US` or `SE`) that matches how your provider names event channels. See [Channel Name Formats](#channel-name-formats) below.
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
    * **🧹 Remove EPG from Hidden Channels**: delete EPG data from disabled channels (confirmation required; destructive).
    * **🗑️ Clear CSV Exports**: remove accumulated export files (confirmation required).
    * **🧼 Cleanup Orphaned Tasks**: remove leftover Celery Beat tasks from older plugin versions (confirmation required).

## Hide Rule Logic
The plugin checks channels against the **Hide Rules Priority** list in the order you define. The first rule that matches is applied, and the channel is marked to be hidden. If no rules match, the channel is marked to be shown.

**Default Rules:**
`[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedEnded],[UndatedAge:2],[ShortDescription],[ShortChannelName]`

**Available Rule Tags:**

| Rule | Parameter | Description |
| :--- | :--- | :--- |
| **[NoEPG]** | - | Hides if no EPG is assigned OR if the assigned EPG has no program data for the next 24 hours. (Skips custom dummy EPG, including the plugin-managed source.) |
| **[BlankName]** | - | Hides if the channel name is blank. |
| **[WrongDayOfWeek]** | - | Hides if the name contains a day name (e.g., "MONDAY", "Mon", "Saturday", "Sat") and the named day is not yesterday, today, or tomorrow in Dispatcharr's time zone. The ±1 day tolerance keeps US/EU named channels visible to viewers in distant timezones (e.g., Australia seeing "Monday Night Football" on local Tuesday). Recognizes full/abbreviated day names plus MNF/TNF/SNF. |
| **[NoEventPattern]** | - | Hides if the name contains patterns like "no event", "offline", "no games scheduled". |
| **[EmptyPlaceholder]** | - | Hides if the name ends with a separator (`:`, `\|`, `-`) and has no event title after it, OR if the name contains a parenthesized literal template token like `(MM.DD h:mmAM/PM ET)` indicating an unpopulated stub channel. |
| **[ShortDescription]** or **[ShortDescription:chars]** | optional `chars` (int) | Hides if the event title (the text after a `:`, `\|` or ` - ` separator) is shorter than `chars` characters. Defaults to **15** when no number is given, which is the value this rule always used. Give it a number to move the line: on one provider's channels `NCAAF 25: FS1 [1080p]` is hidden (11 characters after the colon) while `NCAAF 26: SEC NETWORK [1080p]` stays visible (19), and `[ShortDescription:25]` would catch both. The threshold applied is recorded in the CSV `reason` column. |
| **[ShortChannelName]** or **[ShortChannelName:chars]** | optional `chars` (int) | Hides if the *entire name* is shorter than `chars` characters and has *no* separator. Defaults to **25** when no number is given, which is the value this rule always used. |
| **[NumberOnly]** | - | Hides if the channel name is just a prefix followed by a number (e.g., "PPV 12", "EVENT 15") with no event details. |
| **[PastDate:days]** or **[PastDate:days:Xh]** | `days` (int), optional `Xh` (grace hours) | Hides if the name contains a date that is more than `days` in the past (e.g., `[PastDate:0]` hides yesterday's events). Optionally specify grace period inline like `[PastDate:0:4h]` to override the global grace period setting. **Time-aware matching (v1.26.1711623):** if the name carries an explicit `stop:YYYY-MM-DD HH:MM:SS` end timestamp, the rule compares the **actual end time** (`stop:` + `days`/grace); if it carries a clock time but no `stop:` (e.g. `(6.19 7:30 PM ET)`), the event is assumed to end **Event Duration hours** after its start (localized in the **Channel Name Event Timezone**), and the rule hides it once that end + `days`/grace has elapsed. Day-only names (no parseable time) keep the original calendar-day behavior. |
| **[FutureDate:days]** | `days` (int) | Hides if the name contains a date that is more than `days` in the future (e.g., `[FutureDate:2]` hides events 3+ days from now). "Today" is resolved in Dispatcharr's time zone, consistent with the other date rules (v1.26.1711623). |
| **[UndatedAge:days]** | `days` (int) | Hides channels whose names contain **no parseable date** once they've been visible for more than `days` days. Persists per-channel first-seen state in `/data/event_channel_managarr_undated_first_seen.json`. Resets a channel's age when its name changes. |
| **[UndatedEnded]** or **[UndatedEnded:hours]** | optional `hours` (int) | Hides a channel whose name carries a **clock time but no date** once that event's inferred end has passed. The window is the date the channel was first seen (the same `/data/event_channel_managarr_undated_first_seen.json` record `[UndatedAge:days]` uses), plus the time read from the name, plus the event duration and time pattern taken from the dummy EPG source the channel is bound to (falling back to the **Event Duration** and **Channel Name Event Timezone** settings), plus the grace period. Without a number it reads the **Undated Event Grace Period (Hours)** setting. Every step fails open: a channel with no first-seen record, no readable time or an unusable timezone stays visible and is left to `[UndatedAge:days]`. |
| **[InactiveRegex]** | - | Hides if the name matches the `Regex: Mark Channel as Inactive` setting. |

#### Undated events with a clock time (`[UndatedEnded]`)

Some providers name a channel with a start time and no date at all, such as `Boxing 3 : MOSES vs HRGOVIC  4:00pm`. `[UndatedAge:days]` can only count whole calendar days for such a name, so it hides a late-evening event at midnight or keeps a finished one until the next day. `[UndatedEnded]` computes the real window instead and hides the channel once the event has actually ended, plus the grace period.

Four things are worth knowing before you rely on it:

* **It controls visibility only.** Dispatcharr renders a dateless time as a programme that recurs every day, so the repeated guide entry stops when the channel is hidden and not before. The plugin cannot make Dispatcharr stop generating that entry.
* **The hide happens on the next scan**, so schedule a run shortly after your latest events end. A channel stays visible until a scan evaluates it.
* **Keep `[UndatedAge:days]` in the list after it.** It is the outer bound for a channel whose first-seen record was written late or rebuilt, where the inferred window would be wrong by a day.
* **A setting the rule cannot read is reported in the log, once per run.** If a dummy EPG source carries a time pattern that is not a valid regular expression, or a program duration that is not a whole number of minutes, or a timezone this installation does not know, the plugin writes a warning naming the source and the value, and says what it used instead. These substitutions all shorten or abandon the event window, so without the warning a mistyped value would look exactly like the rule working correctly.
* **A time pattern that matches nothing is respected.** If you narrow a source's time pattern so that, for example, a slot number is not read as an hour, a name your pattern refuses is treated as carrying no time. Earlier behaviour silently fell back to the built-in am/pm pattern, which undid the narrowing. A pattern that does not compile at all still falls back to the built-in one, so a typing mistake does not stop names being read, and that fallback is now logged.
* **A channel that appears after its inferred window has already closed is left visible.** A channel first seen at 23:00 and named for a 1:00am event is named for the next 1:00am, not the one that ended eighteen hours earlier, so the rule declines to act rather than hiding a channel whose event has not started. This needs the first-seen moment, which the plugin began recording alongside the date in this version; a record written by an earlier version carries the date only and does not get the check until its channel name next changes.
* **An existing installation keeps its stored rule list.** Dispatcharr never prunes or rewrites a setting you have saved, so if you have edited **Hide Rules Priority** you must add `[UndatedEnded]` to it by hand, before `[UndatedAge:2]`.

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

* **Auto (recommended)**: try MM/DD first; if the month is invalid (> 12), retry as DD/MM. Handles most regional data without configuration.
* **US (MM/DD)**: always month first. Use this if you want to force US-style parsing (e.g. ambiguous `04/05` always means April 5).
* **EU (DD/MM)**: always day first (e.g. `15/04` = April 15, ambiguous `04/05` means May 4).

**Note:** When using `[PastDate]` or `[FutureDate]` rules, the plugin will attempt to extract a date using these formats. If no date is found, the rule will not match and the next rule in your priority list will be checked. The `[UndatedAge]` rule handles the "no date found" case directly.

## Managed Dummy EPG

When **🗓️ Manage Dummy EPG** is enabled:

* A single plugin-managed `EPGSource(source_type='dummy', name='ECM Managed Dummy')` row is created on first use.
* Visible channels in the monitored profile(s) with **no EPG assigned** are bound to it via a per-channel `EPGData` row keyed by `channel.uuid`.
* Channels that already have a real EPG binding (XMLTV, Schedules Direct) are never touched, **unless** you enable **♻️ Override Empty Existing EPG**, which extends the takeover to channels linked to a non-managed source that has **no programmes in the next 24h** (a blank guide). Channels whose linked EPG has real upcoming programmes are still never touched. (v1.26.1711623)
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
* Toggling **Manage Dummy EPG** off cleanly unbinds every channel the plugin attached. On the next scan, `epg_data` is set to `None` for any channel still pointing at the managed source. The source row itself is preserved for cheap re-adoption.

### ⚠️ Guide titles come from the CHANNEL name, even when Name Source is Stream Name

This catches out anyone whose provider puts the event details in the **stream** name while the **channel** name stays fixed, for example a channel called `NFL : 15 - [1080p]` fed by a stream called `NFL : 15 - 8/22 10pm Cowboys at Cardinals [1080p]`.

Setting **Name Source** to `Stream_Name` changes what **this plugin's hide rules read**, and nothing else. Dispatcharr renders dummy guide entries itself, from the channel's own name, and a stream name is never available to it. So in that setup:

* **Hiding and showing work correctly.** The rules see the game, the date and the time.
* **The guide entry cannot show the game.** The channel name does not match the event title pattern, so the renderer falls back to the channel name plus the static description `Live event. Guide information is currently unavailable.`

This is a limit of dummy guide data, not a fault, and re-running a scan will not change it. To get event titles into the guide, the **channel** names have to carry the event text, which is a Dispatcharr channel-naming matter rather than a plugin setting.

### Channel Name Formats

The **📡 Channel Name Format** setting tells the parser how event titles, times, and dates are laid out in your channel names. The parser ships regex defaults for each format and stores them on the managed source's `custom_properties` (you can override them in Dispatcharr's Pattern Configuration; the plugin only auto-refreshes patterns you haven't customized).

| Format | Example channel name | Parsed title | Notes |
|---|---|---|---|
| **US** (default) | `PPV EVENT 12: Cage Fury FC 153 (4.17 8:30 PM ET)` | `Cage Fury FC 153` | 12-hour AM/PM time, numeric `MM.DD` date. Also handles `LIVE EVENT ##`, leading-time variants, bare `EVENT ##: Title (…)` with no `PPV`/`LIVE` prefix (v1.26.1711623), and a bare slot number followed by a date or a time, such as `07 - 8/14 7pm Broncos at Falcons` (v1.26.2261346). |
| **SE** | `LIVE \| GIRONA - REAL SOCIEDAD \| Thu 14 May 19:55 CEST (SE) \| 8K EXCLUSIVE \| SE: TV4 PLAY PPV 7` | `GIRONA - REAL SOCIEDAD` | Pipe-delimited; 24-hour time, textual month (`14 May`). The **last** pipe segment (`SE: TV4 PLAY PPV 7`) becomes the EPG display name, so the guide's channel list shows the broadcaster rather than the full stream name. |

* **SE display names resync every run.** Because the broadcaster segment can change between M3U refreshes, SE mode re-checks and updates `EPGData.name` for already-attached channels on each scan (US mode only sets it on first attach).
* **Switching formats** auto-refreshes the stock patterns (both formats' historical defaults are recognized), so changing `US` ⇄ `SE` and re-scanning picks up the right patterns without manual edits, unless you've customized a pattern, which is always preserved.

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

**Numeric-offset zones** (e.g., `Etc/GMT+5`) suppress the abbreviation suffix. ECM still converts the time but writes no trailing label, since `+0500` would look wrong in a title.

## Per-Group EPG Sources

*New in v1.26.2451734. Requested in [issue 29](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/issues/29).*

By default every channel this plugin manages shares one dummy EPG source, `ECM Managed
Dummy`, so they all share one timezone, one event duration and one set of title patterns.
That breaks down when groups differ: one provider labels its times in Eastern and another
in Central, college football wants a four hour event and volleyball wants two, and one
group's titles carry a suffix the others do not.

**Per-Group EPG Sources** gives a group its own source. Write one mapping per line in the
setting:

```
NFL Sunday Ticket = ECM - NFL
NCAAF = ECM - NCAAF
SEC+/ACC Extra = ECM - SEC ACC
Big Ten+ = ECM - Big Ten
```

Capitalisation of the group does not have to match. Two groups may point at the same
source. A group you do not list keeps the shared source, so **leaving this blank changes
nothing** and no existing installation is affected.

### The source is yours after the plugin creates it

The plugin creates a listed source and seeds it from your global settings above. **After
that it never writes to that source again.** Its timezone, event duration, title, date and
time patterns, templates, categories and artwork are yours to edit in Dispatcharr's own EPG
source editor, and the plugin will not overwrite them. The shared
source keeps being maintained by the plugin; a mapped source does not.

Two consequences follow from that:

- The seeded patterns are the US ones. If your global **Channel Name Format** is `SE`, a
  newly created mapped source still starts with the US patterns, so set them yourself. The
  seed is a starting point, not a promise.
- A source that already existed before you mapped it is **adopted, not created**. The
  plugin will route channels onto it, but because it did not create it, it will never move
  channels back off it. Validate Configuration tells you when that has happened.

### Six things that will otherwise look like a bug

1. **Nothing happens unless Manage Dummy EPG is on.** Routing is part of the managed EPG
   pass. With that setting off, a perfect mapping does nothing at all.
2. **A mapped group must also be in scan scope.** If **Channel Groups** above lists any
   groups, a mapped group missing from that list is never scanned, so it routes nothing,
   for ever. Either add it there or clear that field to scan every group. Validate
   Configuration reports this specifically, because a mapping that routes nothing looks
   exactly like a mapping that is working.
3. **Only channels that end a scan visible are moved.** This plugin hides event channels
   that have no event, so at any moment most of a group may be hidden and therefore not
   moved.
4. **Changes take effect on the next applied run, not when you press Save.** Scheduled runs
   read settings from a file that only an action button writes, so after editing the
   mapping press **Validate Configuration** (or Update Schedule, Run Now or Dry Run) or an
   unattended run will keep using the previous mapping.
5. **An M3U refresh can trigger an applied run.** With **Auto-Rescan on M3U Refresh** on,
   source creation and channel moves can happen without you pressing anything.
6. **Clearing the box may not appear to work.** Dispatcharr does not reliably send a field
   you have emptied, and the plugin falls back to the value it has saved rather than
   guessing that you meant to clear it. Validate Configuration prints the mapping the
   plugin actually read, which is how you check.

### Undoing a mapping

Remove the line and run an applied run. Those channels return to the shared source
`ECM Managed Dummy` on the next scan.

Three limits on that:

- **The plugin only takes a channel back off a source it created itself.** A channel you
  had put on your own dummy source is never moved back, which is deliberate: this plugin
  must not rearrange sources it does not own.
- **A channel taken from `DAZN PPV Dummy (GMT)` does not automatically return there.** It
  goes back to the shared source unless its name still matches that source's own pattern,
  in which case the plugin reclaims it on the same run.
- **If the mapping has any error in it, no channel is moved back at all that run.** A
  missing equals sign looks identical to "this group is no longer mapped", so rather than
  risk moving a whole group because of a typo, the plugin does nothing in that direction
  and says so. Fix the error and run again.

### Sources are never deleted

Nothing in this plugin deletes an EPG source, because deleting one also deletes the guide
entries attached to it. A source created from a typo is therefore permanent until you
remove it yourself. To remove one safely: change the mapping and run applied so its
channels move away, confirm nothing is bound to it, then delete it in Dispatcharr's EPG
source editor.

Run **Validate Configuration** after editing the mapping. It reports lines it could not
read, groups that match nothing, groups outside your scan scope, and a source name that
clashes with a non-dummy source, and prints the mapping in use.

## Action Reference

| Action | Style | Description |
| :--- | :--- | :--- |
| **🔎 Validate** | Outline blue | Test and validate all plugin settings before running. |
| **💾 Save Schedule** | Filled green | Save all settings and update/activate the scheduled run times. |
| **👁️ Dry Run** | Outline cyan | Preview which channels would be hidden or shown without making any changes. Pure preview: never creates/modifies the managed dummy EPG source. Runs synchronously; the button's loading spinner covers the busy state and a single notification appears on completion with a compact one-line summary (`Dry run: N channels \| X hide / Y show \| EPG +A/-D \| CSV: <file>`). Full details land in the CSV header and logs. |
| **▶️ Run Now** | Filled green, with confirm | Immediately scan and apply visibility updates based on the current EPG data. Same synchronous + compact-notification behavior as Dry Run. |
| **🧹 Remove EPG from Hidden** | Filled red, with confirm | Delete all EPG data from channels that are currently hidden/disabled in the selected profile(s). Destructive; requires confirmation. |
| **🗑️ Clear CSV Exports** | Filled red, with confirm | Delete all CSV export files created by this plugin to free up disk space. Requires confirmation. |
| **🧼 Cleanup Orphaned Tasks** | Outline orange, with confirm | Remove any orphaned Celery periodic tasks from old plugin versions. Requires confirmation. |
| **Auto-rescan after M3U refresh** | No button | Not something you click. Dispatcharr triggers it after each M3U refresh, and it runs a visibility scan only while **🔄 Auto-rescan after M3U refresh** is enabled in the settings. This is what keeps hidden channels hidden when your M3U account has Auto Channel Sync switched on, since that re-enables every channel in a synced group on each refresh. |
| **🩺 Check Scheduler** | Outline blue | Display scheduler status. Reports this worker's scheduler thread, configured times, the next upcoming run, container-wide last-run history (from shared file), and whether a scan is currently holding the cross-process lock. Because Dispatcharr runs under multiple uwsgi workers and each has its own scheduler thread, pressing the button twice may reach different workers. Coordination is via shared files so each scheduled time fires exactly once regardless. |

## File Locations
* **Settings Cache**: `/data/event_channel_managarr_settings.json`
* **Last Run Results**: `/data/event_channel_managarr_results.json`
* **Last Run Tracker** (scheduled run history, cross-worker safe): `/data/event_channel_managarr_last_run.json`
* **Scan Lock** (cross-worker mutex): `/data/event_channel_managarr_scan.lock`
* **Undated-Channel Tracker** (for `[UndatedAge:N]` and `[UndatedEnded]`): `/data/event_channel_managarr_undated_first_seen.json`. Each entry holds the channel name, the date it was first seen, and the exact moment it was first seen. `[UndatedAge:N]` uses the date; `[UndatedEnded]` uses the moment as well, to reject an event window that closed before the channel existed.
* **Group EPG Source Record** (which dummy EPG sources the plugin created for a group mapping): `/data/event_channel_managarr_group_sources.json`. This is what allows the plugin to move a channel back off a source later. If it is lost, channels stay where they are and nothing is moved back.
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
| **has_epg** | Indicates whether an EPG source is *linked* to the channel (`Yes` or `No`). Note this reflects linkage, not whether that source actually has programmes. Reconciled with this run's attach/detach, so a channel attached this run reads `Yes` and one detached reads `No` (v1.26.1711623). |
| **epg_source** | The name of the EPG source the channel is bound to, or empty when it is bound to none. Added so a reader can see which source a channel landed on when a per-group mapping moved it. (new in v1.26.2451734) |
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
* **"No channels found…"**: Verify that the specified profile(s) have channels assigned and that the group names (if used) are spelled correctly. Run **🔎 Validate**. As of v1.26.1711720 it distinguishes a misspelled group ("not found in Dispatcharr") from a real group that simply has no channels in the selected profile(s) ("will match 0 this scan"), and matches profile names case-insensitively (consistent with Run Now). Group names in the **Channel Groups** box are also matched case-insensitively.
* **Scheduler Not Running**: After changing the schedule, you must click **💾 Save Schedule** to save and activate it. Ensure the times are in `HHMM` format (e.g., `0700` for 7 AM).
* **One of my scheduled times never runs**: Valid entries are `0000` to `2359`. **Midnight is `0000`, not `2400`**. `2400` is four digits but is not a real time, and it used to be discarded without a word, so that run simply never happened. Both **🔎 Validate** and **💾 Save Schedule** now name any entry they are going to ignore, and Validate judges the times with the same parser the scheduler arms itself from, so the two can no longer disagree.
* **Fewer channels were processed than I expected / a group I listed did nothing**: Check the result message and the CSV header for **"Channel Groups that matched no channels"**. The usual cause is separating group names in **Channel Groups** with `|` instead of commas: that field is comma-separated, `|` belongs only in the three regex fields, and using it glues several real group names into one name that exists nowhere. Group names are matched case-insensitively, but the spelling must match.
* **A regex field I filled in seems to do nothing**: The CSV header's **Regex Field Matches** block counts what each one matched, and a field that matched zero channels is called out in the result message. The three regex fields are matched against the channel or stream name only, never against guide programme titles, so text copied out of the TV Guide will never match. Filler text such as a "guide information is currently unavailable" line is a programme description generated by Dispatcharr, not a channel name.
* **Event channels are visible when nothing is playing right now**: Look at the channel names. If they carry a date in the next day or two, this is `[FutureDate:2]` behaving as configured. See "How far ahead should a channel appear?" above, and use `[FutureDate:0]` if you want a channel to appear only on the day of its event.
* **Two similar channels get different treatment and it looks arbitrary**: Check the `reason` column in the CSV. `[ShortDescription]` measures the characters after the separator against a cutoff, so `NCAAF 25: FS1 [1080p]` (11 characters after the colon) is hidden while `NCAAF 26: SEC NETWORK [1080p]` (19) is not. The reason line reports both the measured length and the cutoff. Move the cutoff by writing a number in the tag, for example `[ShortDescription:25]`, or drop the tag from **Hide Rules Priority** if the rule does not suit your channel names.
* **A year-round channel in an event group keeps getting hidden**: Channels like a league's RedZone or Network feed carry no date, so `[UndatedAge:N]` hides them once they have been around for N days. Put them in **✅ Regex: Force Visible Channels**, for example `NFL REDZONE\|NFL NETWORK`.
* **Hidden channels still show in Dispatcharr's TV Guide page**: That page's profile filter is probably set to **All Profiles**, which shows every channel by design. Select the managed profile instead.
* **A run reports "Channels to Hide: 0" and "Channels to Show: 0"**: That reads identically to a run where no channels entered scope at all, so check scope before rules. In order: does the group exist and have channels; do those channels have a membership row in the configured **profile** (a channel with no membership row is invisible both to Dispatcharr and to this plugin); and only then look at the hide rules. A group named in your settings but absent from the CSV means zero channels in scope, not zero changes.
* **Channels Aren't Hiding/Showing**: Run a **Dry Run** and check the `reason` and `hide_rule` columns for that channel. This will tell you exactly why a decision was made. You may need to adjust your **Hide Rules Priority** list.
* **"Another scan is already running"**: A cross-process lock prevents concurrent scans. Wait for the current scan to finish. Scheduled runs will skip cleanly when a manual scan is in progress.
* **Hidden channels reappear after a while / after an M3U refresh**: Dispatcharr's **Auto Channel Sync** re-enables every channel in a synced group on each M3U refresh, overriding the plugin's hide. To fix this, enable **🔄 Auto-rescan after M3U refresh** (in the **⏰ Scheduling & Export** section) so the plugin re-runs its scan automatically right after each M3U refresh and re-hides affected channels. Alternatively, turn off Auto Channel Sync for the managed groups in Dispatcharr's M3U account settings.

### Managed Dummy EPG Issues
* **Guide still shows nothing for a channel after enabling Manage Dummy EPG**: Check the CSV. Two common causes: (1) the channel didn't end up **visible** post-scan (e.g., a rule hid it), because only visible channels are attached; or (2) `has_epg` is `Yes` but `managed_epg_assigned` is `False`, meaning the channel is **already linked to another EPG source** that simply has no programmes for it. By default the managed dummy never overwrites an existing link. Enable **♻️ Override Empty Existing EPG** to let it take over channels whose linked EPG is blank, then re-run a scan. (Alternatively, clear that channel's EPG in Dispatcharr so it has none, and re-run.)
* **Guide shows the wrong time**: Verify the **Channel Name Event Timezone** setting matches the timezone encoded in channel names, and that Dispatcharr's **General Settings → Time Zone** is set to your display zone (ECM uses it for guide display; it falls back to UTC when unset).
* **Swedish (pipe-delimited) channels show no title, or the wrong guide name**: Set **📡 Channel Name Format** to `SE` and re-run a scan. `SE` parses `… \| Title \| DDD DD Mon HH:MM TZ \| … \| channel name` and stores the last pipe segment as the broadcaster display name. If you'd left it on `US`, the PPV/LIVE pattern won't match and the channel falls back to its plain name. (Switching formats auto-refreshes the patterns unless you've customized them.)
* **Want the managed source gone**: Toggle **Manage Dummy EPG** off and run a scan. Every managed binding is detached. The source row itself stays in the DB (inert) for cheap re-adoption later.
* **Guide shows the literal text `{channel_name}` as the programme title** (e.g. in Emby/Jellyfin EPG): **fixed.** This affected managed channels whose names don't match the event title pattern, so they fall back to `fallback_title_template`. Dispatcharr's dummy-EPG renderer uses that template *verbatim*. It never substitutes `{channel_name}` (the description only showed the real name because ECM left the description template empty, triggering the renderer's built-in default). ECM now sets `fallback_title_template = ""`, which makes the renderer fall back to the real channel name, plus a static `fallback_description_template`. If you still see the literal text, re-run a scan so the plugin rewrites the managed source's templates, then refresh your EPG.
* **Guide shows literal `{month}/{day}` or `{starttime}` in the programme title** (e.g. `GOBI Live From Coachella 2026 {month}/{day} {starttime} CDT`): **fixed.** This hit *matched* event channels whose name has no parseable date **and** time (a bare year like `2026` is not a date). The timezone-localized title template embedded those placeholders, and the renderer leaves any placeholder it can't fill as literal text. ECM now uses a plain `{title}` for the live title, and timed channels still land in the correct, timezone-converted guide slot, and the `Upcoming…`/`Ended…` titles keep the localized date/time (they only render when a date and time were parsed). A related fix lets the event-number separator be `-` (e.g. `LIVE EVENT 31 - GOBI …`) so the leading `- ` no longer leaks into the title. Re-run a scan and refresh your EPG if you still see the old behavior.
* **Guide title is a fragment of the air time, such as `00pm` or `Ended at 7 PM CDT: 00pm`**: **fixed in v1.26.2420322.** It affected names whose slot number is followed by text rather than by a date or a time, such as `Boxing 3 : MOSES vs HRGOVIC  4:00pm`. The pattern skipped the slot number and started matching at the air time instead, reading `4` as the slot number and capturing `00pm` as the title. The default pattern now refuses to start a match inside a clock time, so such a name falls back to the plain channel name. Update the plugin and run one scan, which rewrites the managed source's pattern; a pattern you have edited yourself is left alone.
* **My channel names don't match the default pattern**: the default `title_pattern` matches `PPV EVENT ##`, `LIVE EVENT ##`, bare `EVENT ##:` names (the `PPV`/`LIVE` prefix is optional as of v1.26.1711623), and a bare slot number followed by a separator and then a date or a time, such as `07 - 8/14 7pm Broncos at Falcons` (v1.26.2261346). A bare slot number is not accepted when the only number-and-separator in the name is the air time itself, so `Boxing 3 : MOSES vs HRGOVIC  4:00pm` falls back to the plain channel name rather than producing a title of `00pm` (v1.26.2420322). Other formats that lack both the `EVENT` keyword and a date or time after the number (e.g. `USA NBA 01: …`, `Pay Per View 19: …`) still fall back to the plain channel name. You can set your own regex in Dispatcharr under **EPG Sources → ECM Managed Dummy → Pattern Configuration**. **Your custom `title_pattern` / `time_pattern` / `date_pattern` now persist across plugin runs**. The plugin no longer overwrites a pattern you've changed (it only refreshes patterns left at a default it shipped). Use JS-style named groups `(?<title>…)` / `(?<hour>…)` / `(?<month>…)` (the UI validator rejects Python-style `(?P<…>)`; Dispatcharr converts JS groups server-side). The date group must be named `month` even when matching a text month (e.g. `(?<month>Jan|Feb|…)`).

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
