"""One structural test for the identity-name wiring in plugin.py.

The decision itself lives in ecm_parsing.regex_target_name and is unit-tested there.
This file only asserts that the scan loop reaches for it, because a structural test
cannot express that a value reaches a call before the call happens.
"""

import ast

import pytest


def _method_ast(plugin_source, name):
    tree = ast.parse(plugin_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in plugin.py")

def _method_ast(plugin_source, name):
    tree = ast.parse(plugin_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in plugin.py")


@pytest.mark.parametrize("regex_var", ["regex_ignore", "regex_force_visible"])
def test_scan_loop_searches_the_identity_name(plugin_source, regex_var):
    """A string search over the whole method would be satisfied by any mention of the
    variable, so this asserts on the argument of the search call itself."""
    method = _method_ast(plugin_source, "_scan_and_update_channels")
    searched = []
    for node in ast.walk(method):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "search"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == regex_var
                and node.args):
            searched.append(node.args[0])
    assert searched, f"{regex_var}.search(...) is not called in _scan_and_update_channels"
    for arg in searched:
        assert isinstance(arg, ast.Name) and arg.id == "identity_name", \
            f"{regex_var}.search reads {ast.dump(arg)}, not identity_name"


def test_scan_loop_builds_identity_name_from_the_decision_function(plugin_source):
    method = _method_ast(plugin_source, "_scan_and_update_channels")
    calls = [n for n in ast.walk(method)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "regex_target_name"]
    assert calls, "_scan_and_update_channels does not call ecm_parsing.regex_target_name"


def test_inactive_counter_and_rule_read_the_same_text(plugin_source):
    """The Validate/scan counter re-tests names after the fact. If it kept reading the
    Name Source text it would report 'matched no channels' for a pattern the rule is
    matching, which is exactly the misleading zero that counter exists to prevent."""
    method = _method_ast(plugin_source, "_scan_and_update_channels")
    source = ast.get_source_segment(plugin_source, method) or ""
    counter = source.split("_inactive_re = re.compile")[-1]
    assert "_inactive_re.search(r.get(\"channel_name\")" not in counter, \
        "the inactive counter still re-tests the Name Source text"
