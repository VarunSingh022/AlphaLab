"""The core imports no third-party package and no optional distribution (AC-38, ADR-0057).

``alphalab`` has no runtime dependency (frozen invariant #9) and keeps none. An
optional capability lives in its own distribution under ``distributions/``,
implementing a protocol the core declares, and the researcher imports it. This
sweep reads every module's syntax tree and holds what the core may import:

* **every import statement** names the standard library, ``alphalab`` itself or
  ``__future__``;
* **every import by a name written in the source** -- ``importlib.import_module``
  and ``__import__`` with a literal name -- names one of those too. A name
  passed at run time is the researcher's own module, loaded at the sites
  ``tests/regression/test_one_executor_path.py`` pins.

``importlib.util.find_spec`` locates a package without importing it, so the
environment's reading of the ``tzdata`` package's version -- located, then read
as text -- runs no third-party code
(``tests/unit/lifecycle/test_engine_build.py``).
"""

from __future__ import annotations

import ast
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "alphalab"

#: What the core may import: the standard library, itself and ``__future__``.
ALLOWED = frozenset(sys.stdlib_module_names) | {"alphalab", "__future__"}


@cache
def _trees() -> tuple[tuple[str, ast.Module], ...]:
    return tuple(
        (path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def test_the_sweep_reads_every_module_of_the_package() -> None:
    assert len(_trees()) > 600
    assert any(name == "alphalab/lifecycle/environment.py" for name, _ in _trees())


def test_every_import_statement_names_the_standard_library_or_alphalab() -> None:
    foreign = []
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            foreign += [
                f"{name}:{node.lineno} {module}"
                for module in modules
                if module.split(".")[0] not in ALLOWED
            ]
    assert foreign == []


def _written(node: ast.Call) -> str | None:
    """The literal name a call imports, if it is an import call with one."""

    function = node.func
    imports = (isinstance(function, ast.Attribute) and function.attr == "import_module") or (
        isinstance(function, ast.Name) and function.id in {"import_module", "__import__"}
    )
    if not imports or not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def test_every_import_by_a_written_name_names_the_standard_library_or_alphalab() -> None:
    foreign = [
        f"{name}:{node.lineno} {written}"
        for name, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (written := _written(node)) is not None
        and written.split(".")[0] not in ALLOWED
    ]
    assert foreign == []


def test_a_planted_third_party_import_would_be_found() -> None:
    """The sweep's own check: each kind of import it reads, planted, is caught."""

    planted = ast.parse(
        "import numpy\n"
        "from sklearn.base import clone\n"
        "import importlib\n"
        "importlib.import_module('torch')\n"
        "__import__('alphalab_models')\n"
        "from . import sibling\n"
        "import json\n"
    )
    statements = [
        module
        for node in ast.walk(planted)
        for module in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module]
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
            else []
        )
        if module.split(".")[0] not in ALLOWED
    ]
    written = [
        name
        for node in ast.walk(planted)
        if isinstance(node, ast.Call)
        and (name := _written(node)) is not None
        and name.split(".")[0] not in ALLOWED
    ]
    assert statements == ["numpy", "sklearn.base"]
    assert written == ["torch", "alphalab_models"]
