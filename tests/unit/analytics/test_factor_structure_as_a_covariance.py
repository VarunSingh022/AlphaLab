"""A covariance stated by its factor model, never written out (v3.13, ledger PRF-013).

v3.12 solved construction over a factor model in ``O(n k^2)`` per step, but the
public path reached that solver only through :meth:`CovarianceMatrix.factor_model`,
which writes ``n^2`` values out and renders each into the matrix's identity --
29 s and 1.3 GB at 4,000 assets. Since v3.13 the :class:`FactorStructure` is a
covariance in its own right: built by :meth:`FactorStructure.of` in
``O(n k^2)``, decomposed by :func:`euler_decomposition` and :func:`factor_risk`
through its factors, and written out only when something asks for the dense
values.

Every figure is checked against the dense matrix the structure implies, and the
dense path is checked against identities v3.12.0 computed.
"""

from __future__ import annotations

import math
import random

import pytest

from alphalab.analytics import (
    CovarianceMatrix,
    FactorLoadings,
    FactorStructure,
    euler_decomposition,
    factor_risk,
)
from alphalab.analytics.exceptions import AnalyticsValidationError


def _inputs(
    count: int, seed: int
) -> tuple[FactorLoadings, CovarianceMatrix, dict[str, float], random.Random]:
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
    return loadings, factor_covariance, specific, rng


def _never_written_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: FactorStructure) -> tuple[tuple[float, ...], ...]:
        raise AssertionError("the structure was written out")

    monkeypatch.setattr(FactorStructure, "_write_out", refuse)


def test_the_dense_path_keeps_the_identity_v3_12_gave_it() -> None:
    """Identities v3.12.0 computed for these inputs (scratch probe on the released tree)."""

    loadings, factor_covariance, specific, _ = _inputs(6, 11)
    assert (
        CovarianceMatrix.factor_model(loadings, factor_covariance, specific).covariance_id
        == "d698308d30bc8229d31a0c1a260ff1f244ade4dc0770c7d3ff47969bc30605a3"
    )
    loadings, factor_covariance, specific, _ = _inputs(120, 11)
    assert (
        CovarianceMatrix.factor_model(loadings, factor_covariance, specific).covariance_id
        == "90b9f7b120b465210378128257d3d6a173edb40df158dff11eeba98c9ab12de2"
    )


def test_a_structure_is_its_matrix_written_out() -> None:
    loadings, factor_covariance, specific, _ = _inputs(40, 3)
    dense = CovarianceMatrix.factor_model(loadings, factor_covariance, specific)
    structure = FactorStructure.of(loadings, factor_covariance, specific)

    matrix = structure.matrix()
    assert matrix == dense
    assert matrix.covariance_id == dense.covariance_id
    assert matrix.values == dense.values
    assert matrix.factors is structure
    assert structure.matrix() == matrix, "written out again, the same"
    assert structure == dense.factors

    assert structure.assets == dense.assets
    assert (structure.currency, structure.period) == (dense.currency, dense.period)
    assert structure.source == dense.source
    for asset in structure.assets:
        assert structure.variance(asset) == dense.variance(asset)
        assert structure.volatility(asset) == dense.volatility(asset)
        assert structure.index(asset) == dense.index(asset)


def test_building_and_reading_a_structure_writes_nothing_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _never_written_out(monkeypatch)
    loadings, factor_covariance, specific, rng = _inputs(300, 5)

    structure = FactorStructure.of(loadings, factor_covariance, specific)
    weights = {asset: rng.uniform(-0.01, 0.02) for asset in structure.assets}
    assert structure.definiteness() is not None
    assert euler_decomposition(weights, structure).volatility > 0.0
    assert factor_risk(weights, structure).volatility > 0.0
    assert structure.volatility("S0007") > 0.0
    assert len(structure.covariance_id) == 64


@pytest.mark.parametrize("seed", range(8))
def test_the_euler_decomposition_through_the_factors_is_the_dense_one(seed: int) -> None:
    loadings, factor_covariance, specific, rng = _inputs(25, seed)
    structure = FactorStructure.of(loadings, factor_covariance, specific)
    dense = structure.matrix()
    # A book that holds some assets, shorts others and leaves the rest out.
    weights = {asset: rng.uniform(-0.3, 0.6) for asset in structure.assets if rng.random() < 0.7}

    through = euler_decomposition(weights, structure)
    reference = euler_decomposition(weights, dense)

    assert through.covariance_id == structure.covariance_id != reference.covariance_id
    assert through.weights == reference.weights
    assert through.variance == pytest.approx(reference.variance, rel=1e-12)
    assert through.volatility == pytest.approx(reference.volatility, rel=1e-12)
    for asset in weights:
        scale = reference.volatility
        assert through.marginal[asset] == pytest.approx(reference.marginal[asset], abs=1e-12)
        assert through.total[asset] == pytest.approx(reference.total[asset], abs=1e-12 * scale)
        assert through.relative[asset] == pytest.approx(reference.relative[asset], abs=1e-12)
    assert math.fsum(through.total.values()) == pytest.approx(through.volatility, rel=1e-13)


def test_factor_risk_reads_a_structure_as_it_reads_the_matrix() -> None:
    loadings, factor_covariance, specific, rng = _inputs(30, 9)
    structure = FactorStructure.of(loadings, factor_covariance, specific)
    weights = {asset: rng.uniform(0.0, 0.05) for asset in structure.assets}

    through = factor_risk(weights, structure)
    reference = factor_risk(weights, structure.matrix())

    assert through.covariance_id == structure.covariance_id
    assert reference.covariance_id == structure.matrix().covariance_id
    assert (through.volatility, through.factor_total, through.specific_total) == (
        reference.volatility,
        reference.factor_total,
        reference.specific_total,
    )
    assert through.factors == reference.factors
    assert through.specific == reference.specific


def test_a_structure_has_an_identity_of_its_own() -> None:
    loadings, factor_covariance, specific, _ = _inputs(12, 2)
    structure = FactorStructure.of(loadings, factor_covariance, specific)
    again = FactorStructure.of(loadings, factor_covariance, dict(reversed(specific.items())))

    assert structure.covariance_id == again.covariance_id
    assert structure.covariance_id != structure.matrix().covariance_id

    moved = dict(specific, S0003=specific["S0003"] + 1e-9)
    assert FactorStructure.of(loadings, factor_covariance, moved).covariance_id != (
        structure.covariance_id
    )
    other_period = CovarianceMatrix.from_rows(
        factor_covariance.assets,
        factor_covariance.values,
        currency="USD",
        period="1W",
        source="probe",
        observations=None,
    )
    assert FactorStructure.of(loadings, other_period, specific).covariance_id != (
        structure.covariance_id
    )


def test_what_a_structure_cannot_state_is_refused() -> None:
    loadings, factor_covariance, specific, _ = _inputs(5, 1)
    with pytest.raises(AnalyticsValidationError, match="cover exactly the loadings' assets"):
        FactorStructure.of(loadings, factor_covariance, dict(list(specific.items())[:4]))
    with pytest.raises(AnalyticsValidationError, match="not negative"):
        FactorStructure.of(loadings, factor_covariance, dict(specific, S0001=-1e-4))
    with pytest.raises(AnalyticsValidationError, match="specific variance"):
        FactorStructure.of(loadings, factor_covariance, dict(specific, S0001=math.inf))
    two = CovarianceMatrix.from_rows(
        ("MKT", "SIZE"),
        [[0.04, 0.0], [0.0, 0.01]],
        currency="USD",
        period="1M",
        source="probe",
        observations=None,
    )
    with pytest.raises(AnalyticsValidationError, match="a factor model needs the same"):
        FactorStructure.of(loadings, two, specific)

    structure = FactorStructure.of(loadings, factor_covariance, specific)
    with pytest.raises(AnalyticsValidationError, match="not covered by factor structure"):
        structure.variance("ELSEWHERE")
    with pytest.raises(AnalyticsValidationError, match="not covered by factor structure"):
        euler_decomposition({"ELSEWHERE": 1.0}, structure)
