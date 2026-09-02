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
#
# Two of the five were RE-RECORDED on 2026-08-12, deliberately and for a reason
# unrelated to S2. The Swedish channel-name format now selects the 24-hour
# {starttime24}/{endtime24} placeholders instead of the 12-hour ones, which
# requires editing exactly the two methods that build those templates. Measured
# in the container at /app/apps/output/epg.py: {starttime}/{endtime} are
# unconditionally converted to 12-hour AM/PM, and output_timezone converts the
# instant rather than the format, so a channel named "19:55" was being titled
# "Upcoming at 7:55 PM" against its own name.
#
# ALL FIVE were also re-recorded on 2026-08-12 for a second, unrelated reason:
# the digest scheme changed. It used to hash ast.dump(fn), whose output is not
# stable across Python versions, so every pin failed on CI (3.11) while passing
# locally (3.12). It now hashes the function's own source text, which involves
# no interpreter at all. See _body_digest below.
#
# Re-recording a pin is only honest when the change that moved it was intended
# and is separately tested. tests/unit/test_se_time_placeholders.py covers the
# behaviour these two hashes no longer pin.
#
# _get_or_create_managed_epg_source was re-recorded a SECOND time on 2026-08-14.
# Its us_title_pattern literal no longer requires a PPV, LIVE or EVENT keyword
# before the slot number, so a provider naming its slots "07 - 8/14 7pm Broncos
# at Falcons" now gets upcoming and ended titles rather than the renderer's
# static fallback. The same method also gained the superseded pattern in its
# stock_patterns set, without which an existing installation would keep the old
# pattern for ever. Both halves are covered by
# tests/unit/test_ecm_profiles.py (extraction from bare-numbered names, and the
# guard that keeps "60 Minutes" from being treated as an event) and by
# tests/contract/test_us_pattern_parity.py (the plugin.py literal still equals
# ecm_profiles.US_ET, and the superseded pattern is still listed).
#
# _get_or_create_managed_epg_source was re-recorded a THIRD time on 2026-08-29.
# Its us_title_pattern literal gained a negative lookahead before the slot number
# so a match cannot begin inside an air time. Without it a name whose slot number
# is followed by text rather than by a date or a time -- measured live as
# "Boxing 3 : MOSES vs HRGOVIC  4:00pm" -- matched at the time instead and put
# the title "00pm" in the guide (bug-146). The same method also gained that
# superseded pattern in its stock_patterns set so existing installations upgrade.
# Both halves are covered by tests/unit/test_ecm_profiles.py (the guard, and the
# names that must still parse unchanged) and by
# tests/contract/test_us_pattern_parity.py.
#
# _get_or_create_managed_epg_source was re-recorded a FOURTH time on 2026-08-30.
# Its us_time_pattern literal gained a boundary on each side of the clock time.
# Without the trailing one the am or pm marker matched the opening letters of an
# ordinary word, so "PPV 12 AMERICAN LEGENDS" read as midnight and
# "ALI vs 8 AMATEUR BOUTS" as 8 o'clock. That was cosmetic while the pattern only
# titled a guide entry, but the new [UndatedEnded] rule hides a channel on the
# time this pattern returns, so a wrongly read time removed a channel from the
# lineup. The same method also gained the superseded time pattern in its
# stock_patterns set so existing installations upgrade, and its _py_named helper
# now rewrites a named group with a regex rather than a blunt string replace,
# because the blunt one would have turned the new lookbehind (?<! into (?P<! and
# put a value in stock_patterns that could never match a real stored pattern.
# All three halves are covered by tests/contract/test_us_pattern_parity.py (the
# plugin.py literal still equals the ecm_profiles copy and the ecm_parsing
# fallback, a word is no longer read as a meridiem, and the superseded pattern is
# still listed as a stock default).
#
# The other three remain at their original S2 baseline values and must not be
# touched without the same argument.
FROZEN_BODIES = {
    "_attach_managed_epg": "b0126debd9231deb49625ca0404952679197b862bff6cd86c618eb46fe3b335e",
    "_detach_managed_epg": "9e8d367e7d0789715e04249dab786a494b7f6919a60d6f9c755029e8261b98ca",
    "_managed_override_ids": "5d41e55a7146863609792f31f20134469c090b51938c1ab67d0f34399c526d6c",
    # re-recorded 2026-08-12, 2026-08-14, 2026-08-29 and 2026-08-30, see the notes above
    "_get_or_create_managed_epg_source": "0bd2e478445ec189406b1924cf24972d057a769cd1980499be5dd57d88552656",
    # re-recorded 2026-08-12, see the note above
    "_localized_template_props": "8daf6f68b33352690f255dc6d7185546c0ea70596743e633b0403861c62e75dd",
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


def _body_digest(name):
    """Digest a method body in a way that does not depend on the interpreter.

    This used to hash `ast.dump(fn)`, which is NOT stable across Python
    versions: measured 2026-08-12 on the same unchanged source,
    _attach_managed_epg digests to ad018b68... under Python 3.12 and
    c996e410... under 3.13. The recorded values were therefore only ever
    reproducible on the exact interpreter that produced them, and since CI runs
    3.11 while they were recorded on 3.12, every one of these pins failed on CI
    from the day it was written. Locally they passed, which is why it went
    unnoticed until the branch was first pushed.

    Hashing the function's own source text has no interpreter involvement at
    all. Line endings are normalised so a CRLF checkout on Windows and an LF one
    on Linux agree.
    """
    segment = ast.get_source_segment(_source(), _fn(name))
    assert segment, f"could not read the source of {name}"
    normalised = segment.replace("\r\n", "\n").rstrip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", sorted(FROZEN_BODIES))
def test_frozen_method_body_is_unchanged(name):
    digest = _body_digest(name)
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
