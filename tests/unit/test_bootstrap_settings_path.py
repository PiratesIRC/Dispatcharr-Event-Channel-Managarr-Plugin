"""scripts/bootstrap_ecm.py writes the settings cache where the plugin reads it.

The plugin's directory is Dispatcharr's stable_plugin_data_path: <plugins>/_data/<slug or key>.
The bootstrap script imports Django models at module level, so these tests extract only the
data_dir_for function from its AST and run that, never the whole script.
"""
import ast
import pathlib
import types

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_ecm.py"


def _data_dir_for():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("PLUGIN_KEY", "PLUGINS_DIR") for t in node.targets):
            wanted.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "data_dir_for":
            wanted.append(node)
    assert len(wanted) == 3, "PLUGIN_KEY, PLUGINS_DIR and data_dir_for must all be defined"
    module = ast.Module(body=wanted, type_ignores=[])
    namespace = {"Path": pathlib.PurePosixPath}  # the container is Linux; no filesystem call here
    exec(compile(module, str(SCRIPT), "exec"), namespace)
    return namespace["data_dir_for"]


def test_without_a_slug_the_directory_is_named_by_the_plugin_key():
    data_dir_for = _data_dir_for()
    assert str(data_dir_for(types.SimpleNamespace(slug=None))) == "/data/plugins/_data/event-channel-managarr"


def test_a_row_without_a_slug_attribute_uses_the_plugin_key():
    assert str(_data_dir_for()(object())) == "/data/plugins/_data/event-channel-managarr"


def test_no_row_uses_the_plugin_key():
    assert str(_data_dir_for()(None)) == "/data/plugins/_data/event-channel-managarr"


def test_a_hub_slug_moves_the_directory_the_plugin_reads():
    assert str(_data_dir_for()(types.SimpleNamespace(slug="ecm-hub"))) == "/data/plugins/_data/ecm-hub"


def test_the_script_no_longer_hard_codes_the_directory():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "/data/plugins/_data/event-channel-managarr/settings.json" not in text
