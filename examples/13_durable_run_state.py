"""
AlphaLab Examples
=================

Example 13 : Durable Run State

Difficulty : Intermediate

Estimated Time : 5 minutes

Topics
------

• Capturing a session mid-run
• Storing the payload with FileRunStateStore
• Continuing the run in a *different* process
• Byte-identity against an uninterrupted run

What this shows
---------------

A run stopping, being written to disk, and finishing somewhere else:

    seeded session          -> five records processed here
      -> capture            -> a complete, JSON-serializable projection
      -> serialize          -> one deterministic string
      -> store.put          -> on disk, under (run_id, sequence)
    ...a new interpreter, sharing no memory with the first...
      -> store.get          -> the same string, digest-verified
      -> from_primitives    -> typed snapshot values
      -> restore            -> the session, with fresh runtime objects
      -> resume + advance   -> the remaining six records
      -> store.put          -> the finished run

The last thing the script does is compare that finished run against one that
never stopped. The two serialized payloads are **byte-identical** -- same cash,
same positions, same orders, same fills, and the same deterministic identifiers.

`capture` / `restore` are not new: they have covered `ExecutionPipelineState`,
`SessionState` and `BacktestState` since v2.9. What v2.13 adds is somewhere to
put the result. See ADR-0029.

Three things worth noticing
---------------------------

**The store never looks inside the payload.** It moves a `str`. It does not
import a snapshot type and does not decode one, which is why a later release
that reshapes `SessionState` will not touch it.

**The run identity is yours.** `run_id` is an opaque string you supply to
`put`. It is not written into any captured state, and no snapshot schema moved
to accommodate it.

**Persisting costs no identifiers.** The store draws nothing from the run's
deterministic identifier stream, which is what lets you checkpoint in the middle
of a seeded run and still reproduce it exactly.

The child process below builds its **own** simulator, sizing model, fill policy
and strategy instance. A snapshot records those by type and requires the caller
to supply them back; it never carries the objects themselves.

Run

    python examples/13_durable_run_state.py

Requires

    Nothing beyond the standard library and AlphaLab itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.common.ids import id_scope
from alphalab.execution.policy import ImmediateFill
from alphalab.market.quote import Quote
from alphalab.market.record import MarketRecord
from alphalab.persistence import (
    FileRunStateStore,
    RunStateRef,
    deserialize,
    serialize,
)
from alphalab.portfolio.account import Account
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.session import ExecutionMode, SessionConfig, SessionState, TradingSession
from alphalab.runtime.session_snapshot import SessionObjects
from alphalab.runtime.session_snapshot import capture as capture_session
from alphalab.runtime.session_snapshot import from_primitives as session_from_primitives
from alphalab.runtime.session_snapshot import restore as restore_session
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

SEED = 20260915
STRATEGY_ID = str(uuid.UUID(int=0x1300_0001))
ASSET_ID = str(uuid.UUID(int=0x1300_0002))
RUN_ID = "example-13-durable-run"
STARTING_CASH = Decimal("1000000")
BEFORE, AFTER = 5, 6

#: Set on the child, so the one script can be both halves of the example.
CHILD_ENV = "ALPHALAB_EXAMPLE_13_SPEC"


# ---------------------------------------------------------------------------
# A small strategy that owns durable state, so restoring it visibly matters
# ---------------------------------------------------------------------------


class AlternatingStrategy(BaseStrategy):
    """Buys, then sells, then buys -- driven by a counter it owns.

    The counter is the point. It decides *which* records trade, so a continued
    run that forgot it would trade the wrong ones. Declaring
    ``strategy_state_version`` / ``capture_state`` / ``restore_state`` is how a
    strategy tells the runtime that it has memory worth carrying across a stop.
    """

    def __init__(self, strategy_id: str, asset_id: str) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._seen = 0

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self._seen += 1
        if self._seen % 2 == 0:
            return ()
        quantity = Decimal("10") if self._seen % 4 == 1 else Decimal("-6")
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=quantity,
                timestamp=event.quote.timestamp,
            ),
        )

    # The durable-state contract. Both directions are required.
    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> Mapping[str, Any]:
        return {"seen": self._seen}

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"AlternatingStrategy cannot read state version {version}")
        self._seen = int(payload["seen"])


# ---------------------------------------------------------------------------
# Configuration, built the same way in both processes
# ---------------------------------------------------------------------------


def build_config() -> SessionConfig:
    """The run's configuration. Values here; live objects are supplied on restore."""

    limit = Decimal("100000000")
    pipeline = ExecutionPipelineConfig(
        account=Account("example-13", "USD", "Durable Run State Example", 1.0),
        starting_cash=STARTING_CASH,
        budget=CapitalBudget(
            global_capital=STARTING_CASH,
            maximum_exposure=STARTING_CASH * Decimal("10"),
            cash_buffer=Decimal("0"),
            strategy_budgets={STRATEGY_ID: STARTING_CASH},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=RiskLimits(
            order_size=OrderSizeLimit(limit, limit),
            position=PositionLimit(limit, limit),
            exposure=ExposureLimit(limit, limit),
            leverage=LeverageLimit(Decimal("1000")),
            margin=MarginLimit(Decimal("1.00")),
            daily_loss=DailyLossLimit(limit),
            drawdown=DrawdownLimit(Decimal("1.00")),
        ),
    )
    return SessionConfig(
        pipeline=pipeline,
        mode=ExecutionMode.BACKTEST,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
    )


def build_strategy_state(strategy: AlternatingStrategy) -> StrategyRuntimeState:
    """Register the strategy and walk it to RUNNING."""

    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    entry = state.strategies[STRATEGY_ID]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return StrategyRuntimeState(strategies={STRATEGY_ID: entry}, events=state.events)


def build_runtime_objects(strategy: AlternatingStrategy) -> SessionObjects:
    """The four live objects a snapshot records by type and never carries.

    Built fresh here. `restore` checks that each is of the recorded class and
    refuses anything else -- it never substitutes a default.
    """

    pipeline = build_config().pipeline
    return SessionObjects(
        pipeline=RuntimeObjects(
            sizing_model=pipeline.sizing_model,
            simulator=pipeline.simulator,
            strategies={STRATEGY_ID: strategy},
            instruments=pipeline.instruments,
        ),
        fill_policy=ImmediateFill(),
    )


def context_factory(strategy_id: str) -> StrategyContext:
    """The pipeline overlays portfolio, orders, risk and market onto this."""

    class _Clock:
        def now(self) -> float:
            return 0.0

    class _Logger:
        def info(self, message: str) -> None: ...

        def error(self, message: str) -> None: ...

    return StrategyContext(
        portfolio=object(),
        market=object(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=object(),
        config={"strategy_id": strategy_id},
        orders=object(),
        history=object(),
        universe=object(),
    )


def records(start: int, count: int) -> list[MarketRecord]:
    """Deterministic quotes, so both processes see exactly the same market."""

    return [
        MarketRecord(
            event_id=f"REC-{index}",
            timestamp=2.0 + index,
            payload=Quote(
                asset_id=ASSET_ID,
                timestamp=2.0 + index,
                bid=Decimal(100 + index),
                ask=Decimal(100 + index),
                bid_size=Decimal("500"),
                ask_size=Decimal("500"),
                venue="SIM",
                currency="USD",
            ),
        )
        for index in range(start, start + count)
    ]


def drive(state: SessionState, batch: Iterable[MarketRecord]) -> SessionState:
    for record in batch:
        state, _ = TradingSession.advance(state, record, context_factory)
    return state


# ---------------------------------------------------------------------------
# The child half: a fresh interpreter that continues a stored run
# ---------------------------------------------------------------------------


def continue_stored_run(spec: Mapping[str, Any]) -> None:
    """Read the run back, continue it, and store the finished state.

    Everything this function touches was constructed in *this* process. The only
    thing that crossed from the parent is the payload on disk, plus a JSON note
    saying where to find it.
    """

    store = FileRunStateStore(spec["root"])
    payload = store.get(RunStateRef(spec["run_id"], spec["sequence"]))

    strategy = AlternatingStrategy(STRATEGY_ID, ASSET_ID)
    restored = restore_session(
        session_from_primitives(deserialize(payload)), build_runtime_objects(strategy)
    )

    # The strategy's own memory came back with the payload, not at zero.
    print(
        f"    child pid {os.getpid()}: restored a run that had processed "
        f"{restored.processed} records"
    )
    print(
        f"    child pid {os.getpid()}: the strategy remembers seeing "
        f"{strategy.capture_state()['seen']} quotes"
    )

    # `resume` rebuilds the identifier stream at the position the payload
    # recorded, so the continued run does not re-mint identifiers it already used.
    with TradingSession.resume(restored):
        restored = drive(restored, records(spec["resume_at"], spec["remaining"]))

    store.put(spec["run_id"], spec["final_sequence"], serialize(capture_session(restored)))
    print(f"    child pid {os.getpid()}: finished at {restored.processed} records and stored it")


# ---------------------------------------------------------------------------
# The parent half
# ---------------------------------------------------------------------------


def uninterrupted() -> str:
    """The control: one run over every record, never stopped."""

    strategy = AlternatingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = TradingSession.initialize(build_config(), build_strategy_state(strategy))
        state = drive(state, records(0, BEFORE + AFTER))
    return serialize(capture_session(state))


def main() -> None:
    print("=" * 72)
    print("AlphaLab Example 13 : Durable Run State")
    print("=" * 72)

    with tempfile.TemporaryDirectory(prefix="alphalab-example-13-") as workspace:
        root = Path(workspace) / "runs"
        # The store requires a root that already exists. It will not create one,
        # and it will not quietly fall back to memory if it cannot write.
        root.mkdir()
        store = FileRunStateStore(root)

        print(f"\n[1] Run {BEFORE} records, then stop.")
        strategy = AlternatingStrategy(STRATEGY_ID, ASSET_ID)
        with id_scope(SEED):
            state = TradingSession.initialize(build_config(), build_strategy_state(strategy))
            state = drive(state, records(0, BEFORE))
        print(f"    processed  : {state.processed}")
        print(f"    cash       : {state.pipeline.portfolio.cash.balance('USD')}")
        print(f"    orders     : {len(list(state.pipeline.oms.orders.orders()))}")
        print(f"    id position: {state.pipeline.id_position}")

        print("\n[2] Capture, serialize, and store it.")
        payload = serialize(capture_session(state))
        ref = store.put(RUN_ID, 0, payload)
        print(f"    payload    : {len(payload):,} bytes")
        print(f"    reference  : {ref}          <- an identity, not a path")
        print(f"    runs held  : {store.list_runs()}")

        print("\n[3] Continue it in a different interpreter.")
        spec = {
            "root": str(root),
            "run_id": ref.run_id,
            "sequence": ref.sequence,
            "final_sequence": 1,
            "resume_at": BEFORE,
            "remaining": AFTER,
        }
        result = subprocess.run(
            [sys.executable, os.path.abspath(__file__)],
            env={**os.environ, CHILD_ENV: json.dumps(spec)},
            capture_output=True,
            text=True,
            check=False,
        )
        print(result.stdout.rstrip() or "    (child produced no output)")
        if result.returncode != 0:
            print(f"    child failed:\n{result.stderr}")
            raise SystemExit(1)
        print(f"    parent pid {os.getpid()} never shared an object with it.")

        print("\n[4] Compare the finished run against one that never stopped.")
        continued = store.get(RunStateRef(RUN_ID, 1))
        control = uninterrupted()

        decoded = deserialize(continued)
        print(f"    processed  : {decoded['processed']}")
        print(f"    latest ref : {store.latest(RUN_ID)}")
        print(f"    payloads   : {len(continued):,} bytes vs {len(control):,} bytes")
        print(f"    byte-identical to the uninterrupted run : {continued == control}")

        if continued != control:  # pragma: no cover - the example asserts its own claim
            raise SystemExit("the continued run diverged from the control")

    print("\n" + "=" * 72)
    print("A run stopped in one process and finished in another, exactly.")
    print("Deferred, and not demonstrated here: artifact byte storage, cloud")
    print("backends, streaming sources, and live venue execution.")
    print("=" * 72)


if __name__ == "__main__":
    _spec = os.environ.get(CHILD_ENV)
    if _spec:
        continue_stored_run(json.loads(_spec))
    else:
        main()
