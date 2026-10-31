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
