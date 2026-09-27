"""Risk budgets along five dimensions, reconciled and judged.

Exposure lines are a test-local frozen dataclass: the budget reads them through
a structural protocol and never imports ``alphalab.portfolio``. Every
contribution is recomputed here from its definition -- ``(value / capital) *
(C w)_i / sigma`` -- rather than read back from the implementation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

import pytest

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_budget import (
    BudgetBasis,
    BudgetLimit,
    BudgetStatus,
    RiskBudget,
    RiskDimension,
    evaluate_risk_budget,
)
from alphalab.analytics.risk_model import Classification, CovarianceMatrix

ROWS = ((0.0004, 0.0002, 0.0001), (0.0002, 0.0003, 0.0001), (0.0001, 0.0001, 0.0005))
COVARIANCE = CovarianceMatrix.from_rows(
    ("AAPL", "MSFT", "SAP"), ROWS, currency="USD", period="1D", source="unit", observations=250
)
CAPITAL = Decimal("46700.00")


@dataclass(frozen=True, slots=True)
class Line:
    strategy_id: str
    asset_id: str
    currency: str
    market_value: Decimal
    reporting_value: Decimal


LINES = (
    Line("MOM", "AAPL", "USD", Decimal("20000.00"), Decimal("20000.00")),
    Line("MOM", "SAP", "EUR", Decimal("6000.00"), Decimal("6600.00")),
    Line("MR", "AAPL", "USD", Decimal("-8000.00"), Decimal("-8000.00")),
    Line("MR", "MSFT", "USD", Decimal("12000.00"), Decimal("12000.00")),
)
SECTORS = Classification(
    "sector", {"AAPL": "Tech", "MSFT": "Tech", "SAP": "Software"}, "registry", None
)
COUNTRIES = Classification("country", {"AAPL": "US", "MSFT": "US", "SAP": "DE"}, "vendor x", None)


def budget(*limits: BudgetLimit, tolerance: float = 1e-12) -> RiskBudget:
    return RiskBudget("unit budget", limits, tolerance)


def evaluate(*limits: BudgetLimit, lines: tuple[Line, ...] = LINES, tolerance: float = 1e-12):  # type: ignore[no-untyped-def]
    return evaluate_risk_budget(
        lines,
        capital=CAPITAL,
        reporting_currency="USD",
        covariance=COVARIANCE,
        budget=budget(*limits, tolerance=tolerance),
        classifications=(SECTORS, COUNTRIES),
    )


def _reference() -> tuple[float, dict[tuple[str, str], float]]:
    assets = ("AAPL", "MSFT", "SAP")
    weights = dict.fromkeys(assets, 0.0)
    for line in LINES:
        weights[line.asset_id] += float(line.reporting_value / CAPITAL)
    exposure = {
        a: sum(ROWS[i][j] * weights[b] for j, b in enumerate(assets)) for i, a in enumerate(assets)
    }
    sigma = math.sqrt(sum(weights[a] * exposure[a] for a in assets))
    return sigma, {
        (line.strategy_id, line.asset_id): float(line.reporting_value / CAPITAL)
        * exposure[line.asset_id]
        / sigma
        for line in LINES
    }


# --------------------------------------------------------------------------- #
# Decomposition and reconciliation
# --------------------------------------------------------------------------- #


def test_every_line_contribution_is_its_weight_times_its_asset_s_marginal() -> None:
    sigma, expected = _reference()
    report = evaluate()

    assert report.volatility == pytest.approx(sigma, rel=1e-14)
    for line in report.lines:
        assert line.contribution == pytest.approx(
            expected[(line.strategy_id, line.asset_id)], rel=1e-12
        )


def test_every_dimension_partitions_the_same_volatility() -> None:
    report = evaluate()

    assert [d.dimension for d in report.dimensions] == list(RiskDimension)
    for dimension in report.dimensions:
        assert abs(dimension.residual) < 1e-15
        assert math.fsum(bucket.share for bucket in dimension.buckets) == pytest.approx(
            1.0, abs=1e-12
        )
        assert sum((bucket.net_exposure for bucket in dimension.buckets), Decimal(0)) == Decimal(
            "30600.00"
        )


def test_asset_buckets_agree_with_the_euler_decomposition() -> None:
    report = evaluate()
    assets = report.dimension(RiskDimension.ASSET)

    for bucket in assets.buckets:
        assert bucket.contribution == pytest.approx(report.risk.total[bucket.bucket], rel=1e-12)


def test_strategy_buckets_keep_opposing_holdings_apart() -> None:
    report = evaluate()
    strategies = report.dimension(RiskDimension.STRATEGY)
    mom = strategies.bucket("MOM")
    mr = strategies.bucket("MR")
    short = next(
        line for line in report.lines if line.strategy_id == "MR" and line.asset_id == "AAPL"
    )

    assert mom is not None and mr is not None
    assert mom.net_exposure == Decimal("26600.00")
    assert mr.net_exposure == Decimal("4000.00")
    assert mr.gross_exposure == Decimal("20000.00")
    assert short.contribution < 0.0


def test_the_currency_dimension_keeps_native_and_reporting_exposure_apart() -> None:
    currencies = evaluate().dimension(RiskDimension.CURRENCY)
    euro = currencies.bucket("EUR")

    assert euro is not None
    assert euro.native_exposure == Decimal("6000.00")
    assert euro.net_exposure == Decimal("6600.00")
    assert all(
        bucket.native_exposure is None
        for bucket in evaluate().dimension(RiskDimension.SECTOR).buckets
    )


def test_classified_dimensions_name_their_source() -> None:
    report = evaluate()

    assert report.dimension(RiskDimension.SECTOR).source == "registry"
    assert report.dimension(RiskDimension.SECTOR).classification_id == SECTORS.classification_id
    assert report.dimension(RiskDimension.COUNTRY).bucket("DE") is not None
    assert report.dimension(RiskDimension.ASSET).source == "line"


def test_without_classifications_only_the_intrinsic_dimensions_are_reported() -> None:
    report = evaluate_risk_budget(
        LINES,
        capital=CAPITAL,
        reporting_currency="USD",
        covariance=COVARIANCE,
        budget=budget(
            BudgetLimit(RiskDimension.ASSET, "AAPL", BudgetBasis.RELATIVE, 0.9, None, None)
        ),
        classifications=(),
    )

    assert [d.dimension for d in report.dimensions] == [
        RiskDimension.ASSET,
        RiskDimension.STRATEGY,
        RiskDimension.CURRENCY,
    ]
    with pytest.raises(AnalyticsValidationError, match="no SECTOR dimension"):
        report.dimension(RiskDimension.SECTOR)


# --------------------------------------------------------------------------- #
# Judging limits
# --------------------------------------------------------------------------- #


def test_a_relative_limit_is_judged_against_the_share_of_risk() -> None:
    report = evaluate(
        BudgetLimit(RiskDimension.STRATEGY, "MOM", BudgetBasis.RELATIVE, 0.8, None, 0.6)
    )
    check = report.checks[0]
    share = report.dimension(RiskDimension.STRATEGY).bucket("MOM")

    assert share is not None
    assert check.used == share.share
    assert check.status is BudgetStatus.BREACHED
    assert check.remaining == pytest.approx(0.8 - share.share)
    assert check.deviation == pytest.approx(share.share - 0.6)
    assert report.breaches == (check,)


def test_an_absolute_limit_is_judged_against_the_contribution() -> None:
    report = evaluate(
        BudgetLimit(RiskDimension.SECTOR, "Tech", BudgetBasis.ABSOLUTE, 0.01, None, None)
    )
    tech = report.dimension(RiskDimension.SECTOR).bucket("Tech")

    assert tech is not None
    assert report.checks[0].used == tech.contribution
    assert report.checks[0].status is BudgetStatus.WITHIN


def test_a_floor_below_which_a_bucket_is_under_budget() -> None:
    report = evaluate(
        BudgetLimit(RiskDimension.COUNTRY, "DE", BudgetBasis.RELATIVE, None, 0.5, None)
    )

    assert report.checks[0].status is BudgetStatus.BELOW_MINIMUM
    assert report.checks[0].above_minimum is not None and report.checks[0].above_minimum < 0


def test_the_tolerance_decides_at_the_limit_and_nothing_else_does() -> None:
    share = evaluate().dimension(RiskDimension.ASSET).bucket("SAP")
    assert share is not None
    exact = BudgetLimit(RiskDimension.ASSET, "SAP", BudgetBasis.RELATIVE, share.share, None, None)
    nudged_below = BudgetLimit(
        RiskDimension.ASSET, "SAP", BudgetBasis.RELATIVE, share.share - 1e-9, None, None
    )

    assert evaluate(exact, tolerance=0.0).checks[0].status is BudgetStatus.AT_LIMIT
    assert evaluate(nudged_below, tolerance=0.0).checks[0].status is BudgetStatus.BREACHED
    assert evaluate(nudged_below, tolerance=1e-8).checks[0].status is BudgetStatus.AT_LIMIT


def test_a_limit_on_a_bucket_holding_nothing_uses_nothing() -> None:
    report = evaluate(
        BudgetLimit(RiskDimension.SECTOR, "Energy", BudgetBasis.RELATIVE, 0.1, None, None)
    )

    assert report.checks[0].present is False
    assert report.checks[0].used == 0.0
    assert report.checks[0].status is BudgetStatus.WITHIN


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_a_sector_limit_without_a_sector_classification_is_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="cannot measure"):
        evaluate_risk_budget(
            LINES,
            capital=CAPITAL,
            reporting_currency="USD",
            covariance=COVARIANCE,
            budget=budget(
                BudgetLimit(RiskDimension.SECTOR, "Tech", BudgetBasis.RELATIVE, 0.5, None, None)
            ),
            classifications=(),
        )


def test_an_unclassified_holding_is_refused_never_put_in_a_default_bucket() -> None:
    partial = Classification("sector", {"AAPL": "Tech", "MSFT": "Tech"}, "registry", None)
    with pytest.raises(AnalyticsValidationError, match=r"needs a sector for \['SAP'\]"):
        evaluate_risk_budget(
            LINES,
            capital=CAPITAL,
            reporting_currency="USD",
            covariance=COVARIANCE,
            budget=budget(),
            classifications=(partial,),
        )


def test_classifications_outside_the_five_dimensions_or_twice_are_refused() -> None:
    industries = Classification("industry", {"AAPL": "x", "MSFT": "x", "SAP": "y"}, "s", None)
    with pytest.raises(AnalyticsValidationError, match="has no risk dimension"):
        evaluate_risk_budget(
            LINES,
            capital=CAPITAL,
            reporting_currency="USD",
            covariance=COVARIANCE,
            budget=budget(),
            classifications=(industries,),
        )
    with pytest.raises(AnalyticsValidationError, match="Two sector"):
        evaluate_risk_budget(
            LINES,
            capital=CAPITAL,
            reporting_currency="USD",
            covariance=COVARIANCE,
            budget=budget(),
            classifications=(SECTORS, SECTORS),
        )


def test_a_strategy_limit_needs_every_line_attributed() -> None:
    unattributed = (*LINES[:3], Line("", "MSFT", "USD", Decimal("12000.00"), Decimal("12000.00")))
    report = evaluate_risk_budget(
        unattributed,
        capital=CAPITAL,
        reporting_currency="USD",
        covariance=COVARIANCE,
        budget=budget(),
        classifications=(),
    )

    assert RiskDimension.STRATEGY not in [d.dimension for d in report.dimensions]
    with pytest.raises(AnalyticsValidationError, match="every line attributed"):
        evaluate_risk_budget(
            unattributed,
            capital=CAPITAL,
            reporting_currency="USD",
            covariance=COVARIANCE,
            budget=budget(
                BudgetLimit(RiskDimension.STRATEGY, "MOM", BudgetBasis.RELATIVE, 0.5, None, None)
            ),
            classifications=(),
        )


def test_a_covariance_in_another_currency_is_refused() -> None:
    euros = CovarianceMatrix.from_rows(
        ("AAPL", "MSFT", "SAP"), ROWS, currency="EUR", period="1D", source="unit", observations=250
    )
    with pytest.raises(AnalyticsValidationError, match="measured in 'EUR'"):
        evaluate_risk_budget(
            LINES,
            capital=CAPITAL,
            reporting_currency="USD",
            covariance=euros,
            budget=budget(),
            classifications=(),
        )


def test_degenerate_books_are_refused() -> None:
    def attempt(lines: tuple[Line, ...], capital: Decimal = CAPITAL) -> None:
        evaluate_risk_budget(
            lines,
            capital=capital,
            reporting_currency="USD",
            covariance=COVARIANCE,
            budget=budget(),
            classifications=(),
        )

    with pytest.raises(AnalyticsValidationError, match="no exposure"):
        attempt(())
    with pytest.raises(AnalyticsValidationError, match="two lines"):
        attempt((LINES[0], LINES[0]))
    with pytest.raises(AnalyticsValidationError, match="positive capital"):
        attempt(LINES, Decimal("0"))
    with pytest.raises(AnalyticsValidationError, match="no risk to decompose"):
        attempt((Line("MOM", "AAPL", "USD", Decimal("0"), Decimal("0")),))
    with pytest.raises(AnalyticsValidationError, match="not covered"):
        attempt((Line("MOM", "TSLA", "USD", Decimal("1"), Decimal("1")),))


@pytest.mark.parametrize(
    ("maximum", "minimum", "target", "message"),
    [
        (None, None, None, "budgets nothing"),
        (0.2, 0.5, None, "above maximum"),
        (0.5, 0.2, 0.6, "outside its own limits"),
        (float("nan"), None, None, "finite"),
    ],
)
def test_malformed_limits_are_refused(
    maximum: float | None, minimum: float | None, target: float | None, message: str
) -> None:
    with pytest.raises(AnalyticsValidationError, match=message):
        BudgetLimit(RiskDimension.ASSET, "AAPL", BudgetBasis.RELATIVE, maximum, minimum, target)


def test_a_budget_states_each_limit_once_and_a_real_tolerance() -> None:
    limit = BudgetLimit(RiskDimension.ASSET, "AAPL", BudgetBasis.RELATIVE, 0.5, None, None)
    with pytest.raises(AnalyticsValidationError, match="stated twice"):
        RiskBudget("b", (limit, limit), 0.0)
    with pytest.raises(AnalyticsValidationError, match="magnitude"):
        RiskBudget("b", (limit,), -1e-9)


# --------------------------------------------------------------------------- #
# Identity and determinism
# --------------------------------------------------------------------------- #


def test_the_same_inputs_give_the_same_report_and_identity() -> None:
    limit = BudgetLimit(RiskDimension.CURRENCY, "EUR", BudgetBasis.RELATIVE, 0.2, None, None)

    assert evaluate(limit) == evaluate(limit)
    assert evaluate(limit).report_id == evaluate(limit).report_id
    assert evaluate(limit).report_id != evaluate(limit, tolerance=1e-6).report_id


def test_line_order_does_not_reach_the_report() -> None:
    forward = evaluate(lines=LINES)
    backward = evaluate(lines=tuple(reversed(LINES)))

    assert forward == backward
