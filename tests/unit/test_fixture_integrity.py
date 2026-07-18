# tests/unit/test_fixture_integrity.py
"""The channel-name fixture is the corpus every routing assertion runs against.

If it drifts or is silently corrupted, every downstream assertion becomes
meaningless, so its shape is pinned here separately from any routing logic.

The BOM checks are not paranoia: PowerShell 5.1 cannot write BOM-free UTF-8 via
Set-Content, and a leading U+FEFF is category Cf -- not whitespace -- so it
survives .strip() and is invisible to both the eye and a naive blank-line check.
"""

from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "us_ppv_channel_names.txt"

# Measured live 2026-07-18. These pin the CORPUS, not the routing rules.
EXPECTED_TOTAL = 278
EXPECTED_FAMILIES = {
    "dazn (GMT)": 48,
    "legacy PPV EVENT": 70,
    "legacy LIVE EVENT": 34,
    "idle NO EVENT slot": 51,
    "UFC": 55,
    "Boxing": 8,
    "BOX OFFICE static": 8,
    "#### header ####": 4,
}

# Measured live 2026-07-18: 11 UFC numbered-slot names ("UFC 00" .. "UFC 10")
# are each reused across 4-5 distinct channel rows (verified by re-querying
# the ORM for distinct ids per name -- these are genuinely separate channels,
# not a capture artifact). Consistent with this operator's documented
# failover/backup-channel architecture (multiple physical channels sharing
# one template display name). Pinned exactly, like EXPECTED_FAMILIES above,
# so real drift is still caught -- this is NOT a blanket "duplicates are fine."
EXPECTED_DUPLICATE_COUNTS = {
    "UFC 00 : UFC FREEDOM 250: PRE-SHOW start:2026-06-14 23:55:00 stop:2026-06-15 02:00:00": 4,
    "UFC 01 : UFC FREEDOM 250: POST-FIGHT PRESS CONFERENCE start:2026-06-15 06:55:00 stop:2026-06-15 09:00:00": 4,
    "UFC 02: UFC FREEDOM 250: TOPURIA VS GAETHJE start:2026-06-15 01:55:00 stop:2026-06-15 06:15:00": 4,
    "UFC 03: UFC FREEDOM 250: PRELIMS start:2026-06-15 01:55:00 stop:2026-06-15 04:00:00": 4,
    "UFC 04: UFC FIGHT NIGHT: DELLA MADDALENA VS PRATES start:2026-05-02 12:55:00 stop:2026-05-02 16:15:00": 4,
    "UFC 05: FIGHT CLUB RUSH 29 start:2026-05-02 17:55:00 stop:2026-05-02 23:00:00": 4,
    "UFC 06: UFC 326: FIGHT PASS PRELIMS start:2026-03-07 22:55:00 stop:2026-03-08 01:00:00": 4,
    "UFC 07: UFC 323: PRELIMS start:2025-12-07 01:55:00 stop:2025-12-07 04:00:00": 4,
    "UFC 08 : LUX 056 start:2025-11-22 03:55:00 stop:2025-11-22 09:00:00": 4,
    "UFC 09:": 5,
    "UFC 10:": 5,
}


def _names():
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _classify(names):
    return {
        "dazn (GMT)": [n for n in names if "(GMT)" in n],
        "legacy PPV EVENT": [n for n in names if n.startswith("PPV EVENT")],
        "legacy LIVE EVENT": [n for n in names if n.startswith("LIVE EVENT")],
        "idle NO EVENT slot": [n for n in names if n.startswith("NO EVENT STREAMING NOW")],
        "UFC": [n for n in names if n.startswith("UFC")],
        "Boxing": [n for n in names if n.startswith("Boxing")],
        "BOX OFFICE static": [n for n in names if "BOX OFFICE" in n],
        "#### header ####": [n for n in names if n.startswith("#")],
    }


def test_fixture_size():
    names = _names()
    assert len(names) == EXPECTED_TOTAL, f"expected {EXPECTED_TOTAL}, got {len(names)}"


def test_fixture_has_no_bom():
    """A BOM survives .strip() and would silently break the ^-anchored selector
    on the first name, shifting routing counts by one with no visible cause."""
    raw = FIXTURE.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "fixture starts with a UTF-8 BOM"
    assert not _names()[0].startswith("﻿")


def test_fixture_has_no_blank_or_whitespace_only_lines():
    names = _names()
    bad = [i for i, n in enumerate(names) if not n.strip("﻿ \t")]
    assert not bad, f"blank/BOM-only lines at indices {bad}"


def test_fixture_has_no_stray_whitespace():
    dirty = [n for n in _names() if n != n.strip()]
    assert not dirty, f"names with leading/trailing whitespace: {dirty[:5]!r}"


def test_fixture_duplicates_match_known_set():
    """The live corpus genuinely contains repeated names: 11 UFC numbered-slot
    templates ("UFC 00" .. "UFC 10") each appear on 4-5 distinct channel rows
    (verified against the ORM by id, not just by name -- see
    EXPECTED_DUPLICATE_COUNTS). That is real operator data (duplicate
    failover/backup channels sharing one display name), not a capture defect,
    so asserting zero duplicates would be false. Pin the exact known set
    instead, so an UNEXPECTED new duplicate (a real capture error, e.g. a
    doubled read) still fails loudly."""
    names = _names()
    from collections import Counter

    counts = Counter(names)
    actual = {n: c for n, c in counts.items() if c > 1}
    assert actual == EXPECTED_DUPLICATE_COUNTS, (
        f"duplicate-name drift: {actual} != {EXPECTED_DUPLICATE_COUNTS}"
    )


def test_fixture_family_breakdown_is_exact():
    """The spec's family table is the durable knowledge artifact this project
    exists to create. Pin every family, not just one."""
    actual = {k: len(v) for k, v in _classify(_names()).items()}
    assert actual == EXPECTED_FAMILIES, f"family drift: {actual} != {EXPECTED_FAMILIES}"


def test_families_account_for_every_name():
    names = _names()
    fams = _classify(names)
    covered = set().union(*fams.values())
    uncovered = set(names) - covered
    assert not uncovered, f"names in no known family: {sorted(uncovered)[:5]}"
