"""Econometric statistics and identities are pinned, as every host computes them (ADR-0074).

A unit-root or Engle-Granger statistic is class 2: least squares by Householder
QR, sums by ``fsum``, square roots and divisions, each correctly rounded, in a
fixed order. Each estimate's identity is SHA-256 over its canonical text. So the
same series give the same bits on every IEEE-754 host. The series here are
built by integer arithmetic and correctly rounded operations, never by a
seeded ``gauss``, whose logarithm and cosine are the host's.

Each value below was computed identically under CPython 3.12, 3.13 and 3.14
before it was pinned, and hosted CI holds it on Linux and macOS. MacKinnon's
p-values pass through the normal distribution, class 3: they are held by a
tolerance, beside the identities and never in them. A change here is a change
to an estimator -- never a host's rounding -- and needs its own record.
"""

from __future__ import annotations

import math

import pytest

from alphalab.econometrics import (
    Deterministic,
    FixedLags,
    JohansenCase,
    LagCriterion,
    PBound,
    SchwertBandwidth,
    SelectedLags,
    VolatilityModel,
    VolatilityStart,
    augmented_dickey_fuller,
    fit_ornstein_uhlenbeck,
    fit_var,
    fit_vecm,
    fit_volatility,
    johansen,
    kpss,
    phillips_perron,
)
from alphalab.econometrics.cointegration import engle_granger

CONDITION = 1e10


def _pair(count: int) -> tuple[list[float], list[float]]:
    """``y = 2 + 1.5 x + u``: ``x`` a walk in steps of eighths, ``u`` halving toward zero."""

    state = 20261116

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    x, u = [0.0], [0.0]
    for _ in range(count - 1):
        x.append(x[-1] + (draw(17) - 8) / 8)
        u.append(u[-1] / 2 + (draw(13) - 6) / 4)
    return [2.0 + 1.5 * a + b for a, b in zip(x, u, strict=True)], x


Y, X = _pair(120)


def test_the_unit_root_statistics_and_their_regressions_are_pinned() -> None:
    adf = augmented_dickey_fuller(
        X,
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.BIC, 4, None),
        maximum_condition=CONDITION,
    )
    pp = phillips_perron(
        X,
        deterministic=Deterministic.CONSTANT_TREND,
        bandwidth=SchwertBandwidth(4),
        maximum_condition=CONDITION,
    )
    stationarity = kpss(
        X,
        deterministic=Deterministic.CONSTANT,
        bandwidth=SchwertBandwidth(4),
        maximum_condition=CONDITION,
    )

    assert (adf.statistic.hex(), adf.lags) == ("-0x1.4115b21fe3c81p+0", 0)
    assert adf.regression.estimate_id == (
        "a2289aa325196fe5aecaa87d1a16f48ead063f674303fbf33abcd86b77152ce4"
    )
    assert (pp.statistic.hex(), pp.lags) == ("-0x1.5a8b128a13381p+0", 4)
    assert pp.regression.estimate_id == (
        "bf944b0d80624059461a8955d204297125253ab7e84b9052f6358bbfcc91b05e"
    )
    assert (stationarity.statistic.hex(), stationarity.lags) == ("0x1.029a8251e7942p-1", 4)
    assert stationarity.regression.estimate_id == (
        "c436e94e7c66095145d6700f7ab4f5cd023ed72724be06218a57cfa0319c7259"
    )
    # KPSS's p-value is interpolated in its table: class 2, pinned.
    assert stationarity.p_value.value.hex() == "0x1.4bf37fb640f12p-5"
    # MacKinnon's go through the normal distribution: class 3, held by tolerance.
    assert adf.p_value.value == pytest.approx(float.fromhex("0x1.4cb7de50e3a20p-1"), rel=1e-12)
    assert pp.p_value.value == pytest.approx(float.fromhex("0x1.bf7d1aa4e1e55p-1"), rel=1e-12)


def test_engle_grangers_statistic_hedge_ratio_and_identities_are_pinned() -> None:
    test = engle_granger(
        Y,
        [X],
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.AIC, 4, None),
        maximum_condition=CONDITION,
    )

    assert (test.statistic.hex(), test.lags, test.observations) == (
        "-0x1.0bf718f3962b5p+2",
        2,
        117,
    )
    assert test.cointegrating.parameters["beta_1"].hex() == "0x1.91ec8c26f01b6p+0"
    assert test.cointegrating.parameters["constant"].hex() == "0x1.09d384ad94007p+1"
    assert test.cointegrating.estimate_id == (
        "6a876d087f93b82db93c3babe0d934087463169c1f676606f00a0fa2c2287228"
    )
    assert test.residual_regression.estimate_id == (
        "ee3fe233d32240002a130373242ba955c43aae9873ca70ad3841ae7973a95cca"
    )
    assert {level: value.hex() for level, value in test.critical_values.values.items()} == {
        0.01: "-0x1.feefc02e0391bp+1",
        0.05: "-0x1.b1c5e2db51f1ep+1",
        0.1: "-0x1.8a5adfe4fde8dp+1",
    }
    assert test.cointegrating.numerical_class.identity_bearing
    assert test.residual_regression.numerical_class.identity_bearing
    assert test.p_value is not None
    assert test.p_value.value == pytest.approx(float.fromhex("0x1.f45937b528c07p-9"), rel=1e-12)


def test_johansens_eigenvalues_statistics_and_identity_are_pinned() -> None:
    """Every Johansen value is class 2: the logarithms are Decimal's, correctly rounded."""

    test = johansen(
        [Y, X],
        case=JohansenCase.RESTRICTED_CONSTANT,
        lags=FixedLags(1),
        tolerance=1e-15,
        max_iterations=60,
        maximum_condition=CONDITION,
    )

    assert test.observations == 118
    pinned = [
        ("0x1.e105b77a6d0b3p-3", "0x1.07f2511fada55p+5", "0x1.f972272674005p+4"),
        ("0x1.8348fa144fff9p-7", "0x1.6727b18e74a46p+0", "0x1.6727b18e74a46p+0"),
    ]
    for rank, (eigenvalue, trace, maximum) in zip(test.ranks, pinned, strict=True):
        assert rank.eigenvalue.hex() == eigenvalue
        assert rank.trace.hex() == trace
        assert rank.maximum_eigenvalue.hex() == maximum
    # The pair is cointegrated: rank 0 beyond the table, rank 1 short of it.
    assert (test.ranks[0].trace_p_value.value, test.ranks[0].trace_p_value.bound) == (
        0.01,
        PBound.AT_MOST,
    )
    assert (test.ranks[1].trace_p_value.value, test.ranks[1].trace_p_value.bound) == (
        0.20,
        PBound.AT_LEAST,
    )
    assert test.estimate.estimate_id == (
        "ca7f6eac44980d420fd296fc1ad4dbb20d8b77487422b82176a7c7ca1bdc59de"
    )
    assert test.estimate.convergence is not None
    assert test.estimate.convergence.reached.hex() == "0x1.036e7b232d853p-65"


def test_the_ornstein_uhlenbeck_fit_and_its_half_life_are_pinned() -> None:
    """The pair's spread, ``y - 1.5 x``, reverts as it was built to: every value class 2."""

    spread = [y - 1.5 * x for y, x in zip(Y, X, strict=True)]
    fit = fit_ornstein_uhlenbeck(spread, interval=1.0 / 252, maximum_condition=CONDITION)

    pinned = {
        "intercept": ("0x1.fa3da61ddf77dp-1", "0x1.770b9ff553ab6p-3"),
        "coefficient": ("0x1.0a3a2bbed7b82p-1", "0x1.46658963eee03p-4"),
    }
    for name, (value, error) in pinned.items():
        assert fit.regression.parameters[name].hex() == value
        assert fit.regression.standard_errors[name].hex() == error
    pinned = {
        "theta": ("0x1.499a65aff410dp+7", "0x1.34f4350c868cbp+5"),
        "mu": ("0x1.07a744b10f4c3p+1", "0x1.6abd869a3343bp-3"),
        "sigma": ("0x1.3b52d9e840f3bp+4", "0x1.c0f822a6bf440p+0"),
        "half_life": ("0x1.13a42390c15f7p-8", "0x1.025f64a3ffbabp-10"),
    }
    for name, (value, error) in pinned.items():
        assert fit.process.parameters[name].hex() == value
        assert fit.process.standard_errors[name].hex() == error
    assert fit.regression.estimate_id == (
        "f2e97216f3fda4b7c634a4907624e44512daa58cfba06553a9304862e0ad7c96"
    )
    assert fit.process.estimate_id == (
        "b98e0ef92285fde88c074a9233756bbf7299c4c15c0df8978629b75b3a68bf67"
    )


def test_the_vecms_relation_adjustments_and_identities_are_pinned() -> None:
    fit = fit_vecm(
        [Y, X],
        rank=1,
        case=JohansenCase.RESTRICTED_CONSTANT,
        lags=FixedLags(1),
        tolerance=1e-15,
        max_iterations=60,
        maximum_condition=CONDITION,
    )

    relation = {name: value.hex() for name, value in fit.relations.parameters.items()}
    assert relation == {
        "beta_1_series_1": "0x1.0000000000000p+0",
        "beta_1_series_2": "-0x1.8231d704935c9p+0",
        "beta_1_constant": "-0x1.05ce8a158b7a6p+1",
    }
    first, second = fit.equations
    assert first.parameters["alpha_1"].hex() == "-0x1.4a293b0bcefe2p-1"
    assert first.standard_errors["alpha_1"].hex() == "0x1.119aa8d60131cp-3"
    assert second.parameters["alpha_1"].hex() == "-0x1.2fb68b2c17f61p-4"
    assert fit.covariance.parameters["sigma_1_2"].hex() == "0x1.2ecc978d2c337p-1"
    assert [fit.relations.estimate_id, first.estimate_id, second.estimate_id] == [
        "728182c2309735e7d944f30ab9e3d500707fa2a5fc323f9fd3f7bd0ebc9b89bb",
        "584deee738ccf991e3a940ce5d469894c73f55663da1eb6586660328c7ac22b4",
        "7e1fb855231b8723798a8b0dcc907660c345491f20868328d4b2e2fb4dc038fb",
    ]
    assert fit.covariance.estimate_id == (
        "24f4ca5d21466c40dba4e15c2ba64cd2676057b559bee12b600ec3339582ccbe"
    )


def test_the_vars_chosen_order_coefficients_and_identities_are_pinned() -> None:
    """BIC's choice is Decimal's: the same order on every host."""

    fit = fit_var(
        [Y, X],
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.BIC, 4, None),
        maximum_condition=CONDITION,
    )

    assert (fit.lags, fit.observations) == (1, 119)
    first, second = fit.equations
    assert first.parameters["phi_1_series_2"].hex() == "0x1.8efaf0575a7bbp-1"
    assert first.standard_errors["phi_1_series_2"].hex() == "0x1.9cbf62629403ap-3"
    assert second.parameters["phi_1_series_2"].hex() == "0x1.0388b2abfed70p+0"
    assert fit.covariance.parameters["sigma_1_2"].hex() == "0x1.3059620495a02p-1"
    assert [first.estimate_id, second.estimate_id, fit.covariance.estimate_id] == [
        "6125b60b1f493db73a960c6b6d695dc877c8fff572ff06a1444e47655cd28e00",
        "7f166064ae68647a865a1744cd3e3ad670dce33b2af65e93654c2f145a5239fe",
        "b2128dec23ff5fbf1336f289803e7db4d31ab3689d93ba4bc000904d0fd5bf3f",
    ]


def _garch_returns(count: int) -> list[float]:
    """A GARCH(1, 1) path from Irwin-Hall shocks: integers, exact scaling, a correct sqrt."""

    state, variance, last, values = 99, 0.25, 0.0, []
    for _ in range(count + 200):
        shock = 0.0
        for _ in range(12):
            state = (1103515245 * state + 12345) % 2**31
            shock += (state >> 8) / 2**23
        shock -= 6.0
        variance = 0.05 + 0.08 * last * last + 0.9 * variance
        last = math.sqrt(variance) * shock
        values.append(last)
    return values[-count:]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            VolatilityModel.GARCH,
            {
                "alpha": 0.07053981635047683,
                "beta": 0.9099659429355309,
                "mu": 0.026355451774899546,
                "omega": 0.04581125895397727,
            },
        ),
        (
            VolatilityModel.EGARCH,
            {
                "alpha": 0.15061474972823405,
                "beta": 0.9789354682831377,
                "gamma": 0.006080560045845804,
                "mu": 0.0406039059642397,
                "omega": 0.015100096254914817,
            },
        ),
    ],
)
def test_a_volatility_fit_agrees_across_hosts_within_its_stated_tolerance(
    model: VolatilityModel, expected: dict[str, float]
) -> None:
    """Class 3: the likelihood is the host's log and exp, so hosts agree to a tolerance.

    The returns are the same bits everywhere. A host's last-bit logarithm can
    move the optimiser's path, and the optimum it reaches moves by far less than
    the gradient tolerance allows: 1e-4, relatively, is the stated agreement.
    """

    fit = fit_volatility(
        _garch_returns(800),
        model=model,
        start=VolatilityStart.MOMENTS,
        tolerance=1e-6,
        max_iterations=200,
        step=1e-5,
    )

    assert fit.estimate.parameters == pytest.approx(expected, rel=1e-4, abs=1e-7)
    assert not fit.estimate.numerical_class.identity_bearing
