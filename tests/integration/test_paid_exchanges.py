"""A corporate action's cash paid after its effective instant (phase 3, ledger FUT-025).

A :class:`PaidExchange` states both of the issuer's instants: at ``at`` the
holding becomes its legs and an entitlement to the cash; at ``payment_at`` the
cash moves. Between them the cash is unsettled -- in trade-date cash, kept out
of buying power and of settled cash, as a sale's proceeds are before they
settle (FUT-015) -- and a restore rebuilds it from the ledger.

OLDCO is bought at 100 on 2.0 with the whole 10,000 of cash; a cash merger at
120 a share takes effect at 3.0 and pays at 6.0. OTHR quotes 100 throughout.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PaidExchange,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduledExchange,
    ScheduleValidationError,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.settlement import SETTLES_AT
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import (
    backtest_config,
    context_factory,
    registry_of,
    running_strategy_state,
)

D = Decimal
OLD, NEW, OTHER = exchanges.OLD, exchanges.NEW, margin.OTHER
CASH = D("10000")
MERGER = HoldingExchange(OLD.asset_id, cash_per_unit=D("120"), reference="cash merger")
BUY_ALL = {(2.0, OLD.asset_id): D("100")}


def config(*, retention: RetentionPolicy | None = None) -> Any:
    base = backtest_config("S", starting_cash=CASH)
    pipeline = replace(base.pipeline, instruments=registry_of(OLD, NEW, OTHER))
    if retention is not None:
        pipeline = replace(pipeline, retention=retention)
    return replace(base, pipeline=pipeline)


def data() -> Any:
    series = margin.series(*["100"] * 7)  # 2.0 to 8.0
    return margin.prices({OLD.asset_id: series, OTHER.asset_id: series})


def run(
    plan: dict[tuple[float, str], Decimal],
    schedule: RunSchedule,
    trader: margin.Trader | None = None,
    **kw: Any,
) -> BacktestResult:
    return BacktestEngine.run(
        config(**kw),
        data(),
        running_strategy_state("S", trader or margin.Trader(plan)),
        context_factory,
        schedule=schedule,
    )


def paid_later(exchange: HoldingExchange = MERGER, payment_at: float = 6.0) -> RunSchedule:
    return RunSchedule.of(exchanges=[PaidExchange(exchange, 3.0, payment_at)])


def other_fills(result: BacktestResult) -> list[tuple[float, Decimal]]:
    return margin.fills(result, OTHER)


# --------------------------------------------------------------------------- #
# The entitlement and the payment
# --------------------------------------------------------------------------- #


def test_the_cash_is_booked_at_the_effective_instant_and_settles_at_the_payment() -> None:
    result = run(BUY_ALL, paid_later())
    portfolio = result.state.portfolio
    (cash,) = [t for t in portfolio.ledger.transactions if t.type is TransactionType.EXCHANGE]
    assert (cash.timestamp, cash.quantity, cash.metadata[SETTLES_AT]) == (3.0, D("12000.00"), 6.0)
    # Realized at the effective instant, as the merger's terms make it.
    assert portfolio.realized_pnl["USD"] == D("2000.00")
    before = PortfolioEngine.cash_position(portfolio, "USD", 5.0)
    after = PortfolioEngine.cash_position(portfolio, "USD", 6.0)
    assert (before.trade_date, before.settled) == (D("12000.00"), D("0.00"))
    assert after.settled == D("12000.00")


def test_a_receivable_does_not_fund_an_order_before_it_is_paid() -> None:
    plan = {**BUY_ALL, (4.0, OTHER.asset_id): D("50"), (7.0, OTHER.asset_id): D("50")}
    trader = margin.Trader(plan)
    result = run(plan, paid_later(), trader)
    # At 4.0 the 12,000 is owed, not paid: nothing to spend. At 7.0 it is.
    assert other_fills(result) == [(7.0, D("50"))]
    assert trader.rejections


def test_cash_paid_at_its_effective_instant_is_spendable_at_once() -> None:
    plan = {**BUY_ALL, (4.0, OTHER.asset_id): D("50")}
    now = run(plan, RunSchedule.of(exchanges=[ScheduledExchange(MERGER, 3.0)]))
    same_instant = run(plan, paid_later(payment_at=3.0))
    assert other_fills(now) == other_fills(same_instant) == [(4.0, D("50"))]
    assert same_instant.state.portfolio.unsettled == ()
    assert digest_run(now).result_id == digest_run(same_instant).result_id


def test_a_short_holder_owes_the_cash_part_from_the_effective_instant() -> None:
    merger = HoldingExchange(
        OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("1"), D("0.5")),), cash_per_unit=D("10")
    )
    plan = {(2.0, OLD.asset_id): D("-50")}
    result = run(plan, paid_later(merger))
    portfolio = result.state.portfolio
    # The short pays 500 for the cash part: owed at 3.0 -- out of trade-date
    # cash at once -- and settled at the payment.
    position = PortfolioEngine.cash_position(portfolio, "USD", 4.0)
    assert position.trade_date == CASH + D("5000") - D("500")
    assert position.unsettled_debits == D("500.00")
    assert portfolio.positions[NEW.asset_id].quantity == D("-50")


def test_cash_in_another_currency_is_owed_in_it_and_not_spent_before_it_is_paid() -> None:
    from alphalab.backtesting import Funding
    from alphalab.backtesting.dataset import MarketDataset
    from alphalab.portfolio.fx import FxRate
    from tests.integration.harness import quote

    euro = exchanges.EURO
    merger = HoldingExchange(OLD.asset_id, cash_per_unit=D("100"), cash_currency="EUR")
    rates = [FxRate("EUR", "USD", D("1.25"), 1.0, "t"), FxRate("USD", "EUR", D("0.8"), 1.0, "t")]
    schedule = RunSchedule.of(
        exchanges=[PaidExchange(merger, 3.0, 6.0)],
        rates=rates,
        fundings=[Funding(D("1"), "EUR", 1.5)],
    )
    base = backtest_config("S", starting_cash=CASH)
    pipeline = replace(
        base.pipeline,
        instruments=registry_of(OLD, euro),
        also_settles=frozenset({"EUR"}),
    )
    cfg = replace(base, pipeline=replace(pipeline, budget=pipeline.budget.in_currency("USD")))
    quotes = [quote(OLD.asset_id, t, D("100")) for t in margin.series(*["1"] * 7)]
    quotes += [
        replace(quote(euro.asset_id, t, D("100")), currency="EUR")
        for t in margin.series(*["1"] * 7)
    ]
    data = MarketDataset.of("PAID", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))
    # 50 EUROCO costs 5,000 EUR: not payable at 4.0 from cash owed, payable at 7.0.
    plan = {**BUY_ALL, (4.0, euro.asset_id): D("50"), (7.0, euro.asset_id): D("50")}
    trader = margin.Trader(plan)
    result = BacktestEngine.run(
        cfg, data, running_strategy_state("S", trader), context_factory, schedule=schedule
    )
    assert margin.fills(result, euro) == [(7.0, D("50"))]
    assert trader.rejections


# --------------------------------------------------------------------------- #
# Replay, retention, declarations
# --------------------------------------------------------------------------- #


def test_an_entitlement_replays_and_resumes_from_every_pause() -> None:
    plan = {**BUY_ALL, (4.0, OTHER.asset_id): D("50"), (7.0, OTHER.asset_id): D("50")}
    schedule = paid_later()
    cfg, dataset = config(), data()
    whole = ReplayBacktest.run(
        cfg,
        dataset,
        running_strategy_state("S", margin.Trader(plan)),
        context_factory,
        schedule=schedule,
    )
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg,
            dataset,
            running_strategy_state("S", margin.Trader(plan)),
            context_factory,
            schedule=schedule,
            pause_when=margin._at(boundary),
        )
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
        restored = restore_replay(replay_from_primitives(payload), objects, dataset, schedule)
        held = paused.run.pipeline.portfolio.unsettled
        assert restored.run.pipeline.portfolio.unsettled == held, boundary
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary


def test_a_results_bound_keeps_an_unpaid_entitlement() -> None:
    bounded = run(BUY_ALL, paid_later(), retention=RetentionPolicy(results=1))
    unbounded = run(BUY_ALL, paid_later())
    at = 5.0
    assert PortfolioEngine.cash_position(bounded.state.portfolio, "USD", at) == (
        PortfolioEngine.cash_position(unbounded.state.portfolio, "USD", at)
    )


def test_a_payment_before_the_effective_instant_is_refused() -> None:
    with pytest.raises(ScheduleValidationError, match="before it"):
        PaidExchange(MERGER, 3.0, 2.0)
    held = run(BUY_ALL, RunSchedule.of()).state
    with pytest.raises(RuntimeValidationError, match="before it"):
        ExecutionPipeline.apply_exchange(held, MERGER, 9.0, payment_at=8.5)
