# Undated Event Window Hide Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide an event channel whose name carries a clock time but no date once that
event's inferred end time, plus a configurable grace period, has passed.

**Architecture:** A new hide-rule tag, `[UndatedEnded]`, reuses the first-seen record the
plugin already keeps in `/data/event_channel_managarr_undated_first_seen.json`. The rule
builds a start datetime from the first-seen date plus the time parsed out of the channel
name, adds the applicable dummy EPG source's program duration and the grace period, and
hides the channel once the current time passes that end. The date arithmetic and the time
extraction are pure functions in `ecm_parsing.py` so they are unit-testable without a
container; `plugin.py` only resolves the source properties and compares against the clock.

**Tech Stack:** Python 3, pytest, pytz (already a plugin dependency), Django ORM read-only
access to `EPGSource.custom_properties`.

**Spec:** GitHub issue 28 on `PiratesIRC/Dispatcharr-Event-Channel-Managarr-Plugin`, plus
the two maintainer comments on it (2026-08-30T12:46:05Z and 2026-08-30T20:42:51Z) and the
requester's confirmations (2026-08-30T18:39:27Z and 2026-08-30T21:27:27Z).

## Global Constraints

- No em dashes in any plugin-facing copy: settings labels, help text, rendered report text,
  README and documentation pages.
- No contractions in code, comments, docstrings, test names or string literals in source.
  Write "does not", "cannot", "it is". Possessives are not contractions.
- No invisible Unicode characters anywhere. Where one must be matched, write the escape.
- `ecm_parsing.py` is stdlib only. It must not import `apps.*`, `django.*` or `core.utils`,
  and it must hold no module-level mutable state
  (`tests/contract/test_module_purity.py` enforces both).
- Version is calver `Major.YY.DDDHHMM`, bumped only by `bump_version.py`. Do not hand-edit
  a version string.
- The rule must fail open. Any parse failure, missing record or unusable property returns
  "does not match" so the channel stays visible and the existing `[UndatedAge:N]` rule
  decides instead.
- Settings are read from the live `settings` dict passed into the call. Never from
  `self.saved_settings` and never from cached instance state.

---

### Task 1: Pure time extraction and event-window inference

**Files:**
- Modify: `Event-Channel-Managarr/ecm_parsing.py` (append after `short_channel_name_match`,
  currently ending at line 342)
- Test: `tests/unit/test_ecm_parsing.py`

**Interfaces:**
- Consumes: `apply_meridiem(hour, meridiem)` at `ecm_parsing.py:33`, which already converts
  a 12-hour clock hour plus "am"/"pm" into a 24-hour hour.
- Produces:
  - `extract_time_of_day(channel_name, time_pattern=None)` returning `(hour, minute)` as
    integers on a 24-hour clock, or `None` when no time is found. `time_pattern` is a
    pattern string carrying named groups `hour`, optional `minute` and optional `ampm`, in
    either the JavaScript `(?<name>)` or the Python `(?P<name>)` form. When it is `None` or
    does not compile, a built-in default pattern is used.
  - `infer_undated_event_window(first_seen_date, hour, minute, tz_name, duration_minutes, grace_hours)`
    returning `(start, hide_after)` as two timezone-aware `datetime` objects in `tz_name`,
    or `None` when `tz_name` is not a usable zone. `first_seen_date` is a `datetime.date`.

- [x] **Step 1: Write the failing tests**

Append to `tests/unit/test_ecm_parsing.py`:

```python
import datetime

import pytest

import ecm_parsing


@pytest.mark.parametrize("name, expected", [
    ("Boxing 3 : MOSES vs HRGOVIC  4:00pm", (16, 0)),
    ("PPV 07 - 8pm Main Card", (20, 0)),
    ("EVENT 12 | 11:30 AM Coverage", (11, 30)),
    ("PPV 02 - Championship Final", None),
])
def test_extract_time_of_day_reads_the_first_clock_time(name, expected):
    assert ecm_parsing.extract_time_of_day(name) == expected


def test_extract_time_of_day_uses_a_supplied_24_hour_pattern():
    # The SE channel-name format carries a 24-hour clock and no am/pm marker.
    name = "LIVE | GIRONA - REAL SOCIEDAD | Thu 14 May 19:55 CEST (SE)"
    pattern = r"(?<hour>\d{1,2}):(?<minute>\d{2})"
    assert ecm_parsing.extract_time_of_day(name, pattern) == (19, 55)


def test_extract_time_of_day_falls_back_when_the_pattern_does_not_compile():
    assert ecm_parsing.extract_time_of_day("PPV 07 - 8pm Main Card", "(unclosed") == (20, 0)


def test_infer_undated_event_window_adds_duration_and_grace():
    start, hide_after = ecm_parsing.infer_undated_event_window(
        datetime.date(2026, 8, 30), 20, 0, "US/Eastern", 180, 1)
    assert (start.year, start.month, start.day, start.hour) == (2026, 8, 30, 20)
    # 20:00 plus a three hour programme plus one hour of grace is 00:00 the next day.
    assert (hide_after.year, hide_after.month, hide_after.day, hide_after.hour) == (2026, 8, 31, 0)
    assert start.tzinfo is not None and hide_after.tzinfo is not None


def test_infer_undated_event_window_crosses_midnight():
    start, hide_after = ecm_parsing.infer_undated_event_window(
        datetime.date(2026, 8, 30), 22, 30, "US/Eastern", 240, 2)
    assert (hide_after.month, hide_after.day, hide_after.hour, hide_after.minute) == (8, 31, 4, 30)


def test_infer_undated_event_window_rejects_an_unknown_timezone():
    assert ecm_parsing.infer_undated_event_window(
        datetime.date(2026, 8, 30), 20, 0, "Mars/Olympus", 180, 1) is None
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `cd C:/Users/User/docker/Event-Channel-Managarr && py -3 -m pytest tests/unit/test_ecm_parsing.py -q -k "time_of_day or undated_event_window"`
Expected: FAIL with `AttributeError: module 'ecm_parsing' has no attribute 'extract_time_of_day'`.

- [x] **Step 3: Write the implementation**

Append to `Event-Channel-Managarr/ecm_parsing.py`:

```python
# A clock time written the way a US event channel name writes it: an hour, an
# optional :minute, and an am or pm marker. The marker is required here so a bare
# slot number such as the 07 in "PPV 07 - Main Card" is not read as 7 o'clock.
_DEFAULT_TIME_OF_DAY = r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[AaPp][Mm])"

# Converts a JavaScript named group (?<name> into the Python (?P<name> form, while
# leaving a lookbehind (?<= or (?<! alone. Dispatcharr stores its patterns in the
# JavaScript form because its frontend validator rejects the Python one (issue 21).
_JS_NAMED_GROUP_RE = re.compile(r"\(\?<(?![=!])([^>]+)>")


def _compile_time_pattern(time_pattern):
    """Compile a stored time pattern, or return the built-in default. Never raises."""
    for candidate in (time_pattern, _JS_NAMED_GROUP_RE.sub(r"(?P<\1>", time_pattern or "")):
        if not candidate:
            continue
        try:
            return re.compile(candidate)
        except re.error:
            continue
    return re.compile(_DEFAULT_TIME_OF_DAY)


def extract_time_of_day(channel_name, time_pattern=None):
    """Return (hour, minute) on a 24 hour clock from the first clock time in the name.

    Returns None when the name carries no time the pattern can read. The pattern may
    use either the JavaScript (?<name>) or the Python (?P<name>) named-group form and
    is expected to provide an `hour` group plus optional `minute` and `ampm` groups.
    A pattern that does not compile falls back to the built-in default rather than
    raising, because the pattern comes from a user-editable EPG source property.
    """
    if not channel_name:
        return None
    compiled = _compile_time_pattern(time_pattern)
    match = compiled.search(channel_name)
    if match is None and time_pattern:
        match = re.compile(_DEFAULT_TIME_OF_DAY).search(channel_name)
    if match is None:
        return None
    groups = match.groupdict()
    try:
        hour = int(groups.get("hour"))
    except (TypeError, ValueError):
        return None
    try:
        minute = int(groups.get("minute") or 0)
    except (TypeError, ValueError):
        minute = 0
    hour = apply_meridiem(hour, groups.get("ampm"))
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None
    return hour, minute


def infer_undated_event_window(first_seen_date, hour, minute, tz_name,
                               duration_minutes, grace_hours):
    """Build (start, hide_after) for an event whose name carries a time but no date.

    `start` is the first-seen date at the parsed time, read in `tz_name`. `hide_after`
    is that start plus the programme duration plus the grace period, so a caller hides
    the channel once the current time passes it. Returns None when `tz_name` is not a
    zone this installation knows, which leaves the caller no window and therefore no
    hide decision to make.
    """
    import pytz  # local, matching coerce_timezone: the module stays importable without it

    try:
        event_tz = pytz.timezone(str(tz_name).strip())
    except Exception:
        return None
    try:
        naive_start = datetime.combine(
            first_seen_date, time(hour=int(hour), minute=int(minute)))
    except (TypeError, ValueError):
        return None
    start = event_tz.localize(naive_start)
    try:
        duration = int(duration_minutes)
    except (TypeError, ValueError):
        duration = 0
    try:
        grace = int(grace_hours)
    except (TypeError, ValueError):
        grace = 0
    hide_after = start + timedelta(minutes=max(duration, 0), hours=max(grace, 0))
    return start, hide_after
```

Import style, measured in the file: `ecm_parsing.py` imports `logging`, `re` and
`from datetime import datetime`, and it does NOT import `pytz` at module scope. It imports
`pytz` locally inside `coerce_timezone` (line 232) to keep the module importable on a
machine without it. Follow that: widen the existing import line to
`from datetime import date, datetime, time, timedelta`, put `import pytz` INSIDE
`infer_undated_event_window`, and write `datetime.combine`, `time(hour=..., minute=...)`
and `timedelta(...)` rather than the `datetime.datetime.` prefix used in the draft above.

- [x] **Step 4: Run the tests to verify they pass**

Run: `cd C:/Users/User/docker/Event-Channel-Managarr && py -3 -m pytest tests/unit/test_ecm_parsing.py -q`
Expected: PASS, with no test in the file regressing.

- [x] **Step 5: Run the purity contract test**

Run: `py -3 -m pytest tests/contract/test_module_purity.py -q`
Expected: PASS. `_DEFAULT_TIME_OF_DAY` and `_JS_NAMED_GROUP_RE` are immutable module
constants, which that test allows; a dict or list at module level would fail it.

- [x] **Step 6: Commit**

```bash
git add Event-Channel-Managarr/ecm_parsing.py tests/unit/test_ecm_parsing.py
git commit -m "feat: parse a clock time and infer an event window for undated names"
```

---

### Task 2: The [UndatedEnded] hide rule and its grace-period setting

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` : the constants block near line 73, the
  `fields` property near line 400, the `_check_hide_rule` method (add a branch after the
  `UndatedAge` branch, which ends near line 1455), and the CSV settings key list near
  line 3532
- Modify: `Event-Channel-Managarr/plugin.json` : the `fields` array, currently 27 entries
- Test: `tests/contract/test_undated_ended_rule.py` (new file)

**Interfaces:**
- Consumes: `ecm_parsing.extract_time_of_day` and `ecm_parsing.infer_undated_event_window`
  from Task 1; `self._undated_tracker`, the dictionary keyed by channel id holding
  `{"first_seen": "YYYY-MM-DD", "name": "<exact channel name>"}`, loaded at
  `plugin.py:3177`; `self._get_system_timezone(settings)` at `plugin.py:1773`.
- Produces: a `_check_hide_rule` branch for `rule_name == "UndatedEnded"`, and a helper
  `_undated_event_properties(self, channel, settings)` returning
  `(time_pattern, tz_name, duration_minutes)` for one channel.

- [x] **Step 1: Write the failing contract test**

Create `tests/contract/test_undated_ended_rule.py`:

```python
"""The [UndatedEnded] rule must read its grace period from the setting, not a constant.

The rule hides a channel once an inferred event end has passed. If it ignored the
configured grace period the channel would disappear while the event overran, and
nothing would report that the setting had been discarded. plugin.py imports Django
at module scope and cannot be imported outside the container, so its structure is
read with ast.
"""

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _rule_branch(rule_name):
    for node in ast.walk(TREE):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == "rule_name"
                and test.comparators
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == rule_name):
            return "\n".join(ast.unparse(stmt) for stmt in node.body)
    pytest.fail(f"plugin.py has no rule branch for {rule_name}")


def test_the_rule_branch_exists():
    assert _rule_branch("UndatedEnded")


def test_the_rule_reads_the_configured_grace_period():
    branch = _rule_branch("UndatedEnded")
    assert "undated_event_grace_hours" in branch, (
        "the rule must read the grace period from settings")


def test_the_rule_reads_the_first_seen_record():
    branch = _rule_branch("UndatedEnded")
    assert "_undated_tracker" in branch


def test_the_rule_uses_the_shared_window_helper():
    branch = _rule_branch("UndatedEnded")
    assert "infer_undated_event_window" in branch, (
        "the window arithmetic belongs in ecm_parsing so it stays unit-testable")


def test_the_setting_is_declared_in_both_the_code_and_the_manifest():
    assert "undated_event_grace_hours" in SOURCE
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    ids = [field["id"] for field in manifest["fields"]]
    assert "undated_event_grace_hours" in ids, (
        "plugin.json must declare the same field id as plugin.py")


def test_the_default_hide_rules_place_the_new_rule_before_the_day_count_rule():
    defaults = next(
        node.value.value for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "DEFAULT_HIDE_RULES")
    assert "[UndatedEnded]" in defaults
    assert defaults.index("[UndatedEnded]") < defaults.index("[UndatedAge:")
```

- [x] **Step 2: Run the test to verify it fails**

Run: `cd C:/Users/User/docker/Event-Channel-Managarr && py -3 -m pytest tests/contract/test_undated_ended_rule.py -q`
Expected: FAIL, with `plugin.py has no rule branch for UndatedEnded`.

- [x] **Step 3: Add the constant and the settings field**

In `Event-Channel-Managarr/plugin.py`, beside `DEFAULT_PAST_DATE_GRACE_HOURS` near line 73:

```python
    # Default grace period after an undated event's inferred end, in hours. Events
    # overrun, so the channel stays visible for this long past the computed end
    # before [UndatedEnded] hides it.
    DEFAULT_UNDATED_EVENT_GRACE_HOURS = "1"
```

Add `[UndatedEnded]` to `DEFAULT_HIDE_RULES` immediately before `[UndatedAge:2]`, so the
constant near line 67 reads:

```python
    DEFAULT_HIDE_RULES = "[InactiveRegex],[BlankName],[WrongDayOfWeek],[NoEventPattern],[EmptyPlaceholder],[PastDate:0],[FutureDate:2],[UndatedEnded],[UndatedAge:2],[ShortDescription],[ShortChannelName]"
```

In the `fields` property, immediately after the `past_date_grace_hours` entry that ends at
line 405:

```python
            {
                "id": "undated_event_grace_hours",
                "label": "🕒 Undated Event Grace Period (Hours)",
                "type": "number",
                "default": int(self.DEFAULT_UNDATED_EVENT_GRACE_HOURS),
                "help_text": "How many whole hours past an undated event's inferred end a channel stays visible before the [UndatedEnded] rule hides it. The inferred end is the date the channel was first seen, plus the time read from its name, plus the event duration. Raise it for events that overrun.",
            },
```

In `Event-Channel-Managarr/plugin.json`, add the matching entry to the `fields` array in
the same position. The manifest carries the id, label and type only; Dispatcharr reads the
live `fields` property for everything else, so the help text does not need duplicating.

```json
    {
      "id": "undated_event_grace_hours",
      "label": "Undated Event Grace Period (Hours)",
      "type": "number",
      "default": 1
    },
```

Add `"undated_event_grace_hours",` to the `settings_keys` list near line 3532, immediately
after `"past_date_grace_hours",`, so an exported CSV describes the setting that produced it.

- [x] **Step 4: Add the property resolver**

In `Event-Channel-Managarr/plugin.py`, immediately before `_check_hide_rule` (line 1148):

```python
    def _undated_event_properties(self, channel, settings):
        """The time pattern, timezone and duration to use for one undated channel.

        A channel bound to a dummy EPG source is rendered from that source's own
        properties, so an installation running more than one managed source (one per
        provider timezone, for example) must infer the event window from the source the
        channel actually sits on. Anything the source does not supply falls back to the
        plugin settings, which is also the whole answer for a channel that is bound to
        nothing yet.

        Never raises: a missing relation, a missing property or a property of the wrong
        shape falls back rather than failing, because the caller must be able to leave
        the channel visible instead of hiding it on a bad read.
        """
        tz_name = str(settings.get("dummy_epg_event_timezone",
                                   self.DEFAULT_DUMMY_EPG_TIMEZONE)).strip()
        try:
            duration_hours = int(str(settings.get(
                "dummy_epg_event_duration_hours", self.DEFAULT_EVENT_DURATION_HOURS)).strip())
        except (ValueError, TypeError):
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
        if duration_hours <= 0:
            duration_hours = int(self.DEFAULT_EVENT_DURATION_HOURS)
        duration_minutes = duration_hours * 60
        time_pattern = None

        try:
            source = channel.epg_data.epg_source
        except AttributeError:
            source = None
        if source is not None and getattr(source, "source_type", None) == "dummy":
            props = source.custom_properties
            if isinstance(props, dict):
                if props.get("time_pattern"):
                    time_pattern = props["time_pattern"]
                if props.get("timezone"):
                    tz_name = str(props["timezone"]).strip()
                try:
                    from_source = int(props.get("program_duration"))
                    if from_source > 0:
                        duration_minutes = from_source
                except (TypeError, ValueError):
                    pass
        return time_pattern, tz_name, duration_minutes
```

- [x] **Step 5: Add the rule branch**

In `Event-Channel-Managarr/plugin.py`, immediately after the `UndatedAge` branch ends
(the `return False, None` at line 1455) and before the `InactiveRegex` branch:

```python
        elif rule_name == "UndatedEnded":
            # A name with a clock time but no date. [UndatedAge:N] can only count whole
            # calendar days, so it hides a late event at midnight or keeps a finished one
            # until tomorrow. This builds the real window instead: the date the channel
            # was first seen, plus the time in the name, plus the programme duration and
            # the configured grace period.
            #
            # Fails open at every step. A channel with no record, no parseable time or an
            # unusable timezone returns no match, which leaves it visible and lets
            # [UndatedAge:N] later in the rule list make the decision it makes today.
            tracker = getattr(self, '_undated_tracker', None) or {}
            entry = tracker.get(str(channel.id))
            if not entry:
                return False, None
            try:
                first_seen = datetime.strptime(entry['first_seen'], '%Y-%m-%d').date()
            except (KeyError, ValueError, TypeError):
                return False, None

            time_pattern, tz_name, duration_minutes = self._undated_event_properties(
                channel, settings)
            parsed_time = ecm_parsing.extract_time_of_day(channel_name, time_pattern)
            if parsed_time is None:
                return False, None

            # The tag may carry its own hour count, as [UndatedEnded:2]. Without one the
            # rule reads the setting, which is what the requester asked for in issue 28.
            if isinstance(rule_param, tuple):
                grace_hours = rule_param[0]
            elif rule_param is not None:
                grace_hours = rule_param
            else:
                try:
                    grace_hours = int(str(settings.get(
                        "undated_event_grace_hours",
                        self.DEFAULT_UNDATED_EVENT_GRACE_HOURS)).strip())
                except (ValueError, TypeError):
                    grace_hours = int(self.DEFAULT_UNDATED_EVENT_GRACE_HOURS)

            window = ecm_parsing.infer_undated_event_window(
                first_seen, parsed_time[0], parsed_time[1], tz_name,
                duration_minutes, grace_hours)
            if window is None:
                return False, None
            start, hide_after = window

            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                local_tz = pytz.timezone(self.DEFAULT_TIMEZONE)
            if datetime.now(local_tz) > hide_after:
                return True, (
                    f"[UndatedEnded] No date in name; first seen {first_seen.isoformat()}, "
                    f"inferred start {start.strftime('%m/%d %I:%M %p %Z')} "
                    f"(+{duration_minutes // 60}h duration, {grace_hours}h grace)")
            return False, None
```

Confirm the names used here resolve in `plugin.py`: `datetime` and `pytz` are already
imported at module scope and `ecm_parsing` is already imported, because the `PastDate`
branch near line 1343 uses all three the same way. Do not add imports if they are present.

- [x] **Step 6: Run the contract tests to verify they pass**

Run: `cd C:/Users/User/docker/Event-Channel-Managarr && py -3 -m pytest tests/contract -q`
Expected: PASS, including the new file and the existing manifest, purity and constant tests.

- [x] **Step 7: Prove the new rule can actually fire, rather than only parsing**

The contract tests read structure, not behaviour. Write a throwaway script under the
scratchpad directory that imports `ecm_parsing` alone and asserts the two boundaries:

```python
import datetime
import ecm_parsing

parsed = ecm_parsing.extract_time_of_day("Boxing 3 : MOSES vs HRGOVIC  4:00pm")
start, hide_after = ecm_parsing.infer_undated_event_window(
    datetime.date(2026, 8, 30), parsed[0], parsed[1], "US/Eastern", 180, 1)
print("start", start, "hide after", hide_after)
assert hide_after.hour == 20 and hide_after.day == 30, hide_after
print("OK")
```

Run it with `py -3` from the repository root so `pyproject.toml` supplies the path.
Expected: a printed window ending at 20:00 on 30 August, then `OK`. A rule that passes its
structural tests while computing the wrong window would print a different hour here.

- [x] **Step 8: Run the whole suite and the linter**

Run: `py -3 -m pytest -q` then `py -3 -m ruff check .` (ruff 0.15.16 is installed as a module)
Expected: PASS on both. Fix any line-length or unused-import finding before committing.

- [x] **Step 9: Commit**

```bash
git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json tests/contract/test_undated_ended_rule.py
git commit -m "feat: add the [UndatedEnded] hide rule with a configurable grace period"
```

---

### Task 3: Documentation

**Files:**
- Modify: `docs/USER-GUIDE.md` : the hide-rule tag reference and the settings reference
- Modify: `Event-Channel-Managarr/plugin.py` : the `hide_rules_priority` help text near
  line 373, which lists every available tag
- Modify: `README.md` : the features list
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: the tag name `[UndatedEnded]` and the setting id `undated_event_grace_hours`
  from Task 2. Both spellings must match exactly.

- [x] **Step 1: Add the tag to the in-product help text**

In `Event-Channel-Managarr/plugin.py`, the `help_text` of `hide_rules_priority` near line
373 lists the available tags. Add `[UndatedEnded]` and `[UndatedEnded:hours]` to that list,
between `[UndatedAge:days]` and `[InactiveRegex]`. Keep it one flowing paragraph with no
em dashes, matching the surrounding copy.

- [x] **Step 2: Document the rule and the setting in the user guide**

In `docs/USER-GUIDE.md`, add a row to the hide-rule table and a row to the settings table.
The rule description must state all four facts a user needs, because each one has already
been asked about on the issue tracker:

- it applies only to a name that carries a clock time and no date
- the window is the first-seen date, plus the time in the name, plus the event duration
  from the applicable dummy EPG source, plus the grace period
- it controls visibility only. Dispatcharr treats a dateless time as a programme that
  recurs daily, so the repeated guide entry stops when the channel is hidden, not before
- the hide happens on the next scan, so a scheduled time shortly after the latest event
  ends is worth configuring

Also state that `[UndatedAge:N]` should stay in the rule list after it, as the outer bound
for a channel whose first-seen record was rebuilt late, and that an existing installation
keeps its stored hide-rule list and must add the new tag by hand, because Dispatcharr never
prunes or rewrites a stored setting.

- [x] **Step 3: Add the feature to the README list**

In `README.md`, add one line to the features list naming the rule and what it does. Keep it
to one line: that list is where a removed capability survived longest last time, so it earns
no detail it does not need.

- [x] **Step 4: Verify every internal link still resolves**

Run a link check across the documentation set rather than reading the links:

```bash
py -3 - <<'PY'
import pathlib, re
root = pathlib.Path(".")
for md in ["README.md", "docs/README.md", "docs/USER-GUIDE.md", "docs/DEVELOPMENT.md", "docs/CHANGELOG.md"]:
    text = pathlib.Path(md).read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?:)([^)#]+)", text):
        resolved = (pathlib.Path(md).parent / target).resolve()
        if not resolved.exists():
            print("BROKEN", md, target)
print("checked")
PY
```

Expected: `checked` with no `BROKEN` line.

- [x] **Step 5: Commit**

```bash
git add README.md docs/USER-GUIDE.md docs/CHANGELOG.md Event-Channel-Managarr/plugin.py
git commit -m "docs: document the [UndatedEnded] rule and its grace period setting"
```

---

### Task 4: Version bump and pre-release verification

**Files:**
- Modify: `Event-Channel-Managarr/plugin.py` and `Event-Channel-Managarr/plugin.json`
  (both via `bump_version.py`, never by hand)

**Interfaces:**
- Consumes: everything from Tasks 1 to 3.
- Produces: a committed tree that is ready for the operator to decide whether to release.
  This task stops before tagging, releasing or deploying.

- [x] **Step 1: Bump the version**

Run: `cd C:/Users/User/docker/Event-Channel-Managarr && py -3 bump_version.py`
Expected: `plugin.py` and `plugin.json` both carry the same new calver string.

- [x] **Step 2: Verify the two version strings agree**

This repository has no `scripts/check_version_sync.py`; that script belongs to the
Stream-Mapparr template. The version parity check here is a contract test.

Run: `py -3 -m pytest tests/contract/test_manifest_parity.py -q`
Expected: PASS. It asserts `PLUGIN_VERSION` in `plugin.py` equals `version` in
`plugin.json`.

- [x] **Step 3: Run the full suite and the linter one more time**

Run: `py -3 -m pytest -q` then `py -3 -m ruff check .` (ruff 0.15.16 is installed as a module)
Expected: PASS on both.

- [x] **Step 4: Run the publish audit**

Run:

```bash
py -3 ../.claude/skills/pre-publish-audit/audit_publish.py --worktree --rules .publish-audit.json
```

Expected: exit 0. This is required before any push, and again before cutting a release.

- [x] **Step 5: Commit**

```bash
git add Event-Channel-Managarr/plugin.py Event-Channel-Managarr/plugin.json
git commit -m "chore: bump version for the [UndatedEnded] hide rule"
```

- [x] **Step 6: Stop and report**

Report to the operator: the new version string, the test and lint results, the audit
result, and that nothing has been pushed, tagged, released or deployed. Pushing, releasing,
listing on the Plugin Hub and deploying into the container are all separate operator
decisions, and the deploy also needs the container question answered, because a plugin
reload requires either the Plugins-page refresh control or a restart, and a restart needs
the operator's approval every time.
