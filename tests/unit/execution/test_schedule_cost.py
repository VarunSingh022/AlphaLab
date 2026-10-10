"""A schedule's shortfall under the Almgren-Chriss model, and a measured one beside it (v3.13).

ADR-0044 deferred "implementation shortfall with an intraday market-impact
model", and the pre-v4 ledger missed it. v3.13 states the model's expected
shortfall and its variance for any schedule of equal intervals
(:func:`schedule_cost`), the cost of the estimate's own optimal schedule
(:meth:`UrgencyEstimate.cost`), and reads a measured shortfall beside it
(:func:`shortfall_against_model`).

The figures are checked against references that share no code with them: hand
arithmetic for a straight schedule, the paper's closed forms for the optimal
one, and -- for optimality itself -- every perturbation of the schedule tried
costing more in ``E + lambda V``.
"""

from __future__ import annotations

import math
import random
from decimal import ROUND_HALF_EVEN, Context, Decimal

import pytest

from alphalab.core.enums import OrderStatus, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution import (
    ExecutionValidationError,
    ImplementationShortfall,
    ScheduleCost,
    UrgencyEstimate,
    estimate_urgency,
    implementation_shortfall,
    schedule_cost,
    shortfall_against_model,
)
from alphalab.execution.fill import FillStatus
from alphalab.execution.quality import ExecutionBenchmarks, OrderExecution
from alphalab.execution.report import ExecutionReport

D = Decimal
#: The module computes in a fixed 34-digit context; references are computed in it too.
CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)

#: Almgren and Chriss's (2000) worked example: a million shares over five days
#: in five slices, sigma 0.95 a share per root day, epsilon 0.0625 a share, eta
#: 2.5e-6 a share per share a day, gamma 2.5e-7 a share per share.
VOLATILITY = D("0.95")
ETA = D("0.0000025")
GAMMA = D("0.00000025")
EPSILON = D("0.0625")
HORIZON = D("5")
SLICES = 5
QUANTITY = D("1000000")


def _cost(trades: list[Decimal]) -> ScheduleCost:
    return schedule_cost(
        trades,
        volatility=VOLATILITY,
        temporary_impact=ETA,
        permanent_impact=GAMMA,
        fixed_cost=EPSILON,
        horizon=HORIZON,
    )


def _estimate(risk_aversion: str) -> UrgencyEstimate:
    return estimate_urgency(
        risk_aversion=D(risk_aversion),
        volatility=VOLATILITY,
        temporary_impact=ETA,
        permanent_impact=GAMMA,
        horizon=HORIZON,
        slices=SLICES,
    )


def test_a_straight_schedule_costs_what_hand_arithmetic_says() -> None:
    cost = _cost([D("200000")] * 5)

    # gamma X^2 / 2 = 2.5e-7 * 1e12 / 2; epsilon X; eta_tilde = 2.5e-6 - 1.25e-7,
    # times 5 * (2e5)^2 over tau = 1; sigma^2 tau (8e5^2 + 6e5^2 + 4e5^2 + 2e5^2).
    assert cost.permanent == D("125000")
    assert cost.fixed == D("62500")
    assert cost.temporary == D("475000")
    assert cost.expected == D("662500")
    assert cost.variance == D("1083000000000")
    assert cost.standard_deviation == CONTEXT.sqrt(D("1083000000000"))
    assert cost.interval == D("1") and cost.quantity == QUANTITY


@pytest.mark.parametrize("risk_aversion", ["0.000001", "0.000002", "0.00001"])
def test_the_optimal_schedule_has_the_papers_closed_form_cost(risk_aversion: str) -> None:
    """Almgren and Chriss (2000), the expectation and variance of the optimal trajectory."""

    estimate = _estimate(risk_aversion)
    cost = estimate.cost(QUANTITY, fixed_cost=EPSILON)
    kappa, horizon, tau = float(estimate.kappa), float(HORIZON), float(HORIZON) / SLICES
    x, gamma, sigma = float(QUANTITY), float(GAMMA), float(VOLATILITY)
    eta_tilde = float(ETA) - gamma * tau / 2
    expected = (
        gamma * x * x / 2
        + float(EPSILON) * x
        + eta_tilde
        * x
        * x
        * math.tanh(kappa * tau / 2)
        * (tau * math.sinh(2 * kappa * horizon) + 2 * horizon * math.sinh(kappa * tau))
        / (2 * tau * tau * math.sinh(kappa * horizon) ** 2)
    )
    variance = (
        sigma
        * sigma
        * x
        * x
        / 2
        * (
            tau * math.sinh(kappa * horizon) * math.cosh(kappa * (horizon - tau))
            - horizon * math.sinh(kappa * tau)
        )
        / (math.sinh(kappa * horizon) ** 2 * math.sinh(kappa * tau))
    )

    assert float(cost.expected) == pytest.approx(expected, rel=1e-12)
    assert float(cost.variance) == pytest.approx(variance, rel=1e-12)
    assert sum(cost.trades, D("0")) == pytest.approx(QUANTITY, abs=D("1e-20"))


@pytest.mark.parametrize("risk_aversion", ["0", "0.000002"])
def test_no_schedule_of_the_same_intervals_costs_less(risk_aversion: str) -> None:
    """What the estimate trades is the least ``E + lambda V`` any schedule attains."""

    aversion = D(risk_aversion)
    best = _estimate(risk_aversion).cost(QUANTITY, fixed_cost=EPSILON)
    floor = best.expected + aversion * best.variance
    rng = random.Random(7)
    for _ in range(300):
        trades = list(best.trades)
        source, target = rng.sample(range(SLICES), 2)
        moved = trades[source] * D(str(rng.uniform(0.0, 0.2)))
        trades[source] -= moved
        trades[target] += moved
        other = _cost(trades)
        assert other.expected + aversion * other.variance >= floor * (1 - D("1e-28"))


def test_a_risk_neutral_estimate_trades_evenly() -> None:
    cost = _estimate("0").cost(QUANTITY, fixed_cost=EPSILON)

    assert cost.trades == tuple(D("200000") for _ in range(SLICES))
    assert cost.expected == D("662500")


def test_what_the_model_cannot_cost_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="no interval"):
        _cost([])
    with pytest.raises(ExecutionValidationError, match="one side"):
        _cost([D("100"), D("-10")])
    with pytest.raises(ExecutionValidationError, match="trades nothing"):
        _cost([D("0"), D("0")])
    with pytest.raises(ExecutionValidationError, match="fixed_cost"):
        schedule_cost(
            [D("1")],
            volatility=VOLATILITY,
            temporary_impact=ETA,
            permanent_impact=GAMMA,
            fixed_cost=D("-0.01"),
            horizon=HORIZON,
        )
    with pytest.raises(ExecutionValidationError, match="outweighs"):
        schedule_cost(
            [D("1")],
            volatility=VOLATILITY,
            temporary_impact=D("0.000001"),
            permanent_impact=D("0.000001"),
            fixed_cost=EPSILON,
            horizon=D("5"),
        )
    with pytest.raises(ExecutionValidationError, match="quantity"):
        _estimate("0.000002").cost(D("0"), fixed_cost=EPSILON)


# --------------------------------------------------------------------------- #
# A measured shortfall beside the model's
# --------------------------------------------------------------------------- #


def _shortfall(
    fills: tuple[tuple[str, str], ...] = (("60", "50.25"), ("40", "50.30")),
    arrival: str | None = "50.20",
    status: OrderStatus = OrderStatus.FILLED,
) -> ImplementationShortfall:
    order = OrderRequest("ORD-1", "", "ASSET", Side.BUY, D("100"), D("50"), 10.0)
    reports = tuple(
        ExecutionReport(
            execution_id=f"X-{index}",
            order_id="ORD-1",
            asset_id="ASSET",
            strategy_id="",
            timestamp=20.0 + index,
            fill_price=D(price),
            fill_quantity=D(quantity),
            commission=D("1"),
            slippage=D("0"),
            liquidity_flag="",
            venue="VEN-A",
            currency="USD",
            status=FillStatus.PARTIAL_FILL,
        )
        for index, (quantity, price) in enumerate(fills)
    )
    return implementation_shortfall(
        OrderExecution(
            order=order,
            fills=reports,
            currency="USD",
            outcome=status,
            benchmarks=ExecutionBenchmarks(
                decision_price=D("50.00"),
                arrival_price=None if arrival is None else D(arrival),
                interval_vwap=None,
                end_price=D("50.40"),
                source="unit tape",
            ),
            limit_price=None,
            timeline=None,
            fill_midpoints=None,
            account_id="ACC-1",
        )
    )


def _model(quantity: str = "100") -> ScheduleCost:
    shares = D(quantity)
    return schedule_cost(
        [shares / 4] * 4,
        volatility=D("0.10"),
        temporary_impact=D("0.002"),
        permanent_impact=D("0.0005"),
        fixed_cost=D("0.01"),
        horizon=D("1"),
    )


def test_a_measured_shortfall_is_read_from_arrival_beside_the_model() -> None:
    measured = _shortfall()
    model = _model()
    reading = shortfall_against_model(measured, model)

    # From arrival: 60 at 0.05 and 40 at 0.10 over 50.20, and two commissions of 1.
    assert reading.realized == D("3") + D("4") + D("2")
    assert reading.expected == model.expected
    assert reading.difference == reading.realized - model.expected
    assert reading.standard_deviations == CONTEXT.divide(
        reading.difference, model.standard_deviation
    )
    assert reading.quantity == D("100") and reading.currency == "USD"


def test_a_model_without_variance_gives_no_standard_score() -> None:
    model = schedule_cost(
        [D("100")],
        volatility=D("0"),
        temporary_impact=D("0.002"),
        permanent_impact=D("0"),
        fixed_cost=D("0"),
        horizon=D("1"),
    )
    reading = shortfall_against_model(_shortfall(), model)

    assert model.variance == 0
    assert reading.standard_deviations is None


def test_a_shortfall_unlike_the_models_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="unfilled"):
        shortfall_against_model(
            _shortfall(fills=(("60", "50.25"),), status=OrderStatus.PARTIALLY_FILLED),
            _model("60"),
        )
    with pytest.raises(ExecutionValidationError, match="same quantity"):
        shortfall_against_model(_shortfall(), _model("80"))
    with pytest.raises(ExecutionValidationError, match="arrival"):
        shortfall_against_model(_shortfall(arrival=None), _model())
