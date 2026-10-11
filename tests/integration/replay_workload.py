"""One replay workload, driven in this process and in fresh ones (v4.1, ledger FUT-001).

A pause-and-resume test is only as strong as the workload it pauses. This one
is built so that a duplicated or omitted step changes the result:

* **Two instruments quoted at every instant.** Every other boundary falls
  *inside* an instant, so a resumed replay must close that instant's slice
  exactly once, after its second record -- not at the pause, and not twice.
* **A strategy with memory.** :class:`RememberingStrategy` trades on the change
  in the mid it saw last and sizes each order by the slices and fills it has
  seen; all three are its declared durable state (``StrategyStateProtocol``),
  so a record, slice or fill delivered twice -- or lost -- moves an order.
* **Partial fills.** :class:`~alphalab.execution.policy.LiquidityCappedFill`
  caps each fill at a share of the size shown, so working orders are open
  across many boundaries.

Run as a module it is the worker the cross-process tests spawn::

    python -m tests.integration.replay_workload uninterrupted OUT
    python -m tests.integration.replay_workload pause K STORE
    python -m tests.integration.replay_workload resume STORE OUT
    python -m tests.integration.replay_workload checkpoint-and-crash EVERY CRASH_AT STORE

``uninterrupted`` and ``resume`` write :func:`outcome` to ``OUT``; ``pause``
stores the paused replay's payload in a :class:`FileRunStateStore` at
``STORE``; ``checkpoint-and-crash`` stores a checkpoint every ``EVERY`` records
and ends its own process abruptly (``os._exit``, exit status 86) once
``CRASH_AT`` records are in.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.backtesting import replay_snapshot
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay import PausedReplay, ReplayBacktest, ReplayProgress
from alphalab.backtesting.state import ReplayResult
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import FillPolicy, LiquidityCappedFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.lifecycle import digest_run
from alphalab.oms.snapshot import capture as capture_oms
from alphalab.persistence import FileRunStateStore, deserialize, serialize
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, Intent, SliceClosed
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = "6a0e6c1e-6b0f-4d5e-9a51-2f4d1f3e9a01"
FIRST, SECOND = "0b7c3a52-3e43-4d0e-8f6c-1a9e5d2c7b10", "c3d9f8e2-71a4-4b5c-a6d7-9e8f1b2c3d20"
SEED = 41_000_001
DATASET_ID = "FUT001-REPLAY"
INSTANTS = 12
#: Records: two per instant.
TOTAL = 2 * INSTANTS
COMMISSION = Decimal("0.01")
RUN_ID = "fut-001-replay"


class RememberingStrategy(BaseStrategy):
    """Trades the change in each instrument's mid, sized by what it has seen.

    Every decision reads its memory, and its memory is its declared state, so
    the continuation a test checks is the strategy's as well as the engine's.
    """

    def __init__(self) -> None:
        self._last: dict[str, str] = {}
        self._slices: list[float] = []
        self._fills = 0

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote = event.quote
        mid = (quote.bid + quote.ask) / 2
        previous = self._last.get(quote.asset_id)
        self._last[quote.asset_id] = str(mid)
        if previous is None or Decimal(previous) == mid:
            return ()
        size = Decimal(1 + len(self._slices) % 3 + self._fills % 2)
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=quote.asset_id,
                target=size if mid > Decimal(previous) else -size,
                timestamp=quote.timestamp,
            ),
        )

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        self._slices.append(event.timestamp)
        return ()

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        self._fills += 1
        return ()

    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> dict[str, Any]:
        return {"last": dict(self._last), "slices": list(self._slices), "fills": self._fills}

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"RememberingStrategy cannot read state version {version}")
        self._last = {str(key): str(value) for key, value in payload["last"].items()}
        self._slices = [float(instant) for instant in payload["slices"]]
        self._fills = int(payload["fills"])


def mids(instants: int = INSTANTS) -> list[tuple[Decimal, Decimal]]:
    """Two mids per instant, moving both ways, sometimes not at all."""

    return [
        (Decimal(100 + (index * 7) % 5), Decimal(50 + (index * 3) % 4)) for index in range(instants)
    ]


def dataset(instants: int = INSTANTS, dataset_id: str = DATASET_ID) -> MarketDataset:
    """Both instruments quoted at each instant, the first one first."""

    inputs = []
    for index, (first, second) in enumerate(mids(instants)):
        timestamp = 10.0 + index
        inputs.append(sized_quote(FIRST, timestamp, first, Decimal("3")))
        inputs.append(sized_quote(SECOND, timestamp, second, Decimal("2")))
    return MarketDataset.of(dataset_id, inputs)


def config(
    seed: int | None = SEED,
    commission: Decimal = COMMISSION,
    fill_policy: FillPolicy | None = None,
) -> RunConfig:
    return RunConfig(
        pipeline=pipeline_config(
            STRATEGY_ID,
            simulator=ExecutionSimulator(commission_model=PerShareCommission(commission)),
        ),
        mode=ExecutionMode.BACKTEST,
        fill_policy=fill_policy
        if fill_policy is not None
        else LiquidityCappedFill(participation_rate=Decimal("0.5")),
        seed=seed,
        start_timestamp=1.0,
        compile_analytics=True,
    )


def strategy_state(instance: BaseStrategy | None = None) -> StrategyRuntimeState:
    return running_strategy_state(
        STRATEGY_ID,
        RememberingStrategy() if instance is None else instance,
        frozenset({"*", "slices"}),
    )


def objects(
    run_config: RunConfig | None = None, instance: BaseStrategy | None = None
) -> RunObjects:
    """The live objects a fresh process builds again, as a host would."""

    built = config() if run_config is None else run_config
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=built.pipeline.sizing_model,
            simulator=built.pipeline.simulator,
            strategies={STRATEGY_ID: RememberingStrategy() if instance is None else instance},
            instruments=built.pipeline.instruments,
        ),
        fill_policy=built.fill_policy,
    )


def pause_at(*boundaries: int) -> Callable[[ReplayProgress], bool]:
    """Pause when the replay has taken exactly one of ``boundaries`` records."""

    wanted = frozenset(boundaries)
    return lambda progress: progress.records_replayed in wanted


def run(
    pause_when: Callable[[ReplayProgress], bool] | None = None,
    *,
    seed: int | None = SEED,
    instants: int = INSTANTS,
) -> ReplayResult | PausedReplay:
    built = config(seed)
    if pause_when is None:
        return ReplayBacktest.run(built, dataset(instants), strategy_state(), context_factory)
    return ReplayBacktest.run(
        built, dataset(instants), strategy_state(), context_factory, pause_when=pause_when
    )


def uninterrupted(*, seed: int | None = SEED, instants: int = INSTANTS) -> ReplayResult:
    result = run(seed=seed, instants=instants)
    assert isinstance(result, ReplayResult)
    return result


def round_trip(
    paused: PausedReplay, *, instants: int = INSTANTS, run_config: RunConfig | None = None
) -> PausedReplay:
    """Capture, serialize, decode and restore with freshly built objects."""

    payload = serialize(replay_snapshot.capture(paused))
    return replay_snapshot.restore(
        replay_snapshot.from_primitives(deserialize(payload)),
        objects(run_config if run_config is not None else config(paused.run.config.seed)),
        dataset(instants),
    )


def outcome(result: ReplayResult) -> str:
    """Everything a replay produced, in one canonical string.

    The run's identities -- ``result_id`` is a digest of the whole recorded run,
    every step, order, fill, report and lifecycle event included -- and then
    each figure a caller reads, written out so a difference names itself.
    """

    run_result = result.backtest
    digest = digest_run(run_result)
    portfolio = run_result.state.portfolio
    return serialize(
        {
            "result_id": digest.result_id,
            "configuration_id": digest.configuration_id,
            "replay_status": result.replay_status,
            "records_replayed": result.records_replayed,
            "last_record": result.last_record,
            "processed": run_result.records_processed,
            "orders": capture_oms(run_result.state.oms),
            "fills": run_result.fills,
            "trades": run_result.trades,
            "steps": run_result.steps,
            "positions": {
                str(asset): (position.quantity, position.cost_basis)
                for asset, position in sorted(
                    portfolio.positions.items(), key=lambda kv: str(kv[0])
                )
            },
            "cash": run_result.valuation.cash,
            "realized_pnl": run_result.valuation.realized_pnl,
            "unrealized_pnl": run_result.valuation.unrealized_pnl,
            "equity": run_result.valuation.equity,
            "equity_curve": run_result.equity_curve,
            "report": run_result.report,
            "strategy_events": run_result.state.strategy.events,
            "strategy_failures": run_result.strategy_failures,
        }
    )


def _store(path: str) -> FileRunStateStore:
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    return FileRunStateStore(root)


def main(argv: list[str]) -> int:
    command, *arguments = argv
    if command == "uninterrupted":
        Path(arguments[0]).write_text(outcome(uninterrupted()), encoding="utf-8")
    elif command == "pause":
        paused = run(pause_at(int(arguments[0])))
        assert isinstance(paused, PausedReplay)
        _store(arguments[1]).put(RUN_ID, 1, serialize(replay_snapshot.capture(paused)))
    elif command == "resume":
        store = _store(arguments[0])
        latest = store.latest(RUN_ID)
        assert latest is not None, "nothing was stored"
        paused = replay_snapshot.restore(
            replay_snapshot.from_primitives(deserialize(store.get(latest))), objects(), dataset()
        )
        result = ReplayBacktest.resume(paused, context_factory)
        Path(arguments[1]).write_text(outcome(result), encoding="utf-8")
    elif command == "checkpoint-and-crash":
        every, crash_at, store = int(arguments[0]), int(arguments[1]), _store(arguments[2])
        state: ReplayResult | PausedReplay = run(
            lambda progress: progress.records_replayed == every
        )
        sequence = 0
        while isinstance(state, PausedReplay):
            sequence += 1
            store.put(RUN_ID, sequence, serialize(replay_snapshot.capture(state)))
            if state.records_replayed >= crash_at:
                # No orderly exit: no ``finally``, no ``atexit``, no flush -- the
                # process dies holding a paused replay in memory, as a host killed
                # by its scheduler would. ``os._exit`` does this on every platform.
                os._exit(86)
            state = ReplayBacktest.resume(
                state, context_factory, pause_when=pause_at(state.records_replayed + every)
            )
        raise AssertionError("the replay finished before it crashed")
    else:
        raise SystemExit(f"unknown command {command!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
