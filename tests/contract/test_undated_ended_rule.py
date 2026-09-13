"""The [UndatedEnded] rule must read its grace period from the setting, not a constant.

The rule hides a channel once an inferred event end has passed. If it ignored the
configured grace period the channel would disappear while the event overran, and
nothing would report that the setting had been discarded. plugin.py imports Django
at module scope and cannot be imported outside the container, so its structure is
read with ast.
"""

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PY = ROOT / "Event-Channel-Managarr" / "plugin.py"
PLUGIN_JSON = ROOT / "Event-Channel-Managarr" / "plugin.json"

SOURCE = PLUGIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _rule_branch(rule_name):
    for node in ast.walk(TREE):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == "rule_name"
                and test.comparators
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == rule_name):
            return "\n".join(ast.unparse(stmt) for stmt in node.body)
    pytest.fail(f"plugin.py has no rule branch for {rule_name}")


def test_the_rule_branch_exists():
    assert _rule_branch("UndatedEnded")


def test_the_rule_reads_the_configured_grace_period():
    branch = _rule_branch("UndatedEnded")
    assert "undated_event_grace_hours" in branch, (
        "the rule must read the grace period from settings")


def test_the_rule_reads_the_first_seen_record():
    branch = _rule_branch("UndatedEnded")
    assert "_undated_tracker" in branch


def test_the_rule_uses_the_shared_window_helper():
    branch = _rule_branch("UndatedEnded")
    assert "infer_undated_event_window" in branch, (
        "the window arithmetic belongs in ecm_parsing so it stays unit-testable")


def test_the_setting_is_declared_in_both_the_code_and_the_manifest():
    assert "undated_event_grace_hours" in SOURCE
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    ids = [field["id"] for field in manifest["fields"]]
    assert "undated_event_grace_hours" in ids, (
        "plugin.json must declare the same field id as plugin.py")


def test_the_default_hide_rules_place_the_new_rule_before_the_day_count_rule():
    defaults = next(
        node.value.value for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "DEFAULT_HIDE_RULES"
        and isinstance(node.value, ast.Constant))
    assert "[UndatedEnded]" in defaults
    assert defaults.index("[UndatedEnded]") < defaults.index("[UndatedAge:")


def test_the_rule_uses_the_shared_decision_helper():
    """The comparison against the clock belongs in ecm_parsing so it can be unit-tested.

    An assertion on the source text cannot tell a correct comparison from an inverted
    one, so the decision itself is tested in tests/unit/test_ecm_parsing.py. This holds
    the wiring: if the rule stops calling the helper, that unit coverage stops applying
    to the shipped behaviour and nothing else would say so.
    """
    branch = _rule_branch("UndatedEnded")
    assert "undated_event_has_ended" in branch


def test_the_rule_passes_the_first_seen_moment_to_the_decision_helper():
    """Without it, a channel first seen at 23:00 named for a 1:00am event is hidden
    on the very first scan that sees it, two hours before the event starts."""
    branch = _rule_branch("UndatedEnded")
    assert "first_seen_at" in branch


def test_every_shipped_default_rule_list_agrees():
    """The default rule list is written down in three places and all three ship.

    plugin.py holds the one the running plugin uses. plugin.json holds the manifest
    fallback, which is also what the release artifact carries. The bootstrap template
    seeds a fresh installation's stored settings, and because the plugin only falls back
    to its built-in default when the stored value is EMPTY, a template missing the tag
    disables the rule permanently on every installation seeded from it.
    """
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    manifest_default = next(
        field["default"] for field in manifest["fields"]
        if field["id"] == "hide_rules_priority")

    code_default = next(
        node.value.value for node in ast.walk(TREE)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "DEFAULT_HIDE_RULES"
        and isinstance(node.value, ast.Constant))

    template_path = ROOT / "config" / "ecm_settings.template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template_default = template["hide_rules_priority"]

    assert manifest_default == code_default, (
        "plugin.json's hide_rules_priority default has drifted from plugin.py")
    assert template_default == code_default, (
        "config/ecm_settings.template.json's hide_rules_priority has drifted from "
        "plugin.py, so a bootstrapped installation would never receive the new rule")


def test_the_bootstrap_template_carries_every_grace_period_setting():
    """A setting absent from the template is never written on a fresh install.

    The existing template test only asserts that template keys are a subset of the
    declared fields, which passes just as well when a setting is missing.
    """
    template_path = ROOT / "config" / "ecm_settings.template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert "undated_event_grace_hours" in template
    assert "past_date_grace_hours" in template


def test_the_rule_reports_a_timezone_it_cannot_use():
    """A substitution the operator cannot see is the failure mode this guards against.

    The rule leaves a channel visible when it cannot build an event window. That is the
    safe direction, but silent: from the operator's side a mistyped timezone looks
    exactly like a rule that has decided the event is still running. The workspace rule
    is to degrade to a sane value and make the degradation loud.
    """
    branch = _rule_branch("UndatedEnded")
    assert "_warn_undated_once" in branch


def test_the_property_resolver_reports_a_time_pattern_it_cannot_compile():
    """A pattern that does not compile is replaced by the built-in one. The operator
    then gets behaviour they did not configure, and nothing else would tell them."""
    source = PLUGIN_PY.read_text(encoding="utf-8")
    start = source.index("def _undated_event_properties")
    body = source[start:source.index("def _check_hide_rule", start)]
    assert "time_pattern_problem" in body
    assert "_warn_undated_once" in body


def test_the_property_resolver_reports_an_unreadable_program_duration():
    source = PLUGIN_PY.read_text(encoding="utf-8")
    start = source.index("def _undated_event_properties")
    body = source[start:source.index("def _check_hide_rule", start)]
    assert "program_duration" in body
    # An ABSENT property is the ordinary case and must not be reported as a mistake.
    assert "if raw_duration is not None:" in body, (
        "the resolver must tell an absent program duration apart from an unreadable one")


def test_the_warning_set_is_cleared_at_the_start_of_every_scan():
    """Warning once ever would hide the problem from every run after the first."""
    source = PLUGIN_PY.read_text(encoding="utf-8")
    assert "self._undated_warned = set()" in source


# --- the named-day anchor -----------------------------------------------------------
# The rule anchors an event on the date the channel was first seen. When the name states
# a day of the week that is the better anchor, and the decision for both halves lives in
# ecm_parsing so it can be unit-tested. These assertions hold the wiring only; whether the
# anchor is computed correctly is settled in tests/unit/test_ecm_parsing.py.


def test_the_rule_reads_the_day_the_name_states():
    branch = _rule_branch("UndatedEnded")
    assert "extract_named_day_of_week" in branch, (
        "the rule must read the day from the name through the shared helper")


def test_the_rule_resolves_the_named_day_to_a_date():
    branch = _rule_branch("UndatedEnded")
    assert "resolve_named_day_date" in branch, (
        "resolving the day to a date belongs in ecm_parsing so it stays unit-testable")


def _rule_branch_nodes(rule_name):
    """The statements of a rule's own branch, as syntax nodes rather than text.

    Asserting on the unparsed text of a branch cannot tell whether a value reaches the
    call that uses it. The rule reverts entirely by passing a different variable to the
    window helper, and a text search for the helper names still passes.
    """
    for node in ast.walk(TREE):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == "rule_name"
                and test.comparators
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == rule_name):
            return node.body
    pytest.fail(f"plugin.py has no rule branch for {rule_name}")


def _window_first_argument(body):
    """The name the rule passes to infer_undated_event_window as its event date."""
    for node in body:
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "infer_undated_event_window":
                assert inner.args, "the window helper is called with no positional arguments"
                first = inner.args[0]
                assert isinstance(first, ast.Name), (
                    "the event date passed to the window helper must be a named variable")
                return first.id
    pytest.fail("the rule does not call infer_undated_event_window")


def _assignments(body):
    """Map every assigned name in the branch to the list of values assigned to it."""
    found = {}
    for node in body:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign):
                for target in inner.targets:
                    if isinstance(target, ast.Name):
                        found.setdefault(target.id, []).append(inner.value)
    return found


def _calls_in(value):
    names = set()
    for inner in ast.walk(value):
        if isinstance(inner, ast.Call):
            func = inner.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def test_the_anchor_reaches_the_window_helper():
    """The date the resolver returns must be the date the window is built from.

    Without this, changing one argument back to the first-seen date reverts the whole
    change and every test still passes. That mutation was run and it did.
    """
    body = _rule_branch_nodes("UndatedEnded")
    argument = _window_first_argument(body)
    assigned = _assignments(body)

    reached = False
    seen = set()
    pending = [argument]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for value in assigned.get(name, []):
            if "resolve_named_day_date" in _calls_in(value):
                reached = True
            if isinstance(value, ast.Name):
                pending.append(value.id)
    assert reached, (
        f"the value passed to infer_undated_event_window ({argument!r}) is never "
        "assigned from resolve_named_day_date, so the named day anchor is not used")


def test_the_anchor_is_only_adopted_when_the_resolver_returns_one():
    """The resolver returns None for anything it cannot use. Adopting that
    unconditionally would pass None to the window helper and lose the rule entirely."""
    body = _rule_branch_nodes("UndatedEnded")
    argument = _window_first_argument(body)

    assigned = _assignments(body)
    resolver_names = {name for name, values in assigned.items()
                      if any("resolve_named_day_date" in _calls_in(v) for v in values)}
    assert resolver_names, "nothing in the rule is assigned from resolve_named_day_date"

    guarded = False
    for node in body:
        for inner in ast.walk(node):
            if not isinstance(inner, ast.If):
                continue
            tested = {n.id for n in ast.walk(inner.test) if isinstance(n, ast.Name)}
            if not (tested & resolver_names):
                continue
            for stmt in inner.body:
                for assign in ast.walk(stmt):
                    if (isinstance(assign, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == argument
                                    for t in assign.targets)):
                        guarded = True
    assert guarded, (
        f"{argument!r} is assigned the resolved anchor without a conditional that "
        f"tests the resolver's own result, so a None anchor would reach the window")


def test_the_rule_still_falls_back_to_the_first_seen_date():
    """A name stating no day, or one the extractor will not read, must keep the
    behaviour the rule had before the anchor existed rather than lose its window."""
    body = _rule_branch_nodes("UndatedEnded")
    argument = _window_first_argument(body)
    assigned = _assignments(body)
    assert any(isinstance(value, ast.Name) and value.id == "first_seen"
               for value in assigned.get(argument, [])), (
        f"{argument!r} is never initialised from first_seen, so a name stating no day "
        "has no event date to fall back to")


def test_the_named_day_helpers_are_declared_in_the_parsing_module():
    parsing = (ROOT / "Event-Channel-Managarr" / "ecm_parsing.py").read_text(encoding="utf-8")
    assert "def extract_named_day_of_week(" in parsing
    assert "def resolve_named_day_date(" in parsing
