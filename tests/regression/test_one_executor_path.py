"""The three orchestration layers are one executor path, and a name selects code from one registry.

v4.3, ADR-0072 sections 6 and 10, GAP-05 and GAP-31. ``docs/ARCHITECTURE.md``
says so, and these tests measure what it says:

* ``alphalab.lifecycle`` imports ``cloud_research`` and ``distributed`` -- the
  trial ledger's ``ClusterExecutor`` runs trials on a cluster's cycle -- so
  neither is a standalone engine any longer;
* ``cluster_scheduler`` and ``research_assistant`` join without an edge and
  stay on the standalone list: nothing imports either;
* nothing in any of the four imports ``lifecycle``, which composes them;
* one class is the registry a name is looked up in;
* a name becomes code in two places, each for its stated reason, and in no
  other: AlphaLab's worker loads the definition its executor was told, in code;
  and ``cloud_research.resolve_task``, superseded and kept until 5.0 (D-54-6).
  The environment reads the ``tzdata`` package's version as data, never
  importing it (v4.3, ADR-0057, AC-38).
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "alphalab"


@cache
def _sources() -> tuple[tuple[str, ast.Module], ...]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found.append((path.relative_to(PACKAGE).as_posix(), ast.parse(path.read_text())))
    return tuple(found)


def _package(relative: str) -> str:
    head, _, rest = relative.partition("/")
    return head if rest else head.removesuffix(".py")


@cache
def _edges() -> frozenset[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for relative, tree in _sources():
        source = _package(relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "alphalab" and len(parts) > 1 and parts[1] != source:
                    edges.add((source, parts[1]))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "alphalab" and len(parts) > 1 and parts[1] != source:
                        edges.add((source, parts[1]))
    return frozenset(edges)


def test_the_lifecycle_reaches_the_cluster_and_nothing_it_composes_reaches_back() -> None:
    edges = _edges()

    assert ("lifecycle", "cloud_research") in edges
    assert ("lifecycle", "distributed") in edges
    for layer in ("cloud_research", "distributed", "cluster_scheduler", "research_assistant"):
        assert (layer, "lifecycle") not in edges, f"{layer} imports the lifecycle that composes it"


def test_the_scheduler_and_the_assistant_join_without_an_edge() -> None:
    importers = {
        target: sorted(source for source, reached in _edges() if reached == target)
        for target in ("cluster_scheduler", "research_assistant")
    }

    assert importers == {"cluster_scheduler": [], "research_assistant": []}


def test_one_class_is_the_registry_a_name_is_looked_up_in() -> None:
    defined = [
        relative
        for relative, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "RunnerRegistry"
    ]

    assert defined == ["common/registry.py"]


def test_a_name_becomes_code_in_three_places_each_for_its_stated_reason() -> None:
    """Any new ``import_module`` is a new trust boundary, and has to be stated before it lands."""

    calls = sorted(
        relative
        for relative, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
            or (isinstance(node.func, ast.Name) and node.func.id in {"import_module", "__import__"})
        )
    )

    assert calls == [
        "cloud_research/task.py",  # resolve_task: superseded, kept until 5.0 (BDY-028)
        "lifecycle/_trial_worker.py",  # the definition its executor names in code (BDY-029)
    ]
