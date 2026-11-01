"""Options exercised, assigned and expired, and futures delivered, by their terms (FUT-013).

Every scenario runs through :meth:`BacktestEngine.run` -- a strategy trades the
contract, the run's schedule carries the exercise, the assignment or the
expiry, and the portfolio, the allocation ledger, the trade records and the
valuation are checked against the arithmetic the contract's terms produce.
Cash is free and fills are immediate, so every figure is the contract's own.
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
    ScheduledExpiry,
    ScheduledSettlement,
    ScheduleValidationError,
    replay_snapshot,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.instrument.terms import (
    AutomaticExercise,
    Deliverable,
    FutureTerms,
    OptionTerms,
    terms_from_primitives,
    terms_to_primitives,
)
from alphalab.lifecycle import digest_run
from alphalab.options.contract import OptionContract
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import ExpirationPolicy, SettlementStyle, resolve_expiration
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import (
    Assignment,
    ContractSettlement,
    Exercise,
    OptionExpiry,
)
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run import RunConfig
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    START_CASH,
    backtest_config,
    context_factory,
    quote,
    registry_of,
)

LAST_TRADE = 6.0
EXPIRES = 6.5
SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")
OTHER_CCY_SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XETR", "EUR")
ONE = LotSpecification(Decimal("1"), Decimal("1"))
ANY_IN_THE_MONEY = AutomaticExercise(Decimal("0"))


def option(
    symbol: str,
    option_type: OptionType,
    style: ExerciseStyle,
    settlement: SettlementStyle,
    *,
    strike: str = "100",
    units: str = "100",
    multiplier: str = "100",
    rule: AutomaticExercise | None = ANY_IN_THE_MONEY,
    deliverable: InstrumentRecord = SHARE,
    exercisable_from: float | None = None,
    currency: str = "USD",
) -> InstrumentRecord:
    return InstrumentRecord(
        symbol,
        AssetType.OPTION,
        "XOPT",
        currency,
        economics=InstrumentEconomics(Decimal(multiplier), SettlementModel.OPTION_PREMIUM, lot=ONE),
        last_trade_at=LAST_TRADE,
        terms=OptionTerms(
            option_type=option_type,
            strike=Decimal(strike),
            exercise_style=style,
            settlement=settlement,
            deliverable=Deliverable(deliverable.asset_id, Decimal(units)),
            expires_at=EXPIRES,
            automatic_exercise=rule,
            exercisable_from=exercisable_from,
        ),
    )


AM_CALL = option("ACMEC100", OptionType.CALL, ExerciseStyle.AMERICAN, SettlementStyle.PHYSICAL)
AM_PUT = option("ACMEP100", OptionType.PUT, ExerciseStyle.AMERICAN, SettlementStyle.PHYSICAL)
EU_CASH_CALL = option("IDXC100", OptionType.CALL, ExerciseStyle.EUROPEAN, SettlementStyle.CASH)
EU_CASH_PUT = option("IDXP100", OptionType.PUT, ExerciseStyle.EUROPEAN, SettlementStyle.CASH)


class Holder(BaseStrategy):
    """Holds a target in an asset from chosen instants."""

    def __init__(self, strategy_id: str, plan: dict[tuple[float, str], Decimal]) -> None:
        self._id = strategy_id
        self._plan = plan

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        target = self._plan.get((event.quote.timestamp, event.quote.asset_id))
        if target is None:
            return ()
        return (
            Intent(
                self._id,
                event.quote.asset_id,
                target,
                timestamp=event.quote.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def runtime(plans: dict[str, dict[tuple[float, str], Decimal]]) -> RuntimeState:
    state = create_runtime()
    for sid, plan in plans.items():
        state = register_strategy(state, sid, Holder(sid, plan))
    running = {}
    for sid, entry in state.strategies.items():
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
        running[sid], _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies=running)


def config(
    records: Iterable[InstrumentRecord],
    strategies: Iterable[str] = ("S",),
    *,
    cash: Decimal = START_CASH,
    shorting: bool = True,
) -> RunConfig:
    ids = tuple(strategies)
    base = backtest_config(ids[0], starting_cash=cash)
    pipeline = replace(
        base.pipeline,
        instruments=registry_of(*records),
        budget=replace(base.pipeline.budget, strategy_budgets=dict.fromkeys(ids, cash)),
        allocation_constraints=replace(
            base.pipeline.allocation_constraints, allow_shorting=shorting
        ),
    )
    return replace(base, pipeline=pipeline)


def data(mids: dict[str, dict[float, str]]) -> MarketDataset:
    quotes = [
        quote(asset, at, Decimal(mid))
        for asset, series in mids.items()
        for at, mid in series.items()
    ]
    return MarketDataset.of("LIFE", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))


def series(*values: str, start: float = 2.0) -> dict[float, str]:
    return {start + n: v for n, v in enumerate(values)}


#: The option quotes until it stops trading; the share all along.
SHARE_MIDS = series("100", "104", "108", "110", "112", "112", "111", "113")


def run(
    contract: InstrumentRecord,
    plans: dict[str, dict[tuple[float, str], Decimal]],
    schedule: RunSchedule,
    *,
    option_mids: dict[float, str] | None = None,
    extra: Iterable[InstrumentRecord] = (),
    cash: Decimal = START_CASH,
    shorting: bool = True,
) -> BacktestResult:
    mids = {
        SHARE.asset_id: SHARE_MIDS,
        contract.asset_id: option_mids or series("5", "6", "8", "9"),
    }
    return BacktestEngine.run(
        config((contract, SHARE, *extra), plans, cash=cash, shorting=shorting),
        data(mids),
        runtime(plans),
        context_factory,
        schedule=schedule,
    )


def hold(contract: InstrumentRecord, contracts: str, sid: str = "S") -> dict[str, Any]:
    return {sid: {(2.0, contract.asset_id): Decimal(contracts)}}


def expiry(contract: InstrumentRecord, value: str, at: float = EXPIRES) -> RunSchedule:
    return RunSchedule.of(
        expiries=[ScheduledExpiry(OptionExpiry(contract.asset_id, Decimal(value)), at)]
    )


def cash(result: BacktestResult) -> Decimal:
    return result.state.portfolio.cash.balance("USD")


def held(result: BacktestResult, record: InstrumentRecord) -> Decimal:
    position = result.state.portfolio.positions.get(record.asset_id)
    return Decimal("0") if position is None else position.quantity


def identity(result: BacktestResult) -> None:
    valuation = result.valuation
    assert valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )


# --------------------------------------------------------------------------- #
# Cash settlement at expiry: calls and puts, long and short, in, at and out
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("contract", "contracts", "value", "paid"),
    [
        (EU_CASH_CALL, "2", "112", "2400"),  # 2 x 12 x 100
        (EU_CASH_CALL, "2", "100", "0"),  # at the money: never exercised
        (EU_CASH_CALL, "2", "93", "0"),  # out of the money
        (EU_CASH_PUT, "2", "93", "1400"),  # 2 x 7 x 100
        (EU_CASH_PUT, "2", "112", "0"),
        (EU_CASH_CALL, "-2", "112", "-2400"),  # the writer pays
        (EU_CASH_PUT, "-3", "93", "-2100"),
    ],
    ids=["call-itm", "call-atm", "call-otm", "put-itm", "put-otm", "short-call", "short-put"],
)
def test_a_cash_settled_option_pays_its_intrinsic_value_at_expiry(
    contract: InstrumentRecord, contracts: str, value: str, paid: str
) -> None:
    result = run(contract, hold(contract, contracts), expiry(contract, value))
    premium = Decimal(contracts) * Decimal("5") * Decimal("100")
    assert held(result, contract) == 0
    assert cash(result) == START_CASH - premium + Decimal(paid)
    assert result.valuation.realized_pnl == Decimal(paid) - premium
    identity(result)
    # The engine's figure is the research model's, contract for contract.
    research = resolve_expiration(
        OptionContract(
            SHARE.asset_id,
            Decimal("100"),
            EXPIRES,
            contract.terms.option_type,  # type: ignore[union-attr]
            ExerciseStyle.EUROPEAN,
            100,
        ),
        Decimal(contracts),
        Decimal(value),
        ExpirationPolicy(SettlementStyle.CASH, True),
    )
    assert research.cash_flow == Decimal(paid)


def test_an_option_with_no_exercise_rule_expires_unexercised_unless_instructed() -> None:
    manual = option(
        "IDXC100M", OptionType.CALL, ExerciseStyle.EUROPEAN, SettlementStyle.CASH, rule=None
    )
    lapsed = run(manual, hold(manual, "2"), expiry(manual, "112"))
    assert cash(lapsed) == START_CASH - Decimal("1000")
    instructed = run(
        manual,
        hold(manual, "2"),
        RunSchedule.of(
            exercises=[
                ScheduledExercise(Exercise(manual.asset_id, Decimal("2"), Decimal("112")), EXPIRES)
            ],
            expiries=[ScheduledExpiry(OptionExpiry(manual.asset_id, Decimal("112")), EXPIRES)],
        ),
    )
    # The exercise at the expiry instant applies before the expiry resolves the rest.
    assert cash(instructed) == START_CASH - Decimal("1000") + Decimal("2400")


def test_an_exercise_threshold_is_the_contracts_and_not_a_default() -> None:
    strict = option(
        "IDXC100T",
        OptionType.CALL,
        ExerciseStyle.EUROPEAN,
        SettlementStyle.CASH,
        rule=AutomaticExercise(Decimal("0.50")),
    )
    below = run(strict, hold(strict, "1"), expiry(strict, "100.25"))
    above = run(strict, hold(strict, "1"), expiry(strict, "100.75"))
    assert cash(below) == START_CASH - Decimal("500")
    assert cash(above) == START_CASH - Decimal("500") + Decimal("75")


# --------------------------------------------------------------------------- #
# Physical settlement: the deliverable moves against the strike
# --------------------------------------------------------------------------- #


def test_an_exercised_call_takes_the_shares_and_pays_the_strike() -> None:
    result = run(AM_CALL, hold(AM_CALL, "2"), expiry(AM_CALL, "112"))
    assert held(result, AM_CALL) == 0
    assert held(result, SHARE) == Decimal("200")
    assert cash(result) == START_CASH - Decimal("1000") - Decimal("20000")
    # The shares are marked at the market's last price for them, not the strike --
    # at the delivery itself, before any later price reaches them.
    share = result.state.portfolio.positions[SHARE.asset_id]
    assert share.average_cost == Decimal("100") and share.market_price == Decimal("113")
    (at_delivery,) = [p for p in result.state.portfolio_snapshots if p.timestamp == EXPIRES]
    last_mid = Decimal(SHARE_MIDS[LAST_TRADE])
    assert at_delivery.total_equity == (
        START_CASH - Decimal("1000") - Decimal("20000") + Decimal("200") * last_mid
    )
    identity(result)


def test_an_exercised_put_delivers_shares_held_and_receives_the_strike() -> None:
    plans = {"S": {(2.0, AM_PUT.asset_id): Decimal("1"), (2.0, SHARE.asset_id): Decimal("100")}}
    result = run(AM_PUT, plans, expiry(AM_PUT, "93"))
    assert held(result, SHARE) == 0 and held(result, AM_PUT) == 0
    assert cash(result) == START_CASH - Decimal("500") - Decimal("10000") + Decimal("10000")
    identity(result)


def test_a_written_call_assigned_at_expiry_delivers_the_covering_shares() -> None:
    plans = {"S": {(2.0, AM_CALL.asset_id): Decimal("-1"), (2.0, SHARE.asset_id): Decimal("100")}}
    result = run(AM_CALL, plans, expiry(AM_CALL, "112"))
    assert held(result, SHARE) == 0 and held(result, AM_CALL) == 0
    # Premium received, shares bought at 100, delivered at the strike of 100.
    assert cash(result) == START_CASH + Decimal("500")
    assert result.valuation.realized_pnl == Decimal("500")
    identity(result)


def test_an_american_option_is_exercised_early_in_part_and_the_rest_at_expiry() -> None:
    schedule = RunSchedule.of(
        exercises=[
            ScheduledExercise(Exercise(AM_CALL.asset_id, Decimal("1"), reference="early"), 4.0)
        ],
        expiries=[ScheduledExpiry(OptionExpiry(AM_CALL.asset_id, Decimal("112")), EXPIRES)],
    )
    result = run(AM_CALL, hold(AM_CALL, "3"), schedule)
    early = [
        r
        for r in result.state.trade_records
        if r.trade_id.startswith(("EXERCISE", "DELIVERY-EXERCISE"))
    ]
    assert [r.asset_id for r in early] == [AM_CALL.asset_id, SHARE.asset_id]
    late = [r for r in result.state.trade_records if "EXPIRY" in r.trade_id]
    assert [r.asset_id for r in late] == [AM_CALL.asset_id, SHARE.asset_id]
    assert held(result, SHARE) == Decimal("300") and held(result, AM_CALL) == 0
    assert cash(result) == START_CASH - Decimal("1500") - Decimal("30000")
    identity(result)


def test_an_early_assignment_is_an_input_and_delivers_at_its_instant() -> None:
    plans = {"S": {(2.0, AM_PUT.asset_id): Decimal("-2")}}
    schedule = RunSchedule.of(
        assignments=[ScheduledAssignment(Assignment(AM_PUT.asset_id, Decimal("1")), 3.0)],
        expiries=[ScheduledExpiry(OptionExpiry(AM_PUT.asset_id, Decimal("112")), EXPIRES)],
    )
    result = run(AM_PUT, plans, schedule)
    # Assigned one put: took 100 shares and paid 100 each; the other expired worthless.
    assert held(result, SHARE) == Decimal("100") and held(result, AM_PUT) == 0
    assert cash(result) == START_CASH + Decimal("1000") - Decimal("10000")
    identity(result)


def test_an_adjusted_contract_delivers_its_declared_units_against_its_multiplier() -> None:
    adjusted = option(
        "ACMEC100A",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        units="150",
        strike="100",
    )
    result = run(adjusted, hold(adjusted, "1"), expiry(adjusted, "80"))
    # 150 shares at 80 are worth 12,000 against a strike consideration of 10,000.
    assert held(result, SHARE) == Decimal("150")
    assert cash(result) == START_CASH - Decimal("500") - Decimal("10000")
    cash_adjusted = option(
        "IDXC100A", OptionType.CALL, ExerciseStyle.EUROPEAN, SettlementStyle.CASH, units="150"
    )
    paid = run(cash_adjusted, hold(cash_adjusted, "1"), expiry(cash_adjusted, "80"))
    assert cash(paid) == START_CASH - Decimal("500") + Decimal("2000")


def test_contract_units_and_multiplier_are_the_contracts_own() -> None:
    small = option(
        "MINIC100",
        OptionType.CALL,
        ExerciseStyle.EUROPEAN,
        SettlementStyle.CASH,
        units="25",
        multiplier="25",
    )
    result = run(small, hold(small, "4"), expiry(small, "112"))
    assert cash(result) == START_CASH - Decimal("4") * Decimal("5") * 25 + Decimal("4") * 12 * 25


# --------------------------------------------------------------------------- #
# Windows: American any time, European only at expiry
# --------------------------------------------------------------------------- #


def _exercise(
    contract: InstrumentRecord, at: float, count: str = "1", value: str | None = None
) -> RunSchedule:
    return RunSchedule.of(
        exercises=[
            ScheduledExercise(
                Exercise(
                    contract.asset_id, Decimal(count), None if value is None else Decimal(value)
                ),
                at,
            )
        ]
    )


def test_a_european_option_cannot_be_exercised_before_its_window() -> None:
    with pytest.raises(RuntimeValidationError, match=r"EUROPEAN: it may be exercised from 6\.0"):
        run(EU_CASH_CALL, hold(EU_CASH_CALL, "1"), _exercise(EU_CASH_CALL, 4.0, value="112"))


def test_no_option_is_exercised_after_it_expires() -> None:
    with pytest.raises(RuntimeValidationError, match=r"not at 7\.0"):
        run(AM_CALL, hold(AM_CALL, "1"), _exercise(AM_CALL, 7.0))


def test_an_american_option_respects_a_declared_first_exercise_instant() -> None:
    later = option(
        "ACMEC100L",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        exercisable_from=4.0,
    )
    with pytest.raises(RuntimeValidationError, match=r"from 4\.0"):
        run(later, hold(later, "1"), _exercise(later, 3.0))
    assert held(run(later, hold(later, "1"), _exercise(later, 4.0)), SHARE) == Decimal("100")


# --------------------------------------------------------------------------- #
# Refusals: what the book holds, what the instruction carries, what can be delivered
# --------------------------------------------------------------------------- #


def test_more_contracts_than_held_cannot_be_exercised_or_assigned() -> None:
    with pytest.raises(RuntimeValidationError, match="only a long position is exercised"):
        run(AM_CALL, hold(AM_CALL, "1"), _exercise(AM_CALL, 4.0, "2"))
    assign = RunSchedule.of(
        assignments=[ScheduledAssignment(Assignment(AM_CALL.asset_id, Decimal("1")), 4.0)]
    )
    with pytest.raises(RuntimeValidationError, match="only a short position is assigned"):
        run(AM_CALL, hold(AM_CALL, "1"), assign)


def test_a_cash_exercise_needs_its_settlement_value_and_a_physical_one_refuses_one() -> None:
    am_cash = option("IDXC100X", OptionType.CALL, ExerciseStyle.AMERICAN, SettlementStyle.CASH)
    with pytest.raises(RuntimeValidationError, match="was not supplied"):
        run(am_cash, hold(am_cash, "1"), _exercise(am_cash, 4.0))
    with pytest.raises(RuntimeValidationError, match="read by nothing"):
        run(AM_CALL, hold(AM_CALL, "1"), _exercise(AM_CALL, 4.0, value="108"))


def test_a_delivery_the_book_cannot_pay_for_is_refused() -> None:
    # Refused while the deliverable declares no margin terms: nothing finances
    # it (FUT-017 finances one only on declared terms).
    with pytest.raises(RuntimeValidationError, match="financed only when its deliverable declares"):
        run(AM_CALL, hold(AM_CALL, "2"), expiry(AM_CALL, "112"), cash=Decimal("5000"))


def test_a_delivery_that_would_leave_the_book_short_is_refused_when_shorting_is_not_allowed() -> (
    None
):
    with pytest.raises(RuntimeValidationError, match="does not allow a short position"):
        run(AM_PUT, hold(AM_PUT, "1"), expiry(AM_PUT, "93"), shorting=False)


def test_a_deliverable_the_registry_does_not_declare_or_another_currency_is_refused() -> None:
    elsewhere = InstrumentRecord("ZZZ", AssetType.EQUITY, "XNAS", "USD")
    orphan = option(
        "ZZZC100",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        deliverable=elsewhere,
    )
    with pytest.raises(RuntimeValidationError, match="does not declare"):
        run(orphan, hold(orphan, "1"), _exercise(orphan, 4.0))
    euro = option(
        "EURC100",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        deliverable=OTHER_CCY_SHARE,
    )
    with pytest.raises(RuntimeValidationError, match="two currencies"):
        run(euro, hold(euro, "1"), _exercise(euro, 4.0), extra=(OTHER_CCY_SHARE,))


def test_an_option_with_declared_terms_is_not_settled_at_a_stated_price() -> None:
    settle = RunSchedule.of(
        settlements=[
            ScheduledSettlement(ContractSettlement(EU_CASH_CALL.asset_id, Decimal("12")), EXPIRES)
        ]
    )
    with pytest.raises(RuntimeValidationError, match="give an OptionExpiry"):
        run(EU_CASH_CALL, hold(EU_CASH_CALL, "1"), settle)


def test_an_option_without_terms_cannot_be_exercised() -> None:
    bare = replace(AM_CALL, terms=None)
    with pytest.raises(RuntimeValidationError, match="declares no OptionTerms"):
        run(bare, hold(bare, "1"), _exercise(bare, 4.0))


def test_an_expired_option_still_held_fails_the_run() -> None:
    with pytest.raises(RuntimeValidationError, match="past their last trading instant"):
        run(AM_CALL, hold(AM_CALL, "1"), RunSchedule())


def test_one_instruction_twice_is_refused_and_a_second_expiry_changes_nothing() -> None:
    twice = ScheduledExercise(Exercise(AM_CALL.asset_id, Decimal("1")), 4.0)
    with pytest.raises(ScheduleValidationError, match="Two exercise entries"):
        RunSchedule.of(exercises=[twice, twice])
    again = RunSchedule.of(
        expiries=[
            ScheduledExpiry(OptionExpiry(EU_CASH_CALL.asset_id, Decimal("112")), EXPIRES),
            ScheduledExpiry(OptionExpiry(EU_CASH_CALL.asset_id, Decimal("112")), 7.0),
        ]
    )
    once = run(EU_CASH_CALL, hold(EU_CASH_CALL, "2"), expiry(EU_CASH_CALL, "112"))
    assert cash(run(EU_CASH_CALL, hold(EU_CASH_CALL, "2"), again)) == cash(once)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: replace(AM_CALL, asset_type=AssetType.EQUITY), "OptionTerms are a option"),
        (lambda: replace(AM_CALL, economics=None), "and not economics"),
        (lambda: replace(AM_CALL, last_trade_at=None), "no last trading instant"),
        (lambda: replace(AM_CALL, last_trade_at=7.0), "exercise cannot end before trading"),
        (
            lambda: option(
                "X",
                OptionType.CALL,
                ExerciseStyle.EUROPEAN,
                SettlementStyle.CASH,
                exercisable_from=1.0,
            ),
            "American contract's term",
        ),
        (lambda: FutureTerms(SettlementStyle.PHYSICAL, None), "names its deliverable"),
        (lambda: AutomaticExercise(Decimal("-1")), "zero or more"),
        (lambda: Deliverable(SHARE.asset_id, Decimal("0")), "positive finite"),
    ],
    ids=[
        "wrong-type",
        "no-economics",
        "no-last-trade",
        "expires-early",
        "european-from",
        "future-no-deliverable",
        "negative-threshold",
        "no-units",
    ],
)
def test_terms_that_contradict_the_contract_are_refused_at_declaration(
    build: Any, message: str
) -> None:
    with pytest.raises(InstrumentInputError, match=message):
        build()


# --------------------------------------------------------------------------- #
# Attribution: each strategy's share of the contract, and of the delivery
# --------------------------------------------------------------------------- #


def test_a_partial_exercise_is_attributed_to_each_strategy_by_its_share() -> None:
    plans = {
        "A": {(2.0, AM_CALL.asset_id): Decimal("2")},
        "B": {(2.0, AM_CALL.asset_id): Decimal("2")},
    }
    schedule = RunSchedule.of(
        exercises=[ScheduledExercise(Exercise(AM_CALL.asset_id, Decimal("3")), 4.0)],
        expiries=[ScheduledExpiry(OptionExpiry(AM_CALL.asset_id, Decimal("90")), EXPIRES)],
    )
    result = run(AM_CALL, plans, schedule)
    positions = result.state.allocation.strategy_positions
    assert positions is not None
    # Three of four by largest remainder, the tie to the earlier strategy.
    # The last contract, B's, expired out of the money.
    assert AM_CALL.asset_id not in positions["A"] and AM_CALL.asset_id not in positions["B"]
    assert positions["A"][SHARE.asset_id] == Decimal("200")
    assert positions["B"][SHARE.asset_id] == Decimal("100")
    (delivery,) = [r for r in result.state.trade_records if r.trade_id.startswith("DELIVERY")]
    assert {c.strategy_id: c.quantity for c in delivery.contributions} == {
        "A": Decimal("200"),
        "B": Decimal("100"),
    }


def test_part_of_a_book_whose_strategies_hold_opposite_sides_is_not_attributed() -> None:
    plans = {
        "A": {(2.0, AM_CALL.asset_id): Decimal("3")},
        "B": {(2.0, AM_CALL.asset_id): Decimal("-1")},
    }
    with pytest.raises(RuntimeValidationError, match="opposite sides"):
        run(AM_CALL, plans, _exercise(AM_CALL, 4.0, "1"))


# --------------------------------------------------------------------------- #
# Futures delivered at their final price
# --------------------------------------------------------------------------- #

DELIVERED_FUTURE = InstrumentRecord(
    "ACMEF",
    AssetType.FUTURE,
    "XFUT",
    "USD",
    economics=InstrumentEconomics(Decimal("50"), SettlementModel.FUTURES_VARIATION, lot=ONE),
    last_trade_at=LAST_TRADE,
    terms=FutureTerms(SettlementStyle.PHYSICAL, Deliverable(SHARE.asset_id, Decimal("50"))),
)


def test_a_physically_settled_future_delivers_against_its_final_price() -> None:
    settle = RunSchedule.of(
        settlements=[
            ScheduledSettlement(
                ContractSettlement(DELIVERED_FUTURE.asset_id, Decimal("104")), EXPIRES
            )
        ]
    )
    result = run(
        DELIVERED_FUTURE,
        {"S": {(2.0, DELIVERED_FUTURE.asset_id): Decimal("2")}},
        settle,
        option_mids=series("100", "101", "102", "103"),
    )
    assert held(result, DELIVERED_FUTURE) == 0
    assert held(result, SHARE) == Decimal("100")
    # Variation: 2 x 50 x (104 - 100); delivery: 100 shares at 104.
    assert cash(result) == START_CASH + Decimal("400") - Decimal("10400")
    share = result.state.portfolio.positions[SHARE.asset_id]
    assert share.average_cost == Decimal("104")
    identity(result)


def test_a_cash_settled_future_with_terms_delivers_nothing() -> None:
    cash_future = replace(
        DELIVERED_FUTURE, symbol="ACMEFC", terms=FutureTerms(SettlementStyle.CASH, None)
    )
    settle = RunSchedule.of(
        settlements=[
            ScheduledSettlement(ContractSettlement(cash_future.asset_id, Decimal("104")), EXPIRES)
        ]
    )
    result = run(
        cash_future,
        {"S": {(2.0, cash_future.asset_id): Decimal("2")}},
        settle,
        option_mids=series("100", "101", "102", "103"),
    )
    assert held(result, SHARE) == 0 and cash(result) == START_CASH + Decimal("400")


# --------------------------------------------------------------------------- #
# Replay, pause and restore; the terms in a registry payload
# --------------------------------------------------------------------------- #

LIFECYCLE = RunSchedule.of(
    exercises=[ScheduledExercise(Exercise(AM_CALL.asset_id, Decimal("1")), 4.0)],
    expiries=[ScheduledExpiry(OptionExpiry(AM_CALL.asset_id, Decimal("112")), EXPIRES)],
)


def _replay(pause: int | None = None) -> Any:
    plans = hold(AM_CALL, "2")
    mids = {SHARE.asset_id: SHARE_MIDS, AM_CALL.asset_id: series("5", "6", "8", "9")}
    kwargs: dict[str, Any] = {"schedule": LIFECYCLE}
    if pause is not None:
        kwargs["pause_when"] = lambda progress: progress.records_replayed == pause
    return ReplayBacktest.run(
        config((AM_CALL, SHARE), plans), data(mids), runtime(plans), context_factory, **kwargs
    )


def test_the_replay_exercises_and_delivers_as_the_backtest_does() -> None:
    backtest = run(AM_CALL, hold(AM_CALL, "2"), LIFECYCLE)
    replayed = _replay()
    assert replayed.backtest.fills == backtest.fills
    assert replayed.backtest.valuation == backtest.valuation
    assert list(replayed.backtest.state.trade_records) == list(backtest.state.trade_records)


@pytest.mark.parametrize("pause", range(1, 12))
def test_a_replay_paused_around_an_exercise_resumes_from_its_payload(pause: int) -> None:
    uninterrupted = _replay()
    paused = _replay(pause)
    assert isinstance(paused, PausedReplay)
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=paused.run.config.pipeline.sizing_model,
            simulator=paused.run.config.pipeline.simulator,
            strategies={"S": Holder("S", hold(AM_CALL, "2")["S"])},
            instruments=registry_of(AM_CALL, SHARE),
        ),
        fill_policy=paused.run.config.fill_policy,
    )
    mids = {SHARE.asset_id: SHARE_MIDS, AM_CALL.asset_id: series("5", "6", "8", "9")}
    restored = replay_snapshot.restore(
        replay_snapshot.from_primitives(deserialize(serialize(replay_snapshot.capture(paused)))),
        objects,
        data(mids),
        LIFECYCLE,
    )
    finished = ReplayBacktest.resume(restored, context_factory)
    assert digest_run(finished.backtest).result_id == digest_run(uninterrupted.backtest).result_id


def test_terms_round_trip_through_a_registry_payload() -> None:
    registry = registry_of(AM_CALL, EU_CASH_PUT, DELIVERED_FUTURE, SHARE)
    payload = deserialize(serialize(capture_registry(registry)))
    restored = restore_registry(registry_from_primitives(payload))
    for record in (AM_CALL, EU_CASH_PUT, DELIVERED_FUTURE):
        assert restored.instruments[record.asset_id].terms == record.terms
        assert restored.instruments[record.asset_id].asset_id == record.asset_id
    assert terms_from_primitives(terms_to_primitives(AM_CALL.terms)) == AM_CALL.terms
    with pytest.raises(InstrumentInputError, match="kind 'swap'"):
        terms_from_primitives({"kind": "swap"})


def test_terms_do_not_change_what_an_instrument_is() -> None:
    assert replace(AM_CALL, terms=None).asset_id == AM_CALL.asset_id
    assert AM_CALL.canonical_key == replace(AM_CALL, terms=None).canonical_key


def test_an_exercise_style_the_terms_cannot_state_is_refused_not_approximated() -> None:
    with pytest.raises(InstrumentInputError, match="exercise_style is an ExerciseStyle"):
        terms = AM_CALL.terms
        assert isinstance(terms, OptionTerms)
        replace(terms, exercise_style="BERMUDAN")  # type: ignore[arg-type]


def test_an_option_on_a_future_delivers_a_futures_position_at_the_strike() -> None:
    underlying = replace(DELIVERED_FUTURE, symbol="ACMEFU", terms=None, last_trade_at=None)
    on_future = option(
        "ACMEFC100",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        units="1",
        multiplier="50",
        deliverable=underlying,
    )
    result = run(on_future, hold(on_future, "2"), expiry(on_future, "112"), extra=(underlying,))
    future = result.state.portfolio.positions[underlying.asset_id]
    # Two futures at the strike; a future pays no notional, so only the premium moved.
    assert future.quantity == Decimal("2") and future.average_cost == Decimal("100")
    assert cash(result) == START_CASH - Decimal("2") * Decimal("5") * Decimal("50")
    identity(result)


def test_a_cash_option_on_an_undeclared_index_is_not_valued_by_an_assumed_multiplier() -> None:
    index = InstrumentRecord("IDX", AssetType.INDEX, "XIDX", "USD")
    on_index = option(
        "IDXC100U", OptionType.CALL, ExerciseStyle.EUROPEAN, SettlementStyle.CASH, deliverable=index
    )
    with pytest.raises(RuntimeValidationError, match="what one unit of it is worth"):
        run(on_index, hold(on_index, "1"), expiry(on_index, "112"))
    declared = run(on_index, hold(on_index, "1"), expiry(on_index, "112"), extra=(index,))
    assert cash(declared) == START_CASH - Decimal("500") + Decimal("1200")


def test_an_index_is_settled_against_and_never_traded() -> None:
    index = InstrumentRecord("IDX", AssetType.INDEX, "XIDX", "USD")
    result = BacktestEngine.run(
        config((index,)),
        data({index.asset_id: series("100", "101")}),
        runtime({"S": {(2.0, index.asset_id): Decimal("10")}}),
        context_factory,
    )
    assert not tuple(result.state.oms.orders.orders())
    assert index.asset_id not in result.state.portfolio.positions
