"""The run ledger counts only channels whose visibility actually changes.

The scan hands _handle_duplicates two lists, and the run ledger records the length of both
after the transaction commits. A duplicate that is already hidden must not be listed for
hiding, or an applied run that changes nothing writes a line. The scan needs the Django ORM,
so these tests call _handle_duplicates directly with the dicts the scan builds for it, and
two end-to-end tests run the real applied _scan_and_update_channels against an in-memory
stand-in for the ORM.
"""

import json
import logging
import threading
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace

import pytest

LOGGER = logging.getLogger("test.ledger.real_changes")


@pytest.fixture
def plugin(bare_plugin):
    return bare_plugin


def _entry(channel_id, name, number, current_visible):
    return {"channel_id": channel_id, "channel_name": name, "channel_number": number,
            "current_visible": current_visible}


def _run(plugin, entries, to_show=()):
    """One scan's duplicate pass. Returns (channels_to_hide, channels_to_show) after it."""
    to_hide, show = [], list(to_show)
    plugin._handle_duplicates(entries, to_hide, show, LOGGER, strategy="lowest_number")
    return to_hide, show


def test_an_already_hidden_duplicate_loser_is_not_listed_for_hiding(plugin):
    """Two applied scans: the loser is hidden and its rules say visible. Neither run changes it."""
    for _ in range(2):
        to_hide, show = _run(
            plugin,
            [_entry(1, "Event A", 1, True), _entry(2, "Event A", 2, False)],
            to_show=[2],
        )
        assert to_hide == [], "a hidden loser was counted as a change"
        assert show == [], "the hidden loser is still in the show list"


def test_a_visible_duplicate_loser_is_listed_once_then_not_again(plugin):
    """Run one hides the visible loser. Run two finds it hidden and lists nothing."""
    to_hide, show = _run(plugin, [_entry(1, "Event A", 1, True), _entry(2, "Event A", 2, True)])
    assert to_hide == [2]
    assert show == []
    to_hide, show = _run(plugin, [_entry(1, "Event A", 1, True), _entry(2, "Event A", 2, False)], to_show=[2])
    assert to_hide == []
    assert show == []


def test_a_single_channel_that_is_already_right_is_not_listed(plugin):
    """Control: nothing to change, so nothing is listed."""
    to_hide, show = _run(plugin, [_entry(1, "Event A", 1, True)])
    assert to_hide == []
    assert show == []


# ---------------------------------------------------------------------------
# End to end: the real applied scan, twice, against an in-memory ORM stand-in
# ---------------------------------------------------------------------------

class _Rows:
    """A queryset stand-in: filter, select_related, iteration, first and update."""

    def __init__(self, rows):
        self.rows = list(rows)

    def select_related(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def update(self, **fields):
        for row in self.rows:
            for name, value in fields.items():
                setattr(row, name, value)
        return len(self.rows)


class _Manager:
    """Objects manager stand-in over a list. Supports exact and __in lookups."""

    def __init__(self, store):
        self._store = store

    def filter(self, **lookups):
        matched = []
        for row in self._store:
            ok = True
            for key, wanted in lookups.items():
                if key.endswith("__isnull"):
                    if (getattr(row, key[:-8]) is None) != wanted:
                        ok = False
                        break
                elif key.endswith("__in"):
                    if getattr(row, key[:-4]) not in wanted:
                        ok = False
                        break
                elif getattr(row, key) != wanted:
                    ok = False
                    break
            if ok:
                matched.append(row)
        return _Rows(matched)

    def bulk_update(self, objs, fields):
        """The rows are mutated in place by the caller, so there is nothing to write back."""
        return len(objs)


def _profile_manager(store):
    class _DoesNotExist(Exception):
        pass

    class _ProfileManager(_Manager):
        def get(self, **lookups):
            name = lookups["name__iexact"].casefold()
            for row in self._store:
                if row.name.casefold() == name:
                    return row
            raise _DoesNotExist(name)

    manager = _ProfileManager(store)
    return SimpleNamespace(objects=manager, DoesNotExist=_DoesNotExist)


@pytest.fixture
def ledger_world(plugin_module, bare_plugin, monkeypatch, tmp_path):
    """The shipped scan, wired to the same fixture the container run used.

    Two channels named "Sports Event Alpha" in one group and one profile. Channel 1 is
    number 5 and visible (the keeper under lowest_number). Channel 2 is number 9. The
    caller chooses whether channel 2 starts hidden or visible.
    """
    group = SimpleNamespace(name="Sports")
    profile = SimpleNamespace(id=1, name="Test Profile")
    channels = [
        SimpleNamespace(id=1, name="Sports Event Alpha", channel_number=5, channel_group=group,
                        epg_data=None, override=None),
        SimpleNamespace(id=2, name="Sports Event Alpha", channel_number=9, channel_group=group,
                        epg_data=None, override=None),
    ]
    memberships = []

    def set_loser_state(loser_enabled):
        memberships[:] = [
            SimpleNamespace(channel_id=1, channel_profile_id=1, enabled=True, channel=channels[0]),
            SimpleNamespace(channel_id=2, channel_profile_id=1, enabled=loser_enabled, channel=channels[1]),
        ]

    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(plugin_module.PluginConfig, "LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr(plugin_module.PluginConfig, "UNDATED_FIRST_SEEN_FILE", str(tmp_path / "undated.json"))
    # conftest stubs django.utils.timezone as a MagicMock, so timezone.now() would make the
    # ledger json.dumps fail and the writer would swallow it. Use a real clock.
    monkeypatch.setattr(plugin_module, "timezone", SimpleNamespace(
        now=lambda: datetime.now(dt_timezone.utc)))
    monkeypatch.setattr(plugin_module, "Channel", SimpleNamespace(objects=_Manager(channels)))
    monkeypatch.setattr(plugin_module, "ChannelProfile", _profile_manager([profile]))
    monkeypatch.setattr(plugin_module, "ChannelProfileMembership",
                        SimpleNamespace(objects=_Manager(memberships)))

    instance = bare_plugin
    instance.version = "test"
    instance.results_file = str(tmp_path / "results.json")
    instance.last_results = []
    instance._op_stop_event = threading.Event()
    monkeypatch.setattr(instance, "_dispatcharr_timezone", lambda: "UTC")
    monkeypatch.setattr(instance, "_load_undated_tracker", lambda logger: {})
    monkeypatch.setattr(instance, "_save_undated_tracker", lambda tracker, logger: True)
    monkeypatch.setattr(instance, "_run_managed_epg_pass", lambda *a, **k: ([], []))
    monkeypatch.setattr(instance, "_export_csv", lambda *a, **k: None)
    monkeypatch.setattr(instance, "_save_settings", lambda settings: None)

    settings = {
        "channel_profile_name": "Test Profile",
        "hide_rules_priority": "[BlankName]",
        "duplicate_strategy": "lowest_number",
        "keep_duplicates": False,
        "auto_set_dummy_epg_on_hide": True,
    }

    def scan():
        return instance._scan_and_update_channels(
            settings, LOGGER, dry_run=False, is_scheduled_run=False)

    def ledger_lines():
        if not ledger_file.exists():
            return []
        return [json.loads(line) for line in ledger_file.read_text(encoding="utf-8").splitlines() if line]

    def enabled():
        return {m.channel_id: m.enabled for m in memberships}

    return SimpleNamespace(set_loser_state=set_loser_state, scan=scan,
                           ledger_lines=ledger_lines, enabled=enabled,
                           channels=channels, memberships=memberships)


def test_scan_loser_already_hidden_writes_no_ledger_line_on_either_run(ledger_world):
    """The defect as measured: the loser is hidden, its rules say visible, and two applied
    scans must change nothing and write nothing."""
    ledger_world.set_loser_state(loser_enabled=False)

    first = ledger_world.scan()
    assert first["status"] == "success", first
    assert ledger_world.ledger_lines() == [], "run 1 wrote a ledger line for a no-op"
    assert ledger_world.enabled() == {1: True, 2: False}

    second = ledger_world.scan()
    assert second["status"] == "success", second
    assert ledger_world.ledger_lines() == [], "run 2 wrote a ledger line for a no-op"
    assert ledger_world.enabled() == {1: True, 2: False}


def test_scan_loser_starts_visible_is_hidden_once_then_writes_nothing(ledger_world):
    """Run 1 really hides the visible loser and writes one line. Run 2 changes nothing."""
    ledger_world.set_loser_state(loser_enabled=True)

    first = ledger_world.scan()
    assert first["status"] == "success", first
    lines = ledger_world.ledger_lines()
    assert len(lines) == 1, lines
    # The timestamp is a real clock reading, so check that it parses rather than its value.
    record = dict(lines[0])
    datetime.fromisoformat(record.pop("ts"))
    assert record == {"version": "test", "shown": 0, "hidden": 1, "scheduled": False}
    assert ledger_world.enabled() == {1: True, 2: False}

    second = ledger_world.scan()
    assert second["status"] == "success", second
    assert len(ledger_world.ledger_lines()) == 1, "run 2 wrote a second ledger line for a no-op"
    assert ledger_world.enabled() == {1: True, 2: False}


def test_scan_already_hidden_duplicate_keeps_its_epg(ledger_world):
    """Auto-Remove EPG clears channels this run hides. An already-hidden loser is not one of
    them, so its guide link is left exactly as it was."""
    ledger_world.set_loser_state(loser_enabled=False)
    epg = SimpleNamespace(id=7, name="Guide")
    ledger_world.channels[1].epg_data = epg

    result = ledger_world.scan()
    assert result["status"] == "success", result
    assert ledger_world.channels[1].epg_data is epg, "an already-hidden loser lost its EPG"
    assert ledger_world.enabled() == {1: True, 2: False}


def test_scan_visible_duplicate_loser_is_hidden_and_loses_its_epg(ledger_world):
    """Control for the case above: a loser this run hides has its EPG cleared."""
    ledger_world.set_loser_state(loser_enabled=True)
    ledger_world.channels[1].epg_data = SimpleNamespace(id=7, name="Guide")

    result = ledger_world.scan()
    assert result["status"] == "success", result
    assert ledger_world.enabled() == {1: True, 2: False}
    assert ledger_world.channels[1].epg_data is None
