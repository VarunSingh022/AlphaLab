"""Where the v4.2.0 milestones meet: margin with algorithms, settlement and queues.

Each milestone has its own suite; this one holds the seams between them. A
liquidation is an order like any other, so a run that works its orders by a
declared algorithm (FUT-009) works the liquidation by it too, and one call is
made while it works; a run that settles T+n (FUT-015) books a financed delivery
and a liquidation's sale by its settlement rule, and keeps the sale's proceeds
out of buying power while its margin counts them; and a queue model (FUT-011),
which governs resting limit orders, leaves a liquidation's market order alone.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.order_terms import MARKET
from alphalab.execution.queue import PriceTimeQueue
from alphalab.instrument.margin import MarginBasis, MarginTerms
from alphalab.lifecycle import digest_run
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.settlement import SETTLES_AT
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import MarginCall
from alphalab.runtime.calendars import VenueCalendars
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_algorithms_on_the_historical_path as algorithms
from tests.integration import test_financed_delivery as financed
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_option_lifecycle as lifecycle
from tests.integration.harness import context_factory, registry_of, running_strategy_state

D = Decimal
TERMS = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"))
TRADES = (TransactionType.BUY, TransactionType.SELL)


def calls(state: Any) -> list[MarginCall]:
    return [e for e in state.risk.events if isinstance(e, MarginCall)]


# --------------------------------------------------------------------------- #
# FUT-009: a liquidation is worked by the asset's declared algorithm
# --------------------------------------------------------------------------- #

SQUEEZED = (D("100"),) * 10 + (D("170"),) * 14
ALGO_RECORD = replace(algorithms.ACME_RECORD, margin=TERMS)
SHORT = {"S": {algorithms.at(0): (D("-100"), MARKET)}}


def _algorithmic_config() -> Any:
    cfg = algorithms.config()
    budget = replace(
        cfg.pipeline.budget, global_capital=D("10000"), strategy_budgets={"S": D("10000")}
    )
    pipeline = replace(
        cfg.pipeline,
        instruments=registry_of(ALGO_RECORD),
        starting_cash=D("10000"),
        budget=budget,
    )
    return replace(cfg, pipeline=pipeline)


def _bars() -> Any:
    return algorithms.bars(SQUEEZED, (D("400"),) * len(SQUEEZED))


def _minutes(result: BacktestResult) -> list[tuple[int, Decimal]]:
    return [
        (round((t.timestamp - algorithms.T0) / algorithms.MINUTE), t.quantity)
        for t in result.state.portfolio.ledger.transactions
        if t.type in TRADES
    ]


def test_a_liquidation_in_an_algorithmic_run_is_worked_by_its_algorithm_and_called_once() -> None:
    result = BacktestEngine.run(
        _algorithmic_config(),
        _bars(),
        algorithms.strategies(SHORT),
        context_factory,
        schedule=RunSchedule(),
        execution=algorithms.TWAP_PLAN,
    )
    # The short is worked in four children; the squeeze at minute 10 calls the
    # account once, and the 65-share liquidation is a parent the TWAP works in
    # four children over its window -- no second call while it works, though the
    # account stays below maintenance until the last of them.
    (call,) = calls(result.state)
    assert round((call.timestamp - algorithms.T0) / algorithms.MINUTE) == 10
    bought = [(m, q) for m, q in _minutes(result) if q > 0]
    assert bought == [(11, D("17")), (13, D("16")), (15, D("16")), (17, D("16"))]
    parents = result.state.oms.orders.orders()
    assert [(p.quantity, p.filled_quantity) for p in parents if p.side.value == "buy"] == [
        (D("65.000000"), D("65"))
    ]


def test_an_algorithmic_liquidation_replays_and_restores_from_every_pause() -> None:
    cfg, data = _algorithmic_config(), _bars()
    plan = algorithms.TWAP_PLAN
    whole = ReplayBacktest.run(
        cfg, data, algorithms.strategies(SHORT), context_factory, execution=plan
    )
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg,
            data,
            algorithms.strategies(SHORT),
            context_factory,
            execution=plan,
            pause_when=financed._at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": algorithms.Orders("S", SHORT["S"])},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data, execution=plan)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference
        assert len(calls(resumed.backtest.state)) == 1


# --------------------------------------------------------------------------- #
# FUT-015: a financed delivery and a liquidation settle by their rule
# --------------------------------------------------------------------------- #


def test_a_financed_delivery_and_its_liquidation_settle_t_plus_2_and_margin_counts_the_sale() -> (
    None
):
    from tests.integration.test_settlement_date_accounting import NEW_YORK, T_PLUS_2

    share = replace(financed.SHARE, exchange="XNAS", settlement=T_PLUS_2)
    put = lifecycle.option(
        "ACMEP100",
        OptionType.PUT,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        deliverable=share,
    )
    plans = lifecycle.hold(put, "-2")
    cfg = lifecycle.config((put, share), plans, cash=D("5000"))
    cfg = replace(cfg, pipeline=replace(cfg.pipeline, calendars=VenueCalendars({"XNAS": NEW_YORK})))
    result = BacktestEngine.run(
        cfg,
        lifecycle.data({share.asset_id: lifecycle.SHARE_MIDS, put.asset_id: financed.OPTION_MIDS}),
        lifecycle.runtime(plans),
        context_factory,
        schedule=financed.assigned(put, "2"),
    )
    fills = [
        t
        for t in result.state.portfolio.ledger.transactions
        if t.asset_id == share.asset_id and t.type in TRADES
    ]
    taken, sold = fills[0], fills[-1]
    assert taken.quantity == D("200") and sold.quantity < 0
    # Both are booked with their T+2 instants, the financed debit too.
    assert SETTLES_AT in taken.metadata and SETTLES_AT in sold.metadata
    (call,) = calls(result.state)
    assert call.deficit > 0
    # The sale's proceeds have not arrived by the run's end: margin equity counts
    # them (the books are trade-date), buying power does not.
    pending = [p for p in result.state.portfolio.unsettled if p.cash > 0]
    assert pending and pending[0].transaction_id == sold.transaction_id
    risk = result.state.risk
    assert risk.buying_power == max(D("0.00"), risk.cash - pending[0].cash)
    assert pending[0].cash == -sold.quantity * sold.price


# --------------------------------------------------------------------------- #
# FUT-011: a queue model leaves a liquidation's market order alone
# --------------------------------------------------------------------------- #


def test_a_queue_model_governs_resting_limits_and_leaves_a_liquidation_alone() -> None:
    plain = margin.run()
    cfg = margin.config(margin.ACME)
    queued = BacktestEngine.run(
        cfg,
        margin.prices({margin.ACME.asset_id: margin.SQUEEZE}),
        running_strategy_state("S", margin.Trader(margin.short())),
        context_factory,
        schedule=RunSchedule.of(),
        queue=PriceTimeQueue(),
    )
    assert margin.fills(queued) == margin.fills(plain) == [(2.0, D("-100")), (5.0, D("65"))]
    assert [(c.timestamp, c.deficit) for c in calls(queued.state)] == [
        (c.timestamp, c.deficit) for c in calls(plain.state)
    ]


# --------------------------------------------------------------------------- #
# The phase's seams: exchanges, margin, bands, borrow, interest, halts
# --------------------------------------------------------------------------- #


def _exchange_run(records: Any, plan: Any, mids: Any, schedule: Any, controls: Any = None) -> Any:
    from alphalab.instrument.registry import declare_trading_controls

    cfg = margin.config(*records)
    if controls is not None:
        registry = declare_trading_controls(cfg.pipeline.instruments, controls)
        cfg = replace(cfg, pipeline=replace(cfg.pipeline, instruments=registry))
    return BacktestEngine.run(
        cfg,
        margin.prices(mids),
        running_strategy_state("S", margin.Trader(plan)),
        context_factory,
        schedule=schedule,
    )


def test_a_merger_into_a_stricter_margined_instrument_is_called_and_liquidated() -> None:
    from alphalab.backtesting import ScheduledExchange
    from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange

    old = margin.ACME
    new = replace(
        margin.PLAIN,
        symbol="NEWM",
        margin=MarginTerms(MarginBasis.VALUE_RATE, D("1.5"), D("1.2")),
    )
    merger = HoldingExchange(old.asset_id, (ExchangeLeg(new.asset_id, D("1"), D("1")),))
    schedule = RunSchedule.of(exchanges=[ScheduledExchange(merger, 2.5)])
    mids = {old.asset_id: {2.0: "100"}, new.asset_id: {3.0: "100", 4.0: "100", 5.0: "100"}}
    result = _exchange_run((old, new), margin.short(old), mids, schedule)
    # Short 100 OLD at 100 becomes short 100 NEWM: margined at 120% on its own,
    # 12,000 against 10,000 of equity. Restoring 150% -- 15,000 -- buys back
    # 5,000 / 150 = 34 at NEWM's next quote.
    (call,) = margin.calls(result)
    assert call.timestamp == 3.0 and call.maintenance_requirement == D("12000")
    assert margin.fills(result, new) == [(4.0, D("34"))]


def test_a_liquidation_rests_while_the_market_is_beyond_the_band() -> None:
    from alphalab.instrument.controls import BandAction, PriceBand, TradingControls

    record = margin.ACME
    band = TradingControls(
        bands=(PriceBand(record.asset_id, 3.5, None, D("90"), D("160"), BandAction.REFUSE),)
    )
    mids = {record.asset_id: {2.0: "100", 3.0: "170", 4.0: "170", 5.0: "155", 6.0: "155"}}
    result = _exchange_run((record,), margin.short(), mids, RunSchedule.of(), band)
    # Called at 3.0, before the band; at 4.0 the market is beyond its limit up
    # and nothing trades; at 5.0, back within it, the liquidation fills there.
    (call,) = margin.calls(result)
    assert call.timestamp == 3.0
    bought = [(t, q) for t, q in margin.fills(result, record) if q > 0]
    assert bought and bought[0][0] == 5.0


def test_an_option_adjusted_for_a_merger_delivers_the_new_shares() -> None:
    from alphalab.backtesting import ScheduledExchange, ScheduledExercise
    from alphalab.instrument.terms import Deliverable, OptionTerms, TermsAdjustment
    from alphalab.portfolio.corporate_actions import ExchangeLeg, Exercise, HoldingExchange
    from tests.integration.test_option_lifecycle import option

    old, new = lifecycle.SHARE, replace(lifecycle.SHARE, symbol="ACMENEW")
    call = option(
        "ACMEC100", OptionType.CALL, ExerciseStyle.AMERICAN, SettlementStyle.PHYSICAL,
        deliverable=old,
    )  # fmt: skip
    # The clearing house's published adjustment: from the merger, each contract
    # delivers 50 ACMENEW for the same strike.
    terms = call.terms
    assert isinstance(terms, OptionTerms)
    adjusted = replace(
        call,
        terms=replace(
            terms,
            adjustments=(TermsAdjustment(3.0, terms.strike, Deliverable(new.asset_id, D("50"))),),
        ),
    )
    merger = HoldingExchange(old.asset_id, (ExchangeLeg(new.asset_id, D("0.5"), D("1")),))
    plans = {"S": {(2.0, adjusted.asset_id): D("1")}}
    schedule = RunSchedule.of(
        exchanges=[ScheduledExchange(merger, 3.0)],
        exercises=[ScheduledExercise(Exercise(adjusted.asset_id, D("1")), 4.0)],
    )
    mids = {
        old.asset_id: lifecycle.series("100", "100"),
        new.asset_id: lifecycle.series("200", "200", "210", "212", start=3.0),
        adjusted.asset_id: financed.OPTION_MIDS,
    }
    result = BacktestEngine.run(
        lifecycle.config((adjusted, old, new), plans),
        lifecycle.data(mids),
        lifecycle.runtime(plans),
        context_factory,
        schedule=schedule,
    )
    assert lifecycle.held(result, new) == D("50")
    assert lifecycle.held(result, adjusted) == 0


def test_a_book_under_every_new_term_replays_and_restores_applying_each_once() -> None:
    from datetime import date

    from alphalab.backtesting import ScheduledBorrowFee, ScheduledExchange, ScheduledInterest
    from alphalab.conventions.daycount import DayCount
    from alphalab.instrument.borrow import BorrowAvailability, BorrowTerms
    from alphalab.instrument.controls import TradingControls, TradingHalt
    from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange
    from alphalab.portfolio.events import PositionExchanged
    from alphalab.portfolio.financing import FinancingTier
    from alphalab.portfolio.types import TransactionType

    # A short in ACME on borrow, a margin purchase of BUYM, a halt of BUYM, and
    # ACME merged into ACMX: every phase's term on one book.
    borrow = BorrowTerms((BorrowAvailability(1.0, None, D("0.10")),), DayCount.ACT_360)
    acme = replace(margin.ACME, borrow=borrow)
    acmx = replace(margin.PLAIN, symbol="ACMX", margin=TERMS, borrow=borrow)
    buym = replace(
        margin.PLAIN,
        symbol="BUYM",
        margin=MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"), purchase_on_margin=True),
    )
    # ACME is sold short at 2.0, on borrow. BUYM is bought on margin at 3.0 --
    # inside its halt, so the order rests and fills at 4.0, after it -- leaving a
    # debit the period's interest charges; ACME is merged into ACMX at 3.5, and
    # the short in ACMX pays its borrow fee at 4.0.
    plan = {(2.0, acme.asset_id): D("-20"), (3.0, buym.asset_id): D("160")}
    day = (date(2024, 1, 2), date(2024, 1, 3))
    schedule = RunSchedule.of(
        exchanges=[
            ScheduledExchange(
                HoldingExchange(acme.asset_id, (ExchangeLeg(acmx.asset_id, D("1"), D("1")),)),
                3.5,
            )
        ],
        interest=[
            ScheduledInterest("USD", *day, 4.0, DayCount.ACT_360, (FinancingTier(None, D("0.08")),))
        ],
        borrow_fees=[ScheduledBorrowFee(*day, 4.0)],
    )
    halt = TradingControls(halts=(TradingHalt(2.5, 3.5, asset_id=buym.asset_id),))
    mids = {
        acme.asset_id: margin.series("100", "101"),
        acmx.asset_id: {4.0: "102", 5.0: "102"},
        buym.asset_id: margin.series("100", "99", "98", "97"),
    }
    from alphalab.instrument.registry import declare_trading_controls

    cfg = margin.config(acme, acmx, buym)
    cfg = replace(
        cfg,
        pipeline=replace(
            cfg.pipeline, instruments=declare_trading_controls(cfg.pipeline.instruments, halt)
        ),
    )
    data = margin.prices(mids)

    def strategies() -> Any:
        return running_strategy_state("S", margin.Trader(plan))

    whole = ReplayBacktest.run(cfg, data, strategies(), context_factory, schedule=schedule)
    backtest = BacktestEngine.run(cfg, data, strategies(), context_factory, schedule=schedule)
    assert whole.backtest.fills == backtest.fills
    reference = digest_run(whole.backtest).result_id

    def counts(result: Any) -> tuple[int, int, int]:
        portfolio = result.state.portfolio
        types = [t.type for t in portfolio.ledger.transactions]
        return (
            sum(isinstance(e, PositionExchanged) for e in portfolio.events),
            types.count(TransactionType.INTEREST),
            types.count(TransactionType.FEE),
        )

    expected = counts(whole.backtest)
    assert expected == (1, 1, 1)
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg, data, strategies(), context_factory, schedule=schedule,
            pause_when=financed._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": margin.Trader(plan)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference
        assert counts(resumed.backtest) == expected
