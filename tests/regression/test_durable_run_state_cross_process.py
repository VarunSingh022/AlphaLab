"""A run stops, is written to disk, and finishes in a different interpreter.

This is what "durable run state" has claimed since v2.9 and has never been shown.
``test_durable_run_continuation`` splits a run at every boundary and resumes it,
and ``test_strategy_state_continuation`` resumes into a freshly constructed
strategy -- but both round-trip *in one process*, holding the payload in a local
variable and handing the same ``sizing_model``, ``simulator``, ``fill_policy``
and registry objects back to ``restore``. Nothing established that a run
continues in an interpreter that shares no memory with the one that started it,
which is the only thing a store is for.

The oracle
----------
::

    control  : one seeded run over all N+M records, uninterrupted
    split    : N records here -> capture -> serialize -> FileRunStateStore.put
               ...a new interpreter...
               -> get -> from_primitives -> restore -> resume -> M records
               -> capture -> serialize -> put
    assert   : the two final payloads are byte-identical

Byte-identity of ``serialize(capture(...))`` is the strongest surface available
and it strictly subsumes ADR-0023's hand-written Class-1 comparison: a field
added to a captured state joins the oracle automatically, where a dictionary of
comparisons has to be remembered. It is only available because ``serialize`` is
deterministic by construction -- ``sort_keys=True``, tight separators, exact
``Decimal`` strings, insertion-ordered ``PersistentSet`` and ``AppendOnlyLog`` --
which ``test_session_serialization_is_deterministic_and_stable`` already pins.

The Class-1 comparisons below are kept, and are **failure localization**: when
byte-identity fails they say which subsystem moved. They do not replace it.

Why the child is genuinely a different process
----------------------------------------------
It is a fresh ``sys.executable`` -- not a fork, so no live object is inherited --
and everything it is told arrives as **JSON**: the store root, the reference, the
identifiers, and the records still to process. Nothing is pickled. It builds its
own :class:`~alphalab.execution.simulator.ExecutionSimulator`, its own sizing
model, its own :class:`~alphalab.execution.policy.ImmediateFill` and its own
strategy instance, and reads the run state back through the store.

What it shares with the parent is *code*: the strategy class, and the harness
that builds a configuration. That is exactly what ADR-0014 and ADR-0023 decision
3 intend -- a live object is recorded by type and supplied back by the caller --
and it is the reason :class:`CountingStrategy` below is written to diverge
loudly if its state does not survive: it trades on the parity of a counter it
owns, so a child that restored the payload but not the strategy's memory would
place its orders on the wrong records and fail the oracle rather than pass it
quietly.

The limitation this does not hide
---------------------------------
A snapshot records ``sizing_model_type``, ``simulator_type``,
``instruments_type`` and ``fill_policy_type`` -- class *names*, never
configuration. The child constructs objects of the recorded classes, and nothing
verifies that they carry the parent's parameters:
``EqualWeightSizing(3)`` and ``EqualWeightSizing(30)`` are indistinguishable to
``restore``. The equivalence this file demonstrates therefore holds under
ADR-0023 decision 8's stated precondition -- "the same supplied runtime objects"
-- which remains asserted by the caller and unverifiable by the framework.
ADR-0029 records it as a non-goal rather than closing it, because closing it
moves ``PIPELINE_SNAPSHOT_SCHEMA``.
"""

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab.common.ids import id_scope
from alphalab.execution.policy import ImmediateFill
from alphalab.market.record import MarketRecord
from alphalab.persistence import (
    FileRunStateStore,
    RunStateRef,
    deserialize,
    serialize,
)
from alphalab.runtime.session import ExecutionMode, SessionConfig, SessionState, TradingSession
from alphalab.runtime.session_snapshot import SessionObjects
from alphalab.runtime.session_snapshot import capture as capture_session
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 20260914

#: Fixed rather than generated: both processes must name the same instrument, and
#: the canonical domain identifiers are UUIDs (``alphalab.core.ids``).
STRATEGY_ID = str(uuid.UUID(int=0x2213_0001))
ASSET_ID = str(uuid.UUID(int=0x2213_0002))

#: The run identity, which is caller-supplied and opaque and is *not* a UUID --
#: that is the point of ADR-0029 decision 3, and a legible one reads better in a
#: store listing than a generated one.
RUN_ID = "run-cross-process"
N, M = 5, 6
TOTAL = N + M

#: Repository root, so the child can import the same code from a clean interpreter.
REPO_ROOT = str(Path(__file__).resolve().parents[2])


class CountingStrategy(BaseStrategy):
    """Trades on the parity of a counter it owns, and declares that counter durable.

    Written so that losing the state is **loud**. A fresh instance restarts at
    ``_seen = 0``, so its parity is inverted relative to a continued run: it would
    trade on the records the original held through and hold through the records the
    original traded. The orders, fills, cash and identifiers would all diverge, and
    the byte-identity oracle would fail rather than a strategy quietly starting
    empty.

    ``_traded`` is a ``Decimal`` deliberately: it exercises ADR-0025 decision 3's
    contract that ``restore_state`` receives JSON-decoded primitives, so the value
    comes back as a ``str`` and the strategy is the thing that knows to rebuild it.
    """

    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._seen = 0
        self._traded = Decimal("0")

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self._seen += 1
        if self._seen % 2 == 0:
            return ()
        delta = Decimal("5") if self._seen % 4 == 1 else Decimal("-3")
        self._traded += delta
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=delta,
                timestamp=event.quote.timestamp,
            ),
        )

    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> Mapping[str, Any]:
        return {"seen": self._seen, "traded": self._traded}

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"CountingStrategy cannot read state version {version}")
        self._seen = int(payload["seen"])
        self._traded = Decimal(str(payload["traded"]))


def record_specs(count: int = TOTAL) -> list[dict[str, Any]]:
    """The records, as plain data, so parent and child build identical ones.

    Deliberately data rather than a shared generator call: the child reconstructs
    its records from what the JSON told it, so nothing depends on two processes
    agreeing about a default argument.
    """

    return [
        {"event_id": f"REC-{index}", "timestamp": 2.0 + index, "mid": str(100 + index)}
        for index in range(count)
    ]


def build_records(asset_id: str, specs: Sequence[Mapping[str, Any]]) -> list[MarketRecord]:
    """Turn record specifications into canonical market records."""

    return [
        MarketRecord(
            event_id=str(spec["event_id"]),
            timestamp=float(spec["timestamp"]),
            payload=sized_quote(
                asset_id,
                float(spec["timestamp"]),
                Decimal(str(spec["mid"])),
                Decimal("100"),
            ),
        )
        for spec in specs
    ]


def build_session_objects(strategy_id: str, strategy: CountingStrategy) -> SessionObjects:
    """The live objects a restore requires, built fresh from their classes.

    ``pipeline_config`` is called only to *source* a sizing model, a simulator and
    the (absent) registry. Every value field of the restored configuration comes
    from the payload, so what this supplies is the four objects a snapshot records
    by type and never carries.
    """

    config = pipeline_config(strategy_id)
    return SessionObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.sizing_model,
            simulator=config.simulator,
            strategies={strategy_id: strategy},
            instruments=config.instruments,
        ),
        fill_policy=ImmediateFill(),
    )


def _session_config(strategy_id: str) -> SessionConfig:
    return SessionConfig(
        pipeline=pipeline_config(strategy_id),
        mode=ExecutionMode.BACKTEST,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
    )


def _drive(state: SessionState, records: Iterable[MarketRecord]) -> SessionState:
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return state


def _control_payload() -> str:
    """One seeded run over all N+M records, never interrupted."""

    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    config = _session_config(STRATEGY_ID)
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        state = _drive(state, build_records(ASSET_ID, record_specs()))
    return serialize(capture_session(state))


# ---------------------------------------------------------------------------
# The child: a clean interpreter that shares no object with this one
# ---------------------------------------------------------------------------

_CHILD = '''
"""Continue a stored run. Started as a fresh interpreter; told everything in JSON."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])

from alphalab.persistence import FileRunStateStore, RunStateRef, deserialize, serialize
from alphalab.runtime.session import TradingSession
from alphalab.runtime.session_snapshot import capture as capture_session
from alphalab.runtime.session_snapshot import from_primitives as session_from_primitives
from alphalab.runtime.session_snapshot import restore as restore_session
from tests.integration.harness import context_factory
from tests.regression.test_durable_run_state_cross_process import (
    CountingStrategy,
    build_records,
    build_session_objects,
)

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

store = FileRunStateStore(spec["root"])
payload = store.get(RunStateRef(spec["run_id"], spec["sequence"]))

# Everything below is constructed here, in this process, from classes only.
strategy = CountingStrategy(spec["strategy_id"], spec["asset_id"])
objects = build_session_objects(spec["strategy_id"], strategy)

restored = restore_session(session_from_primitives(deserialize(payload)), objects)

# The state came back with the strategy's memory in it, not at zero.
observed_after_restore = strategy.capture_state()["seen"]

with TradingSession.resume(restored):
    for record in build_records(spec["asset_id"], spec["remaining"]):
        restored, _ = TradingSession.advance(restored, record, context_factory)

final = serialize(capture_session(restored))
store.put(spec["run_id"], spec["final_sequence"], final)

print(
    json.dumps(
        {
            "pid": os.getpid(),
            "restored_seen": observed_after_restore,
            "final_seen": strategy.capture_state()["seen"],
            "processed": restored.processed,
            "payload_bytes": len(final),
        }
    )
)
'''


def _run_child(script: Path, spec_file: Path) -> dict[str, Any]:
    """Run the continuation in a genuinely separate interpreter."""

    completed = subprocess.run(
        [sys.executable, str(script), str(spec_file), REPO_ROOT],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, (
        f"the child interpreter failed:\n--- stdout ---\n{completed.stdout}\n"
        f"--- stderr ---\n{completed.stderr}"
    )
    report: dict[str, Any] = json.loads(completed.stdout.strip().splitlines()[-1])
    return report


@pytest.fixture(scope="module")
def crossing(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run the full parent -> disk -> child -> disk crossing **once**, and report it.

    Module-scoped deliberately. The crossing is one deterministic event -- a
    seeded run, a real subprocess, and a control -- and every assertion below
    interrogates that same event. Repeating it per test would spawn one
    interpreter per assertion and rebuild the control each time, which buys no
    additional coverage and makes this file the most expensive in the suite.

    Nothing here is mutated by a test: each one reads payload strings and decodes
    its own copy, so sharing the crossing cannot let one test affect another.
    """

    tmp_path = tmp_path_factory.mktemp("crossing")
    root = tmp_path / "runs"
    root.mkdir()
    store = FileRunStateStore(root)
    specs = record_specs()

    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    config = _session_config(STRATEGY_ID)
    with id_scope(SEED):
        partial = TradingSession.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        partial = _drive(partial, build_records(ASSET_ID, specs[:N]))

    checkpoint = serialize(capture_session(partial))
    ref = store.put(RUN_ID, 0, checkpoint)

    spec_file = tmp_path / "spec.json"
    spec_file.write_text(
        json.dumps(
            {
                "root": str(root),
                "run_id": ref.run_id,
                "sequence": ref.sequence,
                "final_sequence": 1,
                "strategy_id": STRATEGY_ID,
                "asset_id": ASSET_ID,
                "remaining": specs[N:],
            }
        ),
        encoding="utf-8",
    )

    script = tmp_path / "continue_run.py"
    script.write_text(_CHILD, encoding="utf-8")
    report = _run_child(script, spec_file)

    return {
        "store": store,
        "checkpoint": checkpoint,
        "partial": partial,
        "child": report,
        "continued": store.get(RunStateRef(RUN_ID, 1)),
        "control": _control_payload(),
        "spec_file": spec_file,
    }


# ---------------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------------


def test_the_continued_payload_is_byte_identical_to_the_uninterrupted_one(
    crossing: dict[str, Any],
) -> None:
    """The v2.13 acceptance invariant, across a real process boundary."""

    assert crossing["continued"] == crossing["control"]


def test_the_workload_is_substantial_enough_to_mean_something(
    crossing: dict[str, Any],
) -> None:
    control = deserialize(crossing["control"])
    pipeline = control["pipeline"]

    assert control["processed"] == TOTAL
    assert len(pipeline["fills"]) >= 5, "the run actually traded"
    assert len(pipeline["oms"]["orders"]) >= 5
    assert len(_identifiers(control)) > 50
    assert pipeline["id_position"]["seed"] == SEED
    assert pipeline["id_position"]["draws"] > 0
    assert len(crossing["control"]) > 10_000, "a payload large enough to be a real state"


def test_the_child_was_a_different_process(crossing: dict[str, Any]) -> None:
    """Not a fork, and not this interpreter."""

    assert crossing["child"]["pid"] != os.getpid()


def test_the_child_was_told_everything_in_json_and_nothing_by_pickle(
    crossing: dict[str, Any],
) -> None:
    """The transport is inspectable text; no live object crossed."""

    raw = Path(crossing["spec_file"]).read_text(encoding="utf-8")
    spec = json.loads(raw)

    assert set(spec) == {
        "root",
        "run_id",
        "sequence",
        "final_sequence",
        "strategy_id",
        "asset_id",
        "remaining",
    }
    assert all(isinstance(value, str | int | list) for value in spec.values())


def test_the_child_restored_the_strategys_memory_rather_than_starting_empty(
    crossing: dict[str, Any],
) -> None:
    """The precondition the workload is built to make load-bearing."""

    assert crossing["child"]["restored_seen"] == N, "a fresh instance would have reported 0"
    assert crossing["child"]["final_seen"] == TOTAL
    assert crossing["child"]["processed"] == TOTAL


def test_no_record_was_processed_twice(crossing: dict[str, Any]) -> None:
    """Continuation starts at N+1, never by replaying N."""

    continued = deserialize(crossing["continued"])

    assert continued["processed"] == TOTAL
    assert len(continued["pipeline"]["market"]["events"]) == TOTAL
    assert len(continued["pipeline"]["portfolio_snapshots"]) == TOTAL + 1, "one each, plus funding"
    event_ids = [event["event"]["event_id"] for event in continued["pipeline"]["market"]["events"]]
    assert len(set(event_ids)) == TOTAL


# ---------------------------------------------------------------------------
# Failure localization: ADR-0023 Class 1, read off the two payloads
# ---------------------------------------------------------------------------


def _identifiers(document: Mapping[str, Any]) -> list[str]:
    """Every deterministic identifier the payload records, in a stable order."""

    pipeline = document["pipeline"]
    return [
        *(str(order["order_id"]["value"]) for order in pipeline["oms"]["orders"]),
        *(str(fill["fill_id"]) for fill in pipeline["fills"]),
        *(str(trade["trade_id"]) for trade in pipeline["trades"]),
        *(report["execution_id"] for report in pipeline["execution"]["history"]),
        *(txn["transaction_id"] for txn in pipeline["portfolio"]["transactions"]),
        *(event["event"]["event_id"] for event in pipeline["allocation"]["events"]),
        *(decision["decision_id"] for decision in pipeline["risk"]["history"]),
        *(event["event"]["event_id"] for event in pipeline["oms"]["events"]),
    ]


#: Class-1 subtrees, named so a failure says which subsystem moved.
CLASS_ONE = (
    "portfolio",
    "oms",
    "allocation",
    "risk",
    "execution",
    "analytics",
    "market",
    "market_prices",
    "fills",
    "trades",
    "trade_records",
    "portfolio_snapshots",
    "unpriced_assets",
    "strategy",
    "strategy_events",
    "id_position",
)


@pytest.mark.parametrize("subtree", CLASS_ONE)
def test_each_class_one_subtree_is_identical(crossing: dict[str, Any], subtree: str) -> None:
    continued = deserialize(crossing["continued"])["pipeline"]
    control = deserialize(crossing["control"])["pipeline"]

    assert continued[subtree] == control[subtree]


@pytest.mark.parametrize(
    "field", ["processed", "current_timestamp", "last_record_timestamp", "skipped", "source_id"]
)
def test_each_session_bookkeeping_field_is_identical(crossing: dict[str, Any], field: str) -> None:
    assert deserialize(crossing["continued"])[field] == deserialize(crossing["control"])[field]


def test_cash_and_positions_are_identical(crossing: dict[str, Any]) -> None:
    """Named separately because it is the first thing anyone will want to see."""

    continued = deserialize(crossing["continued"])["pipeline"]["portfolio"]
    control = deserialize(crossing["control"])["pipeline"]["portfolio"]

    assert continued["balances"] == control["balances"]
    assert continued["positions"] == control["positions"]
    assert continued["realized_pnl"] == control["realized_pnl"]
    assert continued["commission_paid"] == control["commission_paid"]
    assert continued["reserved"] == control["reserved"]


def test_the_execution_idempotence_ledger_is_identical(crossing: dict[str, Any]) -> None:
    """``execution.reports`` is what makes a redelivered venue fill a no-op (ADR-0024)."""

    continued = deserialize(crossing["continued"])["pipeline"]["execution"]["reports"]
    control = deserialize(crossing["control"])["pipeline"]["execution"]["reports"]

    assert continued == control
    assert continued, "the ledger is populated, so comparing it means something"


def test_the_declared_strategy_state_is_identical(crossing: dict[str, Any]) -> None:
    continued = deserialize(crossing["continued"])["pipeline"]["strategy"]
    control = deserialize(crossing["control"])["pipeline"]["strategy"]

    assert continued == control
    assert continued[0]["state"]["payload"]["seen"] == TOTAL
    assert continued[0]["state"]["version"] == 1


def test_the_identifier_stream_is_identical_and_holds_no_duplicate(
    crossing: dict[str, Any],
) -> None:
    continued = deserialize(crossing["continued"])
    control = deserialize(crossing["control"])

    identifiers = _identifiers(continued)
    assert identifiers == _identifiers(control)
    assert len(set(identifiers)) == len(identifiers)
    assert continued["pipeline"]["id_position"] == control["pipeline"]["id_position"]


def test_every_event_log_is_in_the_same_order(crossing: dict[str, Any]) -> None:
    """Class 1 includes the *order* of every event log, not only its contents."""

    continued = deserialize(crossing["continued"])["pipeline"]
    control = deserialize(crossing["control"])["pipeline"]

    for subsystem in ("market", "risk", "execution", "allocation", "oms", "portfolio"):
        node = continued[subsystem]
        for key in ("events", "history"):
            if key in node:
                assert node[key] == control[subsystem][key], f"{subsystem}.{key}"


# ---------------------------------------------------------------------------
# The checkpoint itself, and what the store did to it
# ---------------------------------------------------------------------------


def test_the_stored_checkpoint_is_unchanged_by_storage(crossing: dict[str, Any]) -> None:
    """The store is transparent: what came out is what went in."""

    store: FileRunStateStore = crossing["store"]

    assert store.get(RunStateRef(RUN_ID, 0)) == crossing["checkpoint"]


def test_both_checkpoints_are_addressable_and_ordered(crossing: dict[str, Any]) -> None:
    store: FileRunStateStore = crossing["store"]

    assert store.list_runs() == (RUN_ID,)
    assert store.latest(RUN_ID) == RunStateRef(RUN_ID, 1)
    assert store.get(RunStateRef(RUN_ID, 1)) == crossing["continued"]


def test_the_checkpoint_is_a_partial_run_not_the_finished_one(
    crossing: dict[str, Any],
) -> None:
    """The crossing genuinely happened mid-run."""

    checkpoint = deserialize(crossing["checkpoint"])

    assert checkpoint["processed"] == N
    assert checkpoint["pipeline"]["id_position"]["draws"] > 0
    assert checkpoint["pipeline"]["strategy"][0]["state"]["payload"]["seen"] == N


def test_the_child_read_a_state_written_by_a_process_that_had_exited(
    crossing: dict[str, Any],
) -> None:
    """Durability, not shared memory: a third store object reads both checkpoints."""

    reopened = FileRunStateStore(crossing["store"].root)

    assert reopened.get(RunStateRef(RUN_ID, 0)) == crossing["checkpoint"]
    assert reopened.get(RunStateRef(RUN_ID, 1)) == crossing["control"]


# ---------------------------------------------------------------------------
# The schemas the crossing did not move
# ---------------------------------------------------------------------------


def test_the_crossing_moved_no_schema(crossing: dict[str, Any]) -> None:
    continued = deserialize(crossing["continued"])

    assert continued["schema_version"] == 1, "SESSION_SNAPSHOT_SCHEMA"
    assert continued["pipeline"]["schema_version"] == 2, "PIPELINE_SNAPSHOT_SCHEMA"
    assert continued["pipeline"]["oms"]["schema_version"] == 1
    assert continued["pipeline"]["portfolio"]["schema_version"] == 2
    assert continued["pipeline"]["allocation"]["schema_version"] == 1


def test_the_payload_carries_no_run_identity(crossing: dict[str, Any]) -> None:
    """``run_id`` is the caller's and the store's; it is not in any captured state."""

    assert "run_id" not in crossing["control"]
    assert RUN_ID not in crossing["control"]
