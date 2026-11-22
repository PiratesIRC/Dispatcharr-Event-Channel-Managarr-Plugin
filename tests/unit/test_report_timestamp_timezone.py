"""Report file names carry Dispatcharr's local time, not the worker's UTC clock.

On Dispatcharr 0.34 plugin actions run in a Celery worker whose clock is UTC, so
a bare datetime.now() stamped the CSV names five hours ahead of the Central time
the operator sees. The stamp now comes from _report_timestamp, which reads the
zone through _dispatcharr_timezone.
"""

import importlib.util
from datetime import datetime
from pathlib import Path

import pytz

PLUGIN_PY = Path(__file__).resolve().parents[2] / "Event-Channel-Managarr" / "plugin.py"
STAMP_FORMAT = "%Y%m%d_%H%M%S"


def _plugin_module(name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin(module, zone):
    plugin = module.Plugin.__new__(module.Plugin)
    plugin._dispatcharr_timezone = lambda: zone
    return plugin


def _stamp_as_wall_clock(stamp):
    return datetime.strptime(stamp, STAMP_FORMAT)


def test_stamp_is_the_dispatcharr_wall_clock_not_utc():
    module = _plugin_module("ecm_report_stamp_chicago")
    stamp = _plugin(module, "America/Chicago")._report_timestamp()
    stamped = _stamp_as_wall_clock(stamp)
    chicago_now = datetime.now(pytz.timezone("America/Chicago")).replace(tzinfo=None)
    utc_now = datetime.now(pytz.utc).replace(tzinfo=None)
    # Positive control: the stamp matches Central within a few seconds.
    assert abs((stamped - chicago_now).total_seconds()) < 5
    # Negative control: it is not the UTC wall clock, which is five or six hours away.
    assert abs((stamped - utc_now).total_seconds()) > 3 * 3600


def test_stamp_falls_back_to_utc_when_the_zone_is_utc():
    module = _plugin_module("ecm_report_stamp_utc")
    stamp = _plugin(module, "UTC")._report_timestamp()
    utc_now = datetime.now(pytz.utc).replace(tzinfo=None)
    assert abs((_stamp_as_wall_clock(stamp) - utc_now).total_seconds()) < 5


def test_report_file_names_do_not_use_the_bare_clock():
    source = PLUGIN_PY.read_text(encoding="utf-8")
    for quote in ("\"", "'"):
        assert "datetime.now().strftime(" + quote + "%Y%m%d_%H%M%S" + quote not in source
    assert source.count("self._report_timestamp()") == 2
