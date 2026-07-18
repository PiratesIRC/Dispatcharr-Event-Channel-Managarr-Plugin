# Durable Multi-Timezone Dummy EPG — Design

Date: 2026-07-18
Status: APPROVED for slices S0+S1 only. S2-S5 deferred, not approved.

## 0. Why this exists

Dispatcharr renders dummy EPG programmes on the fly from the CHANNEL NAME, using regex
patterns plus a timezone stored on `EPGSource.custom_properties`. The renderer reads
`timezone` **once per source** (`/app/apps/output/epg.py:305`) and `output_timezone` at
`:306`. There is no per-channel timezone hook anywhere in `generate_custom_dummy_programs`.

Channel group `US: PPV` (id 1915, 278 channels) contains **four** distinct provider name
families, not the two originally assumed:

Measured breakdown of all 278 names (verified live 2026-07-18):

| Family | Count | Date | Clock | Source TZ |
|---|---|---|---|---|
| Legacy `PPV EVENT NN:` (70) + `LIVE EVENT NN -` (34) | **104** | `7.17` (`M.D`) | `7:30 PM` 12h | **ET** |
| DAZN `Next \| …` / `End \| …` | **48** | `2026-07-18` ISO | `14:15` 24h | **GMT/UTC** |
| `UFC …` | **55** | ISO `start:`/`stop:` in-name | ISO | unknown |
| `Boxing …` | **8** | — | `19:00` / `6PM` | unknown |
| `US: BOX OFFICE` / `US: TNT SPORTS BOX OFFICE` (HD/SD/HEVC/4K) | **8** | — | — | n/a — permanent static channels |
| Idle slots `NO EVENT STREAMING NOW …` | **51** | — | — | n/a |
| Headers `#### … ####` | **4** | — | — | n/a |

Only **36** of the 278 actually contain `start:`. An earlier revision of this table folded the 8
permanent BOX OFFICE channels into a "UFC/Boxing" family of 71; they are unrelated static
channels and are counted separately above.

Because the renderer's timezone is source-scoped, **one dummy EPGSource cannot serve
families with different source timezones.** No amount of pattern work changes this; the
timezone field itself must differ. That is the whole reason this project exists.

### 0.1 Current state

A hand-made `EPGSource` id 42 `DAZN PPV Dummy (GMT)` (`timezone=UTC`,
`output_timezone=America/Chicago`, `managed_by='manual-dazn-gmt'`) with 99 channels bound via
**100** `EPGData` rows (99 `dazn_gmt_<channel id>` + 1 auto-created representative row
`dummy_dazn_ppv_dummy_gmt`). Deployed and working, but it exists only as hand-made database
state: not versioned, not tested, not reproducible.

### 0.2 The `(GMT)` label means UTC — confirmed from three in-box sources

For 2026-07-18, all three agree on BKFC 91:

- DAZN slot name: `… | 2026-07-18 | 17:00 (GMT) | … | US: DAZN PPV 42`
- `epgshare-plex` `ProgramData`: `2026-07-18 17:00:00+00:00`
- Legacy slot (independent family): `LIVE EVENT 02 - 1pm BKFC 91 Naples Hunt v Pugliesi`
  = 1:00 PM ET = 17:00 UTC

Under UK summer time the label would have read `18:00`. It did not. Recorded here because it
is the single assumption the whole GMT profile rests on.

## 1. Goal and scope

Move the working-but-hand-made configuration into versioned, tested code.

**Scope: local durability only.** Not a marketplace release. No README/Hub work.

Four failure modes:
1. **Container rebuild / DB loss** — restore with one command.
2. **Ongoing drift** — new slots appear or bindings get stolen; needs re-assertion.
3. **Provider format change** — patterns silently stop matching; must be told.
4. **Losing the knowledge** — reasoning, patterns, verification method versioned.

### 1.1 Approved scope: S0 + S1 ONLY

A full multi-source implementation inside the plugin was designed (rev 1, rev 2) and
**rejected after two adversarial review rounds** (ten reviewers). Both revisions contained
Critical defects, including a fix that re-created the very defect it repaired. Sizing:
~16 distinct edits to `plugin.py`, ~10 inside the attach/detach core that can blank a live
guide, against a file with **zero existing test coverage** of any function involved.

This spec therefore covers only the capture slices, which require **no `plugin.py` edits at
all** and cannot affect the running guide:

- **S0 — Capture.** Design doc, real-name fixture, bootstrap script, config template.
  Delivers failure modes 1 and 4 outright.
- **S1 — Pure module + read-only proof.** `ecm_profiles.py`, fixture-driven tests, and an
  in-container simulation that proves the routing model against live data before any code
  depends on it.

Deferred and explicitly NOT approved: S2 (multi-source attach-only), S3 (per-profile detach —
the destructive one), S4 (drift alarm), S5 (optional cleanups). §7 records why.

## 2. S0 — Capture

### 2.1 `tests/fixtures/us_ppv_channel_names.txt`

All 278 real channel names from group 1915, one per line, committed. This is the corpus every
routing assertion runs against. It is the primary artifact for failure mode 4: it makes the
provider's actual naming reality diffable over time.

### 2.2 `scripts/bootstrap_ecm.py`

Idempotent, one command, restores a rebuilt box:

```
docker exec -i -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell" < scripts/bootstrap_ecm.py
```

The `-u dispatch` is mandatory, not optional. On a rebuilt box the settings file does not yet
exist, so `write_text` CREATES it — as `root:root` if run as root, which silently blocks the
uWSGI workers from ever updating settings again (the E3 trap). `/data` already carries 20+
root-owned JSON files from past sessions as evidence. The script must also refuse to run as
root rather than relying on the caller remembering the flag.

It must:
1. Recreate `EPGSource` `DAZN PPV Dummy (GMT)` with its `custom_properties` verbatim
   (`get_or_create` on the unique `name`; `EPGSource.name` is `unique=True`,
   `apps/epg/models.py:30`).
2. Re-bind the DAZN slot channels (`name__regex=r'US: DAZN PPV \d+'`) via `EPGData` rows.
3. Write `PluginConfig.settings` from the committed template.
4. **Merge** into `/data/event_channel_managarr_settings.json` — never overwrite; that file
   holds runtime keys the template does not.
5. Be a no-op on second run.

**Run it as `-u dispatch`, never as root.** A `docker exec` defaults to root and any
`/data` file it creates lands `root:root`, silently blocking the uWSGI workers (the E3 trap).

### 2.3 `config/ecm_settings.template.json` — and the credential rule

**`/data/event_channel_managarr_settings.json` contains a plaintext `dispatcharr_password`
and `dispatcharr_username`. This repository is PUBLIC. That file must never be committed,
in whole or in part.**

The committed template therefore:
- contains ONLY keys that exist as setting ids in `plugin.json`;
- explicitly **denylists** `dispatcharr_password`, `dispatcharr_username`, `dispatcharr_url`,
  and the runtime-injected keys `timezone`, `event`, `payload` (these are written into the
  settings dict at runtime and are not user settings at all);
- carries placeholder values for environment-specific fields (`channel_profile_name`,
  `channel_groups`, `scheduled_times`), documented as needing local edit.

`bootstrap_ecm.py` reads credentials from the environment, never from the repo.

A contract test asserts: every key in the template is a `plugin.json` setting id, and no
denylisted key appears. Reuse the `ast`/`json` loader from
`tests/contract/test_manifest_parity.py`, which reads `plugin.py` without importing it.

**Separate operational action, outside this spec:** that password is sitting in plaintext in
a named Docker volume. Rotating it is recommended regardless of what this project does.

## 3. S1 — `ecm_profiles.py`, pure

### 3.1 Contract

Stdlib-only. MUST NOT import `apps.*`, `django.*`, or `core.utils`. There is **no**
Django-stubbing harness in this repo — `pyproject.toml` sets
`pythonpath = ["Event-Channel-Managarr"]` and `ecm_parsing.py` stays importable purely by
discipline, keeping module-level imports stdlib and deferring `dateutil`/`pytz` into function
bodies. `ecm_profiles.py` follows the same rule, enforced by an AST test (§4).

No module-level mutable state: no `lru_cache`, no compiled-pattern cache, no registry built at
import. Dispatcharr's loader purges and re-imports sibling modules under the plugin directory
(`apps/plugins/loader.py:832`), so module globals are not reload-safe. Measured routing cost
for all 278 names is well under a millisecond, so no cache is warranted.

### 3.2 `Profile`

Frozen dataclass: `key`, `source_name`, `selector`, `title_pattern`, `date_pattern`,
`time_pattern`, `timezone`, `output_timezone`, `program_duration_minutes`, `include_date`,
`upcoming_title_template`, `ended_title_template`, `is_default`.

Patterns are stored in JS form `(?<name>)`. Dispatcharr's frontend validator is JavaScript and
rejects `(?P<name>)` (issue #21); the renderer converts server-side at
`epg.py:357/373/386` using `regex.sub(r'\(\?<(?![=!])([^>]+)>', r'(?P<\1>', …)`. Any
conversion here must copy that negative-lookahead guard verbatim so `(?<=` lookbehinds are
not mangled.

Dispatcharr's renderer uses the third-party `regex` module (`epg.py:13`), which accepts
`(?<name>)` natively; stdlib `re` does not. So: `try: import regex as _re / except ImportError:
import re as _re`, converting to Python form only on the `re` fallback. Every `compile()` is
wrapped — a bad pattern degrades to "this profile claims nothing" with a warning, never an
exception.

### 3.3 `PROFILES` — a module constant, no JSON override

No runtime JSON setting. A user-editable profile blob would move configuration back into
unversioned DB state, which is precisely what this work exists to escape, and Dispatcharr
never prunes a removed plugin setting, so shipping one is near-irreversible.

Presets: `us_et` (default), `dazn_gmt`. That is the complete chain.

**The `SE` format is deliberately NOT a profile.** This was the defect that killed rev 2: the
SE pattern is pipe-based (`\|\s*(?<title>[^|]+?)\s*\|`), every DAZN name is pipe-delimited,
and with SE in the chain ahead of `dazn_gmt` it claimed 99 names while `dazn_gmt` claimed
**zero** — silently re-creating the exact no-op the revision was written to fix. SE is a
different install's channel-name format and is not present on this box. It stays as the
existing global `dummy_epg_channel_format` behavior, untouched, outside the profile chain.

The UFC/Boxing family is **catalogued in §0 but not given a profile in S1** — its source
timezone is unknown and cannot be determined from the names alone. S1 measures it; it does not
guess at it.

### 3.4 `route(names, profiles)` — the default is evaluated LAST, by invariant

Non-default profiles are tried in declaration order; the default claims only what nothing else
claimed. This is enforced in code and asserted by test, not left to list ordering.

**This invariant alone is not sufficient** — that was the rev-2 defect. Any non-default
profile whose selector is broad enough to claim another family's names re-creates the
failure. Therefore:
- every non-default profile's selector MUST be validated against the full fixture, and
- a profile with no verified selector MUST NOT be in the chain.

`us_et.selector` is anchored: `^\s*(?:PPV|LIVE)\s*(?:EVENT\s*)?\d+|^\s*EVENT\s*\d+`.
Measured against the fixture, anchoring drops exactly 51 names, all of them
`NO EVENT STREAMING NOW … US: DAZN PPV NN` idle slots that should never have been claimed —
zero legitimate loss.

`dazn_gmt.selector` is `^(?:Next|End)\s*\|.*\(GMT\)`.

### 3.5 Expected routing outcome (the S1 gate)

```
dazn_gmt   48    exactly the (GMT)-bearing names
us_et     104    exactly the PPV EVENT (70) + LIVE EVENT (34) family -- set identity verified,
                 not merely the same total
unclaimed 126    51 idle + 55 UFC + 8 Boxing + 8 BOX OFFICE + 4 headers
```

**Counts are the gate only against the COMMITTED FIXTURE, never against live data.** Event
lineups are renamed in place daily, so a live count assertion goes red for reasons unrelated to
the routing model, and an engineer who edits the number twice has destroyed the gate. Against
live data the assertions are invariants that survive churn: `dazn_gmt` is non-empty and equals
exactly the `(GMT)`-bearing names, no `(GMT)` name appears in `us_et`, and the buckets partition
the input.

### 3.6 Consequence for the deferred slices — read before starting S2/S3

The 51 idle slots (`NO EVENT STREAMING NOW …`) are bound to source 42 today but route
**unclaimed**. If S3 ever sets `keep_ids = routed[profile.key]`, those 51 are detached and the
orphan reaper deletes their `EPGData` rows. They are the persistent rows the provider renames
in place, so they cycle back into active service — meaning the deletion is not a one-off. Any
per-profile detach must treat "bound to a managed source but unclaimed by every profile" as a
distinct case, not as "detach it."

## 4. Testing

- `tests/unit/test_ecm_profiles.py` — pure. Routing over the committed fixture asserting
  **exact per-profile counts** (`routed["dazn_gmt"] == 48`), the default-last invariant,
  regex-dialect conversion incl. the lookbehind guard, and graceful degradation on a bad
  pattern. An exact-count assertion is what would have caught both rejected revisions'
  Critical defect before any code depended on it.
- An AST purity test asserting `ecm_profiles.py` has no non-stdlib module-level import.
- A contract test for the config template (§2.3).

No in-memory ORM fakes. Faking what `plugin.py` actually uses — `bulk_update`,
`get_or_create` against a `unique=True` constraint, `transaction.atomic`, `__regex`/`__isnull`
lookups, JSON-field lookups, and Django `post_save` signal cascades — was estimated at 4-6
days and would still be blind to the constraint- and signal-dependent behaviors it is meant to
pin. The ORM side is verified in-container instead (§5).

## 5. The read-only in-container proof (the S1 gate)

Because `ecm_profiles.py` is pure, the entire routing model is validated against live data
**without touching `plugin.py` and without writing anything**:

1. Copy `ecm_profiles.py` into the container (or inline it into a `manage.py shell` script).
   Run as `-u dispatch`.
2. Route the live `Channel.name` values for group 1915; print per-profile counts and id sets.
3. Diff `routed["dazn_gmt"]` against the channels currently bound to source 42. Disagreement
   means the routing is wrong — caught before anything depends on it.
4. Create a **temporary, unbound** `EPGSource` carrying the `dazn_gmt` props, call
   `generate_dummy_programs(channel_id, channel_name, num_days, program_length_hours,
   epg_source=tmp)` for a sample of both families, diff the rendered titles and times, then
   delete the temp source. No channel is repointed; no `EPGData` row is touched.

**Gate:** counts match §3.5 and rendered times are correct in local time. If either fails,
stop — do not proceed to any later slice.

## 6. What S0+S1 deliver against the four failure modes

| Mode | Covered? | By what |
|---|---|---|
| 1. Rebuild / DB loss | **Yes** | `bootstrap_ecm.py` + committed template |
| 4. Losing the knowledge | **Yes** | This doc + the 278-name fixture + the `(GMT)`=UTC evidence |
| 3. Format change | **Partly** | The fixture and simulation detect drift when run; no automatic alarm (deferred to S4) |
| 2. Ongoing drift | **No** | Needs multi-source routing in the plugin (S2/S3). New DAZN slots require a bootstrap re-run |

This is an honest three-of-four. The bootstrap script is not merely a deliverable — it is the
rollback net that makes every later slice safe to attempt.

## 7. Deferred slices, and why

**S2 — multi-source attach-only.** `managed_by`-keyed discovery (the key is already written
at `plugin.py:2348` and never read back, so it is free to become identity), per-profile
source creation, per-profile timezone plumbed into `_localized_template_props` (which today
reads the global setting at `plugin.py:2203` and would silently give the GMT source a wrong
`output_timezone` — a 5-hour error), and an explicit reroute set. Strictly additive: leaves
`keep_ids` alone so nothing new can be detached.

Blocking issues to resolve first, all found in review:
- `_attach_managed_epg` iterates its `channels` argument; passing a reroute *id set* without
  also adding those channels to the list is a silent no-op.
- Source 42 is not ECM-managed, so any reroute set defined over ECM-managed sources is empty
  for all 99 channels — the migration cannot execute as previously written.
- Toggle-off teardown and dry-run preview look the source up by the literal name
  `"ECM Managed Dummy"` (`plugin.py:2632`, `:2661`), so with N sources, unticking
  `manage_dummy_epg` would no longer turn the feature off.
- Creation must key on the same identity as discovery, or a renamed source is found by one
  and re-minted by the other, producing two rows.

**S3 — per-profile detach.** The genuinely destructive step. Today
`keep_ids = set(enabled_channel_ids)` (`plugin.py:2693`) is a superset guard: a routing
mistake cannot detach anything. Making it `routed[profile.key]` means any routing bug detaches
real channels and the orphan reaper `.delete()`s their rows in the same pass. Requires the
reaper hoist above the `if not stale: return []` early return (`plugin.py:2541`), the
attach-before-detach ordering, and the wrong-row rename fix (`plugin.py:2510-2512`).

**S4 — drift alarm.** Deferred because its thresholds were measured wrong twice and the
correct design is not yet settled. For the record: `us_et` title extraction is **34-51%**
depending on denominator, not the ~100% previously assumed — 51 of the legacy slots are bare
placeholders (`PPV EVENT 48`) with no event text, which correctly extract nothing. Any alarm
must exclude bare-slot and header shapes from its denominator, and must distinguish "the
regex broke" from "the schedule is quiet". Hysteresis needs a `/data` state file under the
existing scan lock, because module globals are not reload-safe and `on_m3u_refresh` fires once
per M3U account.

**S5 — optional.** `may_overwrite_pattern` extraction, per-profile display-name rules,
`_managed_override_ids` hoisted once per pass.

Note on `stock_patterns` (`plugin.py:2391-2400`) for whenever S2 lands: it is a union of
**17** distinct historical strings (title 8, time 5, date 4), of which 5 are historical-only.
Splitting it per profile would strand those and permanently freeze pattern upgrades on
existing installs. It must stay global, with per-profile entries additive. Any guard test must
pin the literal 17, not an approximation.

## 8. Known residuals

- `_start_background_scheduler` parks its thread in a module global (`plugin.py:2009-2011`) —
  the bug-136 reload family. Pre-existing. Failure mode 2's durability would rest on it.
- The TZ abbreviation in templates is computed via `datetime.now(tz).strftime("%Z")`
  (`plugin.py:2227-2228`), so it self-heals at DST but rewrites `custom_properties` at each
  boundary.
- `create_dummy_epg_data` fires on every `post_save`, not only on create
  (`apps/epg/signals.py:26-33`), so renaming a managed source orphans its old representative
  row and creates a new one.
- Source 18 has 16 unreferenced `EPGData` rows, of which only 15 are UUID-shaped and therefore
  reapable by the existing filter (`plugin.py:2568`).
- `is_active=False` on a dummy source is **cosmetic**: `apps/output/epg.py:1337` has no
  `is_active` check, and the refresh-task path returns early for dummy sources. Channels bound
  to an inactive dummy source render normally. Any future teardown must repoint, not
  deactivate.

## 9. Non-goals

No Hub/marketplace release. No README rewrite. No changes to the hide-rule engine, scan logic,
or any non-EPG behavior. No modification to `plugin.py` in S0 or S1.
