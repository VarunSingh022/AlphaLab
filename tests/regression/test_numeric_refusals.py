"""Numbers that are not numbers are refused; exact figures stay exact.

Four v3.10 ledger items, pinned by behaviour:

* **NUM-001** -- the statistics authority accepted ``nan`` and infinity:
  ``ranks([1, nan, 0.5, 2])`` returned ``(1, 2, 3, 4)``, a ranking that was wrong
  and changed with the input order, and a mean or a variance carried ``nan``
  silently into whatever table it reached. Every function refuses them now,
  naming the position.
* **NUM-002** -- ``pearson_correlation`` raised ``ZeroDivisionError`` for series
  of magnitude ``1e-160`` and ``OverflowError`` for ``1e160``, because the
  product of the two variances under- or overflowed; and the result could leave
  ``[-1, 1]`` by rounding.
* **NUM-008** -- the percentage and impact slippage models and the percentage
  and per-share commission models quantized to four decimal places, so a
  percentage slippage on an instrument priced below a cent rounded to nothing;
  and the impact model divided by a constant ``1000`` nobody could see.
* **PER-002** -- ``serialize`` wrote ``NaN`` and ``Infinity``, tokens strict JSON
  parsers reject and that do not round-trip to an equal value.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal

import pytest

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import (
    bucket_index,
    linear_regression,
    mean,
    median,
    pearson_correlation,
    percentile,
    rank_correlation,
    ranks,
    sample_covariance,
    sample_variance,
    standard_deviation,
    standardize,
    winsorize,
)
from alphalab.core.enums import Side
from alphalab.execution.commission import PercentageCommission, PerShareCommission
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.slippage import MarketImpactSlippage, PercentageSlippage
from alphalab.persistence.exceptions import SerializationError
from alphalab.persistence.serializer import serialize

# --------------------------------------------------------------------------- #
# NUM-001
# --------------------------------------------------------------------------- #

_SERIES = [1.0, 2.0, 0.5, 3.0]

STATISTICS: list[tuple[str, Callable[[list[float]], object]]] = [
    ("mean", mean),
    ("sample_variance", sample_variance),
    ("sample_covariance", lambda values: sample_covariance(values, _SERIES)),
    ("standard_deviation", standard_deviation),
    ("median", median),
    ("percentile", lambda values: percentile(values, 0.5)),
    ("ranks", ranks),
    ("pearson_correlation", lambda values: pearson_correlation(values, _SERIES)),
    ("rank_correlation", lambda values: rank_correlation(values, _SERIES)),
    ("linear_regression", lambda values: linear_regression(values, _SERIES)),
    ("standardize", standardize),
    ("winsorize", lambda values: winsorize(values, 0.1, 0.9)),
    ("bucket_index", lambda values: bucket_index(values, 2)),
]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize(("name", "statistic"), STATISTICS, ids=[name for name, _ in STATISTICS])
def test_every_statistic_refuses_a_non_finite_observation_by_position(
    name: str, statistic: Callable[[list[float]], object], bad: float
) -> None:
    values = [1.0, bad, 0.5, 2.0]

    with pytest.raises(AlphaLabValidationError, match=r"\[1\]"):
        statistic(values)


def test_the_ranking_that_depended_on_input_order_is_refused_instead() -> None:
    """The audit's probe: this returned ``(1, 2, 3, 4)`` in v3.9."""

    with pytest.raises(AlphaLabValidationError, match="nan"):
        ranks([1.0, math.nan, 0.5, 2.0])


# --------------------------------------------------------------------------- #
# NUM-002
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("scale", [1e-160, 1e160, 1.0], ids=["tiny", "huge", "unit"])
def test_correlation_is_defined_at_any_representable_magnitude(scale: float) -> None:
    xs = [value * scale for value in (1.0, 2.0, 3.0, 4.0)]
    ys = [value * scale for value in (2.0, 4.0, 5.0, 4.0)]

    unit = pearson_correlation([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 5.0, 4.0])

    assert pearson_correlation(xs, ys) == pytest.approx(unit, rel=1e-12)
    assert pearson_correlation(xs, [2.0 * x for x in xs]) == 1.0


def test_a_perfect_correlation_never_leaves_the_unit_interval() -> None:
    xs = [0.1 * index for index in range(1, 50)]

    assert -1.0 <= pearson_correlation(xs, xs) <= 1.0
    assert -1.0 <= pearson_correlation(xs, [-x for x in xs]) <= 1.0


# --------------------------------------------------------------------------- #
# NUM-008
# --------------------------------------------------------------------------- #


def test_percentage_slippage_below_a_cent_is_not_rounded_away() -> None:
    concession = PercentageSlippage(Decimal("0.001")).calculate(
        Decimal("1000000"), Decimal("0.0001234"), Side.BUY
    )

    assert concession == Decimal("0.0000001234")


def test_impact_slippage_is_scaled_against_a_stated_size() -> None:
    model = MarketImpactSlippage(Decimal("0.01"), reference_quantity=Decimal("5000"))

    # 0.01 of the price at the reference size, linear in size: 2,500 is half.
    assert model.calculate(Decimal("2500"), Decimal("40"), Side.SELL) == Decimal("0.2")
    with pytest.raises(ExecutionValidationError, match="reference_quantity"):
        MarketImpactSlippage(Decimal("0.01"), reference_quantity=Decimal("0"))


def test_commissions_are_exact_and_rounded_once_by_the_settlement_currency() -> None:
    assert PerShareCommission(Decimal("0.00035")).calculate(Decimal("3"), Decimal("10")) == Decimal(
        "0.00105"
    )
    assert PercentageCommission(Decimal("0.00001")).calculate(
        Decimal("7"), Decimal("0.0123")
    ) == Decimal("8.61E-7")


# --------------------------------------------------------------------------- #
# PER-002
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
def test_a_non_finite_float_is_refused_at_write_time(bad: float) -> None:
    with pytest.raises(SerializationError):
        serialize({"sharpe_ratio": bad})


def test_what_is_written_is_strict_json() -> None:
    import json

    written = serialize({"value": 1.5, "amount": Decimal("100.00")})

    def refuse(token: str) -> float:
        raise AssertionError(f"non-standard token {token!r}")

    assert json.loads(written, parse_constant=refuse) == {"amount": "100.00", "value": 1.5}
