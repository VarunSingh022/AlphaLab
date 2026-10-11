"""
AlphaLab Examples
=================

Example 71 : Pause a Replay, Keep It, and Resume It

Difficulty : Intermediate

Estimated Time : 10 minutes

Topics
------

• Replaying a dataset through the execution path, and pausing it between two records
• Capturing the paused replay and keeping it in a file store
• Restoring it with freshly built objects, as a new process would, and resuming it
• Checking that the resumed replay is the uninterrupted one, byte for byte
• What is refused: another dataset, altered bytes, another configuration

What this shows
---------------

Replay pause and resume (v4.1, ledger FUT-001), through supported interfaces
only: ``alphalab.api`` for the data and the replay, ``alphalab.backtesting.replay_snapshot``
for the paused replay's envelope, ``alphalab.persistence`` for the store and
``alphalab.lifecycle.digest_run`` for the result's identity. The closes are
**synthetic**, written out so a crossover happens; nothing here is a claim about
returns.

A replay pauses at a boundary *between* records -- here, when ``pause_when``
says so -- and returns a ``PausedReplay`` instead of a result. Its envelope
records the run, the replay cursor and the cursor's own identifier stream, and
what the dataset must be; the dataset itself and the live objects (strategy,
simulator, sizing model, fill policy) are supplied again when it is restored.

Run

    python examples/71_pause_and_resume_a_replay.py
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import ingest_rows, replay, resume_replay, to_market_dataset
from alphalab.backtesting import (
    ExecutionMode,
    MarketDataset,
    PausedReplay,
    ReplayProgress,
    ReplayResult,
    RunConfig,
)
from alphalab.backtesting.replay_snapshot import capture, from_primitives, restore
from alphalab.core.enums import AssetType
from alphalab.data import (
    REFUSE_EVERYTHING,
    DataAssetClass,
    Dataset,
    IngestionRequest,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.time import BarStamp
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.persistence import (
    FileRunStateStore,
    StateDecodeError,
    StorageError,
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
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    IntentKind,
    RuntimeState,
    StrategyContext,
    context_factory,
    create_runtime,
    start_strategy,
)

STRATEGY_ID = "EX71-MA-CROSS"
SYMBOL = "SYNTH"
PROVIDER = "synthetic"
SEED = 71
START_CASH = Decimal("100000.00")
COMMISSION = Decimal("0.01")
DAY = 86_400.0
FIRST_BAR = 1_704_153_600.0
RUN_ID = "example-71"
PAUSE_AFTER = 13

#: Thirty synthetic daily closes: a rise, a fall, a second rise. Hand-written.
CLOSES = tuple(
    str(close)
    for close in (
        100, 100, 101, 103, 106, 108, 107, 104, 100, 97, 95, 94, 95, 97, 100,
        104, 107, 109, 108, 105, 101, 98, 96, 97, 99, 102, 105, 106, 104, 103,
    )
)  # fmt: skip

INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XSYN", "USD", aliases={PROVIDER: SYMBOL})
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XSYN",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=REGISTRY,
    provider=PROVIDER,
)
CONTEXTS = context_factory(FixedClock(FIRST_BAR), DiscardingLogger())


class Crossover(BaseStrategy):
    """Hold ten shares while the 2-bar average is above the 5-bar one; remember the closes."""

    def __init__(self) -> None:
        self._closes: list[Decimal] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        self._closes.append(bar.close)
        if len(self._closes) < 5:
            return ()
        fast, slow = sum(self._closes[-2:]) / 2, sum(self._closes[-5:]) / 5
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=INSTRUMENT.asset_id,
                target=Decimal("10") if fast > slow else Decimal("0"),
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )

    # Its memory is its declared state, so a paused replay carries it.
    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> dict[str, list[str]]:
        return {"closes": [str(close) for close in self._closes]}

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"Crossover cannot read state version {version}")
        self._closes = [Decimal(close) for close in payload["closes"]]


def dataset_of(closes: Sequence[str]) -> Dataset:
    rows = [
        {
            "timestamp": str(FIRST_BAR + index * DAY),
            "symbol": SYMBOL,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": "1000000",
        }
        for index, close in enumerate(closes)
    ]
    request = IngestionRequest(
        name="EX71-SYNTHETIC",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "synthetic://example-71", b"", FIRST_BAR, "application/json"
        ),
        frequency=TimeFrequency.DAILY,
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=REFUSE_EVERYTHING,
        price_basis=PriceBasis.RAW,
    )
    return ingest_rows(rows, request).dataset


def runtime(instance: BaseStrategy) -> RuntimeState:
    return start_strategy(
        create_runtime(),
        STRATEGY_ID,
        instance,
        config={},
        subscriptions={"bars"},
        at=FIRST_BAR - 1.0,
    )


def run_config(commission: Decimal = COMMISSION) -> RunConfig:
    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.REPLAY,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX71", "USD", "Example 71", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("1")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, "UTC"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(commission)),
            instruments=REGISTRY,
        ),
        seed=SEED,
        start_timestamp=FIRST_BAR - 1.0,
    )


def fresh_objects(commission: Decimal = COMMISSION) -> RunObjects:
    """What a new process builds again: a snapshot records these, it does not carry them."""

    config = run_config(commission)
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: Crossover()},
            instruments=REGISTRY,
        ),
        fill_policy=config.fill_policy,
    )


def pause_after(records: int) -> Any:
    def ask(progress: ReplayProgress) -> bool:
        return progress.records_replayed == records

    return ask


def paused_replay() -> PausedReplay:
    paused = replay(
        run_config(),
        dataset_of(CLOSES),
        runtime(Crossover()),
        CONTEXTS,
        NORMALIZATION,
        pause_when=pause_after(PAUSE_AFTER),
    )
    assert isinstance(paused, PausedReplay)
    return paused


def market_dataset(closes: Sequence[str] = CLOSES) -> MarketDataset:
    return to_market_dataset(dataset_of(closes), NORMALIZATION)


def resumed_from(store: FileRunStateStore) -> ReplayResult:
    """Read the newest checkpoint and continue it -- everything a new process would do."""

    latest = store.latest(RUN_ID)
    assert latest is not None
    restored = restore(
        from_primitives(deserialize(store.get(latest))), fresh_objects(), market_dataset()
    )
    return resume_replay(restored, CONTEXTS)


def refusals(store: FileRunStateStore) -> dict[str, str]:
    refused: dict[str, str] = {}
    latest = store.latest(RUN_ID)
    assert latest is not None
    snapshot = from_primitives(deserialize(store.get(latest)))

    def attempt(label: str, action: Any) -> None:
        try:
            action()
        except (StateDecodeError, StorageError) as error:
            refused[label] = f"{type(error).__name__}: {str(error)[:80]}"
        else:
            refused[label] = "ACCEPTED"

    changed = (*CLOSES[:20], "999", *CLOSES[21:])
    attempt(
        "another dataset (one close changed)",
        lambda: restore(snapshot, fresh_objects(), market_dataset(changed)),
    )
    attempt(
        "another commission",
        lambda: restore(snapshot, fresh_objects(Decimal("5.00")), market_dataset()),
    )
    (stored,) = list(store.root.rglob("*.runstate"))
    text = stored.read_text(encoding="utf-8")
    stored.write_text(text.replace('"processed":13', '"processed":12', 1), encoding="utf-8")
    attempt("bytes altered in the store", lambda: store.get(latest))
    stored.write_text(text, encoding="utf-8")
    return refused


def main() -> None:
    print("=" * 70)
    print("AlphaLab Example 71 : Pause a Replay, Keep It, and Resume It")
    print("=" * 70)
    print("Synthetic prices, hand-written; no claim about profitability.")

    whole = replay(run_config(), dataset_of(CLOSES), runtime(Crossover()), CONTEXTS, NORMALIZATION)
    identity = digest_run(whole.backtest).result_id
    print()
    print("1. Uninterrupted")
    print(f"   records         : {whole.records_replayed}")
    print(f"   fills           : {len(whole.backtest.fills)}")
    print(f"   result id       : {identity}")

    paused = paused_replay()
    print()
    print("2. Paused")
    print(f"   after           : {paused.records_replayed} of {paused.records_total} records")
    print(f"   fills so far    : {len(paused.run.pipeline.fills)}")

    with tempfile.TemporaryDirectory(prefix="alphalab-example-71-") as root:
        store = FileRunStateStore(Path(root))
        ref = store.put(RUN_ID, 1, serialize(capture(paused)))
        print(f"   kept            : sequence {ref.sequence} of run {ref.run_id!r}")

        resumed = resumed_from(store)
        assert digest_run(resumed.backtest).result_id == identity, "byte for byte"
        assert resumed.backtest.fills == whole.backtest.fills
        print()
        print("3. Restored with fresh objects and resumed")
        print(f"   records         : {resumed.records_replayed}")
        print(f"   result id       : {digest_run(resumed.backtest).result_id}")
        print("   the same result id as the uninterrupted replay")

        print()
        print("4. Refused")
        for label, outcome in refusals(store).items():
            assert outcome != "ACCEPTED", label
            print(f"   {label:<36}: {outcome}")

    chain: ReplayResult | PausedReplay = replay(
        run_config(),
        dataset_of(CLOSES),
        runtime(Crossover()),
        CONTEXTS,
        NORMALIZATION,
        pause_when=lambda progress: progress.records_replayed % 5 == 0,
    )
    pauses = 0
    while isinstance(chain, PausedReplay):
        pauses += 1
        payload = serialize(capture(chain))
        chain = restore(from_primitives(deserialize(payload)), fresh_objects(), market_dataset())
        chain = resume_replay(chain, CONTEXTS, pause_when=lambda p: p.records_replayed % 5 == 0)
    assert digest_run(chain.backtest).result_id == identity
    print()
    print("5. Paused every five records, each time through a payload")
    print(f"   pauses          : {pauses}; the same result id")


if __name__ == "__main__":
    main()
