"""The settings form must not perform network or blocking I/O.

Dispatcharr evaluates `Plugin.fields` every time the plugin's settings page is
rendered, so it sits on a per-request path. Until 2026-08-12 that property
called api.github.com with a five second timeout to look for a newer release,
and wrote a cache file under /data on the way through. Two consequences:

- the settings page could not render without outbound internet access;
- the once-a-day throttle was written only on SUCCESS, so a box that could not
  reach GitHub never engaged it and retried on EVERY render, not once a day.

The update check still exists. It moved to the Validate Configuration action,
which runs only when the operator clicks it.

These guards parse plugin.py with ast rather than importing it, matching the
other contract tests here: plugin.py imports Django and cannot be imported
outside Dispatcharr. Every guard has a self-check proving it fails on violating
code, because a guard that never fails is not a guard.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "Event-Channel-Managarr" / "plugin.py"

# Bare callables that reach the network whatever they are called on.
# Deliberately NOT `get`/`post`: matching those by bare name flags every
# dict.get() in the file, which is most of them, and a guard that fires on
# ordinary code gets deleted rather than fixed.
NETWORK_CALLABLES = {"urlopen", "urlretrieve", "create_connection"}

# Module roots whose calls are network calls. Matched against the ROOT of the
# dotted path, so `urllib.request.urlopen(...)` and `requests.get(...)` are both
# caught while `settings.get(...)` is not.
NETWORK_MODULES = {"urllib", "requests", "httpx", "socket", "http", "aiohttp"}


def _plugin_tree():
    return ast.parse(PLUGIN.read_text(encoding="utf-8"), filename=str(PLUGIN))


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _dotted(func):
    """Render a call target as a dotted string, e.g. 'urllib.request.urlopen'."""
    parts = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return None            # a call on an expression; the root is unknowable
    return ".".join(reversed(parts))


def _network_calls(node):
    """Dotted call targets inside this function that reach the network."""
    found = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        dotted = _dotted(sub.func)
        if dotted is None:
            continue
        parts = dotted.split(".")
        if parts[0] in NETWORK_MODULES or parts[-1] in NETWORK_CALLABLES:
            found.add(dotted)
    return found


def _self_methods_called(node):
    return {sub.func.attr for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
            and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "self"}


def _transitive_from_fields(tree):
    """`fields` plus every self.<method> it can reach, transitively."""
    seen, queue = set(), ["fields"]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        fn = _find_function(tree, name)
        if fn is None:
            continue
        queue.extend(_self_methods_called(fn) - seen)
    return seen


@pytest.fixture(scope="module")
def tree():
    return _plugin_tree()


def test_fields_property_exists(tree):
    fields = _find_function(tree, "fields")
    assert fields is not None, "Plugin.fields is gone; this whole file stops guarding anything"
    assert any(isinstance(d, ast.Name) and d.id == "property" for d in fields.decorator_list), \
        "fields is no longer a property; re-check what Dispatcharr calls per request"


def test_nothing_reachable_from_fields_touches_the_network(tree):
    """The important one. Transitive, not just the property body, because the
    original defect was one call away: fields -> _get_latest_version -> urlopen."""
    reachable = _transitive_from_fields(tree)
    offenders = {}
    for name in sorted(reachable):
        fn = _find_function(tree, name)
        if fn is None:
            continue
        hits = _network_calls(fn)
        if hits:
            offenders[name] = sorted(hits)
    assert not offenders, (
        f"the settings form can reach network code: {offenders}. Dispatcharr "
        f"evaluates `fields` on every settings-page render, so this makes the "
        f"page depend on outbound internet access. Put the call behind an action."
    )


def test_fields_does_not_write_the_version_cache(tree):
    """Writing on a render path is the other half of the original defect."""
    reachable = _transitive_from_fields(tree)
    assert "_save_version_check" not in reachable, (
        "`fields` can reach _save_version_check, so rendering the settings page "
        "writes to /data. The write belongs in the action that performs the check."
    )


def test_the_update_check_still_happens_somewhere(tree):
    """Guard against 'fixing' this by deleting the feature."""
    refresh = _find_function(tree, "_refresh_version_check")
    assert refresh is not None, "_refresh_version_check is gone"
    assert "_get_latest_version" in _self_methods_called(refresh)

    validate = _find_function(tree, "validate_configuration_action")
    assert validate is not None
    assert "_refresh_version_check" in _self_methods_called(validate), (
        "nothing refreshes the update check any more; it should run from the "
        "Validate Configuration action")


def test_a_failed_check_still_records_a_timestamp(tree):
    """The throttle must engage on failure too, or an offline box retries forever."""
    refresh = _find_function(tree, "_refresh_version_check")
    assert "_save_version_check" in _self_methods_called(refresh), (
        "_refresh_version_check no longer records the attempt, so a failing "
        "check will not engage the once-a-day throttle")


# --- self-checks: each guard must FAIL on violating code -------------------

VIOLATING_NETWORK = '''
class Plugin:
    @property
    def fields(self):
        return self._build()

    def _build(self):
        return urllib.request.urlopen("https://example.invalid")
'''

VIOLATING_WRITE = '''
class Plugin:
    @property
    def fields(self):
        self._save_version_check("v1")
        return []

    def _save_version_check(self, v):
        pass
'''


def test_guard_fails_on_a_network_call_reached_from_fields():
    tree = ast.parse(VIOLATING_NETWORK)
    reachable = _transitive_from_fields(tree)
    offenders = {n for n in reachable
                 if _find_function(tree, n) is not None
                 and _network_calls(_find_function(tree, n))}
    assert offenders, "the network guard does not fire on code that plainly violates it"


def test_guard_fails_on_a_cache_write_reached_from_fields():
    tree = ast.parse(VIOLATING_WRITE)
    assert "_save_version_check" in _transitive_from_fields(tree), \
        "the write guard does not fire on code that plainly violates it"
