# Per-Group Managed Dummy EPG Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

> **Revision 2, 2026-09-02.** Revision 1 was reviewed by four agents with separate lenses and
> did not survive. Four of its statements about the existing code were wrong, and two of its
> three design decisions were unimplementable. The section "What revision 1 got wrong" near the
> end records every defect and how this revision answers it. Read that section before assuming
> any part of this plan is optional.

**Goal:** Let an operator map a Dispatcharr channel group to its own managed dummy EPG source,
so that groups needing different timezones, durations or title patterns stop competing for one
shared source.

**Architecture:** A new text setting maps a group name to a source name, one mapping per line.
The plugin builds one routing profile per mapped source, seeded from the global settings when it
creates the source and never rewritten afterwards, and routes a channel by its channel group
rather than by a regular expression on its name. A plugin-owned record file under `/data/`
records which sources the plugin created, because that is the only durable place to keep it.
Routing is authoritative in both directions: a channel whose group stops mapping returns to the
shared source, but only when the plugin's own record says it put that channel there. One pure
function decides the destination for every channel, so both directions are unit-testable outside
the container.

**Tech stack:** Python 3, pytest, Django ORM access to `EPGSource` and `Channel`.

**Spec:** GitHub issue 29 on `PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin`, the two
maintainer comments on it (2026-08-30T13:19:28Z and 2026-08-30T20:40:19Z) and the requester's
two confirmations (2026-08-30T18:23:48Z and 2026-08-30T21:31:02Z). The requester agreed to the
reduced shape, so no part of this plan is still awaiting their answer.

---

## Global constraints

- No em dashes in any plugin-facing copy: settings labels, help text, rendered report text,
  README and documentation pages.
- No contractions in code, comments, docstrings, test names or string literals in source. Write
  "does not", "cannot", "it is". Possessives are not contractions.
- No invisible Unicode characters anywhere. Where one must be matched, write the escape.
- `ecm_profiles.py` is standard library only. Four guards in
  `tests/contract/test_module_purity.py` enforce this by parsing the file: no import outside the
  standard library except a guarded optional one, no `apps.*` or `django.*` or `core.*` import,
  no module-level mutable state, and no caching decorator. Every guard has a self-check proving
  the guard itself fails on a violating module.
- Version is calver `Major.YY.DDDHHMM`, bumped only by `bump_version.py`. Do not hand-edit a
  version string.
- Settings are read from the live `settings` dictionary passed into the call. Never from
  `self.saved_settings` and never from cached instance state.
- **Every routing decision fails safe by leaving the channel bound exactly where it is.** This is
  stated again inside Task 6, because revision 1 stated it here and then contradicted it.

## Measured facts this plan depends on

All measured on 2026-09-02 against the running container and the current source. Re-measure
rather than trusting these if significant time has passed.

| Fact | Value |
|---|---|
| Settings the plugin declares | 22 real settings plus 6 section headers, 28 entries, identical in `plugin.py` and `plugin.json` |
| Fields on the `Profile` dataclass in `ecm_profiles.py` | 16 |
| Method bodies hash-pinned in `tests/contract/test_s2_wiring.py` | 5, and neither `_ensure_profile_source` nor `_reroute_claimed_channels` is among them |
| Actions returning a `file` key anywhere in `plugin.py` | 0 |
| `EPGSource.name` | `unique=True`, case-sensitive in Postgres |
| Dummy EPG sources on this installation | 5, listed below |

Dummy sources and their channel bindings, measured. Binding counts move with hide state and are
not a fixture:

| Id | Name | Channels | Ownership marker |
|---|---|---|---|
| 18 | `ECM Managed Dummy` | 2 | plugin |
| 36 | `Dummy - Local OTA` | 0 | none |
| 37 | `Dummy - No Guide` | 4 | none |
| 38 | `Dummy - 24/7 Streams` | 1 | none |
| 42 | `DAZN PPV Dummy (GMT)` | 0 | plugin |

**Five channels sit on three dummy sources the plugin does not own.** Task 6 exists partly to
make sure this feature cannot take them.

## The regression guard

**With the new setting empty, routing behaviour must be identical to today.** Express this over
routing decisions for a fixed name-and-group fixture, never over live channel counts, which move
with hide state. Task 3 Step 1 writes it, and Task 3 Step 2 writes the separate test that proves
the group path is not simply absent, because the empty-mapping test passes either way.

---

## Decisions this plan settles

### Decision 1: the record of what the plugin created lives in a file under `/data/`

The plugin needs to know which dummy EPG sources it created for a group mapping. That record
gates every move in Task 6.

**It cannot live in the source's `custom_properties`.** Measured: Dispatcharr's frontend builds
that object from a fixed list of keys and submits it whole, `EPGSourceSerializer.update` assigns
it with `setattr` rather than merging, and the plugin's existing `managed_by` marker appears in
no frontend bundle. So `managed_by` survives today only because the plugin rewrites it on every
applied run. Task 4 deliberately stops that rewrite for a mapped source, which would leave any
marker there with nothing to repair it. The operator opening the source in Dispatcharr's editor
to set its timezone, **which is the entire point of this feature**, would delete the marker on
save.

**The record is therefore `/data/event_channel_managarr_group_sources.json`**, following the
tracker file the plugin already keeps at
`/data/event_channel_managarr_undated_first_seen.json` and reusing its load, save and prune
shape (`_load_undated_tracker` at `plugin.py:926`, `_save_undated_tracker` at `:942`).

Structure, keyed by the exact `EPGSource.name` as created:

```json
{
  "ECM - NFL": {
    "created": "2026-09-02",
    "created_for_groups": ["nfl sunday ticket"],
    "source_id": 51
  }
}
```

Failure behaviour, which must be tested: a missing, empty, unreadable or malformed file yields an
empty record, therefore **no channel is eligible to move back, therefore nothing moves**. Losing
the file strands channels where they are, which is recoverable by hand. That is the safe
direction.

### Decision 2: an explicit group mapping beats a name selector

A channel can be claimed both by a group mapping the operator typed and by one of the two name
selectors shipped in code. The group mapping wins, because the mapping is explicit operator
configuration and the name selectors are defaults. Implemented by ordering group profiles ahead
of code profiles in the routing function, and tested by passing the profiles in reversed order
and asserting the same result, because `route()`'s own docstring records that the analogous
ordering invariant shipped as a silent no-op twice when it rested on list order.

Consequence to document: mapping a group that currently holds channels routed by name to
`DAZN PPV Dummy (GMT)` moves those channels. The dry run must show it before it happens.

### Decision 3: the reverse move requires a mapping that parsed with zero problems

Revision 1 triggered the reverse move on "this channel's group does not map anywhere". A missing
equals sign, a reserved source name, a cleared field or a settings dictionary that never carried
the key all produce that condition, so one typo would have rebound every visible channel of a
group and shifted its rendered guide times by hours.

**When the parser returns any problem, the reverse move is suppressed entirely for that run**,
the forward move still runs for lines that parsed, and the run reports that it was suppressed and
why. This is what makes the fail-safe constraint true rather than merely stated.

### Decision 4: never move a channel off a dummy source the record does not name

`_epg_binding_is_reroutable` at `plugin.py:2388` returns True for **any** source whose type is
dummy, with no ownership check. That was written for narrow name-pattern claims where a false
claim is rare. A group mapping claims every channel in its group unconditionally, and three
hand-made dummy sources on this installation hold five channels between them.

**A channel may be moved only when its current EPG source is unset, is the shared source, or is
named in the record file from Decision 1.** A channel on a hand-made dummy source is left alone.
The existing `_epg_binding_is_reroutable` guard still applies on top of this, unchanged.

### Decision 5: one pure function decides the destination for every channel

Revision 1 computed the forward destination in one place and the reverse eligibility in another,
with nothing holding them consistent. That is the same defect class as the channel-title pattern
written twice in this repository, which needed `tests/contract/test_us_pattern_parity.py` to stop
the copies drifting.

One pure function returns a destination for every in-scope channel. A move is then
`desired != current`, in either direction, and both directions are covered by one set of unit
tests. This also removes the need for a separate reverse-move preview.

### Decision 6: how a cleared mapping is handled, and how the operator can tell

The plugin merges settings as the values cached on disk overlaid by whatever the form sent
(`plugin.py:643-672`). Dispatcharr does not reliably send a field the operator has **cleared**,
which is why that function already carries hand-written workarounds for the scheduled-times
setting and for the profile and group settings.

**When the mapping key is absent from the live form values, the saved value is preserved**,
matching the existing treatment of the group setting and avoiding a destructive surprise from an
ambiguous signal. To make that visible rather than mysterious, **the Validate Configuration
action prints the mapping the plugin will actually use**, so an operator who cleared the field
can see whether the clear took effect.

Also document: scheduled runs reload settings from the disk file (`plugin.py:2076-2090`), which
is written only by the action handlers. A mapping typed into the form reaches an unattended run
only after an action button persists it.

---

### Task 1: Parse the group-to-source mapping

**Files:**
- Modify: `Event-Channel-Managarr/ecm_profiles.py`
- Test: `tests/unit/test_ecm_profiles.py`

**Interfaces:** `parse_group_source_map(raw)` returns `(mapping, problems)`. `mapping` is an
ordered dictionary of casefolded, stripped group name to the source name exactly as typed.
`problems` is a list of plain-language strings naming each rejected line and why.

`RESERVED_SOURCE_NAMES = ("ECM Managed Dummy", "DAZN PPV Dummy (GMT)")` as a module constant,
compared casefolded.

| Input | Result |
|---|---|
| `NFL Sunday Ticket = ECM - NFL` | accepted |
| a line ending `\r\n` | accepted, the carriage return stripped |
| blank lines, and lines beginning `#` | ignored silently, no problem reported |
| no `=` present | rejected |
| empty group name or empty source name | rejected |
| the same group listed twice | first wins, problem names the duplicate |
| two groups mapping to one source name | accepted, both route there |
| two source names differing only in case | rejected as a collision, because `EPGSource.name` is unique and case-sensitive in Postgres, so accepting both creates two sources |
| a source name equal to a reserved name | rejected, problem explains that the shared source and the code-owned source cannot be group targets |
| leading and trailing spaces on either side | stripped |

- [ ] **Step 1: Write the failing tests**, one per row, asserting on both the mapping and the
  problem strings. Include an empty and a whitespace-only input returning an empty mapping and
  **no** problems, because that is the default state of the setting and it must be silent.

- [ ] **Step 2: Implement `parse_group_source_map`.** Standard library only, no module-level
  mutable state.

- [ ] **Step 3: Verify.** Run `tests/unit/test_ecm_profiles.py` and
  `tests/contract/test_module_purity.py`.

---

### Task 2: Build a routing profile per mapped source

**Files:**
- Modify: `Event-Channel-Managarr/ecm_profiles.py`
- Test: `tests/unit/test_ecm_profiles.py`

Add two fields to the frozen `Profile` dataclass, both with defaults. Verified safe: every
`Profile(...)` construction in the repository uses keyword arguments, and `dataclasses.replace`
is likewise only called with keywords, so no positional construction breaks.

- `user_managed: bool = False`. True means seed the source once at creation and never rewrite it.
- `group_names: tuple = ()`. The casefolded group names routing to this profile.

**Interfaces:** `build_group_profiles(settings)` returns `(profiles, problems)`, one profile per
distinct mapped source name, each copied from the resolved default profile then overridden.

Properties, each needing a test:

1. `selector` is the empty string. `compile_pattern("")` returns `None` (`ecm_profiles.py:82`) and
   both `route()` (`:145`) and `claimed_targets()` (`:342`) skip a `None` selector, so a group
   profile cannot claim by name. Assert the behaviour, do not assume it.
2. `key` is derived from the source name, prefixed, and casefold-deduplicated. It must not collide
   with `us_et`, `dazn_gmt` or the `UNCLAIMED` sentinel.
3. **A pathological mapping must never raise.** `route()` raises `ValueError` on duplicate keys and
   on a sentinel collision (`ecm_profiles.py:130-135`), and an unhandled raise on the scan path is
   an outage rather than a fail-safe. Test a mapping whose source names collide after slugging, and
   one containing the literal `__unclaimed__`: both must yield a usable profile tuple plus a
   problem string, never an exception.
4. `is_default` is False on every group profile.
5. An empty mapping returns an empty tuple and no problems.
6. A group profile inherits the global timezone and duration at build time.

- [ ] **Step 1: Write the failing tests, including the `profile_props` key pin.** Pin the exact
  key set `profile_props` returns, so a new dataclass field cannot leak into a source's stored
  properties. Write this pin **before** the implementation, not after.

- [ ] **Step 2: Add the two dataclass fields and implement `build_group_profiles`.**

- [ ] **Step 3: Verify** the full unit and contract suites, not only the files touched.

---

### Task 3: One pure destination function, routing by channel group

**Files:**
- Modify: `Event-Channel-Managarr/ecm_profiles.py`, `Event-Channel-Managarr/plugin.py`
- Test: `tests/unit/test_claimed_targets.py`, `tests/contract/test_s2_wiring.py`

This is the structural core. It replaces both the forward claim and the reverse eligibility of
revision 1 with one decision, implementing Decision 5.

**Interfaces:**

```python
ChannelBinding = namedtuple(
    "ChannelBinding", "id group_name source_name source_is_plugin_created")

def routing_destinations(bindings, group_profiles, code_profiles,
                         default_source_name, mapping_is_clean):
    """Return {channel id: destination source name} for channels that must move."""
```

Plain tuples in, plain dictionary out, so the purity guards still hold and no Django object
reaches this module. `source_is_plugin_created` is supplied by the caller from the record file of
Decision 1. `mapping_is_clean` is False when the parser returned any problem.

Rules, each needing a test where flipping exactly one input field flips the outcome:

- A channel whose group maps goes to that mapped source.
- Group profiles are evaluated before code profiles (Decision 2).
- A channel already on its destination is absent from the result.
- A channel whose group does not map, and which is on a plugin-created mapped source, goes to
  `default_source_name`. That is the reverse move.
- A channel whose group does not map and which is **not** on a plugin-created source is absent.
  This is Decision 4, and it is what protects the five channels on hand-made dummy sources.
- When `mapping_is_clean` is False, **no** reverse destination is produced, only forward ones
  (Decision 3).
- A channel with no group at all is absent.
- Group comparison is casefolded and edge-stripped, matching `plugin.py:3354`.

- [ ] **Step 1: Write the empty-mapping regression guard.** Assert the pinned ground truth
  directly, reusing the existing constants in `tests/unit/test_claimed_targets.py`, **and**
  equality with today's `claimed_targets`. Equality alone is vacuous: if the fixture path breaks,
  both sides come back empty and it still passes. Note that the two functions have different key
  shapes, one keyed by name and one by channel id, and that
  `tests/fixtures/us_ppv_channel_names.txt` pins names that recur across several channels, so a
  naive comparison collapses or double-counts.

- [ ] **Step 2: Write the failing tests for group routing.** There is **no group fixture**;
  `tests/fixtures/` contains only a list of channel names with no group dimension, so every group
  case is hand-built. Cover each rule above, plus two channels sharing a name in different groups
  routing differently, and a channel whose name matches the code selector while its group maps
  elsewhere.

- [ ] **Step 3: Write the ordering-invariant test.** Pass the profiles in reversed order and
  assert identical results, so Decision 2 is tested rather than the list order.

- [ ] **Step 4: Implement `routing_destinations`.** Leave the existing `claimed_targets`
  untouched so nothing else changes behaviour as a side effect.

- [ ] **Step 5: Write the wiring test before the wiring.** An `ast` test that
  `_reroute_claimed_channels` calls `routing_destinations`, and that its `select_related` call
  (`plugin.py:2556`) includes `"channel_group"`. Without this, every unit test in this task can
  pass while the shipped code routes nothing.

- [ ] **Step 6: Rewrite `_reroute_claimed_channels` to consume the destinations.** Build each
  `ChannelBinding` from the pre-pass state, once, and use that same snapshot for both directions.
  Do not re-query between the forward and reverse moves: revision 1 could otherwise move a channel
  forward and immediately move it back, writing twice per channel every run for ever.

- [ ] **Step 7: Verify.** Neither method touched is hash-pinned; confirm by running
  `tests/contract/test_s2_wiring.py` and reading the result rather than assuming.

---

### Task 4: Seed a mapped source once, then stop writing to it

**Files:**
- Modify: `Event-Channel-Managarr/ecm_profiles.py`, `Event-Channel-Managarr/plugin.py`
- Test: `tests/unit/test_ecm_profiles.py`, new `tests/contract/test_group_source_ownership.py`

`_ensure_profile_source` (`plugin.py:2471`) currently rewrites, on every applied run:
`timezone`, `output_timezone`, `program_duration`, `include_date`, `title_template`,
`upcoming_title_template`, `ended_title_template`, `fallback_title_template`,
`fallback_description_template` **and `managed_by`**. Those first nine are exactly the settings
the requester needs to differ per group.

A structural test cannot tell `if profile.user_managed` from `if not profile.user_managed`, and
the inverted form is not a mild bug: it would freeze the shared source and rewrite the mapped
ones, the exact opposite of the feature. So the decision moves into a pure function.

**Interface:** `source_props_to_write(profile, current_props, desired_props)` returns the
properties to write, or `None` meaning write nothing.

- [ ] **Step 1: Write the failing unit tests.** `user_managed` True plus a drifted `timezone`
  returns `None`. `user_managed` False plus the same drift returns a dictionary restoring it.
  Pattern keys never appear in the result under either. An inverted comparison swaps the first two
  answers for identical inputs, so these tests fail loudly on the inversion.

- [ ] **Step 2: Implement `source_props_to_write` and call it from `_ensure_profile_source`,**
  which then performs input and output only.

- [ ] **Step 3: Write the wiring test**, one line, that `_ensure_profile_source` calls it.

- [ ] **Step 4: Record what was created.** On creation of a `user_managed` source, write the entry
  into the record file of Decision 1. Test that the entry appears if and only if `user_managed` is
  True, and that the record file writer and the Task 6 reader both reference one shared constant
  rather than separate literals.

- [ ] **Step 5: Handle the adopted source.** `_ensure_profile_source` uses `get_or_create`, so a
  source that already exists returns `created` False and gets no record entry, leaving the forward
  move working and the reverse move permanently impossible. **Decide deliberately**: either record
  an adopted source too, or refuse to adopt and report it as a problem. Whichever is chosen, test
  it, and make sure the operator is told which happened rather than getting silence.

- [ ] **Step 6: Note that `managed_by` also stops being repaired** on a mapped source, and say so
  in the code comment, because it is a real consequence of the ownership guard.

- [ ] **Step 7: Verify in the container both ways.** Change a mapped source's `timezone` and
  `program_duration` in Dispatcharr's editor, run applied, read them back unchanged. Then do the
  same to the shared source and confirm the plugin does restore them, proving the check is not
  vacuous in either direction. This is verification, not coverage; the unit tests in Step 1 are
  the coverage.

---

### Task 5: Create a mapped source for a group with no channels yet

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`
- Test: `tests/contract/test_group_source_ownership.py`

The requester asked to configure a source before its event channels appear, so an applied run
must walk the mapping rather than only reacting to matched channels.

- [ ] **Step 1: Write the failing tests**, including the line-number test that the pre-creation
  call sits **after** the dry-run early return, using the technique already in
  `tests/contract/test_s2_wiring.py:173`, which compares call line numbers against a return line
  number. A dry run must never create a row.

- [ ] **Step 2: Place the pre-creation pass inside the `toggle_on` branch.** Revision 1 said "top
  of the applied branch", which is **above** the `manage_dummy_epg` test at `plugin.py:3125` and
  would create sources for an operator who has the managed EPG feature switched off. Test that it
  creates nothing when the toggle is off.

- [ ] **Step 3: Check the source name before creating.** `EPGSource.name` is unique and a mapped
  name colliding with a non-dummy source raises an integrity error that is caught, warned and
  swallowed, so the mapping fails on every run for ever with only a log line. Detect the collision
  and report it as a problem instead.

- [ ] **Step 4: Report creation honestly.** Log and count created sources, but **do not report a
  created source as the mapping having taken effect**: a group mapped but absent from the
  `channel_groups` narrowing setting creates a source and routes nothing, for ever. Task 7 Step 5
  adds the validation that catches this; the run summary must not contradict it.

- [ ] **Step 5: Confirm nothing deletes a source.** Nothing does today and this plan adds nothing.
  Deleting an `EPGSource` would cascade to its guide rows.

---

### Task 6: The reverse move, and the record file

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`
- Test: `tests/contract/test_group_source_ownership.py`, `tests/unit/test_claimed_targets.py`

The decision itself was implemented and unit-tested in Task 3. This task supplies its inputs and
performs the writes. **Read the four decisions above before starting: this is the task where a
defect rebinds channels the operator did not ask about.**

- [ ] **Step 1: Implement the record file**, reusing the shape of `_load_undated_tracker` and
  `_save_undated_tracker`. Test that a missing, empty, unreadable and malformed file each yield an
  empty record and therefore no eligible channel.

- [ ] **Step 2: Resolve the destination source before the move, not during it.**
  `_reroute_claimed_channels` runs **before** the shared source is resolved in both branches:
  reroute at `plugin.py:3084` against the source lookup at `:3091` on the dry-run path, and
  reroute at `:3120` against `_get_or_create_managed_epg_source` at `:3126` on the applied path.
  That order is locked in by `test_reroute_calls_precede_attach_and_detach_calls`. Do **not** call
  `_ensure_profile_source` for the default profile as a shortcut: it writes US-format properties
  unconditionally, while `_get_or_create_managed_epg_source` selects different templates when the
  format setting is `SE`, so on an installation using the SE format the two writers would fight
  over the shared source on every run. Pass the shared source name in and skip reverse moves when
  it does not exist.

- [ ] **Step 3: Perform the moves** inside one `transaction.atomic()` block, reusing the existing
  `EPGData.objects.get_or_create` and `bulk_update` pattern.

- [ ] **Step 4: Reap orphans on every vacated source**, forward and reverse.
  **A structural test cannot verify this.** `tests/contract/test_s2_wiring.py:198,204` already
  assert that the reroute function's source text contains `_epg_binding_is_reroutable` and
  `_reap_orphaned_epg_data`, and it does, because the forward move uses them. Any equivalent
  assertion for the reverse path passes today against code with no reverse move at all. Coverage
  comes from the Task 3 unit tests; keep only a one-line wiring assertion here.

- [ ] **Step 5: Document the load-bearing accident.** A rerouted channel cannot be detached in the
  same pass because the detach keeps every id in `enabled_channel_ids` and eligibility requires
  membership in that same set. Revision 1 relied on this without noticing. Write it into the code
  comment so a later change cannot remove it silently.

- [ ] **Step 6: Verify on the live installation with a dry run first.** Add a mapping, dry run,
  read the reported moves, and only then run applied. Do not skip the dry run.

---

### Task 7: The setting, validation, and the report surfaces

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py`, `Event-Channel-Managarr/plugin.json`,
  `config/ecm_settings.template.json`
- Test: `tests/contract/test_group_source_ownership.py`, `tests/contract/test_config_template.py`

- [ ] **Step 1: Add the field.** Id `group_epg_source_map`, type `text`. **Measured: a `text`
  field renders as a Mantine textarea and round-trips newlines verbatim, with no normalisation or
  stripping.** It has no autosize, so it is a small fixed box and a four-group mapping will need
  scrolling. Help text states the format, gives an example, says an unlisted group keeps the
  shared source, and says a mapped source is seeded once and then belongs to the operator.

- [ ] **Step 2: Write the field-parity test.** `tests/contract/test_manifest_parity.py` covers
  actions, action events and version only. **It has no field parity assertion at all**, so there
  is nothing there to extend. Copy the per-field pattern from
  `tests/contract/test_undated_ended_rule.py:59`: assert the id appears in `plugin.py` and in the
  manifest's field ids. Without this the setting can be missing from the live code and every test
  still passes, because Dispatcharr serves the live `plugin.py` property.

- [ ] **Step 3: Add the key to `config/ecm_settings.template.json`** and to that file's test.
  `tests/contract/test_config_template.py` checks the template against `plugin.json` only. Decide
  deliberately whether the mapping belongs in the `REPLACE_ME` placeholder set, since it is
  environment-specific, and write the reason into the test.

- [ ] **Step 4: Add the key to the settings merge preservation logic** (Decision 6) and to the CSV
  settings snapshot at `plugin.py:3761`, each with a test.

- [ ] **Step 5: Extend Validate Configuration with four checks**, all of which catch a silent
  no-op:
  1. every parser problem;
  2. a mapped group that matches no existing channel group;
  3. **a mapped group absent from the `channel_groups` narrowing setting**, which routes nothing
     for ever while looking configured;
  4. a mapped source name that collides with an existing non-dummy source.
  Also print the effective mapping the plugin will use (Decision 6).

- [ ] **Step 6: Do not overflow the toast.** `validate_configuration_action` already returns 9 to
  12 newline-joined lines in a single `message`, and a toast shows roughly 280 characters clipped
  from the middle with newlines collapsed. **The plugin returns a `file` key in zero actions
  today.** Report the first problem plus a count in `message`, put the full list in the log, and
  include the effective mapping in the CSV header lines, which is an existing file surface. Do not
  invent a new file output in this task.

- [ ] **Step 7: Add a CSV column naming each channel's EPG source**, so a reader can see where a
  channel landed. The current columns end `has_epg, managed_epg_assigned, managed_epg_detached`
  (`plugin.py:3795`) and cannot explain a move. If this is deferred, say so in the user guide
  rather than leaving it unsaid.

- [ ] **Step 8: Verify the form inside the container** through Dispatcharr's own
  `_normalize_fields`. The contract tests read `plugin.py` with `ast` and cannot execute the
  `fields` property, so they cannot prove what is actually served.

---

### Task 8: Documentation, release and live verification

**Files:** `README.md`, `docs/USER-GUIDE.md`, `docs/CHANGELOG.md`,
`Event-Channel-Managarr/plugin.json`, `Event-Channel-Managarr/CLAUDE.md`, `.wolf/` records, and
the Hub listing fork `Dispatcharr-Plugins-Fork/`.

- [ ] **Step 1: Write the user guide section.** It must state: the mapping format; that an
  unlisted group keeps the shared source and nothing changes for an existing installation; that a
  mapped source is seeded once and then belongs to the operator, edited in Dispatcharr's own EPG
  source editor; that **routing does nothing unless the managed dummy EPG setting is on**; that
  **a mapped group must also appear in the channel-groups narrowing setting or it routes nothing**;
  that only channels which end a scan visible are moved; that a mapping typed into the form
  reaches an unattended scheduled run only after an action button saves it; that an M3U refresh
  triggers an applied run, so mapping changes can act unattended; that a mapping change takes
  effect on the next applied run; that nothing deletes a source and how to remove one by hand
  (unbind first, then delete in Dispatcharr), and that a typo therefore leaves a permanent source;
  that a channel taken from the code-owned source does not return to it on undo; and the seeded
  pattern limitation below.

- [ ] **Step 2: Record the accepted limitation.** A mapped source is seeded from the resolved
  default profile, which carries the US patterns. The global format setting can also be `SE`, and
  that choice is not carried into the seed. The operator edits patterns anyway, so a seeded
  pattern is a starting point rather than a promise.

- [ ] **Step 3: Correct `Event-Channel-Managarr/CLAUDE.md`.** It states the plugin declares 27
  fields; the measured figure is 22 real settings in 28 entries. Its line about the two managed
  sources reads as though 18 and 42 are channel counts; they are source id numbers.

- [ ] **Step 4: Review open issues and pull requests before tagging**, in both this repository and
  the marketplace repository `Dispatcharr/Plugins`, as `.claude/rules/release.md` requires.

- [ ] **Step 5: Bump the version** with `bump_version.py`. Run the full suite and record the
  measured count. Do not state a count that was not measured.

- [ ] **Step 6: Audit, then push, in that order.**
  `python ../.claude/skills/pre-publish-audit/audit_publish.py --ref <branch> --rules .publish-audit.json`
  Scan history separately with `git rev-list --objects`, which the script does not do.

- [ ] **Step 7: Tag, then build the release asset from the tag**, with
  `git -c core.autocrlf=false -c core.eol=lf archive --format=zip --prefix=Event-Channel-Managarr/`,
  gated by `scripts/validate_zip.py`. Never run `zip.cmd` from an agent shell; it ends in `pause`.
  Audit the shipped artifact, not only the branch. Download the published asset back and compare
  it byte for byte.

- [ ] **Step 8: Open the marketplace listing pull request.** This listing is **external**, so the
  pull request changes only the `version` field in
  `plugins/event-channel-managarr/plugin.json` in the fork. The download URL keeps the `v` prefix
  before the version. The pull request title needs a bracketed prefix. **A merged pull request is
  not a published listing:** confirm by fetching
  `https://dispatcharr.github.io/Plugins/manifest.json` and reading `latest_version`, not
  `version`.

- [ ] **Step 9: Deploy and verify.** Deploy from the git index rather than the working tree, as
  `docker exec -u dispatch`. **The files sit inert until the operator presses the Plugins-page
  refresh control or the container restarts**, so budget for that. `PluginConfig.version` reads
  the old version after a correct deploy and is not a deploy check; use the initialisation log
  line. Prove the routing by generating the guide with Dispatcharr's own
  `generate_dummy_programs` and comparing before against after.

- [ ] **Step 10: Record learnings** in `.wolf/cerebrum.md` and `.wolf/memory.md`, log any defect
  found into `.wolf/buglog.json`, and answer the requester on issue 29 in first person, checked
  with the outbound writing skill before posting.

---

## What revision 1 got wrong

Kept so that a future reader does not reintroduce any of it. Every item below was verified
against the source or the running container, not inferred.

**Design defects, all fatal as written:**

1. **The ownership marker was stored in the source's `custom_properties`,** which Dispatcharr's
   own EPG source editor replaces wholesale from a fixed key list. The operator editing the
   source, the action the feature exists to enable, deleted it. Answered by Decision 1.
2. **The reverse move triggered on "does not map anywhere",** which is what a typo produces, so
   one malformed line would have rebound a whole group. Answered by Decision 3.
3. **The move guard accepted any dummy source,** so the five channels on three hand-made dummy
   sources on this installation could be taken, with nothing recording where they came from.
   Answered by Decision 4.
4. **Forward and reverse computed the same thing in two places** with no test holding them
   consistent, and a plausible read of it moved a channel forward then immediately back, for ever.
   Answered by Decision 5.
5. **The reverse move's destination was not resolved at the point in the run where it was placed.**
   Answered by Task 6 Step 2.
6. **The pre-creation pass sat above the master toggle check,** so it would have created sources
   for an operator with the feature switched off. Answered by Task 5 Step 2.

**Test defects:**

7. **Every test proposed for the reverse move would have passed against unmodified code,** because
   the existing tests are string searches over a function that already contains those names.
8. **The regression guard was over-described:** with an empty mapping the new path reduces to the
   old one by construction, so it passes whether group routing works, is inverted, or is absent.
9. **Three tasks implemented before testing.**

**Factual errors:**

10. **"37 settings fields"** was produced by counting lines containing `"id":` in the manifest,
    which also counted its 9 action definitions. The real figure is 22 settings in 28 entries.
11. **"the `Profile` dataclass has 17 fields"** is 16.
12. **`tests/contract/test_manifest_parity.py` was named as the place to test the new field.** It
    contains no field parity assertion at all.
13. **"18 channels and 42 channels"** were source id numbers misread as channel counts. Measured
    counts are 2 and 0, and they move with hide state so they cannot anchor a regression claim.
14. **The property list `_ensure_profile_source` rewrites omitted `managed_by`.**
15. **`test_module_purity.py` has four guards, not three.**

**Operator-facing gaps:**

16. Clearing the mapping to undo a mistake could be shadowed by the stale value cached on disk.
17. Mapping problems were routed into a toast already carrying 9 to 12 lines and clipping at
    roughly 280 characters.
18. Three scope interactions were undocumented: the master toggle, the group narrowing setting,
    and visible-only channels.
19. The CSV cannot show which source a channel landed on.
20. The release task omitted the issue review, the tag, the asset build and its validation, the
    second audit, the history scan, the entire marketplace listing pull request, and the fact that
    a deploy does not reload the plugin.

## What this plan deliberately does not do

- It does not make the name source, or the inactive, placeholder and no-event patterns per-group.
  Those are visibility settings rather than EPG source properties. The requester accepted this.
- It does not delete an EPG source, ever.
- It does not add per-group settings to the plugin form. A Dispatcharr plugin settings form allows
  only `string`, `number`, `boolean`, `select`, `text` and `info` fields with no repeating group,
  so per-group settings would multiply this plugin's 22 settings by the number of groups and
  duplicate a screen Dispatcharr already ships.
- It does not change how the shared `ECM Managed Dummy` source or the code-owned
  `DAZN PPV Dummy (GMT)` source behave.
- It does not introduce a `file` output for any action. That is a real gap for long readouts, but
  it is a separate change to a plugin that has never had one.
