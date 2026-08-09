"""Comprehensive tests for the Futures Engine: contracts, rolls, curves, spreads."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.core.enums import Side
from alphalab.futures import (
    AdjustmentMethod,
    CalendarSpread,
    CalendarSpreadLeg,
    FutureContract,
    FuturesComputationError,
    FuturesCurve,
    FuturesCurvePoint,
    FuturesError,
    FuturesInputError,
    RollSegment,
    build_continuous_series,
    compute_pnl,
    compute_spread_value,
    curve_slope,
    futures_symbol,
    is_backwardation,
    is_contango,
    open_future_position,
    sorted_by_month,
)
from alphalab.market.bar import Bar, TimeFrame
from alphalab.portfolio.position import Position


def _bar(day: int, close: Decimal, asset_id: str = "CL") -> Bar:
    return Bar(
        asset_id=asset_id,
        timestamp=float(day * 86400),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("100"),
        vwap=close,
        trade_count=10,
        timeframe=TimeFrame.D1,
    )


def _contract(
    month_offset_days: int = 0, expiry_offset_days: int = 30, multiplier: int = 1000
) -> FutureContract:
    return FutureContract(
        underlying_asset_id="CL",
        contract_month=float(month_offset_days * 86400),
        expiry=float((month_offset_days + expiry_offset_days) * 86400),
        multiplier=multiplier,
        tick_size=Decimal("0.01"),
    )


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


def test_exception_hierarchy() -> None:
    assert issubclass(FuturesInputError, FuturesError)
    assert issubclass(FuturesComputationError, FuturesError)


# --------------------------------------------------------------------------- #
# FutureContract
# --------------------------------------------------------------------------- #


def test_contract_is_immutable() -> None:
    contract = _contract()
    with pytest.raises(FrozenInstanceError):
        contract.multiplier = 500  # type: ignore[misc]


def test_contract_rejects_non_positive_multiplier() -> None:
    with pytest.raises(FuturesInputError):
        FutureContract(
            underlying_asset_id="CL",
            contract_month=0.0,
            expiry=1000.0,
            multiplier=0,
            tick_size=Decimal("0.01"),
        )


def test_contract_rejects_non_positive_tick_size() -> None:
    with pytest.raises(FuturesInputError):
        FutureContract(
            underlying_asset_id="CL",
            contract_month=0.0,
            expiry=1000.0,
            multiplier=1000,
            tick_size=Decimal("0"),
        )


def test_contract_rejects_expiry_before_contract_month() -> None:
    with pytest.raises(FuturesInputError):
        FutureContract(
            underlying_asset_id="CL",
            contract_month=5000.0,
            expiry=1000.0,
            multiplier=1000,
            tick_size=Decimal("0.01"),
        )


def test_futures_symbol_is_stable_and_unique_per_month() -> None:
    dec = _contract(month_offset_days=0)
    jan = _contract(month_offset_days=35)
    assert futures_symbol(dec) != futures_symbol(jan)
    assert futures_symbol(dec) == futures_symbol(_contract(month_offset_days=0))


# --------------------------------------------------------------------------- #
# Position bridge: same principle as the Options Engine
# --------------------------------------------------------------------------- #


def test_open_future_position_returns_unmodified_portfolio_position() -> None:
    contract = _contract()
    position = open_future_position(contract, Decimal("2"), Decimal("75.50"), timestamp=1000.0)

    assert type(position) is Position
    assert position.asset_id == futures_symbol(contract)
    assert position.quantity == Decimal("2")
    assert position.average_cost == Decimal("75.50")


def test_future_position_supports_apply_fill_from_portfolio_package() -> None:
    contract = _contract()
    position = open_future_position(contract, Decimal("2"), Decimal("75.50"), timestamp=1000.0)
    updated, realized = position.apply_fill(Decimal("-2"), Decimal("80.00"), timestamp=2000.0)

    assert updated.quantity == Decimal("0")
    assert realized == Decimal("9.00")  # (80.00 - 75.50) * 2


def test_short_future_position_quantity_is_negative() -> None:
    contract = _contract()
    position = open_future_position(contract, Decimal("-3"), Decimal("75.50"), timestamp=1000.0)
    assert position.quantity < 0


# --------------------------------------------------------------------------- #
# Continuous contract construction: verified against hand-computed values
# --------------------------------------------------------------------------- #


def test_unadjusted_series_preserves_raw_jump() -> None:
    dec = _contract(month_offset_days=0)
    jan = _contract(month_offset_days=30)
    seg_dec = RollSegment(contract=dec, bars=(_bar(0, Decimal("50")),))
    seg_jan = RollSegment(contract=jan, bars=(_bar(1, Decimal("60")),))

    result = build_continuous_series((seg_dec, seg_jan), AdjustmentMethod.UNADJUSTED)
    assert [b.close for b in result] == [Decimal("50"), Decimal("60")]


def test_back_adjusted_three_segment_series_matches_hand_computed_values() -> None:
    """Dec->Jan gap=+1, Jan->Feb gap=+2. Dec should shift +3, Jan +2, Feb +0."""
    dec = _contract(month_offset_days=0)
    jan = _contract(month_offset_days=30)
    feb = _contract(month_offset_days=60)

    seg_dec = RollSegment(
        contract=dec,
        bars=(_bar(0, Decimal("50")),),
        outgoing_roll_price=Decimal("50"),
        incoming_roll_price=Decimal("51"),
    )
    seg_jan = RollSegment(
        contract=jan,
        bars=(_bar(1, Decimal("52")),),
        outgoing_roll_price=Decimal("52"),
        incoming_roll_price=Decimal("54"),
    )
    seg_feb = RollSegment(contract=feb, bars=(_bar(2, Decimal("55")),))

    result = build_continuous_series((seg_dec, seg_jan, seg_feb), AdjustmentMethod.BACK_ADJUSTED)
    closes = [b.close for b in result]

    assert closes == [Decimal("53"), Decimal("54"), Decimal("55")]


def test_back_adjusted_current_segment_is_never_shifted() -> None:
    """The defining property of back-adjustment: the newest segment is the anchor."""
    dec = _contract(month_offset_days=0)
    jan = _contract(month_offset_days=30)
    seg_dec = RollSegment(
        contract=dec,
        bars=(_bar(0, Decimal("50")),),
        outgoing_roll_price=Decimal("50"),
        incoming_roll_price=Decimal("999"),
    )
    seg_jan = RollSegment(contract=jan, bars=(_bar(1, Decimal("60")),))

    result = build_continuous_series((seg_dec, seg_jan), AdjustmentMethod.BACK_ADJUSTED)
    assert result[-1].close == Decimal("60")


def test_ratio_adjusted_series_matches_hand_computed_values() -> None:
    dec = _contract(month_offset_days=0)
    jan = _contract(month_offset_days=30)
    seg_dec = RollSegment(
        contract=dec,
        bars=(_bar(0, Decimal("50")),),
        outgoing_roll_price=Decimal("50"),
        incoming_roll_price=Decimal("100"),
    )
    seg_jan = RollSegment(contract=jan, bars=(_bar(1, Decimal("20")),))

    result = build_continuous_series((seg_dec, seg_jan), AdjustmentMethod.RATIO_ADJUSTED)
    closes = [b.close for b in result]

    assert closes == [Decimal("100"), Decimal("20")]


def test_back_adjustment_preserves_volume_unchanged() -> None:
    dec = _contract(month_offset_days=0)
    jan = _contract(month_offset_days=30)
    seg_dec = RollSegment(
        contract=dec,
        bars=(_bar(0, Decimal("50")),),
        outgoing_roll_price=Decimal("50"),
        incoming_roll_price=Decimal("60"),
    )
    seg_jan = RollSegment(contract=jan, bars=(_bar(1, Decimal("60")),))

    result = build_continuous_series((seg_dec, seg_jan), AdjustmentMethod.BACK_ADJUSTED)
    assert all(b.volume == Decimal("100") for b in result)


def test_build_continuous_series_rejects_empty_segments() -> None:
    with pytest.raises(FuturesInputError):
        build_continuous_series((), AdjustmentMethod.BACK_ADJUSTED)


def test_build_continuous_series_rejects_missing_roll_prices_on_non_final_segment() -> None:
    dec = _contract(month_offset_days=0)
    jan = _contract(month_offset_days=30)
    seg_dec = RollSegment(contract=dec, bars=(_bar(0, Decimal("50")),))  # missing roll prices
    seg_jan = RollSegment(contract=jan, bars=(_bar(1, Decimal("60")),))

    with pytest.raises(FuturesInputError):
        build_continuous_series((seg_dec, seg_jan), AdjustmentMethod.BACK_ADJUSTED)


def test_single_segment_series_is_returned_unchanged() -> None:
    contract = _contract()
    seg = RollSegment(contract=contract, bars=(_bar(0, Decimal("50")), _bar(1, Decimal("51"))))

    result = build_continuous_series((seg,), AdjustmentMethod.BACK_ADJUSTED)
    assert [b.close for b in result] == [Decimal("50"), Decimal("51")]


# --------------------------------------------------------------------------- #
# Curve analysis
# --------------------------------------------------------------------------- #


def _contango_curve() -> FuturesCurve:
    points = (
        FuturesCurvePoint(contract_month=0.0, price=Decimal("70.00")),
        FuturesCurvePoint(contract_month=2592000.0, price=Decimal("72.00")),
        FuturesCurvePoint(contract_month=5184000.0, price=Decimal("75.00")),
    )
    return FuturesCurve(underlying_asset_id="CL", timestamp=0.0, points=points)


def _backwardation_curve() -> FuturesCurve:
    points = (
        FuturesCurvePoint(contract_month=0.0, price=Decimal("75.00")),
        FuturesCurvePoint(contract_month=2592000.0, price=Decimal("72.00")),
    )
    return FuturesCurve(underlying_asset_id="CL", timestamp=0.0, points=points)


def test_sorted_by_month_orders_ascending() -> None:
    curve = _contango_curve()
    ordered = sorted_by_month(curve)
    assert [p.contract_month for p in ordered] == sorted(p.contract_month for p in curve.points)


def test_curve_slope_positive_for_contango() -> None:
    assert curve_slope(_contango_curve()) > Decimal("0")


def test_curve_slope_negative_for_backwardation() -> None:
    assert curve_slope(_backwardation_curve()) < Decimal("0")


def test_is_contango_true_for_upward_sloping_curve() -> None:
    assert is_contango(_contango_curve()) is True
    assert is_backwardation(_contango_curve()) is False


def test_is_backwardation_true_for_downward_sloping_curve() -> None:
    assert is_backwardation(_backwardation_curve()) is True
    assert is_contango(_backwardation_curve()) is False


def test_curve_slope_raises_with_fewer_than_two_points() -> None:
    curve = FuturesCurve(
        underlying_asset_id="CL",
        timestamp=0.0,
        points=(FuturesCurvePoint(contract_month=0.0, price=Decimal("70.00")),),
    )
    with pytest.raises(FuturesInputError):
        curve_slope(curve)


def test_curve_point_is_immutable() -> None:
    point = FuturesCurvePoint(contract_month=0.0, price=Decimal("70.00"))
    with pytest.raises(FrozenInstanceError):
        point.price = Decimal("999")  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Calendar spreads
# --------------------------------------------------------------------------- #


def test_calendar_spread_leg_rejects_non_positive_quantity() -> None:
    with pytest.raises(FuturesInputError):
        CalendarSpreadLeg(contract=_contract(), side=Side.BUY, quantity=0)


def test_compute_spread_value_long_near_short_far() -> None:
    near = _contract(month_offset_days=0)
    far = _contract(month_offset_days=90)
    spread = CalendarSpread(
        legs=(
            CalendarSpreadLeg(contract=near, side=Side.BUY, quantity=1),
            CalendarSpreadLeg(contract=far, side=Side.SELL, quantity=1),
        )
    )
    prices = {futures_symbol(near): Decimal("70.00"), futures_symbol(far): Decimal("75.00")}

    value = compute_spread_value(spread, prices)
    # (70*1000*1) - (75*1000*1) = -5000
    assert value == Decimal("-5000.00")


def test_compute_spread_value_raises_on_missing_price() -> None:
    near = _contract(month_offset_days=0)
    far = _contract(month_offset_days=90)
    spread = CalendarSpread(
        legs=(
            CalendarSpreadLeg(contract=near, side=Side.BUY, quantity=1),
            CalendarSpreadLeg(contract=far, side=Side.SELL, quantity=1),
        )
    )
    with pytest.raises(FuturesInputError):
        compute_spread_value(spread, {futures_symbol(near): Decimal("70.00")})


def test_compute_pnl_profitable_calendar_spread() -> None:
    near = _contract(month_offset_days=0)
    far = _contract(month_offset_days=90)
    spread = CalendarSpread(
        legs=(
            CalendarSpreadLeg(contract=near, side=Side.BUY, quantity=1),
            CalendarSpreadLeg(contract=far, side=Side.SELL, quantity=1),
        )
    )
    entry_prices = {futures_symbol(near): Decimal("70.00"), futures_symbol(far): Decimal("75.00")}
    current_prices = {futures_symbol(near): Decimal("74.00"), futures_symbol(far): Decimal("75.00")}

    pnl = compute_pnl(spread, entry_prices, current_prices)
    # long near gained (74-70)*1000=4000, short far unchanged=0 -> total 4000
    assert pnl == Decimal("4000.00")


def test_compute_pnl_raises_on_missing_current_price() -> None:
    near = _contract(month_offset_days=0)
    spread = CalendarSpread(legs=(CalendarSpreadLeg(contract=near, side=Side.BUY, quantity=1),))
    entry_prices = {futures_symbol(near): Decimal("70.00")}
    with pytest.raises(FuturesInputError):
        compute_pnl(spread, entry_prices, {})


def test_calendar_spread_is_immutable() -> None:
    spread = CalendarSpread(
        legs=(CalendarSpreadLeg(contract=_contract(), side=Side.BUY, quantity=1),)
    )
    with pytest.raises(FrozenInstanceError):
        spread.legs = ()  # type: ignore[misc]
