"""Backtest / replay parity.

Both drivers call :func:`alphalab.backtesting.engine.advance` for every record,
so parity ought to be structural. These tests hold it to that: the same dataset,
strategy, configuration and seed must produce the same orders, the same fills,
the same portfolio and the same analytics, whether the records arrived straight
from the dataset or through the replay cursor.

The one place the two runs legitimately differ is documented and tested below:
the replay cursor mints its own lifecycle event ids, and draws them from a
stream separate from the execution path's so that they cannot shift the
identifiers of orders and fills.
"""

from decimal import Decimal
from uuid import uuid4

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    FillPolicy,
    MarketDataset,
    ReplayBacktest,
    ReplayResult,
    StaticFill,
)
from alphalab.backtesting.replay import REPLAY_CURSOR_SEED_OFFSET, session_for
from alphalab.execution.fill import FillStatus
from alphalab.oms.snapshot import capture
from alphalab.persistence.serializer import serialize
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

MIDS = [
    Decimal("100.005"),
    Decimal("120.007"),
    Decimal("119.003"),
    Decimal("121.009"),
    Decimal("118.001"),
    Decimal("122.003"),
]
PLAN = {2.0: Decimal("10.5"), 4.0: Decimal("-4.25"), 6.0: Decimal("3.75")}
SEED = 20220905


def _scenario(
    fill_policy: FillPolicy | None = None,
) -> tuple[BacktestResult, BacktestResult, ReplayResult, str]:
    """Run one scenario both ways and hand back both results for comparison."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    config = backtest_config(strategy_id, seed=SEED, fill_policy=fill_policy)
    dataset = dataset_of_quotes(asset_id, MIDS)

    def strategy_state() -> StrategyRuntimeState:
        return running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, PLAN))

    backtest = BacktestEngine.run(config, dataset, strategy_state(), context_factory)
    replay = ReplayBacktest.run(config, dataset, strategy_state(), context_factory)
    return backtest, replay.backtest, replay, asset_id


def _fingerprint(result: BacktestResult) -> str:
    return serialize(
        {
            "oms": capture(result.state.oms),
            "portfolio": result.state.portfolio,
            "fills": result.fills,
            "trades": result.trades,
            "equity_curve": result.equity_curve,
            "report": result.report,
        }
    )


# ---------------------------------------------------------------------------
# Whole-run parity
# ---------------------------------------------------------------------------


def test_backtest_and_replay_produce_an_identical_run() -> None:
    backtest, replayed, _, _ = _scenario()

    assert _fingerprint(backtest) == _fingerprint(replayed)


def test_the_order_sequence_matches() -> None:
    backtest, replayed, _, _ = _scenario()

    assert [
        (str(o.order_id.value), o.side, o.quantity, o.status, o.average_fill_price)
        for o in backtest.orders
    ] == [
        (str(o.order_id.value), o.side, o.quantity, o.status, o.average_fill_price)
        for o in replayed.orders
    ]
    assert len(backtest.orders) == 3


def test_the_fill_sequence_matches() -> None:
    backtest, replayed, _, _ = _scenario()

    assert [
        (str(f.fill_id), str(f.order_id), f.quantity, f.price, f.commission) for f in backtest.fills
    ] == [
        (str(f.fill_id), str(f.order_id), f.quantity, f.price, f.commission) for f in replayed.fills
    ]
    assert len(backtest.fills) == 3


def test_cash_positions_and_pnl_match() -> None:
    backtest, replayed, _, asset_id = _scenario()

    assert backtest.valuation.cash == replayed.valuation.cash
    assert backtest.valuation.realized_pnl == replayed.valuation.realized_pnl
    assert backtest.valuation.unrealized_pnl == replayed.valuation.unrealized_pnl
    assert backtest.valuation.commission_paid == replayed.valuation.commission_paid
    assert backtest.valuation.equity == replayed.valuation.equity
    assert (
        backtest.state.portfolio.positions[asset_id] == replayed.state.portfolio.positions[asset_id]
    )


def test_the_equity_curve_matches_point_for_point() -> None:
    backtest, replayed, _, _ = _scenario()

    assert backtest.equity_curve == replayed.equity_curve
    assert len(backtest.equity_curve) == len(MIDS) + 1


def test_the_analytics_report_matches() -> None:
    backtest, replayed, _, _ = _scenario()

    assert serialize(backtest.report) == serialize(replayed.report)


def test_the_oms_state_matches_exactly() -> None:
    backtest, replayed, _, _ = _scenario()

    assert backtest.state.oms == replayed.state.oms


def test_the_per_record_steps_match() -> None:
    backtest, replayed, _, _ = _scenario()

    assert [
        (step.index, step.event_id, step.timestamp, step.equity) for step in backtest.steps
    ] == [(step.index, step.event_id, step.timestamp, step.equity) for step in replayed.steps]


def test_allocation_reservations_match() -> None:
    backtest, replayed, _, _ = _scenario()

    assert (
        backtest.state.allocation.notional_allocated == replayed.state.allocation.notional_allocated
    )
    assert dict(backtest.state.allocation.reservations) == dict(
        replayed.state.allocation.reservations
    )


# ---------------------------------------------------------------------------
# Parity holds under non-default execution semantics too
# ---------------------------------------------------------------------------


def test_parity_holds_when_the_venue_rejects() -> None:
    backtest, replayed, _, _ = _scenario(fill_policy=StaticFill(FillStatus.REJECTED))

    assert _fingerprint(backtest) == _fingerprint(replayed)
    assert backtest.fills == ()
    assert len(backtest.orders) == 3


def test_parity_holds_when_the_venue_partially_fills() -> None:
    backtest, replayed, _, _ = _scenario(
        fill_policy=StaticFill(FillStatus.PARTIAL_FILL, Decimal("1"))
    )

    assert _fingerprint(backtest) == _fingerprint(replayed)
    assert [f.quantity for f in backtest.fills] == [Decimal("1")] * 3


# ---------------------------------------------------------------------------
# The documented difference: the replay cursor
# ---------------------------------------------------------------------------


def test_replay_reports_its_own_cursor_progress() -> None:
    """The only thing a replay adds: where the cursor got to."""

    _, _, replay, _ = _scenario()

    assert replay.replay_status == "COMPLETED"
    assert replay.records_replayed == len(MIDS)
    assert replay.last_record is not None
    assert replay.last_record.timestamp == 2.0 + len(MIDS) - 1


def test_the_cursor_draws_from_a_separate_identifier_stream() -> None:
    """Cursor ids must not shift the identities of orders and fills."""

    assert REPLAY_CURSOR_SEED_OFFSET != 0

    backtest, replayed, _, _ = _scenario()

    assert [str(o.order_id.value) for o in backtest.orders] == [
        str(o.order_id.value) for o in replayed.orders
    ]


def test_the_replay_session_covers_the_dataset_exactly() -> None:
    dataset = dataset_of_quotes(str(uuid4()), MIDS)

    session = session_for(dataset)

    assert session.session_id == dataset.dataset_id
    assert session.start_time == dataset.start_time
    assert session.end_time == dataset.end_time


def test_a_replay_reads_every_record_the_dataset_holds() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    quotes = [record.payload for record in dataset_of_quotes(asset_id, MIDS).records]
    dataset = MarketDataset.of("PARITY", quotes)

    replay = ReplayBacktest.run(
        backtest_config(strategy_id, seed=SEED),
        dataset,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, PLAN)),
        context_factory,
    )

    assert replay.records_replayed == len(dataset)
    assert [step.event_id for step in replay.backtest.steps] == [
        record.event_id for record in dataset.records
    ]
