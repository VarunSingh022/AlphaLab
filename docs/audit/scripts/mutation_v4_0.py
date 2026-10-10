"""The v4.0 defect-injection harness: v3.13's mutations, and v4.0's own.

Kept for provenance and re-runnable, not run by the suite (it takes hours). The
method is v3.12's (``mutation_v3_12.py``, master audit W.4) through v3.13's
table and plugin (``mutation_v3_13.py``, W.5), which this script loads and runs
unchanged: scratch copies of the tree, one mutation at a time, the whole suite
with ``-x``, the tests that read a clock and the certificate's source-digest
test deselected, an unmutated baseline first. The table is v3.13's 184
mutations and seventeen of v4.0's behaviour (Z01-Z17).

One addition: ``--worktree`` copies the files Git tracks *and the new ones it
does not ignore* (``git ls-files -co --exclude-standard``) instead of
``git archive HEAD``. A release candidate is reviewed before it is committed, so
the candidate is the working tree; once committed, ``HEAD`` and the working tree
are the same and either source tests it. Usage::

    python docs/audit/scripts/mutation_v4_0.py --check <tree>
    python docs/audit/scripts/mutation_v4_0.py <scratch-dir> --worktree [--workers N] [--only Z01]

Each result is one JSON line on stdout; a summary follows the last one.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V313 = _load("mutation_v3_13", Path(__file__).with_name("mutation_v3_13.py"))
V312: Any = V313.V312
Mutation: Any = V313.Mutation

#: v4.0's own behaviour, one rule each.
V40: tuple[Any, ...] = (
    # A restore continues with the objects the run was captured with (PER-008).
    Mutation(
        "Z01",
        "a restore accepts an object configured otherwise (PER-008)",
        "alphalab/runtime/snapshot.py",
        "    if description is not None:\n        configured = describe(supplied)\n",
        "    if False:\n        configured = describe(supplied)\n",
    ),
    Mutation(
        "Z02",
        "a pipeline snapshot does not describe its simulator (PER-008)",
        "alphalab/runtime/snapshot.py",
        "        simulator_description=describe(config.simulator),\n",
        "        simulator_description=None,\n",
    ),
    Mutation(
        "Z03",
        "a run snapshot does not describe its fill policy (PER-008)",
        "alphalab/runtime/run_snapshot.py",
        "        fill_policy_description=describe(state.config.fill_policy),\n",
        "        fill_policy_description=None,\n",
    ),
    # Ingestion governs the rows it could not read by the cleaning policy (DAT-010).
    Mutation(
        "Z04",
        "a missing value dropped under MissingValuePolicy.REFUSE (DAT-010)",
        "alphalab/data/ingestion.py",
        "    if missing and policy.missing_values is MissingValuePolicy.REFUSE:\n",
        "    if False:\n",
    ),
    Mutation(
        "Z05",
        "an unreadable row dropped under InvalidRecordPolicy.REFUSE (DAT-010)",
        "alphalab/data/ingestion.py",
        "    if unreadable and policy.invalid_records is InvalidRecordPolicy.REFUSE:\n",
        "    if False:\n",
    ),
    Mutation(
        "Z06",
        "a dropped row left out of the provenance (DAT-010)",
        "alphalab/data/ingestion.py",
        "    transformations = [*dropped_rows, *stamped, *outcome.transformations]\n",
        "    transformations = [*stamped, *outcome.transformations]\n",
    ),
    # Option pricing refuses a non-finite input (NUM-015).
    Mutation(
        "Z07",
        "a non-finite option input priced (NUM-015)",
        "alphalab/options/pricing.py",
        "        if value is not None and not _finite(value):\n",
        "        if False:\n",
    ),
    # A whole-unit run fills in whole units (EXE-011).
    Mutation(
        "Z08",
        "a whole-unit run's partial fill left fractional (EXE-011)",
        "alphalab/runtime/execution_pipeline.py",
        "    if not whole_units or decision.status is not FillStatus.PARTIAL_FILL:\n",
        "    if True:\n",
    ),
    # The public conveniences (DOC-009).
    Mutation(
        "Z09",
        "start_strategy replaces a strategy already registered (DOC-009)",
        "alphalab/strategy/runtime.py",
        "    if strategy_id in state.strategies:\n",
        "    if False:\n",
    ),
    Mutation(
        "Z10",
        "start_strategy draws from the caller's identifier stream (DOC-009)",
        "alphalab/strategy/runtime.py",
        "    with use_id_source(None):\n",
        "    if True:\n",
    ),
    Mutation(
        "Z11",
        "a context's configuration writable (DOC-009)",
        "alphalab/strategy/context.py",
        '            config=MappingProxyType({"strategy_id": strategy_id}),\n',
        '            config={"strategy_id": strategy_id},\n',
    ),
    # The schema upgrade states what an older payload recorded (PER-008).
    Mutation(
        "Z12",
        "the v7 -> v8 upgrade invents a sizing model's configuration (PER-008)",
        "alphalab/runtime/snapshot.py",
        '        "sizing_model_description": None,\n',
        '        "sizing_model_description": "FixedQuantitySizing()",\n',
    ),
    # The historical inventory (TST-017).
    Mutation(
        "Z13",
        "an unclassified historical item passes the inventory (TST-017)",
        "docs/audit/scripts/historical_inventory_v4.py",
        "            if row is None:\n                found.append(",
        "            if False:\n                found.append(",
    ),
    Mutation(
        "Z14",
        "the inventory reads only the first item of a bulleted section (TST-017)",
        "docs/audit/scripts/historical_inventory_v4.py",
        "        found[section.ledger_id] = [_normalize(item) for item in items]\n",
        "        found[section.ledger_id] = [_normalize(item) for item in items[:1]]\n",
    ),
    # The documented modules are part of the frozen contract (API-007).
    Mutation(
        "Z15",
        "a name leaves alphalab.api's __all__ (API-007)",
        "alphalab/api.py",
        '    "ingest_rows",\n',
        "",
    ),
    # A container is described by its contents (PER-009).
    Mutation(
        "Z16",
        "a mapping described by its type name alone (PER-009)",
        "alphalab/runtime/assumptions.py",
        "    if isinstance(value, Mapping):\n",
        "    if False:\n",
    ),
    Mutation(
        "Z17",
        "a set described by its type name alone (PER-009)",
        "alphalab/runtime/assumptions.py",
        "    if isinstance(value, AbstractSet):\n",
        "    if False:\n",
    ),
)

#: Every mutation, in the order the run reports them.
MUTATIONS: tuple[Any, ...] = V313.MUTATIONS + V40


def _prepare_worktree(target: Path, workers: int) -> list[Path]:
    """``workers`` copies of the working tree's tracked and new files under ``target``."""

    repository = Path(__file__).resolve().parents[3]
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin").mkdir(exist_ok=True)
    (target / "plugin" / "harness_deselect.py").write_text(V313.PLUGIN, encoding="utf-8")
    listed = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "-co", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    files = [name for name in listed.split("\0") if name]
    copies = []
    for index in range(workers):
        copy = target / f"tree{index}"
        if copy.exists():
            shutil.rmtree(copy)
        for name in files:
            source = repository / name
            if source.is_file():
                destination = copy / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        copies.append(copy)
    return copies


def main(argv: Sequence[str] | None = None) -> int:
    """v3.12's runner over this table, from ``HEAD`` or, with ``--worktree``, the working tree."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--worktree" in arguments:
        arguments.remove("--worktree")
        vars(V312)["_prepare"] = _prepare_worktree
    vars(V312)["MUTATIONS"] = MUTATIONS  # main() reads the table from its module
    vars(V312)["PLUGIN"] = V313.PLUGIN  # and writes v3.13's plugin
    V312.__doc__ = __doc__
    result: int = V312.main(arguments)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
