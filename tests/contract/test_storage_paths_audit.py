"""Every /data path literal in the shipped modules is one the storage design accounts for.

storage.bind rebinds the PluginConfig state constants to the plugin data directory at import.
The literals that stay are the legacy paths storage.bind records in LEGACY_PATHS (the carry-over
source and the fallback when the data directory cannot be trusted), plus the shared exports folder.
A new /data literal anywhere else is a path the data-dir move does not cover, so it fails here.

storage.py and plugin_storage.py are skipped: they name the legacy paths on purpose. A literal
counts only when it starts with /data and contains no whitespace, so help text that mentions a
path is not a path literal. Docstrings are string constants too and are checked.
"""
import ast
import pathlib

PACKAGE_DIR = pathlib.Path(__file__).resolve().parents[2] / "Event-Channel-Managarr"

ALLOWED = {
    # PluginConfig state constants: the legacy paths storage.bind moves (LEGACY_PATHS).
    "/data/event_channel_managarr_last_run.json",
    "/data/event_channel_managarr_scan.lock",
    "/data/event_channel_managarr_settings.json",
    "/data/event_channel_managarr_results.json",
    "/data/event_channel_managarr_undated_first_seen.json",
    "/data/event_channel_managarr_group_sources.json",
    "/data/event_channel_managarr_ledger.jsonl",
    "/data/plugin_stats",
    # Kept as they are: the shared exports folder, which seven plugins use.
    "/data/exports",
}


def _literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("/data") and not any(ch.isspace() for ch in value):
                found.add(value)
    return found


def test_every_data_literal_is_accounted_for():
    found = set()
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        if path.name in ("storage.py", "plugin_storage.py"):
            continue
        found |= _literals(path)
    strays = found - ALLOWED
    unused = ALLOWED - found
    assert not strays, "unaccounted /data literals: %s" % sorted(strays)
    assert not unused, "allowed /data literals no longer in the code: %s" % sorted(unused)
