"""Rollout safety: old and new workers run side by side for one release.

storage.ROLLOUT_DUAL_PATHS is True in this release. While it is, a worker on the new code also
honours the old /data files that workers on the previous release still use: the scheduler's
last-run record (the slot claim), the scan lock (flock) and the ledger tail. The claim reads both
records, writes both, and the scan takes both locks, old first, so an old-code worker's fresh claim
or held lock stops a new-code run.

The flock tests run only where fcntl exists (Linux, the CI and container). Windows skips them, so
a green Windows run proves nothing about locks. The flag and this file's dual-path tests are removed
in the release after next.
"""
import datetime
import json
import logging
import os
import sys
import types

import pytest

DAY = "2026-11-25"
SLOT = "05:05"
KEY = "event-channel-managarr"
LOG = logging.getLogger("test.storage.rollout")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX only")


class Log:
    def __init__(self):
        self.infos, self.warnings, self.errors = [], [], []

    def info(self, *a):
        self.infos.append(a)

    def debug(self, *a):
        pass

    def warning(self, *a):
        self.warnings.append(a)

    def error(self, *a):
        self.errors.append(a)


class FakeUsage:
    def __init__(self):
        self.calls = []

    def report(self, settings=None, logger=None, force=False):
        self.calls.append(force)


@pytest.fixture
def rollout(plugin_module, tmp_path, monkeypatch):
    """Old /data files in tmp/old; the new, beside-the-data-dir files in tmp/plugins/_data."""
    cfg = plugin_module.PluginConfig
    storage = plugin_module.storage
    old = tmp_path / "old"
    old.mkdir()
    plugins = tmp_path / "plugins"
    data = plugins / "_data" / KEY
    data.mkdir(parents=True)
    legacy = dict(cfg.LEGACY_PATHS)
    legacy.update({
        "LAST_RUN_FILE": str(old / "last_run.json"),
        "SCAN_LOCK_FILE": str(old / "scan.lock"),
        "LEDGER_FILE": str(old / "ledger.jsonl"),
    })
    monkeypatch.setattr(cfg, "LEGACY_PATHS", legacy)
    layout = storage.Layout(False, str(data), str(plugins), KEY, "test")
    monkeypatch.setattr(cfg, "STORAGE_LAYOUT", layout)
    monkeypatch.setattr(cfg, "LAST_RUN_FILE", os.path.join(str(plugins), "_data", KEY + ".last_run.json"))
    monkeypatch.setattr(cfg, "SCAN_LOCK_FILE", os.path.join(str(plugins), "_data", KEY + ".scan.lock"))
    monkeypatch.setattr(cfg, "LEDGER_FILE", str(data / "ledger.jsonl"))
    return {"cfg": cfg, "plugin": plugin_module, "storage": storage, "old": old, "data": data,
            "plugins": plugins, "layout": layout, "log": Log()}


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _old(r):
    return r["cfg"].LEGACY_PATHS["LAST_RUN_FILE"]


def _lock_old(r):
    return r["cfg"].LEGACY_PATHS["SCAN_LOCK_FILE"]


def _flock_free(path):
    """True when a fresh descriptor can take the flock on path right now."""
    import fcntl
    with open(path, "a") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return True


def _plugin(r):
    return r["plugin"].Plugin.__new__(r["plugin"].Plugin)


# ---------------------------------------------------------------------------
# The slot claim: one merged view, written to both records
# ---------------------------------------------------------------------------


def test_an_old_record_claiming_todays_slot_is_seen_by_the_new_code(rollout):
    _write(_old(rollout), {SLOT: DAY})
    merged = rollout["plugin"]._read_last_run()
    assert merged.get(SLOT) == DAY, "a slot claimed on the previous release must count as ran"


def test_the_merged_view_keeps_the_later_date_per_slot(rollout):
    _write(_old(rollout), {SLOT: "2026-11-24", "06:00": DAY})
    _write(rollout["cfg"].LAST_RUN_FILE, {SLOT: DAY})
    merged = rollout["plugin"]._read_last_run()
    assert merged == {SLOT: DAY, "06:00": DAY}


def test_a_claim_is_written_to_both_records(rollout):
    rollout["plugin"]._write_last_run({SLOT: DAY})
    assert _read(_old(rollout)) == {SLOT: DAY}
    assert _read(rollout["cfg"].LAST_RUN_FILE) == {SLOT: DAY}


def test_a_corrupt_old_record_is_ignored_not_fatal(rollout):
    with open(_old(rollout), "w") as fh:
        fh.write("{not json")
    _write(rollout["cfg"].LAST_RUN_FILE, {SLOT: DAY})
    assert rollout["plugin"]._read_last_run() == {SLOT: DAY}


def test_the_flag_off_reads_only_the_new_record(rollout, monkeypatch):
    monkeypatch.setattr(rollout["storage"], "ROLLOUT_DUAL_PATHS", False)
    _write(_old(rollout), {SLOT: DAY})
    assert rollout["plugin"]._read_last_run() == {}
    assert rollout["plugin"]._state_paths("LAST_RUN_FILE") == [rollout["cfg"].LAST_RUN_FILE]


def test_a_legacy_process_uses_one_record(rollout, monkeypatch):
    cfg = rollout["cfg"]
    legacy_layout = rollout["storage"].Layout(True, None, None, KEY, "test")
    monkeypatch.setattr(cfg, "STORAGE_LAYOUT", legacy_layout)
    monkeypatch.setattr(cfg, "LAST_RUN_FILE", _old(rollout))
    assert rollout["plugin"]._state_paths("LAST_RUN_FILE") == [_old(rollout)]
    _write(_old(rollout), {SLOT: DAY})
    assert rollout["plugin"]._read_last_run() == {SLOT: DAY}


# ---------------------------------------------------------------------------
# The scan lock: both flocks, old first
# ---------------------------------------------------------------------------


@posix_only
def test_a_scan_holding_the_old_lock_stops_the_new_scan(rollout):
    import fcntl
    holder = open(_lock_old(rollout), "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _plugin(rollout)._acquire_scan_lock(LOG) is None
        assert _flock_free(rollout["cfg"].SCAN_LOCK_FILE), "a refused scan must not keep the new lock"
    finally:
        holder.close()


@posix_only
def test_a_scan_holding_the_new_lock_stops_the_new_scan(rollout):
    import fcntl
    holder = open(rollout["cfg"].SCAN_LOCK_FILE, "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _plugin(rollout)._acquire_scan_lock(LOG) is None
        assert _flock_free(_lock_old(rollout)), "a refused scan must release the old lock it took"
    finally:
        holder.close()


@posix_only
def test_both_locks_are_taken_old_first_and_released_on_close(rollout, monkeypatch):
    plugin = rollout["plugin"]
    order = []
    real = plugin.Plugin._acquire_one_scan_lock

    def record(self, path, logger):
        order.append(path)
        return real(self, path, logger)

    monkeypatch.setattr(plugin.Plugin, "_acquire_one_scan_lock", record)
    holder = _plugin(rollout)._acquire_scan_lock(LOG)
    assert holder is not None
    assert order == [_lock_old(rollout), rollout["cfg"].SCAN_LOCK_FILE]
    assert holder.fileno() == holder.handles[-1].fileno()
    assert not _flock_free(_lock_old(rollout)) and not _flock_free(rollout["cfg"].SCAN_LOCK_FILE)
    holder.close()
    assert _flock_free(_lock_old(rollout)) and _flock_free(rollout["cfg"].SCAN_LOCK_FILE)


@posix_only
def test_a_second_scan_is_refused_while_the_first_holds_both(rollout):
    plugin = _plugin(rollout)
    first = plugin._acquire_scan_lock(LOG)
    try:
        assert first is not None
        assert plugin._acquire_scan_lock(LOG) is None
    finally:
        first.close()


def test_the_scan_lock_probe_reads_both_paths(rollout):
    """The status action probes every path the scan takes, so an old holder shows as HELD."""
    source = open(rollout["plugin"].__file__, encoding="utf-8").read()
    start = source.index("def check_scheduler_status_action")
    end = source.index("return {", start)
    body = source[start:end]
    assert '_state_paths("SCAN_LOCK_FILE")' in body


# ---------------------------------------------------------------------------
# The ledger: the tail is copied before each new line
# ---------------------------------------------------------------------------


def test_a_ledger_line_lands_after_the_old_tail_in_order(rollout, monkeypatch):
    plugin = rollout["plugin"]
    cfg = rollout["cfg"]
    storage = rollout["storage"]
    with open(_old_ledger(rollout), "wb") as fh:
        fh.write(b'{"shown": 1}\n')
    items = storage.items_for(cfg, rollout["layout"])
    storage.migrate(rollout["layout"], items, rollout["log"], version="t")
    with open(_old_ledger(rollout), "ab") as fh:
        fh.write(b'{"shown": 2}\n')
    stamp = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(plugin, "timezone", types.SimpleNamespace(now=lambda: stamp))
    fake = FakeUsage()
    monkeypatch.setattr(plugin, "USAGE", fake)
    _plugin(rollout)._append_ledger_entry(shown=1, hidden=0, is_scheduled_run=False, logger=LOG)
    lines = open(cfg.LEDGER_FILE, "rb").read().splitlines()
    assert [json.loads(x)["shown"] for x in lines] == [1, 2, 1]
    assert fake.calls == [True]


def _old_ledger(r):
    return r["cfg"].LEGACY_PATHS["LEDGER_FILE"]


# ---------------------------------------------------------------------------
# The settings cache: the scheduler is armed from the newer of the two files
# ---------------------------------------------------------------------------


def _settings_load(r, monkeypatch):
    """Run _load_settings against the old and new settings files; return (plugin, armed, log)."""
    cfg = r["cfg"]
    log = Log()
    monkeypatch.setattr(r["plugin"], "LOGGER", log)
    monkeypatch.setattr(cfg, "SETTINGS_FILE", str(r["data"] / "settings.json"))
    monkeypatch.setitem(cfg.LEGACY_PATHS, "SETTINGS_FILE", str(r["old"] / "settings.json"))
    armed = []
    plugin = _plugin(r)
    plugin.settings_file = cfg.SETTINGS_FILE
    plugin._start_background_scheduler = lambda settings: armed.append(dict(settings))
    plugin._load_settings()
    return plugin, armed, log


def _set_mtime(path, when):
    os.utime(path, (when, when))


def test_the_scheduler_is_armed_from_the_new_settings_file(rollout, monkeypatch):
    _write(rollout["data"] / "settings.json", {"scheduled_times": "0300"})
    plugin, armed, _log = _settings_load(rollout, monkeypatch)
    assert armed == [{"scheduled_times": "0300"}]
    assert plugin.saved_settings == {"scheduled_times": "0300"}


def test_a_failed_copy_still_arms_the_scheduler_from_the_old_file(rollout, monkeypatch):
    # The carry-over did not copy the file, so only the old /data file exists.
    _write(rollout["old"] / "settings.json", {"scheduled_times": "0400"})
    _plugin_obj, armed, log = _settings_load(rollout, monkeypatch)
    assert armed == [{"scheduled_times": "0400"}]
    assert log.warnings == []


def test_an_old_code_save_newer_than_the_copy_wins(rollout, monkeypatch):
    _write(rollout["data"] / "settings.json", {"scheduled_times": "0300"})
    _write(rollout["old"] / "settings.json", {"scheduled_times": "0500"})
    _set_mtime(rollout["data"] / "settings.json", 1_000_000)
    _set_mtime(rollout["old"] / "settings.json", 2_000_000)
    _plugin_obj, armed, _log = _settings_load(rollout, monkeypatch)
    assert armed == [{"scheduled_times": "0500"}]


def test_the_new_file_wins_when_it_is_newer(rollout, monkeypatch):
    _write(rollout["data"] / "settings.json", {"scheduled_times": "0300"})
    _write(rollout["old"] / "settings.json", {"scheduled_times": "0500"})
    _set_mtime(rollout["data"] / "settings.json", 2_000_000)
    _set_mtime(rollout["old"] / "settings.json", 1_000_000)
    _plugin_obj, armed, _log = _settings_load(rollout, monkeypatch)
    assert armed == [{"scheduled_times": "0300"}]


def test_neither_file_warns_once_and_arms_nothing(rollout, monkeypatch):
    _plugin_obj, armed, log = _settings_load(rollout, monkeypatch)
    assert armed == []
    assert len(log.warnings) == 1
    assert "not armed" in log.warnings[0][0]


def test_the_warning_and_log_never_carry_the_settings_contents(rollout, monkeypatch):
    _write(rollout["old"] / "settings.json", {"admin_password": "SECRET-VALUE-123"})
    _plugin_obj, _armed, log = _settings_load(rollout, monkeypatch)
    text = repr(log.infos) + repr(log.warnings) + repr(log.errors)
    assert "SECRET-VALUE-123" not in text


def test_with_the_rollout_off_only_the_new_settings_file_arms_the_scheduler(rollout, monkeypatch):
    monkeypatch.setattr(rollout["storage"], "ROLLOUT_DUAL_PATHS", False)
    _write(rollout["old"] / "settings.json", {"scheduled_times": "0400"})
    _plugin_obj, armed, _log = _settings_load(rollout, monkeypatch)
    assert armed == []


def _point_settings(r, monkeypatch):
    cfg = r["cfg"]
    monkeypatch.setattr(cfg, "SETTINGS_FILE", str(r["data"] / "settings.json"))
    monkeypatch.setitem(cfg.LEGACY_PATHS, "SETTINGS_FILE", str(r["old"] / "settings.json"))
    plugin = _plugin(r)
    plugin.settings_file = cfg.SETTINGS_FILE
    return plugin


def test_the_scheduled_run_reads_the_newer_old_code_save(rollout, monkeypatch):
    plugin = _point_settings(rollout, monkeypatch)
    _write(rollout["data"] / "settings.json", {"enable_scheduled_csv_export": False})
    _write(rollout["old"] / "settings.json", {"enable_scheduled_csv_export": True})
    _set_mtime(rollout["data"] / "settings.json", 1_000_000)
    _set_mtime(rollout["old"] / "settings.json", 2_000_000)
    settings, source = plugin._scheduled_settings({"enable_scheduled_csv_export": "memory"})
    assert settings == {"enable_scheduled_csv_export": True}
    assert source == str(rollout["old"] / "settings.json")


def test_the_scheduled_run_falls_back_to_memory_when_no_file_exists(rollout, monkeypatch):
    plugin = _point_settings(rollout, monkeypatch)
    fallback = {"enable_scheduled_csv_export": "memory"}
    assert plugin._scheduled_settings(fallback) == (fallback, None)


def test_constructing_the_plugin_twice_warns_once(rollout, monkeypatch):
    monkeypatch.setattr(rollout["plugin"], "_NEITHER_SETTINGS_WARNED", False)
    _plugin_obj, _armed, first = _settings_load(rollout, monkeypatch)
    _plugin_obj, _armed, second = _settings_load(rollout, monkeypatch)
    assert len(first.warnings) + len(second.warnings) == 1, "one WARNING for two constructions"
