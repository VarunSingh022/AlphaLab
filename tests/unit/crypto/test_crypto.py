"""Comprehensive tests for the Crypto Engine: instruments, funding, symbols, perpetuals."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.crypto import (
    CryptoInputError,
    CryptoInstrument,
    FundingRate,
    FundingRateHistory,
    InstrumentType,
    annualized_funding_rate,
    average_funding_rate,
    compute_funding_payment,
    compute_liquidation_price,
    crypto_symbol,
    mark_to_market,
    open_crypto_position,
    parse_exchange_symbol,
    to_canonical_symbol,
    to_exchange_symbol,
)
from alphalab.portfolio.position import Position
from alphalab.portfolio.types import PositionSide

ONE_MONTH = 30 * 86400


def _spot(exchange: str = "binance") -> CryptoInstrument:
    return CryptoInstrument(
        base_asset="BTC", quote_asset="USDT", instrument_type=InstrumentType.SPOT, exchange=exchange
    )


def _perpetual(exchange: str = "binance") -> CryptoInstrument:
    return CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.PERPETUAL,
        exchange=exchange,
    )


def _future(exchange: str = "binance", expiry: float = ONE_MONTH) -> CryptoInstrument:
    return CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.FUTURE,
        exchange=exchange,
        expiry=expiry,
    )


# --------------------------------------------------------------------------- #
# CryptoInstrument
# --------------------------------------------------------------------------- #


def test_instrument_is_immutable() -> None:
    instrument = _spot()
    with pytest.raises(FrozenInstanceError):
        instrument.base_asset = "ETH"  # type: ignore[misc]


def test_future_requires_expiry() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.FUTURE,
            exchange="binance",
        )


def test_spot_rejects_expiry() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.SPOT,
            exchange="binance",
            expiry=ONE_MONTH,
        )


def test_perpetual_rejects_expiry() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.PERPETUAL,
            exchange="binance",
            expiry=ONE_MONTH,
        )


def test_instrument_rejects_non_positive_contract_size() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.SPOT,
            exchange="binance",
            contract_size=Decimal("0"),
        )


def test_crypto_symbol_differs_by_instrument_type() -> None:
    assert crypto_symbol(_spot()) != crypto_symbol(_perpetual())


def test_crypto_symbol_includes_expiry_for_futures_only() -> None:
    assert crypto_symbol(_future()).endswith("_197001")
    assert "_" not in crypto_symbol(_perpetual()).split("PERPETUAL")[-1]


# --------------------------------------------------------------------------- #
# Position bridge: same principle as Options and Futures
# --------------------------------------------------------------------------- #


def test_open_crypto_position_returns_unmodified_portfolio_position() -> None:
    instrument = _spot()
    position = open_crypto_position(
        instrument, Decimal("0.5"), Decimal("50000.00"), timestamp=1000.0
    )

    assert type(position) is Position
    assert position.asset_id == crypto_symbol(instrument)
    assert position.quantity == Decimal("0.5")
    assert position.currency == "USDT"


def test_crypto_position_supports_apply_fill_from_portfolio_package() -> None:
    instrument = _perpetual()
    position = open_crypto_position(instrument, Decimal("1"), Decimal("50000.00"), timestamp=1000.0)
    updated, realized = position.apply_fill(Decimal("-1"), Decimal("52000.00"), timestamp=2000.0)

    assert updated.quantity == Decimal("0")
    assert realized == Decimal("2000.00")


def test_short_crypto_position_quantity_is_negative() -> None:
    instrument = _perpetual()
    position = open_crypto_position(
        instrument, Decimal("-2"), Decimal("50000.00"), timestamp=1000.0
    )
    assert position.quantity < 0


# --------------------------------------------------------------------------- #
# Symbol normalization: verified round-trips, including the Kraken XBT quirk
# --------------------------------------------------------------------------- #


def test_to_canonical_symbol_format() -> None:
    assert to_canonical_symbol("btc", "usdt") == "BTC-USDT"


def test_binance_symbol_has_no_separator() -> None:
    assert to_exchange_symbol("binance", "BTC", "USDT") == "BTCUSDT"


def test_coinbase_symbol_is_hyphenated() -> None:
    assert to_exchange_symbol("coinbase", "BTC", "USD") == "BTC-USD"


def test_kraken_applies_xbt_alias_for_btc() -> None:
    assert to_exchange_symbol("kraken", "BTC", "USD") == "XBTUSD"


def test_kraken_does_not_alias_non_btc_assets() -> None:
    assert to_exchange_symbol("kraken", "ETH", "USD") == "ETHUSD"


def test_to_exchange_symbol_rejects_unknown_exchange() -> None:
    with pytest.raises(CryptoInputError):
        to_exchange_symbol("unknown_exchange", "BTC", "USDT")


def test_binance_round_trip() -> None:
    symbol = to_exchange_symbol("binance", "BTC", "USDT")
    assert parse_exchange_symbol("binance", symbol) == ("BTC", "USDT")


def test_coinbase_round_trip() -> None:
    symbol = to_exchange_symbol("coinbase", "ETH", "USD")
    assert parse_exchange_symbol("coinbase", symbol) == ("ETH", "USD")


def test_kraken_round_trip_resolves_xbt_back_to_btc() -> None:
    symbol = to_exchange_symbol("kraken", "BTC", "USD")
    assert parse_exchange_symbol("kraken", symbol) == ("BTC", "USD")


def test_parse_coinbase_symbol_requires_hyphen() -> None:
    with pytest.raises(CryptoInputError):
        parse_exchange_symbol("coinbase", "BTCUSD")


def test_parse_binance_symbol_raises_on_unknown_quote_suffix() -> None:
    with pytest.raises(CryptoInputError):
        parse_exchange_symbol("binance", "BTCXYZ")


def test_parse_exchange_symbol_rejects_unknown_exchange() -> None:
    with pytest.raises(CryptoInputError):
        parse_exchange_symbol("unknown_exchange", "BTCUSDT")


def test_longest_quote_asset_suffix_wins() -> None:
    """USDT must be matched before USD to avoid parsing "BTCUSDT" as base="BTCUS"."""
    assert parse_exchange_symbol("binance", "BTCUSDT") == ("BTC", "USDT")


# --------------------------------------------------------------------------- #
# Funding rates
# --------------------------------------------------------------------------- #


def test_funding_rate_rejects_non_positive_interval() -> None:
    with pytest.raises(CryptoInputError):
        FundingRate(
            instrument_symbol="X", rate=Decimal("0.0001"), timestamp=1000.0, interval_hours=0
        )


def test_compute_funding_payment_long_pays_when_rate_positive() -> None:
    payment = compute_funding_payment(
        position_quantity=Decimal("1"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
    )
    assert payment == Decimal("-5.00000")  # long pays 50000 * 0.0001


def test_compute_funding_payment_short_receives_when_rate_positive() -> None:
    payment = compute_funding_payment(
        position_quantity=Decimal("-1"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
    )
    assert payment == Decimal("5.00000")


def test_compute_funding_payment_scales_with_contract_size() -> None:
    payment = compute_funding_payment(
        position_quantity=Decimal("1"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        contract_size=Decimal("10"),
    )
    assert payment == Decimal("-50.00000")


def test_average_funding_rate() -> None:
    history = FundingRateHistory(
        instrument_symbol="X",
        rates=(
            FundingRate(instrument_symbol="X", rate=Decimal("0.0001"), timestamp=0.0),
            FundingRate(instrument_symbol="X", rate=Decimal("0.0003"), timestamp=28800.0),
        ),
    )
    assert average_funding_rate(history) == Decimal("0.0002")


def test_average_funding_rate_raises_on_empty_history() -> None:
    with pytest.raises(CryptoInputError):
        average_funding_rate(FundingRateHistory(instrument_symbol="X", rates=()))


def test_annualized_funding_rate_matches_hand_computed_value() -> None:
    """0.0001 average rate every 8 hours -> 0.0001 * (24*365/8) = 0.1095."""
    history = FundingRateHistory(
        instrument_symbol="X",
        rates=(FundingRate(instrument_symbol="X", rate=Decimal("0.0001"), timestamp=0.0),),
    )
    result = annualized_funding_rate(history)
    assert result == pytest.approx(Decimal("0.1095"), abs=Decimal("0.0001"))


def test_annualized_funding_rate_rejects_mixed_intervals() -> None:
    history = FundingRateHistory(
        instrument_symbol="X",
        rates=(
            FundingRate(
                instrument_symbol="X", rate=Decimal("0.0001"), timestamp=0.0, interval_hours=8
            ),
            FundingRate(
                instrument_symbol="X", rate=Decimal("0.0001"), timestamp=3600.0, interval_hours=1
            ),
        ),
    )
    with pytest.raises(CryptoInputError):
        annualized_funding_rate(history)


# --------------------------------------------------------------------------- #
# Perpetual mark-to-market and liquidation price
# --------------------------------------------------------------------------- #


def test_mark_to_market_updates_price_and_returns_unrealized_pnl() -> None:
    instrument = _perpetual()
    position = open_crypto_position(instrument, Decimal("1"), Decimal("50000.00"), timestamp=1000.0)

    updated, pnl = mark_to_market(position, Decimal("52000.00"), timestamp=2000.0)

    assert updated.market_price == Decimal("52000.0000")
    assert pnl == Decimal("2000.00")


def test_mark_to_market_does_not_mutate_input_position() -> None:
    instrument = _perpetual()
    position = open_crypto_position(instrument, Decimal("1"), Decimal("50000.00"), timestamp=1000.0)
    mark_to_market(position, Decimal("52000.00"), timestamp=2000.0)
    assert position.market_price == Decimal("50000.0000")


def test_liquidation_price_long_matches_hand_computed_value() -> None:
    """10x long at 50000, 0.5% maintenance -> 50000*(1-0.1+0.005) = 45250."""
    liq = compute_liquidation_price(
        Decimal("50000"), PositionSide.LONG, Decimal("10"), Decimal("0.005")
    )
    assert liq == Decimal("45250.000")


def test_liquidation_price_short_matches_hand_computed_value() -> None:
    """10x short at 50000, 0.5% maintenance -> 50000*(1+0.1-0.005) = 54750."""
    liq = compute_liquidation_price(
        Decimal("50000"), PositionSide.SHORT, Decimal("10"), Decimal("0.005")
    )
    assert liq == Decimal("54750.000")


def test_liquidation_price_long_is_below_entry() -> None:
    liq = compute_liquidation_price(
        Decimal("50000"), PositionSide.LONG, Decimal("5"), Decimal("0.01")
    )
    assert liq < Decimal("50000")


def test_liquidation_price_short_is_above_entry() -> None:
    liq = compute_liquidation_price(
        Decimal("50000"), PositionSide.SHORT, Decimal("5"), Decimal("0.01")
    )
    assert liq > Decimal("50000")


def test_liquidation_price_rejects_flat_side() -> None:
    with pytest.raises(CryptoInputError):
        compute_liquidation_price(
            Decimal("50000"), PositionSide.FLAT, Decimal("10"), Decimal("0.005")
        )


def test_liquidation_price_rejects_non_positive_leverage() -> None:
    with pytest.raises(CryptoInputError):
        compute_liquidation_price(
            Decimal("50000"), PositionSide.LONG, Decimal("0"), Decimal("0.005")
        )


def test_liquidation_price_rejects_negative_maintenance_margin() -> None:
    with pytest.raises(CryptoInputError):
        compute_liquidation_price(
            Decimal("50000"), PositionSide.LONG, Decimal("10"), Decimal("-0.01")
        )


def test_higher_leverage_moves_liquidation_price_closer_to_entry() -> None:
    """Higher leverage means less room before liquidation -- a basic sanity check
    on the formula's directional behavior, not just its exact output."""
    low_leverage_liq = compute_liquidation_price(
        Decimal("50000"), PositionSide.LONG, Decimal("2"), Decimal("0.005")
    )
    high_leverage_liq = compute_liquidation_price(
        Decimal("50000"), PositionSide.LONG, Decimal("20"), Decimal("0.005")
    )
    assert high_leverage_liq > low_leverage_liq
