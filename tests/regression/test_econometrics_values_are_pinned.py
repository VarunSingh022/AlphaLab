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
    ArmaMethod,
    ArmaStart,
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
    fit_arma,
    fit_ornstein_uhlenbeck,
    fit_var,
    fit_vecm,
    fit_volatility,
    johansen,
    kpss,
    phillips_perron,
    select_arma,
)
from alphalab.econometrics.cointegration import engle_granger
from alphalab.econometrics.regimes import (
    QuantileStart,
    fit_gaussian_hmm,
    fit_markov_switching_regression,
)
from alphalab.econometrics.state_space import (
    Intercept,
    LinearGaussianModel,
    fit_local_level,
    fit_time_varying_beta,
    kalman_filter,
    rts_smoother,
)
from alphalab.research.cointegration_screen import cointegration_screen
from alphalab.research.multiple_testing import Correction

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


def test_the_kalman_filter_and_rts_smoother_are_pinned() -> None:
    """Class 2 for given variances: every mean and covariance is the same bits on every host."""

    model = LinearGaussianModel(
        transition=((1.0, 0.0), (0.0, 1.0)),
        transition_covariance=((0.0625, 0.0), (0.0, 0.015625)),
        observation_covariance=((0.25,),),
        initial_mean=(2.0, 1.5),
        initial_covariance=((4.0, 0.0), (0.0, 1.0)),
    )
    filtered = kalman_filter(model, [[y] for y in Y], [((1.0, x),) for x in X])
    smoothed = rts_smoother(model, filtered)

    assert [v.hex() for v in filtered.filtered_means[-1]] == [
        "0x1.4f894adde8e70p+0",
        "0x1.e7c67ce57b134p+0",
    ]
    last = filtered.filtered_covariances[-1]
    assert [last[0][0].hex(), last[0][1].hex(), last[1][1].hex()] == [
        "0x1.43af9cc3095bbp-1",
        "-0x1.478b5e2023607p-3",
        "0x1.a5c50f140b442p-5",
    ]
    assert [v.hex() for v in smoothed.smoothed_means[59]] == [
        "0x1.0bddbdb7d7fa0p+1",
        "0x1.edd104da0ac0ap+0",
    ]
    assert [v.hex() for v in smoothed.initial_mean] == [
        "0x1.3949b20eb4e02p+1",
        "0x1.855267976f4e6p+0",
    ]
    assert smoothed.initial_covariance[1][1].hex() == "0x1.2937fab0c28d4p-3"
    # The log-likelihood is class 3: 120 logarithms, each within an ulp of a host's.
    assert filtered.log_likelihood == pytest.approx(-195.03665206640113, rel=1e-12)


def _drifting(count: int) -> tuple[list[float], list[float], list[float]]:
    """A hedge ratio drifting in 256ths beside a price near 40, and a level walking in eighths."""

    state = 4302

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    beta, price, level, xs, ys, levels = 0.75, 40.0, 0.0, [], [], []
    for _ in range(count):
        beta += (draw(9) - 4) / 256
        price += (draw(17) - 8) / 8
        xs.append(price)
        ys.append(1.0 + beta * price + (draw(13) - 6) / 8)
        level += (draw(9) - 4) / 8
        levels.append(level + (draw(13) - 6) / 4)
    return ys, xs, levels


def test_a_state_space_fit_agrees_across_hosts_within_its_stated_tolerance() -> None:
    """Class 3, as a volatility fit: the variances to 1e-4, relatively; the identity exactly."""

    ys, xs, levels = _drifting(400)
    level = fit_local_level(
        levels,
        start={"level_variance": 0.5, "observation_variance": 0.5},
        tolerance=1e-6,
        max_iterations=200,
        step=1e-5,
    )
    beta = fit_time_varying_beta(
        ys,
        [xs],
        intercept=Intercept.FIXED,
        start={"beta_1_drift_variance": 1e-3, "observation_variance": 0.5},
        maximum_condition=CONDITION,
        tolerance=1e-6,
        max_iterations=200,
        step=1e-5,
    )

    assert level.estimate.parameters == pytest.approx(
        {"level_variance": 0.10743178562936154, "observation_variance": 0.8487778414934913},
        rel=1e-4,
    )
    assert beta.estimate.parameters == pytest.approx(
        {
            "beta_1_drift_variance": 9.363121179778145e-05,
            "observation_variance": 0.2514359869023816,
        },
        rel=1e-4,
    )
    assert [level.estimate.estimate_id, beta.estimate.estimate_id] == [
        "f6f338e52aa088b3ef3bcfb9f39db423fcdbc4727442d862fab27d84d869fdbb",
        "8e64c6ada7d10ec341ee2ca46ee082a68f5a2496bada672f5c20cae56962a0c6",
    ]


def _regimes(count: int) -> tuple[list[float], list[float], list[float]]:
    """A two-state chain by integer draws, its noise a sum of four uniforms: exact values."""

    state = 77

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    regime, values, xs, ys = 0, [], [], []
    for _ in range(count):
        if draw(100) < (3 if regime == 0 else 5):
            regime = 1 - regime
        shock = sum(draw(1024) for _ in range(4)) / 512 - 4.0
        values.append((0.5, -1.0)[regime] + (0.25, 1.0)[regime] * shock)
        x = (draw(2048) - 1024) / 512
        xs.append(x)
        ys.append((0.0, 1.0)[regime] + (1.0, -0.5)[regime] * x + (0.25, 1.0)[regime] * shock)
    return values, xs, ys


def test_a_regime_fit_agrees_across_hosts_within_its_stated_tolerance() -> None:
    """Class 3 (ADR-0075 section 5): every value is exp's and log's; the identity is exact."""

    values, xs, ys = _regimes(400)
    hmm = fit_gaussian_hmm(
        values, states=2, start=QuantileStart(0.9), tolerance=1e-8, max_iterations=500, step=1e-5
    )
    regression = fit_markov_switching_regression(
        ys,
        [xs],
        intercept=True,
        states=2,
        start=QuantileStart(0.9),
        tolerance=1e-8,
        max_iterations=500,
        step=1e-5,
        maximum_condition=CONDITION,
    )

    expected_hmm = {
        "state_1_mean": -1.0939088311422966,
        "state_1_variance": 1.2015081352383217,
        "state_2_mean": 0.46272564777608255,
        "state_2_variance": 0.0829480766732849,
        "transition_1_1": 0.9592721483690574,
        "transition_1_2": 0.04072785163094259,
        "transition_2_1": 0.04362770647765761,
        "transition_2_2": 0.9563722935223424,
    }
    expected_regression = {
        "state_1_beta_1": 0.9840588900848486,
        "state_1_intercept": -0.04069761879765738,
        "state_1_variance": 0.0805013909341825,
        "state_2_beta_1": -0.5809314489014361,
        "state_2_intercept": 0.9201526909427418,
        "state_2_variance": 1.2082266418845948,
        "transition_1_1": 0.9565473871927482,
        "transition_1_2": 0.043452612807251705,
        "transition_2_1": 0.04157256237941942,
        "transition_2_2": 0.9584274376205807,
    }
    for fit, expected in ((hmm, expected_hmm), (regression, expected_regression)):
        fitted = {k: v for k, v in fit.estimate.parameters.items() if not k.startswith("initial")}
        assert fitted == pytest.approx(expected, rel=1e-4, abs=1e-9)
    assert hmm.log_likelihood == pytest.approx(-385.74489644201617, rel=1e-9)
    assert [hmm.estimate.estimate_id, regression.estimate.estimate_id] == [
        "3492a28282267dd83240021e272c30c09738cf35beb182af7958166e2c021a95",
        "7ff73ff2c778287769e45f9ee4e2b4c621cc7b925a37851c62f19a035dc73bc7",
    ]


def test_a_pair_screens_identity_statistics_and_hedge_ratios_are_pinned() -> None:
    """The identity covers the class-2 results only: the p-values and decisions are beside it."""

    ys, xs, levels = _drifting(120)
    screen = cointegration_screen(
        {"Y": Y, "X": X, "PRICE": xs, "HEDGED": ys, "LEVEL": levels},
        [("Y", "X"), ("HEDGED", "PRICE"), ("LEVEL", "X"), ("Y", "PRICE")],
        start=10,
        stop=120,
        deterministic=Deterministic.CONSTANT,
        lags=FixedLags(1),
        maximum_condition=CONDITION,
        correction=Correction.HOLM,
        alpha=0.05,
    )

    assert screen.screen_id == "442cca157861b82ab4e8ea0be8c8e2ce109a55b5d80d975d415ce6b1a5ee25c6"
    assert [pair.test.statistic.hex() for pair in screen.pairs if pair.test is not None] == [
        "-0x1.42bd848b2940dp+2",
        "-0x1.25780f7397c44p+1",
        "-0x1.5bb4607a7e5b5p+2",
        "-0x1.122d8a69302e1p+1",
    ]
    assert [ratio.hex() for pair in screen.pairs if (ratio := pair.hedge_ratio) is not None] == [
        "0x1.90b4bdf402cf6p+0",
        "0x1.21bb8c952272cp+0",
        "-0x1.21396a982347fp-1",
        "-0x1.3e80a877fc4f4p-6",
    ]


def _arma_values(count: int) -> list[float]:
    """An ARMA(1, 1) about 0.5 from integer shocks in eighths: exact values."""

    state = 1717

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    value, last, values = 0.5, 0.0, []
    for _ in range(count + 50):
        shock = (draw(17) - 8) / 8
        value = 0.5 + 0.625 * (value - 0.5) + shock + 0.25 * last
        last = shock
        values.append(value)
    return values[-count:]


def test_arma_by_least_squares_is_pinned_and_by_likelihood_agrees_across_hosts() -> None:
    """Least squares is class 2, bit for bit; the exact likelihood class 3, to 1e-4."""

    values = _arma_values(300)
    choices = {"tolerance": 1e-6, "max_iterations": 200, "step": 1e-6}
    least = fit_arma(
        values,
        order=(1, 1),
        deterministic=Deterministic.CONSTANT,
        method=ArmaMethod.CONDITIONAL_LEAST_SQUARES,
        start=ArmaStart.ZEROS,
        **choices,  # type: ignore[arg-type]
    )
    exact = fit_arma(
        values,
        order=(1, 1),
        deterministic=Deterministic.CONSTANT,
        method=ArmaMethod.EXACT_LIKELIHOOD,
        start=ArmaStart.ZEROS,
        **choices,  # type: ignore[arg-type]
    )
    selection = select_arma(
        values,
        maximum_order=(2, 1),
        criterion=LagCriterion.BIC,
        deterministic=Deterministic.CONSTANT,
        method=ArmaMethod.CONDITIONAL_LEAST_SQUARES,
        start=ArmaStart.ZEROS,
        **choices,  # type: ignore[arg-type]
    )

    assert {k: v.hex() for k, v in least.estimate.parameters.items()} == {
        "ar_1": "0x1.5fac0698d471fp-1",
        "ma_1": "0x1.21310f57012d9p-2",
        "mean": "0x1.7c271c8e91b6cp-2",
        "variance": "0x1.8d5e3fdef7b4dp-2",
    }
    assert {k: v.hex() for k, v in least.estimate.standard_errors.items()} == {
        "ar_1": "0x1.a7a5d19247f5ap-5",
        "ma_1": "0x1.18235d1ac09bap-4",
        "mean": "0x1.2de46ee6a0113p-3",
        "variance": "0x1.03fe54c11ae6cp-5",
    }
    assert least.estimate.estimate_id == (
        "ce199db6f5c688a44a6edf17bb8b2ab20607c5ce11346563565dbd18e687d2f1"
    )
    assert exact.estimate.parameters == pytest.approx(
        {
            "ar_1": 0.6834719124410868,
            "ma_1": 0.28621869470073213,
            "mean": 0.35845851768491405,
            "variance": 0.38724130337383567,
        },
        rel=1e-4,
    )
    assert exact.estimate.estimate_id == (
        "b0304718fa7691d433d5b1f0a473644a0f44a3d7aa7652d928d5f57649fc29c1"
    )
    assert selection.chosen.order == (1, 1)
    assert [value.hex() for _, value in selection.candidates] == [
        "0x1.f9ad74c5f512ep+4",
        "-0x1.5f7020cf0e50bp+7",
        "-0x1.f8626a1f11657p+7",
        "-0x1.03c773e432606p+8",
        "-0x1.03a9b2c435ee6p+8",
        "-0x1.fde3105d2a661p+7",
    ]
