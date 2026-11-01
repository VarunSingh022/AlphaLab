"""Contract terms beyond one deliverable, carried out by the run (v4.2.0, ledger FUT-016).

A basket of deliverables with a cash component; a fraction of a unit settled in
cash in lieu; a consideration in another currency than the deliverable's,
converted at the exercise instant by the run's own rates; Bermudan exercise in
declared windows; and adjusted terms from a declared instant -- a split's,
checked against the split itself, and a merger's. Every movement goes through
the portfolio's fill authority, attributed, and the books reconcile. A
contract declaring none of these is booked exactly as before
(``test_option_lifecycle.py``).
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
    ScheduledExercise,
    ScheduledExpiry,
    ScheduledSplit,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.core.enums import AssetType
from alphalab.instrument import CashInLieu, Deliverable, OptionTerms, TermsAdjustment
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.options.contract import OptionContract
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.exceptions import OptionInputError
from alphalab.options.expiration import SettlementStyle
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import Exercise, OptionExpiry, Split
from alphalab.portfolio.fx import FxRate
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration.harness import START_CASH, context_factory, quote, registry_of
from tests.integration.test_option_lifecycle import (
    EXPIRES,
    SHARE,
    SHARE_MIDS,
    config,
    data,
    option,
    runtime,
    series,
)

D = Decimal
BETA = InstrumentRecord("BETA", AssetType.EQUITY, "XNAS", "USD")
BETA_MIDS = series("40", "40", "42", "44", "44", "44", "45", "45")
EURO_SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XETR", "EUR")
EURO_MIDS = series("90", "92", "95", "96", "97", "97", "98", "99")


def terms_of(record: InstrumentRecord) -> OptionTerms:
    terms = record.terms
    assert isinstance(terms, OptionTerms)
    return terms


def with_terms(record: InstrumentRecord, **changes: Any) -> InstrumentRecord:
    return replace(record, terms=replace(terms_of(record), **changes))


CALL = option("PKGC100", OptionType.CALL, ExerciseStyle.AMERICAN, SettlementStyle.PHYSICAL)
PUT = option("PKGP100", OptionType.PUT, ExerciseStyle.AMERICAN, SettlementStyle.PHYSICAL)
BASKET = (Deliverable(BETA.asset_id, D("50")),)
BASKET_CALL = with_terms(CALL, basket=BASKET, cash_per_contract=D("200"))
BASKET_PUT = with_terms(PUT, basket=BASKET, cash_per_contract=D("200"))


def run(
    contract: InstrumentRecord,
    plans: dict[str, dict[tuple[float, str], Decimal]],
    schedule: RunSchedule,
    *,
    extra: tuple[InstrumentRecord, ...] = (BETA,),
    mids: dict[str, dict[float, str]] | None = None,
    whole_units: bool = False,
    configured: Any = None,
) -> BacktestResult:
    prices = {
        SHARE.asset_id: SHARE_MIDS,
        BETA.asset_id: BETA_MIDS,
        contract.asset_id: series("5", "6", "8", "9"),
    }
    if mids is not None:
        prices = mids
    cfg = configured if configured is not None else config((contract, SHARE, *extra), plans)
    if whole_units:
        constraints = replace(cfg.pipeline.allocation_constraints, enforce_integer_quantities=True)
        cfg = replace(cfg, pipeline=replace(cfg.pipeline, allocation_constraints=constraints))
    return BacktestEngine.run(cfg, data(prices), runtime(plans), context_factory, schedule=schedule)


def hold(*legs: tuple[InstrumentRecord, str]) -> dict[str, dict[tuple[float, str], Decimal]]:
    return {"S": {(2.0, record.asset_id): D(quantity) for record, quantity in legs}}


def exercise_at(contract: InstrumentRecord, contracts: str, at: float = 4.0) -> RunSchedule:
    return RunSchedule.of(
        exercises=[ScheduledExercise(Exercise(contract.asset_id, D(contracts)), at)]
    )


def cash(result: BacktestResult, currency: str = "USD") -> Decimal:
    return result.state.portfolio.cash.balance(currency)


def position(result: BacktestResult, record: InstrumentRecord) -> Any:
    return result.state.portfolio.positions.get(record.asset_id)


def held(result: BacktestResult, record: InstrumentRecord) -> Decimal:
    found = position(result, record)
    return D("0") if found is None else found.quantity


def identity(result: BacktestResult) -> None:
    valuation = result.valuation
    assert valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )


# --------------------------------------------------------------------------- #
# A basket with a cash component
# --------------------------------------------------------------------------- #


def test_a_basket_call_delivers_every_component_and_the_cash_against_one_strike() -> None:
    result = run(BASKET_CALL, hold((BASKET_CALL, "2")), exercise_at(BASKET_CALL, "2"))

    assert held(result, BASKET_CALL) == 0
    assert held(result, SHARE) == D("200") and held(result, BETA) == D("100")
    premium = D("5") * 100 * 2
    net = D("100") * 100 * 2 - D("200") * 2  # the strike less the cash component received
    assert cash(result) == START_CASH - premium - net
    # The components' cost is the net consideration, apportioned by what each
    # was observed worth at the exercise: 200 x 108 and 100 x 42.
    acme, beta = position(result, SHARE), position(result, BETA)
    worth_acme, worth_beta = D("200") * D("108"), D("100") * D("42")
    acme_cost = acme.quantity * acme.average_cost
    beta_cost = beta.quantity * beta.average_cost
    assert acme_cost + beta_cost == net
    # Apportioned to the cent the book keeps prices at.
    assert abs(acme_cost - net * worth_acme / (worth_acme + worth_beta)) <= D("0.01")
    identity(result)


def test_a_basket_put_delivers_the_package_and_receives_the_strike() -> None:
    plans = hold((BASKET_PUT, "1"), (SHARE, "100"), (BETA, "50"))
    result = run(BASKET_PUT, plans, exercise_at(BASKET_PUT, "1"))

    assert held(result, SHARE) == 0 and held(result, BETA) == 0 and held(result, BASKET_PUT) == 0
    premium = D("5") * 100
    bought = D("100") * D("100") + D("50") * D("40")
    received = D("100") * 100 - D("200")  # the strike, less the cash component delivered
    assert cash(result) == START_CASH - premium - bought + received
    identity(result)


def test_a_cash_settled_basket_pays_its_package_value_against_the_strike() -> None:
    contract = with_terms(
        option("PKGCASH", OptionType.CALL, ExerciseStyle.EUROPEAN, SettlementStyle.CASH),
        basket=BASKET,
        cash_per_contract=D("200"),
    )
    schedule = RunSchedule.of(expiries=[ScheduledExpiry(OptionExpiry(contract.asset_id), EXPIRES)])
    result = run(contract, hold((contract, "2")), schedule)
    # Observed by expiry: ACME at 111 (6.0... last before 6.5 is 112 at 6.0? no:
    # the share's mid at 6.0 is 112), BETA at 44. Package per contract:
    # 100 x 112 + 50 x 44 + 200 = 13600; per unit of price, 136; less 100 = 36.
    acme = D(SHARE_MIDS[6.0])
    beta = D(BETA_MIDS[6.0])
    intrinsic = (D("100") * acme + D("50") * beta + D("200")) / 100 - D("100")
    premium = D("5") * 100 * 2
    assert cash(result) == START_CASH - premium + intrinsic * 100 * 2
    identity(result)


def test_one_value_cannot_state_a_package_and_is_refused() -> None:
    contract = with_terms(
        option("PKGCASH", OptionType.CALL, ExerciseStyle.EUROPEAN, SettlementStyle.CASH),
        basket=BASKET,
    )
    schedule = RunSchedule.of(
        expiries=[ScheduledExpiry(OptionExpiry(contract.asset_id, D("112")), EXPIRES)]
    )
    with pytest.raises(RuntimeValidationError, match="one underlying value cannot state it"):
        run(contract, hold((contract, "1")), schedule)
    with pytest.raises(InstrumentInputError, match="basket_intrinsic"):
        terms_of(contract).intrinsic(D("112"), D("100"), D("1"))


# --------------------------------------------------------------------------- #
# Fractions in cash in lieu
# --------------------------------------------------------------------------- #

#: After a one-for-three reverse split: a third of a hundred shares a contract.
THIRDS = with_terms(
    CALL, deliverable=Deliverable(SHARE.asset_id, D("33.3333")), fractions=CashInLieu(D("100"))
)


def test_a_fraction_of_a_share_is_paid_in_cash_in_lieu_at_the_declared_price() -> None:
    result = run(THIRDS, hold((THIRDS, "3")), exercise_at(THIRDS, "3"), whole_units=True)

    # 3 x 33.3333 = 99.9999: 99 shares delivered, 0.9999 of one in cash at 100.
    assert held(result, SHARE) == D("99")
    premium = D("5") * 100 * 3
    strike = D("100") * 100 * 3
    in_lieu = D("0.9999") * D("100")
    assert cash(result) == START_CASH - premium - strike + in_lieu
    identity(result)


def test_a_fraction_with_no_declared_rule_is_refused() -> None:
    contract = replace(THIRDS, terms=replace(terms_of(THIRDS), fractions=None))
    with pytest.raises(RuntimeValidationError, match="declares no cash in lieu"):
        run(contract, hold((contract, "3")), exercise_at(contract, "3"), whole_units=True)


# --------------------------------------------------------------------------- #
# A consideration in another currency
# --------------------------------------------------------------------------- #

RATES = (
    FxRate("USD", "EUR", D("0.80"), 1.0, "test"),
    FxRate("EUR", "USD", D("1.25"), 1.0, "test"),
)
#: A USD-quoted call delivering a EUR-priced share, the strike paid in USD.
QUANTO_STYLE = with_terms(
    option(
        "XCCYC100",
        OptionType.CALL,
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        deliverable=EURO_SHARE,
    ),
    consideration_currency="USD",
)


def _two_currencies(contract: InstrumentRecord) -> Any:
    cfg = config((contract, EURO_SHARE), hold((contract, "1")))
    pipeline = replace(
        cfg.pipeline,
        currency="USD",
        also_settles=frozenset({"EUR"}),
        budget=cfg.pipeline.budget.in_currency("USD"),
    )
    return replace(cfg, pipeline=pipeline)


def test_a_consideration_in_another_currency_is_converted_at_the_exercise_instant() -> None:
    schedule = RunSchedule.of(
        rates=list(RATES),
        exercises=[ScheduledExercise(Exercise(QUANTO_STYLE.asset_id, D("1")), 4.0)],
    )
    mids = {EURO_SHARE.asset_id: EURO_MIDS, QUANTO_STYLE.asset_id: series("5", "6", "8", "9")}
    result = run(
        QUANTO_STYLE,
        hold((QUANTO_STYLE, "1")),
        schedule,
        extra=(),
        mids=mids,
        configured=_two_currencies(QUANTO_STYLE),
    )
    assert held(result, EURO_SHARE) == D("100")
    premium = D("5") * 100
    # The strike, 100 x 100 USD, was paid in USD and converted to EUR at 0.80.
    assert cash(result, "USD") == START_CASH - premium - D("10000")
    assert cash(result, "EUR") == 0
    share = position(result, EURO_SHARE)
    assert share.quantity * share.average_cost == D("8000")


def test_a_consideration_with_no_rate_to_convert_it_is_refused() -> None:
    mids = {EURO_SHARE.asset_id: EURO_MIDS, QUANTO_STYLE.asset_id: series("5", "6", "8", "9")}
    with pytest.raises(RuntimeValidationError, match="needs a rate the run holds"):
        run(
            QUANTO_STYLE,
            hold((QUANTO_STYLE, "1")),
            # The budget's EUR->USD rate, and none for USD->EUR, which the
            # consideration's conversion needs.
            RunSchedule.of(
                rates=[RATES[1]],
                exercises=[ScheduledExercise(Exercise(QUANTO_STYLE.asset_id, D("1")), 4.0)],
            ),
            extra=(),
            mids=mids,
            configured=_two_currencies(QUANTO_STYLE),
        )


# --------------------------------------------------------------------------- #
# Bermudan exercise
# --------------------------------------------------------------------------- #

BERMUDAN = with_terms(
    CALL,
    exercise_style=ExerciseStyle.BERMUDAN,
    exercise_windows=((3.0, 3.2), (5.0, 5.2), (6.0, EXPIRES)),
)


def test_a_bermudan_option_is_exercised_in_a_window_and_refused_between_them() -> None:
    inside = run(BERMUDAN, hold((BERMUDAN, "1")), exercise_at(BERMUDAN, "1", at=5.0))
    assert held(inside, SHARE) == D("100") and held(inside, BERMUDAN) == 0
    with pytest.raises(RuntimeValidationError, match="is in none of them"):
        run(BERMUDAN, hold((BERMUDAN, "1")), exercise_at(BERMUDAN, "1", at=4.0))


@pytest.mark.parametrize(
    ("windows", "named"),
    [
        ((), "declares the windows"),
        (((3.0, 3.2), (3.1, EXPIRES)), "do not overlap"),
        (((3.0, 3.2),), "last window ends when it expires"),
    ],
    ids=["none", "overlapping", "ending-early"],
)
def test_a_bermudan_contract_declares_ordered_windows_ending_at_expiry(
    windows: tuple[tuple[float, float], ...], named: str
) -> None:
    with pytest.raises(InstrumentInputError, match=named):
        replace(terms_of(CALL), exercise_style=ExerciseStyle.BERMUDAN, exercise_windows=windows)
    with pytest.raises(InstrumentInputError, match="Bermudan contract's term"):
        replace(terms_of(CALL), exercise_windows=((3.0, EXPIRES),))


def test_the_pricers_refuse_a_bermudan_contract_rather_than_misprice_it() -> None:
    with pytest.raises(OptionInputError, match="Bermudan"):
        OptionContract("ACME", D("100"), 30.0, OptionType.CALL, ExerciseStyle.BERMUDAN, 100)


# --------------------------------------------------------------------------- #
# Adjusted terms: a split, checked; a merger, declared
# --------------------------------------------------------------------------- #

SPLIT_AT = 3.5
SPLIT = RunSchedule.of(splits=[ScheduledSplit(Split(SHARE.asset_id, D("2")), SPLIT_AT)])
ADJUSTED = replace(CALL, terms=terms_of(CALL).split_adjusted(SHARE.asset_id, D("2"), SPLIT_AT))


def _split_then_exercise(contract: InstrumentRecord, at: float = 4.0) -> RunSchedule:
    return RunSchedule.of(
        splits=[ScheduledSplit(Split(SHARE.asset_id, D("2")), SPLIT_AT)],
        exercises=[ScheduledExercise(Exercise(contract.asset_id, D("1")), at)],
    )


def test_a_split_adjusted_contract_delivers_twice_the_shares_for_the_same_strike() -> None:
    result = run(ADJUSTED, hold((ADJUSTED, "1")), _split_then_exercise(ADJUSTED))
    assert held(result, SHARE) == D("200")
    premium = D("5") * 100
    assert cash(result) == START_CASH - premium - D("100") * 100
    assert position(result, SHARE).average_cost == D("50")
    identity(result)


def test_the_adjustment_preserves_the_contracts_economics() -> None:
    before, after = terms_of(CALL), terms_of(ADJUSTED).effective(SPLIT_AT)
    for price in (D("80"), D("100"), D("120")):
        # The same package, priced before the split at S and after it at S / 2.
        assert before.intrinsic(price, D("100"), D("1")) == after.intrinsic(
            price / 2, D("100"), D("1")
        )
    assert after.strike == before.strike and after.deliverable.units == D("200")
    # An instruction before the split reads the terms before it.
    earlier = terms_of(ADJUSTED).effective(SPLIT_AT - 1)
    assert (earlier.strike, earlier.deliverables) == (before.strike, before.deliverables)


def test_an_exercise_before_the_split_delivers_the_terms_before_it() -> None:
    result = run(ADJUSTED, hold((ADJUSTED, "1")), _split_then_exercise(ADJUSTED, at=3.0))
    # 100 shares delivered at 3.0, then split two for one.
    assert held(result, SHARE) == D("200")
    assert position(result, SHARE).average_cost == D("50")


def test_a_split_of_a_held_contracts_deliverable_with_no_adjustment_is_refused() -> None:
    with pytest.raises(RuntimeValidationError, match="declares no adjustment effective then"):
        run(CALL, hold((CALL, "1")), _split_then_exercise(CALL))


def test_an_adjustment_that_does_not_preserve_the_economics_is_refused() -> None:
    wrong = replace(
        CALL,
        terms=replace(
            terms_of(CALL),
            adjustments=(
                TermsAdjustment(SPLIT_AT, D("100"), Deliverable(SHARE.asset_id, D("300"))),
            ),
        ),
    )
    with pytest.raises(RuntimeValidationError, match="does not preserve its"):
        run(wrong, hold((wrong, "1")), _split_then_exercise(wrong))


def test_a_merger_adjustment_turns_the_deliverable_into_a_package_from_its_instant() -> None:
    merged = with_terms(
        CALL,
        adjustments=(
            TermsAdjustment(
                3.5,
                D("100"),
                Deliverable(BETA.asset_id, D("150")),
                cash_per_contract=D("1000"),
                reason="ACME acquired for 1.5 BETA and 10 USD a share",
            ),
        ),
    )
    result = run(merged, hold((merged, "1")), exercise_at(merged, "1"))
    assert held(result, SHARE) == 0 and held(result, BETA) == D("150")
    premium = D("5") * 100
    assert cash(result) == START_CASH - premium - (D("100") * 100 - D("1000"))
    identity(result)


# --------------------------------------------------------------------------- #
# Persistence and replay
# --------------------------------------------------------------------------- #


def test_every_new_term_round_trips_through_the_registry_snapshot() -> None:
    quanto = with_terms(QUANTO_STYLE, consideration_rate=D("0.90"))
    for record in (BASKET_CALL, THIRDS, QUANTO_STYLE, quanto, BERMUDAN, ADJUSTED):
        payload = deserialize(serialize(capture_registry(registry_of(record))))
        restored = restore_registry(registry_from_primitives(payload))
        assert restored.record_for(record.asset_id) == record
    # A contract declaring none of the new terms writes them not at all.
    plain = deserialize(serialize(capture_registry(registry_of(CALL))))
    assert "basket" not in serialize(plain) and "adjustments" not in serialize(plain)


def _pauses_at(boundary: int) -> Any:
    def pause(progress: Any) -> bool:
        return bool(progress.records_replayed == boundary)

    return pause


def test_a_package_exercise_replays_and_resumes_from_any_pause_to_the_same_result() -> None:
    plans = hold((BASKET_CALL, "2"))
    schedule = exercise_at(BASKET_CALL, "2")
    cfg = config((BASKET_CALL, SHARE, BETA), plans)
    prices = data(
        {
            SHARE.asset_id: SHARE_MIDS,
            BETA.asset_id: BETA_MIDS,
            BASKET_CALL.asset_id: series("5", "6", "8", "9"),
        }
    )
    backtest = BacktestEngine.run(cfg, prices, runtime(plans), context_factory, schedule=schedule)
    whole = ReplayBacktest.run(cfg, prices, runtime(plans), context_factory, schedule=schedule)
    assert whole.backtest.fills == backtest.fills
    reference = digest_run(whole.backtest).result_id
    for pause in range(1, len(prices.records)):
        paused = ReplayBacktest.run(
            cfg,
            prices,
            runtime(plans),
            context_factory,
            schedule=schedule,
            pause_when=_pauses_at(pause),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {sid: entry.instance for sid, entry in runtime(plans).strategies.items()},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, prices, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference


def test_the_quotes_in_this_module_are_well_formed() -> None:
    # A guard on the fixtures: every series quotes the instants the runs read.
    assert set(SHARE_MIDS) == set(BETA_MIDS) == set(EURO_MIDS)
    assert quote(SHARE.asset_id, 2.0, D("100")).bid == D("100")


def test_an_exercise_stating_one_value_for_a_package_is_refused() -> None:
    contract = with_terms(
        option("PKGCASHAM", OptionType.CALL, ExerciseStyle.AMERICAN, SettlementStyle.CASH),
        basket=BASKET,
    )
    stated = RunSchedule.of(
        exercises=[
            ScheduledExercise(Exercise(contract.asset_id, D("1"), underlying_value=D("110")), 4.0)
        ]
    )
    with pytest.raises(RuntimeValidationError, match="one underlying value cannot state it"):
        run(contract, hold((contract, "1")), stated)
    # Stated by nothing, it settles against what the run has observed of each part.
    observed = RunSchedule.of(
        exercises=[ScheduledExercise(Exercise(contract.asset_id, D("1")), 4.0)]
    )
    result = run(contract, hold((contract, "1")), observed)
    package = D("100") * D(SHARE_MIDS[4.0]) + D("50") * D(BETA_MIDS[4.0])
    paid = (package / 100 - D("100")) * 100
    assert cash(result) == START_CASH - D("5") * 100 + paid


def test_a_quanto_contract_converts_at_the_rate_it_fixes_not_the_markets() -> None:
    quanto = with_terms(QUANTO_STYLE, consideration_rate=D("0.90"))
    schedule = RunSchedule.of(
        rates=list(RATES),  # the market's rate is 0.80; the contract fixes 0.90
        exercises=[ScheduledExercise(Exercise(quanto.asset_id, D("1")), 4.0)],
    )
    mids = {EURO_SHARE.asset_id: EURO_MIDS, quanto.asset_id: series("5", "6", "8", "9")}
    result = run(
        quanto,
        hold((quanto, "1")),
        schedule,
        extra=(),
        mids=mids,
        configured=_two_currencies(quanto),
    )
    assert held(result, EURO_SHARE) == D("100")
    assert cash(result, "USD") == START_CASH - D("5") * 100 - D("10000")
    share = position(result, EURO_SHARE)
    assert share.quantity * share.average_cost == D("9000")
    with pytest.raises(InstrumentInputError, match="names none"):
        replace(terms_of(CALL), consideration_rate=D("0.9"))
