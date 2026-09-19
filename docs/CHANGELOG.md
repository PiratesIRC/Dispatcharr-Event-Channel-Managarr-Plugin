# Changelog

Every released version, newest first, with a link to its release notes. The notes
on each release page describe what you will notice, and
they are the authoritative record; this page is an index.

Versions are calendar-based: `Major.YY.DDDHHMM`, where `DDD` is the day of the year
and `HHMM` the UTC time of the version bump. A higher version is always later.

## 1.26.2621024 (2026-09-19)

### Fixed

- The guide label shown after an event ends no longer carries a wrong date. The
  plugin wrote the label as `Ended at {month}/{day} {endtime}`, but Dispatcharr
  fills the month and day with the date the event STARTED and has no placeholder
  for the date it ended. An event starting at 7pm and running five hours was
  labelled `Ended at 9/19 12 AM` when it ended at midnight on 9/20. The label is
  now `Ended at <end-time> <zone>: <title>`, with no date. The `Upcoming at` label
  keeps its date, which is correct because it is shown before the event starts.
  The managed dummy EPG source is rewritten on the next applied run.

- **Regex: Mark Channel as Inactive** now compiles the pattern exactly as typed,
  the same way the Ignore and Force Visible fields and Validate Configuration
  already did. It used to pass the pattern through an escape-decoding step first,
  which turned the word boundary `\b` into a backspace character, so a pattern
  such as `\bCANCELLED\b` never matched any channel, and a pattern containing a
  non-ASCII letter could not match that letter. A pattern written with doubled
  backslashes to work around this, such as `\\d`, now means a literal backslash
  and needs to be written with single backslashes, as in the other two fields.

## 1.26.2561545 (2026-09-13)

### Fixed

- The idiom `24/7` in a channel name is no longer read as a date. The bare
  month-and-day pattern reads any two number pair as a date, and in both Auto and
  European format `24/7` is a valid day and month, so a channel named
  `24/7 Racing Stream` was read as carrying a date of 24 July, months in the past,
  and the past-date rule hid it. Measured across 1445 channel names on one
  installation, 157 of them were affected. The idiom is only rejected when no clock
  time follows it, because a European installation legitimately writes 24 July as
  `24/7` and such a name carries a time, so `Racing 24/7 8pm` keeps its date
  reading. A rejected pair no longer suppresses a real date later in the same name.

- A day-first textual date keeps its clock time, contributed by mwongj in pull
  request 30. `12 Sep 7:00pm` previously read as midnight, so the past-date rule
  judged the event at day granularity: it kept a finished evening event visible
  until the next day and could hide a live one after local midnight. The month-first
  pattern already kept its time. Measured across the same 1445 names, four of them
  change, all boxing events that were losing their start time. Tests covering it
  were added after the merge.

### Known limitation

- An ordinal out of a total, as in the doubleheader name
  `Cubs at Reds Game 1/2 7:05 PM ET`, is still read as a date, 2 January in that
  example. It is not safely separable from a real date: names such as `Game 10/27`
  and `Race 10/27 8:00 PM` use the same shape after the same word and there the pair
  IS the date, and 1 and 2 are each a valid month and a valid day. The levers for an
  affected channel are the Regex: Force Visible field or a narrower Channel Name
  Format.

## 1.26.2561458 (2026-09-13)

### Changed

- `[UndatedEnded]` now anchors an event on the day its name states, when the name
  states one. A full day of the week, or the markers `MNF`, `TNF` and `SNF`, is
  resolved to the first such day on or after the date the channel was first seen.
  A provider that publishes a weekend slate several days early previously had every
  one of those events judged against the day the channels appeared, so a channel
  named for Monday Night Football was treated as an event on the Thursday it was
  published, judged finished five hours later, and hidden all weekend before the
  game was played. A name stating no day keeps the first-seen date, so nothing
  changes for the names this rule already handled.

- No short form of a day is read. `Sat` and `Sun` appear inside ordinary names such
  as the broadcaster SAT.1 and the team Arizona State Sun Devils, and a day taken
  from an ordinary word would move the event window rather than widen it, so the
  whole set is excluded rather than some of it. A full day name is read wherever it
  appears, including inside a programme title, which can only keep such a channel
  visible longer and never hide it sooner.

- The rule reports which anchor it used. When the day came from the name, the reason
  in the CSV export says so and gives the date.

### Behaviour worth knowing about before you upgrade

- **A channel this rule never used to hide can now be hidden after its event.** The
  rule declines to act when the inferred window closed at or before the moment the
  channel was first recorded, which protects a channel named for an event that has
  not started yet. Anchored on the first-seen date, that condition could hold for
  ever for a late-appearing channel, so the rule never fired for it at all and
  `[UndatedAge:days]` was left to decide. Anchored on the day the name states, the
  window describes the real event, so the channel is hidden once that event has
  ended. Measured across the parameter space: 54 combinations move from never hidden
  to hidden after the event, and none is hidden earlier than before.

- The anchor only ever moves the event window later, never earlier, and by at most
  six days. It is resolved forward from the first-seen date rather than onto the
  current week, so a channel a provider never removes cannot be re-dated into the
  future week after week.

### Tests

- The plugin can now be imported outside the container, through stand-ins for the
  Django and Dispatcharr modules it imports at module scope, so its hide rules can
  be run rather than only read. Eight tests execute `[UndatedEnded]` and assert on
  what it decides. This was added because eight separate mutations that reverted the
  new anchor passed the whole suite while no test executed the rule, including one
  that only moved a statement below the call that consumes it.

- 749 tests pass and `ruff check .` exits 0.

| Version | Released | Notes |
| :--- | :--- | :--- |
| `v1.26.2561545` | 2026-09-13 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2561545) |
| `v1.26.2561458` | 2026-09-13 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2561458) |
| `v1.26.2490035` | 2026-09-06 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2490035) |
| `v1.26.2451734` | 2026-09-02 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2451734) |
| `v1.26.2450117` | 2026-09-02 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2450117) |
| `v1.26.2420322` | 2026-08-30 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2420322) |
| `v1.26.2351639` | 2026-08-23 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2351639) |
| `v1.26.2341504` | 2026-08-22 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2341504) |
| `v1.26.2341433` | 2026-08-22 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2341433) |
| `v1.26.2261346` | 2026-08-14 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2261346) |
| `v1.26.2251616` | 2026-08-13 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2251616) |
| `v1.26.2242049` | 2026-08-12 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2242049) |
| `v1.26.2241846` | 2026-08-12 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.2241846) |
| `v1.26.1711720` | 2026-06-20 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1711720) |
| `v1.26.1711623` | 2026-06-20 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1711623) |
| `v1.26.1641827` | 2026-06-13 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1641827) |
| `v1.26.1600157` | 2026-06-09 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1600157) |
| `v1.26.1600037` | 2026-06-09 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1600037) |
| `v1.26.1401103` | 2026-05-20 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1401103) |
| `v1.26.1362004` | 2026-05-16 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1362004) |
| `v1.26.1291442` | 2026-05-09 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/v1.26.1291442) |
| `1.26.1172336` | 2026-04-27 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/1.26.1172336) |
| `1.26.1152350` | 2026-04-26 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/1.26.1152350) |
| `1.26.1081615` | 2026-04-18 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/1.26.1081615) |
| `0.7.0` | 2026-04-05 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.7.0) |
| `0.6.0a` | 2026-03-09 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.6.0a) |
| `0.5.0` | 2026-01-18 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.5.0) |
| `0.4.9` | 2025-12-23 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.4.9) |
| `0.4.8` | 2025-12-03 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.4.8) |
| `0.4.7` | 2025-11-22 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.4.7) |
| `0.4.6` | 2025-11-22 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.4.6) |
| `0.4.5` | 2025-11-14 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.4.5) |
| `0.4.1` | 2025-11-10 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.4.1) |
| `0.4` | 2025-11-09 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.4) |
| `0.3.3b` | 2025-11-08 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.3.3b) |
| `0.3` | 2025-11-04 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.3) |
| `0.3.0g` | 2025-11-03 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.3.0g) |
| `0.3.0f` | 2025-11-01 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.3.0f) |
| `0.21` | 2025-10-24 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.21) |
| `0.3.0c` | 2025-10-29 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.3.0c) |
| `0.3.0b` | 2025-10-29 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.3.0b) |
| `0.3.0a` | 2025-10-28 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.3.0a) |
| `0.1` | 2025-10-15 | [Release notes](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases/tag/0.1) |

---

The **[releases page](https://github.com/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/releases)** carries the downloadable
archive for each version. The **[user guide](USER-GUIDE.md)** documents current
behaviour rather than history.
