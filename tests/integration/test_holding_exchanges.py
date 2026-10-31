"""Mergers, spin-offs and rights issues: a holding exchanged by declared terms (v4.2.0, FUT-021).

A ``HoldingExchange`` states the issuer's terms -- what each unit held becomes,
the cash it pays, how the cost basis is apportioned -- and the run books exactly
that at its instant: each received holding at the basis it carries, the cash
part realizing its share, a fraction paid in lieu or refused, every strategy's
share carried over, working orders cancelled as at a split, and one
``PositionExchanged`` saying why the holding changed.

OLDCO is bought at 100 on 2.0 -- 100 shares, a basis of 10,000 -- and the
exchange takes effect at 3.0.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduledExchange,
    ScheduleValidationError,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange
from alphalab.portfolio.events import PositionExchanged
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.fx import FxRate
from alphalab.portfolio.snapshot import capture as capture_portfolio
from alphalab.portfolio.snapshot import from_primitives as portfolio_from_primitives
from alphalab.portfolio.snapshot import restore as restore_portfolio
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import (
    backtest_config,
    context_factory,
    registry_of,
    running_strategy_state,
)

D = Decimal
OLD = InstrumentRecord("OLDCO", AssetType.EQUITY, "XNAS", "USD")
NEW = InstrumentRecord("NEWCO", AssetType.EQUITY, "XNAS", "USD")
SPIN = InstrumentRecord("SPINCO", AssetType.EQUITY, "XNAS", "USD")
EURO = InstrumentRecord("EUROCO", AssetType.EQUITY, "XETR", "EUR")
BUY = {(2.0, OLD.asset_id): D("100")}


def config(*records: InstrumentRecord, whole: bool = False, **pipeline: Any) -> Any:
    base = backtest_config("S")
    constraints = replace(base.pipeline.allocation_constraints, enforce_integer_quantities=whole)
    if "also_settles" in pipeline:
        # Two settlement currencies: the budget states its own.
        pipeline.setdefault("budget", base.pipeline.budget.in_currency("USD"))
    return replace(
        base,
        pipeline=replace(
            base.pipeline,
            instruments=registry_of(*records),
            allocation_constraints=constraints,
            **pipeline,
        ),
    )


def run(
    exchange: HoldingExchange,
    *records: InstrumentRecord,
    plan: dict[tuple[float, str], Decimal] | None = None,
    mids: dict[str, dict[float, str]] | None = None,
    whole: bool = False,
    schedule: RunSchedule | None = None,
    strategies: Any = None,
    **pipeline: Any,
) -> BacktestResult:
    chosen = records or (OLD, NEW)
    prices = mids or {r.asset_id: margin.series("100", "100", "100") for r in chosen}
    return BacktestEngine.run(
        config(*chosen, whole=whole, **pipeline),
        margin.prices(prices),
        strategies or running_strategy_state("S", margin.Trader(BUY if plan is None else plan)),
        context_factory,
        schedule=schedule or RunSchedule.of(exchanges=[ScheduledExchange(exchange, 3.0)]),
    )


def position(result: BacktestResult, record: InstrumentRecord) -> Any:
    return result.state.portfolio.positions.get(record.asset_id)


def exchanged(result: BacktestResult) -> list[PositionExchanged]:
    return [e for e in result.state.portfolio.events if isinstance(e, PositionExchanged)]


def usd(result: BacktestResult) -> Decimal:
    return result.state.portfolio.cash.balance("USD")


START = D("1000000")


# --------------------------------------------------------------------------- #
# Each transformation, by its declared terms
# --------------------------------------------------------------------------- #


def test_a_stock_for_stock_merger_carries_the_basis_and_realizes_nothing() -> None:
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    result = run(merger)
    assert position(result, OLD) is None
    held = position(result, NEW)
    assert held.quantity == D("50.0") and held.basis == D("10000.00")
    assert held.average_cost == D("200")
    assert result.state.portfolio.realized_pnl.get("USD", D("0")) == 0
    (event,) = exchanged(result)
    assert event.received == {NEW.asset_id: D("50.0")} and not event.retained
    assert usd(result) == START - D("10000")


def test_a_cash_and_stock_merger_realizes_the_cash_against_its_share_of_the_basis() -> None:
    merger = HoldingExchange(
        OLD.asset_id,
        (ExchangeLeg(NEW.asset_id, D("0.5"), D("0.6")),),
        cash_per_unit=D("50"),
    )
    result = run(merger)
    # 5,000 of cash against 40% of the 10,000 basis: 1,000 realized; the shares
    # carry the other 6,000.
    assert position(result, NEW).basis == D("6000.00")
    assert result.state.portfolio.realized_pnl["USD"] == D("1000.00")
    assert usd(result) == START - D("10000") + D("5000")
    (cash,) = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.EXCHANGE
    ]
    assert cash.quantity == D("5000.00")


def test_a_cash_merger_closes_the_holding_for_its_cash() -> None:
    result = run(HoldingExchange(OLD.asset_id, cash_per_unit=D("120")), OLD)
    assert position(result, OLD) is None
    assert result.state.portfolio.realized_pnl["USD"] == D("2000.00")
    assert usd(result) == START + D("2000")


def test_a_spin_off_keeps_the_parent_and_splits_its_basis() -> None:
    spin = HoldingExchange(
        OLD.asset_id,
        (ExchangeLeg(SPIN.asset_id, D("0.2"), D("0.25")),),
        retained_basis_share=D("0.75"),
    )
    result = run(spin, OLD, SPIN)
    parent, spun = position(result, OLD), position(result, SPIN)
    assert parent.quantity == D("100.000000") and parent.basis == D("7500.00")
    assert spun.quantity == D("20.0") and spun.basis == D("2500.00")
    (event,) = exchanged(result)
    assert event.retained


def test_a_rights_issue_distributes_rights_that_are_exercised_through_the_lifecycle() -> None:
    from alphalab.backtesting import ScheduledExercise
    from alphalab.options.enums import ExerciseStyle, OptionType
    from alphalab.options.expiration import SettlementStyle
    from alphalab.portfolio.corporate_actions import Exercise
    from tests.integration.test_option_lifecycle import ANY_IN_THE_MONEY, option

    # One right per share, each a call on one OLDCO at 80, at no basis.
    rights = replace(
        option(
            "OLDRT",
            OptionType.CALL,
            ExerciseStyle.AMERICAN,
            SettlementStyle.PHYSICAL,
            strike="80",
            units="1",
            multiplier="1",
            rule=ANY_IN_THE_MONEY,
            deliverable=OLD,
        ),
    )
    issue = HoldingExchange(
        OLD.asset_id,
        (ExchangeLeg(rights.asset_id, D("1"), D("0")),),
        retained_basis_share=D("1"),
    )
    schedule = RunSchedule.of(
        exchanges=[ScheduledExchange(issue, 3.0)],
        exercises=[ScheduledExercise(Exercise(rights.asset_id, D("100")), 3.5)],
    )
    mids = {OLD.asset_id: margin.series("100", "100", "100"), rights.asset_id: {3.0: "20"}}
    result = run(issue, OLD, rights, mids=mids, schedule=schedule)
    # 100 rights exercised: 100 more shares for 8,000. The rights carried no basis.
    assert position(result, rights) is None
    assert position(result, OLD).quantity == D("200.000000")
    assert position(result, OLD).basis == D("18000.00")


# --------------------------------------------------------------------------- #
# Fractions, shorts and currencies
# --------------------------------------------------------------------------- #


def test_a_fraction_is_paid_in_lieu_and_realizes_its_share_of_the_basis() -> None:
    merger = HoldingExchange(
        OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1"), in_lieu_price=D("210")),)
    )
    result = run(merger, plan={(2.0, OLD.asset_id): D("101")}, whole=True)
    # 101 shares are 50.5 NEWCO: 50 held, half a share paid at 210 = 105. The
    # half share's basis is 10,100 x 0.5 / 50.5 = 100; 5 realized.
    held = position(result, NEW)
    assert held.quantity == D("50") and held.basis == D("10000.00")
    assert result.state.portfolio.realized_pnl["USD"] == D("5.00")


def test_a_fraction_with_no_in_lieu_price_is_refused() -> None:
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    with pytest.raises(RuntimeValidationError, match="in-lieu price"):
        run(merger, plan={(2.0, OLD.asset_id): D("101")}, whole=True)


def test_a_short_holder_owes_the_new_shares_and_pays_the_cash() -> None:
    merger = HoldingExchange(
        OLD.asset_id,
        (ExchangeLeg(NEW.asset_id, D("0.5"), D("0.6")),),
        cash_per_unit=D("50"),
    )
    result = run(merger, plan={(2.0, OLD.asset_id): D("-100")})
    # Short 100 for 10,000 of proceeds: short 50 NEWCO carrying 6,000 of them,
    # 5,000 paid against the other 4,000 -- 1,000 lost.
    assert position(result, NEW).quantity == D("-50.0")
    assert position(result, NEW).basis == D("6000.00")
    assert result.state.portfolio.realized_pnl["USD"] == D("-1000.00")
    assert usd(result) == START + D("10000") - D("5000")


def test_a_leg_in_another_currency_carries_its_basis_at_the_rate() -> None:
    from alphalab.backtesting import Funding

    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(EURO.asset_id, D("1"), D("1")),))
    rates = [FxRate("USD", "EUR", D("0.8"), 1.0, "t"), FxRate("EUR", "USD", D("1.25"), 1.0, "t")]
    schedule = RunSchedule.of(
        exchanges=[ScheduledExchange(merger, 3.0)],
        rates=rates,
        fundings=[Funding(D("1"), "EUR", 1.5)],
    )
    result = run(
        merger,
        OLD,
        EURO,
        schedule=schedule,
        also_settles=frozenset({"EUR"}),
    )
    held = position(result, EURO)
    assert held.currency == "EUR" and held.basis == D("8000.00")


def test_a_leg_in_another_currency_with_no_rate_is_refused() -> None:
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(EURO.asset_id, D("1"), D("1")),))
    # EUROCO is not quoted, so nothing before the exchange needs a rate.
    only_old = {OLD.asset_id: margin.series("100", "100", "100")}
    with pytest.raises(RuntimeValidationError, match="needs a rate"):
        run(merger, OLD, EURO, mids=only_old, also_settles=frozenset({"EUR"}))


# --------------------------------------------------------------------------- #
# What cannot be applied, and what an exchange does to orders and contracts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: HoldingExchange(OLD.asset_id),  # delivers nothing
        lambda: HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("0.9")),)),
        lambda: HoldingExchange(
            OLD.asset_id,
            (ExchangeLeg(NEW.asset_id, D("0.5"), D("0.7")),),
            retained_basis_share=D("0.5"),
        ),
        lambda: HoldingExchange(OLD.asset_id, (ExchangeLeg(OLD.asset_id, D("1"), D("1")),)),
        lambda: ExchangeLeg(NEW.asset_id, D("0"), D("1")),
        lambda: ExchangeLeg(NEW.asset_id, D("1"), D("1.5")),
    ],
)
def test_terms_that_cannot_apportion_the_whole_basis_are_refused(build: Any) -> None:
    with pytest.raises(InvalidTransactionError):
        build()


def test_a_leg_the_registry_does_not_declare_is_refused() -> None:
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    with pytest.raises(RuntimeValidationError, match="does not declare"):
        run(merger, OLD)


def test_two_exchanges_of_one_holding_at_one_instant_are_refused() -> None:
    one = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    other = HoldingExchange(OLD.asset_id, cash_per_unit=D("120"))
    with pytest.raises(ScheduleValidationError, match="cannot be applied twice"):
        RunSchedule.of(exchanges=[ScheduledExchange(one, 3.0), ScheduledExchange(other, 3.0)])


def test_working_orders_in_the_exchanged_holding_are_cancelled() -> None:
    from alphalab.common.order_terms import OrderTerms, TimeInForce
    from alphalab.core.enums import OrderStatus
    from alphalab.strategy.events import Intent

    class Bidder(margin.Trader):
        def on_quote(self, context: Any, event: Any) -> Any:
            if event.quote.timestamp == 2.0 and event.quote.asset_id == OLD.asset_id:
                bid = OrderTerms.limit(D("90"), TimeInForce.GTC)
                return (
                    Intent("S", OLD.asset_id, D("100"), timestamp=2.0),
                    Intent("S", OLD.asset_id, D("10"), timestamp=2.0, terms=bid),
                )
            return ()

    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    result = run(merger, strategies=running_strategy_state("S", Bidder({})))
    statuses = sorted(o.status.value for o in result.state.oms.orders.orders())
    assert OrderStatus.CANCELLED.value in statuses


def test_a_contract_on_the_exchanged_holding_must_declare_its_adjustment() -> None:
    from alphalab.options.enums import ExerciseStyle, OptionType
    from alphalab.options.expiration import SettlementStyle
    from tests.integration.test_option_lifecycle import option

    call = option(
        "OLDC100",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        deliverable=OLD,
    )
    plan = {(2.0, OLD.asset_id): D("100"), (2.0, call.asset_id): D("1")}
    mids = {r.asset_id: margin.series("100", "100", "100") for r in (OLD, NEW, call)}
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    with pytest.raises(RuntimeValidationError, match="declares no adjustment"):
        run(merger, OLD, NEW, call, plan=plan, mids=mids)


def test_each_strategys_share_becomes_its_share_of_each_leg() -> None:
    from alphalab.allocation.engine import AllocationEngine
    from tests.integration.test_option_lifecycle import runtime

    plans = {"A": {(2.0, OLD.asset_id): D("60")}, "B": {(2.0, OLD.asset_id): D("40")}}
    cfg = config(OLD, NEW)
    budget = replace(cfg.pipeline.budget, strategy_budgets={"A": START, "B": START})
    cfg = replace(cfg, pipeline=replace(cfg.pipeline, budget=budget))
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    result = BacktestEngine.run(
        cfg,
        margin.prices({r.asset_id: margin.series("100", "100", "100") for r in (OLD, NEW)}),
        runtime(plans),
        context_factory,
        schedule=RunSchedule.of(exchanges=[ScheduledExchange(merger, 3.0)]),
    )
    shares = dict(AllocationEngine.shares_of(result.state.allocation, NEW.asset_id))
    assert shares == {"A": D("30"), "B": D("20")}
    assert AllocationEngine.shares_of(result.state.allocation, OLD.asset_id) == ()


def test_an_exchange_of_a_holding_not_held_moves_nothing() -> None:
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    result = run(merger, plan={})
    assert exchanged(result) == [] and usd(result) == START


# --------------------------------------------------------------------------- #
# Persistence and replay: applied once, whatever the pause
# --------------------------------------------------------------------------- #


def test_the_event_and_the_holdings_round_trip_through_the_portfolio_snapshot() -> None:
    merger = HoldingExchange(
        OLD.asset_id,
        (ExchangeLeg(NEW.asset_id, D("0.5"), D("0.6")),),
        cash_per_unit=D("50"),
        reference="OLD/NEW 2024",
    )
    portfolio = run(merger).state.portfolio
    payload = deserialize(serialize(capture_portfolio(portfolio)))
    restored = restore_portfolio(portfolio_from_primitives(payload))
    assert restored.positions == portfolio.positions
    (event,) = [e for e in restored.events if isinstance(e, PositionExchanged)]
    assert event.reference == "OLD/NEW 2024" and event.consideration == {"USD": D("5000.00")}


def test_an_exchange_replays_and_restores_from_every_pause_applied_once() -> None:
    merger = HoldingExchange(
        OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("0.6")),), cash_per_unit=D("50")
    )
    schedule = RunSchedule.of(exchanges=[ScheduledExchange(merger, 3.0)])
    cfg = config(OLD, NEW)
    data = margin.prices({r.asset_id: margin.series("100", "100", "100") for r in (OLD, NEW)})

    def strategies() -> Any:
        return running_strategy_state("S", margin.Trader(BUY))

    whole = ReplayBacktest.run(cfg, data, strategies(), context_factory, schedule=schedule)
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg, data, strategies(), context_factory, schedule=schedule,
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": margin.Trader(BUY)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference
        assert len(exchanged(resumed.backtest)) == 1


def test_a_leg_already_held_on_the_same_side_is_added_to_at_its_basis() -> None:
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    plan = {(2.0, OLD.asset_id): D("100"), (2.0, NEW.asset_id): D("10")}
    result = run(merger, plan=plan)
    # 10 NEWCO bought at 100 and 50 received carrying 10,000: 60 at 11,000.
    held = position(result, NEW)
    assert held.quantity == D("60.000000") and held.basis == D("11000.00")


def test_a_leg_the_book_holds_the_other_side_of_is_refused() -> None:
    merger = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
    plan = {(2.0, OLD.asset_id): D("100"), (2.0, NEW.asset_id): D("-10")}
    with pytest.raises(InvalidTransactionError, match="does not net opposite holdings"):
        run(merger, plan=plan)
