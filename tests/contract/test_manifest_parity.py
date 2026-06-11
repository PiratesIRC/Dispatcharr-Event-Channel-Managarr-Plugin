"""Contract tests: plugin.py and plugin.json must stay in sync.

Checks:
- Action IDs match between plugin.py Plugin.actions and plugin.json actions.
- events lists match for every action that has an events key in either file.
- Version strings match (PLUGIN_VERSION in plugin.py == version in plugin.json).
- No duplicate action IDs within either file.

Uses only stdlib (ast, json, pathlib, re) — does NOT import plugin.py (Django).
"""

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"


# ---------------------------------------------------------------------------
# Helpers — parse plugin.py without importing it
# ---------------------------------------------------------------------------

def _load_plugin_py_actions():
    """Parse plugin.py with ast and return list of action dicts from Plugin.actions.

    Returns list[dict] on success; raises AssertionError with a clear message on failure.
    """
    source = PLUGIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PLUGIN_PY))

    # Find the Plugin class
    plugin_class = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Plugin":
            plugin_class = node
            break
    assert plugin_class is not None, "Could not find class 'Plugin' in plugin.py"

    # Find the class-level `actions = [...]` assignment
    actions_node = None
    for stmt in plugin_class.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "actions"
        ):
            actions_node = stmt.value
            break
    assert actions_node is not None, (
        "Could not find 'actions = [...]' class-level assignment in Plugin class in plugin.py"
    )

    try:
        actions = ast.literal_eval(actions_node)
    except Exception as exc:
        raise AssertionError(
            f"ast.literal_eval failed on Plugin.actions list in plugin.py — "
            f"the list must contain only plain dict literals (no self.* references). "
            f"Error: {exc}"
        ) from exc

    assert isinstance(actions, list), "Plugin.actions in plugin.py is not a list"
    return actions


def _load_plugin_py_version():
    """Extract PLUGIN_VERSION = '...' from plugin.py via regex."""
    source = PLUGIN_PY.read_text(encoding="utf-8")
    m = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']', source)
    assert m is not None, "Could not find PLUGIN_VERSION = '...' in plugin.py"
    return m.group(1)


def _load_plugin_json():
    with PLUGIN_JSON.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_action_ids_match():
    """The set of action IDs in plugin.py must equal the set in plugin.json."""
    py_actions = _load_plugin_py_actions()
    js_data = _load_plugin_json()

    py_ids = {a["id"] for a in py_actions}
    json_ids = {a["id"] for a in js_data["actions"]}

    only_in_py = py_ids - json_ids
    only_in_json = json_ids - py_ids

    assert not only_in_py, f"Action IDs in plugin.py but NOT in plugin.json: {only_in_py}"
    assert not only_in_json, f"Action IDs in plugin.json but NOT in plugin.py: {only_in_json}"


def test_action_events_match():
    """For every action that has an 'events' key in either file, the lists must match."""
    py_actions = _load_plugin_py_actions()
    js_data = _load_plugin_json()

    py_by_id = {a["id"]: a for a in py_actions}
    json_by_id = {a["id"]: a for a in js_data["actions"]}

    # Collect all action ids that have events in either source
    all_ids_with_events = set()
    for aid, a in py_by_id.items():
        if "events" in a:
            all_ids_with_events.add(aid)
    for aid, a in json_by_id.items():
        if "events" in a:
            all_ids_with_events.add(aid)

    mismatches = []
    for aid in sorted(all_ids_with_events):
        py_events = py_by_id.get(aid, {}).get("events")
        json_events = json_by_id.get(aid, {}).get("events")
        if py_events != json_events:
            mismatches.append(
                f"  action '{aid}': plugin.py events={py_events!r}, plugin.json events={json_events!r}"
            )

    assert not mismatches, "events mismatch between plugin.py and plugin.json:\n" + "\n".join(mismatches)


def test_version_strings_match():
    """PLUGIN_VERSION in plugin.py must equal 'version' in plugin.json."""
    py_version = _load_plugin_py_version()
    js_data = _load_plugin_json()
    json_version = js_data.get("version")
    assert py_version == json_version, (
        f"Version mismatch: plugin.py PLUGIN_VERSION={py_version!r}, "
        f"plugin.json version={json_version!r}"
    )


def test_no_duplicate_action_ids_in_plugin_py():
    """Plugin.actions in plugin.py must not contain duplicate IDs."""
    py_actions = _load_plugin_py_actions()
    ids = [a["id"] for a in py_actions]
    seen = set()
    dupes = [aid for aid in ids if aid in seen or seen.add(aid)]
    assert not dupes, f"Duplicate action IDs in plugin.py Plugin.actions: {dupes}"


def test_no_duplicate_action_ids_in_plugin_json():
    """actions in plugin.json must not contain duplicate IDs."""
    js_data = _load_plugin_json()
    ids = [a["id"] for a in js_data["actions"]]
    seen = set()
    dupes = [aid for aid in ids if aid in seen or seen.add(aid)]
    assert not dupes, f"Duplicate action IDs in plugin.json: {dupes}"
