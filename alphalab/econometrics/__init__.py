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
  cointegrating rank, under each of Osterwald-Lenum's five cases.
* :mod:`~alphalab.econometrics.mean_reversion`: the Ornstein-Uhlenbeck fit and
  the half-life it implies, each with its standard error (OFE-007 reopened).

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
    CointegrationTest,
    JohansenRank,
    JohansenTest,
    engle_granger,
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
from alphalab.econometrics.exceptions import EconometricsError, EconometricsInputError
from alphalab.econometrics.mean_reversion import (
    MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS,
    ORNSTEIN_UHLENBECK_METHOD,
    ORNSTEIN_UHLENBECK_REGRESSION_METHOD,
    OrnsteinUhlenbeckFit,
    fit_ornstein_uhlenbeck,
)
from alphalab.econometrics.series import (
    SERIES_SCHEME,
    SYSTEM_SCHEME,
    require_series,
    series_identity,
    system_identity,
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

__all__ = [
    "ADF_METHOD",
    "ENGLE_GRANGER_METHOD",
    "ENGLE_GRANGER_RESIDUAL_METHOD",
    "JOHANSEN_LEVELS",
    "JOHANSEN_METHOD",
    "KPSS_1992",
    "KPSS_METHOD",
    "LEVELS",
    "MACKINNON_1994",
    "MACKINNON_2010",
    "MHM_1996",
    "MINIMUM_DICKEY_FULLER_OBSERVATIONS",
    "MINIMUM_JOHANSEN_OBSERVATIONS",
    "MINIMUM_ORNSTEIN_UHLENBECK_OBSERVATIONS",
    "ORNSTEIN_UHLENBECK_METHOD",
    "ORNSTEIN_UHLENBECK_REGRESSION_METHOD",
    "PHILLIPS_PERRON_METHOD",
    "SERIES_SCHEME",
    "SYSTEM_SCHEME",
    "Bandwidth",
    "CointegrationTest",
    "CriticalValues",
    "Deterministic",
    "EconometricsError",
    "EconometricsInputError",
    "FixedBandwidth",
    "FixedLags",
    "JohansenCase",
    "JohansenRank",
    "JohansenStatistic",
    "JohansenTest",
    "LagCriterion",
    "LagRule",
    "Null",
    "OrnsteinUhlenbeckFit",
    "PBound",
    "PValue",
    "SchwertBandwidth",
    "SelectedLags",
    "UnitRootTest",
    "augmented_dickey_fuller",
    "engle_granger",
    "fit_ornstein_uhlenbeck",
    "johansen",
    "johansen_critical_values",
    "johansen_p_value",
    "kpss",
    "kpss_critical_values",
    "kpss_p_value",
    "mackinnon_critical_values",
    "mackinnon_p_value",
    "phillips_perron",
    "require_series",
    "series_identity",
    "system_identity",
]
