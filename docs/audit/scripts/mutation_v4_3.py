"""The v4.3 defect-injection harness: v4.1's table, and the v4.3 programme's own series.

Kept for provenance and re-runnable, not run by the suite (it takes hours in
full). The method is v3.12's through v4.1's (``mutation_v4_1.py``), which this
script loads and runs unchanged: scratch copies of the tree, one mutation at a
time, the whole suite with ``-x``, the tests that read a clock and the
certificate's source-digest test deselected, an unmutated baseline first. The
table is v4.1's 236 mutations and one series per v4.3 milestone, each mutation a
defect that milestone's tests exist to catch (plan WS-21). Usage::

    python docs/audit/scripts/mutation_v4_3.py --check <tree>
    python docs/audit/scripts/mutation_v4_3.py <scratch-dir> --worktree [--workers N] [--only J01]

``--check`` proves only that each pattern applies exactly once; a run is what
proves a test fails. A mutation that survives a run is explained in the
progress register, beside the series' results.

Three of v4.1's patterns no longer matched the code v4.2.0 restructured -- they
fail ``mutation_v4_1.py --check`` against the v4.2.1 tag too -- and are
re-targeted here as the same defect in today's code (``RETARGETED``): X15
(observation delivery moved into the schedule cursor), A04 (``PausedReplay``
gained its schedule, book and queue) and G02 (INTEGRATION's commands name
4.2.1's assets).

M0's series:

* **J** -- WS-01: the v4.2.1 payloads and the schedule-digest pins.
* **K** -- WS-02, DET-007 and D-54-7: isolated scopes, derived registry ids.
* **L** -- WS-23, SEC-002: the HTTP transport's schemes and redirects.
* **N** -- WS-29: artifacts held to their commit, the build reproduced.
* **O** -- WS-20 and WS-00: the corrected statements and the charter.
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


V41 = _load("mutation_v4_1", Path(__file__).with_name("mutation_v4_1.py"))
Mutation: Any = V41.Mutation

_SCHEDULE = "alphalab/backtesting/schedule.py"
_TRACKER = "alphalab/experiment_tracking/tracker.py"
_CLUSTER = "alphalab/cloud_research/cluster.py"
_TRANSPORT = "alphalab/marketdata/transport.py"
_PREFLIGHT = "docs/audit/scripts/release_preflight.py"

#: M0's behaviour, one rule each.
V43_M0: tuple[Any, ...] = (
    # --- J: the v4.2.1 payloads, and the schedule's digest (WS-01, GAP-07, R53-6)
    Mutation(
        "J01",
        "a schedule's digest changes its separator",
        _SCHEDULE,
        '            line = f"{entry.kind.value}|{entry.at!r}|{serialize(entry.item)}\\n"\n',
        '            line = f"{entry.kind.value};{entry.at!r};{serialize(entry.item)}\\n"\n',
    ),
    Mutation(
        "J02",
        "a schedule kind's persisted value is renamed",
        _SCHEDULE,
        '    RATE = "rate"\n',
        '    RATE = "fx_rate"\n',
    ),
    Mutation(
        "J03",
        "a registry's digest hashes another rendering of its terms",
        "alphalab/instrument/snapshot.py",
        '    return hashlib.sha256(serialize(capture(registry)).encode("utf-8")).hexdigest()\n',
        '    return hashlib.sha256(repr(capture(registry)).encode("utf-8")).hexdigest()\n',
    ),
    Mutation(
        "J04",
        "a paused replay's recorded registry digest is not read back",
        "alphalab/backtesting/replay_snapshot.py",
        "        instruments_digest=as_optional_str(\n"
        '            require(payload, "instruments_digest"), "instruments_digest"\n'
        "        ),\n",
        "        instruments_digest=None,\n",
    ),
    # --- K: an unseeded scope is isolated; registries derive their ids (DET-007, D-54-7)
    Mutation(
        "K01",
        "an unseeded scope inherits the stream around it again",
        "alphalab/common/ids.py",
        "    return use_id_source(id_source(seed))\n",
        "    return use_id_source(id_source(seed) if seed is not None else _ID_SOURCE.get())\n",
    ),
    Mutation(
        "K02",
        "a tracker run's id forgets its place in the tracker",
        _TRACKER,
        '            f"sequence={len(tracker.runs)}",\n',
        '            "sequence=0",\n',
    ),
    Mutation(
        "K03",
        "a tracker run's parameters enter its id in the order they were given",
        _TRACKER,
        '            f"parameters={_canonical(dict(parameters))}",\n',
        '            f"parameters={dict(parameters)}",\n',
    ),
    Mutation(
        "K04",
        "a tracker run's tags do not enter its id",
        _TRACKER,
        '            f"tags={_canonical(dict(tags) if tags else {})}",\n',
        '            "tags={}",\n',
    ),
    Mutation(
        "K05",
        "a research job's id forgets its place in the cluster",
        _CLUSTER,
        '            f"sequence={distributed.statistics.total_jobs_submitted}",\n',
        '            "sequence=0",\n',
    ),
    # --- L: the HTTP transport opens http and https only (SEC-002)
    Mutation(
        "L01",
        "the transport admits file:// again",
        _TRANSPORT,
        '_HTTP_SCHEMES: Final = frozenset({"http", "https"})\n',
        '_HTTP_SCHEMES: Final = frozenset({"http", "https", "file"})\n',
    ),
    Mutation(
        "L02",
        "the transport opens a URL without checking it",
        _TRANSPORT,
        "        _require_http(url)\n",
        "        pass\n",
    ),
    Mutation(
        "L03",
        "a redirect to ftp:// is followed",
        _TRANSPORT,
        "            if urlsplit(target).scheme not in _HTTP_SCHEMES:\n",
        '            if urlsplit(target).scheme in {"file", "data"}:\n',
    ),
    Mutation(
        "L04",
        "a URL that names no host is opened",
        _TRANSPORT,
        "    if not parts.hostname:\n",
        "    if False:\n",
    ),
    # --- N: artifacts held to their commit; the build reproduced (WS-29)
    Mutation(
        "N01",
        "a packaged file that differs from the commit passes",
        _PREFLIGHT,
        "        name for name in packaged.keys() & committed.keys() if packaged[name] != "
        "committed[name]\n",
        "        name for name in packaged.keys() & committed.keys() if False\n",
    ),
    Mutation(
        "N02",
        "the sdist's licence is treated as generated",
        _PREFLIGHT,
        '_GENERATED = frozenset({"PKG-INFO"})\n',
        '_GENERATED = frozenset({"PKG-INFO", "LICENSE"})\n',
    ),
    Mutation(
        "N03",
        "only the sdist is rebuilt and compared",
        _PREFLIGHT,
        "        for name in (sdist_name, wheel_name):\n",
        "        for name in (sdist_name,):\n",
    ),
    Mutation(
        "N04",
        "the distributions check never rebuilds",
        _PREFLIGHT,
        "            found, log = reproduction_problems(dist, version, source[0], sys.executable, "
        "root=root)\n",
        "            found, log = [], []\n",
    ),
    Mutation(
        "N05",
        "the record does not name its commit",
        _PREFLIGHT,
        '    built = [f"Built from commit {source[0]}, tree {source[1]}.", ""] if source is not '
        "None else []\n",
        "    built: list[str] = []\n",
    ),
    # --- O: the corrected statements and the charter stay (WS-20, WS-00)
    Mutation(
        "O01",
        "COMPOSING says a refused amend loses the original again",
        "docs/COMPOSING_STRATEGIES.md",
        "placed. If any of them refuses it, the original keeps working with its\n",
        "placed. If any of them refuses it, the original is gone, with its\n",
    ),
    Mutation(
        "O02",
        "ROADMAP calls the engine complete and frozen again",
        "ROADMAP.md",
        "What v4.0 froze is the contract, not the capability: one execution spine with\n",
        "At v4.0 that engine is complete and frozen: one execution spine with\n",
    ),
    Mutation(
        "O03",
        "ARCHITECTURE's status section loses the current minor release",
        "docs/ARCHITECTURE.md",
        "**v4.2** (v4.1.1 and v4.2.0) moved no boundary. One `RunSchedule` applies every\n",
        "v4.1.1 and v4.2.0 moved no boundary. One `RunSchedule` applies every\n",
    ),
)

#: v4.1's mutations whose patterns v4.2.0's restructuring moved: the same defect, now.
RETARGETED: dict[str, Any] = {
    "X15": Mutation(
        "X15",
        "an observation delivered before the records of its own instant (OFE-009)",
        _SCHEDULE,
        "    ScheduleKind.OBSERVATION: SchedulePlace.AFTER_RECORDS,\n",
        "    ScheduleKind.OBSERVATION: SchedulePlace.BEFORE_RECORDS,\n",
    ),
    "A04": Mutation(
        "A04",
        "a pause closes the open instant's slice early",
        "alphalab/backtesting/replay.py",
        "                return PausedReplay(state, paused, position, schedule, book, queue)\n",
        "                return PausedReplay(\n"
        "                    RunEngine.close_slice(state, context_factory, inputs.rates),\n"
        "                    paused, position, schedule, book, queue,\n"
        "                )\n",
    ),
    "G02": Mutation(
        "G02",
        "a continuation broken by a trailing space",
        "docs/INTEGRATION.md",
        'curl -fsSLO "${RELEASE}/SHA256SUMS-4.2.1"\n',
        'curl -fsSLO \\ \n  "${RELEASE}/SHA256SUMS-4.2.1"\n',
    ),
}

MUTATIONS: tuple[Any, ...] = (
    tuple(RETARGETED.get(mutation.ident, mutation) for mutation in V41.MUTATIONS) + V43_M0
)


def main(argv: Sequence[str] | None = None) -> int:
    """v4.1's runner over this table."""

    vars(V41.V40)["MUTATIONS"] = MUTATIONS
    vars(V41)["MUTATIONS"] = MUTATIONS
    vars(V41.V40)["__doc__"] = __doc__
    result: int = V41.V40.main(argv)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
