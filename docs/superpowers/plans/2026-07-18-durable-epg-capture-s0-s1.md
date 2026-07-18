# Durable Multi-Timezone EPG — S0+S1 Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the working-but-hand-made DAZN GMT EPG configuration into versioned, tested code, and prove the routing model against live data — without editing `plugin.py`.

**Architecture:** Two slices. S0 commits the artifacts that let a rebuilt box be restored with one command (real-name fixture, bootstrap script, config template). S1 adds a pure stdlib-only `ecm_profiles.py` that decides which timezone profile owns a channel name, tested against the committed fixture, then proves it in-container read-only.

**Tech Stack:** Python 3 (stdlib only for the shipped module), pytest, Django ORM via `manage.py shell` for in-container verification only, Docker, Windows PowerShell 5.1.

> **rev 2 — revised after four adversarial reviews of rev 1.** Rev 1 had five Critical defects: an extraction command that cannot run on this box and whose natural fix silently corrupts the fixture; two committed artifacts disagreeing about which channels `dazn_gmt` owns; a bootstrap script that destroys working config; and a gate that could print `GATE PASSED` while `dazn_gmt` claimed zero channels. Sections marked **[REV1 WAS WRONG]** correct a specific error.

## Global Constraints

- **NO edits to `Event-Channel-Managarr/plugin.py` or `plugin.json`.** If a task seems to need one, stop and escalate.
- **`ecm_profiles.py` is stdlib-only.** No `apps.*`, `django.*`, `core.utils`; no non-stdlib module-level imports except `regex` inside a `try/except ImportError`. Enforced by Task 7.
- **No module-level mutable state in `ecm_profiles.py`** — no `lru_cache`, no caches, no registry built at import. Dispatcharr's loader purges and re-imports sibling modules (`apps/plugins/loader.py:832`).
- **Never commit credentials.** `/data/event_channel_managarr_settings.json` holds a plaintext `dispatcharr_password`/`dispatcharr_username`. This repo is PUBLIC.
- **Patterns are STORED in JS named-group form `(?<name>)`**, not `(?P<name>)` (issue #21).
- **Every `docker exec` that writes under `/data` must use `-u dispatch`.** Root-owned `/data` files silently block the uWSGI workers.
- **PowerShell here is 5.1** (`5.1.26100.8655`). `utf8NoBOM` does NOT exist; `-Encoding UTF8` writes a BOM. Never round-trip UTF-8 through PS 5.1 text cmdlets.
- Repo root: `C:\Users\User\docker\Event-Channel-Managarr`. Inner folder: `Event-Channel-Managarr/`. Branch: `feat/durable-epg-capture`.
- Spec: `docs/superpowers/specs/2026-07-18-durable-multi-timezone-epg-design.md`.
- **Shell marking:** every command block is labelled `POWERSHELL` or `BASH`. They are different tools; do not mix.

## Verified ground truth (measured live 2026-07-18 — do not re-derive)

```
group 1915 "US: PPV"          278 channels
  dazn (GMT)                   48   -> dazn_gmt
  PPV EVENT 70 + LIVE EVENT 34 104   -> us_et   (SET IDENTITY verified, not just count)
  NO EVENT STREAMING NOW       51   -> unclaimed
  UFC                          55   -> unclaimed
  Boxing                        8   -> unclaimed
  US:/TNT SPORTS BOX OFFICE     8   -> unclaimed   (permanent static channels)
  #### headers ####             4   -> unclaimed
live EPGSource id 42 "DAZN PPV Dummy (GMT)"  99 channels bound, 100 EPGData rows
  all 99 tvg_ids already equal dazn_gmt_<own channel id>   (bootstrap is a true no-op today)
source 18 "ECM Managed Dummy"  28 channels bound, all of which route us_et
regex module: PRESENT in container (2026.5.9), ABSENT on dev machine
```

## File Structure

| Path | Responsibility | Slice |
|---|---|---|
| `.gitattributes` | Pin LF line endings | S0 |
| `tests/fixtures/us_ppv_channel_names.txt` | The 278 real names — the corpus every routing assertion runs against | S0 |
| `config/ecm_settings.template.json` | Restorable settings, credential-free | S0 |
| `tests/contract/test_config_template.py` | Template keys are real field ids; no denylisted key | S0 |
| `scripts/bootstrap_ecm.py` | One-command restore. Pure merge logic extracted for testing | S0 |
| `tests/unit/test_bootstrap_merge.py` | Idempotency + credential handling of the pure merge | S0 |
| `Event-Channel-Managarr/ecm_profiles.py` | Pure: `Profile`, `PROFILES`, `route()`, regex shim | S1 |
| `tests/unit/test_ecm_profiles.py` | Fixture-driven routing + extraction tests | S1 |
| `tests/contract/test_module_purity.py` | AST purity guards, plus self-checks proving each guard bites | S1 |
| `scripts/verify_routing_incontainer.py` | Read-only in-container gate | S1 |

---

## Task 1: Pin LF line endings

**[REV1 WAS WRONG:** rev 1 claimed "this repo has never been renormalized, so text blobs carry CRLF." False. `core.autocrlf=true` has been normalizing on commit all along — 29 of 31 tracked files have **zero** CR bytes. `--renormalize` rewrites exactly ONE file: `Event-Channel-Managarr/__init__.py` (1 CR byte). `logo.png` is the only binary and is protected by `*.png binary`.**]**

The value here is forward-looking: `.gitattributes` guarantees `ecm_profiles.py` lands LF.

**Files:** Create `.gitattributes`

- [ ] **Step 1: Create `.gitattributes`**

```
* text=auto eol=lf
*.png binary
*.zip binary
```

- [ ] **Step 2: Renormalize (BASH, from repo root)**

```bash
cd /c/Users/User/docker/Event-Channel-Managarr
git add --renormalize .
git status --short
```
Expected: one staged modification `M  Event-Channel-Managarr/__init__.py`, plus untracked
`?? .gitattributes` (created in Step 1; `--renormalize` does not stage new files) and
`?? message.txt` (a pre-existing scratch file, not ours). If any OTHER file shows as modified,
stop and report — the premise has changed.

- [ ] **Step 3: Commit (BASH)**

```bash
cd /c/Users/User/docker/Event-Channel-Managarr
git add .gitattributes && git add --renormalize .
git commit -m "chore: pin LF line endings for future files

Note: this repo was ALREADY effectively LF (core.autocrlf normalizes on
commit); 29 of 31 tracked files had zero CR bytes. Renormalize touches only
__init__.py's single CRLF line. The value is forward-looking: new files such
as ecm_profiles.py are guaranteed LF, which matters for byte-identical
vendoring and is invisible to validate_zip.py."
```

---

## Task 2: Capture the 278-name fixture

**[REV1 WAS WRONG:** the extraction command used `Set-Content -Encoding utf8NoBOM`, which does not exist in PowerShell 5.1 and hard-errors. The natural repair (`-Encoding UTF8`) writes a BOM, producing 279 lines whose first is a lone `﻿` — and rev 1's own `test_fixture_has_no_blank_lines` **passed** on it, because U+FEFF is category `Cf`, not whitespace. Same failure family as bug-105.**]**

**Files:** Create `tests/fixtures/us_ppv_channel_names.txt`, `tests/unit/test_fixture_integrity.py`

**Interfaces:**
- Produces: the fixture — 278 lines, one name each, UTF-8 **no BOM**, LF. Consumed by Tasks 6 and 8.

- [ ] **Step 1: Extract (POWERSHELL, from repo root)**

Uses a `NAME>` sentinel rather than a noise denylist (immune to new startup banners) and .NET `WriteAllText` for BOM-free LF regardless of PowerShell edition. READ-ONLY query.

```powershell
New-Item -ItemType Directory -Force tests\fixtures | Out-Null
$names = docker exec dispatcharr python manage.py shell -c "
from apps.channels.models import Channel
import sys
sys.stdout.reconfigure(encoding='utf-8')
for n in Channel.objects.filter(channel_group_id=1915).order_by('id').values_list('name', flat=True):
    print('NAME>' + n)
" | Where-Object { $_ -like 'NAME>*' } | ForEach-Object { $_.Substring(5) }

Write-Host "captured: $($names.Count)"
if ($names.Count -ne 278) { throw "expected 278 names, got $($names.Count)" }

[System.IO.File]::WriteAllText(
    (Join-Path $PWD 'tests\fixtures\us_ppv_channel_names.txt'),
    (($names -join "`n") + "`n"),
    (New-Object System.Text.UTF8Encoding($false)))
Write-Host "written"
```

- [ ] **Step 2: Verify the bytes (BASH)**

```bash
cd /c/Users/User/docker/Event-Channel-Managarr
wc -l < tests/fixtures/us_ppv_channel_names.txt
head -c 3 tests/fixtures/us_ppv_channel_names.txt | xxd | head -1
grep -c "(GMT)" tests/fixtures/us_ppv_channel_names.txt
grep -c $'\r' tests/fixtures/us_ppv_channel_names.txt || echo "no CR (good)"
```
Expected: `278`; first bytes must NOT be `efbb bf`; `48`; `no CR (good)`.

If the count is not 278, do NOT assume the lineup changed — first confirm the file has no BOM and no stray banner line. The group was verified at exactly 278.

- [ ] **Step 3: Write the integrity test**

```python
# tests/unit/test_fixture_integrity.py
"""The channel-name fixture is the corpus every routing assertion runs against.

If it drifts or is silently corrupted, every downstream assertion becomes
meaningless, so its shape is pinned here separately from any routing logic.

The BOM checks are not paranoia: PowerShell 5.1 cannot write BOM-free UTF-8 via
Set-Content, and a leading U+FEFF is category Cf -- not whitespace -- so it
survives .strip() and is invisible to both the eye and a naive blank-line check.
"""

from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"

# Measured live 2026-07-18. These pin the CORPUS, not the routing rules.
EXPECTED_TOTAL = 278
EXPECTED_FAMILIES = {
    "dazn (GMT)": 48,
    "legacy PPV EVENT": 70,
    "legacy LIVE EVENT": 34,
    "idle NO EVENT slot": 51,
    "UFC": 55,
    "Boxing": 8,
    "BOX OFFICE static": 8,
    "#### header ####": 4,
}


def _names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _classify(names):
    return {
        "dazn (GMT)": [n for n in names if "(GMT)" in n],
        "legacy PPV EVENT": [n for n in names if n.startswith("PPV EVENT")],
        "legacy LIVE EVENT": [n for n in names if n.startswith("LIVE EVENT")],
        "idle NO EVENT slot": [n for n in names if n.startswith("NO EVENT STREAMING NOW")],
        "UFC": [n for n in names if n.startswith("UFC")],
        "Boxing": [n for n in names if n.startswith("Boxing")],
        "BOX OFFICE static": [n for n in names if "BOX OFFICE" in n],
        "#### header ####": [n for n in names if n.startswith("#")],
    }


def test_fixture_size():
    names = _names()
    assert len(names) == EXPECTED_TOTAL, f"expected {EXPECTED_TOTAL}, got {len(names)}"


def test_fixture_has_no_bom():
    """A BOM survives .strip() and would silently break the ^-anchored selector
    on the first name, shifting routing counts by one with no visible cause."""
    raw = FIXTURE.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "fixture starts with a UTF-8 BOM"
    assert not _names()[0].startswith("﻿")


def test_fixture_has_no_blank_or_whitespace_only_lines():
    names = _names()
    bad = [i for i, n in enumerate(names) if not n.strip("﻿ \t")]
    assert not bad, f"blank/BOM-only lines at indices {bad}"


def test_fixture_has_no_stray_whitespace():
    dirty = [n for n in _names() if n != n.strip()]
    assert not dirty, f"names with leading/trailing whitespace: {dirty[:5]!r}"


def test_fixture_has_no_duplicates():
    """Per-name set assertions elsewhere silently deduplicate, so a capture error
    that doubled a name would otherwise be invisible."""
    names = _names()
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate names: {dupes[:5]}"


def test_fixture_family_breakdown_is_exact():
    """The spec's family table is the durable knowledge artifact this project
    exists to create. Pin every family, not just one."""
    actual = {k: len(v) for k, v in _classify(_names()).items()}
    assert actual == EXPECTED_FAMILIES, f"family drift: {actual} != {EXPECTED_FAMILIES}"


def test_families_account_for_every_name():
    names = _names()
    fams = _classify(names)
    covered = set().union(*fams.values())
    uncovered = set(names) - covered
    assert not uncovered, f"names in no known family: {sorted(uncovered)[:5]}"
```

- [ ] **Step 4: Run (BASH)**

Run: `python -m pytest tests/unit/test_fixture_integrity.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit (BASH)**

```bash
git add tests/fixtures/us_ppv_channel_names.txt tests/unit/test_fixture_integrity.py
git commit -m "test: capture the real 278 US: PPV channel names as a fixture

Written via .NET WriteAllText, not PowerShell Set-Content: PS 5.1 has no
utf8NoBOM and -Encoding UTF8 emits a BOM that survives .strip() (category Cf),
which would corrupt the corpus invisibly.

Pins the exact family breakdown (48/70/34/51/55/8/8/4) rather than one family's
count, and asserts every name falls in a known family."
```

---

## Task 3: Config template + credential denylist

**Files:** Create `config/ecm_settings.template.json`, `tests/contract/test_config_template.py`

**Background:** `plugin.json` has a `fields` array (NOT `settings`); ids starting `_section_` are UI headers, not settings. Live `PluginConfig.settings` holds 9 keys.

- [ ] **Step 1: Write the failing contract test**

```python
# tests/contract/test_config_template.py
"""The committed settings template must be restorable AND credential-free.

/data/event_channel_managarr_settings.json holds a plaintext dispatcharr_password
and dispatcharr_username. This repository is public.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "config" / "ecm_settings.template.json"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"

DENYLIST = {
    "dispatcharr_password", "dispatcharr_username", "dispatcharr_url",
    "timezone", "event", "payload",
}

# Keys whose value is environment-specific and MUST ship as a placeholder, so
# that running bootstrap without editing them cannot silently overwrite a
# working config with someone else's values.
MUST_BE_PLACEHOLDER = {"channel_profile_name", "channel_groups"}
PLACEHOLDER_PREFIX = "REPLACE_ME"


def _template():
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _plugin_field_ids():
    data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    return {f["id"] for f in data["fields"]
            if "id" in f and not f["id"].startswith("_section_")}


def test_template_exists():
    assert TEMPLATE.exists(), f"missing {TEMPLATE}"


def test_no_denylisted_keys():
    leaked = set(_template()) & DENYLIST
    assert not leaked, f"denylisted keys in committed template: {sorted(leaked)}"


def test_no_credential_shaped_keys():
    suspicious = [k for k in _template()
                  if any(t in k.lower() for t in ("pass", "secret", "token", "auth", "cred"))]
    assert not suspicious, f"credential-shaped keys: {suspicious}"


def _looks_like_a_secret(value):
    """Mixed-case + digit, no separators, >=12 chars.

    Calibrated against the real template AND real credential shapes. An earlier
    version used fullmatch(r"[A-Za-z0-9+/=_-]{16,}") which flagged
    "America/New_York" -- exactly 16 chars of that class -- so the test failed on
    the very template this task tells you to write. Timezone names, prose and
    CSV values are excluded by the separator check; a 15-char password like
    "2NhqS8vGw4HwYeg" is still caught.
    """
    if not isinstance(value, str) or len(value) < 12:
        return False
    if value.startswith(PLACEHOLDER_PREFIX):
        return False
    if any(sep in value for sep in ("/", " ", ",")):
        return False
    return bool(re.search(r"[a-z]", value)
                and re.search(r"[A-Z]", value)
                and re.search(r"\d", value))


def test_no_value_looks_like_a_secret():
    """Defence in depth: a high-entropy opaque string under an innocent key."""
    bad = [k for k, v in _template().items() if _looks_like_a_secret(v)]
    assert not bad, f"values that look like secrets: {bad}"


def test_the_secret_heuristic_actually_bites():
    """A guard that never fires is not a guard."""
    assert _looks_like_a_secret("2NhqS8vGw4HwYeg")
    assert _looks_like_a_secret("ghp_A1b2C3d4E5f6")
    assert not _looks_like_a_secret("America/New_York")
    assert not _looks_like_a_secret("lowest_number")


def test_all_keys_are_real_plugin_fields():
    unknown = set(_template()) - _plugin_field_ids()
    assert not unknown, f"template keys that are not plugin.json field ids: {sorted(unknown)}"


def test_environment_specific_keys_are_placeholders():
    """These MUST NOT carry real values. bootstrap refuses to write a placeholder,
    so shipping a real value here is what would let it clobber a working config."""
    t = _template()
    for key in MUST_BE_PLACEHOLDER:
        assert key in t, f"template missing {key}"
        assert str(t[key]).startswith(PLACEHOLDER_PREFIX), (
            f"{key} must ship as a {PLACEHOLDER_PREFIX}* placeholder, got {t[key]!r}"
        )


def test_template_covers_the_epg_critical_settings():
    missing = {"manage_dummy_epg", "dummy_epg_event_timezone",
               "dummy_epg_event_duration_hours", "channel_groups",
               "channel_profile_name", "scheduled_times"} - set(_template())
    assert not missing, f"template missing required settings: {sorted(missing)}"
```

- [ ] **Step 2: Run to verify it fails (BASH)**

Run: `python -m pytest tests/contract/test_config_template.py -v`
Expected: FAIL — `test_template_exists`: missing file.

- [ ] **Step 3: Create the template**

```json
{
  "manage_dummy_epg": true,
  "override_existing_epg": true,
  "auto_set_dummy_epg_on_hide": true,
  "dummy_epg_event_timezone": "America/New_York",
  "dummy_epg_event_duration_hours": 4,
  "dummy_epg_channel_format": "US",
  "channel_profile_name": "REPLACE_ME_profile_name",
  "channel_groups": "REPLACE_ME_comma_separated_group_names",
  "hide_rules_priority": "[EmptyPlaceholder],[InactiveRegex],[NoEventPattern],[NumberOnly],[FutureDate:5],[PastDate:1]",
  "scheduled_times": "0400,1000,1100,1200",
  "auto_rescan_on_m3u_refresh": true,
  "past_date_grace_hours": 4,
  "duplicate_strategy": "lowest_number",
  "keep_duplicates": false,
  "rate_limiting": "none",
  "name_source": "Channel_Name",
  "date_format": "Auto"
}
```

- [ ] **Step 4: Run to verify pass (BASH)**

Run: `python -m pytest tests/contract/test_config_template.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit (BASH)**

```bash
git add config/ecm_settings.template.json tests/contract/test_config_template.py
git commit -m "feat: committed ECM settings template with credential denylist

Guarded by contract tests: denylisted keys, credential-shaped key names,
secret-shaped values, and any key that is not a real plugin.json field id all
fail the build. Environment-specific keys MUST ship as REPLACE_ME placeholders
- bootstrap refuses to write a placeholder, so this is what stops a restore
from clobbering a working config."
```

---

## Task 4: Bootstrap script, with its merge logic extracted and tested

**[REV1 WAS WRONG:** rev 1's bootstrap did a blind `settings.update(template)`, so running it on a configured box would overwrite live `channel_groups`/`channel_profile_name` with `REPLACE_ME` placeholders. At the next scheduled pass the group resolves to zero channels, `keep_ids` empties, and source 18's **28 bound channels** get detached and their `EPGData` rows reaped. Rev 1 also shipped ~190 lines of mutation logic with zero automated tests, and an unanchored/unscoped rebind regex.**]**

**Files:** Create `scripts/bootstrap_ecm.py`, `tests/unit/test_bootstrap_merge.py`

**Interfaces:**
- Consumes: `config/ecm_settings.template.json`
- Produces: `merge_settings(existing, template, env) -> (dict, bool)` — pure, importable, tested.

- [ ] **Step 1: Write the failing tests for the pure merge**

```python
# tests/unit/test_bootstrap_merge.py
"""The bootstrap script is the only thing in this slice that writes to Postgres
and /data. Its decision logic is pure and therefore testable; only the I/O is not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bootstrap_merge import merge_settings, PLACEHOLDER_PREFIX  # noqa: E402


def test_merge_is_idempotent():
    tmpl = {"manage_dummy_epg": True}
    once, changed1 = merge_settings({"runtime_key": 1}, tmpl, {})
    twice, changed2 = merge_settings(once, tmpl, {})
    assert changed1 is True and changed2 is False
    assert once == twice


def test_merge_preserves_runtime_only_keys():
    """The on-disk settings file carries keys the template deliberately omits."""
    merged, _ = merge_settings({"event": "m3u_refresh", "payload": {"a": 1}},
                               {"manage_dummy_epg": True}, {})
    assert merged["event"] == "m3u_refresh"
    assert merged["payload"] == {"a": 1}


def test_placeholders_never_overwrite_an_existing_value():
    """THE CRITICAL ONE. A REPLACE_ME value must not clobber working config -
    doing so takes the plugin out of scope for every group it manages, and the
    next scheduled pass then detaches every channel it owns."""
    existing = {"channel_groups": "US: PPV", "channel_profile_name": "a"}
    tmpl = {"channel_groups": f"{PLACEHOLDER_PREFIX}_groups",
            "channel_profile_name": f"{PLACEHOLDER_PREFIX}_profile"}
    merged, changed = merge_settings(existing, tmpl, {})
    assert merged["channel_groups"] == "US: PPV"
    assert merged["channel_profile_name"] == "a"
    assert changed is False


def test_placeholder_fills_an_absent_key_but_is_reported():
    merged, changed = merge_settings({}, {"channel_groups": f"{PLACEHOLDER_PREFIX}_g"}, {})
    assert merged["channel_groups"].startswith(PLACEHOLDER_PREFIX)
    assert changed is True


def test_credentials_come_only_from_env():
    merged, _ = merge_settings({}, {"manage_dummy_epg": True},
                               {"ECM_DISPATCHARR_PASSWORD": "s3cret"})
    assert merged["dispatcharr_password"] == "s3cret"
    plain, _ = merge_settings({}, {"manage_dummy_epg": True}, {})
    assert "dispatcharr_password" not in plain


def test_existing_credentials_are_never_dropped():
    merged, _ = merge_settings({"dispatcharr_password": "keep-me"},
                               {"manage_dummy_epg": True}, {})
    assert merged["dispatcharr_password"] == "keep-me"
```

- [ ] **Step 2: Run to verify it fails (BASH)**

Run: `python -m pytest tests/unit/test_bootstrap_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap_merge'`.

- [ ] **Step 3: Write the pure merge module**

```python
# scripts/bootstrap_merge.py
"""Pure decision logic for bootstrap_ecm.py. No I/O, no ORM, no Django.

Split out so the one thing in this slice that writes to Postgres and /data has
its behavior pinned by unit tests rather than by reading the code.
"""

PLACEHOLDER_PREFIX = "REPLACE_ME"

CREDENTIAL_ENV = (
    ("ECM_DISPATCHARR_URL", "dispatcharr_url"),
    ("ECM_DISPATCHARR_USERNAME", "dispatcharr_username"),
    ("ECM_DISPATCHARR_PASSWORD", "dispatcharr_password"),
)


def is_placeholder(value):
    return isinstance(value, str) and value.startswith(PLACEHOLDER_PREFIX)


def merge_settings(existing, template, env):
    """Return (merged, changed).

    Rules:
      - a template value that is a REPLACE_ME placeholder NEVER overwrites an
        existing value; it only fills an absent key
      - runtime-only keys already present are preserved
      - credentials come from `env` only, and an existing credential is kept
        when the env does not supply one
    """
    existing = dict(existing or {})
    merged = dict(existing)

    for key, value in (template or {}).items():
        if is_placeholder(value) and key in merged:
            continue
        merged[key] = value

    for env_name, key in CREDENTIAL_ENV:
        value = (env or {}).get(env_name)
        if value:
            merged[key] = value

    return merged, merged != existing
```

- [ ] **Step 4: Run to verify pass (BASH)**

Run: `python -m pytest tests/unit/test_bootstrap_merge.py -v`
Expected: 6 passed.

- [ ] **Step 5: Write the bootstrap script**

```python
# scripts/bootstrap_ecm.py
"""Restore Event-Channel-Managarr configuration on a rebuilt Dispatcharr box.

Run INSIDE the container, AS THE DISPATCH USER.

STEP 1 IS MANDATORY: this script imports bootstrap_merge, which is a SEPARATE
file. Piping only this script via stdin leaves that import unresolvable and the
run dies before doing anything.

    docker cp scripts/bootstrap_merge.py dispatcharr:/tmp/bootstrap_merge.py
    $env:ECM_SETTINGS_JSON = (Get-Content config\\ecm_settings.template.json -Raw)
    docker exec -i -u dispatch -e ECM_SETTINGS_JSON -e ECM_BOOTSTRAP_APPLY dispatcharr \\
        sh -c "cd /app && python3 manage.py shell" < scripts/bootstrap_ecm.py

DEFAULTS TO DRY RUN. Set $env:ECM_BOOTSTRAP_APPLY = "1" to actually write.

WHY THIS EXISTS: plugin CODE returns via docker cp, but everything that makes it
do anything lives in Postgres and /data. manage_dummy_epg defaults to False and
the scheduler only arms from the on-disk settings file, so a fresh box comes up
inert with the code present and nothing configured.

WHAT IT WILL NOT DO: overwrite an existing setting with a REPLACE_ME placeholder;
run as root; rebind a channel outside group 1915; rebind a channel already bound
to a source other than the DAZN GMT source.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/tmp")
try:
    from bootstrap_merge import merge_settings  # docker cp'd to /tmp -- see docstring
except ImportError:
    raise SystemExit(
        "bootstrap_merge not found on /tmp.\n"
        "Run first:  docker cp scripts/bootstrap_merge.py dispatcharr:/tmp/bootstrap_merge.py"
    )

from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGData, EPGSource  # noqa: E402
from apps.plugins.models import PluginConfig  # noqa: E402

PLUGIN_KEY = "event-channel-managarr"
SETTINGS_FILE = Path("/data/event_channel_managarr_settings.json")
APPLY = os.environ.get("ECM_BOOTSTRAP_APPLY") == "1"

# MUST match ecm_profiles.DAZN_GMT.source_name. Cross-checked by a unit test.
DAZN_SOURCE_NAME = "DAZN PPV Dummy (GMT)"
DAZN_GROUP_ID = 1915
DAZN_SLOT_REGEX = r"US: DAZN PPV \d+$"   # anchored: no partial-name capture

DAZN_PROPS = {
    "timezone": "UTC",
    "output_timezone": "America/Chicago",
    "managed_by": "manual-dazn-gmt",
    "title_pattern": r"^(?:Next|End)\s*\|\s*(?<title>.+?)\s*\|",
    "date_pattern": r"\b(?<year>\d{4})-(?<month>\d{1,2})-(?<day>\d{1,2})\b",
    "time_pattern": r"\|\s*(?<hour>\d{1,2}):(?<minute>\d{2})\s*\(GMT\)",
    "title_template": "{title}",
    "upcoming_title_template": "Upcoming at {month}/{day} {starttime} CDT: {title}",
    "ended_title_template": "Ended at {month}/{day} {endtime} CDT: {title}",
    "include_date": False,
    "program_duration": 240,
    "fallback_title_template": "",
    "fallback_description_template": "Live event — guide information is currently unavailable.",
}


def refuse_if_root():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit(
            "REFUSING to run as root. Files this script creates under /data would be\n"
            "root-owned and would silently block the uWSGI workers.\n"
            "Re-run with:  docker exec -i -u dispatch ..."
        )


def load_template():
    raw = os.environ.get("ECM_SETTINGS_JSON")
    if not raw:
        print("[bootstrap] ECM_SETTINGS_JSON not set; skipping settings restore.")
        return None
    return json.loads(raw)


def restore_plugin_settings(template):
    if template is None:
        return "skipped"
    cfg = PluginConfig.objects.filter(key=PLUGIN_KEY).first()
    if cfg is None:
        print(f"[bootstrap] ERROR: no PluginConfig row for {PLUGIN_KEY!r}. Run discovery first.")
        return "no-plugin-row"

    merged, changed = merge_settings(cfg.settings or {}, template, os.environ)
    if not changed:
        return "unchanged"
    if not APPLY:
        added = set(merged) - set(cfg.settings or {})
        print(f"[bootstrap]   would add/update keys: {sorted(added)}")
        return "DRY-RUN would update"
    cfg.settings = merged
    cfg.save(update_fields=["settings"])
    return "updated"


def mirror_settings_file(template):
    if template is None:
        return "skipped"
    existing = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[bootstrap] WARNING: could not parse {SETTINGS_FILE}: {exc}")

    merged, changed = merge_settings(existing, template, os.environ)
    if not changed:
        return "unchanged"
    if not APPLY:
        return "DRY-RUN would update"
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return "updated"


def restore_dazn_source():
    source = EPGSource.objects.filter(name=DAZN_SOURCE_NAME).first()
    if source is None:
        if not APPLY:
            return None, "DRY-RUN would create"
        source = EPGSource.objects.create(
            name=DAZN_SOURCE_NAME, source_type="dummy", is_active=True,
            refresh_interval=0, priority=0, custom_properties=DAZN_PROPS)
        return source, "created"
    if source.custom_properties != DAZN_PROPS:
        if not APPLY:
            return source, "DRY-RUN would update props"
        source.custom_properties = DAZN_PROPS
        source.save(update_fields=["custom_properties"])
        return source, "updated props"
    return source, "unchanged"


def rebind_dazn_channels(source):
    if source is None:
        return 0, 0, 0
    targets = list(
        Channel.objects.filter(channel_group_id=DAZN_GROUP_ID, name__regex=DAZN_SLOT_REGEX)
        .select_related("epg_data").order_by("id"))

    would_bind, skipped_foreign = 0, 0
    for channel in targets:
        current_source_id = getattr(channel.epg_data, "epg_source_id", None)
        if current_source_id is not None and current_source_id != source.id:
            skipped_foreign += 1
            continue
        if not APPLY:
            if channel.epg_data_id is None:
                would_bind += 1
            continue
        epg_data, _ = EPGData.objects.get_or_create(
            epg_source=source, tvg_id=f"dazn_gmt_{channel.id}",
            defaults={"name": channel.name})
        if epg_data.name != channel.name:
            epg_data.name = channel.name
            epg_data.save(update_fields=["name"])
        if channel.epg_data_id != epg_data.id:
            channel.epg_data = epg_data
            channel.save(update_fields=["epg_data"])
            would_bind += 1
    return len(targets), would_bind, skipped_foreign


def main():
    refuse_if_root()
    mode = "APPLY" if APPLY else "DRY RUN (set ECM_BOOTSTRAP_APPLY=1 to write)"
    print(f"[bootstrap] Event-Channel-Managarr restore -- {mode}")

    template = load_template()
    print(f"[bootstrap] PluginConfig.settings: {restore_plugin_settings(template)}")
    print(f"[bootstrap] {SETTINGS_FILE}: {mirror_settings_file(template)}")

    source, status = restore_dazn_source()
    sid = source.id if source else "n/a"
    print(f"[bootstrap] EPGSource {DAZN_SOURCE_NAME!r} (id={sid}): {status}")

    total, bound, skipped = rebind_dazn_channels(source)
    print(f"[bootstrap] DAZN slots in group {DAZN_GROUP_ID}: {total}, "
          f"bound: {bound}, skipped (bound elsewhere): {skipped}")
    print("[bootstrap] done. Scheduler arms on next settings load; click any "
          "plugin action to arm immediately.")


main()
```

- [ ] **Step 6: Syntax-check (BASH)**

```bash
python -m py_compile scripts/bootstrap_ecm.py scripts/bootstrap_merge.py && echo "SYNTAX OK"
```
Expected: `SYNTAX OK`. (`bootstrap_ecm.py` cannot be imported outside the container.)

- [ ] **Step 7: Commit (BASH)**

```bash
git add scripts/bootstrap_ecm.py scripts/bootstrap_merge.py tests/unit/test_bootstrap_merge.py
git commit -m "feat: bootstrap restore with tested merge logic and destructive guards

Defaults to DRY RUN. Refuses to run as root (root-owned /data files silently
block the uWSGI workers). A REPLACE_ME placeholder never overwrites an existing
value - without that guard, a restore on a configured box would blank
channel_groups, empty keep_ids at the next pass, and detach every channel the
plugin manages. The rebind regex is anchored and scoped to group 1915, and
refuses channels bound to a foreign source.

The pure merge logic lives in bootstrap_merge.py with unit tests, so the one
piece of this slice that writes to Postgres and /data is not verified by
reading it."
```

---

## Task 5: `ecm_profiles.py` — Profile, PROFILES, regex shim

**[REV1 WAS WRONG:** `DAZN_GMT.source_name` was `"ECM Managed Dummy (GMT)"` while the live source and the bootstrap script both use `"DAZN PPV Dummy (GMT)"` — two committed artifacts disagreeing about the same source, in the slice whose purpose is to be the durable record. Rev 1 also shipped `test_default_source_name_is_pinned` whose docstring describes exactly this hazard, applied to `us_et` (which was correct) and not to `dazn_gmt` (which was wrong). Additionally, the regex shim's production branch was untestable: `regex` is absent on dev machines and present in the container, so tests only ever exercised the branch that never runs in production.**]**

**Files:** Create `Event-Channel-Managarr/ecm_profiles.py`, `tests/unit/test_ecm_profiles.py`

**Interfaces:**
- Produces:
  - `Profile` — frozen dataclass: `key`, `source_name`, `selector`, `title_pattern`, `date_pattern`, `time_pattern`, `timezone`, `output_timezone`, `program_duration_minutes`, `include_date`, `title_template`, `upcoming_title_template`, `ended_title_template`, `fallback_title_template`, `fallback_description_template`, `is_default`
  - `PROFILES: tuple[Profile, ...]`
  - `to_python_named(pattern) -> str`
  - `compile_pattern(pattern, engine=None, convert=None)` → compiled regex or `None`
  - `profile_props(profile) -> dict` — the `custom_properties` payload

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_ecm_profiles.py
"""Unit tests for ecm_profiles - the pure, Django-free profile module."""

import dataclasses
import importlib.util
import re as _stdlib_re
from pathlib import Path

import pytest

import ecm_profiles   # resolves via pyproject.toml pythonpath

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"


def _fixture_names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


# --- Profile / PROFILES shape -------------------------------------------------

def test_exactly_one_default_profile():
    defaults = [p for p in ecm_profiles.PROFILES if p.is_default]
    assert len(defaults) == 1, f"expected 1 default, got {[p.key for p in defaults]}"


def test_profile_keys_are_unique():
    """A duplicate key silently merges two buckets - one profile then routes
    nowhere, and the partition test still passes because the total is preserved."""
    keys = [p.key for p in ecm_profiles.PROFILES]
    assert len(keys) == len(set(keys)), f"duplicate profile keys: {keys}"


def test_no_profile_key_collides_with_the_unclaimed_sentinel():
    assert ecm_profiles.UNCLAIMED not in {p.key for p in ecm_profiles.PROFILES}


@pytest.mark.parametrize("key,expected_name", [
    ("us_et", "ECM Managed Dummy"),
    ("dazn_gmt", "DAZN PPV Dummy (GMT)"),
])
def test_source_names_are_pinned(key, expected_name):
    """EPGSource.name is unique=True and is how sources are looked up. A changed
    name makes get_or_create mint a SECOND source while the original keeps every
    binding - the guide then renders from a row nothing manages.

    BOTH profiles are pinned. Pinning only the default is how rev 1 shipped a
    dazn_gmt name that disagreed with both the live source and bootstrap."""
    profile = next(p for p in ecm_profiles.PROFILES if p.key == key)
    assert profile.source_name == expected_name


def test_dazn_profile_targets_utc():
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    assert dazn.timezone == "UTC"
    assert dazn.output_timezone == "America/Chicago"


def test_profile_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ecm_profiles.PROFILES[0].key = "mutated"


def test_profile_props_round_trips_every_renderer_key():
    """profile_props is what would be written to EPGSource.custom_properties.
    Missing a key means the renderer silently falls back to a default."""
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    props = ecm_profiles.profile_props(dazn)
    for key in ("timezone", "output_timezone", "title_pattern", "date_pattern",
                "time_pattern", "title_template", "upcoming_title_template",
                "ended_title_template", "program_duration", "include_date",
                "fallback_title_template", "fallback_description_template"):
        assert key in props, f"profile_props missing {key}"
    assert props["timezone"] == "UTC"
    assert props["program_duration"] == 240


# --- regex dialect shim --------------------------------------------------------

def _fake_native_engine():
    """Stand-in for the `regex` package: accepts JS (?<name>) natively."""
    class _Engine:
        @staticmethod
        def compile(pattern):
            return _stdlib_re.compile(ecm_profiles.to_python_named(pattern))
    return _Engine


DIALECTS = [
    pytest.param(None, True, id="stdlib_re_converting"),
    pytest.param(_fake_native_engine(), False, id="regex_native"),
]


def test_dialect_detection_matches_environment():
    """Guards against the shim silently flipping: production (container) has
    `regex`, dev machines do not, so each only ever exercises one branch."""
    assert ecm_profiles._NEEDS_CONVERSION == (importlib.util.find_spec("regex") is None)


def test_to_python_named_converts_js_groups():
    assert ecm_profiles.to_python_named(r"(?<title>.+)") == r"(?P<title>.+)"


@pytest.mark.parametrize("pattern", [r"(?<=foo)bar", r"(?<!foo)bar"])
def test_to_python_named_preserves_lookbehind(pattern):
    """(?<= and (?<! are lookbehinds, NOT named groups."""
    assert ecm_profiles.to_python_named(pattern) == pattern


def test_to_python_named_handles_mixed():
    assert ecm_profiles.to_python_named(r"(?<=x)(?<name>\d+)(?<!y)") == r"(?<=x)(?P<name>\d+)(?<!y)"


@pytest.mark.parametrize("engine,convert", DIALECTS)
@pytest.mark.parametrize("profile", ecm_profiles.PROFILES, ids=lambda p: p.key)
@pytest.mark.parametrize("attr", ["selector", "title_pattern", "date_pattern", "time_pattern"])
def test_shipped_patterns_compile_in_both_dialects(profile, attr, engine, convert):
    """Both branches tested on every machine - not just whichever one is installed."""
    value = getattr(profile, attr)
    assert ecm_profiles.compile_pattern(value, engine=engine, convert=convert) is not None, \
        f"{profile.key}.{attr} failed to compile: {value!r}"


def test_compile_pattern_degrades_on_bad_pattern():
    """A bad pattern must never raise on the scan path."""
    assert ecm_profiles.compile_pattern(r"(?<unclosed") is None


# --- pattern EXTRACTION (not merely compilation) -------------------------------

@pytest.mark.parametrize("attr,required_groups", [
    ("title_pattern", {"title"}),
    ("date_pattern", {"year", "month", "day"}),
    ("time_pattern", {"hour", "minute"}),
])
def test_dazn_patterns_extract_from_every_routed_name(attr, required_groups):
    """Compiling is not enough - a pattern that matches NOTHING compiles fine.
    Every name route() gives dazn_gmt must yield every group the renderer needs."""
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    routed = ecm_profiles.route(_fixture_names())["dazn_gmt"]
    assert routed, "no DAZN names routed - extraction assertions would be vacuous"

    rx = ecm_profiles.compile_pattern(getattr(dazn, attr))
    failures = [n for n in routed
                if not (rx.search(n) and required_groups <=
                        {k for k, v in rx.search(n).groupdict().items() if v})]
    assert not failures, (
        f"dazn_gmt.{attr} extracted no {sorted(required_groups)} from "
        f"{len(failures)}/{len(routed)} names, e.g. {failures[:3]}")


def test_us_et_title_extracts_where_the_name_has_event_text():
    """Bare slots (PPV EVENT 48) legitimately extract nothing - the renderer's
    fallback handles them. Names WITH event text must extract."""
    us = next(p for p in ecm_profiles.PROFILES if p.key == "us_et")
    rx = ecm_profiles.compile_pattern(us.title_pattern)
    m = rx.search("PPV EVENT 07: MARS Late Models at Farmer City (7.17 7:30 PM ET)")
    assert m and m.group("title") == "MARS Late Models at Farmer City"
```

- [ ] **Step 2: Run to verify it fails (BASH)**

Run: `python -m pytest tests/unit/test_ecm_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecm_profiles'`.

- [ ] **Step 3: Write the module**

```python
# Event-Channel-Managarr/ecm_profiles.py
"""Pure, Django-free timezone-profile definitions for Event Channel Managarr.

WHY THIS EXISTS
---------------
Dispatcharr renders dummy EPG programmes from the CHANNEL NAME using regex
patterns plus a timezone stored on the EPGSource. It reads that timezone ONCE
PER SOURCE (apps/output/epg.py:305) with no per-channel hook. So a channel group
containing provider families in DIFFERENT source timezones cannot be served by a
single dummy EPGSource -- the timezone field itself must differ.

This module decides WHICH profile owns a given channel name. It performs no I/O
and imports nothing outside the stdlib, so it is unit-testable without a
container. See docs/superpowers/specs/2026-07-18-durable-multi-timezone-epg-design.md

CONSTRAINTS
-----------
- stdlib only; no apps.*, django.*, core.utils
- no module-level mutable state: Dispatcharr's loader re-imports this module on
  nearly every streaming event, so caches are wiped and are pointless anyway
  (routing all 278 names measures well under a millisecond)
- patterns are STORED in JS named-group form (?<name>) because Dispatcharr's
  JavaScript frontend validator rejects the Python (?P<name>) form (issue #21)
"""

import re
from dataclasses import dataclass

try:  # Dispatcharr ships `regex`; dev machines generally do not.
    import regex as _re  # accepts (?<name>) natively
    _NEEDS_CONVERSION = False
except ImportError:
    _re = re
    _NEEDS_CONVERSION = True


# Matches a JS named-group opener (?<name> but NOT a lookbehind (?<= or (?<!
# Same guard Dispatcharr's own renderer uses (apps/output/epg.py:357).
_JS_NAMED_GROUP = re.compile(r"\(\?<(?![=!])([^>]+)>")

UNCLAIMED = "__unclaimed__"


@dataclass(frozen=True)
class Profile:
    """One provider name-family and the EPGSource settings it needs."""

    key: str
    source_name: str
    selector: str
    title_pattern: str
    date_pattern: str
    time_pattern: str
    timezone: str
    output_timezone: str
    program_duration_minutes: int
    include_date: bool
    title_template: str
    upcoming_title_template: str
    ended_title_template: str
    fallback_title_template: str
    fallback_description_template: str
    is_default: bool


def to_python_named(pattern):
    """Convert JS named groups (?<n>) to Python (?P<n>), preserving lookbehinds."""
    return _JS_NAMED_GROUP.sub(r"(?P<\1>", pattern)


def compile_pattern(pattern, engine=None, convert=None):
    """Compile a stored pattern. Returns None on failure -- never raises.

    engine/convert are injectable so BOTH dialect branches are testable on a
    machine that has only one of them installed. Production (container) has
    `regex` and takes convert=False; dev machines take convert=True.
    """
    if not pattern:
        return None
    engine = _re if engine is None else engine
    convert = _NEEDS_CONVERSION if convert is None else convert
    candidate = to_python_named(pattern) if convert else pattern
    try:
        return engine.compile(candidate)
    except Exception:
        try:
            return engine.compile(to_python_named(pattern))
        except Exception:
            return None


def profile_props(profile):
    """The EPGSource.custom_properties payload for this profile. Pure."""
    return {
        "timezone": profile.timezone,
        "output_timezone": profile.output_timezone,
        "title_pattern": profile.title_pattern,
        "date_pattern": profile.date_pattern,
        "time_pattern": profile.time_pattern,
        "title_template": profile.title_template,
        "upcoming_title_template": profile.upcoming_title_template,
        "ended_title_template": profile.ended_title_template,
        "program_duration": profile.program_duration_minutes,
        "include_date": profile.include_date,
        "fallback_title_template": profile.fallback_title_template,
        "fallback_description_template": profile.fallback_description_template,
    }


def route(names, profiles=None):
    """Assign each channel name to exactly one profile bucket.

    Non-default profiles are evaluated in declaration order; the default profile
    is evaluated LAST and claims only what nothing else claimed. Names no profile
    claims land in UNCLAIMED.

    THE DEFAULT-LAST RULE IS AN INVARIANT, not an artifact of list order: a broad
    default declared first must still lose to a specific profile. That is how two
    earlier revisions of this design shipped a silent no-op.

    NOTE: this invariant alone is NOT sufficient. Any non-default profile whose
    selector is broad enough to claim another family's names re-creates the same
    failure, which is why every selector is asserted against the real fixture.

    Returns dict[profile_key -> list[name]] plus UNCLAIMED, partitioning `names`
    exactly: every name appears in exactly one bucket, in input order.
    """
    profiles = PROFILES if profiles is None else profiles

    keys = [p.key for p in profiles]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate profile keys would silently merge buckets: {keys}")
    if UNCLAIMED in keys:
        raise ValueError(f"a profile key collides with the {UNCLAIMED!r} sentinel")
    if sum(1 for p in profiles if p.is_default) > 1:
        raise ValueError("more than one default profile; ordering would be ambiguous")

    ordered = [p for p in profiles if not p.is_default] + [p for p in profiles if p.is_default]
    compiled = [(p, compile_pattern(p.selector)) for p in ordered]

    buckets = {p.key: [] for p in profiles}
    buckets[UNCLAIMED] = []

    for name in names:
        for profile, selector in compiled:
            if selector is not None and selector.search(name):
                buckets[profile.key].append(name)
                break
        else:
            buckets[UNCLAIMED].append(name)

    return buckets


# --- The profile chain --------------------------------------------------------
#
# ORDER MATTERS: route() evaluates non-defaults in this order, default last.
#
# There is deliberately NO "se" profile. The SE title pattern is pipe-based
# (\|\s*(?<title>[^|]+?)\s*\|), every DAZN name is pipe-delimited, and an SE
# profile ahead of dazn_gmt claims 99 names while dazn_gmt claims ZERO --
# silently re-creating the very no-op this design exists to fix. SE remains the
# existing global dummy_epg_channel_format behavior, outside the profile chain.

_FALLBACK_DESCRIPTION = "Live event — guide information is currently unavailable."

DAZN_GMT = Profile(
    key="dazn_gmt",
    # PINNED to the live source. Cross-checked against bootstrap_ecm.DAZN_SOURCE_NAME.
    source_name="DAZN PPV Dummy (GMT)",
    selector=r"^(?:Next|End)\s*\|.*\(GMT\)",
    title_pattern=r"^(?:Next|End)\s*\|\s*(?<title>.+?)\s*\|",
    date_pattern=r"\b(?<year>\d{4})-(?<month>\d{1,2})-(?<day>\d{1,2})\b",
    time_pattern=r"\|\s*(?<hour>\d{1,2}):(?<minute>\d{2})\s*\(GMT\)",
    timezone="UTC",
    output_timezone="America/Chicago",
    program_duration_minutes=240,
    include_date=False,
    title_template="{title}",
    # NOTE: the literal " CDT" mirrors the live source exactly so this profile is
    # a faithful capture. It is WRONG for ~5 months a year (America/Chicago is CST
    # in winter). plugin.py computes the abbreviation dynamically via strftime("%Z")
    # precisely to avoid this; the hand-made source did not. Recorded as a known
    # defect of the captured config -- fix it in S2, not by diverging here.
    upcoming_title_template="Upcoming at {month}/{day} {starttime} CDT: {title}",
    ended_title_template="Ended at {month}/{day} {endtime} CDT: {title}",
    fallback_title_template="",
    fallback_description_template=_FALLBACK_DESCRIPTION,
    is_default=False,
)

US_ET = Profile(
    key="us_et",
    # PINNED: plugin.py:2404/2632/2661 all use this literal.
    source_name="ECM Managed Dummy",
    # ANCHORED. The unanchored form (?:PPV|LIVE|EVENT)\s*\d+ also matches the
    # TRAILING slot label "US: DAZN PPV 46" on every DAZN name, which routed all
    # 48 DAZN channels to the ET source. Anchoring drops exactly 51 names, all
    # idle DAZN slots that never should have matched. Both branches are
    # independently ^-anchored (verified: top-level alternation).
    selector=r"^\s*(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+|^\s*EVENT\s*\d+",
    title_pattern=(
        r"(?:(?:PPV|LIVE)\s*(?:EVENT\s*)?|EVENT\s*)\d+\s*[:|\-\s]\s*"
        r"(?:(?<leading_time>\d{1,2}(?::\d{2})?\s*[AaPp][Mm])\s+)?"
        r"(?<title>.+?)"
        r"(?=\s*\(|\s+\d{1,2}(?::\d{2})?\s*[AaPp][Mm]|"
        r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+|$)"
    ),
    date_pattern=r"\b(?<month>\d{1,2})[./](?<day>\d{1,2})(?:[./](?<year>\d{2,4}))?\b",
    time_pattern=r"(?<hour>\d{1,2})(?::(?<minute>\d{2}))?\s*(?<ampm>[AaPp][Mm])",
    timezone="America/New_York",
    output_timezone="America/Chicago",
    program_duration_minutes=240,
    include_date=False,
    title_template="{title}",
    upcoming_title_template="Upcoming at {starttime}: {title}",
    ended_title_template="Ended at {endtime}: {title}",
    fallback_title_template="",
    fallback_description_template=_FALLBACK_DESCRIPTION,
    is_default=True,
)

PROFILES = (DAZN_GMT, US_ET)
```

- [ ] **Step 4: Run (BASH)**

Run: `python -m pytest tests/unit/test_ecm_profiles.py -v`
Expected: all pass (~30 including parametrized cases).

- [ ] **Step 5: Add the cross-artifact test**

Append to `tests/unit/test_bootstrap_merge.py`:

```python
def _bootstrap_source():
    """Extract DAZN_SOURCE_NAME and DAZN_PROPS from bootstrap_ecm.py via ast.

    Parsed, never imported: bootstrap_ecm.py imports Django models at module
    scope and cannot be imported outside the container.
    """
    import ast
    src = (Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_ecm.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("DAZN_SOURCE_NAME", "DAZN_PROPS"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    assert "DAZN_SOURCE_NAME" in found, "DAZN_SOURCE_NAME not found in bootstrap_ecm.py"
    assert "DAZN_PROPS" in found, "DAZN_PROPS not found in bootstrap_ecm.py"
    return found


def test_bootstrap_and_profile_agree_on_the_dazn_source_name():
    """Two committed artifacts describing ONE source. If they disagree, a restore
    and the profile model create two different EPGSource rows for the same
    profile - and rev 1 shipped exactly that divergence."""
    import ecm_profiles
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    name = _bootstrap_source()["DAZN_SOURCE_NAME"]
    assert name == dazn.source_name, (
        f"bootstrap says {name!r}, profile says {dazn.source_name!r}")


def test_bootstrap_and_profile_agree_on_the_dazn_props():
    """The name is not the only thing that can drift. If the restore script writes
    different custom_properties than the profile models, the restored source
    renders differently from what the tests verified -- silently.

    bootstrap additionally carries `managed_by` (source identity, which the
    profile model does not own); every OTHER key must match exactly.
    """
    import ecm_profiles
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    boot = dict(_bootstrap_source()["DAZN_PROPS"])
    boot.pop("managed_by", None)
    assert boot == ecm_profiles.profile_props(dazn), (
        "bootstrap DAZN_PROPS and profile_props(dazn_gmt) have diverged:\n"
        f"  only in bootstrap: {sorted(set(boot) - set(ecm_profiles.profile_props(dazn)))}\n"
        f"  only in profile:   {sorted(set(ecm_profiles.profile_props(dazn)) - set(boot))}\n"
        f"  differing values:  "
        f"{sorted(k for k in set(boot) & set(ecm_profiles.profile_props(dazn)) if boot[k] != ecm_profiles.profile_props(dazn)[k])}")
```

- [ ] **Step 6: Run and commit (BASH)**

```bash
python -m pytest tests/ -v
git add Event-Channel-Managarr/ecm_profiles.py tests/unit/test_ecm_profiles.py tests/unit/test_bootstrap_merge.py
git commit -m "feat: pure ecm_profiles module with Profile, PROFILES, route and regex shim

Stdlib-only, no module-level mutable state. route() validates its inputs
(duplicate keys, multiple defaults, sentinel collision) because a duplicate key
silently merges buckets while the partition check still passes.

BOTH source names are pinned by test, and a cross-artifact test asserts
bootstrap and the profile agree - rev 1 pinned only the default (which was
correct) and left dazn_gmt (which was wrong) unguarded.

The regex dialect is injectable so both branches are tested everywhere:
production has \`regex\` and dev machines do not, so each would otherwise only
ever exercise the branch it does not ship."
```

---

## Task 6: Routing assertions against the real corpus

**Files:** Modify `tests/unit/test_ecm_profiles.py`

**Interfaces:** Consumes `route`, `PROFILES`, `UNCLAIMED` (Task 5); the fixture (Task 2).

- [ ] **Step 1: Write the routing tests**

Append to `tests/unit/test_ecm_profiles.py`:

```python
# --- routing over the real corpus ----------------------------------------------

def test_route_returns_a_bucket_per_profile_plus_unclaimed():
    result = ecm_profiles.route(["PPV EVENT 01: Something"])
    assert set(result) == {"dazn_gmt", "us_et", ecm_profiles.UNCLAIMED}


def test_dazn_bucket_is_exactly_the_gmt_bearing_names():
    """Ground truth is the NAME ITSELF, not a count. This subsumes any count
    assertion and survives lineup churn."""
    names = _fixture_names()
    result = ecm_profiles.route(names)
    assert set(result["dazn_gmt"]) == {n for n in names if "(GMT)" in n}


def test_us_et_bucket_is_exactly_the_legacy_family():
    """SET IDENTITY, not just the total. A count-only assertion would pass on a
    different set of 104 names - precisely the blind spot this plan exists to close."""
    names = _fixture_names()
    result = ecm_profiles.route(names)
    legacy = {n for n in names if n.startswith(("PPV EVENT", "LIVE EVENT"))}
    assert set(result["us_et"]) == legacy


def test_route_partitions_the_corpus_exactly_once():
    names = _fixture_names()
    result = ecm_profiles.route(names)
    assert sum(len(v) for v in result.values()) == len(names)


def test_no_dazn_name_leaks_into_us_et():
    """The failure mode of both rejected design revisions."""
    result = ecm_profiles.route(_fixture_names())
    assert not [n for n in result["us_et"] if "(GMT)" in n]


def test_idle_dazn_slots_are_unclaimed_not_us_et():
    """'NO EVENT STREAMING NOW - | ... | US: DAZN PPV 50' contains 'PPV 50'. An
    unanchored us_et selector claims it; the anchored one must not."""
    result = ecm_profiles.route(_fixture_names())
    assert not [n for n in result["us_et"] if n.startswith("NO EVENT STREAMING NOW")]


def test_no_profile_selector_claims_another_familys_names():
    """Generalizes the rejected 'se' profile: ANY selector broad enough to claim
    the GMT family ahead of dazn_gmt re-creates the no-op. Name-agnostic, so it
    still bites if someone adds the same greedy pattern under a different key."""
    names = _fixture_names()
    gmt = [n for n in names if "(GMT)" in n]
    assert gmt, "fixture has no GMT names - this test would be vacuous"
    for profile in ecm_profiles.PROFILES:
        if profile.key == "dazn_gmt":
            continue
        rx = ecm_profiles.compile_pattern(profile.selector)
        greedy = [n for n in gmt if rx and rx.search(n)]
        assert not greedy, (
            f"{profile.key}'s selector claims {len(greedy)} GMT names, e.g. {greedy[:2]}. "
            f"Ordered ahead of dazn_gmt, dazn_gmt would route ZERO channels.")


def test_fixture_era_counts_for_the_record():
    """Counts against the COMMITTED FIXTURE (not live data), so lineup churn
    cannot make this flap. If it fails, a selector changed - investigate, do not
    edit the numbers."""
    result = ecm_profiles.route(_fixture_names())
    assert len(result["dazn_gmt"]) == 48
    assert len(result["us_et"]) == 104
    assert len(result[ecm_profiles.UNCLAIMED]) == 126


# --- route() contract ----------------------------------------------------------

def _mk(key, selector, is_default=False):
    return ecm_profiles.Profile(
        key=key, source_name=key, selector=selector, title_pattern=r"(?<title>.*)",
        date_pattern="", time_pattern="", timezone="UTC", output_timezone="UTC",
        program_duration_minutes=60, include_date=False, title_template="{title}",
        upcoming_title_template="", ended_title_template="",
        fallback_title_template="", fallback_description_template="",
        is_default=is_default)


def test_default_profile_is_evaluated_last_regardless_of_declaration_order():
    greedy = _mk("greedy", r".*", is_default=True)
    specific = _mk("specific", r"^SPECIAL")
    result = ecm_profiles.route(["SPECIAL thing"], profiles=(greedy, specific))
    assert result["specific"] == ["SPECIAL thing"]
    assert result["greedy"] == []


def test_non_default_profiles_are_evaluated_in_declaration_order():
    first, second = _mk("first", r"FOO"), _mk("second", r"FOO")
    assert ecm_profiles.route(["FOO"], profiles=(first, second))["first"] == ["FOO"]
    assert ecm_profiles.route(["FOO"], profiles=(second, first))["second"] == ["FOO"]


def test_route_rejects_duplicate_keys():
    with pytest.raises(ValueError, match="duplicate profile keys"):
        ecm_profiles.route(["x"], profiles=(_mk("dup", r"a"), _mk("dup", r"b")))


def test_route_rejects_multiple_defaults():
    with pytest.raises(ValueError, match="more than one default"):
        ecm_profiles.route(["x"], profiles=(_mk("a", r"a", True), _mk("b", r"b", True)))


def test_route_rejects_a_key_colliding_with_the_sentinel():
    with pytest.raises(ValueError, match="sentinel"):
        ecm_profiles.route(["x"], profiles=(_mk(ecm_profiles.UNCLAIMED, r"a"),))


def test_route_with_uncompilable_selector_claims_nothing():
    broken = _mk("broken", r"(?<unclosed")
    default = next(p for p in ecm_profiles.PROFILES if p.is_default)
    result = ecm_profiles.route(["PPV EVENT 01: X"], profiles=(broken, default))
    assert result["broken"] == []
    assert result["us_et"] == ["PPV EVENT 01: X"]


def test_route_without_a_default_leaves_unmatched_names_unclaimed():
    only = next(p for p in ecm_profiles.PROFILES if not p.is_default)
    result = ecm_profiles.route(["nothing matches"], profiles=(only,))
    assert result[ecm_profiles.UNCLAIMED] == ["nothing matches"]


def test_route_on_empty_input_returns_empty_buckets():
    result = ecm_profiles.route([])
    assert all(v == [] for v in result.values())


def test_bucket_order_follows_input_order():
    """Observable by callers - the in-container gate slices [:5]."""
    names = _fixture_names()
    result = ecm_profiles.route(names)
    for bucket in result.values():
        members = set(bucket)
        assert bucket == [n for n in names if n in members]
```

- [ ] **Step 2: Run (BASH)**

Run: `python -m pytest tests/unit/test_ecm_profiles.py -v`
Expected: all pass. If `test_fixture_era_counts_for_the_record` fails, a selector changed — investigate; do NOT edit the numbers.

- [ ] **Step 3: Commit (BASH)**

```bash
git add tests/unit/test_ecm_profiles.py
git commit -m "test: routing assertions against the real 278-name corpus

Set-identity assertions, not count-only: us_et must equal exactly the
PPV EVENT + LIVE EVENT family, because a count-only check passes on a different
set of 104 names. Counts are kept as a separate record-keeping test and run
against the COMMITTED FIXTURE so lineup churn cannot make them flap.

route()'s contract (default-last, declaration order, input validation,
degradation on a bad selector) is pinned separately from the corpus."
```

---

## Task 7: AST purity guards, with self-checks that prove they bite

**[REV1 WAS WRONG:** rev 1's `test_no_module_level_mutable_state` missed `dict()`/`list()` factory calls, `AnnAssign` (`X: dict = {}`), comprehensions, and `@lru_cache` entirely — which the Global Constraints explicitly forbid. It passed against the real module for the wrong reason: it cannot see `ast.Call` nodes at all. Rev 1 also proved only 1 of its 4 guards actually fires, by temporarily editing the real module — a footgun if the engineer is interrupted.**]**

**Files:** Create `tests/contract/test_module_purity.py`

- [ ] **Step 1: Write the guards and their self-checks**

```python
# tests/contract/test_module_purity.py
"""ecm_profiles.py must stay importable without Django.

There is no conftest stubbing Django in this repo - pure modules stay pure by
discipline alone. These guards make that discipline enforceable: they parse the
file with ast (never import it).

Every guard has a self-check proving it FAILS on a violating module. A guard
that never fails is not a guard.
"""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "Event-Channel-Managarr" / "ecm_profiles.py"

FORBIDDEN_ROOTS = {"apps", "django", "core"}
GUARDED_OPTIONAL = {"regex"}
MUTABLE_FACTORIES = {"list", "dict", "set", "bytearray", "defaultdict", "OrderedDict", "Counter"}
FORBIDDEN_DECORATORS = {"lru_cache", "cache", "cached_property"}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _root_name(node, alias):
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0]
    return alias.name.split(".")[0]


def _module_level_imports(tree):
    """Yield (root_module, is_guarded). Covers try body, handlers, else and finally."""
    def emit(stmt, guarded):
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                yield _root_name(stmt, alias), guarded

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield from emit(node, False)
        elif isinstance(node, ast.Try):
            for section in (node.body, node.orelse, node.finalbody):
                for stmt in section:
                    yield from emit(stmt, True)
            for handler in node.handlers:
                for stmt in handler.body:
                    yield from emit(stmt, True)


def _assign_names(node):
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    return [t.id for t in node.targets if isinstance(t, ast.Name)]


# --- the guards ----------------------------------------------------------------

def check_no_django_or_app_imports(path=None):
    tree = _tree(path or MODULE)
    bad = sorted({
        _root_name(node, alias)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if _root_name(node, alias) in FORBIDDEN_ROOTS
    })
    assert not bad, f"forbidden imports: {bad}"


def check_module_imports_are_stdlib_or_guarded(path=None):
    tree = _tree(path or MODULE)
    stdlib = set(sys.stdlib_module_names)
    offenders = [
        f"{name} (guarded={guarded})"
        for name, guarded in _module_level_imports(tree)
        if name and name not in stdlib and not (guarded and name in GUARDED_OPTIONAL)
    ]
    assert not offenders, (
        f"non-stdlib module-level imports: {offenders}. "
        f"Only stdlib, or {sorted(GUARDED_OPTIONAL)} inside try/except ImportError.")


def check_no_module_level_mutable_state(path=None):
    """Constants (tuples, frozen dataclass instances, compiled regex) are fine;
    anything mutable is not - the loader wipes module globals unpredictably."""
    tree = _tree(path or MODULE)
    offenders = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        names = _assign_names(node)
        value = node.value
        if isinstance(value, (ast.List, ast.Dict, ast.Set,
                              ast.ListComp, ast.DictComp, ast.SetComp)):
            offenders += [f"{n} (mutable literal/comprehension)" for n in names]
        elif isinstance(value, ast.Call):
            fn = value.func
            fn_name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if fn_name in MUTABLE_FACTORIES:
                offenders += [f"{n} ({fn_name}() factory)" for n in names]
    assert not offenders, f"module-level mutable state: {offenders}. Use a tuple/frozenset."


def check_no_caching_decorators(path=None):
    """lru_cache is module-level mutable state wearing a hat."""
    tree = _tree(path or MODULE)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name in FORBIDDEN_DECORATORS:
                bad.append(f"{node.name} -> @{name}")
    assert not bad, f"caching decorators are not reload-safe: {bad}"


ALL_GUARDS = (
    check_no_django_or_app_imports,
    check_module_imports_are_stdlib_or_guarded,
    check_no_module_level_mutable_state,
    check_no_caching_decorators,
)


# --- the real module must pass every guard -------------------------------------

def test_module_exists():
    assert MODULE.exists(), f"missing {MODULE}"


@pytest.mark.parametrize("guard", ALL_GUARDS, ids=lambda g: g.__name__)
def test_real_module_passes_guard(guard):
    guard()


# --- every guard must FAIL on a violating module -------------------------------

BAD_SOURCES = {
    "django_import": "import django\n",
    "app_import": "from apps.epg.models import EPGSource\n",
    "unguarded_third_party": "import requests\n",
    "mutable_dict_literal": "CACHE = {}\n",
    "mutable_factory": "CACHE = dict()\n",
    "annotated_mutable": "CACHE: dict = {}\n",
    "comprehension": "CACHE = [x for x in range(3)]\n",
    "lru_cache": "from functools import lru_cache\n@lru_cache\ndef f(x):\n    return x\n",
    "import_in_except": "try:\n    import regex\nexcept ImportError:\n    import requests\n",
}


@pytest.mark.parametrize("label,source", sorted(BAD_SOURCES.items()))
def test_guards_reject_a_violating_module(label, source, tmp_path):
    """Uses a TEMP file - never edits the real module, which rev 1 did and which
    leaves the repo dirty if the engineer is interrupted."""
    fake = tmp_path / "ecm_profiles.py"
    fake.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        for guard in ALL_GUARDS:
            guard(path=fake)
```

- [ ] **Step 2: Run (BASH)**

Run: `python -m pytest tests/contract/test_module_purity.py -v`
Expected: all pass — 5 guard-passes on the real module plus 9 self-checks proving each violation is caught.

- [ ] **Step 3: Commit (BASH)**

```bash
git add tests/contract/test_module_purity.py
git commit -m "test: AST purity guards for ecm_profiles.py, with self-checks

Parses the file without importing it and rejects Django/app imports, non-stdlib
module-level imports (including inside except handlers), module-level mutable
state (literals, factory calls, annotated assignments and comprehensions) and
caching decorators.

Every guard has a self-check proving it fails on a violating temp module. Rev 1's
version missed dict()/list() factories, AnnAssign, comprehensions and lru_cache
entirely, and passed against the real module for the wrong reason."
```

---

## Task 8: Read-only in-container gate

**[REV1 WAS WRONG on four counts:** (a) `finally: temp.delete()` was bound to the VARIABLE — `create()` inserts and commits before assigning, so a raise in the post_save chain leaves `temp` as `None`, skips cleanup, and the orphan then blocks every retry via the unique name; (b) the docstring claimed "No EPGData row is created" — `create_dummy_epg_data` fires on post_save for any dummy source and creates one (cascade-deleted, so safe, but the claim was false), and it emits a websocket event with no delete counterpart; (c) proof 2's "missing names are OK if they lack `(GMT)`" excuse clause made it circular — `(GMT)` is the selector's own discriminator, so it could never disagree with the model it audits; (d) proof 3 passed vacuously on an empty sample (`0 == 0`), i.e. it passed most confidently exactly when `dazn_gmt` claimed nothing.**]**

**Files:** Create `scripts/verify_routing_incontainer.py`

- [ ] **Step 1: Take a backup first (POWERSHELL)**

Workspace hard rule. Cheap (~7 s) and covers the orphan scenario.

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.backups.tasks import create_backup_task
print(create_backup_task.apply().result)
"
```
Expected: a `dispatcharr-backup-*.zip` filename. Record it.

- [ ] **Step 2: Write the script**

```python
# scripts/verify_routing_incontainer.py
"""Read-only proof that the ecm_profiles routing model matches live reality.

Run INSIDE the container, as the dispatch user:

    docker cp Event-Channel-Managarr/ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
    docker exec -i -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell" \
        < scripts/verify_routing_incontainer.py

WHAT IT PROVES
  1. route() over LIVE names satisfies churn-proof invariants (NOT frozen counts:
     event lineups are renamed in place daily, so a live count assertion goes red
     for reasons unrelated to the routing model).
  2. The dazn_gmt bucket and the idle slots together partition exactly what the
     hand-made source binds -- a falsifiable comparison, not a restatement of the
     selector.
  3. Dispatcharr's REAL renderer produces correct local times from the dazn_gmt
     patterns, via a TEMPORARY UNBOUND EPGSource.

WHAT IT WRITES
  One temporary EPGSource, removed in a finally block keyed on its NAME (not on a
  variable, which would be None if the post_save chain raised). Dispatcharr's
  create_dummy_epg_data post_save signal ALSO auto-creates one EPGData row against
  it and emits an epg_data_created websocket event; the row goes away by CASCADE
  on delete, but a user with the UI open may briefly see the temp source in the
  EPG dropdown until they reload. No channel is repointed and no pre-existing
  EPGData row is touched.

EXIT CODE: 0 on pass, 1 on any failure.
"""

import sys
import traceback

sys.path.insert(0, "/tmp")

import ecm_profiles  # noqa: E402

from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGData, EPGSource  # noqa: E402
from apps.output import epg as epg_renderer  # noqa: E402

GROUP_ID = 1915
HANDMADE_SOURCE_NAME = "DAZN PPV Dummy (GMT)"
TEMP_SOURCE_NAME = "__ecm_verify_temp__DO_NOT_USE"
FIXTURE_ERA = "48/104/126 over 278"

failures = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def proof_1_invariants(names):
    print("\n(1) Routing invariants over LIVE channel names")
    result = ecm_profiles.route(names)

    # The no-op defect that killed two prior design revisions.
    check("dazn_gmt is non-empty", len(result["dazn_gmt"]) > 0,
          f"got {len(result['dazn_gmt'])}")

    gmt = {n for n in names if "(GMT)" in n}
    check("dazn_gmt == exactly the GMT-bearing names", set(result["dazn_gmt"]) == gmt,
          f"routed={len(result['dazn_gmt'])} gmt={len(gmt)} "
          f"symdiff={sorted(set(result['dazn_gmt']) ^ gmt)[:2]}")

    legacy = {n for n in names if n.startswith(("PPV EVENT", "LIVE EVENT"))}
    check("us_et == exactly the legacy family", set(result["us_et"]) == legacy,
          f"routed={len(result['us_et'])} legacy={len(legacy)}")

    check("no GMT name leaked into us_et",
          not [n for n in result["us_et"] if "(GMT)" in n])
    check("no idle slot leaked into us_et",
          not [n for n in result["us_et"] if n.startswith("NO EVENT STREAMING NOW")])
    check("buckets partition the input",
          sum(len(v) for v in result.values()) == len(names))

    print("       counts: " + ", ".join(f"{k}={len(v)}" for k, v in result.items()))
    print(f"       (fixture-era baseline {FIXTURE_ERA}; drift is EXPECTED as the "
          f"lineup changes -- investigate only if a check FAILED)")
    return result


def proof_2_matches_handmade_binding(result):
    print("\n(2) dazn_gmt bucket vs the live hand-made source binding")
    source = EPGSource.objects.filter(name=HANDMADE_SOURCE_NAME).first()
    # A missing source is a FAILED PRECONDITION, never a silent skip.
    check(f"hand-made source {HANDMADE_SOURCE_NAME!r} exists", source is not None,
          "this proof compares against live ground truth and cannot be skipped")
    if source is None:
        return

    bound = set(Channel.objects.filter(epg_data__epg_source=source)
                .values_list("name", flat=True))
    routed = set(result["dazn_gmt"])

    check("every routed DAZN name is bound in the live config", not (routed - bound),
          f"{len(routed - bound)} unbound: {sorted(routed - bound)[:2]}")

    # Falsifiable: the leftover must be EXACTLY the idle slots, not merely
    # "anything lacking (GMT)" -- which is the selector's own discriminator and
    # would make this check unable to disagree with the model it audits.
    idle = {n for n in bound if n.startswith("NO EVENT STREAMING NOW")}
    check("bound set partitions into routed + idle exactly", (bound - routed) == idle,
          f"unexplained={sorted((bound - routed) - idle)[:2]}")
    print(f"       bound={len(bound)} routed={len(routed)} idle={len(idle)}")


def proof_3_renderer_output(result):
    print("\n(3) Real renderer output from the dazn_gmt patterns (temp source)")
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    props = dict(ecm_profiles.profile_props(dazn))
    props["fallback_description_template"] = "verify-temp"

    sample = result["dazn_gmt"][:5]
    # Without this, an empty bucket makes the loop below assert 0 == 0 and PASS --
    # i.e. the proof would be most confident exactly when the model is most broken.
    check("there are DAZN names to render", len(sample) >= 1,
          "empty sample would make the render check vacuous")
    if not sample:
        return

    try:
        # Pre-flight: clear any orphan from an interrupted earlier run, otherwise
        # the unique name constraint fails every retry.
        EPGSource.objects.filter(name=TEMP_SOURCE_NAME).delete()
        EPGSource.objects.create(
            name=TEMP_SOURCE_NAME, source_type="dummy", is_active=False,
            refresh_interval=0, priority=0, custom_properties=props)
        temp = EPGSource.objects.get(name=TEMP_SOURCE_NAME)

        ok = 0
        for name in sample:
            programs = epg_renderer.generate_dummy_programs(
                999999, name, num_days=1, program_length_hours=4, epg_source=temp)
            title = programs[0].get("title") if programs else None
            extracted = bool(title and title != name)
            ok += extracted
            print(f"       {'OK ' if extracted else 'RAW'}  {name[:52]}")
            print(f"              -> {str(title)[:70]}")
        check("all sampled DAZN names render an extracted title", ok == len(sample),
              f"{ok}/{len(sample)}")
    finally:
        # Keyed on the NAME, not a variable: create() commits before assignment,
        # so a raise in the post_save chain would leave a variable unbound.
        deleted, _ = EPGSource.objects.filter(name=TEMP_SOURCE_NAME).delete()
        print(f"       (temp source cleanup: {deleted} row(s) removed)")


def main():
    print("=" * 70)
    print("ECM routing verification -- READ-ONLY (one temp source, auto-deleted)")
    print("=" * 70)
    names = list(Channel.objects.filter(channel_group_id=GROUP_ID)
                 .order_by("id").values_list("name", flat=True))
    print(f"\nLive channel names in group {GROUP_ID}: {len(names)}")
    result = proof_1_invariants(names)
    proof_2_matches_handmade_binding(result)
    proof_3_renderer_output(result)


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception during verification")

print("\n" + "=" * 70)
if failures:
    print(f"GATE FAILED -- {len(failures)} check(s):")
    for f in failures:
        print(f"  - {f}")
    print("DO NOT PROCEED to any later slice.")
else:
    print("GATE PASSED -- routing model reproduces the live working config.")
print(f"ECM_GATE_RESULT={'FAIL' if failures else 'PASS'}")
print("=" * 70)
sys.exit(1 if failures else 0)
```

- [ ] **Step 3: Syntax-check (BASH)**

```bash
python -m py_compile scripts/verify_routing_incontainer.py && echo "SYNTAX OK"
```

- [ ] **Step 4: Run the gate (POWERSHELL)**

```powershell
docker cp Event-Channel-Managarr\ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
$out = docker exec -i -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell" `
    < scripts\verify_routing_incontainer.py
$out
if ($out -notmatch 'ECM_GATE_RESULT=PASS') { throw "ECM GATE FAILED" }
Write-Host "GATE PASSED"
```

Expected: `GATE PASSED`, with five DAZN samples rendering extracted titles in local time (e.g. `Upcoming at 7/18 7:10 AM CDT: RWS: Chiabkard vs. Narak`).

If the gate fails, stop and report. Do not adjust expectations to match output.

- [ ] **Step 5: Confirm nothing was left behind (POWERSHELL)**

```powershell
docker exec dispatcharr python manage.py shell -c "
from apps.epg.models import EPGData, EPGSource
from apps.channels.models import Channel
print('temp sources:', EPGSource.objects.filter(name__startswith='__ecm_verify_temp__').count())
print('temp epgdata:', EPGData.objects.filter(tvg_id__contains='ecm_verify_temp').count())
print('source 42 channels:', Channel.objects.filter(epg_data__epg_source__name='DAZN PPV Dummy (GMT)').count())
"
```
Expected: `0`, `0`, and `99` (the live binding untouched).

- [ ] **Step 6: Commit (BASH)**

```bash
git add scripts/verify_routing_incontainer.py
git commit -m "feat: read-only in-container gate for the routing model

Proves the model without editing plugin.py and without repointing any channel.
Live assertions are churn-proof INVARIANTS (dazn_gmt equals exactly the
GMT-bearing names) rather than frozen counts, because event lineups are renamed
in place daily and a flapping gate gets edited away.

Every vacuous-pass hole from rev 1 is closed: a missing hand-made source is a
failed precondition not a skip, an empty sample fails instead of asserting
0 == 0, cleanup is keyed on the source NAME rather than a variable that would be
unbound if post_save raised, and the run exits non-zero so automation can consume
it. Proof 2 compares against the idle-slot set rather than excusing anything
lacking (GMT) - the selector's own discriminator, which made rev 1's version
unable to disagree with the model it audits."
```

---

## Task 9: Close the slice

- [ ] **Step 1: Full suite (BASH)**

Run: `python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 2: Confirm plugin.py untouched (BASH)**

```bash
git diff main --stat -- Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json
```
Expected: EMPTY. If not, a constraint was violated — stop and report.

Note: `Event-Channel-Managarr/__init__.py` WILL show a one-line change from Task 1's renormalize. That is expected and is a line-ending only.

- [ ] **Step 3: Update local project memory**

`.wolf/` is gitignored — local only, not committed. Add the new files to `.wolf/anatomy.md`, append a session line to `.wolf/memory.md`, and record in `.wolf/cerebrum.md`: PS 5.1 has no `utf8NoBOM` and `-Encoding UTF8` writes a BOM that survives `.strip()`; `regex` is present in the container and absent on dev machines.

- [ ] **Step 4: Push (BASH)**

```bash
git push -u origin feat/durable-epg-capture
```

---

## Definition of Done

- [ ] `python -m pytest tests/ -v` fully green
- [ ] `git diff main --stat -- .../plugin.py .../plugin.json` is empty
- [ ] The in-container gate printed `ECM_GATE_RESULT=PASS` and exited 0
- [ ] Zero temp EPGSource and zero temp EPGData rows remain; source 42 still binds 99 channels
- [ ] No credential anywhere under `config/` or `scripts/`
- [ ] The live guide is unchanged — DAZN channels still showing local times
