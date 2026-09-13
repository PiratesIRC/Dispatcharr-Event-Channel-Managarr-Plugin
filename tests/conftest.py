"""Make plugin.py importable outside the container, so its rules can be RUN.

plugin.py imports Django and Dispatcharr models at module scope, so it cannot be
imported without a running backend. Registering stand-ins for those modules lets the
import succeed. The approach is the one EPG-Janitor and Channel-Maparr already use.

The stubs answer attribute access and nothing more. They do NOT emulate the ORM, so a
test may run a pure decision such as a hide rule, but never anything that executes a
query: a test that appears to exercise a queryset is asserting on a MagicMock and is
worthless. Anything database shaped belongs in the container.

This exists because the contract tests read plugin.py's structure with ast, and a
structural test cannot express that a value reaches a call before the call happens.
Five separate mutations that reverted the named day anchor passed the whole suite while
the rule itself was never executed by anything.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "Event-Channel-Managarr"
PLUGIN_PY = PLUGIN_DIR / "plugin.py"

_STUBBED_MODULES = [
    "apps", "apps.channels", "apps.channels.models",
    "apps.epg", "apps.epg.models",
    "core", "core.utils",
    "django", "django.db", "django.utils",
]

_MODULE_NAME = "ecm_plugin_under_test"


def _load_plugin_module():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    for name in _STUBBED_MODULES:
        sys.modules.setdefault(name, MagicMock())
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, PLUGIN_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin_module():
    """The shipped plugin module, imported with Dispatcharr stubbed."""
    return _load_plugin_module()


@pytest.fixture
def bare_plugin(plugin_module):
    """A Plugin instance built without running __init__.

    __init__ loads settings from a container path and starts a background scheduler,
    neither of which belongs in a test. The hide rules need neither.
    """
    cls = plugin_module.Plugin
    instance = cls.__new__(cls)
    instance._undated_tracker = {}
    instance._undated_warned = set()
    return instance
