"""Fama-MacBeth premiums and principal-component factors are pinned, as every host computes them.

Both are class 2 (WS-25): least squares by Householder QR, means by ``fsum``,
Newey and West's standard error, and M2's symmetric eigensystem are correctly
rounded operations in a fixed order. So the same inputs give the same bits on
every IEEE-754 host. The inputs here are built by integer arithmetic and exact
scaling, never by a seeded ``gauss``, whose logarithm and cosine are the
host's.

Each value below was computed identically under CPython 3.12, 3.13 and 3.14
before it was pinned. A change here is a change to an estimator -- never a
host's rounding -- and needs its own record.
"""

from __future__ import annotations

from collections.abc import Callable

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.analytics.statistical_factors import principal_component_factors
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    ForwardReturnPanel,
)
from alphalab.factor_library.fama_macbeth import fama_macbeth

START = 1_735_689_600.0
DAY = 86400.0
NAMES = [f"S{i:02d}" for i in range(12)]


def _draws(seed: int) -> Callable[[int], int]:
    state = seed

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    return draw


def _panel(rows: dict[float, dict[str, float]], name: str) -> FeaturePanel:
    definition = FeatureDefinition(name, FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=1)
    return FeaturePanel(
        definition=definition,
        dataset_version="ds@v1",
        timezone_name="UTC",
        rows=rows,
        symbols=tuple(NAMES),
    )


def test_fama_macbeths_premiums_errors_and_identities_are_pinned() -> None:
    draw = _draws(2511)
    first: dict[float, dict[str, float]] = {}
    second: dict[float, dict[str, float]] = {}
    returns: dict[float, dict[str, float]] = {}
    for t in range(40):
        stamp = START + t * DAY
        first[stamp] = {n: (draw(64) - 32) / 16 for n in NAMES}
        second[stamp] = {n: (draw(64) - 32) / 16 for n in NAMES}
        returns[stamp] = {
            n: (draw(512) - 256) / 4096 + first[stamp][n] / 64 - second[stamp][n] / 128
            for n in NAMES
        }
    forward = ForwardReturnPanel(
        horizon=1,
        rows=returns,
        dataset_version="ds@v1",
        timezone_name="UTC",
        symbols=tuple(NAMES),
        unrealized_instants=0,
        lag=1,
        delisted_instants=0,
        unenterable_instants=0,
        delistings=(),
    )
    result = fama_macbeth(
        [_panel(first, "momentum"), _panel(second, "value")],
        forward,
        intercept=True,
        minimum_assets=5,
        newey_west_lag=0,
        maximum_condition=1e8,
    )

    assert {k: v.hex() for k, v in result.estimate.parameters.items()} == {
        "intercept": "0x1.b3f748a9ae0e3p-9",
        "momentum": "0x1.f88c6678f4a6bp-7",
        "value": "-0x1.665941ff9d5a6p-7",
    }
    assert {k: v.hex() for k, v in result.estimate.standard_errors.items()} == {
        "intercept": "0x1.d87d5eccb1a0dp-10",
        "momentum": "0x1.df5da5725fde4p-10",
        "value": "0x1.f07257325c00ap-10",
    }
    assert [result.estimate.estimate_id, result.estimate.data_id] == [
        "cd1f3f2401477c68f6abb63298750c9e8ae28fdf4fc48f50e2feb5fc7c8cd62b",
        "c2eef605366f21bca9fac2a98008cdc2ac216031635c2c61281af08edacbe52a",
    ]


def test_principal_component_factors_and_their_identity_are_pinned() -> None:
    draw = _draws(77)
    series = {n: [(draw(1024) - 512) / 8192 for _ in range(60)] for n in NAMES[:6]}
    covariance = CovarianceMatrix.sample(series, currency="USD", period="1D", source="pinned")
    result = principal_component_factors(covariance, factors=2, tolerance=1e-15, max_iterations=60)

    assert [v.hex() for v in result.eigenvalues] == [
        "0x1.abe35cb75318ap-10",
        "0x1.97827cdb27885p-10",
        "0x1.900af5ccdc081p-10",
        "0x1.247fdf44a86f3p-10",
        "0x1.05b894c612c7dp-10",
        "0x1.75cdb2972d87cp-11",
    ]
    loadings = result.structure.loadings
    assert [loadings.loading("S00", f).hex() for f in loadings.factors] == [
        "0x1.a79239ccd51a8p-3",
        "0x1.13c2493d2c515p-2",
    ]
    assert result.structure.specific[0].hex() == "0x1.4faec5ee0392ap-11"
    assert (
        result.structure.covariance_id
        == "65e4edee5672a0b2c82fd510d0d6d5883866e1d823ccde3cd834fbf242270337"
    )
