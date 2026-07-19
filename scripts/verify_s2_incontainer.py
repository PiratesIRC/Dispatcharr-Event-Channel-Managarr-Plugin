# scripts/verify_s2_incontainer.py
"""Prove S2 against LIVE data. The real pass runs inside a rolled-back transaction.

    docker cp Event-Channel-Managarr/ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
    docker cp scripts/verify_s2_incontainer.py dispatcharr:/tmp/verify_s2.py
    docker exec -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell < /tmp/verify_s2.py"

IMPORTANT: a DB rollback does NOT undo side effects that are not deferred to
commit. Dispatcharr's create_dummy_epg_data signal calls send_websocket_update
SYNCHRONOUSLY inside .save(), so creating a source inside this gate would broadcast
to every connected browser and then roll the row back underneath it. Proof 4
monkeypatches that function and asserts nothing escaped.

EXIT CODE: 0 pass, 1 fail.
"""

import logging
import sys
import traceback

sys.path.insert(0, "/tmp")
import ecm_profiles  # noqa: E402

from django.db import transaction  # noqa: E402
from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGSource  # noqa: E402
from apps.plugins.models import PluginConfig  # noqa: E402
import apps.epg.signals as epg_signals  # noqa: E402

GROUP_ID = 1915
GMT_SOURCE = "DAZN PPV Dummy (GMT)"
failures = []
log = logging.getLogger("ecm-verify")


def load_plugin_instance():
    """Get a Plugin instance WITHOUT Django's plugin machinery.

    PluginManager.get().get_plugin(key) returns None under manage.py shell --
    discovery is skipped for shell commands. And Plugin() must not be called: its
    __init__ runs _load_settings(), which arms the background scheduler thread.

    So: load the DEPLOYED module by path and allocate without __init__. __new__ is
    safe because every method the gate calls uses `self` only for method dispatch
    and class attributes, never instance state set in __init__.

    Loading by path also means this reads whatever is actually deployed -- old code
    now, new code after the deploy step -- which is what the gate should measure.
    """
    import importlib.util
    path = "/data/plugins/event-channel-managarr/plugin.py"
    spec = importlib.util.spec_from_file_location("_ecm_gate_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Plugin.__new__(module.Plugin)


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def snapshot():
    return {c.id: (c.epg_data.epg_source.name if c.epg_data and c.epg_data.epg_source
                   else None)
            for c in Channel.objects.filter(channel_group_id=GROUP_ID)
                                    .select_related("epg_data__epg_source")}


def main():
    settings = PluginConfig.objects.get(key="event-channel-managarr").settings or {}
    profiles = ecm_profiles.build_profiles(settings)
    inst = load_plugin_instance()
    has_new_code = hasattr(inst, "_reroute_claimed_channels")
    print(f"deployed plugin has the S2 methods: {has_new_code}")
    chans = list(Channel.objects.filter(channel_group_id=GROUP_ID)
                 .select_related("epg_data__epg_source"))
    enabled = list(Channel.objects.filter(
        channel_group_id=GROUP_ID, channelprofilemembership__enabled=True
    ).values_list("id", flat=True).distinct())
    claims = ecm_profiles.claimed_targets([c.name for c in chans], profiles)
    print(f"channels={len(chans)} enabled={len(enabled)} claimed={len(claims)}")

    # Captured before proofs 1-2 run: proof 1 is read-only and proof 2 is a
    # dry-run (writes nothing by contract, itself asserted below), so this is
    # equivalent to capturing immediately before proof 3's real pass.
    before = snapshot()

    protected = []
    if has_new_code:
        print("\n(1) the reroutability guard vetoes populated real EPGs")
        protected = [c for c in chans
                     if c.name in claims and not inst._epg_binding_is_reroutable(c)]
        print(f"       claimed but PROTECTED from moving: {len(protected)}")
        for c in protected[:3]:
            print(f"         {c.id} {c.name[:48]} -> {c.epg_data.epg_source.name}")
        check("guard is callable and returns bools",
              all(isinstance(inst._epg_binding_is_reroutable(c), bool) for c in chans[:20]))
    else:
        print("\n(1) SKIPPED -- deployed plugin does not have _epg_binding_is_reroutable yet")

    dry_ids = []
    if has_new_code:
        print("\n(2) dry-run reroute writes NOTHING and reports the same set")
        dry_ids = inst._reroute_claimed_channels(settings, log, True, enabled)
        check("dry run wrote nothing", snapshot() == before, "bindings changed")
        print(f"       dry run predicts {len(dry_ids)} move(s)")
    else:
        print("\n(2) SKIPPED -- deployed plugin does not have _reroute_claimed_channels yet")

    print("\n(3) REAL pass, rolled back")
    ws_calls = []
    original_ws = epg_signals.send_websocket_update
    epg_signals.send_websocket_update = lambda *a, **k: ws_calls.append((a, k))
    try:
        with transaction.atomic():
            att, det = inst._run_managed_epg_pass(
                settings, log, False, enabled, [c.id for c in chans])
            after = snapshot()

            lost = [cid for cid, src in before.items() if src and not after.get(cid)]
            check("NO channel lost its EPG", not lost, f"{len(lost)}: {lost[:5]}")

            moved = {cid: (before[cid], after[cid]) for cid in before
                     if after.get(cid) and before[cid] != after[cid]}
            print(f"       attached={len(att)} detached={len(det)} moved={len(moved)}")
            for cid, (b, a) in list(moved.items())[:8]:
                print(f"         {cid}: {b} -> {a}")

            claimed_ids = {c.id for c in chans if c.name in claims}
            protected_ids = {c.id for c in protected}
            expect_gmt = (claimed_ids & set(enabled)) - protected_ids
            on_gmt = [cid for cid in expect_gmt if after.get(cid) == GMT_SOURCE]
            check("every enabled, unprotected, claimed channel is on the GMT source",
                  len(on_gmt) == len(expect_gmt), f"{len(on_gmt)}/{len(expect_gmt)}")

            check("no protected channel was moved",
                  not (protected_ids & set(moved)), f"{protected_ids & set(moved)}")
            check("no UNCLAIMED channel was moved",
                  not (set(moved) - claimed_ids), f"{list(set(moved) - claimed_ids)[:5]}")
            check("dry run predicted the same set the real pass moved",
                  set(dry_ids) == set(moved) or not moved,
                  f"dry={len(dry_ids)} real={len(moved)}")
            transaction.set_rollback(True)
    except Exception:
        traceback.print_exc()
        failures.append("real pass raised")
    finally:
        epg_signals.send_websocket_update = original_ws

    print("\n(4) no side effect escaped the rollback")
    check("no websocket broadcast escaped", not ws_calls, f"{len(ws_calls)}: {ws_calls[:2]}")
    check("bindings identical to before the run", snapshot() == before)

    print("\n(5) the GMT source's rendering properties are correct")
    gmt = EPGSource.objects.filter(name=GMT_SOURCE, source_type="dummy").first()
    if gmt is None:
        check("GMT source exists", False, "not found")
    else:
        expected = ecm_profiles.resolve_output_timezone(
            "UTC", inst._get_system_timezone(settings),
            settings.get("date_format", "Auto"))
        props = gmt.custom_properties or {}
        check("timezone is UTC", props.get("timezone") == "UTC", repr(props.get("timezone")))
        # Catches a transposed resolve_output_timezone(system, source) call, which
        # raises nothing and renders every time wrong while all binding checks pass.
        check("output_timezone matches the independently-computed value",
              props.get("output_timezone") == expected["output_timezone"],
              f"{props.get('output_timezone')!r} vs {expected['output_timezone']!r}")


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception during verification")

print("\n" + "=" * 70)
print(f"S2_GATE_RESULT={'FAIL' if failures else 'PASS'}")
for f in failures:
    print(f"  - {f}")
print("=" * 70)
sys.exit(1 if failures else 0)
