"""Options repriced from their underlying in a scenario margin model (phase 3, ledger FUT-026).

A :class:`ScenarioMargin` declaring ``repriced`` values each such option under
every scenario from its underlying's declared move and the scenario's volatility
shift -- Black-Scholes in closed form for a European contract, a binomial
lattice for an American one -- from declared inputs (volatility, rate, carry)
and the contract's terms and the market's prices now. The loss is the model
value's change, so a book's gamma and vega are margined, not only its delta.

ACME quotes at 100; its options expire ninety days after the first quote.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import BacktestEngine, BacktestResult, ReplayBacktest, RunSchedule
from alphalab.backtesting.dataset import MarketDataset
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.margin import (
    MarginBasis,
    MarginScenario,
    MarginTerms,
    OptionPricing,
    OptionRisk,
    ScenarioMargin,
    margin_model_from_primitives,
    margin_model_to_primitives,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import declare_margin_model
from alphalab.instrument.terms import Deliverable, OptionTerms
from alphalab.lifecycle import digest_run
from alphalab.options.carry import FUTURES_CARRY, dividend_yield
from alphalab.options.contract import OptionContract
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle
from alphalab.options.pricing import black_scholes_value
from alphalab.portfolio.fx import NO_RATES
from alphalab.runtime.execution_pipeline import margin_position
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import (
    backtest_config,
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
)

D = Decimal
T0 = 1_717_500_000.0
EXPIRES = T0 + 90 * 86400
#: The pricer's year: 365.25 days.
YEAR = 365.25 * 86400
HALF = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"))
PREMIUM = MarginTerms(MarginBasis.VALUE_RATE, D("1"), D("1"))
SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD", margin=HALF)


def option(
    symbol: str,
    kind: OptionType,
    style: ExerciseStyle = ExerciseStyle.EUROPEAN,
    strike: str = "100",
    **terms: Any,
) -> InstrumentRecord:
    return InstrumentRecord(
        symbol,
        AssetType.OPTION,
        "XOPT",
        "USD",
        economics=InstrumentEconomics(
            D("100"), SettlementModel.OPTION_PREMIUM, lot=LotSpecification(D("1"), D("1"))
        ),
        last_trade_at=EXPIRES,
        margin=PREMIUM,
        terms=OptionTerms(
            option_type=kind,
            strike=D(strike),
            exercise_style=style,
            settlement=SettlementStyle.CASH,
            deliverable=Deliverable(SHARE.asset_id, D("100")),
            expires_at=EXPIRES,
            automatic_exercise=None,
            **terms,
        ),
    )


CALL = option("ACMEC100", OptionType.CALL)
PUT = option("ACMEP100", OptionType.PUT)
AM_PUT = option("ACMEPA100", OptionType.PUT, ExerciseStyle.AMERICAN)
PRICING = OptionPricing(D("0.30"), D("0.04"), dividend_yield(0.0))


def scenarios(*options: InstrumentRecord, vol: str = "0", **kw: Any) -> ScenarioMargin:
    shifts = {o.asset_id: D(vol) for o in options} if vol != "0" else {}
    return ScenarioMargin(
        (
            MarginScenario("down 15", {SHARE.asset_id: D("-0.15")}, shifts),
            MarginScenario("up 15", {SHARE.asset_id: D("0.15")}, shifts),
            MarginScenario("flat", {SHARE.asset_id: D("0")}, shifts),
        ),
        maintenance_ratio=D("0.75"),
        repriced={o.asset_id: kw.get("pricing", PRICING) for o in options},
    )


def book(
    plan: dict[str, str],
    model: ScenarioMargin,
    *records: InstrumentRecord,
    premium: dict[str, str] | None = None,
    cash: str = "1000000",
    trader: margin.Trader | None = None,
) -> BacktestResult:
    chosen = (SHARE, *records)
    registry = declare_margin_model(registry_of(*chosen), model)
    base = backtest_config("S", starting_cash=D(cash), risk_limits=margin.LEVERED)
    cfg = replace(
        base,
        pipeline=replace(
            base.pipeline,
            instruments=registry,
            budget=replace(
                base.pipeline.budget,
                global_capital=margin.WIDE,
                maximum_exposure=margin.WIDE,
                strategy_budgets={"S": margin.WIDE},
            ),
        ),
    )
    marks = {SHARE.asset_id: "100", **(premium or {})}
    quotes = [quote(asset, at, D(mid)) for asset, mid in marks.items() for at in (T0, T0 + 60)]
    trades = {(T0, asset): D(q) for asset, q in plan.items()}
    return BacktestEngine.run(
        cfg,
        MarketDataset.of("REPRICE", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id))),
        running_strategy_state("S", trader or margin.Trader(trades)),
        context_factory,
        schedule=RunSchedule.of(),
    )


def bs(kind: OptionType, spot: float, vol: float = 0.30, at: float = T0 + 60) -> Decimal:
    contract = OptionContract(SHARE.asset_id, D("100"), EXPIRES, kind, ExerciseStyle.EUROPEAN, 1)
    value = black_scholes_value(
        contract, spot, vol, 0.04, (EXPIRES - at) / YEAR, carry=dividend_yield(0.0)
    )
    return Decimal(repr(value)).quantize(D("0.0001"))


def required(result: BacktestResult) -> Decimal:
    figures = margin_position(result.state, NO_RATES, T0 + 60)
    assert figures is not None
    return figures.initial


# --------------------------------------------------------------------------- #
# Repriced by the scenarios
# --------------------------------------------------------------------------- #


def test_a_short_put_is_margined_by_its_repriced_loss_in_the_worst_scenario() -> None:
    result = book({PUT.asset_id: "-10"}, scenarios(PUT), PUT, premium={PUT.asset_id: "5"})
    now = bs(OptionType.PUT, 100.0)
    worst = max(bs(OptionType.PUT, spot) - now for spot in (85.0, 115.0, 100.0))
    # 10 contracts of 100 units, short: the put's rise in value is the loss.
    assert required(result) == worst * 10 * 100


def test_the_repriced_loss_carries_the_convexity_a_linear_move_leaves_out() -> None:
    result = book({PUT.asset_id: "-10"}, scenarios(PUT), PUT, premium={PUT.asset_id: "5"})
    now = bs(OptionType.PUT, 100.0)
    # A delta approximation of the fall: the slope at 100 times 15.
    slope = (bs(OptionType.PUT, 100.01) - bs(OptionType.PUT, 99.99)) / D("0.02")
    linear = -slope * 15 * 10 * 100
    assert required(result) > linear > 0
    assert required(result) == (bs(OptionType.PUT, 85.0) - now) * 10 * 100


def test_a_delta_hedge_offsets_the_move_and_leaves_the_gamma() -> None:
    hedged = book(
        {CALL.asset_id: "-10", SHARE.asset_id: "550"},
        scenarios(CALL),
        CALL,
        premium={CALL.asset_id: "6"},
    )
    naked = book({CALL.asset_id: "-10"}, scenarios(CALL), CALL, premium={CALL.asset_id: "6"})
    assert D("0") < required(hedged) < required(naked)
    now = bs(OptionType.CALL, 100.0)
    losses = [
        (bs(OptionType.CALL, 100.0 * (1 + m)) - now) * 1000 - D("550") * 100 * D(repr(m))
        for m in (-0.15, 0.15, 0.0)
    ]
    assert required(hedged) == max(max(losses), D("0"))


def test_a_volatility_shift_up_raises_a_short_options_requirement() -> None:
    plan, premium = {CALL.asset_id: "-10"}, {CALL.asset_id: "6"}
    calm = book(plan, scenarios(CALL), CALL, premium=premium)
    stressed = book(plan, scenarios(CALL, vol="0.10"), CALL, premium=premium)
    assert required(stressed) > required(calm)
    now = bs(OptionType.CALL, 100.0)
    assert required(stressed) == (bs(OptionType.CALL, 115.0, vol=0.40) - now) * 1000


def test_an_order_is_held_to_its_repriced_requirement_before_it_is_placed() -> None:
    # 10 puts written against 8,000 of equity: their own terms require the
    # 5,000 premium, but repriced the fall to 85 costs about 9,672 -- refused.
    plan = {PUT.asset_id: "-10"}
    trader = margin.Trader({(T0, PUT.asset_id): D("-10")})
    result = book(
        plan, scenarios(PUT), PUT, premium={PUT.asset_id: "5"}, cash="8000", trader=trader
    )
    assert margin.fills(result, PUT) == []
    assert any("initial margin" in r for r in trader.rejections)


def test_a_long_option_never_requires_more_than_its_model_value() -> None:
    result = book({CALL.asset_id: "10"}, scenarios(CALL), CALL, premium={CALL.asset_id: "6"})
    assert required(result) <= bs(OptionType.CALL, 100.0) * 1000
    assert required(result) == (bs(OptionType.CALL, 100.0) - bs(OptionType.CALL, 85.0)) * 1000


def test_an_american_put_is_valued_on_its_lattice_at_least_as_a_european_one() -> None:
    lattice = OptionPricing(D("0.30"), D("0.04"), dividend_yield(0.0), lattice_steps=100)
    american = book(
        {AM_PUT.asset_id: "-10"},
        scenarios(AM_PUT, pricing=lattice),
        AM_PUT,
        premium={AM_PUT.asset_id: "5"},
    )
    european = book({PUT.asset_id: "-10"}, scenarios(PUT), PUT, premium={PUT.asset_id: "5"})
    assert required(american) > 0 and required(european) > 0
    # Early exercise is worth something on the way down: the change differs.
    assert required(american) != required(european)


def test_at_expiry_an_option_is_worth_what_exercise_pays() -> None:
    pricing = PRICING
    risk = OptionRisk(
        SHARE.asset_id, D("100"), D("100"), OptionType.PUT, ExerciseStyle.EUROPEAN,
        EXPIRES, EXPIRES, D("100"), D("1"),
    )  # fmt: skip
    assert pricing.value(risk, D("85"), D("0")) == D("15")
    assert pricing.value(risk, D("115"), D("0")) == D("0")


def test_a_futures_underlying_is_valued_with_no_carry() -> None:
    risk = OptionRisk(
        SHARE.asset_id, D("100"), D("100"), OptionType.CALL, ExerciseStyle.EUROPEAN,
        EXPIRES, T0, D("100"), D("1"),
    )  # fmt: skip
    futures = OptionPricing(D("0.30"), D("0.04"), FUTURES_CARRY)
    assert futures.value(risk, D("100"), D("0")) < PRICING.value(risk, D("100"), D("0"))


def test_an_option_in_another_currency_converts_its_loss_at_the_rate() -> None:
    risk = OptionRisk(
        SHARE.asset_id, D("100"), D("100"), OptionType.PUT, ExerciseStyle.EUROPEAN,
        EXPIRES, T0, D("-1000"), D("1.25"),
    )  # fmt: skip
    from alphalab.instrument.margin import MarginExposure

    model = scenarios(PUT)
    exposure = MarginExposure(PUT.asset_id, D("-6250"), D("0"), D("0"), risk)
    in_usd = model.requirement((exposure,)).initial
    at_par = model.requirement((replace(exposure, option=replace(risk, to_base=D("1"))),)).initial
    assert in_usd == at_par * D("1.25")


# --------------------------------------------------------------------------- #
# What cannot be repriced, persistence, replay
# --------------------------------------------------------------------------- #


def test_an_option_whose_underlying_is_unpriced_is_margined_by_its_own_terms() -> None:
    registry = declare_margin_model(registry_of(SHARE, PUT), scenarios(PUT))
    base = backtest_config("S", starting_cash=D("1000000"), risk_limits=margin.LEVERED)
    cfg = replace(base, pipeline=replace(base.pipeline, instruments=registry))
    quotes = [quote(PUT.asset_id, at, D("5")) for at in (T0, T0 + 60)]
    result = BacktestEngine.run(
        cfg,
        MarketDataset.of("UNPRICED", quotes),
        running_strategy_state("S", margin.Trader({(T0, PUT.asset_id): D("-1")})),
        context_factory,
    )
    # Nothing values the put without ACME's price: its own terms, the premium.
    assert required(result) == D("5") * 100


@pytest.mark.parametrize(
    ("record", "pricing", "match"),
    [
        (option("ACMEB", OptionType.PUT, ExerciseStyle.AMERICAN), PRICING, "lattice"),
        (
            option("ACMEK", OptionType.PUT, cash_per_contract=D("5")),
            PRICING,
            "package or a quanto",
        ),
    ],
)
def test_an_option_the_model_cannot_value_is_refused_at_declaration(
    record: InstrumentRecord, pricing: OptionPricing, match: str
) -> None:
    with pytest.raises(InstrumentRegistrationError, match=match):
        declare_margin_model(registry_of(SHARE, record), scenarios(record, pricing=pricing))


@pytest.mark.parametrize(
    "build",
    [
        lambda: OptionPricing(D("0"), D("0.04"), dividend_yield(0.0)),
        lambda: OptionPricing(D("0.3"), D("0.04"), "flat"),  # type: ignore[arg-type]
        lambda: OptionPricing(D("0.3"), D("0.04"), dividend_yield(0.0), lattice_steps=0),
        lambda: scenarios(CALL, vol="-0.30"),
        lambda: ScenarioMargin(
            (MarginScenario("s", {SHARE.asset_id: D("0"), CALL.asset_id: D("0")}),),
            D("1"),
            repriced={CALL.asset_id: PRICING},
        ),
        lambda: ScenarioMargin(
            (MarginScenario("s", {SHARE.asset_id: D("0")}, {CALL.asset_id: D("0.1")}),),
            D("1"),
        ),
    ],
)
def test_a_repricing_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_a_repricing_model_round_trips_and_a_plain_one_keeps_its_form() -> None:
    american = OptionPricing(D("0.25"), D("0.03"), dividend_yield(0.01), lattice_steps=50)
    model = replace(scenarios(PUT, vol="0.05"), repriced={PUT.asset_id: american})
    assert margin_model_from_primitives(margin_model_to_primitives(model)) == model
    plain = ScenarioMargin((MarginScenario("s", {SHARE.asset_id: D("-0.1")}),), D("1"))
    written = margin_model_to_primitives(plain)
    assert written is not None and "repriced" not in written
    assert "volatility_shifts" not in written["scenarios"][0]


def test_a_repriced_book_replays_to_the_same_result() -> None:
    model = scenarios(PUT)
    plan = {(T0, PUT.asset_id): D("-10")}
    registry = declare_margin_model(registry_of(SHARE, PUT), model)
    base = backtest_config("S", starting_cash=D("1000000"), risk_limits=margin.LEVERED)
    cfg = replace(base, pipeline=replace(base.pipeline, instruments=registry))
    quotes = [
        quote(asset, at, D(mid))
        for asset, mid in ((SHARE.asset_id, "100"), (PUT.asset_id, "5"))
        for at in (T0, T0 + 60)
    ]
    dataset = MarketDataset.of("REPRICE", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))
    backtest = BacktestEngine.run(
        cfg, dataset, running_strategy_state("S", margin.Trader(plan)), context_factory
    )
    replay = ReplayBacktest.run(
        cfg, dataset, running_strategy_state("S", margin.Trader(plan)), context_factory
    )
    assert replay.backtest.fills == backtest.fills
    assert required(replay.backtest) == required(backtest)
    assert digest_run(replay.backtest).result_id
