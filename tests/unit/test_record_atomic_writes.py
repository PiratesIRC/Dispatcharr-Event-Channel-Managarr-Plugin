"""The undated tracker and the group source record are written through the storage layer's
atomic write (temp file, fsync, os.replace), not a bare ".tmp" file that is never fsynced.
"""
import json
import os


class Log:
    def __init__(self):
        self.errors = []

    def error(self, *a):
        self.errors.append(a)

    def warning(self, *a):
        pass

    def info(self, *a):
        pass


def _plugin(module):
    return module.Plugin.__new__(module.Plugin)


def _spy_writes(module, monkeypatch):
    """Record every path the storage layer writes atomically, and every fsync."""
    writes, fsyncs = [], []
    real_bytes = module.storage._ps.atomic_write_bytes
    real_fsync = os.fsync

    def spy_bytes(path, data):
        writes.append(path)
        return real_bytes(path, data)

    def spy_fsync(fd):
        fsyncs.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(module.storage._ps, "atomic_write_bytes", spy_bytes)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    return writes, fsyncs


def test_undated_tracker_is_saved_through_the_atomic_write(plugin_module, tmp_path, monkeypatch):
    target = tmp_path / "undated_first_seen.json"
    monkeypatch.setattr(plugin_module.PluginConfig, "UNDATED_FIRST_SEEN_FILE", str(target))
    writes, fsyncs = _spy_writes(plugin_module, monkeypatch)
    log = Log()
    tracker = {"ch-1": "2026-11-26"}
    assert _plugin(plugin_module)._save_undated_tracker(tracker, log) is True
    assert writes == [str(target)]
    assert fsyncs, "the record must be fsynced before it is renamed into place"
    assert json.loads(target.read_text(encoding="utf-8")) == tracker
    assert not os.path.exists(str(target) + ".tmp"), "no bare .tmp file is left beside it"


def test_group_source_record_is_saved_through_the_atomic_write(plugin_module, tmp_path, monkeypatch):
    target = tmp_path / "group_sources.json"
    monkeypatch.setattr(plugin_module.PluginConfig, "GROUP_SOURCE_RECORD_FILE", str(target))
    writes, fsyncs = _spy_writes(plugin_module, monkeypatch)
    log = Log()
    record = {"US: NFL": {"created": "2026-09-02"}}
    assert _plugin(plugin_module)._save_group_source_record(record, log) is True
    assert writes == [str(target)]
    assert fsyncs
    assert json.loads(target.read_text(encoding="utf-8")) == record
    assert not os.path.exists(str(target) + ".tmp")


def test_a_failed_write_is_logged_and_reported_false(plugin_module, tmp_path, monkeypatch):
    target = tmp_path / "undated_first_seen.json"
    monkeypatch.setattr(plugin_module.PluginConfig, "UNDATED_FIRST_SEEN_FILE", str(target))

    def refuse(path, data):
        raise OSError("read-only")

    monkeypatch.setattr(plugin_module.storage._ps, "atomic_write_bytes", refuse)
    log = Log()
    assert _plugin(plugin_module)._save_undated_tracker({}, log) is False
    assert log.errors, "a failed save is logged"
