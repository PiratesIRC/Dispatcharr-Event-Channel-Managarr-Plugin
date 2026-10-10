"""Event Channel Managarr reports its Visibility Changes total through the vendored usage client.

Report sites: run() once for every action that reaches its handler (whatever the result,
including an exception, but not a disabled M3U refresh), the scheduler's scan through _run_scheduled_scan, and
_append_ledger_entry right after the ledger file is closed (forced, only above zero).
"""
import datetime
import json
import logging
import types
from pathlib import Path

import pytest

LOGGER = logging.getLogger("test.usage")
PLUGIN_DIR = Path(__file__).resolve().parents[2] / "Event-Channel-Managarr"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
PLUGIN_PY = PLUGIN_DIR / "plugin.py"


class FakeUsage:
    def __init__(self):
        self.calls = []
        self.on_report = None

    def report(self, settings=None, logger=None, force=False):
        if self.on_report:
            self.on_report()
        self.calls.append((settings, force))


@pytest.fixture
def usage(plugin_module, monkeypatch):
    fake = FakeUsage()
    monkeypatch.setattr(plugin_module, "USAGE", fake)
    return fake


@pytest.fixture
def plugin(bare_plugin):
    bare_plugin.saved_settings = {}
    bare_plugin.version = "test"
    return bare_plugin


@pytest.fixture
def frozen_clock(plugin_module, monkeypatch):
    stamp = datetime.datetime(2026, 10, 10, 12, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(plugin_module, "timezone", types.SimpleNamespace(now=lambda: stamp))


def test_the_send_path_is_reached_when_the_lock_is_held(plugin_module, monkeypatch, tmp_path):
    """Positive control: on Windows there is no fcntl, so the real reporter returns before any
    send. Force the lock and consent and the recorder must see exactly one start."""
    usage = plugin_module.USAGE
    started = []
    monkeypatch.setattr(usage, "data_dir", str(tmp_path / "plugin_stats"))
    monkeypatch.setattr(usage, "directory", str(tmp_path / "plugin_stats" / "event-channel-managarr"))
    monkeypatch.setattr(usage, "_start", lambda fn: started.append(fn))
    monkeypatch.setattr(usage, "_lock", lambda fd: True)
    usage.report({"share_usage_counts": True}, LOGGER)
    assert len(started) == 1


def test_the_reporter_is_configured_for_this_plugin(plugin_module):
    u = plugin_module.USAGE
    assert (u.plugin, u.counter, u.label) == ("event-channel-managarr", "visibility_changes", "Visibility Changes")


def test_the_total_sums_shown_and_hidden_over_the_live_and_rotated_ledger(plugin_module, tmp_path, monkeypatch):
    live = tmp_path / "ledger.jsonl"
    live.write_text(json.dumps({"shown": 2, "hidden": 3}) + "\n", encoding="utf-8")
    (tmp_path / "ledger.jsonl.1").write_text(json.dumps({"shown": 5, "hidden": 1}) + "\n", encoding="utf-8")
    monkeypatch.setattr(plugin_module.PluginConfig, "LEDGER_FILE", str(live))
    assert plugin_module.USAGE.total_fn() == 11


def test_settings_are_read_under_the_plugin_key(plugin_module, monkeypatch):
    seen = []
    monkeypatch.setattr(plugin_module, "load_plugin_settings", lambda key, logger=None: seen.append(key) or {})
    plugin_module.USAGE.settings_fn()
    assert seen == ["event-channel-managarr"]


def test_a_ledger_line_reports_forced_after_the_file_is_closed(plugin, plugin_module, usage, frozen_clock, tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    seen_lines = []
    usage.on_report = lambda: seen_lines.append(ledger.read_text(encoding="utf-8").count("\n"))
    monkeypatch.setattr(plugin_module.PluginConfig, "LEDGER_FILE", str(ledger))
    plugin._append_ledger_entry(shown=2, hidden=3, is_scheduled_run=False, logger=LOGGER)
    assert usage.calls == [(None, True)]
    assert seen_lines == [1]


def test_a_zero_count_ledger_call_makes_no_forced_report(plugin, plugin_module, usage, frozen_clock, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "LEDGER_FILE", str(tmp_path / "ledger.jsonl"))
    plugin._append_ledger_entry(shown=0, hidden=0, is_scheduled_run=True, logger=LOGGER)
    assert usage.calls == []


def test_a_failed_ledger_write_does_not_report(plugin, plugin_module, usage, frozen_clock, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "LEDGER_FILE", str(tmp_path / "missing" / "ledger.jsonl"))
    plugin._append_ledger_entry(shown=2, hidden=3, is_scheduled_run=False, logger=LOGGER)
    assert usage.calls == []


def test_a_reporter_that_raises_never_breaks_the_ledger(plugin, plugin_module, frozen_clock, tmp_path, monkeypatch):
    class Exploding:
        def report(self, settings=None, logger=None, force=False):
            raise RuntimeError("boom")

    monkeypatch.setattr(plugin_module, "USAGE", Exploding())
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(plugin_module.PluginConfig, "LEDGER_FILE", str(ledger))
    plugin._append_ledger_entry(shown=2, hidden=3, is_scheduled_run=False, logger=LOGGER)
    assert ledger.read_text(encoding="utf-8").count("\n") == 1


def test_an_action_reports_once_with_the_live_settings(plugin, usage):
    plugin.validate_configuration_action = lambda settings, logger: {"status": "success", "message": "ok"}
    result = plugin.run("validate_configuration", {}, {"settings": {"x": 1}, "logger": LOGGER})
    assert result == {"status": "success", "message": "ok"}
    assert usage.calls == [({"x": 1}, False)]


def test_an_action_that_returns_an_error_still_reports_once(plugin, usage):
    plugin.validate_configuration_action = lambda settings, logger: {"status": "error", "message": "no"}
    result = plugin.run("validate_configuration", {}, {"settings": {"x": 1}, "logger": LOGGER})
    assert result["status"] == "error"
    assert usage.calls == [({"x": 1}, False)]


def test_an_action_that_raises_still_reports_once(plugin, usage):
    def boom(settings, logger):
        raise RuntimeError("boom")

    plugin.validate_configuration_action = boom
    result = plugin.run("validate_configuration", {}, {"settings": {"x": 1}, "logger": LOGGER})
    assert result == {"status": "error", "message": "boom"}
    assert usage.calls == [({"x": 1}, False)]


def test_an_unknown_action_does_not_report(plugin, usage):
    result = plugin.run("no_such_action", {}, {"settings": {}, "logger": LOGGER})
    assert result["status"] == "error"
    assert usage.calls == []


def test_a_disabled_m3u_event_sends_nothing(plugin, usage):
    settings = {"event": "m3u_refresh", "auto_rescan_on_m3u_refresh": False}
    result = plugin.run("on_m3u_refresh", {}, {"settings": settings, "logger": LOGGER})
    assert result is None
    assert usage.calls == []


def test_an_enabled_m3u_event_that_scans_reports_once(plugin, usage, monkeypatch):
    settings = {"event": "m3u_refresh", "auto_rescan_on_m3u_refresh": True}
    monkeypatch.setattr(plugin, "_scan_and_update_channels", lambda *a, **k: {"status": "success", "message": "ok"})
    monkeypatch.setattr(plugin, "_compact_scan_summary", lambda label, result: None)
    result = plugin.run("on_m3u_refresh", {}, {"settings": settings, "logger": LOGGER})
    assert result["status"] == "success"
    assert usage.calls == [(settings, False)]


@pytest.mark.parametrize("outcome", ["success", "skipped", "raises"])
def test_a_scheduled_scan_reports_once_on_every_path(plugin, usage, outcome):
    settings = {"x": 1}
    returned = {"status": "success", "message": "ok"}
    if outcome == "skipped":
        returned["skipped_due_to_lock"] = True

    def scan(*args, **kwargs):
        if outcome == "raises":
            raise RuntimeError("boom")
        return returned

    plugin._scan_and_update_channels = scan
    if outcome == "raises":
        with pytest.raises(RuntimeError):
            plugin._run_scheduled_scan(settings, LOGGER)
    else:
        assert plugin._run_scheduled_scan(settings, LOGGER) is returned
    assert usage.calls == [(None, False)]


def test_the_scheduler_calls_the_reporting_wrapper():
    source = PLUGIN_PY.read_text(encoding="utf-8")
    assert "self._run_scheduled_scan(current_settings, LOGGER)" in source
    assert "self._scan_and_update_channels(current_settings, LOGGER" not in source


def test_the_form_ends_with_the_usage_section_and_checkbox(plugin, plugin_module):
    fields = plugin_module.Plugin.fields.fget(plugin)
    assert fields[-1] == plugin_module.with_usage_field([], plugin_module.USAGE)[0]
    assert fields[-2]["id"] == "_section_usage" and fields[-2]["type"] == "info"
    assert [f.get("id") for f in fields].count("share_usage_counts") == 1


def test_the_manifest_carries_the_same_heading_and_checkbox(plugin, plugin_module):
    fields = plugin_module.Plugin.fields.fget(plugin)
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["fields"]
    assert manifest[-2] == fields[-2]
    assert manifest[-1] == fields[-1]
