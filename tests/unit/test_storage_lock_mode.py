"""Every file this plugin creates with os.open is owner-only (no group or other bits).

The Dispatcharr Plugin Hub runs CodeQL on the released archive and refuses a pull request on a
high py/overly-permissive-file finding. Mode 0o644 and 0o660 both failed on Dispatcharr/Plugins#628;
0o600 passed. Every Dispatcharr worker runs as one user, so owner-only is enough.
"""
import os
import re
import stat
import sys

import pytest

PACKAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir,
                           "Event-Channel-Managarr")
SHIPPED = ("plugin.py", "storage.py", "plugin_storage.py", "usage_client.py", "ecm_parsing.py",
           "ecm_fields.py", "ecm_profiles.py")


def test_no_world_bits_in_any_os_open_mode():
    modes = []
    for name in SHIPPED:
        with open(os.path.join(PACKAGE_DIR, name), encoding="utf-8") as fh:
            modes += [(name, m) for m in re.findall(r"os\.open\([^)]*?(0o[0-7]{3})\)", fh.read())]
    assert modes, "no os.open mode found; the check would pass vacuously"
    for name, mode in modes:
        assert int(mode, 8) & 0o077 == 0, f"{name}: os.open mode {mode} grants access beyond the owner"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_created_lock_file_has_no_world_bits(plugin_module, tmp_path):
    lock_path = str(tmp_path / "x.lock")
    old_umask = os.umask(0)
    try:
        with plugin_module.storage._ps._lock(lock_path):
            pass
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(os.stat(lock_path).st_mode) & 0o077 == 0
