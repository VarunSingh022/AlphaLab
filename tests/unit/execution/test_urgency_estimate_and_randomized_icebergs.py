"""Estimated urgency and randomized iceberg tranches (ledger BRK-006, v3.13).

Until v3.13 an urgency was stated, never estimated, and an iceberg's tranches
were all one size. The estimate is pinned against the model it claims to solve:
the schedule it shapes is checked against a direct minimization of the
Almgren-Chriss objective. The randomization is pinned for what a run needs from
it: bounded, whole, reproducible from its seed, different across parents, and
uniform.
"""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal
from uuid import UUID

import pytest

from alphalab.core.enums import OrderType, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution import (
    MAX_URGENCY,
    AlgorithmTerms,
    Iceberg,
    TrancheRandomization,
    Urgency,
    algorithm_configuration_id,
    estimate_urgency,
    record_child_execution,
    release_children,
    start_algorithm,
)
from alphalab.execution.exceptions import ExecutionValidationError

D = Decimal

# The worked example of Almgren and Chriss (2000), section 4.
EXAMPLE = {
    "risk_aversion": D("0.000002"),
    "volatility": D("0.95"),
    "temporary_impact": D("0.0000025"),
    "permanent_impact": D("0.00000025"),
    "horizon": D("5"),
}


def _direct_optimum(slices: int, holdings: float = 1_000_000.0) -> list[float]:
    """Minimize E + lambda V over the discrete trajectory by its first-order conditions.

    E = sum (eta_tilde / tau) n_k^2 (+ a constant), V = sigma^2 tau sum x_k^2: a
    tridiagonal linear system in the holdings, solved by the Thomas algorithm.
    Independent of the closed form under test.
    """

    lam, sigma = float(EXAMPLE["risk_aversion"]), float(EXAMPLE["volatility"])
    eta, gamma = float(EXAMPLE["temporary_impact"]), float(EXAMPLE["permanent_impact"])
    tau = float(EXAMPLE["horizon"]) / slices
    coupling = 2.0 * (eta - 0.5 * gamma * tau) / tau
    diagonal = 2.0 * coupling + 2.0 * lam * sigma**2 * tau
    size = slices - 1
    upper, solution = [0.0] * size, [0.0] * size
    rhs = [0.0] * size
    rhs[0] = coupling * holdings
    upper[0] = -coupling / diagonal
    solution[0] = rhs[0] / diagonal
    for row in range(1, size):
        pivot = diagonal + coupling * upper[row - 1]
        upper[row] = -coupling / pivot
        solution[row] = (rhs[row] + coupling * solution[row - 1]) / pivot
    for row in range(size - 2, -1, -1):
        solution[row] -= upper[row] * solution[row + 1]
    return [holdings, *solution, 0.0]


@pytest.mark.parametrize("slices", [5, 25])
def test_the_estimated_urgency_shapes_the_models_optimal_schedule(slices: int) -> None:
    estimate = estimate_urgency(**EXAMPLE, slices=slices)
    direct = _direct_optimum(slices)
    for k in range(slices + 1):
        due = estimate.urgency.fraction(D(k) / D(slices))
        holding = 1_000_000.0 * (1.0 - float(due))
        assert holding == pytest.approx(direct[k], abs=1e-6)


def test_the_estimate_approaches_the_continuous_kappa_as_slices_shrink() -> None:
    continuous = math.sqrt(
        float(EXAMPLE["risk_aversion"])
        * float(EXAMPLE["volatility"]) ** 2
        / float(EXAMPLE["temporary_impact"])
    )
    fine = estimate_urgency(**EXAMPLE, slices=5000)
    assert float(fine.kappa) == pytest.approx(continuous, rel=1e-3)


def test_a_risk_neutral_trader_gets_a_straight_schedule() -> None:
    estimate = estimate_urgency(**{**EXAMPLE, "risk_aversion": D("0")}, slices=10)
    assert estimate.urgency == Urgency.neutral()
    assert estimate.kappa == 0


def test_more_risk_aversion_is_more_urgency() -> None:
    low = estimate_urgency(**EXAMPLE, slices=10).urgency.kappa
    high = estimate_urgency(**{**EXAMPLE, "risk_aversion": D("0.00001")}, slices=10).urgency.kappa
    assert high > low > 0


def test_the_estimate_is_the_same_decimal_every_time_and_records_its_inputs() -> None:
    first = estimate_urgency(**EXAMPLE, slices=25)
    assert first == estimate_urgency(**EXAMPLE, slices=25)
    assert first.slices == 25
    assert first.volatility == D("0.95")
    # The urgency is kappa T (the product here rounds at the default 28 digits).
    assert abs(first.urgency.kappa - first.kappa * D("5")) < D("1e-26")


def test_inputs_the_model_has_no_optimum_for_are_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="no optimal schedule"):
        estimate_urgency(**{**EXAMPLE, "permanent_impact": D("0.01")}, slices=2)
    with pytest.raises(ExecutionValidationError, match=">= 0"):
        estimate_urgency(**{**EXAMPLE, "volatility": D("-1")}, slices=5)
    with pytest.raises(ExecutionValidationError, match="> 0"):
        estimate_urgency(**{**EXAMPLE, "temporary_impact": D("0")}, slices=5)
    with pytest.raises(ExecutionValidationError, match="positive int"):
        estimate_urgency(**EXAMPLE, slices=0)
    # On five slices kappa grows only with the log of the risk aversion: about
    # 64 at lambda = 1, past the ceiling of 100 near 1e5.
    with pytest.raises(ExecutionValidationError, match="exceeds"):
        estimate_urgency(**{**EXAMPLE, "risk_aversion": D("100000")}, slices=5)
    assert D("100") == MAX_URGENCY


# --------------------------------------------------------------------------- #
# Randomized iceberg tranches
# --------------------------------------------------------------------------- #


def _parent(quantity: str, order_id: str = "0f1e2d3c-4b5a-4968-8776-655443322110") -> OrderRequest:
    return OrderRequest(
        order_id=str(UUID(order_id)),
        strategy_id="S",
        asset_id="ASSET-1",
        side=Side.BUY,
        quantity=D(quantity),
        price=D("50"),
        timestamp=0.0,
    )


TERMS = AlgorithmTerms(0.0, 300.0, D("1"), OrderType.MARKET, None)


def _tranches(
    algorithm: Iceberg, quantity: str = "1000", order_id: str | None = None
) -> list[Decimal]:
    parent = _parent(quantity) if order_id is None else _parent(quantity, order_id)
    state = start_algorithm(parent, algorithm, TERMS)
    sizes: list[Decimal] = []
    for step in range(10_000):
        state, released = release_children(state, 1.0)
        if not released:
            break
        (child,) = released
        sizes.append(child.quantity)
        state = record_child_execution(state, child.child_id, f"X-{step}", child.quantity, 1.0)
    return sizes


RANDOM = Iceberg(D("10"), TrancheRandomization(D("3"), 7))


def test_randomized_tranches_stay_within_the_spread_and_sum_to_the_parent() -> None:
    sizes = _tranches(RANDOM)
    assert sum(sizes) == D("1000")
    assert all(size == size.to_integral_value() for size in sizes)
    assert all(D("7") <= size <= D("13") for size in sizes[:-1])
    assert D("1") <= sizes[-1] <= D("13")
    assert len(set(sizes[:-1])) > 1


def test_a_seed_reproduces_its_tranches_and_another_seed_does_not() -> None:
    assert _tranches(RANDOM) == _tranches(RANDOM)
    other = Iceberg(D("10"), TrancheRandomization(D("3"), 8))
    assert _tranches(other) != _tranches(RANDOM)


def test_two_parents_worked_with_one_seed_draw_differently() -> None:
    second = "1f1e2d3c-4b5a-4968-8776-655443322110"
    assert _tranches(RANDOM, order_id=second) != _tranches(RANDOM)


def test_the_draws_are_uniform_over_the_whole_increments_of_the_spread() -> None:
    sizes = _tranches(Iceberg(D("10"), TrancheRandomization(D("2"), 11)), quantity="50000")
    counts = Counter(sizes[:-1])
    assert set(counts) == {D(n) for n in range(8, 13)}
    expected = (len(sizes) - 1) / 5
    assert all(abs(count - expected) < 0.15 * expected for count in counts.values())


def test_the_randomization_is_part_of_the_configurations_identity() -> None:
    plain = algorithm_configuration_id(Iceberg(D("10")))
    assert algorithm_configuration_id(RANDOM) != plain
    assert algorithm_configuration_id(Iceberg(D("10"), TrancheRandomization(D("3"), 8))) != (
        algorithm_configuration_id(RANDOM)
    )
    # An iceberg without randomization keeps the identity it had before v3.13.
    assert plain == algorithm_configuration_id(Iceberg(D("10.0")))


def test_a_randomization_that_could_draw_nothing_or_randomizes_nothing_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="tranche of nothing"):
        Iceberg(D("3"), TrancheRandomization(D("3"), 1))
    with pytest.raises(ExecutionValidationError, match="randomizes nothing"):
        TrancheRandomization(D("0"), 1)
    for seed in (-1, True):
        with pytest.raises(ExecutionValidationError, match="seed"):
            TrancheRandomization(D("1"), seed)
    with pytest.raises(ExecutionValidationError, match="spread"):
        start_algorithm(_parent("100"), Iceberg(D("10"), TrancheRandomization(D("2.5"), 1)), TERMS)
