"""Unit tests for ecm_parsing.regex_alternative_problems.

Validate Configuration used to check only that each regex settings field
compiles. A user's Regex: Channel Names to Ignore field compiled perfectly and
was still wrong: they had typed four channel GROUP names separated by the pipe
character, but each of those group names already contained " | ", so the
pattern the engine saw was

    USA | Sports |USA | Kids |USA | Documentary |USA | Entertainment

whose alternatives are "USA ", " Sports ", "USA ", " Kids " and so on. Any
channel name containing the text "USA " was skipped completely.

The signal is the surrounding whitespace, not the length. Every alternative in
that pattern carries a leading or trailing space, because each came from
splitting on a pipe that had spaces around it. A length rule would instead
flag "NFL|NHL|NBA", where three-character alternatives are exactly what the
operator meant.
"""

import ecm_parsing  # resolves via pyproject.toml pythonpath

problems = ecm_parsing.regex_alternative_problems


# --- patterns that must produce no warning -------------------------------------

def test_a_pattern_with_no_alternation_is_fine():
    assert problems("^NFL REDZONE") == []


def test_short_alternatives_without_whitespace_are_fine():
    """Deliberately allowed: league codes are the normal way to write this."""
    assert problems("NFL|NHL|NBA") == []


def test_the_reporters_force_visible_pattern_is_fine():
    assert problems("NFL REDZONE|NFL NETWORK|NHL NETWORK") == []


def test_an_empty_pattern_is_fine():
    assert problems("") == []


def test_none_is_fine():
    assert problems(None) == []


def test_internal_spaces_are_fine():
    assert problems("NFL REDZONE|NHL NETWORK") == []


# --- the whitespace signal ------------------------------------------------------

def test_a_trailing_space_is_reported():
    found = problems("USA |NCAAF")
    assert len(found) == 1
    assert "USA " in found[0]


def test_a_leading_space_is_reported():
    found = problems("NCAAF| Sports")
    assert len(found) == 1
    assert "Sports" in found[0]


def test_the_reporters_ignore_pattern_is_reported():
    """The pattern that caused this feature. Every alternative is flagged."""
    found = problems("USA | Sports |USA | Kids |USA | Documentary |USA | Entertainment")
    assert len(found) == 8


def test_each_report_names_the_alternative_and_says_what_to_do():
    found = problems("USA |NCAAF")
    assert "USA " in found[0]
    assert "space" in found[0].lower()


# --- the empty-alternative signal -----------------------------------------------

def test_an_empty_alternative_is_reported():
    found = problems("NFL||NHL")
    assert len(found) == 1
    assert "empty" in found[0].lower()
    assert "every" in found[0].lower()


def test_a_leading_empty_alternative_is_reported():
    assert len(problems("|NFL")) == 1


def test_a_trailing_empty_alternative_is_reported():
    assert len(problems("NFL|")) == 1


def test_a_whitespace_only_alternative_counts_as_empty_not_as_whitespace():
    found = problems("NFL| |NHL")
    assert len(found) == 1
    assert "empty" in found[0].lower()


# --- alternation inside a group -------------------------------------------------

def test_alternation_inside_a_group_is_examined_too():
    """`^(USA |NCAAF)` has the same defect as `USA |NCAAF`."""
    found = problems("^(USA |NCAAF)$")
    assert len(found) == 1
    assert "USA " in found[0]


def test_an_escaped_pipe_is_not_an_alternation():
    r"""A channel name really can contain a pipe, written \| in the pattern."""
    assert problems(r"USA \| NCAAF") == []


def test_a_pipe_inside_a_character_class_is_not_an_alternation():
    assert problems("[|]NFL") == []


# --- shape of the result --------------------------------------------------------

def test_the_result_is_a_list_of_strings():
    found = problems("USA |NCAAF")
    assert isinstance(found, list)
    assert all(isinstance(item, str) for item in found)


def test_a_pattern_that_does_not_compile_produces_no_problems():
    """Compilation is reported separately; this must not raise or double-report."""
    assert problems("NFL(") == []


def test_reports_are_capped_so_one_bad_field_cannot_flood_the_readout():
    found = problems("|".join(f"USA{n} " for n in range(50)))
    assert len(found) <= 10


# --- the short summary line that fits in Dispatcharr's toast --------------------
#
# Dispatcharr clips an action toast at roughly 280 characters, from the middle,
# with no visual marker that anything was cut. The full descriptions above are
# written to the log; this one line is what the operator actually sees.

summary = ecm_parsing.regex_alternative_summary


def test_a_clean_pattern_has_no_summary():
    assert summary("NFL REDZONE|NFL NETWORK") is None


def test_an_empty_pattern_has_no_summary():
    assert summary("") is None


def test_the_summary_names_the_count_and_the_first_offending_alternative():
    line = summary("USA | Sports |USA | Kids ")
    assert "USA " in line
    assert "4" in line


def test_the_summary_fits_in_a_toast():
    line = summary("USA | Sports |USA | Kids |USA | Documentary |USA | Entertainment")
    assert len(line) <= 120, f"too long for a clipped toast: {len(line)}"


def test_the_summary_stays_short_even_for_a_very_long_alternative():
    line = summary("USA " + "x" * 300 + " |NCAAF")
    assert len(line) <= 120


def test_a_single_problem_reads_naturally():
    line = summary("USA |NCAAF")
    assert line.startswith("1 ")


def test_the_summary_covers_the_empty_alternative_case():
    line = summary("NFL||NHL")
    assert line is not None
    assert "empty" in line.lower()
