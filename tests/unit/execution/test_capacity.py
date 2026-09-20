"""Capacity: the binding constraint, and how it moves with each assumption."""

from decimal import Decimal

import pytest

from alphalab.execution.capacity import (
    AssetLiquidity,
    CapacityConstraint,
    CapacityModel,
    capacity_curve,
)
from alphalab.execution.costs import LinearImpact, NoImpact, SquareRootImpact
from alphalab.execution.exceptions import ExecutionValidationError

CEILING = Decimal("1e12")

LIQUID = AssetLiquidity("LIQUID", Decimal("50"), Decimal("10000000"), Decimal("0.5"), "USD", "SIM")
THIN = AssetLiquidity("THIN", Decimal("20"), Decimal("100000"), Decimal("0.5"), "USD", "SIM")
UNIVERSE = (LIQUID, THIN)


def model(
    participation: str = "0.10",
    turnover: str = "0.20",
    impact: object = None,
    budget: str | None = None,
) -> CapacityModel:
    return CapacityModel(
        participation_limit=Decimal(participation),
        turnover=Decimal(turnover),
        impact_model=NoImpact() if impact is None else impact,  # type: ignore[arg-type]
        impact_budget=None if budget is None else Decimal(budget),
        search_ceiling=CEILING,
    )


# --------------------------------------------------------------------------- #
# The closed form
# --------------------------------------------------------------------------- #


def test_participation_capacity_is_the_documented_closed_form() -> None:
    """``limit * adv * price / (weight * turnover)``."""

    built = model()

    assert built.participation_capacity(THIN) == (
        Decimal("0.10") * Decimal("100000") * Decimal("20") / (Decimal("0.5") * Decimal("0.20"))
    )


def test_the_thinnest_name_binds_the_whole_book() -> None:
    result = model().capacity(UNIVERSE, 0.0)

    assert result.binding_asset_id == "THIN"
    assert result.constraint is CapacityConstraint.PARTICIPATION
    assert result.capacity == Decimal("2000000")


def test_participation_at_capacity_is_exactly_the_limit_for_the_binding_name() -> None:
    built = model()
    result = built.capacity(UNIVERSE, 0.0)
    binding = next(item for item in result.per_asset if item.asset_id == "THIN")

    assert binding.participation_at_capacity == built.participation_limit


# --------------------------------------------------------------------------- #
# Sensitivity -- the four the brief asks to be visible
# --------------------------------------------------------------------------- #


def test_capacity_is_proportional_to_the_participation_limit() -> None:
    at_one = model(participation="0.01").capacity(UNIVERSE, 0.0).capacity
    at_ten = model(participation="0.10").capacity(UNIVERSE, 0.0).capacity

    assert at_ten == at_one * Decimal("10")


def test_capacity_is_inversely_proportional_to_turnover() -> None:
    slow = model(turnover="0.05").capacity(UNIVERSE, 0.0).capacity
    fast = model(turnover="1.00").capacity(UNIVERSE, 0.0).capacity

    assert slow == fast * Decimal("20")


def test_capacity_is_proportional_to_liquidity() -> None:
    deeper = AssetLiquidity("THIN", Decimal("20"), Decimal("200000"), Decimal("0.5"), "USD", "SIM")
    before = model().capacity(UNIVERSE, 0.0).capacity
    after = model().capacity((LIQUID, deeper), 0.0).capacity

    assert after == before * Decimal("2")


def test_a_tighter_impact_assumption_lowers_capacity() -> None:
    lenient = model(impact=SquareRootImpact(Decimal("0.01")), budget="0.002")
    severe = model(impact=SquareRootImpact(Decimal("0.20")), budget="0.002")

    assert severe.capacity(UNIVERSE, 0.0).capacity < lenient.capacity(UNIVERSE, 0.0).capacity


def test_a_smaller_position_raises_that_name_s_own_ceiling() -> None:
    lighter = AssetLiquidity("THIN", Decimal("20"), Decimal("100000"), Decimal("0.1"), "USD", "SIM")

    assert model().participation_capacity(lighter) == model().participation_capacity(THIN) * 5


# --------------------------------------------------------------------------- #
# The impact constraint
# --------------------------------------------------------------------------- #


def test_the_impact_budget_can_bind_before_participation_does() -> None:
    result = model(impact=SquareRootImpact(Decimal("0.05")), budget="0.002").capacity(UNIVERSE, 0.0)

    assert result.constraint is CapacityConstraint.IMPACT_BUDGET
    assert result.capacity < Decimal("2000000")


def test_the_impact_solution_matches_the_closed_form_of_a_linear_model() -> None:
    """Linear impact inverts exactly, so the bisection has a known answer.

    ``coefficient * p * price / price <= budget`` gives ``p <= budget /
    coefficient``, and participation ``p`` at capital ``C`` is
    ``C * weight * turnover / (price * adv)``.
    """

    coefficient, budget = Decimal("0.5"), Decimal("0.01")
    built = model(impact=LinearImpact(coefficient), budget=str(budget))
    expected = (
        (budget / coefficient)
        * THIN.price
        * THIN.average_daily_volume
        / (THIN.weight * built.turnover)
    )
    solved = built.impact_capacity(THIN, 0.0)

    assert solved is not None
    assert abs(solved - expected) / expected < Decimal("0.001")


def test_no_budget_means_the_impact_constraint_is_not_evaluated() -> None:
    result = model().capacity(UNIVERSE, 0.0)

    assert all(item.impact_capacity is None for item in result.per_asset)
    assert result.assumptions["impact_budget"] == "unevaluated"


def test_nothing_binding_below_the_ceiling_reports_unbound() -> None:
    deep = AssetLiquidity("DEEP", Decimal("50"), Decimal("1e15"), Decimal("1"), "USD", "SIM")
    result = model().capacity((deep,), 0.0)

    assert result.constraint is CapacityConstraint.UNBOUND
    assert result.binding_asset_id is None
    assert result.capacity == CEILING


# --------------------------------------------------------------------------- #
# Determinism and refusals
# --------------------------------------------------------------------------- #


def test_capacity_is_deterministic() -> None:
    built = model(impact=SquareRootImpact(Decimal("0.05")), budget="0.002")

    assert built.capacity(UNIVERSE, 0.0) == built.capacity(UNIVERSE, 0.0)


def test_capacity_does_not_depend_on_the_order_of_the_universe() -> None:
    built = model(impact=SquareRootImpact(Decimal("0.05")), budget="0.002")

    assert built.capacity(UNIVERSE, 0.0) == built.capacity(tuple(reversed(UNIVERSE)), 0.0)


def test_the_assumptions_travel_with_the_figure() -> None:
    result = model(impact=SquareRootImpact(Decimal("0.05")), budget="0.002").capacity(UNIVERSE, 0.0)

    assert result.assumptions["participation_limit"] == "0.10"
    assert result.assumptions["turnover"] == "0.20"
    assert result.assumptions["impact_model"] == "SquareRootImpact"
    assert result.assumptions["impact_budget"] == "0.002"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"participation_limit": Decimal("0")}, "must lie in"),
        ({"participation_limit": Decimal("1.5")}, "must lie in"),
        ({"turnover": Decimal("0")}, "trades nothing"),
        ({"impact_budget": Decimal("0")}, "admits no capital"),
        ({"search_ceiling": Decimal("0")}, "positive"),
    ],
)
def test_an_unusable_assumption_is_refused(kwargs: dict[str, Decimal], match: str) -> None:
    base = {
        "participation_limit": Decimal("0.1"),
        "turnover": Decimal("0.2"),
        "impact_model": NoImpact(),
        "impact_budget": None,
        "search_ceiling": CEILING,
    }
    base.update(kwargs)

    with pytest.raises(ExecutionValidationError, match=match):
        CapacityModel(**base)  # type: ignore[arg-type]


def test_a_name_that_trades_nothing_is_refused_rather_than_given_tiny_capacity() -> None:
    with pytest.raises(ExecutionValidationError, match="trades nothing"):
        AssetLiquidity("DEAD", Decimal("10"), Decimal("0"), Decimal("1"), "USD", "SIM")


def test_an_empty_universe_is_refused_rather_than_called_unlimited() -> None:
    with pytest.raises(ExecutionValidationError, match="empty universe"):
        model().capacity((), 0.0)


def test_a_duplicated_name_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="Duplicate assets"):
        model().capacity((THIN, THIN), 0.0)


# --------------------------------------------------------------------------- #
# The curve
# --------------------------------------------------------------------------- #


def test_the_curve_reports_the_worst_name_and_rises_with_capital() -> None:
    points = capacity_curve(
        model(impact=SquareRootImpact(Decimal("0.05")), budget="0.002"),
        UNIVERSE,
        [Decimal("1e6"), Decimal("1e7"), Decimal("1e8")],
        0.0,
    )
    participations = [participation for _, participation, _ in points]
    impacts = [impact for _, _, impact in points]

    assert participations == sorted(participations)
    assert impacts == sorted(impacts)


def test_the_curve_refuses_negative_capital() -> None:
    with pytest.raises(ExecutionValidationError, match="negative capital"):
        capacity_curve(model(), UNIVERSE, [Decimal("-1")], 0.0)
