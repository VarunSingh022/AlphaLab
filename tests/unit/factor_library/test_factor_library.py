"""Comprehensive tests for the Factor Library: every factor, inputs, and catalog."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.factor_library import (
    FACTOR_CATALOG,
    FactorComputationError,
    FactorInputError,
    FactorResult,
    FundamentalSnapshot,
    PriceSeries,
    RequiredInput,
    compute_carry,
    compute_liquidity,
    compute_momentum,
    compute_quality,
    compute_value,
    compute_volatility,
    get_spec,
)
from alphalab.feature_store import FeatureValueAdapter, FeatureValueProtocol
from alphalab.market.bar import Bar, TimeFrame


def _bar(day: int, close: Decimal, volume: Decimal = Decimal("1000")) -> Bar:
    return Bar(
        asset_id="AAPL",
        timestamp=float(day * 86400),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        vwap=close,
        trade_count=100,
        timeframe=TimeFrame.D1,
    )


def _rising_price_series(days: int = 21, start: Decimal = Decimal("100")) -> PriceSeries:
    bars = tuple(_bar(i, start + Decimal(i)) for i in range(days))
    return PriceSeries(asset_id="AAPL", bars=bars)


def _fundamentals(
    price: Decimal = Decimal("150.00"),
    eps: Decimal = Decimal("6.00"),
    book_value: Decimal = Decimal("30.00"),
    dividend: Decimal = Decimal("3.00"),
) -> FundamentalSnapshot:
    return FundamentalSnapshot(
        asset_id="AAPL",
        timestamp=1000.0,
        price=price,
        earnings_per_share=eps,
        book_value_per_share=book_value,
        dividend_per_share=dividend,
    )


# --------------------------------------------------------------------------- #
# PriceSeries input validation
# --------------------------------------------------------------------------- #


def test_price_series_rejects_mismatched_asset_bars() -> None:
    good_bar = _bar(0, Decimal("100"))
    bad_bar = Bar(
        asset_id="MSFT",
        timestamp=86400.0,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1000"),
        vwap=Decimal("100"),
        trade_count=100,
        timeframe=TimeFrame.D1,
    )
    with pytest.raises(ValueError):
        PriceSeries(asset_id="AAPL", bars=(good_bar, bad_bar))


def test_price_series_is_immutable() -> None:
    series = _rising_price_series()
    with pytest.raises(FrozenInstanceError):
        series.asset_id = "MSFT"  # type: ignore[misc]


def test_fundamental_snapshot_is_immutable() -> None:
    snap = _fundamentals()
    with pytest.raises(FrozenInstanceError):
        snap.price = Decimal("999")  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Momentum
# --------------------------------------------------------------------------- #


def test_compute_momentum_positive_for_rising_prices() -> None:
    series = _rising_price_series(days=21)
    result = compute_momentum(series, "momentum_20d", 1, lookback_periods=20, timestamp=2000.0)

    assert isinstance(result, FactorResult)
    assert result.value > 0
    assert result.asset_id == "AAPL"
    assert result.feature_id == "momentum_20d"


def test_compute_momentum_matches_manual_total_return() -> None:
    series = _rising_price_series(days=21, start=Decimal("100"))
    result = compute_momentum(series, "momentum_20d", 1, lookback_periods=20, timestamp=2000.0)

    start_close = Decimal("100")
    end_close = Decimal("120")
    expected = float((end_close - start_close) / start_close)
    assert result.value == pytest.approx(expected)


def test_compute_momentum_raises_on_insufficient_bars() -> None:
    series = _rising_price_series(days=5)
    with pytest.raises(FactorInputError):
        compute_momentum(series, "momentum_20d", 1, lookback_periods=20, timestamp=2000.0)


def test_compute_momentum_raises_on_non_positive_lookback() -> None:
    series = _rising_price_series(days=21)
    with pytest.raises(FactorInputError):
        compute_momentum(series, "momentum_20d", 1, lookback_periods=0, timestamp=2000.0)


# --------------------------------------------------------------------------- #
# Volatility
# --------------------------------------------------------------------------- #


def test_compute_volatility_zero_for_flat_prices() -> None:
    bars = tuple(_bar(i, Decimal("100")) for i in range(21))
    series = PriceSeries(asset_id="AAPL", bars=bars)
    result = compute_volatility(series, "vol_20d", 1, lookback_periods=20, timestamp=2000.0)
    assert result.value == pytest.approx(0.0)


def test_compute_volatility_positive_for_varying_prices() -> None:
    prices = [Decimal("100"), Decimal("110"), Decimal("95"), Decimal("115"), Decimal("90")] * 5
    bars = tuple(_bar(i, p) for i, p in enumerate(prices))
    series = PriceSeries(asset_id="AAPL", bars=bars)
    result = compute_volatility(series, "vol_20d", 1, lookback_periods=20, timestamp=2000.0)
    assert result.value > 0


def test_compute_volatility_raises_on_insufficient_bars() -> None:
    series = _rising_price_series(days=5)
    with pytest.raises(FactorInputError):
        compute_volatility(series, "vol_20d", 1, lookback_periods=20, timestamp=2000.0)


def test_compute_volatility_raises_below_minimum_lookback() -> None:
    series = _rising_price_series(days=21)
    with pytest.raises(FactorInputError):
        compute_volatility(series, "vol_20d", 1, lookback_periods=1, timestamp=2000.0)


# --------------------------------------------------------------------------- #
# Liquidity
# --------------------------------------------------------------------------- #


def test_compute_liquidity_matches_manual_mean_dollar_volume() -> None:
    bars = (_bar(0, Decimal("100"), Decimal("10")), _bar(1, Decimal("200"), Decimal("20")))
    series = PriceSeries(asset_id="AAPL", bars=bars)
    result = compute_liquidity(series, "liq_2d", 1, lookback_periods=2, timestamp=2000.0)

    expected = float((Decimal("100") * Decimal("10") + Decimal("200") * Decimal("20")) / 2)
    assert result.value == pytest.approx(expected)


def test_compute_liquidity_raises_on_insufficient_bars() -> None:
    series = _rising_price_series(days=3)
    with pytest.raises(FactorInputError):
        compute_liquidity(series, "liq_20d", 1, lookback_periods=20, timestamp=2000.0)


# --------------------------------------------------------------------------- #
# Value
# --------------------------------------------------------------------------- #


def test_compute_value_earnings_yield() -> None:
    fundamentals = _fundamentals(price=Decimal("150"), eps=Decimal("6"))
    result = compute_value(fundamentals, "value_ey", 1, timestamp=1000.0)
    assert result.value == pytest.approx(6 / 150)


def test_compute_value_raises_on_non_positive_price() -> None:
    fundamentals = _fundamentals(price=Decimal("0"))
    with pytest.raises(FactorComputationError):
        compute_value(fundamentals, "value_ey", 1, timestamp=1000.0)


# --------------------------------------------------------------------------- #
# Quality
# --------------------------------------------------------------------------- #


def test_compute_quality_return_on_book_equity() -> None:
    fundamentals = _fundamentals(eps=Decimal("6"), book_value=Decimal("30"))
    result = compute_quality(fundamentals, "quality_roe", 1, timestamp=1000.0)
    assert result.value == pytest.approx(6 / 30)


def test_compute_quality_raises_on_non_positive_book_value() -> None:
    fundamentals = _fundamentals(book_value=Decimal("0"))
    with pytest.raises(FactorComputationError):
        compute_quality(fundamentals, "quality_roe", 1, timestamp=1000.0)


# --------------------------------------------------------------------------- #
# Carry
# --------------------------------------------------------------------------- #


def test_compute_carry_dividend_yield() -> None:
    fundamentals = _fundamentals(price=Decimal("150"), dividend=Decimal("3"))
    result = compute_carry(fundamentals, "carry_dy", 1, timestamp=1000.0)
    assert result.value == pytest.approx(3 / 150)


def test_compute_carry_raises_on_non_positive_price() -> None:
    fundamentals = _fundamentals(price=Decimal("0"))
    with pytest.raises(FactorComputationError):
        compute_carry(fundamentals, "carry_dy", 1, timestamp=1000.0)


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #


def test_catalog_lists_all_six_roadmap_factors() -> None:
    ids = {spec.factor_id for spec in FACTOR_CATALOG}
    assert ids == {"momentum", "value", "quality", "carry", "volatility", "liquidity"}


def test_get_spec_returns_correct_spec() -> None:
    spec = get_spec("momentum")
    assert spec is not None
    assert spec.required_input == RequiredInput.PRICE_SERIES


def test_get_spec_returns_none_for_unknown_factor() -> None:
    assert get_spec("nonexistent_factor") is None


def test_catalog_is_immutable() -> None:
    spec = FACTOR_CATALOG[0]
    with pytest.raises(FrozenInstanceError):
        spec.factor_id = "renamed"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Cross-package integration: Factor Library -> Feature Store
# --------------------------------------------------------------------------- #


def test_factor_result_satisfies_feature_value_protocol_structurally() -> None:
    result = compute_momentum(_rising_price_series(), "momentum_20d", 1, 20, 2000.0)
    accepted: FeatureValueProtocol = result
    assert accepted.feature_id == "momentum_20d"


def test_factor_result_flows_through_feature_store_adapter_unmodified() -> None:
    """Proves the real decoupling seam works end-to-end, not just in theory.

    Feature Store's adapter never imports FactorResult -- this test constructs one
    and passes it straight through FeatureValueAdapter, which only knows about the
    structural protocol.
    """
    result = compute_momentum(_rising_price_series(), "momentum_20d", 1, 20, 2000.0)
    converted = FeatureValueAdapter.to_feature_value(result)

    assert converted.feature_id == result.feature_id
    assert converted.version == result.version
    assert converted.asset_id == result.asset_id
    assert converted.value == result.value
    assert converted.timestamp == result.timestamp
