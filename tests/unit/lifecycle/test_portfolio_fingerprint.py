"""A strategy researched as a constructed, funded portfolio names both in its fingerprint."""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.allocation.capital import (
    CapitalAccount,
    CapitalAllocationPlan,
    CapitalAllocationResult,
    CapitalPlacement,
    FixedAmounts,
    OversubscriptionRule,
    allocate_capital,
)
from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.lifecycle import (
    CAPITAL_SETTING_PREFIX,
    PORTFOLIO_SETTING_PREFIX,
    LifecycleInputError,
    StrategyFingerprint,
    canonical_fingerprint_key,
    research_configuration,
    research_configuration_with_portfolio,
    verify_fingerprint,
)
from alphalab.portfolio.fx import NO_RATES
from alphalab.portfolio_optimizer.construction import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionResult,
    ExposureRange,
    MinimumVariance,
    RiskParity,
    SolverSettings,
    WeightBounds,
    construct,
)
from tests.unit.lifecycle.evidence_harness import fingerprint

COVARIANCE = CovarianceMatrix.from_rows(
    ("A", "B"),
    ((0.04, 0.01), (0.01, 0.09)),
    currency="USD",
    period="1D",
    source="unit",
    observations=None,
)
SETTINGS = SolverSettings(1e-10, 1e-10, 200)
PARITY = construct(
    ConstructionProblem(
        COVARIANCE,
        RiskParity.equal(),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(None)),
        SETTINGS,
    )
)
MINIMUM = construct(
    ConstructionProblem(
        COVARIANCE,
        MinimumVariance(),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(None)),
        SETTINGS,
    )
)
INFEASIBLE = construct(
    ConstructionProblem(
        COVARIANCE,
        MinimumVariance(),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(0.4)),
        SETTINGS,
    )
)
PLACEMENT = CapitalPlacement("MOM", "US_EQUITIES", "ACC")


def capital(amount: str) -> CapitalAllocationResult:
    return allocate_capital(
        CapitalAllocationPlan(
            "plan",
            "USD",
            0.0,
            (CapitalAccount("ACC", "BROKER", "USD", Decimal("1000"), Decimal("0")),),
            (PLACEMENT,),
            FixedAmounts({PLACEMENT: Decimal(amount)}),
            (),
            OversubscriptionRule.REFUSE,
            Decimal("1"),
        ),
        NO_RATES,
    )


def test_each_component_writes_its_identities_under_its_prefix() -> None:
    funded = capital("800")
    research = research_configuration_with_portfolio(
        {"validation": "walk-forward"},
        constructions={"core": PARITY},
        capital={"q4": funded},
        study=None,
    )

    assert research.settings == {
        "validation": "walk-forward",
        f"{PORTFOLIO_SETTING_PREFIX}core.problem": PARITY.problem_id,
        f"{PORTFOLIO_SETTING_PREFIX}core.result": PARITY.result_id,
        f"{CAPITAL_SETTING_PREFIX}q4.plan": funded.plan_id,
        f"{CAPITAL_SETTING_PREFIX}q4.allocation": funded.result_id,
    }


def test_a_different_construction_or_allocation_is_a_different_strategy() -> None:
    def fingerprint_with(
        construction: ConstructionResult, allocation: CapitalAllocationResult
    ) -> StrategyFingerprint:
        return fingerprint(
            research=research_configuration_with_portfolio(
                {}, constructions={"core": construction}, capital={"q4": allocation}, study=None
            )
        )

    base = fingerprint_with(PARITY, capital("800"))

    assert verify_fingerprint(base)
    assert fingerprint_with(PARITY, capital("800")).fingerprint == base.fingerprint
    assert fingerprint_with(MINIMUM, capital("800")).fingerprint != base.fingerprint
    assert fingerprint_with(PARITY, capital("700")).fingerprint != base.fingerprint


def test_the_fingerprint_key_itself_is_unchanged() -> None:
    fp = fingerprint(
        research=research_configuration_with_portfolio(
            {}, constructions={"core": PARITY}, capital={}, study=None
        )
    )
    key = canonical_fingerprint_key(
        fp.name, fp.strategy_id, fp.code, fp.dependencies, fp.parameters, fp.research, fp.engine
    )

    assert [line for line in key.splitlines() if "=" not in line] == [
        "alphalab.strategy_fingerprint.v1",
        "code",
        "dependencies",
        "parameters",
        "research",
        "engine",
    ]
    assert fingerprint(research=research_configuration({"a": "b"})).research.settings == {"a": "b"}


def test_an_infeasible_construction_or_a_refused_plan_identifies_nothing() -> None:
    with pytest.raises(LifecycleInputError, match="did not succeed"):
        research_configuration_with_portfolio(
            {}, constructions={"core": INFEASIBLE}, capital={}, study=None
        )
    with pytest.raises(LifecycleInputError, match="was refused"):
        research_configuration_with_portfolio(
            {}, constructions={}, capital={"q4": capital("5000")}, study=None
        )


def test_nothing_named_bad_names_and_collisions_are_refused() -> None:
    with pytest.raises(LifecycleInputError, match="names no construction"):
        research_configuration_with_portfolio({}, constructions={}, capital={}, study=None)
    with pytest.raises(LifecycleInputError, match="not an identifier"):
        research_configuration_with_portfolio(
            {}, constructions={"core.v2": PARITY}, capital={}, study=None
        )
    with pytest.raises(LifecycleInputError, match="also supplied by the caller"):
        research_configuration_with_portfolio(
            {f"{PORTFOLIO_SETTING_PREFIX}core.result": "mine"},
            constructions={"core": PARITY},
            capital={},
            study=None,
        )
