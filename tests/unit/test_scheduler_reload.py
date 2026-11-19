"""A Plugins page refresh must not leave the previous scheduler thread running.

Dispatcharr imports plugin.py afresh on a refresh, so the new copy has fresh
module globals and cannot see the thread the old copy started. Before the fix
each refresh added one scheduler thread per process (measured 2026-11-19:
"3 threads in one worker (leak)" after two refreshes). The thread now carries
its own stop event and is found through threading.enumerate().
"""

import importlib.util
import threading
from pathlib import Path

import ecm_parsing

PLUGIN_PY = Path(__file__).resolve().parents[2] / "Event-Channel-Managarr" / "plugin.py"


def _fresh_copy(name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin(module):
    plugin = module.Plugin.__new__(module.Plugin)
    plugin._dispatcharr_timezone = lambda: "UTC"
    return plugin


def _alive(name):
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


def test_a_second_copy_of_the_module_stops_the_first_copys_thread(plugin_module):
    first = _fresh_copy("ecm_reload_copy_one")
    second = _fresh_copy("ecm_reload_copy_two")
    name = first.SCHEDULER_THREAD_NAME
    try:
        _plugin(first)._start_background_scheduler({"scheduled_times": "0300"})
        assert len(_alive(name)) == 1
        old_thread = _alive(name)[0]
        # The second copy has its own globals: its _bg_thread is None.
        assert second._bg_thread is None
        _plugin(second)._start_background_scheduler({"scheduled_times": "0300"})
        old_thread.join(timeout=5)
        assert not old_thread.is_alive()
        assert len(_alive(name)) == 1
    finally:
        _plugin(second)._stop_background_scheduler()
    assert _alive(name) == []


def test_a_thread_without_a_stop_event_is_reported_not_counted_as_stopped():
    class Old:
        name = "sched"
        def is_alive(self):
            return True

    class New(Old):
        def __init__(self):
            self.stop = threading.Event()

    old, new, other = Old(), New(), Old()
    other.name = "something else"
    found = ecm_parsing.scheduler_threads_to_stop([old, new, other], "sched", "stop", current=None)
    assert found == [(old, None), (new, new.stop)]
    assert ecm_parsing.scheduler_threads_to_stop([new], "sched", "stop", current=new) == []
