"""The carry-over of the old /data state into the plugin data directory, and the bind that moves
the state constants there. Design rules 1 to 6 and 9 of the plugin data-dir runbook.

Every old path lives under tmp_path/old and every new one under tmp_path/plugins/_data, so nothing
here touches Dispatcharr's real /data (the conftest sandbox refuses it as well).
"""
import os

import pytest

KEY = "event-channel-managarr"


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


@pytest.fixture
def world(plugin_module, tmp_path, monkeypatch):
    """Old /data paths in tmp/old, a non-legacy layout whose data dir is tmp/plugins/_data/<key>."""
    storage = plugin_module.storage
    cfg = plugin_module.PluginConfig
    old = tmp_path / "old"
    old.mkdir()
    legacy = {name: str(old / name.lower()) for name, _kind, _rel in storage.SPEC.table}
    legacy["USAGE_DATA_DIR"] = str(old / "plugin_stats")
    monkeypatch.setattr(cfg, "LEGACY_PATHS", legacy)
    # The constants start at the legacy paths, as they do in a fresh process before bind runs.
    for name, value in legacy.items():
        if name != "USAGE_DATA_DIR":
            monkeypatch.setattr(cfg, name, value)
    monkeypatch.setattr(cfg, "STORAGE_LAYOUT", None)
    monkeypatch.setattr(cfg, "STORAGE_MIGRATION", None)
    plugins = tmp_path / "plugins"
    data = plugins / "_data" / KEY
    data.mkdir(parents=True)
    layout = storage.Layout(False, str(data), str(plugins), KEY, "test")
    return {"storage": storage, "cfg": cfg, "old": old, "plugins": plugins, "data": data,
            "layout": layout, "legacy": legacy, "log": Log(), "tmp": tmp_path}


def _migrate(w, version="test-version"):
    items = w["storage"].items_for(w["cfg"], w["layout"])
    return w["storage"].migrate(w["layout"], items, w["log"], version=version)


def test_items_map_every_constant_to_its_new_home(world):
    storage, cfg = world["storage"], world["cfg"]
    items = {i.name: i for i in storage.items_for(cfg, world["layout"])}
    data = world["data"]
    plugins = world["plugins"]
    assert items["SETTINGS_FILE"].new == str(data / "settings.json")
    assert items["RESULTS_FILE"].new == str(data / "results.json")
    assert items["UNDATED_FIRST_SEEN_FILE"].new == str(data / "undated_first_seen.json")
    assert items["GROUP_SOURCE_RECORD_FILE"].new == str(data / "group_sources.json")
    assert items["LEDGER_FILE"].new == str(data / "ledger.jsonl")
    assert items["USAGE_DATA_DIR"].new == str(data / "usage")
    assert items["LAST_RUN_FILE"].new == os.path.join(str(plugins), "_data", KEY + ".last_run.json")
    assert items["SCAN_LOCK_FILE"].new == os.path.join(str(plugins), "_data", KEY + ".scan.lock")


def test_bind_rebinds_the_constants_and_keeps_the_legacy_literals(world, monkeypatch):
    storage, cfg = world["storage"], world["cfg"]
    root = world["tmp"] / "bind"
    data = root / "_data" / KEY
    monkeypatch.setattr(storage._ps, "_from_module_context",
                        lambda plugin_dir, failures: (str(data), "module context"))
    layout = storage.bind(cfg, str(root / KEY / "plugin.py"), world["log"])
    assert layout.legacy is False and layout.data_dir == str(data)
    assert cfg.SETTINGS_FILE == str(data / "settings.json")
    assert cfg.LEDGER_FILE == str(data / "ledger.jsonl")
    assert cfg.USAGE_DATA_DIR == str(data / "usage")
    assert cfg.LAST_RUN_FILE == os.path.join(str(root), "_data", KEY + ".last_run.json")
    assert cfg.LEGACY_PATHS["SETTINGS_FILE"] == world["legacy"]["SETTINGS_FILE"]
    assert cfg.STORAGE_MIGRATION is not None


def test_a_failed_carry_over_puts_every_legacy_path_back(world, monkeypatch):
    storage, cfg = world["storage"], world["cfg"]
    root = world["tmp"] / "bind"
    monkeypatch.setattr(storage._ps, "_from_module_context",
                        lambda plugin_dir, failures: (str(root / "_data" / KEY), "module context"))

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(storage._ps, "migrate", boom)
    layout = storage.bind(cfg, str(root / KEY / "plugin.py"), world["log"])
    assert layout.legacy is True
    assert cfg.STORAGE_MIGRATION is None
    assert cfg.SETTINGS_FILE == world["legacy"]["SETTINGS_FILE"]
    assert cfg.LAST_RUN_FILE == world["legacy"]["LAST_RUN_FILE"]
    assert cfg.LEDGER_FILE == world["legacy"]["LEDGER_FILE"]
    assert world["log"].errors, "a failed carry-over must log an error"


def test_group_source_record_is_carried_over_byte_identical(world):
    # Non-ASCII bytes on purpose: the copy must be a byte copy, not a re-serialisation.
    record = b'{\n  "US: NFL": {\n    "created": "2026-09-02"\n  }\n}\n\xc3\xa9\n'
    with open(world["legacy"]["GROUP_SOURCE_RECORD_FILE"], "wb") as fh:
        fh.write(record)
    result = _migrate(world)
    assert result.failed == []
    with open(world["data"] / "group_sources.json", "rb") as fh:
        assert fh.read() == record


def test_settings_and_results_are_copied_and_a_new_file_wins(world):
    with open(world["legacy"]["SETTINGS_FILE"], "w") as fh:
        fh.write('{"old": true}')
    with open(world["legacy"]["RESULTS_FILE"], "w") as fh:
        fh.write('{"results": 1}')
    (world["data"] / "settings.json").write_text('{"new": true}')
    _migrate(world)
    assert (world["data"] / "settings.json").read_text() == '{"new": true}'
    assert (world["data"] / "results.json").read_text() == '{"results": 1}'
    assert os.path.exists(world["legacy"]["SETTINGS_FILE"]), "the old file is never deleted"


def test_usage_dir_is_copied_under_the_plugin_subfolder(world):
    src = os.path.join(world["legacy"]["USAGE_DATA_DIR"], KEY)
    os.makedirs(src)
    with open(os.path.join(src, "install_id"), "w") as fh:
        fh.write("a" * 32)
    result = _migrate(world)
    assert result.install_id_blocked is False
    assert (world["data"] / "usage" / KEY / "install_id").read_text() == "a" * 32


def test_a_failed_install_id_copy_blocks_usage_reporting(world, monkeypatch):
    src = os.path.join(world["legacy"]["USAGE_DATA_DIR"], KEY)
    os.makedirs(src)
    with open(os.path.join(src, "install_id"), "w") as fh:
        fh.write("b" * 32)
    real = world["storage"]._ps.atomic_write_bytes

    def refuse_install_id(path, data):
        if path.endswith("install_id"):
            raise OSError("read-only")
        return real(path, data)

    monkeypatch.setattr(world["storage"]._ps, "atomic_write_bytes", refuse_install_id)
    result = _migrate(world)
    assert result.install_id_blocked is True
    assert "install_id" in result.failed


def test_the_ledger_tally_tail_appends_old_lines_once(world):
    storage = world["storage"]
    old_ledger = world["legacy"]["LEDGER_FILE"]
    # Binary on purpose: text mode on Windows writes CRLF and would skew the byte counts.
    with open(old_ledger, "wb") as fh:
        fh.write(b'{"shown": 1}\n{"shown": 2}\n')
    _migrate(world)
    new_ledger = str(world["data"] / "ledger.jsonl")
    assert open(new_ledger, "rb").read() == b'{"shown": 1}\n{"shown": 2}\n'
    with open(old_ledger, "ab") as fh:
        fh.write(b'{"shown": 3}\n')
    assert storage.sync_tally_tail(world["layout"], new_ledger, world["log"]) == len(b'{"shown": 3}\n')
    assert open(new_ledger, "rb").read() == b'{"shown": 1}\n{"shown": 2}\n{"shown": 3}\n'
    assert storage.sync_tally_tail(world["layout"], new_ledger, world["log"]) == 0


def test_the_rotated_ledger_is_carried_over_byte_identical(world):
    # Non-ASCII bytes on purpose: the rotated file is copied whole, never re-serialised.
    record = b'{"shown": 4, "hidden": 1}\n\xc3\xa9\n'
    with open(world["legacy"]["LEDGER_ROTATED_FILE"], "wb") as fh:
        fh.write(record)
    result = _migrate(world)
    assert result.failed == []
    rotated = world["data"] / "ledger.jsonl.1"
    assert rotated.read_bytes() == record
    assert str(rotated) in result.copied


def test_an_absent_rotated_ledger_is_not_a_failure(world):
    result = _migrate(world)
    assert result.failed == []
    assert not (world["data"] / "ledger.jsonl.1").exists()


def test_the_visibility_total_counts_the_rotated_ledger(world, plugin_module, monkeypatch):
    with open(world["legacy"]["LEDGER_ROTATED_FILE"], "wb") as fh:
        fh.write(b'{"shown": 4, "hidden": 1}\n')
    with open(world["legacy"]["LEDGER_FILE"], "wb") as fh:
        fh.write(b'{"shown": 2, "hidden": 0}\n')
    _migrate(world)
    monkeypatch.setattr(world["cfg"], "LEDGER_FILE", str(world["data"] / "ledger.jsonl"))
    assert plugin_module.USAGE.total_fn() == 7, "the total globs the rotated file too"


def test_a_legacy_layout_migrates_nothing(world):
    storage = world["storage"]
    legacy_layout = storage.Layout(True, None, None, KEY, "test")
    result = storage.migrate(legacy_layout, [], world["log"], version="v")
    assert result.copied == [] and result.failed == []
    assert not (world["data"] / "settings.json").exists()


def test_check_context_warns_once_when_dispatcharr_names_another_directory(world):
    storage = world["storage"]
    log = world["log"]
    layout = world["layout"]
    elsewhere = str(world["tmp"] / "elsewhere")
    storage.check_context(layout, {"data_dir": elsewhere}, log)
    storage.check_context(layout, {"data_dir": elsewhere}, log)
    assert len(log.warnings) == 1, "one WARNING per process and pair, not one per run"
    storage.check_context(layout, {"data_dir": str(world["data"])}, log)
    assert len(log.warnings) == 1, "equal paths are silent"


def test_usage_reporting_is_pointed_at_the_plugin_data_dir_and_a_blocked_id_turns_it_off(plugin_source):
    """Structural: the reporter takes the rebound constant, and install_id_blocked disables it."""
    import ast
    assert "data_dir=PluginConfig.USAGE_DATA_DIR" in plugin_source
    tree = ast.parse(plugin_source)
    guarded = [
        node for node in tree.body
        if isinstance(node, ast.If) and "install_id_blocked" in ast.unparse(node.test)
        and any("USAGE.report" in ast.unparse(stmt) for stmt in node.body)
    ]
    assert guarded, "a blocked install id must replace USAGE.report at import"
