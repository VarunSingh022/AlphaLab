"""The engine contract a consuming application relies on, checked through public names (v4.1).

``docs/INTEGRATION.md`` states what an application -- iluvtrade first -- may rely
on. This script exercises each part of it the way such an application would:
only names the public-API manifest records, no fixture or helper from the test
suite, no network. ``tests/installed_smoke.py`` runs it inside the fresh
environment that holds a built wheel or sdist and nothing else, from outside the
checkout, so what it checks is the artifact; ``tests/regression/test_consumer_contract.py``
runs it against the source tree, so it cannot rot between releases.

Each check prints one line. Run it directly with::

    python tests/consumer_contract.py EXPECTED_VERSION

Not collected by pytest (the name does not start with ``test_``).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import warnings
from collections.abc import Callable, Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

import alphalab
from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import (
    BacktestEngine,
    ExecutionMode,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    RunConfig,
    replay_snapshot,
)
from alphalab.core.enums import AssetType
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.quote import Quote
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
from alphalab.runtime import run_snapshot
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    RuntimeState,
    StrategyContext,
    StrategyStatus,
    context_factory,
    create_runtime,
    start_strategy,
)

SOURCE_TREE = Path(__file__).resolve().parents[1]
STRATEGY_ID = "CONSUMER-STRATEGY"
VENUE = "XSYN"
SEED = 2026
INSTRUMENT = InstrumentRecord("CONS", AssetType.EQUITY, VENUE, "USD")
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))
CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


class Momentum(BaseStrategy):
    """Buys after a rise, sells after a fall; its last mid is its declared state."""

    def __init__(self, size: Decimal = Decimal("4")) -> None:
        self._size = size
        self._last: str | None = None

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote = event.quote
        mid = (quote.bid + quote.ask) / 2
        previous, self._last = self._last, str(mid)
        if previous is None or Decimal(previous) == mid:
            return ()
        delta = self._size if mid > Decimal(previous) else -self._size
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=quote.asset_id,
                target=delta,
                timestamp=quote.timestamp,
            ),
        )

    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> dict[str, str | None]:
        return {"last": self._last}

    def restore_state(self, payload: Any, version: int) -> None:
        self._last = payload["last"]


def dataset(dataset_id: str = "CONSUMER", count: int = 40) -> MarketDataset:
    quotes = []
    for index in range(count):
        mid = Decimal(100 + (index * 7) % 9)
        quotes.append(
            Quote(
                asset_id=INSTRUMENT.asset_id,
                timestamp=1_000.0 + index,
                bid=mid,
                ask=mid,
                bid_size=Decimal("50"),
                ask_size=Decimal("50"),
                venue=VENUE,
                currency="USD",
            )
        )
    return MarketDataset.of(dataset_id, quotes)


def config(
    mode: ExecutionMode = ExecutionMode.REPLAY,
    commission: Decimal = Decimal("0.01"),
    max_order: Decimal = Decimal("1000"),
) -> RunConfig:
    cash, ample = Decimal("100000"), Decimal("1000000000")
    return RunConfig(
        mode=mode,
        pipeline=ExecutionPipelineConfig(
            account=Account("CONSUMER", "USD", "Consumer contract", 1.0),
            starting_cash=cash,
            budget=CapitalBudget(
                global_capital=cash,
                maximum_exposure=cash * 10,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: cash},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(max_order, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("10")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, "UTC"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(commission)),
            instruments=REGISTRY,
        ),
        seed=SEED,
        start_timestamp=999.0,
    )


def runtime(instance: BaseStrategy | None = None) -> RuntimeState:
    return start_strategy(
        create_runtime(),
        STRATEGY_ID,
        Momentum() if instance is None else instance,
        config={"size": "4"},
        subscriptions={"quotes"},
        at=998.0,
    )


def objects(run_config: RunConfig | None = None) -> RunObjects:
    built = config() if run_config is None else run_config
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=built.pipeline.sizing_model,
            simulator=built.pipeline.simulator,
            strategies={STRATEGY_ID: Momentum()},
            instruments=REGISTRY,
        ),
        fill_policy=built.fill_policy,
    )


def _refused(action: Callable[[], object], *errors: type[BaseException]) -> str:
    try:
        action()
    except errors as error:
        return type(error).__name__
    raise AssertionError(f"accepted what must be refused: {action}")


def check_identity(expected: str) -> str:
    assert alphalab.__version__ == expected, (alphalab.__version__, expected)
    return f"alphalab {alphalab.__version__}"


def check_strategy_lifecycle() -> str:
    state = runtime()
    entry = state.strategies[STRATEGY_ID]
    assert entry.status is StrategyStatus.RUNNING and entry.config == {"size": "4"}
    assert _refused(
        lambda: start_strategy(
            state, STRATEGY_ID, Momentum(), config={}, subscriptions={"quotes"}, at=999.0
        ),
        Exception,
    )
    return "start_strategy reaches RUNNING; a second start under one identity is refused"


def check_backtest_and_replay_agree() -> str:
    backtest = BacktestEngine.run(config(ExecutionMode.BACKTEST), dataset(), runtime(), CONTEXTS)
    replayed = ReplayBacktest.run(config(), dataset(), runtime(), CONTEXTS)
    assert [(f.quantity, f.price) for f in backtest.fills] == [
        (f.quantity, f.price) for f in replayed.backtest.fills
    ]
    assert backtest.valuation.cash == replayed.backtest.valuation.cash
    # Read before any figure: a run whose strategy failed reports figures without it.
    assert backtest.strategy_failures == () and replayed.backtest.strategy_failures == ()
    assert len(backtest.fills) > 5
    return f"backtest and replay: {len(backtest.fills)} identical fills, the same cash"


def check_accounting() -> str:
    result = ReplayBacktest.run(config(), dataset(), runtime(), CONTEXTS).backtest
    valuation = result.valuation
    position = result.state.portfolio.positions.get(INSTRUMENT.asset_id)
    held = Decimal(0) if position is None else position.quantity
    paid = sum((fill.commission for fill in result.fills), Decimal(0))
    spent = sum(
        (
            fill.quantity * fill.price * (1 if fill.side.value == "buy" else -1)
            for fill in result.fills
        ),
        Decimal(0),
    )
    assert valuation.cash == Decimal("100000") - spent - paid, "cash is what the fills say"
    last_mark = Decimal(100 + (39 * 7) % 9)
    assert valuation.equity == valuation.cash + held * last_mark, "equity is cash plus the mark"
    return f"cash = start - fills - commission ({valuation.cash}); equity = cash + {held} x mark"


def check_risk_boundary() -> str:
    refused = ReplayBacktest.run(config(max_order=Decimal("1")), dataset(), runtime(), CONTEXTS)
    assert refused.backtest.fills == (), "an order over the size limit never reaches a fill"
    return "an order above the pre-trade size limit is refused before execution"


def check_pause_resume_in_process() -> str:
    whole = ReplayBacktest.run(config(), dataset(), runtime(), CONTEXTS)
    paused = ReplayBacktest.run(
        config(), dataset(), runtime(), CONTEXTS, pause_when=lambda p: p.records_replayed == 17
    )
    assert isinstance(paused, PausedReplay) and paused.records_replayed == 17
    payload = serialize(replay_snapshot.capture(paused))
    restored = replay_snapshot.restore(
        replay_snapshot.from_primitives(deserialize(payload)), objects(), dataset()
    )
    resumed = ReplayBacktest.resume(restored, CONTEXTS)
    assert digest_run(resumed.backtest).result_id == digest_run(whole.backtest).result_id
    return "a replay paused after 17 records resumes to the uninterrupted result_id"


def check_refusals() -> str:
    paused = ReplayBacktest.run(
        config(), dataset(), runtime(), CONTEXTS, pause_when=lambda p: p.records_replayed == 9
    )
    assert isinstance(paused, PausedReplay)
    snapshot = replay_snapshot.from_primitives(
        deserialize(serialize(replay_snapshot.capture(paused)))
    )
    names = [
        _refused(
            lambda: replay_snapshot.restore(snapshot, objects(), dataset("OTHER")), StateDecodeError
        ),
        _refused(
            lambda: replay_snapshot.restore(
                snapshot, objects(config(commission=Decimal("1"))), dataset()
            ),
            StateDecodeError,
        ),
        _refused(lambda: replay_snapshot.from_primitives({"schema_version": 99}), StateDecodeError),
        _refused(lambda: deserialize("{not json"), Exception),
    ]
    return f"another dataset, another commission, another schema, corrupt JSON: {names}"


def check_run_snapshot_and_history() -> str:
    with_fixtures = SOURCE_TREE / "tests" / "fixtures" / "snapshots"
    read = 0
    warned = 0
    for path in sorted(with_fixtures.glob("v*/run_*.json")):
        # An upgrade that cannot carry a recorded fact forward says so with a
        # SchemaUpgradeWarning -- v3.9's unenforced daily-loss limit -- and that
        # warning is the only one reading an older payload may raise.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            snapshot = run_snapshot.from_primitives(json.loads(path.read_text(encoding="utf-8")))
        assert all(type(item.message).__name__ == "SchemaUpgradeWarning" for item in caught)
        warned += len(caught)
        assert snapshot.schema_version == run_snapshot.RUN_SNAPSHOT_SCHEMA
        read += 1
    frozen = with_fixtures / "v4.1.0" / "replay_paused.json"
    replay_snapshot.from_primitives(json.loads(frozen.read_text(encoding="utf-8")))
    assert read >= 4
    return (
        f"{read} run payloads of earlier releases ({warned} documented upgrade warnings) and "
        "the v4.1.0 replay payload read"
    )


_WORKER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {tests!r})
    import consumer_contract as contract
    from alphalab.backtesting import PausedReplay, ReplayBacktest
    from alphalab.backtesting import replay_snapshot
    from alphalab.lifecycle import digest_run
    from alphalab.persistence import FileRunStateStore, deserialize, serialize
    store = FileRunStateStore(sys.argv[2])
    if sys.argv[1] == "pause":
        paused = ReplayBacktest.run(
            contract.config(), contract.dataset(), contract.runtime(), contract.CONTEXTS,
            pause_when=lambda p: p.records_replayed == 23)
        assert isinstance(paused, PausedReplay)
        store.put("consumer", 1, serialize(replay_snapshot.capture(paused)))
    else:
        latest = store.latest("consumer")
        restored = replay_snapshot.restore(
            replay_snapshot.from_primitives(deserialize(store.get(latest))),
            contract.objects(), contract.dataset())
        print(digest_run(ReplayBacktest.resume(restored, contract.CONTEXTS).backtest).result_id)
    """
)


def check_process_restart() -> str:
    """Pause in one interpreter, end it, resume in another: what a host's restart does."""

    whole = digest_run(ReplayBacktest.run(config(), dataset(), runtime(), CONTEXTS).backtest)
    with tempfile.TemporaryDirectory(prefix="alphalab-consumer-") as root:
        worker = Path(root) / "worker.py"
        worker.write_text(_WORKER.format(tests=str(Path(__file__).resolve().parent)), "utf-8")
        store = Path(root) / "store"
        store.mkdir()
        for step in ("pause", "resume"):
            done = subprocess.run(
                [sys.executable, "-W", "error", str(worker), step, str(store)],
                capture_output=True,
                text=True,
                check=False,
                cwd=root,
            )
            assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == whole.result_id, (done.stdout, whole.result_id)
        assert _refused(lambda: FileRunStateStore(Path(root) / "missing"), StorageError)
    return "paused in one process, resumed in a fresh one: the uninterrupted result_id"


CHECKS: tuple[Callable[[], str], ...] = (
    check_strategy_lifecycle,
    check_backtest_and_replay_agree,
    check_accounting,
    check_risk_boundary,
    check_pause_resume_in_process,
    check_refusals,
    check_run_snapshot_and_history,
    check_process_restart,
)


def main(expected_version: str) -> None:
    print(f"consumer contract: {check_identity(expected_version)}")
    for check in CHECKS:
        print(f"  {check.__name__}: {check()}")
    print(f"consumer contract: {len(CHECKS) + 1} checks passed")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: consumer_contract.py EXPECTED_VERSION")
    main(sys.argv[1])
