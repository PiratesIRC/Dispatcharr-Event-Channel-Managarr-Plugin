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
[![Events surfaced](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/PiratesIRC/6d203e81e83657ee1cbc6e77f5c03d65/raw/event-channel-managarr-events.json)](docs/USER-GUIDE.md#run-ledger)

<sub>The **events surfaced** badge is the number of event channels this plugin has switched from hidden to visible on the maintainer's own installation, counted from its run ledger and refreshed twice a day. It counts channels that actually changed, so a channel that stays visible across many scheduled runs is counted once, when it appeared. Channels hidden, channels merely scanned, and dry runs are all excluded, so the number is work done rather than activity. The ledger starts when it was first deployed rather than at the plugin's first release, so this is a total since then, not a lifetime one. It is one installation's total, not a project metric.</sub>

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin)

## Features

**Decides visibility from the channel name.** A prioritised, fully customisable
rule list decides what to hide and in what order, with the first matching rule
winning. Rules cover a past date, a date too far ahead, a name that never carries a
date at all, a name too short to describe an event, the wrong day of the week, a
blank or placeholder name, and a pattern you supply yourself.

**Reads either the channel name or the stream name.** Providers that leave the
channel name fixed and put the game in the stream name are handled by switching one
setting.

**Fills the guide for channels that have no EPG.** An optional plugin-managed dummy
EPG source renders the event title during its window, `Upcoming at <time>: <title>`
before it and `Ended at <time>: <title>` after, in the viewer's local time. Channels
whose names claim a different timezone get their own source rather than being pulled
back and forth. Two channel-name layouts are understood: the US form
(`PPV EVENT 12: Title (MM.DD HH:MM AM/PM TZ)`, and bare numbered slots such as
`07 - 8/14 7pm Broncos at Falcons`) and the Swedish pipe-delimited form.

**Hides duplicates of the same event**, keeping the lowest number, the highest
number or the longest name, whichever you choose.

**Scopes precisely.** Monitor several channel profiles at once, narrow to named
channel groups, skip channels by regular expression, and force chosen channels to
stay visible whatever the rules say.

**Runs on a schedule**, at times you set in Dispatcharr's own timezone, and
optionally straight after each M3U refresh. A cross-process lock means at most one
scan runs at a time across every worker, and a lock left behind by a dead process is
broken automatically after fifteen minutes.

**Reports what it did, and what it ignored.** Every run writes a CSV giving the
action, the reason and the rule for each channel. Channel groups that matched
nothing, and regular expressions that matched nothing, are named in the result and
in the CSV header, so a setting that is quietly doing nothing is visible rather than
silent.

**Previews safely.** Dry Run reports what would change and writes the CSV without
touching a single channel or creating any EPG binding.

**Makes no outbound network request.** The plugin talks to Dispatcharr's database
and nothing else.

## Requirements

* An active Dispatcharr installation, v0.20.0 or newer (declared as
  `min_dispatcharr_version` in `plugin.json`).

## Installation

1. Log in to Dispatcharr's web interface.
2. Go to **Plugins**.
3. Click **Import Plugin** and upload the plugin zip file.
4. Enable the plugin after installation.

Then read the **[user guide](docs/USER-GUIDE.md)**: the settings that decide scope
are the ones worth getting right before the first applied run.

## Documentation

| Page | What is in it |
| :--- | :--- |
| **[User guide](docs/USER-GUIDE.md)** | Every setting and action, how the hide rules decide, the managed dummy EPG, client setup for Jellyfin, Plex and Emby, file locations, the CSV format, and troubleshooting by symptom |
| **[Changelog](docs/CHANGELOG.md)** | Every released version with a link to its release notes |
| **[Development notes](docs/DEVELOPMENT.md)** | The runtime model, code map, deploying, testing, adding a setting or action, the release procedure, and how to contribute |
| **[Documentation index](docs/README.md)** | The above, described by who is reading |

## Disclaimer

**Event Channel Managarr provides no television content of any kind.** It supplies no channels, no
playlists, no streams, no electronic programme guide data and no provider accounts, and it contains
no list of where to obtain any of those. It bundles no reference data at all: everything it works on
already exists in **your** Dispatcharr installation.

What it reads is channel *names* and the programme data already stored against your channels. It
parses event titles, dates and times out of those names in order to decide which channels currently
have an event. **It never opens, reads, decodes, records, restreams or redistributes a stream**, and
it never reads a stream URL. **It makes no outbound network request at all.** The update checker that
once called the GitHub releases API was removed in `1.26.2251616`, along with the code behind it.

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

## License

Released under the MIT License. See **[LICENSE](LICENSE)**.
