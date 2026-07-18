"""Guards for S2's plugin.py wiring.

plugin.py imports Django at module scope and cannot be imported outside the
container, so structure is checked with ast. Where structure is too weak, methods
are COMPILED OUT and CALLED with stubs.

The most important guards here are NEGATIVE: this slice's safety argument is that
the existing pass is unmodified. Signature pinning is NOT enough -- someone could
reintroduce bug-045 (global detach ignoring scope_ids) inside _detach_managed_epg
while keeping its exact five arguments. So the BODIES are hash-pinned, reusing the
pattern scripts/core_manifest.json already uses for matching_core.py.
"""

import ast
import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
sys.path.insert(0, str(ROOT / "Event-Channel-Managarr"))
import ecm_profiles  # noqa: E402

# Recorded from the pre-S2 baseline (Task 3 Step 2). If one of these changes, this
# slice has modified machinery it promised not to touch.
FROZEN_BODIES = {
    "_attach_managed_epg": "ad018b684c875cec7c9d3d341c81103bc4bad00fe2389bf74ccbaefc80b072ff",
    "_detach_managed_epg": "086a0ef01f87f7e3770e3ac3a84ebb40c7022244675b625b13769462dddd4942",
    "_managed_override_ids": "c08f0bf1c24837f888608e82d2f54b96bb30c5b5fa36ebd7bdc305edc263b534",
    "_get_or_create_managed_epg_source": "16480528054402082ee07c025f390b2a2ade7b2bd1e59cfee0c66a076d9e9b20",
    "_localized_template_props": "c323376d0b18b007b59085709482d5742a6f7a97413d54eba2c23270513e69c1",
}


def _source():
    return PLUGIN_PY.read_text(encoding="utf-8")


def _fn(name):
    return next((n for n in ast.walk(ast.parse(_source()))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)


def test_frozen_baseline_was_recorded():
    """A empty FROZEN_BODIES would make every pin below vacuous."""
    assert len(FROZEN_BODIES) == 5, "record the baseline hashes (Task 3 Step 2)"


@pytest.mark.parametrize("name", sorted(FROZEN_BODIES))
def test_frozen_method_body_is_unchanged(name):
    digest = hashlib.sha256(ast.dump(_fn(name)).encode()).hexdigest()
    assert digest == FROZEN_BODIES[name], (
        f"{name}'s BODY changed. This slice's entire safety argument is that the "
        f"existing pass is untouched.")


def test_ecm_profiles_is_imported():
    assert re.search(r"^import ecm_profiles$", _source(), re.M)


def test_new_methods_exist():
    for name in ("_managed_props_for_profile", "_ensure_profile_source",
                 "_epg_binding_is_reroutable", "_reap_orphaned_epg_data"):
        assert _fn(name) is not None, name


def test_props_builder_passes_the_profiles_own_timezone_FIRST():
    """resolve_output_timezone(source, system) is not symmetric and both args are
    plain strings -- a transposition raises nothing and renders times wrong."""
    src = ast.get_source_segment(_source(), _fn("_managed_props_for_profile"))
    call = re.search(r"resolve_output_timezone\(\s*([^,]+),", src)
    assert call and "profile.timezone" in call.group(1), \
        "profile.timezone must be the FIRST argument (the source zone)"
