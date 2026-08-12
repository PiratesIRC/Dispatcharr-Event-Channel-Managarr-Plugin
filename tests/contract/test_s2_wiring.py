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


def _calls(fn_name, method):
    fn = _fn(fn_name)
    return [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == method]


def test_reroute_is_called_from_BOTH_exits():
    """_run_managed_epg_pass returns twice -- the dry-run branch exits early. A
    single call site before the final return is unreachable during a Dry Run, so
    the preview would never report a reroute the applied run performs."""
    fn = _fn("_run_managed_epg_pass")
    returns = sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Return))
    calls = sorted(_calls("_run_managed_epg_pass", "_reroute_claimed_channels"))
    assert len(returns) >= 2, "expected an early dry-run return plus a final return"
    assert len(calls) >= 2, "reroute must be called on BOTH exit paths"
    assert any(c < returns[0] for c in calls), "no reroute call before the early return"
    assert any(c > returns[0] for c in calls), "no reroute call on the applied path"


def test_reroute_result_is_not_folded_into_attached_ids():
    """attached_ids feeds the CSV header, the per-row managed_epg_assigned flag and
    the scan notification. Merging reroutes into it makes a channel that merely
    MOVED indistinguishable from one that had no EPG at all."""
    fn = _fn("_run_managed_epg_pass")
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "attached_ids" for t in node.targets):
            seg = ast.dump(node.value)
            assert "rerouted" not in seg, "rerouted ids must not be merged into attached_ids"


def test_reroute_consults_the_reroutability_guard():
    src = ast.get_source_segment(_source(), _fn("_reroute_claimed_channels"))
    assert "_epg_binding_is_reroutable" in src, \
        "reroute must never move a channel off a populated real EPG"


def test_reroute_reaps_orphans_on_sources_it_touched():
    src = ast.get_source_segment(_source(), _fn("_reroute_claimed_channels"))
    assert "_reap_orphaned_epg_data" in src


def test_reroute_calls_precede_attach_and_detach_calls():
    """Reroute must run BEFORE attach/detach in each branch, not after.

    This ordering was an implementation surprise: _run_managed_epg_pass has
    four return statements (one bailout per branch), so the natural place for
    a call meant to "finish" the pass landed at the TOP of each branch instead
    of the end. A review confirmed the resulting order is not just harmless
    but strictly better: reroute reads pre-pass state and moves a claimed
    channel directly to its destination source; the NULL-only attach that
    runs afterward re-queries fresh state and so skips a channel already
    bound. Reversing the order would let the attach bind the channel to the
    DEFAULT source first, and reroute would then immediately re-point it --
    one wasted EPGData write (and orphan-reap) per reclaimed channel, every
    cycle. Do not "fix" this back to reroute-last.
    """
    reroute_calls = sorted(_calls("_run_managed_epg_pass", "_reroute_claimed_channels"))
    attach_calls = sorted(_calls("_run_managed_epg_pass", "_attach_managed_epg"))
    detach_calls = sorted(_calls("_run_managed_epg_pass", "_detach_managed_epg"))

    assert reroute_calls, "no _reroute_claimed_channels call found -- ordering check is vacuous"
    assert attach_calls, "no _attach_managed_epg call found -- ordering check is vacuous"
    assert detach_calls, "no _detach_managed_epg call found -- ordering check is vacuous"

    assert max(reroute_calls) < min(attach_calls), (
        "a _reroute_claimed_channels call sits after an _attach_managed_epg call; "
        "reroute must run first so the later NULL-only attach observes fresh state")
    assert max(reroute_calls) < min(detach_calls), (
        "a _reroute_claimed_channels call sits after a _detach_managed_epg call; "
        "reroute must run first so the later NULL-only attach observes fresh state")
