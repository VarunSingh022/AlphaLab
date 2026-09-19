"""The import graph is measured here, not in a release note.

`nowandfuture.md` section 14 lists **zero package-level import cycles** as a
frozen invariant, and the v3.0 audit established it by measuring once, by hand.
That is the shape of claim this repository has already learned not to trust:
a count in a document decays the moment somebody adds a module, and nobody finds
out until the next audit.

It decayed immediately. v3.1 added `alphalab/data/api.py`, which joined the data
layer to the execution path from *inside* `alphalab.data` -- and since
`alphalab.market` already imports `alphalab.data.feed` for the wire records,
that closed three package cycles. Every test passed, because a cycle that
resolves at import time is invisible at runtime. Moving the joining layer to
`alphalab.api`, above both, restored zero.

So the invariant is measured here now, the same way
``test_the_suite_reports_nothing_deferred.py`` measures the skip count: from the
thing itself, on every run.
"""

from __future__ import annotations

import ast
import collections
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"

#: Module-level cycles that are allowed, each with the reason it exists.
#:
#: A *package*-level cycle has no entry here and never will: the list is for
#: cycles inside one package, where the deferred import is visible in one file
#: and its safety can be read in one sitting.
PERMITTED_MODULE_CYCLES: dict[frozenset[str], str] = {
    frozenset({"alphalab.oms.state", "alphalab.oms.snapshot"}): (
        "a deferred import inside __serializable__; both import orders were "
        "shown safe from a cold interpreter by the v3.0 audit"
    ),
}


def _modules() -> list[tuple[str, pathlib.Path]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        name = str(path.relative_to(PACKAGE.parent).with_suffix("")).replace("/", ".")
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        found.append((name, path))
    return found


def _graph(level: str) -> dict[str, set[str]]:
    """Import edges between packages (``"pkg"``) or between modules (``"mod"``)."""

    graph: dict[str, set[str]] = collections.defaultdict(set)
    for name, path in _modules():
        source = ".".join(name.split(".")[:2]) if level == "pkg" else name
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level or not node.module or not node.module.startswith("alphalab"):
                continue
            target = ".".join(node.module.split(".")[:2]) if level == "pkg" else node.module
            if target != source:
                graph[source].add(target)
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Every cycle reachable by depth-first search, as a list of node paths."""

    found: list[list[str]] = []
    state: dict[str, int] = collections.defaultdict(int)

    def visit(node: str, stack: list[str]) -> None:
        state[node] = 1
        stack.append(node)
        for neighbour in sorted(graph.get(node, ())):
            if state[neighbour] == 1:
                found.append([*stack[stack.index(neighbour) :], neighbour])
            elif state[neighbour] == 0:
                visit(neighbour, stack)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if state[node] == 0:
            visit(node, [])
    return found


# --------------------------------------------------------------------------- #
# The invariant
# --------------------------------------------------------------------------- #


def test_there_are_no_package_level_import_cycles() -> None:
    cycles = _cycles(_graph("pkg"))

    assert cycles == [], "package-level import cycles:\n  " + "\n  ".join(
        " -> ".join(cycle) for cycle in cycles
    )


def test_module_level_cycles_are_exactly_the_documented_ones() -> None:
    """Each permitted one is named individually, so adding another is deliberate."""

    cycles = _cycles(_graph("mod"))
    unexpected = [cycle for cycle in cycles if frozenset(cycle) not in PERMITTED_MODULE_CYCLES]

    assert unexpected == [], "undocumented module-level import cycles:\n  " + "\n  ".join(
        " -> ".join(cycle) for cycle in unexpected
    )


def test_common_is_the_bottom_layer() -> None:
    """``nowandfuture.md`` invariant 8: the whole graph rests on this."""

    assert _graph("pkg").get("alphalab.common", set()) == set()


def test_the_data_package_does_not_reach_the_execution_path() -> None:
    """What keeps the package-level graph acyclic, stated as the rule it is.

    ``alphalab.market`` imports ``alphalab.data.feed`` for the wire records, so
    an edge back from ``data`` to ``market`` -- or to anything that imports
    ``market`` -- closes a cycle. Anything joining the two belongs in
    ``alphalab.api``, which sits above both and which nothing imports.

    ``alphalab.options`` is the one outward edge: ``data.assets`` reads
    ``OptionType`` from ``options.enums``, a leaf module importing nothing but
    ``enum``, rather than spelling call/put a third time.
    """

    assert _graph("pkg").get("alphalab.data", set()) == {
        "alphalab.common",
        "alphalab.options",
    }


def test_nothing_imports_the_application_api() -> None:
    """It is the top of the graph. An importer would put it under something."""

    graph = _graph("pkg")
    importers = sorted(name for name, targets in graph.items() if "alphalab.api" in targets)

    assert importers == []
