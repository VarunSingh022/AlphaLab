"""Published critical values and p-values for unit-root and cointegration tests (ADR-0074 s. 6).

Four published sources, each held to its own table:

* **MacKinnon (2010)**, "Critical Values for Cointegration Tests", Queen's
  Economics Department Working Paper 1227, Tables 2 to 4. Its response surfaces
  give the Dickey-Fuller and Engle-Granger tests' critical values at 1, 5 and
  10 percent for a sample of ``T`` observations, ``N`` the series in the
  cointegrating regression (one for a unit-root test):
  ``C(p) = β∞ + β1 / T + β2 / T² + β3 / T³``. A coefficient the paper omits is
  zero, as it states. The no-constant case is published for ``N = 1`` only.
* **MacKinnon (1994)**, "Approximate Asymptotic Distribution Functions for
  Unit-Root and Cointegration Tests", Journal of Business and Economic
  Statistics 12(2), 167-176. Its approximations give a statistic's p-value as
  ``Φ(g0 + g1 τ + g2 τ²)`` in the left tail, below ``τ*``, and
  ``Φ(g0 + g1 τ + g2 τ² + g3 τ³)`` above it, within ``[τmin, τmax]``, for
  ``N = 1`` to ``6``. The coefficients are printed scaled, and are kept here as
  printed, with the scale applied by exact division.
* **Kwiatkowski, Phillips, Schmidt and Shin (1992)**, "Testing the Null
  Hypothesis of Stationarity against the Alternative of a Unit Root", Journal
  of Econometrics 54, 159-178, Table 1: the KPSS statistic's asymptotic critical
  values, for level and for trend stationarity.
* **MacKinnon, Haug and Michelis (1996)**, "Numerical Distribution Functions of
  Likelihood Ratio Tests for Cointegration", York University Department of
  Economics Working Paper 96-07, Tables 1 to 5, published in the Journal of
  Applied Econometrics 14 (1999), 563-577. They give Johansen's trace and
  maximum-eigenvalue tests' asymptotic critical values at 1, 2, 5, 10 and 20
  percent, for ``p - r = 1`` to ``12`` and each of Osterwald-Lenum's five
  cases of deterministic terms, to two decimals.

How the tables were transcribed
-------------------------------
MacKinnon (2010)'s tables were extracted from the paper's text and generated
into the literal below, then checked row by row against an independent
transcription. That transcription differs from the paper in four rows, which
the tests pin to the paper's printed values. The 1994 coefficients are held by
their own consistency: at the 2010 asymptotic critical values they give p-values
within 0.6 percent of 0.01, 0.05 and 0.10 for every case and ``N``, and their
two branches meet at ``τ*``.

MacKinnon, Haug and Michelis's tables were decoded from the working paper's own
text, font by font, and generated into the literal below, never typed. Their
authors' program gives the same critical values in a widely used four-decimal
transcription for three of the five cases, and every one of its 216 values is
within 0.035 of the paper's: the transcription was made from the program, not
the tables, so they differ by more than rounding, but both are the same
estimates of the same quantiles.

Numerical classes (ADR-0058)
----------------------------
A response surface is Horner's rule in ``1 / T``, and a KPSS or Johansen p-value
a linear interpolation: class 2. A MacKinnon p-value passes through the normal
distribution: class 3, recorded beside an identity, never in it.

Beyond a table
--------------
A p-value is never extrapolated. A statistic beyond the 1994 approximation's
range reports the p-value at the range's end, as a bound: the true value is at
most it below ``τmin``, and at least it above ``τmax``. A KPSS statistic beyond
the table reports 0.01 or 0.10 the same way, and a Johansen statistic beyond
its table 0.01 or 0.20.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from alphalab.common.statistics import normal_cdf
from alphalab.econometrics.exceptions import EconometricsInputError

__all__ = [
    "JOHANSEN_LEVELS",
    "KPSS_1992",
    "LEVELS",
    "MACKINNON_1994",
    "MACKINNON_2010",
    "MHM_1996",
    "CriticalValues",
    "Deterministic",
    "JohansenCase",
    "JohansenStatistic",
    "PBound",
    "PValue",
    "johansen_critical_values",
    "johansen_p_value",
    "kpss_critical_values",
    "kpss_p_value",
    "mackinnon_critical_values",
    "mackinnon_p_value",
]

#: The source of the response surfaces.
MACKINNON_2010: Final = (
    "MacKinnon (2010), Critical Values for Cointegration Tests, Queen's Economics "
    "Department Working Paper 1227, Tables 2-4"
)
#: The source of the p-value approximations.
MACKINNON_1994: Final = (
    "MacKinnon (1994), Approximate Asymptotic Distribution Functions for Unit-Root and "
    "Cointegration Tests, Journal of Business and Economic Statistics 12(2), 167-176"
)
#: The source of KPSS's critical values.
KPSS_1992: Final = (
    "Kwiatkowski, Phillips, Schmidt and Shin (1992), Journal of Econometrics 54, 159-178, Table 1"
)

#: The source of Johansen's critical values.
MHM_1996: Final = (
    "MacKinnon, Haug and Michelis (1996), Numerical Distribution Functions of Likelihood "
    "Ratio Tests for Cointegration, York University Department of Economics Working Paper "
    "96-07, Tables 1-5; Journal of Applied Econometrics 14 (1999), 563-577"
)

#: The test sizes MacKinnon (2010) tabulates.
LEVELS: Final = (0.01, 0.05, 0.10)

#: The test sizes MacKinnon, Haug and Michelis (1996) tabulate.
JOHANSEN_LEVELS: Final = (0.01, 0.02, 0.05, 0.10, 0.20)


class Deterministic(Enum):
    """The deterministic terms in a test regression, by MacKinnon's names."""

    #: No constant: MacKinnon's ``nc``. Tabulated for unit-root tests only.
    NONE = "nc"
    #: A constant: ``c``.
    CONSTANT = "c"
    #: A constant and a linear trend: ``ct``.
    CONSTANT_TREND = "ct"
    #: A constant, a linear and a quadratic trend: ``ctt``.
    CONSTANT_QUADRATIC_TREND = "ctt"


class JohansenCase(Enum):
    """A VECM's deterministic terms: Osterwald-Lenum's five cases.

    MacKinnon, Haug and Michelis number them 0, 1*, 1, 2* and 2, from the most
    restrictive to the least. A restricted term lies in the cointegrating
    relations only; an unrestricted one also moves the levels.
    """

    #: Case 0: no deterministic terms.
    NONE = "none"
    #: Case 1*: a constant in the cointegrating relations only; no trend in the levels.
    RESTRICTED_CONSTANT = "restricted_constant"
    #: Case 1: an unrestricted constant: linear trends in the levels.
    UNRESTRICTED_CONSTANT = "unrestricted_constant"
    #: Case 2*: an unrestricted constant and a trend in the cointegrating relations only.
    RESTRICTED_TREND = "restricted_trend"
    #: Case 2: an unrestricted constant and trend: quadratic trends in the levels.
    UNRESTRICTED_TREND = "unrestricted_trend"


class JohansenStatistic(Enum):
    """Which of Johansen's two likelihood-ratio statistics."""

    #: Rank at most ``r`` against rank ``p``.
    TRACE = "trace"
    #: Rank ``r`` against rank ``r + 1``.
    MAXIMUM_EIGENVALUE = "maximum_eigenvalue"


@dataclass(frozen=True, slots=True)
class CriticalValues:
    """A test's critical values at stated sizes, and where they come from.

    Attributes:
        values: The critical value at each size, smallest size first.
        observations: The sample size ``T`` they are for; ``None`` for the
            asymptotic values.
        source: The published table.
    """

    values: Mapping[float, float]
    observations: int | None
    source: str


class PBound(Enum):
    """Which side of a p-value's reported value the true value lies, beyond a table."""

    #: The statistic is beyond the table's extreme: the p-value is at most the value.
    AT_MOST = "at_most"
    #: The statistic is short of the table's least extreme: the p-value is at least it.
    AT_LEAST = "at_least"


@dataclass(frozen=True, slots=True)
class PValue:
    """A test's p-value, how it was computed, and whether it is a bound.

    Attributes:
        value: The p-value, or the bound it is reported as.
        bound: ``None`` within the source's range; otherwise which side the
            true p-value lies.
        source: The published approximation or table.
    """

    value: float
    bound: PBound | None
    source: str


_Coefficients = tuple[float, float, float, float]
_Surface = tuple[_Coefficients, _Coefficients, _Coefficients]

# MacKinnon (2010), Tables 2-4: (β∞, β1, β2, β3) at 1, 5 and 10 percent, as printed.
_MACKINNON_2010: Final[Mapping[tuple[Deterministic, int], _Surface]] = {
    (Deterministic.NONE, 1): (
        (-2.56574, -2.2358, -3.627, 0.0),
        (-1.94100, -0.2686, -3.365, 31.223),
        (-1.61682, 0.2656, -2.714, 25.364),
    ),
    (Deterministic.CONSTANT, 1): (
        (-3.43035, -6.5393, -16.786, -79.433),
        (-2.86154, -2.8903, -4.234, -40.040),
        (-2.56677, -1.5384, -2.809, 0.0),
    ),
    (Deterministic.CONSTANT, 2): (
        (-3.89644, -10.9519, -22.527, 0.0),
        (-3.33613, -6.1101, -6.823, 0.0),
        (-3.04445, -4.2412, -2.720, 0.0),
    ),
    (Deterministic.CONSTANT, 3): (
        (-4.29374, -14.4354, -33.195, 47.433),
        (-3.74066, -8.5631, -10.852, 27.982),
        (-3.45218, -6.2143, -3.718, 0.0),
    ),
    (Deterministic.CONSTANT, 4): (
        (-4.64332, -18.1031, -37.972, 0.0),
        (-4.09600, -11.2349, -11.175, 0.0),
        (-3.81020, -8.3931, -4.137, 0.0),
    ),
    (Deterministic.CONSTANT, 5): (
        (-4.95756, -21.8883, -45.142, 0.0),
        (-4.41519, -14.0406, -12.575, 0.0),
        (-4.13157, -10.7417, -3.784, 0.0),
    ),
    (Deterministic.CONSTANT, 6): (
        (-5.24568, -25.6688, -57.737, 88.639),
        (-4.70693, -16.9178, -17.492, 60.007),
        (-4.42501, -13.1875, -5.104, 27.877),
    ),
    (Deterministic.CONSTANT, 7): (
        (-5.51233, -29.5760, -69.398, 164.295),
        (-4.97684, -19.9021, -22.045, 110.761),
        (-4.69648, -15.7315, -6.922, 67.721),
    ),
    (Deterministic.CONSTANT, 8): (
        (-5.76202, -33.5258, -82.189, 256.289),
        (-5.22924, -23.0023, -24.646, 144.479),
        (-4.95007, -18.3959, -7.344, 94.872),
    ),
    (Deterministic.CONSTANT, 9): (
        (-5.99742, -37.6572, -87.365, 248.316),
        (-5.46697, -26.2057, -26.627, 176.382),
        (-5.18897, -21.1377, -9.484, 172.704),
    ),
    (Deterministic.CONSTANT, 10): (
        (-6.22103, -41.7154, -102.680, 389.330),
        (-5.69244, -29.4521, -30.994, 251.016),
        (-5.41533, -24.0006, -7.514, 163.049),
    ),
    (Deterministic.CONSTANT, 11): (
        (-6.43377, -46.0084, -106.809, 352.752),
        (-5.90714, -32.8336, -30.275, 249.994),
        (-5.63086, -26.9693, -4.083, 151.427),
    ),
    (Deterministic.CONSTANT, 12): (
        (-6.63790, -50.2095, -124.156, 579.622),
        (-6.11279, -36.2681, -32.505, 314.802),
        (-5.83724, -29.9864, -2.686, 184.116),
    ),
    (Deterministic.CONSTANT_TREND, 1): (
        (-3.95877, -9.0531, -28.428, -134.155),
        (-3.41049, -4.3904, -9.036, -45.374),
        (-3.12705, -2.5856, -3.925, -22.380),
    ),
    (Deterministic.CONSTANT_TREND, 2): (
        (-4.32762, -15.4387, -35.679, 0.0),
        (-3.78057, -9.5106, -12.074, 0.0),
        (-3.49631, -7.0815, -7.538, 21.892),
    ),
    (Deterministic.CONSTANT_TREND, 3): (
        (-4.66305, -18.7688, -49.793, 104.244),
        (-4.11890, -11.8922, -19.031, 77.332),
        (-3.83511, -9.0723, -8.504, 35.403),
    ),
    (Deterministic.CONSTANT_TREND, 4): (
        (-4.96940, -22.4694, -52.599, 51.314),
        (-4.42871, -14.5876, -18.228, 39.647),
        (-4.14633, -11.2500, -9.873, 54.109),
    ),
    (Deterministic.CONSTANT_TREND, 5): (
        (-5.25276, -26.2183, -59.631, 50.646),
        (-4.71537, -17.3569, -22.660, 91.359),
        (-4.43422, -13.6078, -10.238, 76.781),
    ),
    (Deterministic.CONSTANT_TREND, 6): (
        (-5.51727, -29.9760, -75.222, 202.253),
        (-4.98228, -20.3050, -25.224, 132.030),
        (-4.70233, -16.1253, -9.836, 94.272),
    ),
    (Deterministic.CONSTANT_TREND, 7): (
        (-5.76537, -33.9165, -84.312, 245.394),
        (-5.23299, -23.3328, -28.955, 182.342),
        (-4.95405, -18.7352, -10.168, 120.575),
    ),
    (Deterministic.CONSTANT_TREND, 8): (
        (-6.00003, -37.8892, -96.428, 335.920),
        (-5.46971, -26.4771, -31.034, 220.165),
        (-5.19183, -21.4328, -10.726, 157.955),
    ),
    (Deterministic.CONSTANT_TREND, 9): (
        (-6.22288, -41.9496, -109.881, 466.068),
        (-5.69447, -29.7152, -33.784, 273.002),
        (-5.41738, -24.2882, -8.584, 169.891),
    ),
    (Deterministic.CONSTANT_TREND, 10): (
        (-6.43551, -46.1151, -120.814, 566.823),
        (-5.90887, -33.0251, -37.208, 346.189),
        (-5.63255, -27.2042, -6.792, 177.666),
    ),
    (Deterministic.CONSTANT_TREND, 11): (
        (-6.63894, -50.4287, -128.997, 642.781),
        (-6.11404, -36.4610, -36.246, 348.554),
        (-5.83850, -30.1995, -5.163, 210.338),
    ),
    (Deterministic.CONSTANT_TREND, 12): (
        (-6.83488, -54.7119, -139.800, 736.376),
        (-6.31127, -39.9676, -37.021, 406.051),
        (-6.03650, -33.2381, -6.606, 317.776),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 1): (
        (-4.37113, -11.5882, -35.819, -334.047),
        (-3.83239, -5.9057, -12.490, -118.284),
        (-3.55326, -3.6596, -5.293, -63.559),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 2): (
        (-4.69276, -20.2284, -64.919, 88.884),
        (-4.15387, -13.3114, -28.402, 72.741),
        (-3.87346, -10.4637, -17.408, 66.313),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 3): (
        (-4.99071, -23.5873, -76.924, 184.782),
        (-4.45311, -15.7732, -32.316, 122.705),
        (-4.17280, -12.4909, -17.912, 83.285),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 4): (
        (-5.26780, -27.2836, -78.971, 137.871),
        (-4.73244, -18.4833, -31.875, 111.817),
        (-4.45268, -14.7199, -17.969, 101.920),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 5): (
        (-5.52826, -30.9051, -92.490, 248.096),
        (-4.99491, -21.2360, -37.685, 194.208),
        (-4.71587, -17.0820, -18.631, 136.672),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 6): (
        (-5.77379, -34.7010, -105.937, 393.991),
        (-5.24217, -24.2177, -39.153, 232.528),
        (-4.96397, -19.6064, -18.858, 174.919),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 7): (
        (-6.00609, -38.7383, -108.605, 365.208),
        (-5.47664, -27.3005, -39.498, 246.918),
        (-5.19921, -22.2617, -17.910, 208.494),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 8): (
        (-6.22758, -42.7154, -119.622, 421.395),
        (-5.69983, -30.4365, -44.300, 345.480),
        (-5.42320, -24.9686, -19.688, 274.462),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 9): (
        (-6.43933, -46.7581, -136.691, 651.380),
        (-5.91298, -33.7584, -42.686, 346.629),
        (-5.63704, -27.8965, -13.880, 236.975),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 10): (
        (-6.64235, -50.9783, -145.462, 752.228),
        (-6.11753, -37.056, -48.719, 473.905),
        (-5.84215, -30.8119, -14.938, 316.006),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 11): (
        (-6.83743, -55.2861, -152.651, 792.577),
        (-6.31396, -40.5507, -46.771, 487.185),
        (-6.03921, -33.8950, -9.122, 285.164),
    ),
    (Deterministic.CONSTANT_QUADRATIC_TREND, 12): (
        (-7.02582, -59.6037, -166.368, 989.879),
        (-6.50353, -44.0797, -47.242, 543.889),
        (-6.22941, -36.9673, -10.868, 418.414),
    ),
}


def _require_variables(variables: int, largest: int, source: str) -> int:
    if (
        isinstance(variables, bool)
        or not isinstance(variables, int)
        or not 1 <= variables <= largest
    ):
        raise EconometricsInputError(
            f"{source} tabulates 1 to {largest} series, got {variables!r}."
        )
    return variables


def mackinnon_critical_values(
    deterministic: Deterministic, variables: int, observations: int | None
) -> CriticalValues:
    """The 1, 5 and 10 percent critical values of a Dickey-Fuller or Engle-Granger test.

    Args:
        deterministic: The test regression's deterministic terms.
        variables: ``N``: 1 for a unit-root test, the series in the cointegrating
            regression for Engle-Granger.
        observations: ``T``, the observations in the test regression; ``None``
            for the asymptotic values.

    Raises:
        EconometricsInputError: If the case and ``N`` are not tabulated -- the
            no-constant case is published for ``N = 1`` only -- or ``T`` is not
            a positive count.
    """

    _require_variables(variables, 12, MACKINNON_2010)
    surface = _MACKINNON_2010.get((deterministic, variables))
    if surface is None:
        raise EconometricsInputError(
            f"{MACKINNON_2010} publishes no {deterministic.name} case for {variables} series."
        )
    if observations is not None and (
        isinstance(observations, bool) or not isinstance(observations, int) or observations < 1
    ):
        raise EconometricsInputError(
            f"observations must be a positive count, or None for the asymptotic values; got "
            f"{observations!r}."
        )
    values = {}
    for level, (asymptotic, first, second, third) in zip(LEVELS, surface, strict=True):
        if observations is None:
            values[level] = asymptotic
        else:
            u = 1.0 / observations
            values[level] = asymptotic + u * (first + u * (second + u * third))
    return CriticalValues(MappingProxyType(values), observations, MACKINNON_2010)


# MacKinnon (1994): the cut-off between the two approximations, and the range of each.
_TAU_STAR: Final[Mapping[Deterministic, tuple[float, ...]]] = {
    Deterministic.NONE: (-1.04, -1.53, -2.68, -3.09, -3.07, -3.77),
    Deterministic.CONSTANT: (-1.61, -2.62, -3.13, -3.47, -3.78, -3.93),
    Deterministic.CONSTANT_TREND: (-2.89, -3.19, -3.50, -3.65, -3.80, -4.36),
    Deterministic.CONSTANT_QUADRATIC_TREND: (-3.21, -3.51, -3.81, -3.83, -4.12, -4.63),
}
_TAU_MIN: Final[Mapping[Deterministic, tuple[float, ...]]] = {
    Deterministic.NONE: (-19.04, -19.62, -21.21, -23.25, -21.63, -25.74),
    Deterministic.CONSTANT: (-18.83, -18.86, -23.48, -28.07, -25.96, -23.27),
    Deterministic.CONSTANT_TREND: (-16.18, -21.15, -25.37, -26.63, -26.53, -26.18),
    Deterministic.CONSTANT_QUADRATIC_TREND: (-17.17, -21.1, -24.33, -24.03, -24.33, -28.22),
}
# The no-constant case for N = 1 has no upper end: its approximation holds throughout.
_TAU_MAX: Final[Mapping[Deterministic, tuple[float | None, ...]]] = {
    Deterministic.NONE: (None, 1.51, 0.86, 0.88, 1.05, 1.24),
    Deterministic.CONSTANT: (2.74, 0.92, 0.55, 0.61, 0.79, 1.0),
    Deterministic.CONSTANT_TREND: (0.7, 0.63, 0.71, 0.93, 1.19, 1.42),
    Deterministic.CONSTANT_QUADRATIC_TREND: (0.54, 0.79, 1.08, 1.43, 3.49, 1.92),
}
# (g0, g1, g2 times 100), as printed: the left-tail approximation.
_SMALL_P: Final[Mapping[Deterministic, tuple[tuple[float, float, float], ...]]] = {
    Deterministic.NONE: (
        (0.6344, 1.2378, 3.2496),
        (1.9129, 1.3857, 3.5322),
        (2.7648, 1.4502, 3.4186),
        (3.4336, 1.4835, 3.19),
        (4.0999, 1.5533, 3.59),
        (4.5388, 1.5344, 2.9807),
    ),
    Deterministic.CONSTANT: (
        (2.1659, 1.4412, 3.8269),
        (2.92, 1.5012, 3.9796),
        (3.4699, 1.4856, 3.164),
        (3.9673, 1.4777, 2.6315),
        (4.5509, 1.5338, 2.9545),
        (5.1399, 1.6036, 3.4445),
    ),
    Deterministic.CONSTANT_TREND: (
        (3.2512, 1.6047, 4.9588),
        (3.6646, 1.5419, 3.6448),
        (4.0983, 1.5173, 2.9898),
        (4.5844, 1.5338, 2.8796),
        (5.0722, 1.5634, 2.9472),
        (5.53, 1.5914, 3.0392),
    ),
    Deterministic.CONSTANT_QUADRATIC_TREND: (
        (4.0003, 1.658, 4.8288),
        (4.3534, 1.6016, 3.7947),
        (4.7343, 1.5768, 3.2396),
        (5.214, 1.6077, 3.3449),
        (5.6481, 1.6274, 3.3455),
        (5.9296, 1.5929, 2.8223),
    ),
}
# (g0, g1 times 10, g2 times 10, g3 times 100), as printed: the approximation above τ*.
_LARGE_P: Final[Mapping[Deterministic, tuple[tuple[float, float, float, float], ...]]] = {
    Deterministic.NONE: (
        (0.4797, 9.3557, -0.6999, 3.3066),
        (1.5578, 8.558, -2.083, -3.3549),
        (2.2268, 6.8093, -3.2362, -5.4448),
        (2.7654, 6.4502, -3.0811, -4.4946),
        (3.2684, 6.8051, -2.6778, -3.4972),
        (3.7268, 7.167, -2.3648, -2.8288),
    ),
    Deterministic.CONSTANT: (
        (1.7339, 9.3202, -1.2745, -1.0368),
        (2.1945, 6.4695, -2.9198, -4.2377),
        (2.5893, 4.5168, -3.6529, -5.0074),
        (3.0387, 4.5452, -3.3666, -4.1921),
        (3.5049, 5.2098, -2.9158, -3.3468),
        (3.9489, 5.8933, -2.5359, -2.721),
    ),
    Deterministic.CONSTANT_TREND: (
        (2.5261, 6.1654, -3.7956, -6.0285),
        (2.85, 5.272, -3.6622, -5.1695),
        (3.221, 5.255, -3.2685, -4.1501),
        (3.652, 5.9758, -2.7483, -3.2081),
        (4.0712, 6.6428, -2.3464, -2.546),
        (4.4735, 7.1757, -2.0681, -2.1196),
    ),
    Deterministic.CONSTANT_QUADRATIC_TREND: (
        (3.0778, 4.9529, -4.1477, -5.9359),
        (3.4713, 5.967, -3.2507, -4.2286),
        (3.8637, 6.7852, -2.6286, -3.1381),
        (4.2736, 7.6199, -2.1534, -2.4026),
        (4.6679, 8.2618, -1.822, -1.9147),
        (5.0009, 8.3735, -1.6994, -1.6928),
    ),
}


def _approximation(statistic: float, deterministic: Deterministic, variables: int) -> float:
    if statistic <= _TAU_STAR[deterministic][variables - 1]:
        g0, g1, g2 = _SMALL_P[deterministic][variables - 1]
        return normal_cdf(g0 + statistic * (g1 + statistic * (g2 / 100)))
    h0, h1, h2, h3 = _LARGE_P[deterministic][variables - 1]
    return normal_cdf(h0 + statistic * (h1 / 10 + statistic * (h2 / 10 + statistic * (h3 / 100))))


def mackinnon_p_value(statistic: float, deterministic: Deterministic, variables: int) -> PValue:
    """A Dickey-Fuller or Engle-Granger statistic's p-value, by MacKinnon's approximation.

    Args:
        statistic: The test's t-statistic.
        deterministic: The test regression's deterministic terms.
        variables: ``N``, as for :func:`mackinnon_critical_values`; 1 to 6.

    Raises:
        EconometricsInputError: If the statistic is not finite, or ``N`` is not
            1 to 6, the range the approximation is published for.
    """

    _require_variables(variables, 6, MACKINNON_1994)
    if isinstance(statistic, bool) or not isinstance(statistic, int | float):
        raise EconometricsInputError(f"A test statistic is a number, got {statistic!r}.")
    value = float(statistic)
    if value != value or value in (float("inf"), float("-inf")):
        raise EconometricsInputError(f"A test statistic is finite, got {statistic!r}.")
    low = _TAU_MIN[deterministic][variables - 1]
    high = _TAU_MAX[deterministic][variables - 1]
    if value < low:
        return PValue(_approximation(low, deterministic, variables), PBound.AT_MOST, MACKINNON_1994)
    if high is not None and value > high:
        return PValue(
            _approximation(high, deterministic, variables), PBound.AT_LEAST, MACKINNON_1994
        )
    return PValue(_approximation(value, deterministic, variables), None, MACKINNON_1994)


# Kwiatkowski, Phillips, Schmidt and Shin (1992), Table 1: (size, critical value).
_KPSS: Final[Mapping[Deterministic, tuple[tuple[float, float], ...]]] = {
    Deterministic.CONSTANT: ((0.10, 0.347), (0.05, 0.463), (0.025, 0.574), (0.01, 0.739)),
    Deterministic.CONSTANT_TREND: ((0.10, 0.119), (0.05, 0.146), (0.025, 0.176), (0.01, 0.216)),
}


def _kpss_table(deterministic: Deterministic) -> tuple[tuple[float, float], ...]:
    table = _KPSS.get(deterministic)
    if table is None:
        raise EconometricsInputError(
            f"KPSS tests level stationarity (CONSTANT) or trend stationarity "
            f"(CONSTANT_TREND), not {deterministic.name}."
        )
    return table


def kpss_critical_values(deterministic: Deterministic) -> CriticalValues:
    """KPSS's asymptotic critical values at 1, 2.5, 5 and 10 percent.

    Raises:
        EconometricsInputError: For a case other than level or trend stationarity.
    """

    table = _kpss_table(deterministic)
    return CriticalValues(MappingProxyType(dict(sorted(table))), None, KPSS_1992)


def kpss_p_value(statistic: float, deterministic: Deterministic) -> PValue:
    """A KPSS statistic's p-value, interpolated linearly within the table, bounded beyond it.

    Raises:
        EconometricsInputError: For a case other than level or trend
            stationarity, or a statistic that is not a finite, non-negative number.
    """

    table = _kpss_table(deterministic)
    return _interpolated(statistic, table, KPSS_1992, "A KPSS statistic")


def _interpolated(
    statistic: float, table: tuple[tuple[float, float], ...], source: str, what: str
) -> PValue:
    """The size at ``statistic`` within ``table``, ``(size, critical value)`` ascending in value."""

    if isinstance(statistic, bool) or not isinstance(statistic, int | float):
        raise EconometricsInputError(f"A test statistic is a number, got {statistic!r}.")
    value = float(statistic)
    if not value >= 0.0 or value == float("inf"):
        raise EconometricsInputError(f"{what} is finite and non-negative, got {statistic!r}.")
    (largest_size, smallest_value), *_, (smallest_size, largest_value) = table
    if value <= smallest_value:
        return PValue(largest_size, PBound.AT_LEAST if value < smallest_value else None, source)
    if value >= largest_value:
        return PValue(smallest_size, PBound.AT_MOST if value > largest_value else None, source)
    for (size_low, at_low), (size_high, at_high) in itertools.pairwise(table):
        if at_low <= value <= at_high:
            share = (value - at_low) / (at_high - at_low)
            return PValue(size_low + share * (size_high - size_low), None, source)
    raise AssertionError("unreachable: the table brackets every statistic between its ends")


# MacKinnon, Haug and Michelis (1996), Tables 1-5, as printed: for p - r = 1 to 12,
# the critical value at 1, 2, 5, 10 and 20 percent. Generated from the paper.
_MHM_1996: Final[Mapping[tuple[JohansenCase, JohansenStatistic], tuple[tuple[float, ...], ...]]] = {
    (JohansenCase.NONE, JohansenStatistic.MAXIMUM_EIGENVALUE): (
        (6.94, 5.71, 4.13, 2.98, 1.88),
        (15.09, 13.45, 11.22, 9.47, 7.62),
        (22.25, 20.38, 17.80, 15.72, 13.45),
        (29.06, 27.02, 24.16, 21.84, 19.28),
        (35.72, 33.52, 30.44, 27.92, 25.10),
        (42.23, 39.90, 36.63, 33.93, 30.91),
        (48.66, 46.22, 42.77, 39.91, 36.70),
        (55.04, 52.48, 48.88, 45.89, 42.52),
        (61.35, 58.71, 54.97, 51.85, 48.32),
        (67.65, 64.92, 61.03, 57.80, 54.14),
        (73.89, 71.07, 67.08, 63.73, 59.94),
        (80.12, 77.22, 73.09, 69.65, 65.73),
    ),
    (JohansenCase.NONE, JohansenStatistic.TRACE): (
        (6.94, 5.71, 4.13, 2.98, 1.89),
        (16.36, 14.65, 12.32, 10.47, 8.51),
        (29.51, 27.33, 24.28, 21.78, 19.01),
        (46.57, 43.92, 40.17, 37.03, 33.49),
        (67.64, 64.53, 60.06, 56.28, 51.96),
        (92.71, 89.13, 83.94, 79.53, 74.44),
        (121.74, 117.70, 111.78, 106.74, 100.88),
        (154.80, 150.27, 143.67, 138.00, 131.38),
        (191.83, 186.81, 179.52, 173.23, 165.84),
        (232.84, 227.41, 219.41, 212.47, 204.30),
        (278.00, 272.01, 263.26, 255.68, 246.78),
        (326.96, 320.57, 311.13, 302.90, 293.21),
    ),
    (JohansenCase.RESTRICTED_CONSTANT, JohansenStatistic.MAXIMUM_EIGENVALUE): (
        (12.76, 11.23, 9.16, 7.56, 5.88),
        (20.16, 18.37, 15.89, 13.91, 11.77),
        (27.07, 25.07, 22.30, 20.05, 17.59),
        (33.73, 31.59, 28.59, 26.12, 23.40),
        (40.29, 38.01, 34.81, 32.17, 29.21),
        (46.75, 44.35, 40.96, 38.16, 35.02),
        (53.12, 50.61, 47.07, 44.13, 40.81),
        (59.51, 56.89, 53.19, 50.11, 46.64),
        (65.79, 63.07, 59.24, 56.05, 52.44),
        (72.10, 69.26, 65.30, 61.99, 58.25),
        (78.29, 75.42, 71.33, 67.93, 64.05),
        (84.51, 81.56, 77.38, 73.85, 69.86),
    ),
    (JohansenCase.RESTRICTED_CONSTANT, JohansenStatistic.TRACE): (
        (12.76, 11.23, 9.16, 7.56, 5.88),
        (25.08, 23.07, 20.26, 17.98, 15.48),
        (41.20, 38.72, 35.19, 32.27, 28.99),
        (61.27, 58.31, 54.08, 50.53, 46.46),
        (85.34, 81.90, 76.97, 72.77, 67.94),
        (113.42, 109.51, 103.84, 99.02, 93.42),
        (145.40, 141.03, 134.68, 129.23, 122.85),
        (181.51, 176.70, 169.61, 163.50, 156.36),
        (221.45, 216.17, 208.45, 201.69, 193.80),
        (265.53, 259.75, 251.27, 243.96, 235.28),
        (313.75, 307.37, 298.17, 290.17, 280.73),
        (365.64, 358.96, 348.99, 340.38, 330.19),
    ),
    (JohansenCase.UNRESTRICTED_CONSTANT, JohansenStatistic.MAXIMUM_EIGENVALUE): (
        (6.63, 5.41, 3.84, 2.71, 1.64),
        (18.52, 16.72, 14.26, 12.30, 10.19),
        (25.86, 23.88, 21.13, 18.89, 16.44),
        (32.71, 30.58, 27.58, 25.12, 22.41),
        (39.37, 37.08, 33.88, 31.24, 28.30),
        (45.87, 43.47, 40.08, 37.28, 34.15),
        (52.31, 49.78, 46.23, 43.29, 39.98),
        (58.67, 56.06, 52.36, 49.29, 45.82),
        (64.99, 62.28, 58.43, 55.24, 51.63),
        (71.26, 68.46, 64.51, 61.20, 57.45),
        (77.49, 74.63, 70.53, 67.13, 63.26),
        (83.70, 80.79, 76.58, 73.06, 69.08),
    ),
    (JohansenCase.UNRESTRICTED_CONSTANT, JohansenStatistic.TRACE): (
        (6.63, 5.41, 3.84, 2.71, 1.64),
        (19.94, 18.07, 15.50, 13.43, 11.19),
        (35.46, 33.12, 29.80, 27.07, 24.01),
        (54.68, 51.87, 47.86, 44.49, 40.65),
        (77.82, 74.54, 69.82, 65.82, 61.21),
        (104.96, 101.20, 95.75, 91.11, 85.74),
        (135.97, 131.76, 125.61, 120.37, 114.23),
        (171.09, 166.38, 159.53, 153.63, 146.74),
        (210.06, 204.93, 197.37, 190.88, 183.24),
        (253.24, 247.54, 239.25, 232.11, 223.72),
        (300.29, 294.13, 285.14, 277.38, 268.17),
        (351.25, 344.66, 334.98, 326.53, 316.63),
    ),
    (JohansenCase.RESTRICTED_TREND, JohansenStatistic.MAXIMUM_EIGENVALUE): (
        (16.55, 14.85, 12.52, 10.67, 8.69),
        (23.97, 22.05, 19.39, 17.23, 14.88),
        (30.83, 28.75, 25.82, 23.44, 20.81),
        (37.49, 35.25, 32.12, 29.54, 26.67),
        (44.02, 41.66, 38.33, 35.58, 32.52),
        (50.47, 47.99, 44.50, 41.60, 38.34),
        (56.85, 54.26, 50.59, 47.56, 44.15),
        (63.17, 60.49, 56.71, 53.55, 49.98),
        (69.44, 66.68, 62.75, 59.49, 55.79),
        (75.69, 72.86, 68.81, 65.44, 61.61),
        (81.94, 79.00, 74.84, 71.36, 67.41),
        (88.11, 85.15, 80.87, 77.30, 73.23),
    ),
    (JohansenCase.RESTRICTED_TREND, JohansenStatistic.TRACE): (
        (16.55, 14.85, 12.52, 10.67, 8.69),
        (31.16, 28.95, 25.87, 23.34, 20.54),
        (49.36, 46.69, 42.91, 39.75, 36.17),
        (71.47, 68.36, 63.88, 60.09, 55.73),
        (97.60, 93.99, 88.80, 84.38, 79.26),
        (127.71, 123.61, 117.71, 112.65, 106.77),
        (161.72, 157.17, 150.56, 144.87, 138.24),
        (199.81, 194.80, 187.47, 181.16, 173.75),
        (241.74, 236.27, 228.31, 221.36, 213.20),
        (287.87, 281.95, 273.19, 265.63, 256.70),
        (337.97, 331.58, 322.06, 313.86, 304.15),
        (392.01, 385.14, 374.91, 366.11, 355.65),
    ),
    (JohansenCase.UNRESTRICTED_TREND, JohansenStatistic.MAXIMUM_EIGENVALUE): (
        (6.63, 5.41, 3.84, 2.71, 1.64),
        (21.74, 19.82, 17.15, 15.00, 12.66),
        (29.26, 27.16, 24.25, 21.87, 19.25),
        (36.19, 33.95, 30.82, 28.24, 25.38),
        (42.86, 40.49, 37.16, 34.42, 31.36),
        (49.41, 46.93, 43.42, 40.53, 37.28),
        (55.81, 53.25, 49.58, 46.56, 43.15),
        (62.17, 59.51, 55.73, 52.58, 49.02),
        (68.50, 65.73, 61.81, 58.53, 54.85),
        (74.74, 71.91, 67.90, 64.53, 60.70),
        (81.07, 78.14, 73.94, 70.46, 66.51),
        (87.23, 84.24, 79.97, 76.41, 72.35),
    ),
    (JohansenCase.UNRESTRICTED_TREND, JohansenStatistic.TRACE): (
        (6.63, 5.41, 3.84, 2.71, 1.64),
        (23.15, 21.17, 18.40, 16.16, 13.71),
        (41.08, 38.56, 35.01, 32.06, 28.74),
        (62.52, 59.54, 55.24, 51.65, 47.53),
        (87.78, 84.32, 79.34, 75.10, 70.20),
        (116.99, 113.05, 107.34, 102.47, 96.81),
        (150.08, 145.69, 139.28, 133.79, 127.37),
        (187.20, 182.31, 175.16, 169.07, 161.90),
        (228.23, 222.91, 215.12, 208.36, 200.43),
        (273.37, 267.54, 259.02, 251.63, 242.94),
        (322.41, 316.14, 306.90, 298.89, 289.45),
        (375.30, 368.52, 358.72, 350.12, 339.88),
    ),
}

#: The most ``p - r`` the tables reach.
_MOST_JOHANSEN_DIMENSION: Final = 12


def _johansen_table(
    case: JohansenCase, statistic: JohansenStatistic, dimension: int
) -> tuple[tuple[float, float], ...]:
    """``(size, critical value)``, largest size first: the critical values ascending."""

    if not isinstance(case, JohansenCase):
        raise EconometricsInputError(f"case must be a JohansenCase, got {case!r}.")
    if not isinstance(statistic, JohansenStatistic):
        raise EconometricsInputError(f"statistic must be a JohansenStatistic, got {statistic!r}.")
    if (
        isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or not 1 <= dimension <= _MOST_JOHANSEN_DIMENSION
    ):
        raise EconometricsInputError(
            f"MacKinnon, Haug and Michelis tabulate p - r from 1 to "
            f"{_MOST_JOHANSEN_DIMENSION}, got {dimension!r}."
        )
    row = _MHM_1996[case, statistic][dimension - 1]
    return tuple(reversed(tuple(zip(JOHANSEN_LEVELS, row, strict=True))))


def johansen_critical_values(
    case: JohansenCase, statistic: JohansenStatistic, dimension: int
) -> CriticalValues:
    """Johansen's asymptotic critical values at 1, 2, 5, 10 and 20 percent.

    Args:
        case: The VECM's deterministic terms.
        statistic: The trace or the maximum-eigenvalue statistic.
        dimension: ``p - r``: the series less the rank under the null, 1 to 12.

    Raises:
        EconometricsInputError: For a dimension outside 1 to 12, or a case or
            statistic of another kind.
    """

    table = _johansen_table(case, statistic, dimension)
    return CriticalValues(MappingProxyType(dict(sorted(table))), None, MHM_1996)


def johansen_p_value(
    statistic: float, case: JohansenCase, kind: JohansenStatistic, dimension: int
) -> PValue:
    """A Johansen statistic's p-value, interpolated linearly within the table, bounded beyond it.

    Raises:
        EconometricsInputError: For a dimension outside 1 to 12, or a statistic
            that is not a finite, non-negative number.
    """

    table = _johansen_table(case, kind, dimension)
    return _interpolated(statistic, table, MHM_1996, "A Johansen statistic")
