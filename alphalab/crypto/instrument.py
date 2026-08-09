"""Crypto instrument identity and the bridge into AlphaLab's existing Position model.

Same principle as `alphalab.options.contract` and `alphalab.futures.contract`: no new
Position type. `crypto_symbol` generates a stable synthetic asset_id so spot,
dated-future, and perpetual instruments can all be tracked as ordinary Positions
without any change to the portfolio package.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from alphalab.crypto.enums import InstrumentType
from alphalab.crypto.exceptions import CryptoInputError
from alphalab.portfolio.position import Position


@dataclass(frozen=True, slots=True)
class CryptoInstrument:
    """Immutable identity and terms of a single crypto instrument.

    Attributes:
        base_asset: The asset being bought/sold, e.g. "BTC".
        quote_asset: The asset it's priced in, e.g. "USDT".
        instrument_type: Spot, dated future, or perpetual.
        exchange: Venue this instrument trades on, e.g. "binance".
        contract_size: Units of base_asset per contract. 1 for spot (quantity is
            denominated directly in base_asset); exchange-defined for futures and
            perpetuals.
        expiry: Unix timestamp of contract expiration. Required for FUTURE, and
            must be None for SPOT and PERPETUAL, which never expire.
    """

    base_asset: str
    quote_asset: str
    instrument_type: InstrumentType
    exchange: str
    contract_size: Decimal = Decimal("1")
    expiry: float | None = None

    def __post_init__(self) -> None:
        if self.contract_size <= Decimal("0"):
            raise CryptoInputError(f"contract_size must be positive, got {self.contract_size}.")
        if self.instrument_type is InstrumentType.FUTURE and self.expiry is None:
            raise CryptoInputError("FUTURE instruments require an expiry.")
        if self.instrument_type is not InstrumentType.FUTURE and self.expiry is not None:
            raise CryptoInputError(
                f"{self.instrument_type.name} instruments must not have an expiry."
            )


def crypto_symbol(instrument: CryptoInstrument) -> str:
    """Builds a stable, human-readable synthetic asset_id for an instrument.

    Format: "{EXCHANGE}_{base}_{quote}_{TYPE}[_{expiry:%Y%m}]", e.g.
    "BINANCE_BTC_USDT_PERPETUAL" or "BINANCE_BTC_USDT_FUTURE_202612". Used directly
    as `Position.asset_id`.
    """
    parts = [
        instrument.exchange.upper(),
        instrument.base_asset,
        instrument.quote_asset,
        instrument.instrument_type.name,
    ]
    if instrument.expiry is not None:
        parts.append(datetime.fromtimestamp(instrument.expiry, tz=UTC).strftime("%Y%m"))
    return "_".join(parts)


def open_crypto_position(
    instrument: CryptoInstrument,
    quantity: Decimal,
    price: Decimal,
    timestamp: float,
) -> Position:
    """Opens a new crypto position using the unmodified portfolio Position model.

    `quantity` follows the same sign convention as every other Position in AlphaLab:
    positive to go long, negative to go short. For PERPETUAL instruments, `price`
    should be the entry mark price -- see `alphalab.crypto.perpetual` for ongoing
    mark-to-market, which must continue to use mark price, not last trade price.
    """
    return Position(
        asset_id=crypto_symbol(instrument),
        quantity=quantity,
        average_cost=price,
        market_price=price,
        realized_pnl=Decimal("0.00"),
        currency=instrument.quote_asset,
        last_updated=timestamp,
    )
