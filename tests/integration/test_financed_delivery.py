"""A delivery taken on margin: financed, called and liquidated (v4.2.0, ledger FUT-017).

A physical delivery the account cannot pay for in cash is financed -- booked as
a debit -- only when every deliverable it takes in declares margin terms; a
deliverable declaring none is refused, as it always was. An obligation (an
assignment, a future's delivery, an expiry's automatic exercise) is booked
whatever it leaves, and the account's next margin check calls it and
liquidates through allocation, risk and the OMS; an instructed exercise is
financed only while the account still meets its initial requirement.

The deliverable is margined at half its value initially and four tenths at
maintenance. Each figure below follows from the option lifecycle's series:
the share quotes 100, 104, 108, 110, 112, 112, 111, 113 from 2.0.
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
    RunSchedule,
    ScheduledAssignment,
    ScheduledExercise,
    ScheduledSettlement,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.instrument.margin import MarginBasis, MarginTerms
from alphalab.lifecycle import digest_run
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import Assignment, ContractSettlement, Exercise
from alphalab.portfolio.fx import NO_RATES
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import MarginCall
from alphalab.risk.limits import MarginLimit
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import margin_position
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import ExerciseRefused, Intent, IntentKind
from tests.integration import test_advanced_contract_terms as terms_tests
from tests.integration import test_option_lifecycle as lifecycle
from tests.integration.harness import context_factory

D = Decimal
MARGIN = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.4"))
SHARE = replace(lifecycle.SHARE, margin=MARGIN)
BETA = replace(terms_tests.BETA, margin=MARGIN)
AM_CALL, AM_PUT = lifecycle.AM_CALL, lifecycle.AM_PUT
OPTION_MIDS = lifecycle.series("5", "6", "8", "9")
TRADES = (TransactionType.BUY, TransactionType.SELL)


def run(
    contract: Any,
    records: tuple[Any, ...],
    plans: dict[str, dict[tuple[float, str], Decimal]],
    schedule: RunSchedule,
    cash: str,
    *,
    mids: dict[str, dict[float, str]] | None = None,
    whole_units: bool = False,
    budget: str | None = None,
) -> BacktestResult:
    cfg = lifecycle.config(records, plans, cash=D(cash))
    if budget is not None:
        # The strategy's capital ceiling is the user's, and is a notional one.
        wide = replace(
            cfg.pipeline.budget,
            global_capital=D(budget),
            maximum_exposure=D(budget),
            strategy_budgets=dict.fromkeys(plans, D(budget)),
        )
        # Risk's margin limit is a configured gross-notional cap: a leveraged
        # account configures one that admits its leverage.
        limits = replace(cfg.pipeline.risk_limits, margin=MarginLimit(D("10")))
        cfg = replace(cfg, pipeline=replace(cfg.pipeline, budget=wide, risk_limits=limits))
    if whole_units:
        constraints = replace(cfg.pipeline.allocation_constraints, enforce_integer_quantities=True)
        cfg = replace(cfg, pipeline=replace(cfg.pipeline, allocation_constraints=constraints))
    prices = (
        {SHARE.asset_id: lifecycle.SHARE_MIDS, contract.asset_id: OPTION_MIDS}
        if mids is None
        else mids
    )
    return BacktestEngine.run(
        cfg, lifecycle.data(prices), lifecycle.runtime(plans), context_factory, schedule=schedule
    )


def assigned(contract: Any, contracts: str, at: float = 4.0) -> RunSchedule:
    return RunSchedule.of(
        assignments=[ScheduledAssignment(Assignment(contract.asset_id, D(contracts)), at)]
    )


def exercised(contract: Any, contracts: str, at: float = 4.0) -> RunSchedule:
    return RunSchedule.of(
        exercises=[ScheduledExercise(Exercise(contract.asset_id, D(contracts)), at)]
    )


def trades(result: BacktestResult, record: Any) -> list[tuple[float, Decimal, Decimal]]:
    return [
        (t.timestamp, t.quantity, t.price)
        for t in result.state.portfolio.ledger.transactions
        if t.asset_id == record.asset_id and t.type in TRADES
    ]


def calls(result: BacktestResult) -> list[MarginCall]:
    return [e for e in result.state.risk.events if isinstance(e, MarginCall)]


def usd(result: BacktestResult) -> Decimal:
    return result.state.portfolio.cash.balance("USD")


# --------------------------------------------------------------------------- #
# An obligation is financed, called and liquidated
# --------------------------------------------------------------------------- #


def test_an_assignment_the_cash_cannot_pay_is_financed_then_called_and_liquidated() -> None:
    result = run(
        AM_PUT, (AM_PUT, SHARE), lifecycle.hold(AM_PUT, "-2"), assigned(AM_PUT, "2"), "5000"
    )
    # Sold two puts for 1,000; assigned at 4.0, the writer takes 200 shares for
    # the 20,000 strike with 6,000 of cash: 14,000 debited.
    (taken,) = [t for t in trades(result, SHARE) if t[1] > 0]
    assert taken == (4.0, D("200"), D("100"))
    # At 110 the shares are worth 22,000: equity 8,000 against 8,800
    # maintenance. Restoring the 11,000 initial requirement sells 3,000 / 55.
    (call,) = calls(result)
    assert call.timestamp == 5.0
    assert (call.equity, call.maintenance_requirement) == (D("8000.00"), D("8800"))
    assert (call.initial_requirement, call.deficit) == (D("11000"), D("3000"))
    sold = [t for t in trades(result, SHARE) if t[1] < 0]
    assert sold == [(6.0, D("-55"), D("112.00"))]
    assert usd(result) == D("-14000") + D("55") * D("112")


def test_a_delivery_of_a_deliverable_declaring_no_margin_is_refused_as_before() -> None:
    with pytest.raises(RuntimeValidationError, match="ACME declares none"):
        run(
            AM_PUT,
            (AM_PUT, lifecycle.SHARE),
            lifecycle.hold(AM_PUT, "-2"),
            assigned(AM_PUT, "2"),
            "5000",
        )


def test_an_automatic_exercise_at_expiry_is_an_obligation_and_is_financed() -> None:
    result = run(
        AM_CALL,
        (AM_CALL, SHARE),
        lifecycle.hold(AM_CALL, "2"),
        lifecycle.expiry(AM_CALL, "112"),
        "5000",
    )
    # 4,000 of cash pays for nothing of the 20,000 strike at 6.5: 16,000 debited.
    # At 112 equity is 6,400 against 8,960; restoring 11,200 sells 4,800 / 56, up.
    (call,) = calls(result)
    assert (call.timestamp, call.equity, call.deficit) == (7.0, D("6400.00"), D("4800"))
    assert [t for t in trades(result, SHARE) if t[1] < 0] == [(8.0, D("-86"), D("111.00"))]


FUTURE_MIDS = lifecycle.series("100", "101", "102", "103")


def _future_run(future: Any, cash: str) -> BacktestResult:
    settle = RunSchedule.of(
        settlements=[
            ScheduledSettlement(ContractSettlement(future.asset_id, D("104")), lifecycle.EXPIRES)
        ]
    )
    return run(
        future,
        (future, SHARE),
        {"S": {(2.0, future.asset_id): D("2")}},
        settle,
        cash,
        mids={SHARE.asset_id: lifecycle.SHARE_MIDS, future.asset_id: FUTURE_MIDS},
        budget="100000",
    )


def test_a_margined_future_is_opened_on_its_margin_and_its_delivery_financed() -> None:
    future = replace(
        lifecycle.DELIVERED_FUTURE,
        margin=MarginTerms(MarginBasis.PER_CONTRACT, D("1500"), D("1200")),
    )
    # Two contracts of 50 at 100 are 10,000 of notional; what opening them
    # spends of buying power is their declared initial margin, 3,000 of 4,000.
    result = _future_run(future, "4000")
    assert trades(result, future)[0] == (2.0, D("2.00"), D("100"))
    # Delivered at 104 after 400 of variation: 100 shares for 10,400 against
    # 4,400 of cash -- 6,000 debited, the shares held on their margin.
    (taken,) = [t for t in trades(result, SHARE) if t[1] > 0]
    assert taken == (lifecycle.EXPIRES, D("100"), D("104"))
    assert usd(result) == D("-6000.00")


def test_an_undeclared_future_is_still_charged_its_notional() -> None:
    result = _future_run(lifecycle.DELIVERED_FUTURE, "4000")
    assert trades(result, lifecycle.DELIVERED_FUTURE) == []
    rejected = [d for d in result.state.risk.history if not d.approved]
    assert any(v.rule == "BuyingPowerLimit" for d in rejected for v in d.violations)


# --------------------------------------------------------------------------- #
# An instructed exercise is financed only within the initial requirement
# --------------------------------------------------------------------------- #


def test_an_exercise_financed_within_the_initial_requirement_is_taken() -> None:
    result = run(
        AM_CALL, (AM_CALL, SHARE), lifecycle.hold(AM_CALL, "2"), exercised(AM_CALL, "2"), "15000"
    )
    # 14,000 of cash, 20,000 of strike: 6,000 debited. At 108 equity is 15,600
    # against the 10,800 initial requirement.
    assert usd(result) == D("-6000")
    assert lifecycle.held(result, SHARE) == D("200")
    assert calls(result) == []


def test_an_exercise_that_would_leave_equity_below_the_initial_requirement_is_refused() -> None:
    with pytest.raises(RuntimeValidationError, match="only within the initial requirement"):
        run(
            AM_CALL,
            (AM_CALL, SHARE),
            lifecycle.hold(AM_CALL, "2"),
            exercised(AM_CALL, "2"),
            "5000",
        )


class Exerciser(lifecycle.Holder):
    """Holds two calls, then asks to exercise them at 4.0."""

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.timestamp == 4.0 and event.quote.asset_id == SHARE.asset_id:
            return (Intent("S", AM_CALL.asset_id, D("2"), timestamp=4.0, kind=IntentKind.EXERCISE),)
        return super().on_quote(context, event)


def test_a_strategys_exercise_beyond_its_initial_margin_is_refused_and_moves_nothing() -> None:
    from alphalab.strategy.runtime import create_runtime, register_strategy
    from alphalab.strategy.supervisor import RuntimeSupervisor

    plans = lifecycle.hold(AM_CALL, "2")
    state = register_strategy(create_runtime(), "S", Exerciser("S", plans["S"]))
    entry = state.strategies["S"]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    result = BacktestEngine.run(
        lifecycle.config((AM_CALL, SHARE), plans, cash=D("5000")),
        lifecycle.data({SHARE.asset_id: lifecycle.SHARE_MIDS, AM_CALL.asset_id: OPTION_MIDS}),
        replace(state, strategies={"S": entry}),
        context_factory,
        # The calls it keeps expire out of the money.
        schedule=lifecycle.expiry(AM_CALL, "90"),
    )
    (refused,) = [e for e in result.state.strategy.events if isinstance(e, ExerciseRefused)]
    assert "only within the initial requirement" in refused.reason
    assert lifecycle.held(result, SHARE) == 0
    assert usd(result) == D("4000.00")


# --------------------------------------------------------------------------- #
# Packages, cash in lieu and another currency
# --------------------------------------------------------------------------- #

PACKAGE_MIDS = {SHARE.asset_id: lifecycle.SHARE_MIDS, BETA.asset_id: terms_tests.BETA_MIDS}


def _package(contract: Any) -> dict[str, dict[float, str]]:
    return {**PACKAGE_MIDS, contract.asset_id: OPTION_MIDS}


def test_a_package_is_financed_only_when_every_deliverable_declares_margin() -> None:
    call = terms_tests.BASKET_CALL
    with pytest.raises(RuntimeValidationError, match="BETA declares none"):
        run(
            call,
            (call, SHARE, terms_tests.BETA),
            terms_tests.hold((call, "2")),
            exercised(call, "2"),
            "15000",
            mids=_package(call),
        )
    result = run(
        call,
        (call, SHARE, BETA),
        terms_tests.hold((call, "2")),
        exercised(call, "2"),
        "15000",
        mids=_package(call),
    )
    # The 19,600 net consideration against 14,000 of cash: 5,600 debited, both
    # components booked, their cost the consideration.
    assert usd(result) == D("-5600")
    assert lifecycle.held(result, SHARE) == D("200") and lifecycle.held(result, BETA) == D("100")


def test_an_assigned_package_is_financed_and_its_largest_requirement_liquidated_first() -> None:
    put = terms_tests.BASKET_PUT
    result = run(
        put,
        (put, SHARE, BETA),
        terms_tests.hold((put, "-2")),
        assigned(put, "2"),
        "3000",
        mids=_package(put),
    )
    # 4,000 of cash, 19,600 owed: 15,600 debited. At the option's 5.0 quote the
    # book is marked at 108 and 42 -- 25,800 -- so equity is 10,200 against
    # 10,320; restoring 12,900 sells 2,700 / 54 = 50 shares of the larger
    # requirement, ACME, at its next quote.
    (call,) = calls(result)
    assert (call.equity, call.deficit) == (D("10200.00"), D("2700"))
    assert [t for t in trades(result, SHARE) if t[1] < 0] == [(5.0, D("-50"), D("110.00"))]
    assert [t for t in trades(result, BETA) if t[1] < 0] == []


def test_cash_in_lieu_is_credited_on_a_financed_delivery() -> None:
    thirds = terms_tests.THIRDS
    result = run(
        thirds,
        (thirds, SHARE),
        terms_tests.hold((thirds, "3")),
        exercised(thirds, "3"),
        "28000",
        whole_units=True,
    )
    # 26,500 of cash, 30,000 of strike, 0.9999 of a share in cash at 100.
    assert lifecycle.held(result, SHARE) == D("99")
    assert usd(result) == D("26500") - D("30000") + D("99.99")


def test_a_consideration_in_another_currency_is_converted_on_margin() -> None:
    euro = replace(terms_tests.EURO_SHARE, margin=MARGIN)
    contract = terms_tests.with_terms(
        lifecycle.option(
            "XCCYC100",
            OptionType.CALL,
            ExerciseStyle.AMERICAN,
            SettlementStyle.PHYSICAL,
            deliverable=euro,
        ),
        consideration_currency="USD",
    )
    plans = terms_tests.hold((contract, "1"))
    cfg = lifecycle.config((contract, euro), plans, cash=D("6000"))
    pipeline = replace(
        cfg.pipeline,
        currency="USD",
        also_settles=frozenset({"EUR"}),
        budget=cfg.pipeline.budget.in_currency("USD"),
    )
    schedule = RunSchedule.of(
        rates=list(terms_tests.RATES),
        exercises=[ScheduledExercise(Exercise(contract.asset_id, D("1")), 4.0)],
    )
    result = BacktestEngine.run(
        replace(cfg, pipeline=pipeline),
        lifecycle.data({euro.asset_id: terms_tests.EURO_MIDS, contract.asset_id: OPTION_MIDS}),
        lifecycle.runtime(plans),
        context_factory,
        schedule=schedule,
    )
    # The 10,000 USD strike against 5,500 USD: debited past the cash, converted
    # at 0.80 into the 8,000 EUR that pays for the shares.
    cash = result.state.portfolio.cash
    assert (cash.balance("USD"), cash.balance("EUR")) == (D("-4500.00"), D("0.00"))
    assert lifecycle.held(result, euro) == D("100")


# --------------------------------------------------------------------------- #
# Replay, pause and restore
# --------------------------------------------------------------------------- #


def test_a_financed_assignment_replays_and_restores_from_every_pause() -> None:
    plans = lifecycle.hold(AM_PUT, "-2")
    cfg = lifecycle.config((AM_PUT, SHARE), plans, cash=D("5000"))
    data = lifecycle.data({SHARE.asset_id: lifecycle.SHARE_MIDS, AM_PUT.asset_id: OPTION_MIDS})
    schedule = assigned(AM_PUT, "2")
    backtest = BacktestEngine.run(
        cfg, data, lifecycle.runtime(plans), context_factory, schedule=schedule
    )
    whole = ReplayBacktest.run(
        cfg, data, lifecycle.runtime(plans), context_factory, schedule=schedule
    )
    assert whole.backtest.fills == backtest.fills
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg,
            data,
            lifecycle.runtime(plans),
            context_factory,
            schedule=schedule,
            pause_when=_at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": lifecycle.Holder("S", plans["S"])},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference
        assert len(calls(resumed.backtest)) == 1
    margin = margin_position(backtest.state, NO_RATES, 10.0)
    assert margin is not None and not margin.in_breach


def _at(boundary: int) -> Any:
    def pause(progress: Any) -> bool:
        return bool(progress.records_replayed == boundary)

    return pause
