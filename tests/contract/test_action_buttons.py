"""Action button colour means one thing, and the two action lists agree.

Measured 2026-09-05. The two lists already agreed here, across all 9 actions and
every piece of button metadata, which is not what the sibling project
Stream-Mapparr found for itself. These tests keep it that way, because
Dispatcharr serves the list in plugin.py for an enabled plugin while plugin.json
is the manifest, and reading either alone gives a false picture of the interface.

COLOUR DID NOT TRACK CONSEQUENCE. Run Now, which hides channels and clears their
EPG assignment, was green, the same family as Save Schedule which only re-arms a
timer. Dry Run, which changes nothing and sends nothing anywhere, was cyan, the
colour reserved for sending something outward. Clear CSV Exports, which deletes
report files and no channel or guide data at all, was one of only two red
buttons. An operator scanning the buttons was being told the opposite of the
truth about which one to be careful with.

The scheme, taken from Stream-Mapparr and adapted to what this plugin does:

  red     can REMOVE something the user cares about, or take a channel off air
  orange  writes data or clears state, but removes nothing
  green   runs a normal operation that writes no user data
  cyan    sends something outward, to an inbox or an issue tracker
  blue    reads and reports, changing nothing

This plugin has no cyan action, because nothing it does sends anything outward.
"""

import ast
import io
import json
import os

import pytest

# tests/contract/<this file>, so three levels up is the repository root.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_PY = os.path.join(ROOT, "Event-Channel-Managarr", "plugin.py")
MANIFEST = os.path.join(ROOT, "Event-Channel-Managarr", "plugin.json")

# Invoked by Dispatcharr after an M3U refresh. It is not a button and must never
# grow one, or it appears on the page as something to press.
EVENT_HANDLER = "on_m3u_refresh"

EXPECTED_COLOURS = {
    # red: hides channels, or removes guide data
    "run_now": "red",
    "remove_epg_from_hidden": "red",
    # orange: writes or clears state, removes nothing the plugin manages
    "clear_csv_exports": "orange",
    "cleanup_periodic_tasks": "orange",
    # green: runs an operation, writes no channel or guide data
    "update_schedule": "green",
    # blue: reads and reports
    "validate_configuration": "blue",
    "dry_run": "blue",
    "check_scheduler_status": "blue",
}

# Pressing these can take a channel off air or remove guide data, so each must
# ask first.
MUST_CONFIRM = {"run_now", "remove_epg_from_hidden"}

METADATA_KEYS = ("label", "description", "button_label", "button_variant",
                 "button_color", "confirm", "events")


def _python_actions():
    """The list Dispatcharr serves for an enabled plugin."""
    with io.open(PLUGIN_PY, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "actions" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("plugin.py has no actions list that can be read statically")


def _manifest_actions():
    with io.open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)["actions"]


def _by_id(actions):
    return {a["id"]: a for a in actions}


def _pressable(actions):
    return [a for a in actions if a["id"] != EVENT_HANDLER]


# --- every button is labelled and coloured ---------------------------------------

def test_every_pressable_action_has_a_button_label():
    for action in _pressable(_python_actions()):
        assert action.get("button_label"), f"{action['id']} has no button label"


def test_every_pressable_action_has_a_button_colour():
    for action in _pressable(_python_actions()):
        assert action.get("button_color"), f"{action['id']} has no button colour"


def test_the_event_handler_has_no_button_at_all():
    """It is invoked by an event, never pressed, so a button would mislead."""
    handler = _by_id(_python_actions())[EVENT_HANDLER]
    assert not handler.get("button_label")
    assert not handler.get("button_color")
    assert handler.get("events"), "the event handler must declare its events"


def test_no_other_action_claims_to_be_event_driven():
    for action in _pressable(_python_actions()):
        assert not action.get("events"), (
            f"{action['id']} declares events but is also a button")


# --- colour tracks consequence ----------------------------------------------------

def test_every_action_is_covered_by_the_colour_scheme():
    ids = {a["id"] for a in _pressable(_python_actions())}
    assert ids == set(EXPECTED_COLOURS), (
        "an action was added or removed without deciding its colour: "
        f"{ids ^ set(EXPECTED_COLOURS)}")


@pytest.mark.parametrize("action_id,colour", sorted(EXPECTED_COLOURS.items()))
def test_the_action_carries_the_colour_its_consequence_calls_for(action_id, colour):
    assert _by_id(_python_actions())[action_id].get("button_color") == colour


def test_red_is_reserved_for_actions_that_remove_something():
    red = {a["id"] for a in _python_actions() if a.get("button_color") == "red"}
    assert red == MUST_CONFIRM, (
        "red must mean an action can take a channel off air or remove guide "
        f"data; found {red}")


def test_every_red_action_asks_for_confirmation():
    for action in _python_actions():
        if action.get("button_color") == "red":
            assert action.get("confirm"), (
                f"{action['id']} is red and does not ask before acting")


def test_a_confirmation_prompt_says_what_will_happen():
    for action in _python_actions():
        confirm = action.get("confirm")
        if not confirm:
            continue
        message = confirm.get("message", "") if isinstance(confirm, dict) else ""
        assert len(message) >= 40, (
            f"{action['id']} asks for confirmation without saying what it will do")


def test_the_reading_actions_change_nothing_and_are_blue():
    """Dry Run is the one most likely to be miscoloured: it is a preview."""
    by_id = _by_id(_python_actions())
    assert by_id["dry_run"]["button_color"] == "blue"
    assert not by_id["dry_run"].get("confirm"), (
        "a preview that changes nothing should not ask permission")


# --- the two lists agree ----------------------------------------------------------

def test_the_manifest_and_the_served_list_hold_the_same_actions():
    assert sorted(_by_id(_python_actions())) == sorted(_by_id(_manifest_actions()))


@pytest.mark.parametrize("key", METADATA_KEYS)
def test_the_manifest_and_the_served_list_agree_on_button_metadata(key):
    python, manifest = _by_id(_python_actions()), _by_id(_manifest_actions())
    disagree = [aid for aid in python
                if python[aid].get(key) != manifest.get(aid, {}).get(key)]
    assert not disagree, (
        f"plugin.py and plugin.json disagree about {key} for {disagree}; "
        "Dispatcharr serves the plugin.py value, so the manifest is the lie")


# --- the copy on the buttons -------------------------------------------------------

def test_no_button_text_contains_an_em_dash():
    for action in _python_actions():
        for key in ("label", "description", "button_label"):
            value = action.get(key) or ""
            assert chr(0x2014) not in value and chr(0x2013) not in value, (
                f"{action['id']}.{key} contains an em or en dash")


def test_every_action_description_says_what_it_does():
    for action in _python_actions():
        assert len(action.get("description") or "") >= 40, (
            f"{action['id']} has no useful description")
