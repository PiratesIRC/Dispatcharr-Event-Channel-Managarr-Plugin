"""Unit tests for age-based cleanup of this plugin's CSV exports.

/data/exports IS SHARED. Measured on the live installation, six plugins write
there: stream_mapparr, epg_janitor, event_channel_managarr, lineuparr,
iptv_checker and channel_mapparr, 90 files between them. Selecting by suffix
alone, or by a glob of *.csv, would delete other projects' data, and with a
seven day rule that is most of the directory on the first run.

This plugin owns TWO prefixes, not one: event_channel_managarr_ for the scan
exports and epg_removal_ for the "Remove EPG from Hidden Channels" export
(plugin.py writes both). Checked against every sibling project: none of them
writes either prefix, and EPG-Janitor's own removal export is named
epg_janitor_removal_, which does not start with epg_removal_.

Every test below that exercises the age rule or the off-by-default rule uses
SEVERAL old files on purpose. With a single file the "one always survives" rule
keeps it regardless, so such a test passes with the guard it names deleted and
proves nothing.
"""

import ecm_parsing  # resolves via pyproject.toml pythonpath

to_delete = ecm_parsing.csv_exports_to_delete
prune = ecm_parsing.prune_csv_exports

DAY = 86400.0
NOW = 1_800_000_000.0


def aged(days):
    """A modification time that many days before NOW."""
    return NOW - days * DAY


def mine(n, days, prefix="event_channel_managarr_applied_"):
    return (f"{prefix}{n}.csv", aged(days))


# --- the shared directory: only our own files may be selected -------------------

def test_other_plugins_files_are_never_deleted():
    entries = [
        ("stream_mapparr_sorted_20260101_000000.csv", aged(400)),
        ("epg_janitor_automatch_applied_20260101_000000.csv", aged(400)),
        ("lineuparr_match_applied_20260101_000000.csv", aged(400)),
        ("iptv_checker_results_20260101_000000.csv", aged(400)),
        ("channel_mapparr_seed_preview_20260101_000000.csv", aged(400)),
        ("epg_janitor_removal_20260101_000000.csv", aged(400)),
        mine("a", 400), mine("b", 400), mine("c", 400),
    ]
    result = to_delete(entries, 7, NOW)
    assert all(name.startswith(("event_channel_managarr_", "epg_removal_"))
               for name in result), f"a foreign file was selected: {result}"


def test_a_sibling_removal_export_is_not_mistaken_for_ours():
    """epg_janitor_removal_ must not match our epg_removal_ prefix."""
    entries = [("epg_janitor_removal_1.csv", aged(400)),
               ("epg_janitor_removal_2.csv", aged(400)),
               ("epg_removal_1.csv", aged(400)),
               ("epg_removal_2.csv", aged(400))]
    result = to_delete(entries, 7, NOW)
    assert not any(n.startswith("epg_janitor_") for n in result)


def test_both_of_our_own_prefixes_are_pruned():
    entries = [mine("a", 400), mine("b", 400),
               ("epg_removal_1.csv", aged(400)), ("epg_removal_2.csv", aged(400))]
    result = to_delete(entries, 7, NOW)
    assert any(n.startswith("event_channel_managarr_") for n in result)
    assert any(n.startswith("epg_removal_") for n in result)


def test_a_non_csv_file_with_our_prefix_is_left_alone():
    entries = [("event_channel_managarr_ledger.jsonl", aged(400)),
               ("event_channel_managarr_settings.json", aged(400)),
               mine("a", 400), mine("b", 400)]
    result = to_delete(entries, 7, NOW)
    assert all(n.endswith(".csv") for n in result), result
    assert "event_channel_managarr_ledger.jsonl" not in result


def test_an_empty_directory_deletes_nothing():
    assert to_delete([], 7, NOW) == []


def test_a_directory_of_only_other_plugins_files_deletes_nothing():
    entries = [("stream_mapparr_1.csv", aged(400)), ("lineuparr_2.csv", aged(400))]
    assert to_delete(entries, 7, NOW) == []


# --- off unless configured ------------------------------------------------------
# Several old files in each, so the "one always survives" rule cannot mask a
# missing off-by-default check.

def test_zero_days_deletes_nothing():
    entries = [mine("a", 400), mine("b", 400), mine("c", 400), mine("d", 400)]
    assert to_delete(entries, 0, NOW) == []


def test_a_negative_number_deletes_nothing():
    entries = [mine("a", 400), mine("b", 400), mine("c", 400), mine("d", 400)]
    assert to_delete(entries, -5, NOW) == []


def test_none_deletes_nothing():
    entries = [mine("a", 400), mine("b", 400), mine("c", 400), mine("d", 400)]
    assert to_delete(entries, None, NOW) == []


def test_a_blank_string_deletes_nothing():
    entries = [mine("a", 400), mine("b", 400), mine("c", 400), mine("d", 400)]
    assert to_delete(entries, "", NOW) == []
    assert to_delete(entries, "   ", NOW) == []


def test_unparseable_input_deletes_nothing():
    entries = [mine("a", 400), mine("b", 400), mine("c", 400), mine("d", 400)]
    for bad in ("soon", "7 days", [], {}, object()):
        assert to_delete(entries, bad, NOW) == [], f"{bad!r} should delete nothing"


def test_a_numeric_string_is_accepted():
    """Dispatcharr can hand back a number field as a string."""
    entries = [mine("a", 400), mine("b", 400), mine("c", 400), mine("d", 400)]
    assert len(to_delete(entries, "7", NOW)) == 3


# --- the age rule ---------------------------------------------------------------

def test_files_younger_than_the_limit_are_kept():
    entries = [mine("a", 1), mine("b", 2), mine("c", 3), mine("d", 4)]
    assert to_delete(entries, 7, NOW) == []


def test_files_older_than_the_limit_are_deleted():
    entries = [mine("a", 30), mine("b", 20), mine("c", 10), mine("new", 0)]
    assert sorted(to_delete(entries, 7, NOW)) == [
        "event_channel_managarr_applied_a.csv",
        "event_channel_managarr_applied_b.csv",
        "event_channel_managarr_applied_c.csv",
    ]


def test_exactly_the_limit_is_not_older_than_the_limit():
    """Strict comparison. Several files so the survivor rule cannot mask it."""
    entries = [mine("a", 7), mine("b", 7), mine("c", 7), mine("d", 7)]
    assert to_delete(entries, 7, NOW) == []


def test_a_moment_past_the_limit_is_older():
    entries = [(f"event_channel_managarr_applied_{n}.csv", NOW - 7 * DAY - 1)
               for n in "abcd"]
    assert len(to_delete(entries, 7, NOW)) == 3


# --- the file just written ------------------------------------------------------

def test_the_protected_file_is_never_deleted():
    entries = [mine("a", 400), mine("b", 400), mine("c", 400),
               ("event_channel_managarr_applied_new.csv", aged(400))]
    result = to_delete(entries, 7, NOW, protect="event_channel_managarr_applied_new.csv")
    assert "event_channel_managarr_applied_new.csv" not in result
    assert len(result) == 3


def test_the_protected_file_is_the_survivor_even_when_it_is_the_oldest():
    entries = [mine("new", 400), mine("a", 10), mine("b", 20), mine("c", 30)]
    result = to_delete(entries, 7, NOW,
                       protect="event_channel_managarr_applied_new.csv")
    assert "event_channel_managarr_applied_new.csv" not in result
    assert len(result) == 3, "every other old file should still go"


def test_a_protected_name_that_is_not_present_changes_nothing():
    entries = [mine("a", 400), mine("b", 400), mine("c", 400)]
    result = to_delete(entries, 7, NOW, protect="not_in_this_directory.csv")
    assert len(result) == 2


# --- one of ours always survives ------------------------------------------------

def test_when_every_file_is_old_the_newest_survives():
    entries = [mine("old", 400), mine("older", 500), mine("newest", 8)]
    result = to_delete(entries, 7, NOW)
    assert "event_channel_managarr_applied_newest.csv" not in result
    assert len(result) == 2


def test_a_single_old_file_is_kept():
    assert to_delete([mine("only", 400)], 7, NOW) == []


def test_the_survivor_rule_counts_our_files_only():
    """A foreign file being newer must not become the survivor and doom ours."""
    entries = [("stream_mapparr_brand_new.csv", NOW), mine("a", 400), mine("b", 500)]
    result = to_delete(entries, 7, NOW)
    assert len(result) == 1, "one of ours must survive, whatever else is in there"


# --- a modification time that is not a number -----------------------------------

def test_a_file_whose_mtime_is_not_a_number_is_skipped():
    entries = [("event_channel_managarr_applied_bad.csv", "not a number"),
               mine("a", 400), mine("b", 400), mine("c", 400)]
    result = to_delete(entries, 7, NOW)
    assert "event_channel_managarr_applied_bad.csv" not in result


def test_a_bad_mtime_does_not_become_the_survivor():
    """Keeping it makes every comparison false, so it wins "which is newest".

    Every real file would then be deleted and the unreadable one kept, which is
    the opposite of the rule. This survived a first round of tests elsewhere.
    """
    entries = [("event_channel_managarr_applied_bad.csv", None),
               mine("a", 400), mine("b", 500), mine("c", 600)]
    result = to_delete(entries, 7, NOW)
    assert len(result) == 2, f"one real file must survive, got {result}"
    assert "event_channel_managarr_applied_a.csv" not in result, (
        "the newest real file should be the survivor")


def test_a_not_a_number_float_is_skipped():
    entries = [("event_channel_managarr_applied_nan.csv", float("nan")),
               mine("a", 400), mine("b", 500), mine("c", 600)]
    result = to_delete(entries, 7, NOW)
    assert "event_channel_managarr_applied_nan.csv" not in result
    assert len(result) == 2


def test_every_mtime_unreadable_deletes_nothing():
    entries = [("event_channel_managarr_applied_a.csv", None),
               ("event_channel_managarr_applied_b.csv", "x")]
    assert to_delete(entries, 7, NOW) == []


# --- the result shape -----------------------------------------------------------

def test_the_result_is_sorted_and_has_no_duplicates():
    entries = [mine(n, 400) for n in ("d", "a", "c", "b")]
    result = to_delete(entries, 7, NOW)
    assert result == sorted(result)
    assert len(result) == len(set(result))


# --- the wrapper that touches the filesystem -------------------------------------
#
# The wrapper takes its directory calls as arguments so both halves are testable
# with no filesystem, the same way compile_pattern takes an injectable engine.

class FakeDir:
    def __init__(self, files, fail_on=(), listdir_error=None, mtime_error=()):
        self.files = dict(files)
        self.fail_on = set(fail_on)
        self.listdir_error = listdir_error
        self.mtime_error = set(mtime_error)
        self.removed = []

    def listdir(self, directory):
        if self.listdir_error:
            raise self.listdir_error
        return list(self.files)

    def getmtime(self, path):
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if name in self.mtime_error:
            raise OSError("vanished between listing and asking")
        return self.files[name]

    def remove(self, path):
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if name in self.fail_on:
            raise OSError("permission denied")
        self.removed.append(name)
        del self.files[name]


def run_prune(fake, days=7, protect=None):
    return prune("/data/exports", days, now=NOW, protect=protect,
                 listdir=fake.listdir, getmtime=fake.getmtime, remove=fake.remove)


def test_the_wrapper_deletes_and_counts():
    fake = FakeDir({f"event_channel_managarr_applied_{n}.csv": aged(400)
                    for n in "abcd"})
    assert run_prune(fake) == 3
    assert len(fake.removed) == 3


def test_the_wrapper_leaves_other_plugins_files_alone():
    fake = FakeDir({"stream_mapparr_a.csv": aged(400),
                    "lineuparr_b.csv": aged(400),
                    "event_channel_managarr_applied_a.csv": aged(400),
                    "event_channel_managarr_applied_b.csv": aged(400)})
    assert run_prune(fake) == 1
    assert fake.removed == ["event_channel_managarr_applied_a.csv"]


def test_the_wrapper_returns_zero_when_the_directory_cannot_be_listed():
    fake = FakeDir({}, listdir_error=OSError("no such directory"))
    assert run_prune(fake) == 0


def test_the_wrapper_does_not_raise_when_a_delete_fails():
    files = {f"event_channel_managarr_applied_{n}.csv": aged(400) for n in "abcd"}
    fake = FakeDir(files, fail_on=["event_channel_managarr_applied_a.csv"])
    assert run_prune(fake) == 2, "the failure must not stop the others or raise"


def test_the_wrapper_does_not_raise_when_every_delete_fails():
    files = {f"event_channel_managarr_applied_{n}.csv": aged(400) for n in "abcd"}
    fake = FakeDir(files, fail_on=list(files))
    assert run_prune(fake) == 0


def test_the_wrapper_skips_a_file_that_vanished_between_listing_and_asking():
    files = {f"event_channel_managarr_applied_{n}.csv": aged(400) for n in "abcd"}
    fake = FakeDir(files, mtime_error=["event_channel_managarr_applied_a.csv"])
    assert run_prune(fake) == 2


def test_the_wrapper_is_off_when_not_configured():
    files = {f"event_channel_managarr_applied_{n}.csv": aged(400) for n in "abcd"}
    fake = FakeDir(files)
    assert run_prune(fake, days=0) == 0
    assert run_prune(fake, days=None) == 0
    assert fake.removed == []


def test_the_wrapper_never_deletes_the_file_just_written():
    files = {f"event_channel_managarr_applied_{n}.csv": aged(400) for n in "abcd"}
    fake = FakeDir(files)
    assert run_prune(fake, protect="event_channel_managarr_applied_a.csv") == 3
    assert "event_channel_managarr_applied_a.csv" not in fake.removed
