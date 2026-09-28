"""A content identity renders every ``Decimal`` by value (ledger DET-006).

Until v3.10 most identities rendered a caller-declared ``Decimal`` with ``str``
or ``repr``, which keep the exponent: a weight declared ``0.5`` and one declared
``0.50`` -- equal amounts -- derived two identities. Each scheme that rendered a
``Decimal`` moved to version 2 in v3.11 and renders it through
:func:`~alphalab.common.arithmetic.canonical_text`. A different *value* still
derives a different identity.
"""

from decimal import Decimal

import pytest

from alphalab.allocation.capital import (
    CAPITAL_ALLOCATION_SCHEME,
    CAPITAL_PLAN_SCHEME,
    CapitalDimension,
    CapitalLimit,
    EqualWeights,
)
from alphalab.analytics.risk_budget import RISK_BUDGET_REPORT_SCHEME
from alphalab.broker.requests import VENUE_REQUEST_SCHEME, ModifyRequest
from alphalab.execution.algorithms import (
    ALGORITHM_CONFIGURATION_SCHEME,
    ALGORITHM_RUN_SCHEME,
    EXECUTION_SCHEDULE_SCHEME,
    TWAP,
    IncompletePolicy,
    Participation,
    Slicing,
    Urgency,
    algorithm_configuration_id,
)
from alphalab.execution.algorithms import Iceberg as IcebergAlgorithm
from alphalab.execution.quality import EXECUTION_QUALITY_SCHEME
from alphalab.execution.routing import (
    ROUTE_DECISION_SCHEME,
    ROUTING_POLICY_SCHEME,
    RoutingObjective,
    RoutingPolicy,
)
from alphalab.portfolio_optimizer.black_litterman import BLACK_LITTERMAN_SCHEME, EquilibriumPrior
from alphalab.portfolio_optimizer.construction import CONSTRUCTION_PROBLEM_SCHEME, RiskParity


def test_every_scheme_that_renders_a_decimal_moved_to_version_2() -> None:
    for scheme in (
        CAPITAL_PLAN_SCHEME,
        CAPITAL_ALLOCATION_SCHEME,
        RISK_BUDGET_REPORT_SCHEME,
        ALGORITHM_CONFIGURATION_SCHEME,
        ALGORITHM_RUN_SCHEME,
        EXECUTION_SCHEDULE_SCHEME,
        EXECUTION_QUALITY_SCHEME,
        ROUTING_POLICY_SCHEME,
        ROUTE_DECISION_SCHEME,
        BLACK_LITTERMAN_SCHEME,
        CONSTRUCTION_PROBLEM_SCHEME,
        VENUE_REQUEST_SCHEME,
    ):
        assert scheme.endswith(".v2"), scheme


def test_a_retried_amendment_is_the_same_request_however_it_is_spelled() -> None:
    """An idempotency key must not depend on how the caller spelled the amounts.

    Until v3.11 a retry of an amendment to ``100`` spelled ``100.0`` derived a
    new request id with the same revision, which the ledger refused as a reused
    revision with different content.
    """

    first = ModifyRequest("B-1", 1, Decimal("100"), Decimal("20.5"), 1.0)
    retry = ModifyRequest("B-1", 1, Decimal("100.0"), Decimal("20.50"), 2.0)
    assert first.request_id == retry.request_id
    assert (
        first.request_id != ModifyRequest("B-1", 1, Decimal("101"), Decimal("20.5"), 1.0).request_id
    )


@pytest.mark.parametrize(
    ("first", "second", "different"),
    [
        (
            TWAP(12, Urgency(Decimal("2"))),
            TWAP(12, Urgency(Decimal("2.000"))),
            TWAP(12, Urgency(Decimal("2.5"))),
        ),
        (
            Participation(Decimal("0.1"), Decimal("100"), None, IncompletePolicy.LEAVE_UNFILLED),
            Participation(Decimal("0.10"), Decimal("1E+2"), None, IncompletePolicy.LEAVE_UNFILLED),
            Participation(Decimal("0.2"), Decimal("100"), None, IncompletePolicy.LEAVE_UNFILLED),
        ),
        (Slicing(Decimal("5"), None), Slicing(Decimal("5.00"), None), Slicing(Decimal("6"), None)),
        (
            IcebergAlgorithm(Decimal("10")),
            IcebergAlgorithm(Decimal("10.0")),
            IcebergAlgorithm(Decimal("11")),
        ),
    ],
)
def test_an_algorithm_configuration_is_identified_by_value(
    first: object, second: object, different: object
) -> None:
    assert algorithm_configuration_id(first) == algorithm_configuration_id(second)  # type: ignore[arg-type]
    assert algorithm_configuration_id(first) != algorithm_configuration_id(different)  # type: ignore[arg-type]


def _policy(cap: Decimal | None) -> RoutingPolicy:
    return RoutingPolicy(RoutingObjective.BEST_QUOTED_PRICE, 5.0, cap, True, False, frozenset())


def test_a_routing_policy_is_identified_by_value() -> None:
    assert _policy(Decimal("0.5")).policy_id == _policy(Decimal("0.50")).policy_id
    assert _policy(Decimal("0.5")).policy_id != _policy(Decimal("0.6")).policy_id
    assert _policy(None).policy_id != _policy(Decimal("0")).policy_id


def test_capital_rules_and_limits_render_by_value() -> None:
    assert EqualWeights(Decimal("0.5")).rendering() == EqualWeights(Decimal("0.500")).rendering()
    limit = CapitalLimit(CapitalDimension.STRATEGY, "alpha", Decimal("0.25"), None)
    same = CapitalLimit(CapitalDimension.STRATEGY, "alpha", Decimal("0.2500"), None)
    assert limit.rendering() == same.rendering()
    assert "min=None" in limit.rendering()


def test_construction_inputs_render_by_value() -> None:
    budgets = {"A": Decimal("0.5"), "B": Decimal("0.5")}
    spelled = {"A": Decimal("0.50"), "B": Decimal("0.500")}
    assert RiskParity(budgets).rendering() == RiskParity(spelled).rendering()
    prior = EquilibriumPrior({"A": Decimal("100"), "B": Decimal("300")}, "USD", 2.5)
    respelled = EquilibriumPrior({"A": Decimal("1E+2"), "B": Decimal("300.00")}, "USD", 2.5)
    assert prior.rendering() == respelled.rendering()
