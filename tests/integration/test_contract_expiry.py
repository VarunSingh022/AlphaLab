"""A contract stops trading at its expiry, and its final settlement closes what is held (FUT-012).

Until v4.1.1 an instrument declaration carried no expiry, so a future or option
held past its last trading day stayed on the book, marked at its last price --
a position the contract could not have had -- unless the strategy traded it
out. ``InstrumentRecord.last_trade_at`` declares the instant; after it the
path skips the contract's records and refuses its orders, a
:class:`~alphalab.portfolio.corporate_actions.ContractSettlement` closes every
position at the exchange's final price (a future's last variation, an option's
intrinsic value), and a run that ends still holding an expired contract is
refused. Each property is checked against the arithmetic it must produce.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    ReplayResult,
    RunSchedule,
    ScheduledSettlement,
    replay_snapshot,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.common.order_terms import OrderTerms
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import ContractSettlement
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run import RunConfig, RunEngine
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    START_CASH,
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

STRATEGY = "EXPIRY"
EXPIRY = 5.0


def _economics(multiplier: str, model: SettlementModel) -> InstrumentEconomics:
    return InstrumentEconomics(
        Decimal(multiplier), model, lot=LotSpecification(Decimal("1"), Decimal("1"))
    )


FUTURE = InstrumentRecord(
    "ESH6",
    AssetType.FUTURE,
    "XCME",
    "USD",
    economics=_economics("50", SettlementModel.FUTURES_VARIATION),
    last_trade_at=EXPIRY,
)
CALL = InstrumentRecord(
    "SPXC100",
    AssetType.OPTION,
    "XCBO",
    "USD",
    economics=_economics("100", SettlementModel.OPTION_PREMIUM),
    last_trade_at=EXPIRY,
)
SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")


class Holder(BaseStrategy):
    """Holds a target in each asset from chosen instants; may keep asking after expiry."""

    def __init__(self, plan: dict[tuple[float, str], Decimal]) -> None:
        self._plan = plan

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        target = self._plan.get((event.quote.timestamp, event.quote.asset_id))
        if target is None:
            return ()
        return (
            Intent(
                STRATEGY,
                event.quote.asset_id,
                target,
                timestamp=event.quote.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def _config(*records: InstrumentRecord) -> RunConfig:
    base = backtest_config(STRATEGY)
    return replace(base, pipeline=replace(base.pipeline, instruments=registry_of(*records)))


def _dataset(asset_id: str, mids: dict[float, str], extra: Iterable[Any] = ()) -> MarketDataset:
    items = [quote(asset_id, at, Decimal(mid)) for at, mid in mids.items()]
    return MarketDataset.of("EXP", sorted([*items, *extra], key=lambda item: item.timestamp))


def _run(
    record: InstrumentRecord,
    mids: dict[float, str],
    plan: dict[tuple[float, str], Decimal],
    settlements: Iterable[ScheduledSettlement] = (),
    extra: Iterable[Any] = (),
    records: tuple[InstrumentRecord, ...] = (),
) -> BacktestResult:
    return BacktestEngine.run(
        _config(record, *records),
        _dataset(record.asset_id, mids, extra),
        running_strategy_state(STRATEGY, Holder(plan)),
        context_factory,
        schedule=RunSchedule.of(settlements=settlements),
    )


def _settled(asset_id: str, price: str, at: float = EXPIRY) -> ScheduledSettlement:
    return ScheduledSettlement(ContractSettlement(asset_id, Decimal(price)), at)


FUTURE_MIDS = {2.0: "4000", 3.0: "4010", 4.0: "4020", 5.0: "4030", 6.0: "4100", 7.0: "4200"}


def _identity(result: BacktestResult) -> None:
    valuation = result.valuation
    assert valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )


# --------------------------------------------------------------------------- #
# A cash-settled future
# --------------------------------------------------------------------------- #


def test_a_future_held_to_expiry_is_closed_at_its_final_settlement() -> None:
    result = _run(
        FUTURE,
        FUTURE_MIDS,
        {(2.0, FUTURE.asset_id): Decimal("2")},
        settlements=(_settled(FUTURE.asset_id, "4035"),),
    )

    assert FUTURE.asset_id not in result.state.portfolio.positions
    # Bought 2 at 4000, settled at 4035: 2 x 35 x 50, all of it realized.
    assert result.valuation.realized_pnl == Decimal("3500.00")
    assert result.valuation.unrealized_pnl == Decimal("0")
    assert result.state.portfolio.cash.balance("USD") == START_CASH + Decimal("3500")
    _identity(result)


def test_the_settlement_is_attributed_to_the_strategy_that_held_the_contract() -> None:
    result = _run(
        FUTURE,
        FUTURE_MIDS,
        {(2.0, FUTURE.asset_id): Decimal("2")},
        settlements=(_settled(FUTURE.asset_id, "4035"),),
    )
    (record,) = [r for r in result.state.trade_records if r.trade_id.startswith("SETTLEMENT-")]
    assert record.asset_id == FUTURE.asset_id
    assert [(c.strategy_id, c.quantity) for c in record.contributions] == [(STRATEGY, Decimal("2"))]
    positions = result.state.allocation.strategy_positions
    assert positions is not None
    assert FUTURE.asset_id not in positions[STRATEGY]


def test_after_expiry_the_contracts_records_are_skipped_and_its_orders_refused() -> None:
    result = _run(
        FUTURE,
        FUTURE_MIDS,
        {(2.0, FUTURE.asset_id): Decimal("2"), (6.0, FUTURE.asset_id): Decimal("5")},
        settlements=(_settled(FUTURE.asset_id, "4035"),),
    )

    skipped = [(s.record.timestamp, s.reason) for s in result.run.skipped]
    assert [at for at, _ in skipped] == [6.0, 7.0]
    assert all("stopped trading" in reason for _, reason in skipped)
    assert all(order.created_at <= EXPIRY for order in result.orders)
    assert all(fill.filled_at <= EXPIRY for fill in result.fills)


def test_a_request_after_expiry_is_refused_before_an_order_exists() -> None:
    """Another asset's quote at 6.0 asks for the expired future: dropped, reported."""

    config = _config(FUTURE, SHARE)
    plan = {(2.0, FUTURE.asset_id): Decimal("2"), (6.0, SHARE.asset_id): Decimal("1")}

    class AsksForTheExpired(Holder):
        def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
            if event.quote.asset_id == SHARE.asset_id and event.quote.timestamp == 6.0:
                return (Intent(STRATEGY, FUTURE.asset_id, Decimal("3"), timestamp=6.0),)
            return super().on_quote(context, event)

    state = RunEngine.initialize(config, running_strategy_state(STRATEGY, AsksForTheExpired(plan)))
    dataset = _dataset(
        FUTURE.asset_id, {2.0: "4000", 3.0: "4010"}, [quote(SHARE.asset_id, 6.0, Decimal("10"))]
    )
    for record in dataset.records:
        state, result = RunEngine.advance(state, record, context_factory)
    assert result is not None
    (refusal,) = result.settlement_refusals
    assert refusal.asset_id == FUTURE.asset_id and "stopped trading" in refusal.detail
    assert not [order for order in state.pipeline.oms.orders.orders() if order.created_at == 6.0]


# --------------------------------------------------------------------------- #
# A cash-settled option: exercised at intrinsic value, or worthless
# --------------------------------------------------------------------------- #

CALL_MIDS = {2.0: "5.00", 3.0: "6.00", 4.0: "7.00", 5.0: "8.00"}


@pytest.mark.parametrize(
    ("quantity", "intrinsic", "realized"),
    [
        (Decimal("1"), "12.50", Decimal("750.00")),
        (Decimal("1"), "0", Decimal("-500.00")),
        (Decimal("-1"), "12.50", Decimal("-750.00")),
        (Decimal("-1"), "0", Decimal("500.00")),
    ],
    ids=["exercised", "expired-worthless", "assigned", "written-and-expired"],
)
def test_an_option_is_settled_at_its_intrinsic_value(
    quantity: Decimal, intrinsic: str, realized: Decimal
) -> None:
    result = _run(
        CALL,
        CALL_MIDS,
        {(2.0, CALL.asset_id): quantity},
        settlements=(_settled(CALL.asset_id, intrinsic),),
    )
    # Bought (or written) 1 at 5.00 x 100; settled at the intrinsic value x 100:
    # the writer of an exercised option pays it.
    assert result.valuation.realized_pnl == realized
    assert result.state.portfolio.cash.balance("USD") == START_CASH + realized
    assert CALL.asset_id not in result.state.portfolio.positions
    _identity(result)


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_a_run_that_ends_holding_an_expired_contract_is_refused() -> None:
    with pytest.raises(RuntimeValidationError, match="past their last trading instant"):
        _run(FUTURE, FUTURE_MIDS, {(2.0, FUTURE.asset_id): Decimal("2")})


def test_a_contract_closed_before_expiry_needs_no_settlement() -> None:
    result = _run(
        FUTURE,
        FUTURE_MIDS,
        {(2.0, FUTURE.asset_id): Decimal("2"), (3.0, FUTURE.asset_id): Decimal("0")},
    )
    assert FUTURE.asset_id not in result.state.portfolio.positions


@pytest.mark.parametrize(
    ("settlement", "records", "message"),
    [
        (_settled(FUTURE.asset_id, "4035", at=4.5), (FUTURE,), "still trades"),
        (_settled(SHARE.asset_id, "10"), (FUTURE, SHARE), "does not expire"),
        (_settled("0b7c3a52-3e43-4d0e-8f6c-1a9e5d2c7b10", "1"), (FUTURE,), "not declared"),
        (_settled(FUTURE.asset_id, "-1"), (FUTURE,), "do not admit"),
    ],
    ids=["before-expiry", "no-expiry", "undeclared", "negative-price"],
)
def test_a_settlement_that_cannot_be_right_is_refused(
    settlement: ScheduledSettlement, records: tuple[InstrumentRecord, ...], message: str
) -> None:
    with pytest.raises(RuntimeValidationError, match=message):
        BacktestEngine.run(
            _config(*records),
            _dataset(FUTURE.asset_id, FUTURE_MIDS),
            running_strategy_state(STRATEGY, Holder({(2.0, FUTURE.asset_id): Decimal("2")})),
            context_factory,
            schedule=RunSchedule.of(settlements=(settlement,)),
        )


def test_a_perpetual_does_not_expire_and_is_not_settled() -> None:
    perpetual = InstrumentRecord(
        "BTCPERP",
        AssetType.CRYPTO,
        "XBIN",
        "USD",
        economics=_economics("1", SettlementModel.PERPETUAL),
        last_trade_at=2.5,
    )
    with pytest.raises(RuntimeValidationError, match="only a future or an option"):
        _run(
            perpetual,
            {2.0: "100", 3.0: "101"},
            {},
            settlements=(_settled(perpetual.asset_id, "100", at=3.0),),
        )


def test_an_order_still_working_at_settlement_is_cancelled() -> None:
    """A limit that never fills before expiry: the settlement cancels it, frees what it held."""

    class RestsALimit(Holder):
        def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
            if event.quote.timestamp == 4.0:
                return (
                    Intent(
                        STRATEGY,
                        FUTURE.asset_id,
                        Decimal("1"),
                        timestamp=4.0,
                        terms=OrderTerms.limit(Decimal("3000")),
                    ),
                )
            return ()

    result = BacktestEngine.run(
        _config(FUTURE),
        _dataset(FUTURE.asset_id, FUTURE_MIDS),
        running_strategy_state(STRATEGY, RestsALimit({})),
        context_factory,
        schedule=RunSchedule.of(settlements=(_settled(FUTURE.asset_id, "4035"),)),
    )
    (order,) = result.orders
    assert order.status.name == "CANCELLED"
    assert result.state.allocation.reservations.get(str(order.order_id.value)) is None


# --------------------------------------------------------------------------- #
# Replay, payloads and the instrument registry
# --------------------------------------------------------------------------- #

SCHEDULE = RunSchedule.of(settlements=(_settled(FUTURE.asset_id, "4035"),))
PLAN = {(2.0, FUTURE.asset_id): Decimal("2")}


def _replayed(pause: int | None) -> ReplayResult:
    config = _config(FUTURE)
    dataset = _dataset(FUTURE.asset_id, FUTURE_MIDS)
    if pause is None:
        result = ReplayBacktest.run(
            config,
            dataset,
            running_strategy_state(STRATEGY, Holder(PLAN)),
            context_factory,
            schedule=SCHEDULE,
        )
        assert isinstance(result, ReplayResult)
        return result
    paused = ReplayBacktest.run(
        config,
        dataset,
        running_strategy_state(STRATEGY, Holder(PLAN)),
        context_factory,
        pause_when=lambda progress: progress.records_replayed == pause,
        schedule=SCHEDULE,
    )
    assert isinstance(paused, PausedReplay)
    restored = replay_snapshot.restore(
        replay_snapshot.from_primitives(deserialize(serialize(replay_snapshot.capture(paused)))),
        RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=config.pipeline.sizing_model,
                simulator=config.pipeline.simulator,
                strategies={STRATEGY: Holder(PLAN)},
                instruments=config.pipeline.instruments,
            ),
            fill_policy=config.fill_policy,
        ),
        dataset,
        SCHEDULE,
    )
    resumed = ReplayBacktest.resume(restored, context_factory)
    assert isinstance(resumed, ReplayResult)
    return resumed


def test_the_replay_settles_as_the_backtest_does() -> None:
    backtest = _run(FUTURE, FUTURE_MIDS, PLAN, settlements=(_settled(FUTURE.asset_id, "4035"),))
    replay = _replayed(None).backtest
    assert (replay.fills, replay.valuation, replay.state.trade_records.to_tuple()) == (
        backtest.fills,
        backtest.valuation,
        backtest.state.trade_records.to_tuple(),
    )


@pytest.mark.parametrize("pause", range(len(FUTURE_MIDS)))
def test_a_replay_paused_anywhere_settles_identically(pause: int) -> None:
    assert digest_run(_replayed(pause).backtest) == digest_run(_replayed(None).backtest)


def test_the_last_trading_instant_survives_a_registry_snapshot() -> None:
    registry = registry_of(FUTURE, SHARE)
    payload = deserialize(serialize(capture_registry(registry)))
    restored = restore_registry(registry_from_primitives(payload))

    assert restored == registry
    assert restored.record_for(FUTURE.asset_id).last_trade_at == EXPIRY  # type: ignore[union-attr]
    assert restored.record_for(SHARE.asset_id).last_trade_at is None  # type: ignore[union-attr]


def test_a_last_trading_instant_must_be_an_instant() -> None:
    from alphalab.instrument.exceptions import InstrumentInputError

    for bad in (float("nan"), True, "2026-03-20"):
        with pytest.raises(InstrumentInputError, match="last_trade_at"):
            replace(FUTURE, last_trade_at=bad)  # type: ignore[arg-type]
    assert FUTURE.asset_id == replace(FUTURE, last_trade_at=None).asset_id, "not identity"
