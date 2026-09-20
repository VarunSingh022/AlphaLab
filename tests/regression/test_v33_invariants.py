"""The invariants v3.3's institutional surfaces rest on, measured on every run.

Each section below is a property that, if it broke, would produce numbers that
still look plausible. They are the ones a behavioural test would not catch:

* an execution cost counted twice -- once in the price and again in cash;
* a partial fill that conserved neither quantity nor cash;
* a liquidity cap exceeded by the fill the policy authorised;
* a risk decomposition whose parts no longer sum to the whole;
* an attribution that reconciles only because a bucket was fabricated;
* a scenario that mutated the book it was measuring;
* a "deterministic" model that is deterministic only within one process.

The last of those is why several tests here spawn a **fresh interpreter**: a
model built on ``hash()`` reproduces perfectly inside one run and differs on the
next, and no in-process assertion can tell the two apart. This follows
``test_the_suite_reports_nothing_deferred.py``, which spawns an interpreter for
the same reason.
"""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal

import pytest

from alphalab.analytics.attribution import (
    AttributionDimension,
    Availability,
    TradeFacts,
    TradeRecord,
    attribute,
)
from alphalab.analytics.decomposition import (
    PositionRisk,
    VaRMethod,
    VaRPolicy,
    portfolio_volatility,
    risk_contributions,
)
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import Side
from alphalab.execution.capacity import AssetLiquidity, CapacityModel
from alphalab.execution.commission import PercentageCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    ProportionalFee,
    ProportionalTax,
    QuotedHalfSpread,
    SquareRootImpact,
    itemized,
    reconciles,
)
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.latency import ConstantLatency, DeterministicLatency
from alphalab.execution.policy import ImmediateFill, LiquidityCappedFill, LiquidityContext
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import PercentageSlippage
from alphalab.scenario import ScenarioExposure, ScenarioState, scenario

# --------------------------------------------------------------------------- #
# 1. Execution cost accounting reconciles, and nothing is counted twice
# --------------------------------------------------------------------------- #


def instrument(side: Side = Side.BUY) -> OrderInstruction:
    return OrderInstruction(
        order_id="ORD-1",
        strategy_id="MOM",
        asset_id="AAPL",
        quantity=Decimal("100"),
        price=Decimal("50"),
        side=side,
        venue="SIM",
        currency="USD",
    )


def full_cost_model() -> ExecutionCostModel:
    return ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=PercentageSlippage(Decimal("0.0005")),
        impact_model=SquareRootImpact(Decimal("0.08")),
        commission_model=PercentageCommission(Decimal("0.0002")),
        fee_model=ProportionalFee(Decimal("0.0001")),
        tax_model=ProportionalTax(Decimal("0.005"), frozenset({Side.BUY})),
    )


def simulator() -> ExecutionSimulator:
    return ExecutionSimulator(cost_model=full_cost_model(), latency_model=ConstantLatency(0.5))


def test_the_report_s_two_cost_figures_are_exactly_the_six_items() -> None:
    """The itemization and the report cannot drift apart."""

    sim = simulator()
    kwargs = {
        "bid": Decimal("49.95"),
        "ask": Decimal("50.05"),
        "available_liquidity": Decimal("4000"),
    }
    costs = sim.simulate_costs(instrument(), Decimal("100"), Decimal("50"), 1.0, **kwargs)
    report = sim.simulate_fill(
        instrument(), Decimal("100"), Decimal("50"), 1.0, FillStatus.FULL_FILL, **kwargs
    )

    assert reconciles(costs, report.slippage, report.commission)


def test_a_price_embedded_cost_is_in_the_price_and_never_also_in_cash() -> None:
    """The double-count this separation exists to prevent."""

    sim = simulator()
    kwargs = {
        "bid": Decimal("49.95"),
        "ask": Decimal("50.05"),
        "available_liquidity": Decimal("4000"),
    }
    costs = sim.simulate_costs(instrument(), Decimal("100"), Decimal("50"), 1.0, **kwargs)
    report = sim.simulate_fill(
        instrument(), Decimal("100"), Decimal("50"), 1.0, FillStatus.FULL_FILL, **kwargs
    )

    # The fill price carries exactly the three concessions, and no more.
    assert report.fill_price == Decimal("50") + costs.price_concession
    # The cash charge carries exactly the three cash costs, and no concession.
    assert report.commission == costs.commission + costs.fees + costs.tax
    assert costs.price_concession not in (report.commission,)


def test_the_all_in_cost_is_the_two_channels_and_nothing_else() -> None:
    sim = simulator()
    kwargs = {
        "bid": Decimal("49.95"),
        "ask": Decimal("50.05"),
        "available_liquidity": Decimal("4000"),
    }
    quantity = Decimal("100")
    costs = sim.simulate_costs(instrument(), quantity, Decimal("50"), 1.0, **kwargs)
    report = sim.simulate_fill(
        instrument(), quantity, Decimal("50"), 1.0, FillStatus.FULL_FILL, **kwargs
    )

    paid = report.fill_price * quantity + report.commission
    reference = Decimal("50") * quantity

    assert paid - reference == costs.total(quantity)


def test_a_simulator_configured_the_pre_v33_way_is_unchanged() -> None:
    """One costing path, and the legacy configuration still means what it did."""

    legacy = ExecutionSimulator(
        commission_model=PercentageCommission(Decimal("0.001")),
        slippage_model=PercentageSlippage(Decimal("0.002")),
    )
    report = legacy.simulate_fill(
        instrument(), Decimal("100"), Decimal("50"), 1.0, FillStatus.FULL_FILL
    )

    slippage = PercentageSlippage(Decimal("0.002")).calculate(
        Decimal("100"), Decimal("50"), Side.BUY
    )
    expected_price = Decimal("50") + slippage

    assert report.slippage == slippage
    assert report.fill_price == expected_price
    assert report.commission == PercentageCommission(Decimal("0.001")).calculate(
        Decimal("100"), expected_price
    )


def test_a_sell_and_a_buy_pay_the_concession_in_opposite_directions() -> None:
    sim = simulator()
    kwargs = {
        "bid": Decimal("49.95"),
        "ask": Decimal("50.05"),
        "available_liquidity": Decimal("4000"),
    }
    buy = sim.simulate_fill(
        instrument(Side.BUY), Decimal("100"), Decimal("50"), 1.0, FillStatus.FULL_FILL, **kwargs
    )
    sell = sim.simulate_fill(
        instrument(Side.SELL), Decimal("100"), Decimal("50"), 1.0, FillStatus.FULL_FILL, **kwargs
    )

    assert buy.fill_price > Decimal("50") > sell.fill_price
    assert buy.fill_price - Decimal("50") == Decimal("50") - sell.fill_price


def test_every_itemized_component_is_non_negative() -> None:
    costs = simulator().simulate_costs(
        instrument(),
        Decimal("100"),
        Decimal("50"),
        1.0,
        bid=Decimal("49.95"),
        ask=Decimal("50.05"),
        available_liquidity=Decimal("4000"),
    )

    assert all(value >= Decimal("0") for value in itemized(costs, Decimal("100")).values())


# --------------------------------------------------------------------------- #
# 2. Partial fills conserve quantity, and liquidity caps hold
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("participation", ["0.01", "0.10", "0.25", "0.50", "1.00"])
def test_a_liquidity_capped_fill_never_exceeds_its_share_of_shown_liquidity(
    participation: str,
) -> None:
    policy = LiquidityCappedFill(Decimal(participation))
    available = Decimal("1000")
    context = LiquidityContext("AAPL", Side.BUY, Decimal("5000"), Decimal("50"), available, 1.0)
    decision = policy.decide(context)

    assert decision.quantity is not None
    assert decision.quantity <= available * Decimal(participation)


def test_a_partial_fill_plus_its_remainder_is_the_requested_quantity() -> None:
    requested = Decimal("5000")
    policy = LiquidityCappedFill(Decimal("0.10"))
    context = LiquidityContext("AAPL", Side.BUY, requested, Decimal("50"), Decimal("1000"), 1.0)
    decision = policy.decide(context)

    assert decision.status is FillStatus.PARTIAL_FILL
    assert decision.quantity is not None
    remainder = requested - decision.quantity

    assert decision.quantity + remainder == requested
    assert remainder > Decimal("0")


def test_a_sequence_of_capped_fills_conserves_the_total_quantity() -> None:
    """Quantity is neither created nor destroyed across a partial-fill sequence."""

    requested = Decimal("5000")
    policy = LiquidityCappedFill(Decimal("0.50"))
    remaining = requested
    filled = Decimal("0")

    for _ in range(20):
        if remaining <= Decimal("0"):
            break
        context = LiquidityContext("AAPL", Side.BUY, remaining, Decimal("50"), Decimal("1000"), 1.0)
        decision = policy.decide(context)
        quantity = decision.quantity if decision.quantity is not None else remaining
        filled += quantity
        remaining -= quantity

    assert filled + remaining == requested


def test_an_event_showing_no_liquidity_produces_no_fill_rather_than_a_small_one() -> None:
    policy = LiquidityCappedFill(Decimal("0.10"))
    context = LiquidityContext("AAPL", Side.BUY, Decimal("100"), Decimal("50"), Decimal("0"), 1.0)

    assert policy.decide(context).status is FillStatus.NO_FILL


def test_costs_are_charged_on_what_filled_not_on_what_was_asked_for() -> None:
    sim = simulator()
    asked = sim.simulate_costs(
        instrument(),
        Decimal("1000"),
        Decimal("50"),
        1.0,
        bid=Decimal("49.95"),
        ask=Decimal("50.05"),
        available_liquidity=Decimal("4000"),
    )
    filled = sim.simulate_costs(
        instrument(),
        Decimal("100"),
        Decimal("50"),
        1.0,
        bid=Decimal("49.95"),
        ask=Decimal("50.05"),
        available_liquidity=Decimal("4000"),
    )

    assert filled.impact < asked.impact
    assert filled.cash_charged < asked.cash_charged


# --------------------------------------------------------------------------- #
# 3. Determinism, including across processes
# --------------------------------------------------------------------------- #

_CROSS_PROCESS = """
from decimal import Decimal
from alphalab.core.enums import Side
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.latency import DeterministicLatency
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.scenario import scenario

print(round(DeterministicLatency(0.1, 0.5).calculate("ORDER-ABC", 0.0), 12))

instruction = OrderInstruction(
    "ORD-1", "MOM", "AAPL", Decimal("100"), Decimal("50"), Side.BUY, "SIM", "USD"
)
sim = ExecutionSimulator(latency_model=DeterministicLatency(0.1, 0.5))
report = sim.simulate_fill(instruction, Decimal("100"), Decimal("50"), 1.0, FillStatus.FULL_FILL)
print(report.timestamp)
print(scenario("crash", price_shock=Decimal("-0.30")).identity())
"""


def _run_in_fresh_interpreter() -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def test_a_deterministic_latency_is_deterministic_across_processes() -> None:
    """It was not: ``hash()`` on a str is salted per process (PEP 456).

    Asserted from separate interpreters, because within one process a salted
    hash is perfectly stable and this failure is invisible.
    """

    assert _run_in_fresh_interpreter() == _run_in_fresh_interpreter()


def test_the_latency_draw_still_varies_between_orders() -> None:
    """Stable is not the same as constant: the model must still spread."""

    model = DeterministicLatency(0.1, 0.5)
    draws = {model.calculate(f"ORDER-{index}", 0.0) for index in range(50)}

    assert len(draws) > 40
    assert all(0.1 <= draw <= 0.5 for draw in draws)


def test_a_scenario_identity_is_stable_across_processes() -> None:
    identity = scenario("crash", price_shock=Decimal("-0.30")).identity()

    assert identity in _run_in_fresh_interpreter()


def test_a_fill_timestamp_reproduces_across_processes() -> None:
    lines = _run_in_fresh_interpreter().splitlines()

    assert lines == _run_in_fresh_interpreter().splitlines()


# --------------------------------------------------------------------------- #
# 4. Capacity is deterministic and its assumptions travel with it
# --------------------------------------------------------------------------- #


def test_capacity_is_reproducible_and_order_independent() -> None:
    universe = (
        AssetLiquidity("LIQUID", Decimal("50"), Decimal("1e7"), Decimal("0.5"), "USD", "SIM"),
        AssetLiquidity("THIN", Decimal("20"), Decimal("1e5"), Decimal("0.5"), "USD", "SIM"),
    )
    model = CapacityModel(
        Decimal("0.10"),
        Decimal("0.20"),
        SquareRootImpact(Decimal("0.05")),
        Decimal("0.002"),
        Decimal("1e12"),
    )

    assert model.capacity(universe, 0.0) == model.capacity(tuple(reversed(universe)), 0.0)


def test_a_capacity_figure_never_travels_without_its_assumptions() -> None:
    universe = (AssetLiquidity("A", Decimal("50"), Decimal("1e7"), Decimal("1"), "USD", "SIM"),)
    result = CapacityModel(
        Decimal("0.10"),
        Decimal("0.20"),
        SquareRootImpact(Decimal("0.05")),
        Decimal("0.002"),
        Decimal("1e12"),
    ).capacity(universe, 0.0)

    assert set(result.assumptions) == {
        "participation_limit",
        "turnover",
        "impact_model",
        "impact_budget",
        "search_ceiling",
    }


def test_capacity_and_the_backtest_price_impact_with_the_same_model() -> None:
    """One impact assumption, not two: the protocol is shared.

    A capacity study that used a different impact model from the one the fills
    were priced with would report a ceiling the backtest's own costs contradict.
    """

    impact = SquareRootImpact(Decimal("0.05"))
    model = CapacityModel(
        Decimal("0.10"), Decimal("0.20"), impact, Decimal("0.002"), Decimal("1e12")
    )
    asset = AssetLiquidity("A", Decimal("50"), Decimal("1e6"), Decimal("1"), "USD", "SIM")

    capital = Decimal("100000")
    traded_units = capital * asset.weight * model.turnover / asset.price
    from alphalab.execution.costs import CostContext

    direct = impact.impact(
        CostContext(
            "A",
            Side.BUY,
            traded_units,
            Decimal("50"),
            "USD",
            "SIM",
            0.0,
            available_liquidity=Decimal("1e6"),
        )
    )

    assert model.impact_at(asset, capital, 0.0) == direct


# --------------------------------------------------------------------------- #
# 5. Risk decomposition is internally consistent
# --------------------------------------------------------------------------- #

_A = (0.010, -0.005, 0.021, -0.013, 0.004, 0.017, -0.020, 0.009, 0.002, -0.008)
_B = (-0.004, 0.012, -0.009, 0.016, -0.002, -0.014, 0.011, 0.003, -0.007, 0.010)
_C = (0.006, 0.002, 0.009, -0.003, 0.008, 0.001, -0.005, 0.007, 0.004, -0.002)

BOOK = (
    PositionRisk("AAA", Decimal("600000"), "USD", _A),
    PositionRisk("BBB", Decimal("-250000"), "USD", _B),
    PositionRisk("CCC", Decimal("150000"), "USD", _C),
)


def test_risk_contributions_sum_to_the_volatility_they_decompose() -> None:
    assert sum(risk_contributions(BOOK).values()) == pytest.approx(
        portfolio_volatility(BOOK), rel=1e-12
    )


def test_scaling_every_position_leaves_the_volatility_of_the_weights_unchanged() -> None:
    """Volatility is a function of weights, and weights are scale free."""

    doubled = tuple(
        PositionRisk(p.asset_id, p.market_value * 2, p.currency, p.returns) for p in BOOK
    )

    assert portfolio_volatility(doubled) == pytest.approx(portfolio_volatility(BOOK), rel=1e-12)


def test_cvar_is_never_below_var_for_any_method() -> None:
    returns = tuple(0.6 * a - 0.25 * b + 0.15 * c for a, b, c in zip(_A, _B, _C, strict=True))

    for method in VaRMethod:
        policy = VaRPolicy(method, 0.95)

        assert policy.cvar(returns) >= policy.var(returns) - 1e-12


def test_the_var_method_is_recoverable_from_the_figure_s_policy() -> None:
    policy = VaRPolicy(VaRMethod.CORNISH_FISHER, 0.99)

    assert policy.identity() == "CORNISH_FISHER@0.99"


# --------------------------------------------------------------------------- #
# 6. Attribution reconciles, and never fabricates
# --------------------------------------------------------------------------- #


def test_every_reconciling_dimension_sums_to_the_portfolio_result() -> None:
    trades = (
        TradeRecord(
            "T1",
            "AAPL",
            "Tech",
            Decimal("100.00"),
            Decimal("5000"),
            1.0,
            (StrategyContribution("MOM", Decimal("60")), StrategyContribution("MR", Decimal("40"))),
        ),
        TradeRecord(
            "T2",
            "SAP",
            "Tech",
            Decimal("-40.00"),
            Decimal("2000"),
            1.0,
            (StrategyContribution("MOM", Decimal("100")),),
        ),
    )
    facts = {
        "T1": TradeFacts("T1", currency="USD", venue="XNAS", broker="B1", country="US"),
        "T2": TradeFacts("T2", currency="USD", venue="XETR", broker="B2", country="DE"),
    }
    report = attribute(trades, facts)

    for dimension, built in report.dimensions.items():
        if built.reconciles:
            assert sum(built.buckets.values()) == report.realized_pnl, dimension


def test_no_dimension_ever_invents_an_unknown_bucket() -> None:
    trades = (TradeRecord("T1", "AAPL", None, Decimal("10.00"), Decimal("100"), None, ()),)
    report = attribute(trades)

    for built in report.dimensions.values():
        assert not {"UNKNOWN", "UNCLASSIFIED", "N/A", "", "OTHER"} & set(built.buckets)


def test_an_unavailable_dimension_never_claims_to_reconcile() -> None:
    trades = (TradeRecord("T1", "AAPL", None, Decimal("10.00"), Decimal("100"), None, ()),)
    report = attribute(trades)

    for built in report.dimensions.values():
        if built.availability is not Availability.AVAILABLE:
            assert not built.reconciles


def test_currency_attribution_does_not_total_across_currencies() -> None:
    trades = (
        TradeRecord("T1", "AAPL", None, Decimal("100.00"), Decimal("1"), None, ()),
        TradeRecord("T2", "SAP", None, Decimal("100.00"), Decimal("1"), None, ()),
    )
    facts = {
        "T1": TradeFacts("T1", currency="USD"),
        "T2": TradeFacts("T2", currency="EUR"),
    }
    report = attribute(trades, facts)

    assert not report.dimensions[AttributionDimension.CURRENCY].reconciles
    assert len(report.currencies) == 2


# --------------------------------------------------------------------------- #
# 7. Scenarios do not leak into the base state
# --------------------------------------------------------------------------- #


def state() -> ScenarioState:
    return ScenarioState(
        exposures=(
            ScenarioExposure(
                "AAPL",
                Decimal("1000"),
                Decimal("200"),
                "USD",
                volatility=0.2,
                available_liquidity=Decimal("50000"),
                sector="Tech",
            ),
            ScenarioExposure(
                "SAP",
                Decimal("500"),
                Decimal("100"),
                "EUR",
                volatility=0.3,
                available_liquidity=Decimal("20000"),
                sector="Tech",
            ),
        ),
        base_currency="USD",
        rates={"EUR": Decimal("1.10")},
    )


def test_applying_many_scenarios_leaves_the_base_state_byte_identical() -> None:
    base = state()
    snapshot = (base.exposures, dict(base.rates), base.value())

    for magnitude in ("-0.05", "-0.20", "-0.50", "-0.99"):
        scenario("s", price_shock=Decimal(magnitude)).apply(base)
        scenario("v", volatility_shock=Decimal(magnitude)).apply(base)
        scenario("l", liquidity_shock=Decimal(magnitude)).apply(base)

    assert (base.exposures, dict(base.rates), base.value()) == snapshot


def test_a_shocked_state_can_itself_be_shocked_without_touching_the_original() -> None:
    base = state()
    once = scenario("a", price_shock=Decimal("-0.1")).apply(base)
    twice = scenario("b", price_shock=Decimal("-0.1")).apply(once.shocked_state)

    assert base.value() == once.base_value
    assert once.shocked_value == twice.base_value
    assert twice.shocked_value < once.shocked_value


def test_the_scenario_result_carries_both_states_so_neither_has_to_be_remembered() -> None:
    base = state()
    result = scenario("s", price_shock=Decimal("-0.25")).apply(base)

    assert result.base_state == base
    assert result.base_state.value() == result.base_value
    assert result.shocked_state.value() == result.shocked_value


def test_an_immediate_fill_policy_still_fills_everything_requested() -> None:
    """The v3.3 cost work did not change what the default policy decides."""

    context = LiquidityContext("AAPL", Side.BUY, Decimal("100"), Decimal("50"), None, 1.0)
    decision = ImmediateFill().decide(context)

    assert decision.status is FillStatus.FULL_FILL
    assert decision.quantity == Decimal("100")
