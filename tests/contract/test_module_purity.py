"""ecm_profiles.py must stay importable without Django.

There is no conftest stubbing Django in this repo - pure modules stay pure by
discipline alone. These guards make that discipline enforceable: they parse the
file with ast (never import it).

Every guard has a self-check proving it FAILS on a violating module. A guard
that never fails is not a guard.
"""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "Event-Channel-Managarr" / "ecm_profiles.py"

FORBIDDEN_ROOTS = {"apps", "django", "core"}
GUARDED_OPTIONAL = {"regex"}
MUTABLE_FACTORIES = {"list", "dict", "set", "bytearray", "defaultdict", "OrderedDict", "Counter"}
FORBIDDEN_DECORATORS = {"lru_cache", "cache", "cached_property"}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _root_name(node, alias):
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0]
    return alias.name.split(".")[0]


def _module_level_imports(tree):
    """Yield (root_module, is_guarded). Covers try body, handlers, else and finally."""
    def emit(stmt, guarded):
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                yield _root_name(stmt, alias), guarded

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield from emit(node, False)
        elif isinstance(node, ast.Try):
            for section in (node.body, node.orelse, node.finalbody):
                for stmt in section:
                    yield from emit(stmt, True)
            for handler in node.handlers:
                for stmt in handler.body:
                    yield from emit(stmt, True)


def _assign_names(node):
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    return [t.id for t in node.targets if isinstance(t, ast.Name)]


# --- the guards ----------------------------------------------------------------

def check_no_django_or_app_imports(path=None):
    tree = _tree(path or MODULE)
    bad = sorted({
        _root_name(node, alias)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if _root_name(node, alias) in FORBIDDEN_ROOTS
    })
    assert not bad, f"forbidden imports: {bad}"


def check_module_imports_are_stdlib_or_guarded(path=None):
    tree = _tree(path or MODULE)
    stdlib = set(sys.stdlib_module_names)
    offenders = [
        f"{name} (guarded={guarded})"
        for name, guarded in _module_level_imports(tree)
        if name and name not in stdlib and not (guarded and name in GUARDED_OPTIONAL)
    ]
    assert not offenders, (
        f"non-stdlib module-level imports: {offenders}. "
        f"Only stdlib, or {sorted(GUARDED_OPTIONAL)} inside try/except ImportError.")


def check_no_module_level_mutable_state(path=None):
    """Constants (tuples, frozen dataclass instances, compiled regex) are fine;
    anything mutable is not - the loader wipes module globals unpredictably."""
    tree = _tree(path or MODULE)
    offenders = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        names = _assign_names(node)
        value = node.value
        if isinstance(value, (ast.List, ast.Dict, ast.Set,
                              ast.ListComp, ast.DictComp, ast.SetComp)):
            offenders += [f"{n} (mutable literal/comprehension)" for n in names]
        elif isinstance(value, ast.Call):
            fn = value.func
            fn_name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if fn_name in MUTABLE_FACTORIES:
                offenders += [f"{n} ({fn_name}() factory)" for n in names]
    assert not offenders, f"module-level mutable state: {offenders}. Use a tuple/frozenset."


def check_no_caching_decorators(path=None):
    """lru_cache is module-level mutable state wearing a hat."""
    tree = _tree(path or MODULE)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name in FORBIDDEN_DECORATORS:
                bad.append(f"{node.name} -> @{name}")
    assert not bad, f"caching decorators are not reload-safe: {bad}"


ALL_GUARDS = (
    check_no_django_or_app_imports,
    check_module_imports_are_stdlib_or_guarded,
    check_no_module_level_mutable_state,
    check_no_caching_decorators,
)


# --- the real module must pass every guard -------------------------------------

def test_module_exists():
    assert MODULE.exists(), f"missing {MODULE}"


@pytest.mark.parametrize("guard", ALL_GUARDS, ids=lambda g: g.__name__)
def test_real_module_passes_guard(guard):
    guard()


# --- every guard must FAIL on a violating module -------------------------------

BAD_SOURCES = {
    "django_import": "import django\n",
    "app_import": "from apps.epg.models import EPGSource\n",
    "unguarded_third_party": "import requests\n",
    "mutable_dict_literal": "CACHE = {}\n",
    "mutable_factory": "CACHE = dict()\n",
    "annotated_mutable": "CACHE: dict = {}\n",
    "comprehension": "CACHE = [x for x in range(3)]\n",
    "lru_cache": "from functools import lru_cache\n@lru_cache\ndef f(x):\n    return x\n",
    "import_in_except": "try:\n    import regex\nexcept ImportError:\n    import requests\n",
}


@pytest.mark.parametrize("label,source", sorted(BAD_SOURCES.items()))
def test_guards_reject_a_violating_module(label, source, tmp_path):
    """Uses a TEMP file - never edits the real module, which rev 1 did and which
    leaves the repo dirty if the engineer is interrupted."""
    fake = tmp_path / "ecm_profiles.py"
    fake.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        for guard in ALL_GUARDS:
            guard(path=fake)
