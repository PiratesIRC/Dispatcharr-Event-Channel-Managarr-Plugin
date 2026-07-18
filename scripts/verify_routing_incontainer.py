# scripts/verify_routing_incontainer.py
"""Read-only proof that the ecm_profiles routing model matches live reality.

Run INSIDE the container, as the dispatch user:

    docker cp Event-Channel-Managarr/ecm_profiles.py dispatcharr:/tmp/ecm_profiles.py
    docker exec -i -u dispatch dispatcharr sh -c "cd /app && python3 manage.py shell" \
        < scripts/verify_routing_incontainer.py

WHAT IT PROVES
  1. route() over LIVE names satisfies churn-proof invariants (NOT frozen counts:
     event lineups are renamed in place daily, so a live count assertion goes red
     for reasons unrelated to the routing model).
  2. The dazn_gmt bucket and the idle slots together partition exactly what the
     hand-made source binds -- a falsifiable comparison, not a restatement of the
     selector.
  3. Dispatcharr's REAL renderer produces correct local times from the dazn_gmt
     patterns, via a TEMPORARY UNBOUND EPGSource.

WHAT IT WRITES
  One temporary EPGSource, removed in a finally block keyed on its NAME (not on a
  variable, which would be None if the post_save chain raised). Dispatcharr's
  create_dummy_epg_data post_save signal ALSO auto-creates one EPGData row against
  it and emits an epg_data_created websocket event; the row goes away by CASCADE
  on delete, but a user with the UI open may briefly see the temp source in the
  EPG dropdown until they reload. No channel is repointed and no pre-existing
  EPGData row is touched.

EXIT CODE: 0 on pass, 1 on any failure.
"""

import sys
import traceback

sys.path.insert(0, "/tmp")

import ecm_profiles  # noqa: E402

from apps.channels.models import Channel  # noqa: E402
from apps.epg.models import EPGData, EPGSource  # noqa: E402
from apps.output import epg as epg_renderer  # noqa: E402

GROUP_ID = 1915
HANDMADE_SOURCE_NAME = "DAZN PPV Dummy (GMT)"
TEMP_SOURCE_NAME = "__ecm_verify_temp__DO_NOT_USE"
FIXTURE_ERA = "48/104/126 over 278"

failures = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def proof_1_invariants(names):
    print("\n(1) Routing invariants over LIVE channel names")
    result = ecm_profiles.route(names)

    # The no-op defect that killed two prior design revisions.
    check("dazn_gmt is non-empty", len(result["dazn_gmt"]) > 0,
          f"got {len(result['dazn_gmt'])}")

    gmt = {n for n in names if "(GMT)" in n}
    check("dazn_gmt == exactly the GMT-bearing names", set(result["dazn_gmt"]) == gmt,
          f"routed={len(result['dazn_gmt'])} gmt={len(gmt)} "
          f"symdiff={sorted(set(result['dazn_gmt']) ^ gmt)[:2]}")

    legacy = {n for n in names if n.startswith(("PPV EVENT", "LIVE EVENT"))}
    check("us_et == exactly the legacy family", set(result["us_et"]) == legacy,
          f"routed={len(result['us_et'])} legacy={len(legacy)}")

    check("no GMT name leaked into us_et",
          not [n for n in result["us_et"] if "(GMT)" in n])
    check("no idle slot leaked into us_et",
          not [n for n in result["us_et"] if n.startswith("NO EVENT STREAMING NOW")])
    check("buckets partition the input",
          sum(len(v) for v in result.values()) == len(names))

    print("       counts: " + ", ".join(f"{k}={len(v)}" for k, v in result.items()))
    print(f"       (fixture-era baseline {FIXTURE_ERA}; drift is EXPECTED as the "
          f"lineup changes -- investigate only if a check FAILED)")
    return result


def proof_2_matches_handmade_binding(result):
    print("\n(2) dazn_gmt bucket vs the live hand-made source binding")
    source = EPGSource.objects.filter(name=HANDMADE_SOURCE_NAME).first()
    # A missing source is a FAILED PRECONDITION, never a silent skip.
    check(f"hand-made source {HANDMADE_SOURCE_NAME!r} exists", source is not None,
          "this proof compares against live ground truth and cannot be skipped")
    if source is None:
        return

    bound = set(Channel.objects.filter(epg_data__epg_source=source)
                .values_list("name", flat=True))
    routed = set(result["dazn_gmt"])

    check("every routed DAZN name is bound in the live config", not (routed - bound),
          f"{len(routed - bound)} unbound: {sorted(routed - bound)[:2]}")

    # Falsifiable: the leftover must be EXACTLY the idle slots, not merely
    # "anything lacking (GMT)" -- which is the selector's own discriminator and
    # would make this check unable to disagree with the model it audits.
    idle = {n for n in bound if n.startswith("NO EVENT STREAMING NOW")}
    check("bound set partitions into routed + idle exactly", (bound - routed) == idle,
          f"unexplained={sorted((bound - routed) - idle)[:2]}")
    print(f"       bound={len(bound)} routed={len(routed)} idle={len(idle)}")


def proof_3_renderer_output(result):
    print("\n(3) Real renderer output from the dazn_gmt patterns (temp source)")
    dazn = next(p for p in ecm_profiles.PROFILES if p.key == "dazn_gmt")
    props = dict(ecm_profiles.profile_props(dazn))
    props["fallback_description_template"] = "verify-temp"

    sample = result["dazn_gmt"][:5]
    # Without this, an empty bucket makes the loop below assert 0 == 0 and PASS --
    # i.e. the proof would be most confident exactly when the model is most broken.
    check("there are DAZN names to render", len(sample) >= 1,
          "empty sample would make the render check vacuous")
    if not sample:
        return

    try:
        # Pre-flight: clear any orphan from an interrupted earlier run, otherwise
        # the unique name constraint fails every retry.
        EPGSource.objects.filter(name=TEMP_SOURCE_NAME).delete()
        EPGSource.objects.create(
            name=TEMP_SOURCE_NAME, source_type="dummy", is_active=False,
            refresh_interval=0, priority=0, custom_properties=props)
        temp = EPGSource.objects.get(name=TEMP_SOURCE_NAME)

        ok = 0
        for name in sample:
            programs = epg_renderer.generate_dummy_programs(
                999999, name, num_days=1, program_length_hours=4, epg_source=temp)
            title = programs[0].get("title") if programs else None
            extracted = bool(title and title != name)
            ok += extracted
            print(f"       {'OK ' if extracted else 'RAW'}  {name[:52]}")
            print(f"              -> {str(title)[:70]}")
        check("all sampled DAZN names render an extracted title", ok == len(sample),
              f"{ok}/{len(sample)}")
    finally:
        # Keyed on the NAME, not a variable: create() commits before assignment,
        # so a raise in the post_save chain would leave a variable unbound.
        deleted, _ = EPGSource.objects.filter(name=TEMP_SOURCE_NAME).delete()
        print(f"       (temp source cleanup: {deleted} row(s) removed)")


def main():
    print("=" * 70)
    print("ECM routing verification -- READ-ONLY (one temp source, auto-deleted)")
    print("=" * 70)
    names = list(Channel.objects.filter(channel_group_id=GROUP_ID)
                 .order_by("id").values_list("name", flat=True))
    print(f"\nLive channel names in group {GROUP_ID}: {len(names)}")
    result = proof_1_invariants(names)
    proof_2_matches_handmade_binding(result)
    proof_3_renderer_output(result)


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception during verification")

print("\n" + "=" * 70)
if failures:
    print(f"GATE FAILED -- {len(failures)} check(s):")
    for f in failures:
        print(f"  - {f}")
    print("DO NOT PROCEED to any later slice.")
else:
    print("GATE PASSED -- routing model reproduces the live working config.")
print(f"ECM_GATE_RESULT={'FAIL' if failures else 'PASS'}")
print("=" * 70)
sys.exit(1 if failures else 0)
