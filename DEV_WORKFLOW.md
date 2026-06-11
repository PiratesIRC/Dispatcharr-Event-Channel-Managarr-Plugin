# Event Channel Managarr — Developer Workflow

A practical guide for contributors and maintainers.

---

## Overview

**Event Channel Managarr (ECM)** is a single-file Dispatcharr plugin that automatically manages channel visibility based on EPG data and channel names. Channels with no current or upcoming event are hidden; channels with matching events are shown.

### Shipped artifact vs. repo tooling

The artifact that Dispatcharr loads is the `Event-Channel-Managarr/` directory:

| File | Ships? | Purpose |
|---|---|---|
| `Event-Channel-Managarr/plugin.py` | Yes | All plugin logic (~3,300 lines) |
| `Event-Channel-Managarr/ecm_parsing.py` | Yes | Django-free date/time parsing logic |
| `Event-Channel-Managarr/plugin.json` | Yes | Manifest: `fields` + `actions` arrays |
| `Event-Channel-Managarr/__init__.py` | Yes | Package marker |
| `Event-Channel-Managarr/README.txt` | Yes | In-container readme |
| `README.md` | Yes | User-facing documentation |
| `tests/` | Yes | pytest suite (unit + contract) |
| `pyproject.toml` | Yes | ruff + pytest config |
| `.github/workflows/ci.yml` | Yes | CI (runs tests on push/PR) |
| `bump_version.py` | **No** | Maintainer-local version bump tool (gitignored) |
| `zip.cmd` | **No** | Builds the release ZIP (gitignored) |
| `.claude/`, `.wolf/`, `docs/` | **No** | AI tooling and internal docs (gitignored) |

---

## Architecture & Code Map

### `plugin.py` — two classes, one file

`PluginConfig` (line ~41) is a constants holder; it stores nothing dynamic. The actual plugin is `Plugin` (line ~212). Always instantiate `Plugin`, never `PluginConfig`, when smoke-testing.

Key methods inside `Plugin`:

| Method | Role |
|---|---|
| `run(action_id, params, context)` | Entry point for all action calls. Builds `merged_settings`, dispatches via `_action_map`. |
| `_action_map` | Dict mapping action id strings → handler methods. |
| `get_fields()` | Returns the settings field definitions (must mirror `plugin.json` `fields`). |
| `actions` property | Returns the action definitions (must mirror `plugin.json` `actions`). |
| `_hide_rule_engine` | Evaluates channel name patterns and EPG state to decide hide/show. |
| `_scheduler` | Handles the cron-like schedule for automatic scans. |
| `dummy_epg_*` methods | Manages dummy EPG source creation and custom program templates. |
| `_export_csv` | Exports scan results to `/data/exports/`. |

ECM state files (inside the container at `/data/`):

- `event_channel_managarr_results.json` — last scan output
- `event_channel_managarr_settings.json` — saved settings (on-disk cache)
- `event_channel_managarr_last_run.json` — last-run timestamp

Channel visibility is per-profile via `ChannelProfileMembership.enabled`, not a flag on `Channel` itself.

### `ecm_parsing.py` — Django-free date logic

This sibling module was extracted so date-parsing logic can be unit-tested without a running Django/Dispatcharr environment. `plugin.py` imports it via a `sys.path` shim and delegates all date extraction to it. The module has no Django dependencies and can be imported with plain Python + `python-dateutil`.

### `plugin.json` — manifest

The manifest declares two top-level arrays that must stay in sync with `plugin.py`:

- `fields` — mirrors `Plugin.get_fields()`: every setting id, type, default, label
- `actions` — mirrors `Plugin.actions`: every action id, label, event bindings

**The contract test (`tests/contract`) enforces action-id and version parity automatically.** README table coverage is a manual step (see [Adding a Setting or Action](#adding-a-setting-or-action)).

### Version in two places

`PLUGIN_VERSION` in `plugin.py` and `"version"` in `plugin.json` must always match. Format: `1.26.{DDD}{HHMM}` (day-of-year + UTC HHMM). The `bump_version.py` tool updates both atomically. The contract test will fail if they diverge.

---

## Local Development & Deploy

There is no local Python environment assumption. Development is: edit → copy into container → restart → verify.

### Quick deploy loop

```bash
# Copy all three plugin files into the running container
MSYS_NO_PATHCONV=1 docker cp Event-Channel-Managarr/plugin.py \
    dispatcharr:/data/plugins/event-channel-managarr/plugin.py

MSYS_NO_PATHCONV=1 docker cp Event-Channel-Managarr/plugin.json \
    dispatcharr:/data/plugins/event-channel-managarr/plugin.json

MSYS_NO_PATHCONV=1 docker cp Event-Channel-Managarr/ecm_parsing.py \
    dispatcharr:/data/plugins/event-channel-managarr/ecm_parsing.py

# Restart (Dispatcharr imports plugin code at startup — restart is required)
docker restart dispatcharr

# Wait ~18 seconds, then verify the plugin loaded
docker logs dispatcharr --since 30s | grep "Plugin v"
```

> The `/deploy-plugin` skill (`.claude/skills/deploy-plugin/SKILL.md`) automates this entire loop.

### Git Bash / MSYS path-mangling gotchas

Running `docker exec` or `docker cp` with absolute container paths (e.g. `/data/plugins/...`) from Git Bash causes MSYS to rewrite them to Windows paths (`C:/Program Files/Git/data/...`). Three workarounds:

1. **Prefix with `MSYS_NO_PATHCONV=1`** — suppresses MSYS conversion for that command:
   ```bash
   MSYS_NO_PATHCONV=1 docker exec dispatcharr cat /data/event_channel_managarr_results.json
   ```

2. **Pipe scripts via stdin** — avoids passing the script path as an argument at all:
   ```bash
   docker exec -i dispatcharr python3 < my_script.py
   ```

3. **Django shell via stdin** — for ORM access:
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

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs the full test suite on every push and PR. No setup needed — just push a branch or open a PR.

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

**`tests/unit/`** — Django-free, fast. Covers the bug-prone date-parsing logic in `ecm_parsing.py`. Fixtures were captured from live plugin behavior to prevent regressions. These tests can run with just `pytest` + `python-dateutil`; no Django or Dispatcharr stack needed.

**`tests/contract/`** — Static analysis. Verifies:
- Every action id in `Plugin.actions` (plugin.py) appears in `plugin.json` `actions`, and vice versa.
- `PLUGIN_VERSION` in plugin.py matches `"version"` in plugin.json.
- Basic structural sanity of both files.

The contract tests run via AST/JSON parsing — no Django needed, and they run in seconds. If you add or rename an action and forget to update plugin.json (or vice versa), the contract test catches it immediately.

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
    ↓  overridden by
DB (PluginConfig.settings)
    ↓  overridden by
action params (passed to run())
```

The DB value always wins over the on-disk file. This matters for in-container testing: writing to the JSON file alone does not change active behavior. See [Testing settings-driven behavior](#testing-settings-driven-behavior-in-the-container) above for the DB update pattern.

---

## Versioning & Release

### Version scheme

`1.26.{DDD}{HHMM}` — day-of-year (zero-padded to 3 digits) + UTC hour/minute (4 digits). Examples: `1.26.1610837` (day 161, 08:37 UTC).

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

> `PYTHONUTF8=1` is required on Windows — plugin files contain UTF-8 characters (emoji labels, em-dashes) that the default `cp1252` codec cannot handle.

`bump_version.py` is **intentionally gitignored** — it is a maintainer-local tool and does not ship.

### Release runbook

> The `/release` skill (`.claude/skills/release/SKILL.md`) walks through this as an interactive checklist.

**Before tagging anything**, review open issues and PRs on both repos. Do not cut a release without confirming that no open bug, in-flight fix, or conflicting PR should be included first.

```bash
# Pull PAT from Git Credential Manager (do NOT print the token)
TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill | sed -n 's/^password=//p')

# Open issues + PRs on this repo
curl -s -H "Authorization: token $TOKEN" \
    "https://api.github.com/repos/PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin/issues?state=open&per_page=100"

# Open PRs on the upstream marketplace repo
curl -s -H "Authorization: token $TOKEN" \
    "https://api.github.com/repos/Dispatcharr/Plugins/pulls?state=open&per_page=100"
```

Summarize the results and confirm scope before proceeding.

**Full release steps:**

1. Review open issues/PRs on both repos (above). Confirm scope with the user.
2. Bump version: `PYTHONUTF8=1 python bump_version.py [version]`
3. Verify `PLUGIN_VERSION` in plugin.py matches `"version"` in plugin.json (the contract test checks this).
4. Commit: `git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json && git commit -m "chore: bump version to X.XX.XXXXXXX"`
5. Tag and push: `git tag vX.XX.XXXXXXX && git push origin main --tags`
6. Build the ZIP: `zip.cmd` (produces `Event-Channel-Managarr.zip`)
7. Create the GitHub release on `PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin` with the ZIP as an asset.
8. Open (or update) the marketplace PR to `Dispatcharr/Plugins`, copying `Event-Channel-Managarr/` into `plugins/event-channel-managarr/`.
   - **Important:** always branch from `upstream/main`, not from the fork's main — the fork's main may carry stale unmerged upstream changes that contaminate the PR diff.

---

## Repo Conventions

### What's gitignored and why

| Path | Reason |
|---|---|
| `.claude/` | AI tooling (Claude Code config, skills, agents) — local only |
| `.wolf/` | OpenWolf session memory — local only |
| `.serena/` | Serena MCP config — local only |
| `docs/` | Internal design specs and plans — not user-facing |
| `CLAUDE.md`, `GEMINI.md` | AI context files — local only |
| `bump_version.py` | Maintainer-local tool — not needed by contributors |
| `zip.cmd` | Release packaging — maintainer-local |
| `Event-Channel-Managarr.zip` | Build artifact — not committed |

**Note on `.claude/` skills and agents:** `.claude/skills/` and `.claude/agents/` are gitignored by default, so the `/release`, `/deploy-plugin`, and `plugin-contract-reviewer` automation files work locally but are not committed to the repo. To share them with other maintainers, un-ignore `.claude/skills` and `.claude/agents` in `.gitignore`.

### Branching and commits

- One branch per feature/fix: `feature/my-thing`, `fix/my-bug`, `chore/my-task`
- Commit style: `type: description (#issue)` where type is `feat`, `fix`, `chore`, `refactor`, `test`, `docs`
- Open a PR for review before merging to `main`

---

## Roadmap / Further Improvements

These items were identified during a workflow review as worthwhile future work:

- **Finish splitting the monolith.** `ecm_parsing.py` was the first extraction. The hide-rule engine, dummy-EPG logic, and scheduler are good candidates to extract the same way — independently testable modules imported by `plugin.py`.
- **Tighten ruff to blocking.** The linter config is currently permissive while legacy lint is cleaned up. Once the backlog is clear, make ruff failures block CI.
- **Generate README tables from plugin.json.** The settings and actions tables in `README.md` mirror `plugin.json` by hand. A small script (or pre-commit hook) could regenerate them automatically, eliminating drift.
- **Install `gh` CLI or a GitHub MCP.** The `gh` CLI is not installed; release steps use raw REST API calls. Installing `gh` or a GitHub MCP would reduce friction and let the `/release` skill automate more steps end-to-end.
- **Docker MCP or helper script.** The `docker cp` + restart loop and MSYS path-mangling workarounds add friction. A Docker MCP or a single `deploy.sh` wrapper (handling `MSYS_NO_PATHCONV` internally) would smooth this out.
- **Fix or disable the noisy `claude-mem` hook** if it produces spurious output during normal sessions.
