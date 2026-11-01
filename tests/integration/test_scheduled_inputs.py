"""Every timed input on the historical drivers, applied at its instant (v4.1.1, ledger FUT-004).

Until v4.1.1 :meth:`BacktestEngine.run` took records and observations only: a
book settling two currencies, an ``on_timer`` strategy, a dividend or a split
had to be stepped by hand through :class:`~alphalab.runtime.run.RunEngine`.
A :class:`~alphalab.backtesting.schedule.RunSchedule` now carries them, and the
backtest and the replay apply each at its instant -- a rate, a funding, a cash
flow and a split *before* the records of their instant, a timer *after* them --
with the rate table the schedule builds converting every step.

Each property is checked against an oracle that does not use the schedule: a
loop written against ``RunEngine`` with the table in force stated by hand, or
the arithmetic itself.
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
    Funding,
    PausedReplay,
    ReplayBacktest,
    ReplayResult,
    RunSchedule,
    ScheduleCursor,
    ScheduledCashFlow,
    ScheduledDistribution,
    ScheduledFundingRate,
    ScheduledSplit,
    ScheduleValidationError,
    replay_snapshot,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.common.ids import id_scope
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import CashFlow, CashFlowKind, Split
from alphalab.portfolio.fx import FxRate, FxRates, MissingRateError
from alphalab.runtime.run import RunConfig, RunEngine
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind, TimerEvent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    START_CASH,
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

STRATEGY = "SCHEDULED"
SAP = InstrumentRecord("SAPX", AssetType.EQUITY, "XETR", "EUR")
ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")
EURUSD = (
    FxRate("EUR", "USD", Decimal("1.10"), 1.0, "ECB"),
    FxRate("EUR", "USD", Decimal("1.20"), 4.0, "ECB"),
)
EUR_CASH = Decimal("50000")


class Planner(BaseStrategy):
    """Holds a target quantity of each asset at chosen instants, and buys on each timer."""

    def __init__(self, plan: dict[tuple[float, str], Decimal], timer_buy: str = "") -> None:
        self._plan = plan
        self._timer_buy = timer_buy
        self.timers: list[float] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        key = (event.quote.timestamp, event.quote.asset_id)
        target = self._plan.get(key)
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

    def on_timer(self, context: StrategyContext, event: TimerEvent) -> Iterable[Intent]:
        self.timers.append(event.timestamp)
        if not self._timer_buy:
            return ()
        return (Intent(STRATEGY, self._timer_buy, Decimal("5"), timestamp=event.timestamp),)


def _config(*records: InstrumentRecord, two_currencies: bool = False) -> RunConfig:
    base = backtest_config(STRATEGY)
    pipeline = replace(base.pipeline, instruments=registry_of(*records))
    if two_currencies:
        pipeline = replace(
            pipeline,
            currency="USD",
            also_settles=frozenset({"EUR"}),
            budget=pipeline.budget.in_currency("USD"),
        )
    return replace(base, pipeline=pipeline)


def _quotes(asset_id: str, mids: dict[float, str]) -> list[Any]:
    return [quote(asset_id, at, Decimal(mid)) for at, mid in mids.items()]


def _strategies(strategy: Planner) -> Any:
    return running_strategy_state(STRATEGY, strategy, frozenset({"quotes", "timers"}))


def _instance(result: BacktestResult) -> Planner:
    instance = result.state.strategy.strategies[STRATEGY].instance
    assert isinstance(instance, Planner)
    return instance


# --------------------------------------------------------------------------- #
# Two currencies: funding, rates, conversion at the rate in force
# --------------------------------------------------------------------------- #

SAP_MIDS = {2.0: "100", 3.0: "100", 4.0: "100", 5.0: "100", 6.0: "110"}
EUR_PLAN = {(3.0, SAP.asset_id): Decimal("10")}


def _eur_run(inputs: RunSchedule) -> BacktestResult:
    return BacktestEngine.run(
        _config(SAP, two_currencies=True),
        MarketDataset.of("EUR", _quotes(SAP.asset_id, SAP_MIDS)),
        _strategies(Planner(dict(EUR_PLAN))),
        context_factory,
        schedule=inputs,
    )


EUR_SCHEDULE = RunSchedule.of(rates=EURUSD, fundings=(Funding(EUR_CASH, "EUR", 1.5),))


def test_a_two_currency_book_is_funded_and_traded_through_the_driver() -> None:
    result = _eur_run(EUR_SCHEDULE)

    (fill,) = result.fills
    assert fill.asset_id == SAP.asset_id and fill.quantity == Decimal("10")
    portfolio = result.state.portfolio
    assert portfolio.settlement_currencies == ("EUR", "USD")
    assert portfolio.positions[SAP.asset_id].currency == "EUR"
    assert portfolio.cash.balance("EUR") == EUR_CASH - Decimal("1000")
    assert portfolio.cash.balance("USD") == START_CASH


def test_each_step_is_valued_at_the_rate_in_force_at_it_and_never_a_later_one() -> None:
    """1.10 until the 4.0 quote, 1.20 from it -- a rate quoted at t converts at t."""

    result = _eur_run(EUR_SCHEDULE)
    by_time = {snapshot.timestamp: snapshot.total_equity for snapshot in result.equity_curve}
    eur_value = {2.0: EUR_CASH, 3.0: EUR_CASH, 4.0: EUR_CASH, 5.0: EUR_CASH}
    eur_value[6.0] = EUR_CASH - Decimal("1000") + Decimal("1100")
    for at, rate in ((2.0, "1.10"), (3.0, "1.10"), (4.0, "1.20"), (5.0, "1.20"), (6.0, "1.20")):
        assert by_time[at] == START_CASH + eur_value[at] * Decimal(rate), at


def test_the_driver_equals_a_loop_that_states_every_rate_by_hand() -> None:
    """The oracle: RunEngine stepped with the table in force written out per record."""

    config = _config(SAP, two_currencies=True)
    records = MarketDataset.of("EUR", _quotes(SAP.asset_id, SAP_MIDS)).records
    first, second = EURUSD
    table = {2.0: FxRates.of([first]), 3.0: FxRates.of([first])}
    table |= {at: FxRates.of([second]) for at in (4.0, 5.0, 6.0)}
    initial = _strategies(Planner(dict(EUR_PLAN)))
    assert config.seed is not None
    with id_scope(config.seed):
        state = replace(RunEngine.initialize(config, initial), source_id="EUR")
        state = RunEngine.fund(state, EUR_CASH, "EUR", 1.5, FxRates.of([first]))
        for record in records:
            rates = table[record.timestamp]
            state = RunEngine.close_slice(state, context_factory, rates, before=record.timestamp)
            state, _ = RunEngine.advance(state, record, context_factory, rates=rates)
        by_hand = BacktestResult(
            run=RunEngine.finalize(RunEngine.close_slice(state, context_factory))
        )

    assert digest_run(_eur_run(EUR_SCHEDULE)) == digest_run(by_hand)


def test_funding_a_second_currency_with_no_rate_yet_quoted_is_refused() -> None:
    """The book becomes mixed at 1.5 and nothing says what EUR is worth: refused, not guessed."""

    late = RunSchedule.of(
        rates=(replace(EURUSD[0], as_of=2.5),), fundings=(Funding(EUR_CASH, "EUR", 1.5),)
    )
    with pytest.raises(MissingRateError):
        _eur_run(late)


def test_two_rates_for_one_pair_at_one_instant_are_refused() -> None:
    clash = replace(EURUSD[0], rate=Decimal("1.11"), source="OTHER")
    with pytest.raises(ScheduleValidationError, match="rate"):
        RunSchedule.of(rates=(EURUSD[0], clash))


# --------------------------------------------------------------------------- #
# Timers, a dividend and a split, each at its place in its instant
# --------------------------------------------------------------------------- #

ACME_MIDS = {2.0: "50", 3.0: "50", 4.0: "51", 5.0: "52", 6.0: "26", 7.0: "27"}
TIMERS: tuple[TimerEvent, ...] = (
    TimerEvent("T-1", 3.5, timer_id="t1", schedule_type="once"),
    TimerEvent("T-2", 5.0, timer_id="t2", schedule_type="once"),
)
DIVIDEND = ScheduledCashFlow(
    CashFlow(CashFlowKind.DIVIDEND, Decimal("2.50"), "USD", ACME.asset_id), 5.0
)
SPLIT = ScheduledSplit(Split(ACME.asset_id, Decimal("2")), 6.0)
ACME_SCHEDULE = RunSchedule.of(timers=TIMERS, cash_flows=(DIVIDEND,), splits=(SPLIT,))


def _acme_run(inputs: RunSchedule = ACME_SCHEDULE) -> BacktestResult:
    return BacktestEngine.run(
        _config(ACME),
        MarketDataset.of("ACME", _quotes(ACME.asset_id, ACME_MIDS)),
        _strategies(Planner({}, timer_buy=ACME.asset_id)),
        context_factory,
        schedule=inputs,
    )


def test_a_timer_fires_after_its_instants_records_and_its_order_fills_at_the_next_price() -> None:
    result = _acme_run()

    assert _instance(result).timers == [3.5, 5.0]
    (fill,) = result.fills
    assert (fill.filled_at, fill.price) == (4.0, Decimal("51"))
    # The timer at 5.0 fires after the quote at 5.0, so its order rests to 6.0 --
    # where the split, applied before the records of 6.0, cancels it as a venue
    # cancels open orders at a corporate action. The strategy would ask again.
    rested = [order for order in result.orders if order.created_at == 5.0]
    assert [order.status.name for order in rested] == ["CANCELLED"]


def test_a_dividend_and_a_split_take_effect_before_the_records_of_their_instant() -> None:
    result = _acme_run()
    portfolio = result.state.portfolio

    # The dividend at 5.0 is the 2.50 the holding was paid, booked as stated.
    assert portfolio.realized_pnl["USD"] == Decimal("2.50")
    # The split at 6.0 doubles the 5 held, before the quote of 6.0 is marked.
    position = portfolio.positions[ACME.asset_id]
    assert position.quantity == Decimal("10")
    assert position.market_price == Decimal("27")
    valuation = result.valuation
    assert valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )


def test_without_a_schedule_the_run_is_the_run_it_was() -> None:
    plain = BacktestEngine.run(
        _config(ACME),
        MarketDataset.of("ACME", _quotes(ACME.asset_id, ACME_MIDS)),
        _strategies(Planner({})),
        context_factory,
    )
    empty = BacktestEngine.run(
        _config(ACME),
        MarketDataset.of("ACME", _quotes(ACME.asset_id, ACME_MIDS)),
        _strategies(Planner({})),
        context_factory,
        schedule=RunSchedule(),
    )
    assert digest_run(plain) == digest_run(empty)


def test_an_input_after_the_data_is_never_applied() -> None:
    beyond = RunSchedule.of(
        timers=(TimerEvent("T-9", 99.0, timer_id="t9", schedule_type="once"),),
        cash_flows=(replace(DIVIDEND, at=99.0),),
    )
    result = _acme_run(beyond)
    assert _instance(result).timers == []
    assert result.state.portfolio.realized_pnl.get("USD", Decimal("0")) == Decimal("0")


# --------------------------------------------------------------------------- #
# The replay applies the same inputs, and a payload keeps them
# --------------------------------------------------------------------------- #

FULL = RunSchedule.of(
    rates=EURUSD,
    fundings=(Funding(EUR_CASH, "EUR", 1.5),),
    timers=TIMERS,
    cash_flows=(DIVIDEND,),
    splits=(SPLIT,),
)
BOTH_MIDS = sorted(
    [*_quotes(SAP.asset_id, SAP_MIDS), *_quotes(ACME.asset_id, ACME_MIDS)],
    key=lambda item: item.timestamp,
)


def _both() -> MarketDataset:
    return MarketDataset.of("BOTH", BOTH_MIDS)


def _both_strategies() -> Any:
    return _strategies(Planner(dict(EUR_PLAN), timer_buy=ACME.asset_id))


def _economics(result: BacktestResult) -> tuple[Any, ...]:
    # A book in two currencies is valued in one only at stated rates.
    valuation = result.valuation_in("USD", FxRates.of([EURUSD[1]]))
    return (result.fills, valuation, result.equity_curve, result.trades)


def _replayed(pause: int | None = None) -> ReplayResult:
    config = _config(SAP, ACME, two_currencies=True)
    if pause is None:
        result = ReplayBacktest.run(
            config, _both(), _both_strategies(), context_factory, schedule=FULL
        )
        assert isinstance(result, ReplayResult)
        return result
    paused = ReplayBacktest.run(
        config,
        _both(),
        _both_strategies(),
        context_factory,
        pause_when=lambda progress: progress.records_replayed == pause,
        schedule=FULL,
    )
    assert isinstance(paused, PausedReplay)
    payload = serialize(replay_snapshot.capture(paused))
    restored = replay_snapshot.restore(
        replay_snapshot.from_primitives(deserialize(payload)),
        RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=config.pipeline.sizing_model,
                simulator=config.pipeline.simulator,
                strategies={STRATEGY: Planner(dict(EUR_PLAN), timer_buy=ACME.asset_id)},
                instruments=config.pipeline.instruments,
            ),
            fill_policy=config.fill_policy,
        ),
        _both(),
        FULL,
    )
    resumed = ReplayBacktest.resume(restored, context_factory)
    assert isinstance(resumed, ReplayResult)
    return resumed


def test_the_replay_applies_every_input_as_the_backtest_does() -> None:
    backtest = BacktestEngine.run(
        _config(SAP, ACME, two_currencies=True),
        _both(),
        _both_strategies(),
        context_factory,
        schedule=FULL,
    )
    assert _economics(_replayed().backtest) == _economics(backtest)
    assert len(backtest.fills) == 2


@pytest.mark.parametrize("pause", range(len(BOTH_MIDS)))
def test_a_replay_paused_anywhere_through_a_payload_keeps_every_input(pause: int) -> None:
    assert digest_run(_replayed(pause).backtest) == digest_run(_replayed().backtest)


@pytest.mark.parametrize("after", [None, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
def test_a_cursor_started_after_a_record_has_applied_exactly_what_preceded_it(
    after: float | None,
) -> None:
    cursor = ScheduleCursor.start(FULL, after)
    applied = FULL.entries[: cursor.position]
    if after is None:
        assert applied == ()
    else:
        assert all(entry.key[:2] < (after, 1) for entry in applied)
        assert all(entry.key[:2] >= (after, 1) for entry in cursor.pending)
    known = [rate for rate in EURUSD if after is not None and rate.as_of <= after]
    assert cursor.rates == FxRates.of(known[-1:]), "the latest rate known, and no other"


# --------------------------------------------------------------------------- #
# Per-unit distributions and funding rates: sized by what is held when they apply
# --------------------------------------------------------------------------- #


def _held_run(plan: dict[tuple[float, str], Decimal], inputs: RunSchedule) -> BacktestResult:
    return BacktestEngine.run(
        _config(ACME),
        MarketDataset.of("ACME", _quotes(ACME.asset_id, {2.0: "50", 3.0: "50", 4.0: "50"})),
        _strategies(Planner(plan)),
        context_factory,
        schedule=inputs,
    )


@pytest.mark.parametrize(
    ("held", "paid"),
    [(Decimal("8"), Decimal("4.00")), (Decimal("-8"), Decimal("-4.00")), (Decimal("0"), None)],
    ids=["long-receives", "short-pays", "flat-gets-nothing"],
)
def test_a_dividend_declared_per_share_pays_the_position_held_on_its_ex_date(
    held: Decimal, paid: Decimal | None
) -> None:
    dividend = ScheduledDistribution(ACME.asset_id, Decimal("0.50"), "USD", 4.0)
    result = _held_run({(2.0, ACME.asset_id): held}, RunSchedule.of(distributions=(dividend,)))
    booked = result.state.portfolio.realized_pnl.get("USD", Decimal("0"))
    assert booked == (Decimal("0") if paid is None else paid)
    flows = [e for e in result.state.portfolio.events if type(e).__name__ == "CashFlowBooked"]
    assert len(flows) == (0 if paid is None else 1)


PERP = InstrumentRecord(
    "BTCPERP",
    AssetType.CRYPTO,
    "XBIN",
    "USD",
    economics=InstrumentEconomics(Decimal("1"), SettlementModel.PERPETUAL),
)


def _perp_run(inputs: RunSchedule, plan: dict[tuple[float, str], Decimal]) -> BacktestResult:
    return BacktestEngine.run(
        _config(PERP),
        MarketDataset.of("PERP", _quotes(PERP.asset_id, {2.0: "100", 3.0: "110", 4.0: "120"})),
        _strategies(Planner(plan)),
        context_factory,
        schedule=inputs,
    )


def test_a_perpetuals_funding_is_charged_on_the_position_at_the_runs_mark() -> None:
    # Long 2 from 2.0; funding at 3.5 is charged at the mark of 3.0 (110): 2 x 110 x 0.001.
    funding = ScheduledFundingRate(PERP.asset_id, Decimal("0.001"), 3.5)
    result = _perp_run(
        RunSchedule.of(funding_rates=(funding,)), {(2.0, PERP.asset_id): Decimal("2")}
    )
    flows = [e for e in result.state.portfolio.events if type(e).__name__ == "CashFlowBooked"]
    assert len(flows) == 1
    without = _perp_run(RunSchedule(), {(2.0, PERP.asset_id): Decimal("2")})
    paid = without.valuation.realized_pnl - result.valuation.realized_pnl
    assert paid == Decimal("0.22")


def test_a_funding_rate_before_any_position_charges_nothing() -> None:
    early = ScheduledFundingRate(PERP.asset_id, Decimal("0.001"), 1.5)
    # Nothing is held before 2.0, so the interval charges nothing.
    assert _perp_run(RunSchedule.of(funding_rates=(early,)), {}).state.portfolio.realized_pnl.get(
        "USD", Decimal("0")
    ) == Decimal("0")
