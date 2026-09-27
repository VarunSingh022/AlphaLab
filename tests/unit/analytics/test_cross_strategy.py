"""Cross-strategy risk: every comparison named by what it compares, and checked by hand."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from decimal import Decimal

import pytest

from alphalab.analytics.cross_strategy import (
    CommonDimension,
    StrategyReturns,
    capital_concentration,
    capital_overlap,
    common_exposures,
    factor_crowding,
    strategy_overlap,
    strategy_return_correlation,
)
from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import Classification, FactorLoadings
from alphalab.common.statistics import pearson_correlation


@dataclass(frozen=True, slots=True)
class Line:
    strategy_id: str
    asset_id: str
    currency: str
    market_value: Decimal
    reporting_value: Decimal


def line(strategy: str, asset: str, value: str, currency: str = "USD") -> Line:
    return Line(strategy, asset, currency, Decimal(value), Decimal(value))


BOOK = (
    line("MOM", "AAPL", "100"),
    line("MOM", "MSFT", "50"),
    line("MOM", "SAP", "30", "EUR"),
    line("MR", "AAPL", "-40"),
    line("MR", "MSFT", "20"),
    line("MR", "XOM", "60"),
    line("CARRY", "SAP", "10", "EUR"),
)


# --------------------------------------------------------------------------- #
# Return correlation
# --------------------------------------------------------------------------- #


def test_return_correlation_is_pearson_of_the_series_with_its_basis() -> None:
    rng = random.Random(2)
    series = {
        name: tuple(rng.gauss(0.0, 0.01) for _ in range(60)) for name in ("MOM", "MR", "CARRY")
    }
    result = strategy_return_correlation(
        [StrategyReturns(name, values, "USD", "1D") for name, values in series.items()],
        source="three runs",
    )

    assert result.correlation.assets == ("CARRY", "MOM", "MR")
    assert result.correlation.correlation("MOM", "MR") == pytest.approx(
        pearson_correlation(series["MOM"], series["MR"]), abs=1e-14
    )
    assert (result.correlation.currency, result.correlation.period) == ("USD", "1D")
    assert result.correlation.observations == 60
    assert result.covariance.source == "three runs"


def test_return_correlation_refuses_mixed_units_and_degenerate_inputs() -> None:
    usd = StrategyReturns("A", (0.01, 0.02, -0.01), "USD", "1D")
    with pytest.raises(AnalyticsValidationError, match="at least two"):
        strategy_return_correlation([usd], source="s")
    with pytest.raises(AnalyticsValidationError, match="listed twice"):
        strategy_return_correlation([usd, usd], source="s")
    with pytest.raises(AnalyticsValidationError, match="different quantities"):
        strategy_return_correlation(
            [usd, StrategyReturns("B", (0.01, 0.0, 0.02), "EUR", "1D")], source="s"
        )
    with pytest.raises(AnalyticsValidationError, match="differing lengths"):
        strategy_return_correlation(
            [usd, StrategyReturns("B", (0.01, 0.0), "USD", "1D")], source="s"
        )
    with pytest.raises(AnalyticsValidationError, match="constant return series"):
        strategy_return_correlation(
            [usd, StrategyReturns("B", (0.0, 0.0, 0.0), "USD", "1D")], source="s"
        )


# --------------------------------------------------------------------------- #
# Overlap and exposure correlation
# --------------------------------------------------------------------------- #


def test_position_overlap_separates_duplicated_from_opposing_exposure() -> None:
    pairs = {(pair.first, pair.second): pair for pair in strategy_overlap(BOOK)}
    mom_mr = pairs[("MOM", "MR")]

    assert list(pairs) == [("CARRY", "MOM"), ("CARRY", "MR"), ("MOM", "MR")]
    assert mom_mr.shared_instruments == ("AAPL", "MSFT")
    assert mom_mr.jaccard == pytest.approx(2 / 4)
    assert mom_mr.same_direction_overlap == Decimal("20")
    assert mom_mr.opposing_overlap == Decimal("40")
    assert mom_mr.first_overlap_share == pytest.approx(60 / 180)
    assert mom_mr.second_overlap_share == pytest.approx(60 / 120)
    assert pairs[("CARRY", "MR")].shared_instruments == ()
    assert pairs[("CARRY", "MR")].jaccard == 0.0


def test_the_cosine_is_uncentered_and_the_exposure_correlation_is_centered() -> None:
    pair = next(p for p in strategy_overlap(BOOK) if (p.first, p.second) == ("MOM", "MR"))
    union = ("AAPL", "MSFT", "SAP", "XOM")
    mom = [100.0, 50.0, 30.0, 0.0]
    mr = [-40.0, 20.0, 0.0, 60.0]
    dot = sum(a * b for a, b in zip(mom, mr, strict=True))

    assert len(union) == 4
    assert pair.cosine_similarity == pytest.approx(
        dot / (math.sqrt(sum(a * a for a in mom)) * math.sqrt(sum(b * b for b in mr)))
    )
    assert pair.exposure_correlation == pytest.approx(pearson_correlation(mom, mr))


def test_identical_and_mirrored_books_read_one_and_minus_one() -> None:
    mirror = (
        line("A", "X", "10"),
        line("A", "Y", "20"),
        line("B", "X", "-10"),
        line("B", "Y", "-20"),
    )
    same = (line("A", "X", "10"), line("A", "Y", "20"), line("B", "X", "5"), line("B", "Y", "10"))

    assert strategy_overlap(mirror)[0].cosine_similarity == pytest.approx(-1.0)
    assert strategy_overlap(same)[0].cosine_similarity == pytest.approx(1.0)
    assert strategy_overlap(same)[0].opposing_overlap == Decimal("0")


def test_undefined_similarities_are_none_never_zero() -> None:
    single = (line("A", "X", "10"), line("B", "X", "5"))
    flat = (line("A", "X", "0"), line("B", "Y", "5"))

    assert strategy_overlap(single)[0].exposure_correlation is None
    assert strategy_overlap(flat)[0].cosine_similarity is None


def test_overlap_refuses_unattributed_duplicated_or_lonely_lines() -> None:
    with pytest.raises(AnalyticsValidationError, match="names no strategy"):
        strategy_overlap((line("", "X", "1"), line("B", "X", "1")))
    with pytest.raises(AnalyticsValidationError, match="two lines"):
        strategy_overlap((line("A", "X", "1"), line("A", "X", "2")))
    with pytest.raises(AnalyticsValidationError, match="compares strategies"):
        strategy_overlap((line("A", "X", "1"),))


# --------------------------------------------------------------------------- #
# Factor crowding
# --------------------------------------------------------------------------- #

LOADINGS = FactorLoadings.of(
    {
        "AAPL": {"momentum": 1.0, "value": -0.5},
        "MSFT": {"momentum": 0.8, "value": 0.0},
        "SAP": {"momentum": 0.2, "value": 0.6},
        "XOM": {"momentum": -0.4, "value": 1.2},
    },
    source="unit model",
    lineage={"momentum": "12-1 rank", "value": "b/p rank"},
    as_of=None,
)


def test_strategy_factor_exposures_add_up_to_the_portfolio_s() -> None:
    report = factor_crowding(BOOK, LOADINGS, capital=Decimal("1000"))
    momentum = next(entry for entry in report.factors if entry.factor == "momentum")
    portfolio = (
        180 * 0 + 100 * 1.0 + 50 * 0.8 + 30 * 0.2 - 40 * 1.0 + 20 * 0.8 + 60 * -0.4 + 10 * 0.2
    ) / 1000

    assert momentum.by_strategy["MOM"] == pytest.approx((100 * 1.0 + 50 * 0.8 + 30 * 0.2) / 1000)
    assert momentum.aggregate_exposure == pytest.approx(portfolio)
    assert momentum.aggregate_exposure == pytest.approx(math.fsum(momentum.by_strategy.values()))


def test_alignment_and_concentration_say_how_crowded_a_factor_is_within_the_book() -> None:
    report = factor_crowding(BOOK, LOADINGS, capital=Decimal("1000"))
    momentum = next(entry for entry in report.factors if entry.factor == "momentum")
    gross = sum(abs(value) for value in momentum.by_strategy.values())

    assert momentum.alignment == pytest.approx(abs(momentum.aggregate_exposure) / gross)
    assert momentum.concentration == pytest.approx(
        sum((abs(v) / gross) ** 2 for v in momentum.by_strategy.values())
    )
    assert momentum.aligned_strategies == tuple(
        name for name, value in momentum.by_strategy.items() if value > 0
    )


def test_offsetting_strategies_read_as_uncrowded() -> None:
    offset = (line("A", "AAPL", "100"), line("B", "AAPL", "-100"))
    report = factor_crowding(offset, LOADINGS, capital=Decimal("1000"))
    momentum = next(entry for entry in report.factors if entry.factor == "momentum")

    assert momentum.alignment == pytest.approx(0.0)
    assert momentum.aligned_strategies == ()
    assert report.pairs[0].cosine_similarity == pytest.approx(-1.0)


def test_crowding_refuses_holdings_without_loadings_and_a_bad_capital() -> None:
    with pytest.raises(AnalyticsValidationError, match="not an asset with a zero loading"):
        factor_crowding((line("A", "TSLA", "1"),), LOADINGS, capital=Decimal("1"))
    with pytest.raises(AnalyticsValidationError, match="positive Decimal"):
        factor_crowding(BOOK, LOADINGS, capital=Decimal("0"))


# --------------------------------------------------------------------------- #
# Common exposures
# --------------------------------------------------------------------------- #


def test_common_exposures_by_instrument_mark_shared_and_opposing_buckets() -> None:
    report = common_exposures(BOOK, by=CommonDimension.INSTRUMENT, classification=None)
    aapl = next(bucket for bucket in report.buckets if bucket.bucket == "AAPL")

    assert aapl.by_strategy == {"MOM": Decimal("100"), "MR": Decimal("-40")}
    assert aapl.net_exposure == Decimal("60")
    assert aapl.gross_exposure == Decimal("140")
    assert aapl.shared and aapl.opposing
    assert [bucket.bucket for bucket in report.shared] == ["AAPL", "MSFT", "SAP"]


def test_common_exposures_by_currency_and_by_classification() -> None:
    currencies = common_exposures(BOOK, by=CommonDimension.CURRENCY, classification=None)
    sectors = Classification(
        "sector", {"AAPL": "Tech", "MSFT": "Tech", "SAP": "Tech", "XOM": "Energy"}, "registry", None
    )
    by_sector = common_exposures(BOOK, by=CommonDimension.CLASSIFICATION, classification=sectors)

    assert [bucket.bucket for bucket in currencies.buckets] == ["EUR", "USD"]
    assert next(b for b in currencies.buckets if b.bucket == "EUR").by_strategy == {
        "CARRY": Decimal("10"),
        "MOM": Decimal("30"),
    }
    assert by_sector.label == "sector" and by_sector.source == "registry"
    energy = next(bucket for bucket in by_sector.buckets if bucket.bucket == "Energy")
    assert energy.by_strategy == {"MR": Decimal("60")} and not energy.shared


def test_common_exposures_refuse_an_unclassified_holding_and_a_mismatched_request() -> None:
    partial = Classification("sector", {"AAPL": "Tech"}, "registry", None)
    with pytest.raises(AnalyticsValidationError, match="needs a sector"):
        common_exposures(BOOK, by=CommonDimension.CLASSIFICATION, classification=partial)
    with pytest.raises(AnalyticsValidationError, match="exactly when"):
        common_exposures(BOOK, by=CommonDimension.CLASSIFICATION, classification=None)
    with pytest.raises(AnalyticsValidationError, match="exactly when"):
        common_exposures(BOOK, by=CommonDimension.INSTRUMENT, classification=partial)


# --------------------------------------------------------------------------- #
# Capital concentration and overlap
# --------------------------------------------------------------------------- #


def test_capital_concentration_is_the_herfindahl_index_of_shares() -> None:
    report = capital_concentration(
        {"MOM": Decimal("600"), "MR": Decimal("300"), "CARRY": Decimal("100")},
        currency="USD",
        dimension="strategy",
    )

    assert report.total == Decimal("1000")
    assert report.shares == {"CARRY": 0.1, "MOM": 0.6, "MR": 0.3}
    assert report.herfindahl == pytest.approx(0.36 + 0.09 + 0.01)
    assert report.effective_count == pytest.approx(1 / 0.46)
    assert (report.largest, report.largest_share) == ("MOM", 0.6)


def test_equal_capital_ties_to_the_earlier_label() -> None:
    report = capital_concentration(
        {"B": Decimal("1"), "A": Decimal("1")}, currency="USD", dimension="strategy"
    )

    assert report.largest == "A"
    assert report.effective_count == pytest.approx(2.0)


def test_capital_concentration_refuses_what_is_undefined() -> None:
    with pytest.raises(AnalyticsValidationError, match="no bucket"):
        capital_concentration({}, currency="USD", dimension="strategy")
    with pytest.raises(AnalyticsValidationError, match="non-negative"):
        capital_concentration({"A": Decimal("-1")}, currency="USD", dimension="strategy")
    with pytest.raises(AnalyticsValidationError, match="undefined rather than zero"):
        capital_concentration({"A": Decimal("0")}, currency="USD", dimension="strategy")


def test_capital_overlap_names_pools_several_strategies_draw_on() -> None:
    pools = capital_overlap(
        {
            "MOM": {"ACC-1": Decimal("500"), "ACC-2": Decimal("100")},
            "MR": {"ACC-1": Decimal("200")},
            "CARRY": {"ACC-3": Decimal("300"), "ACC-2": Decimal("0")},
        },
        currency="USD",
    )

    assert [pool.pool for pool in pools] == ["ACC-1", "ACC-2", "ACC-3"]
    assert pools[0].shared and pools[0].total == Decimal("700")
    assert pools[0].by_strategy == {"MOM": Decimal("500"), "MR": Decimal("200")}
    assert not pools[1].shared and pools[1].by_strategy == {"MOM": Decimal("100")}
    with pytest.raises(AnalyticsValidationError, match="non-negative"):
        capital_overlap({"MOM": {"ACC": Decimal("-1")}}, currency="USD")


def test_nothing_held_is_an_empty_grouping_and_an_undefined_measure() -> None:
    """A grouping of nothing is empty; a measurement of nothing is refused, not zero."""

    report = common_exposures([], by=CommonDimension.INSTRUMENT, classification=None)

    assert report.buckets == ()
    assert capital_overlap({}, currency="USD") == ()
    with pytest.raises(AnalyticsValidationError, match="compares strategies"):
        strategy_overlap([])
    with pytest.raises(AnalyticsValidationError, match="no bucket is undefined"):
        capital_concentration({}, currency="USD", dimension="strategy")
