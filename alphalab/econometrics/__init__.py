"""AlphaLab Econometrics: time-series estimation with an identity (v4.3, ADR-0074).

A researcher's time-series model is a recorded estimate. Every fit returns an
:class:`~alphalab.common.estimate.Estimate` -- its parameters, their standard
errors, its method, the identity of the data it was fitted on, every choice it
was given -- and every test returns its statistic, its critical values with
their source, its p-value with how it was computed, and the estimate of the
regression it came from (ADR-0058 section 6).

This package imports :mod:`alphalab.common` and the standard library only. It
takes the caller's series as finite floats, and never reads a dataset, runs a
strategy or touches the execution path.

Delivered so far
----------------
* :mod:`~alphalab.econometrics.series`: a series' identity, derived from its
  values and never typed.
* :mod:`~alphalab.econometrics.critical_values`: MacKinnon's (2010) response
  surfaces and (1994) p-value approximations, KPSS's (1992) table, and
  MacKinnon, Haug and Michelis's (1996) tables for Johansen's tests, each held
  to its published values.
* :mod:`~alphalab.econometrics.unit_root`: the augmented Dickey-Fuller,
  Phillips-Perron and KPSS tests.
* :mod:`~alphalab.econometrics.cointegration`: Engle and Granger's two-step
  test. Its cointegrating regression is the combination tested: for a pair,
  the hedge ratio. Johansen's trace and maximum-eigenvalue tests of a system's
  cointegrating rank, under each of Osterwald-Lenum's five cases, and the VECM
  of a stated rank, fitted by Johansen's two steps.
* :mod:`~alphalab.econometrics.mean_reversion`: the Ornstein-Uhlenbeck fit and
  the half-life it implies, each with its standard error (OFE-007 reopened).
* :mod:`~alphalab.econometrics.var`: vector autoregressions by least squares,
  equation by equation, the lags fixed or chosen by AIC or BIC.
* :mod:`~alphalab.econometrics.volatility`: GARCH, GJR-GARCH and EGARCH by
  Gaussian likelihood, admissible by their parameterisation.
* :mod:`~alphalab.econometrics.state_space`: the Kalman filter and the RTS
  smoother, and the local level and the time-varying beta fitted from a
  diffuse start.
* :mod:`~alphalab.econometrics.regimes`: the Gaussian hidden Markov model and
  the Markov-switching regression, fitted by EM (ADR-0075, OFE-010 reopened).

Every choice is stated
----------------------
Deterministic terms, lags or the criterion choosing them, a long-run
variance's bandwidth, and the largest condition number a regression may have
are arguments, never defaults. What an estimator cannot take is refused before
it runs: a non-finite value, a sample too short, an ill-conditioned design.
"""

from alphalab.econometrics.cointegration import (
    ENGLE_GRANGER_METHOD,
    ENGLE_GRANGER_RESIDUAL_METHOD,
    JOHANSEN_METHOD,
    MINIMUM_JOHANSEN_OBSERVATIONS,
    VECM_METHOD,
    CointegrationTest,
    JohansenRank,
    JohansenTest,
    VecmFit,
    engle_granger,
    fit_vecm,
    johansen,
)
from alphalab.econometrics.critical_values import (
    JOHANSEN_LEVELS,
    KPSS_1992,
    LEVELS,
    MACKINNON_1994,
    MACKINNON_2010,
    MHM_1996,
    CriticalValues,
    Deterministic,
    JohansenCase,
    JohansenStatistic,
    PBound,
    PValue,
    johansen_critical_values,
    johansen_p_value,
    kpss_critical_values,
    kpss_p_value,
    mackinnon_critical_values,
    mackinnon_p_value,
)
from alphalab.econometrics.exceptions import (
    CollapsedStateError,
    EconometricsError,
    EconometricsInputError,
)
from alphalab.econometrics.mean_reversion import (
    MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS,
    ORNSTEIN_UHLENBECK_METHOD,
    ORNSTEIN_UHLENBECK_REGRESSION_METHOD,
    OrnsteinUhlenbeckFit,
    fit_ornstein_uhlenbeck,
)
from alphalab.econometrics.regimes import (
    GAUSSIAN_HMM_METHOD,
    MARKOV_SWITCHING_METHOD,
    MINIMUM_REGIME_OBSERVATIONS,
    QuantileStart,
    RegimeFit,
    fit_gaussian_hmm,
    fit_markov_switching_regression,
)
from alphalab.econometrics.series import (
    SERIES_SCHEME,
    SYSTEM_SCHEME,
    require_series,
    series_identity,
    system_identity,
)
from alphalab.econometrics.state_space import (
    LOCAL_LEVEL_METHOD,
    MINIMUM_STATE_SPACE_OBSERVATIONS,
    TIME_VARYING_BETA_METHOD,
    Intercept,
    KalmanFilter,
    LinearGaussianModel,
    RtsSmoother,
    StateSpaceFit,
    fit_local_level,
    fit_time_varying_beta,
    kalman_filter,
    rts_smoother,
)
from alphalab.econometrics.unit_root import (
    ADF_METHOD,
    KPSS_METHOD,
    MINIMUM_DICKEY_FULLER_OBSERVATIONS,
    PHILLIPS_PERRON_METHOD,
    Bandwidth,
    FixedBandwidth,
    FixedLags,
    LagCriterion,
    LagRule,
    Null,
    SchwertBandwidth,
    SelectedLags,
    UnitRootTest,
    augmented_dickey_fuller,
    kpss,
    phillips_perron,
)
from alphalab.econometrics.var import MINIMUM_VAR_OBSERVATIONS, VAR_METHOD, VarFit, fit_var
from alphalab.econometrics.volatility import (
    MINIMUM_VOLATILITY_OBSERVATIONS,
    VOLATILITY_METHOD,
    VolatilityFit,
    VolatilityModel,
    VolatilityStart,
    fit_volatility,
)

__all__ = [
    "ADF_METHOD",
    "ENGLE_GRANGER_METHOD",
    "ENGLE_GRANGER_RESIDUAL_METHOD",
    "GAUSSIAN_HMM_METHOD",
    "JOHANSEN_LEVELS",
    "JOHANSEN_METHOD",
    "KPSS_1992",
    "KPSS_METHOD",
    "LEVELS",
    "LOCAL_LEVEL_METHOD",
    "MACKINNON_1994",
    "MACKINNON_2010",
    "MARKOV_SWITCHING_METHOD",
    "MHM_1996",
    "MINIMUM_DICKEY_FULLER_OBSERVATIONS",
    "MINIMUM_JOHANSEN_OBSERVATIONS",
    "MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS",
    "MINIMUM_REGIME_OBSERVATIONS",
    "MINIMUM_STATE_SPACE_OBSERVATIONS",
    "MINIMUM_VAR_OBSERVATIONS",
    "MINIMUM_VOLATILITY_OBSERVATIONS",
    "ORNSTEIN_UHLENBECK_METHOD",
    "ORNSTEIN_UHLENBECK_REGRESSION_METHOD",
    "PHILLIPS_PERRON_METHOD",
    "SERIES_SCHEME",
    "SYSTEM_SCHEME",
    "TIME_VARYING_BETA_METHOD",
    "VAR_METHOD",
    "VECM_METHOD",
    "VOLATILITY_METHOD",
    "Bandwidth",
    "CointegrationTest",
    "CollapsedStateError",
    "CriticalValues",
    "Deterministic",
    "EconometricsError",
    "EconometricsInputError",
    "FixedBandwidth",
    "FixedLags",
    "Intercept",
    "JohansenCase",
    "JohansenRank",
    "JohansenStatistic",
    "JohansenTest",
    "KalmanFilter",
    "LagCriterion",
    "LagRule",
    "LinearGaussianModel",
    "Null",
    "OrnsteinUhlenbeckFit",
    "PBound",
    "PValue",
    "QuantileStart",
    "RegimeFit",
    "RtsSmoother",
    "SchwertBandwidth",
    "SelectedLags",
    "StateSpaceFit",
    "UnitRootTest",
    "VarFit",
    "VecmFit",
    "VolatilityFit",
    "VolatilityModel",
    "VolatilityStart",
    "augmented_dickey_fuller",
    "engle_granger",
    "fit_gaussian_hmm",
    "fit_local_level",
    "fit_markov_switching_regression",
    "fit_ornstein_uhlenbeck",
    "fit_time_varying_beta",
    "fit_var",
    "fit_vecm",
    "fit_volatility",
    "johansen",
    "johansen_critical_values",
    "johansen_p_value",
    "kalman_filter",
    "kpss",
    "kpss_critical_values",
    "kpss_p_value",
    "mackinnon_critical_values",
    "mackinnon_p_value",
    "phillips_perron",
    "require_series",
    "rts_smoother",
    "series_identity",
    "system_identity",
]
