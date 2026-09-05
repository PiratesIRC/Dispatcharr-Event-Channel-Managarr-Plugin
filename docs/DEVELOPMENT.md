# Event Channel Managarr development notes

The runtime model is the first thing to understand: the plugin runs **inside
Dispatcharr's Django backend**. There is no build step, no standalone run and no
staging environment, so `apps.*`, `django.*` and `core.utils` imports resolve only
inside the container. The test suite stubs them.

The **[user guide](USER-GUIDE.md)** covers settings, rules and troubleshooting.
The **[project front page](../README.md)** describes what the plugin is.

---

A practical guide for contributors and maintainers.

---

## Overview

**Event Channel Managarr (ECM)** is a single-file Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. Channels with no current or upcoming event are hidden; channels with matching events are shown.

### Shipped artifact vs. repo tooling

The artifact that Dispatcharr loads is the `Event-Channel-Managarr/` directory:

| File | Ships? | Purpose |
|---|---|---|
The set that ships is whatever is committed under `Event-Channel-Managarr/`. Read it from
git rather than from a list here, which is how a list like this goes stale:

```bash
git ls-tree --name-only HEAD:Event-Channel-Managarr
```

| File | Ships? | Purpose |
|---|---|---|
| `Event-Channel-Managarr/plugin.py` | Yes | All plugin logic (about 4,250 lines) |
| `Event-Channel-Managarr/ecm_parsing.py` | Yes | Django-free date, time and event-window logic |
| `Event-Channel-Managarr/ecm_profiles.py` | Yes | Django-free. The channel-name format profiles, and every routing and ownership decision: `parse_group_source_map`, `build_group_profiles`, `routing_destinations` and `source_props_to_write` |
| `Event-Channel-Managarr/plugin.json` | Yes | Manifest: `fields` + `actions` arrays |
| `Event-Channel-Managarr/__init__.py` | Yes | Package marker; must export only `Plugin` |
| `Event-Channel-Managarr/README.txt` | Yes | In-container readme |
| `Event-Channel-Managarr/logo.png` | Yes | Plugin card icon |
| `README.md` | Yes | Project front page |
| `docs/` | Yes, except the files noted below | User guide, development notes, changelog, plans and designs |
| `tests/` | Yes | pytest suite (unit + contract) |
| `pyproject.toml` | Yes | ruff + pytest config |
| `.github/workflows/ci.yml` | Yes | CI (runs tests on push and pull request) |
| `bump_version.py` | **No** | Maintainer-local version bump tool (gitignored) |
| `zip.cmd` | **No** | Builds a release ZIP (gitignored, and must not be run from an agent shell: it ends in `pause`) |
| `.claude/`, `.wolf/` | **No** | Agent tooling and session state (gitignored) |
| `docs/CLAUDE-*.md` | **No** | Agent notes and hand-off prompts, gitignored because they quote a real installation |

Note that `docs/` itself is tracked and published, and only the `docs/CLAUDE-*.md`
files inside it are ignored. Anything written there is public.

---

## Architecture & Code Map

### `plugin.py`: two classes, one file

`PluginConfig` (line ~41) is a constants holder; it stores nothing dynamic. The actual plugin is `Plugin` (line ~212). Always instantiate `Plugin`, never `PluginConfig`, when smoke-testing.

Key methods inside `Plugin`:

| Method | Role |
|---|---|
| `run(action, params, context)` | Entry point for all action calls. Builds the merged settings and dispatches through a local `action_map` dict. |
| `fields` property | Returns the settings field definitions, which must mirror `plugin.json` `fields`. Dispatcharr reads this live property, not the manifest, so help text belongs here. |
| `actions` property | Returns the action definitions, which must mirror `plugin.json` `actions`. |
| `_check_channel_should_hide` | Walks the configured rule list for one channel and returns the first rule that matches. |
| `_check_hide_rule` | Evaluates a single rule tag against one channel. Every rule tag has a branch here. |
| `_start_background_scheduler` / `_stop_background_scheduler` | Run the scan at the configured times, in Dispatcharr's own system timezone. |
| `_get_or_create_managed_epg_source` | Creates and maintains the managed dummy EPG source, including the title, time and date patterns written onto it. |
| `_export_csv` | Exports scan results to `/data/exports/`. |

These names were checked against the source on 2026-09-02. An earlier version of this
table listed four methods that do not exist (`get_fields`, `_action_map`,
`_hide_rule_engine`, `_scheduler`), so confirm a name before relying on it.

ECM state files (inside the container at `/data/`):

- `event_channel_managarr_results.json`: last scan output
- `event_channel_managarr_settings.json`: saved settings (on-disk cache)
- `event_channel_managarr_last_run.json`: last-run timestamp
- `event_channel_managarr_undated_first_seen.json`: per-channel first-seen record for channels whose names carry no date. Each entry holds the channel name, the date it was first seen and, for entries written by version `1.26.2450117` or later, the exact moment. `[UndatedAge:days]` uses the date; `[UndatedEnded]` uses the moment as well, to reject an inferred event window that closed before the channel existed.
- `event_channel_managarr_ledger.jsonl`: one line per applied run, recording how many channels changed visibility. The README badge publishes the running total.
- `event_channel_managarr_scan.lock`: guards against two scans running at once
- `event_channel_managarr_version_check.json`: cache for the version check

Channel visibility is per-profile via `ChannelProfileMembership.enabled`, not a flag on `Channel` itself.

### `ecm_parsing.py`: Django-free date, time and event-window logic

This sibling module was extracted so the most bug-prone logic can be unit-tested without a
running Django or Dispatcharr environment. `plugin.py` imports it through a `sys.path` shim
and delegates to it. The module has no Django dependencies and imports with plain Python
plus `python-dateutil`; `pytz` is imported inside the one function that needs it, so the
module still imports on a machine without it.

It holds the date extraction, the clock-time extraction, the inferred event window used by
the `[UndatedEnded]` hide rule, and the decision that rule makes. Putting the decision here
rather than in `plugin.py` is deliberate: a rule left in `plugin.py` can only be tested by
reading its source, which cannot distinguish a working comparison from a broken one.

**Anything moved here must fail rather than guess.** An error handler in this
module returns "no answer" instead of substituting a smaller number. Collapsing an
unreadable duration to zero minutes does not fail open; it shortens the event window and
hides a channel earlier than any configured value asked for.

### `plugin.json`: manifest

The manifest declares two top-level arrays that must stay in sync with `plugin.py`:

- `fields`: mirrors `Plugin.get_fields()`: every setting id, type, default, label
- `actions`: mirrors `Plugin.actions`: every action id, label, event bindings

**The contract test (`tests/contract`) enforces action-id and version parity automatically.** README table coverage is a manual step (see [Adding a Setting or Action](#adding-a-setting-or-action)).

### Version in two places

`PLUGIN_VERSION` in `plugin.py` and `"version"` in `plugin.json` must always match. Format: `1.26.{DDD}{HHMM}` (day-of-year + UTC HHMM). The `bump_version.py` tool updates both atomically. The contract test will fail if they diverge.

---

## Local Development & Deploy

There is no local Python environment assumption. Development is: edit, run the tests, deploy
the committed code into the container, reload, verify.

### Deploy loop

Deploy the **whole package from the git index**, not a hand-written list of files from the
working tree. Both shortcuts have caused real problems here: a three-file list omitted
`ecm_profiles.py` when that file was the one that had changed, and copying the working tree
ships Windows line endings and any private working files sitting in the directory.

```bash
# Build the package from the committed code, with Unix line endings
git -c core.autocrlf=false -c core.eol=lf archive --format=tar \
    -o /tmp/ecm-deploy.tar HEAD:Event-Channel-Managarr

# Clear the bytecode cache so a later .pyc is evidence of a real import
docker exec -u dispatch dispatcharr sh -c \
    'rm -rf /data/plugins/event-channel-managarr/__pycache__'
```

Copy the archive in from PowerShell, which does no path rewriting:

```powershell
docker cp /tmp/ecm-deploy.tar dispatcharr:/tmp/ecm-deploy.tar
```

Extract **as `dispatch`**. `docker cp` and `docker exec` default to root, and a root-owned
file under `/data/plugins/` imports fine, so everything looks healthy while the application
cannot write it:

```bash
docker exec -u dispatch dispatcharr sh -c \
    'cd /data/plugins/event-channel-managarr && tar -xf /tmp/ecm-deploy.tar'
docker exec dispatcharr sh -c 'rm -f /tmp/ecm-deploy.tar'

# Must print 0
docker exec dispatcharr sh -c \
    'find /data/plugins/event-channel-managarr ! -user dispatch | wc -l'
```

This applies to probes too: a read-only `docker exec dispatcharr python3` that imports a
plugin module runs as root and leaves a root-owned `__pycache__`.

A matching version string is not evidence of matching code. Compare hashes of the deployed
files against the archive before believing the deploy landed.

### Reloading

The files being on disk does not mean the workers are running them. Hot-reload keys on
`plugin.json` mtime, but mtime alone triggers nothing: `loader.py` consults it only inside
`discover_plugins()`, which runs at worker start or from the plugins API, never on a timer.
So a correct deploy sits on disk with the old code running while every health signal reads
green. Use the refresh control on the Plugins page, or restart the container. **A restart
drops every in-flight stream, so check whether anything is streaming and ask first.**

```bash
# Verify the plugin loaded
docker logs dispatcharr --since 5m | grep "Plugin v"
```

A `docker logs` search that finds nothing is not evidence of absence. It retains a limited
window, `--since` reads a bare timestamp as local time so append `Z`, and on 2026-09-01 the
stream stopped showing new output entirely after a restart. Prove the probe can find
something before trusting a zero. When the log is not usable, two independent checks:
`PluginConfig.version` changing to the new value during a reload, and evaluating the
settings form through Dispatcharr's own `PluginManager.get()._normalize_fields(...)`, which
proves what the deployed code would serve.

### Git Bash / MSYS path-mangling gotchas

Running `docker exec` or `docker cp` with absolute container paths (e.g. `/data/plugins/...`) from Git Bash causes MSYS to rewrite them to Windows paths (`C:/Program Files/Git/data/...`). Three workarounds:

1. **Prefix with `MSYS_NO_PATHCONV=1`**: suppresses MSYS conversion for that command:
   ```bash
   MSYS_NO_PATHCONV=1 docker exec dispatcharr cat /data/event_channel_managarr_results.json
   ```

2. **Pipe scripts via stdin**: avoids passing the script path as an argument at all:
   ```bash
   docker exec -i dispatcharr python3 < my_script.py
   ```

3. **Django shell via stdin**: for ORM access:
   ```bash
   docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" < my_script.py
   ```

### Reading logs and the Django shell

```bash
# Tail recent logs
docker logs dispatcharr --tail 50

# Filter to ECM output only
docker logs dispatcharr --tail 200 | grep -i "event.channel\|ECM\|Plugin v"

# Open an interactive Django shell
MSYS_NO_PATHCONV=1 docker exec -it dispatcharr sh -c "cd /app && python3 manage.py shell"

# Run a one-shot ORM query from a local file
docker exec -i dispatcharr sh -c "cd /app && python3 manage.py shell" < query.py
```

### Testing settings-driven behavior in the container

**Important:** ECM's `run()` builds `merged_settings` as:

```
saved JSON file  <  live DB (cfg.settings)  <  action params
```

The DB value wins over the on-disk JSON. If you write directly to `/data/event_channel_managarr_settings.json`, that does NOT affect the active merged_settings unless `_load_settings()` is called again. To test a toggle ON/OFF, update `cfg.settings` in the DB:

```python
# Run inside the Django shell
from apps.connect.models import PluginConfig
cfg = PluginConfig.objects.get(key="event-channel-managarr")
import json
s = json.loads(cfg.settings or "{}")
s["on_m3u_refresh_enabled"] = True
cfg.settings = json.dumps(s)
cfg.save()
```

---

## Testing

There is no host Python environment required. Tests can be run in CI (recommended) or directly in the container.

### Running in CI

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs the full test suite on every push and PR. No setup needed - just push a branch or open a PR.

### Running in the container (manual)

```bash
# Copy the test dependencies into the container
MSYS_NO_PATHCONV=1 docker cp Event-Channel-Managarr/ecm_parsing.py \
    dispatcharr:/tmp/ecm_parsing.py
MSYS_NO_PATHCONV=1 docker cp tests dispatcharr:/tmp/ecm_tests
MSYS_NO_PATHCONV=1 docker cp pyproject.toml dispatcharr:/tmp/pyproject.toml

# Install test deps (once) and run
docker exec dispatcharr sh -c "
    cd /tmp &&
    pip install pytest python-dateutil --quiet &&
    python3 -m pytest ecm_tests/ -v
"
```

### Two test layers

**`tests/unit/`**: Django-free, fast. Covers the bug-prone date-parsing logic in `ecm_parsing.py`. Fixtures were captured from live plugin behavior to prevent regressions. These tests can run with just `pytest` + `python-dateutil`; no Django or Dispatcharr stack needed.

**`tests/contract/`**: Static analysis, because `plugin.py` imports Django at module scope
and cannot be imported outside the container. Verifies, among other things:

- Every action id in `Plugin.actions` appears in `plugin.json` `actions`, and the reverse.
- `PLUGIN_VERSION` in `plugin.py` matches `"version"` in `plugin.json`.
- Every constant reached as `self.DEFAULT_X` exists on the `Plugin` class, not only on
  `PluginConfig`. A missing mirror line raises only at run time.
- `ecm_parsing.py` imports nothing from Django or Dispatcharr and holds no module-level
  mutable state.
- The three copies of the US channel-name patterns agree: the literal in `plugin.py` that is
  written onto the EPG source, the copy in `ecm_profiles.py` used for routing, and the
  fallback in `ecm_parsing.py` used for a channel bound to no source.
- Every superseded pattern is still listed as a stock default, because the plugin only
  replaces a pattern it recognises as one of its own, so an unlisted one is kept for ever.
- The default hide-rule list is identical in `plugin.py`, `plugin.json` and
  `config/ecm_settings.template.json`.
- The body of a few named methods is unchanged, pinned by hash. Re-record a pin only with
  the reason and the covering tests written beside it.

**Know what a static test cannot do.** It reads structure, so it cannot tell a correct
comparison from an inverted one. Six contract tests written for the `[UndatedEnded]` rule
all passed against a version of that rule deliberately mutated to hide every channel. Where
a decision matters, move it into `ecm_parsing.py` or `ecm_profiles.py` and unit-test the
behaviour, and leave the contract test to hold the wiring in place. After writing a guard,
break it on purpose and watch it fail.

**A second trap, and it is easy to walk into.** Several existing contract tests are string
searches over a whole method, such as `assert "_epg_binding_is_reroutable" in src` over
`_reroute_claimed_channels`. Adding a SECOND code path to that same method cannot be tested
that way: the method already contains the name, so the new assertion passes against code
that does not implement the new path at all. Assert instead on the CALLS a method makes and
on the ORDER of its statements, and put the behaviour in a pure function. The per-group EPG
source work put three decisions in `ecm_profiles.py` for this reason:
`routing_destinations` (where each channel belongs, in both directions),
`source_props_to_write` (whether the plugin may rewrite a source) and
`parse_group_source_map`.

**Mutation-test a new guard, and let the harness apply several substitutions at once.** A
behaviour can be provided at more than one point, and mutating one of them then proves
nothing. Carriage-return handling in the mapping parser is provided independently by
`str.splitlines()`, by the per-line strip and by the strip on each side of the equals sign,
which produced two false "this test is vacuous" reports before the harness was changed.

---

## Adding a Setting or Action

ECM has a **duplication rule**: settings and actions are declared in two places and must be kept in sync. The contract test enforces action-id and version parity; README coverage is manual.

### Adding a setting

1. Add the field definition to `Plugin.get_fields()` in `plugin.py` (id, type, label, default, description).
2. Add the matching entry to the `"fields"` array in `plugin.json` (same id, type, default).
3. Add a row to the **Settings** table in `README.md`.
4. Handle the new setting in `run()` / the relevant handler method.

### Adding an action

1. Add the action definition to `Plugin.actions` in `plugin.py` (id, label, description, any params).
2. Add a handler method and register it in `_action_map`.
3. Add the matching entry to the `"actions"` array in `plugin.json` (same id, label).
4. Add a row to the **Actions** table in `README.md`.

### Event-bound actions

Actions can subscribe to Dispatcharr system events by declaring an `"events"` list:

```json
{
    "id": "on_m3u_refresh",
    "label": "Auto-rescan after M3U refresh",
    "events": ["m3u_refresh"]
}
```

`apps/connect/utils.py::trigger_event` iterates enabled plugins and calls the matching action whenever that event fires. Supported events include `m3u_refresh`, `epg_refresh`, `channel_start`, `channel_stop`, `client_connect`, and others. The `events` binding must appear in the `plugin.json` action entry as well.

---

## Settings Precedence

```
saved JSON file (/data/..._settings.json)
    v  overridden by
DB (PluginConfig.settings)
    v  overridden by
action params (passed to run())
```

The DB value always wins over the on-disk file. This matters for in-container testing: writing to the JSON file alone does not change active behavior. See [Testing settings-driven behavior](#testing-settings-driven-behavior-in-the-container) above for the DB update pattern.

---

## Versioning & Release

### Version scheme

`1.26.{DDD}{HHMM}`: day-of-year (zero-padded to 3 digits) + UTC hour/minute (4 digits). Examples: `1.26.1610837` (day 161, 08:37 UTC).

The version appears in **two places** that must match:
- `PLUGIN_VERSION = "..."` in `plugin.py`
- `"version": "..."` in `plugin.json`

### Bumping the version

`bump_version.py` (repo root) updates both files atomically:

```bash
# Bump to a new auto-generated version (current UTC timestamp)
PYTHONUTF8=1 python bump_version.py

# Bump to a specific version
PYTHONUTF8=1 python bump_version.py 1.26.1610837
```

> `PYTHONUTF8=1` is required on Windows - plugin files contain UTF-8 characters (emoji labels, em-dashes) that the default `cp1252` codec cannot handle.

`bump_version.py` is **intentionally gitignored**: it is a maintainer-local tool and does not ship.

### Release runbook

> The `/release` skill (`.claude/skills/release/SKILL.md`) walks through this as an interactive checklist.

**Before tagging anything**, review open issues and PRs on both repos. Do not cut a release without confirming that no open bug, in-flight fix, or conflicting PR should be included first.

Use the `gh` command line tool. An earlier version of this page told you to read the token
out of Git Credential Manager; that is blocked, and reading a stored credential to reach the
API is the wrong approach anyway.

```bash
gh api "repos/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/issues?state=open&per_page=100" \
  --jq '.[] | "#\(.number) \(if .pull_request then "PR" else "issue" end) \(.title)"'

gh api "repos/Dispatcharr/Plugins/pulls?state=open&per_page=100" \
  --jq '.[] | select(.title | test("event.channel";"i")) | "#\(.number) \(.title)"'
```

The issues endpoint returns both issues and pull requests; a pull request carries a
`pull_request` key. Summarise the results and confirm scope before proceeding.

**Full release steps:**

1. Review open issues and pull requests on both repositories, as above. Confirm scope with
   the operator.
2. **Run the publish audit** before pushing and again before cutting the release:
   `python ../.claude/skills/pre-publish-audit/audit_publish.py --worktree --rules .publish-audit.json`.
   It scans the current tree only, while a push publishes every reachable commit, so scan
   the new commits separately with `git rev-list --objects origin/main..HEAD` plus
   `git cat-file -p` per blob. A clean result means nothing until you have watched the rules
   fire: `python ../.claude/skills/pre-publish-audit/verify_deny_rules.py --rules .publish-audit.json`.
3. Bump the version: `PYTHONUTF8=1 python bump_version.py`. Never hand-edit a version string.
4. Confirm `PLUGIN_VERSION` in `plugin.py` matches `"version"` in `plugin.json`. The contract
   test `tests/contract/test_manifest_parity.py` checks this.
5. Update `docs/CHANGELOG.md` with the new version and a link to its release notes.
6. Merge to `main`, then tag: `git tag vX.YY.DDDHHMM && git push origin main --tags`. Tags in
   this repository carry the `v` prefix. Confirm the pushed authorship against what GitHub
   reports, not against the local commit.
7. Build the release asset **from the tag, using git archive**, so it contains the committed
   bytes with Unix line endings:

   ```bash
   git -c core.autocrlf=false -c core.eol=lf archive --format=zip \
       --prefix=Event-Channel-Managarr/ -o Event-Channel-Managarr.zip vX.YY.DDDHHMM
   python scripts/validate_zip.py Event-Channel-Managarr.zip   # must print OK
   ```

   **Do not run `zip.cmd` from an agent shell**: it ends in `pause` and uses 7-Zip in add
   mode, so it can carry stale files from a previous build. The `--prefix` is required;
   every previous release asset has that top-level directory and a flat archive would
   differ from all of them. Compare the entry names against the previous release's asset
   every time. `validate_zip.py` guards against backslash path separators, which fail to
   install on Dispatcharr's Linux host, but it does **not** check line endings; check those
   separately by reading raw bytes.
8. Create the GitHub release with that ZIP as an asset. Download the asset back and compare
   it byte for byte with what you uploaded.
9. Update the marketplace listing. **This listing is in `external` mode**, so the pull
   request to `Dispatcharr/Plugins` changes only the `version` field in
   `plugins/event-channel-managarr/plugin.json`. It does not copy plugin source anywhere; an
   earlier version of this page described the `standard` mode procedure, which is wrong for
   this plugin and would create a second copy that drifts.
   - Always branch from `upstream/main`, never from the fork's main, which may carry stale
     unmerged upstream changes that contaminate the diff.
   - `source_url` keeps the `v` before `{version}` for this repository. The wrong form
     returns 404 at publish time, not in the pull request, so nothing tells you.
   - **A merged pull request is not a published listing.** Confirm by fetching
     `https://dispatcharr.github.io/Plugins/manifest.json` and reading `latest_version`, not
     `version`. The publish job does not retry, and the Pages deploy lags the merge.

---

## Repo Conventions

### What's gitignored and why

| Path | Reason |
|---|---|
| `.claude/` | AI tooling (Claude Code config, skills, agents) - local only |
| `.wolf/` | OpenWolf session memory - local only |
| `.serena/` | Serena MCP config - local only |
| `docs/` | Internal design specs and plans - not user-facing |
| `CLAUDE.md`, `GEMINI.md` | AI context files - local only |
| `bump_version.py` | Maintainer-local tool - not needed by contributors |
| `zip.cmd` | Release packaging - maintainer-local |
| `Event-Channel-Managarr.zip` | Build artifact - not committed |

**Note on `.claude/` skills and agents:** `.claude/skills/` and `.claude/agents/` are gitignored by default, so the `/release`, `/deploy-plugin`, and `plugin-contract-reviewer` automation files work locally but are not committed to the repo. To share them with other maintainers, un-ignore `.claude/skills` and `.claude/agents` in `.gitignore`.

### Branching and commits

- One branch per feature/fix: `feature/my-thing`, `fix/my-bug`, `chore/my-task`
- Commit style: `type: description (#issue)` where type is `feat`, `fix`, `chore`, `refactor`, `test`, `docs`
- Open a PR for review before merging to `main`

---

## Roadmap / Further Improvements

These items were identified during a workflow review as worthwhile future work:

- **Finish splitting the monolith.** `ecm_parsing.py` was the first extraction. The hide-rule engine, dummy-EPG logic, and scheduler are good candidates to extract the same way - independently testable modules imported by `plugin.py`.
- **Tighten ruff to blocking.** The linter config is currently permissive while legacy lint is cleaned up. Once the backlog is clear, make ruff failures block CI.
- **Generate README tables from plugin.json.** The settings and actions tables in `README.md` mirror `plugin.json` by hand. A small script (or pre-commit hook) could regenerate them automatically, eliminating drift.
- **Install `gh` CLI or a GitHub MCP.** The `gh` CLI is not installed; release steps use raw REST API calls. Installing `gh` or a GitHub MCP would reduce friction and let the `/release` skill automate more steps end-to-end.
- **Docker MCP or helper script.** The `docker cp` + restart loop and MSYS path-mangling workarounds add friction. A Docker MCP or a single `deploy.sh` wrapper (handling `MSYS_NO_PATHCONV` internally) would smooth this out.
- **Fix or disable the noisy `claude-mem` hook** if it produces spurious output during normal sessions.

---

## Contributing

Pull requests welcome. To submit changes:

### To this repo (`PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin`)

0. **Check open issues and PRs first**: review open issues + PRs on this repo (and any open `[event-channel-managarr]` PRs on `Dispatcharr/Plugins`) before cutting a release, so in-flight reports/fixes are included and nothing conflicts or duplicates.
1. Bump version: `python3 bump_version.py` (auto-stamps with current UTC day-of-year + HHMM).
2. Commit, push, tag, and release:
   ```bash
   git tag <version> && git push origin <version>
   gh release create <version> --title "v<version>" --notes "..."
   gh release upload <version> Event-Channel-Managarr.zip
   ```

### To the upstream marketplace (`Dispatcharr/Plugins`)

Updates also need to be PR'd to `Dispatcharr/Plugins` so the plugin updates in users' Dispatcharr UIs. The repo's GitHub Actions validator enforces strict rules - failing any blocks the merge:

| Check | Requirement |
| :--- | :--- |
| **PR title** | Must match `[event-channel-managarr]: <description>`. The `validate-title` job fails on any other format. **Most common trip-up.** |
| **Version bump** | `plugin.json` `version` must be greater than the version on upstream `main` for any code/asset change. Metadata-only edits are exempt. |
| **Required `plugin.json` fields** | `name`, `version`, `description`, `author`, `license` (SPDX). |
| **Authorship** | PR author's GitHub username must appear in `author` or `maintainers`, or the `close-unauthorized` job auto-closes the PR. |
| **Folder name** | `plugins/event-channel-managarr/` (lowercase-kebab) - note this differs from the `Event-Channel-Managarr/` capitalization used in this repo's zip. |

Workflow:

```bash
# In your fork of Dispatcharr/Plugins:
git fetch upstream && git checkout main && git merge upstream/main --ff-only && git push origin main
git checkout -b ecm-v<version>
cp <this-repo>/Event-Channel-Managarr/plugin.{py,json} plugins/event-channel-managarr/
git commit -am "[event-channel-managarr]: ..."
git push -u origin ecm-v<version>
gh pr create --repo Dispatcharr/Plugins --base main \
    --title "[event-channel-managarr]: Bump to v<version> - <summary>" \
    --body "..."
```

On merge, upstream automation builds the zip + checksums and updates `manifest.json` on the `releases` branch - do not touch that branch manually.
