"""Construction over a covariance stated by its factor structure (v3.13, ledger PRF-013).

v3.12's factor-structured solver costs ``O(n k^2)`` per step, but a public
caller reached it only through a dense matrix -- ``O(n^2)`` to write out before
the first step. A :class:`ConstructionProblem` now takes the
:class:`FactorStructure` itself; the dense values are written out only for a
method that needs them, and the weights are those of the same problem over the
dense matrix. A problem over a matrix keeps the identity v3.12 gave it.
"""

from __future__ import annotations

import random

import pytest

from alphalab.analytics import CovarianceMatrix, FactorLoadings, FactorStructure
from alphalab.portfolio_optimizer.construction import (
    FACTOR_STRUCTURED_MINIMUM_ASSETS,
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    ExpectedReturns,
    ExposureRange,
    MeanVariance,
    MinimumVariance,
    RiskParity,
    SolverSettings,
    WeightBounds,
    construct,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError

SETTINGS = SolverSettings(1e-9, 1e-9, 10_000)


def _model(count: int, seed: int) -> tuple[FactorStructure, ExpectedReturns]:
    rng = random.Random(seed)
    assets = [f"S{i:04d}" for i in range(count)]
    factors = ["MKT", "SIZE", "VALUE"]
    loadings = FactorLoadings.of(
        {asset: {factor: round(rng.gauss(0.0, 1.0), 6) for factor in factors} for asset in assets},
        source="probe",
        lineage=dict.fromkeys(factors, "probe"),
        as_of=None,
    )
    factor_covariance = CovarianceMatrix.from_rows(
        factors,
        [[0.04, 0.002, -0.001], [0.002, 0.01, 0.0005], [-0.001, 0.0005, 0.0225]],
        currency="USD",
        period="1M",
        source="probe",
        observations=None,
    )
    specific = {asset: round(0.01 + 0.02 * rng.random(), 6) for asset in assets}
    expected = ExpectedReturns(
        {asset: round(rng.gauss(0.01, 0.02), 6) for asset in assets}, "USD", "1M", "probe"
    )
    return FactorStructure.of(loadings, factor_covariance, specific), expected


def _never_written_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: FactorStructure) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("the structure was written out")

    monkeypatch.setattr(FactorStructure, "_write_out", refuse)


def _long_only(cap: float, **extra: float) -> ConstraintSet:
    return ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(0.0, cap), **extra)


def test_a_matrix_problem_keeps_the_identity_v3_12_gave_it() -> None:
    """Identities and a figure v3.12.0 computed for these problems, dense and structured."""

    for count, cap, problem_id, result_id, volatility in (
        (
            6,
            0.3,
            "37f6d1ba6313c280d7a5373555505a8a29af8bb1e4ef98455cc3cc2f0e245adc",
            "183a1960314db9b4e4ecdcc94c91fcba10edd7792b9e863804072649e458d410",
            0.09927127580315924,
        ),
        (
            120,
            0.05,
            "31f8edd0931299bf6acfc855a81b86ad299632efaec38d681c35a78b6e4ad5f1",
            "8dedf8dc6f29566365c0b8cee02130f46f7d747574e2acd23328adb765b4b639",
            0.030376467962661264,
        ),
    ):
        structure, expected = _model(count, 11)
        problem = ConstructionProblem(
            structure.matrix(), MeanVariance(expected, 2.0), _long_only(cap), SETTINGS
        )
        result = construct(problem)
        assert (problem.problem_id, result.result_id) == (problem_id, result_id)
        assert result.diagnostics.volatility == volatility


@pytest.mark.parametrize("seed", range(4))
def test_mean_variance_over_a_structure_never_writes_it_out(
    seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    structure, expected = _model(150, seed)
    dense = ConstructionProblem(
        structure.matrix(), MeanVariance(expected, 2.0), _long_only(0.05), SETTINGS
    )
    reference = construct(dense)

    structure, expected = _model(150, seed)  # a fresh structure, nothing written out
    _never_written_out(monkeypatch)
    problem = ConstructionProblem(
        structure, MeanVariance(expected, 2.0), _long_only(0.05), SETTINGS
    )
    result = construct(problem)

    assert result.status is reference.status is ConstructionStatus.OPTIMAL
    assert result.weights == reference.weights
    assert problem.problem_id != dense.problem_id
    assert result.problem_id == problem.problem_id
    ours, theirs = result.diagnostics, reference.diagnostics
    assert ours.binding == theirs.binding
    assert ours.volatility == pytest.approx(theirs.volatility, rel=1e-12)
    assert ours.diversification_ratio == pytest.approx(theirs.diversification_ratio, rel=1e-12)
    assert ours.risk is not None and theirs.risk is not None
    assert ours.risk.covariance_id == structure.covariance_id
    for asset, total in theirs.risk.total.items():
        assert ours.risk.total[asset] == pytest.approx(total, abs=1e-13)


def test_a_capped_minimum_variance_over_a_structure_never_writes_it_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _never_written_out(monkeypatch)
    structure, _ = _model(200, 21)
    loose = construct(ConstructionProblem(structure, MinimumVariance(), _long_only(0.05), SETTINGS))
    assert loose.status is ConstructionStatus.OPTIMAL
    assert loose.diagnostics.volatility is not None

    impossible = construct(
        ConstructionProblem(
            structure,
            MinimumVariance(),
            _long_only(0.05, max_volatility=loose.diagnostics.volatility / 2),
            SETTINGS,
        )
    )
    assert impossible.status is ConstructionStatus.INFEASIBLE


def test_a_method_that_needs_the_dense_values_is_solved_over_the_matrix() -> None:
    """Risk parity is not a quadratic program; the structure writes its matrix out for it."""

    structure, _ = _model(30, 4)
    budgets = None
    over_structure = construct(
        ConstructionProblem(structure, RiskParity(budgets), _long_only(1.0), SETTINGS)
    )
    over_matrix = construct(
        ConstructionProblem(structure.matrix(), RiskParity(budgets), _long_only(1.0), SETTINGS)
    )
    assert over_structure.status is ConstructionStatus.OPTIMAL
    assert over_structure.weights == over_matrix.weights


def test_a_small_universe_over_a_structure_is_solved_densely() -> None:
    count = FACTOR_STRUCTURED_MINIMUM_ASSETS // 4
    structure, expected = _model(count, 8)
    over_structure = construct(
        ConstructionProblem(structure, MeanVariance(expected, 3.0), _long_only(0.2), SETTINGS)
    )
    over_matrix = construct(
        ConstructionProblem(
            structure.matrix(), MeanVariance(expected, 3.0), _long_only(0.2), SETTINGS
        )
    )
    assert over_structure.status is ConstructionStatus.OPTIMAL
    assert over_structure.weights == over_matrix.weights


def test_a_structure_is_held_to_the_units_of_what_it_is_paired_with() -> None:
    structure, expected = _model(130, 2)
    in_euros = ExpectedReturns(dict(expected.values), "EUR", "1M", "probe")
    with pytest.raises(ConstructionInputError, match="refused as a pair"):
        construct(
            ConstructionProblem(structure, MeanVariance(in_euros, 2.0), _long_only(0.05), SETTINGS)
        )


def test_a_thousand_assets_through_the_public_path_write_nothing_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scale the structured solver was built for, reached without the dense matrix."""

    _never_written_out(monkeypatch)
    structure, expected = _model(1_000, 13)
    result = construct(
        ConstructionProblem(
            structure,
            MeanVariance(expected, 2.0),
            _long_only(0.01),
            SolverSettings(1e-9, 1e-9, 100_000),
        )
    )
    assert result.status is ConstructionStatus.OPTIMAL
    assert result.weights is not None
    assert abs(sum(result.weights.values()) - 1.0) <= 1e-9
