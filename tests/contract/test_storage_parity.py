"""Drift gate for the vendored storage layer.

plugin_storage.py holds the data-directory resolution, the carry-over of the old /data state and
the tally tail. It is vendored rather than imported: a plugin deploys as a self-contained directory
into /data/plugins, where the workspace _shared path does not exist.

The committed copy must hash-match scripts/storage_manifest.json. To land an intended change: edit
_shared/plugin_storage.py, re-vendor it with _shared/tools/sync_storage.py, and commit the copy and
the manifest together. Runs in CI and needs no access to the workspace _shared directory.
"""
import hashlib
import json
import os

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(_REPO, "release.json"), encoding="utf-8") as _fh:
    _INNER = os.path.join(_REPO, json.load(_fh)["package_dir"])
_MANIFEST = os.path.join(_REPO, "scripts", "storage_manifest.json")

with open(_MANIFEST, encoding="utf-8") as _fh:
    _PINS = json.load(_fh)


def test_manifest_pins_the_storage_layer():
    assert list(_PINS) == ["plugin_storage.py"], "the manifest must pin exactly plugin_storage.py"


@pytest.mark.parametrize("fname", sorted(_PINS))
def test_vendored_storage_matches_manifest(fname):
    path = os.path.join(_INNER, fname)
    assert os.path.exists(path), f"vendored {fname} is missing"
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert digest == _PINS[fname], (
        f"{fname} drifted from its pinned hash. If the change is intended, edit "
        f"_shared/plugin_storage.py, re-vendor it with _shared/tools/sync_storage.py, "
        f"and commit the copy and scripts/storage_manifest.json together."
    )


@pytest.mark.parametrize("fname", sorted(_PINS))
def test_vendored_storage_uses_unix_line_endings(fname):
    path = os.path.join(_INNER, fname)
    assert b"\r\n" not in open(path, "rb").read()
