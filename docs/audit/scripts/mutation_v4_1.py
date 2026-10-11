"""The v4.1 defect-injection harness: v4.0's table, and v4.1's own mutations.

Kept for provenance and re-runnable, not run by the suite (it takes hours in
full). The method is v3.12's through v4.0's (``mutation_v4_0.py``), which this
script loads and runs unchanged -- scratch copies of the working tree, one
mutation at a time, the whole suite with ``-x``, the tests that read a clock
and the certificate's source-digest test deselected, an unmutated baseline
first. The table is v4.0's 201 mutations and v4.1's (A to I below), each one
defect the release's tests exist to catch. Usage::

    python docs/audit/scripts/mutation_v4_1.py --check <tree>
    python docs/audit/scripts/mutation_v4_1.py <scratch-dir> --worktree [--workers N] [--only A01]

A mutation that applies is not a mutation that is caught: ``--check`` proves
only that each pattern applies exactly once; a run is what proves a test fails.

The series:

* **A** -- FUT-001 and PER-010: the replay's pause, capture, restore and resume.
* **B** -- PKG-002: the release verifier's paths.
* **C** -- TST-020: release validation's approval of the tagged commit.
* **D** -- REP-005: the correctly rounded power in a run's report.
* **E** -- API-008: one spelling of a signature on every interpreter.
* **F, G, H, I** -- TST-019, DOC-011, LIM-007 and the consumer contract: what
  the workflows install and run, what the documents tell a reader to run, and
  what each installed artifact is checked against.
"""

from __future__ import annotations

import importlib.util
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


V40 = _load("mutation_v4_0", Path(__file__).with_name("mutation_v4_0.py"))
Mutation: Any = V40.Mutation

_REPLAY = "alphalab/backtesting/replay.py"
_SNAPSHOT = "alphalab/backtesting/replay_snapshot.py"
_PREFLIGHT = "docs/audit/scripts/release_preflight.py"

#: v4.1's own behaviour, one rule each.
V41: tuple[Any, ...] = (
    # --- A: the replay pauses, is kept, and resumes to the same result (FUT-001)
    Mutation(
        "A01",
        "a resumed replay is asked to pause where it already is",
        _REPLAY,
        "    ask = not resumed\n",
        "    ask = True\n",
    ),
    Mutation(
        "A02",
        "a resumed cursor restarts its identifier stream from its seed",
        _REPLAY,
        "        cursor_ids = RunEngine.id_source_at(paused.cursor_position)\n",
        "        cursor_ids = id_source(cursor_seed(paused.run.config.seed))\n",
    ),
    Mutation(
        "A03",
        "a resumed run restarts its identifier stream from its seed",
        _REPLAY,
        "        with RunEngine.resume(paused.run):\n",
        "        with id_scope(paused.run.config.seed):\n",
    ),
    Mutation(
        "A04",
        "a pause closes the open instant's slice early",
        _REPLAY,
        "                return PausedReplay(state, paused, position)\n",
        "                return PausedReplay(\n"
        "                    close_slice(state, context_factory), paused, position\n"
        "                )\n",
    ),
    Mutation(
        "A05",
        "a resumed replay counts its records from zero",
        _REPLAY,
        "    replayed = replay.current_index\n",
        "    replayed = 0\n",
    ),
    Mutation(
        "A06",
        "a paused replay whose cursor and run disagree can be built",
        _REPLAY,
        "        if taken != cursor.current_index:\n",
        "        if False:\n",
    ),
    Mutation(
        "A07",
        "caller code deciding a pause draws from the run's stream",
        _REPLAY,
        "            with use_id_source(None):\n                pause = pause_when(progress)\n",
        "            pause = pause_when(progress)\n",
    ),
    Mutation(
        "A08",
        "a declared retention does not reach the cursor's log (PER-010)",
        _REPLAY,
        "        kept = trimmed(replay.system_events, retention)\n",
        "        kept = replay.system_events\n",
    ),
    Mutation(
        "A09",
        "a restore accepts records changed since the pause",
        _SNAPSHOT,
        "    if found != snapshot.dataset_digest:\n",
        "    if False:\n",
    ),
    Mutation(
        "A10",
        "a restore does not check the records either side of the pause",
        _SNAPSHOT,
        "    if last != snapshot.last_event_id"
        " or records[index].event_id != snapshot.next_event_id:\n",
        "    if False:\n",
    ),
    Mutation(
        "A11",
        "a restore accepts a stream that does not match its log",
        _SNAPSHOT,
        "    if position.seed is not None and position.draws != len(events) + dropped:\n",
        "    if False:\n",
    ),
    Mutation(
        "A12",
        "a restore accepts a cursor and a run that disagree, by its own check",
        _SNAPSHOT,
        "    if taken != index:\n",
        "    if False:\n",
    ),
    Mutation(
        "A13",
        "a checkpoint forgets the events a retention dropped",
        _SNAPSHOT,
        "        events_dropped=cursor.system_events.dropped,\n",
        "        events_dropped=0,\n",
    ),
    Mutation(
        "A14",
        "a restored cursor log forgets what was dropped before it",
        _SNAPSHOT,
        "        system_events=AppendOnlyLog.restored(\n"
        "            (entry.event for entry in snapshot.events), snapshot.events_dropped\n"
        "        ),\n",
        "        system_events=AppendOnlyLog(entry.event for entry in snapshot.events),\n",
    ),
    Mutation(
        "A15",
        "a restore accepts a cursor whose clock disagrees with its position",
        _SNAPSHOT,
        "    if snapshot.current_timestamp != expected_clock:\n",
        "    if False:\n",
    ),
    # --- B: the verifier installs what it was given, from anywhere (PKG-002)
    Mutation(
        "B01",
        "an artifact reaches pip as given, relative to another directory",
        _PREFLIGHT,
        "        artifact = artifact.resolve(strict=True)\n",
        "        artifact.resolve(strict=True)\n",
    ),
    Mutation(
        "B02",
        "a missing distribution directory is not refused by name",
        _PREFLIGHT,
        '    dist = existing_directory(dist, "the distribution directory")\n',
        "    dist = Path(dist)\n",
    ),
    Mutation(
        "B03",
        "any existing file is installed as the release's artifact",
        _PREFLIGHT,
        "    if not artifact.is_file() or artifact.name not in artifact_names(version):\n",
        "    if not artifact.is_file():\n",
    ),
    Mutation(
        "B04",
        "a version that is not one shapes the artifact names",
        _PREFLIGHT,
        "    if _VERSION.fullmatch(version) is None:\n        # It becomes part",
        "    if False:\n        # It becomes part",
    ),
    Mutation(
        "B05",
        "a missing record directory is found only after the installs",
        _PREFLIGHT,
        "    record = None if record is None else "
        'writable_file(record, "the verification record")\n',
        "    record = None if record is None else Path(record)\n",
    ),
    Mutation(
        "B06",
        "an inherited PYTHONPATH reaches the fresh environment's interpreter (PKG-003)",
        _PREFLIGHT,
        "        env=isolated_environment(),\n",
        "",
    ),
    # --- C: a published tag is the source a passing preflight approved (TST-020)
    Mutation(
        "C01",
        "a merge that changed no file is not the approved source",
        _PREFLIGHT,
        "        if head != sha and (_SHA.fullmatch(head) is None or tree_of(head) != tree):\n",
        "        if head != sha:\n",
    ),
    Mutation(
        "C02",
        "a preflight of another version approves the tag",
        _PREFLIGHT,
        "        if preflight_artifact(version, head) in names:\n",
        "        if True:\n",
    ),
    Mutation(
        "C03",
        "a failed preflight approves the tag",
        _PREFLIGHT,
        '        if run.get("conclusion") != "success" or not str(run.get("path", "")).endswith(\n',
        '        if not str(run.get("path", "")).endswith(\n',
    ),
    Mutation(
        "C04",
        "release validation proceeds when the tag is another commit",
        ".github/workflows/release.yml",
        '          if [ "${tagged}" != "${head}" ] || [ "${head}" != "${RUN_SHA}" ]; then\n',
        "          if false; then\n",
    ),
    # --- D: one run, one identity, on any host (REP-005)
    Mutation(
        "D01",
        "CAGR and the geometric return go back to the host's pow",
        "alphalab/analytics/returns.py",
        "        result = float(_POWER.power(Decimal(base), Decimal(exponent)))\n",
        "        result = math.pow(base, exponent)\n",
    ),
    # --- E: one spelling of a signature on every interpreter (API-008)
    Mutation(
        "E01",
        "the manifest records the interpreter's own spelling",
        "docs/api/generate_public_api.py",
        '    return canonical(_ADDRESS.sub(">", text))\n',
        '    return _ADDRESS.sub(">", text)\n',
    ),
    Mutation(
        "E02",
        "an Optional loses its None",
        "docs/api/generate_public_api.py",
        '            members.append("None")\n',
        "            pass\n",
    ),
    # --- F: the benchmark job installs what the benchmarks import (TST-019)
    Mutation(
        "F01",
        "the benchmark job installs the package alone",
        ".github/workflows/benchmarks.yml",
        '          python -m pip install -e ".[dev]"\n',
        "          python -m pip install -e .\n",
    ),
    # --- G: a documented command runs as pasted (DOC-011)
    Mutation(
        "G01",
        "a placeholder written as a command",
        "docs/INTEGRATION.md",
        "```bash\npython -m pip check\n```\n",
        "```bash\npython -m pip check <environment>\n```\n",
    ),
    Mutation(
        "G02",
        "a continuation broken by a trailing space",
        "docs/INTEGRATION.md",
        'curl -fsSLO "${RELEASE}/SHA256SUMS-4.1.0"\n',
        'curl -fsSLO \\ \n  "${RELEASE}/SHA256SUMS-4.1.0"\n',
    ),
    # --- H: every interpreter, every system, every gate required (LIM-007, PKG-002)
    Mutation(
        "H01",
        "CI verifies the distributions by another path than release validation",
        ".github/workflows/ci.yml",
        "          python docs/audit/scripts/release_preflight.py distributions dist \\\n",
        '          python docs/audit/scripts/release_preflight.py distributions "${PWD}/dist" \\\n',
    ),
    Mutation(
        "H02",
        "CI drops CPython 3.14",
        ".github/workflows/ci.yml",
        '          - "3.13"\n          - "3.14"\n',
        '          - "3.13"\n',
    ),
    Mutation(
        "H03",
        "a failing test step is allowed",
        ".github/workflows/ci.yml",
        "      - name: Pytest\n        run: python -m pytest -W error\n",
        "      - name: Pytest\n"
        "        continue-on-error: true\n"
        "        run: python -m pytest -W error\n",
    ),
    # --- I: each installed artifact is held to the consumer contract
    Mutation(
        "I01",
        "the installed smoke test skips the consumer contract",
        "tests/installed_smoke.py",
        "    contract.main(expected_version)\n",
        "    del contract\n",
    ),
)

MUTATIONS: tuple[Any, ...] = V40.MUTATIONS + V41


def main(argv: Sequence[str] | None = None) -> int:
    """v4.0's runner over this table."""

    vars(V40)["MUTATIONS"] = MUTATIONS
    vars(V40)["__doc__"] = __doc__
    result: int = V40.main(argv)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
