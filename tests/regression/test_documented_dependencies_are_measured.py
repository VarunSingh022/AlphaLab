"""The package dependencies ``docs/ARCHITECTURE.md`` states are the ones the code has (v4.0).

Until v4.0 the document's "Allowed Dependencies" said ``alphalab.data`` might
depend on "Market Data, Feed, Persistence" -- ``feed`` was removed in v3.10, and
``data`` imports ``common`` and ``options`` -- and that ``research`` might depend on
"Universal Data, Analytics, Replay", none of which it imports: it imports
``alt_data``, ``common`` and ``factor_library``. A rule stated in prose and never
measured had become a description of a different library. The section is now a
table, and this test reads each row against the AST import graph, in both
directions: every stated edge exists and every existing edge is stated.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "alphalab"
DOCUMENT = ROOT / "docs" / "ARCHITECTURE.md"


def _imports(package: str) -> set[str]:
    """The other top-level ``alphalab`` packages ``package`` imports, at any depth."""

    found: set[str] = set()
    for path in (PACKAGE / package).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                parts = name.split(".")
                if parts[0] == "alphalab" and len(parts) > 1 and parts[1] != package:
                    found.add(parts[1])
    return found


def _stated() -> dict[str, set[str]]:
    text = DOCUMENT.read_text(encoding="utf-8")
    section = text.split("# Allowed Dependencies", 1)[1].split("\n# ", 1)[0]
    rows = re.findall(r"^\| `(\w+)` \| (.+?) \|$", section, re.M)
    return {
        package: set() if cells.strip() == "none" else set(re.findall(r"`(\w+)`", cells))
        for package, cells in rows
    }


def test_the_table_is_read() -> None:
    stated = _stated()
    assert len(stated) >= 12, "the dependency table moved; this test reads it"
    assert stated["common"] == set()


def test_every_documented_package_imports_exactly_what_the_table_states() -> None:
    wrong = {
        package: {"stated": sorted(edges), "measured": sorted(_imports(package))}
        for package, edges in _stated().items()
        if edges != _imports(package)
    }
    assert not wrong, f"docs/ARCHITECTURE.md's dependency table is not the import graph: {wrong}"
