"""Futures contract identity and the bridge into AlphaLab's existing Position model.

Same principle as `alphalab.options.contract`: no new Position type. A futures
contract's economics -- signed quantity, average entry price, mark-to-market,
realized P&L -- are structurally identical to any other Position.
`futures_symbol` generates a stable synthetic asset_id so a specific contract month
can be tracked as a Position without any change to the portfolio package.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from alphalab.conventions.tick import TickValue, tick_value
from alphalab.futures.exceptions import FuturesInputError
from alphalab.portfolio.position import Position


@dataclass(frozen=True, slots=True)
class FutureContract:
    """Immutable identity and terms of a single futures contract.

    Attributes:
        underlying_asset_id: Root symbol of the underlying, e.g. "CL" for WTI crude.
        contract_month: Unix timestamp representing the delivery month (the day
            value is not significant; only year/month are used by `futures_symbol`).
        expiry: Unix timestamp of the last trading day.
        multiplier: Contract size, e.g. 1000 for a 1,000-barrel crude contract.
        tick_size: Minimum price movement.
        currency: Settlement currency. **Required** as of v3.4, having defaulted
            to ``"USD"``. It becomes ``Position.currency`` through
            :func:`open_future_position`, which decides what a valuation of the
            book must convert and which settlement bucket this contract's P&L
            accrues to -- and a Nifty, Bund or TOPIX future is exactly the case
            where a US default is wrong. The same change v2.17 made to
            :func:`alphalab.options.contract.open_option_position`, for the same
            reason, under ADR-0019's rule: a currency is named, never assumed.
    """

    underlying_asset_id: str
    contract_month: float
    expiry: float
    multiplier: int
    tick_size: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise FuturesInputError(
                f"{self.underlying_asset_id} names no settlement currency. A contract whose "
                "currency is unstated produces a Position that cannot be valued or settled."
            )
        if self.multiplier <= 0:
            raise FuturesInputError(f"multiplier must be positive, got {self.multiplier}.")
        if self.tick_size <= Decimal("0"):
            raise FuturesInputError(f"tick_size must be positive, got {self.tick_size}.")
        if self.expiry < self.contract_month:
            raise FuturesInputError("expiry cannot be before contract_month.")


def futures_symbol(contract: FutureContract) -> str:
    """Builds a stable, human-readable synthetic asset_id for a contract.

    Format: "{underlying}_{contract_month:%Y%m}", e.g. "CL_202612" for a December
    2026 crude oil contract. Used directly as `Position.asset_id`.
    """
    month_code = datetime.fromtimestamp(contract.contract_month, tz=UTC).strftime("%Y%m")
    return f"{contract.underlying_asset_id}_{month_code}"


def open_future_position(
    contract: FutureContract,
    quantity: Decimal,
    price: Decimal,
    timestamp: float,
) -> Position:
    """Opens a new futures position using the unmodified portfolio Position model.

    `quantity` follows the same sign convention as every other Position in AlphaLab:
    positive to go long, negative to go short. Notional exposure is
    `quantity * price * contract.multiplier`, computed by the caller -- Position has
    no concept of a multiplier, consistent with it never being modified for futures.
    """
    return Position(
        asset_id=futures_symbol(contract),
        quantity=quantity,
        average_cost=price,
        market_price=price,
        realized_pnl=Decimal("0.00"),
        currency=contract.currency,
        last_updated=timestamp,
    )


def contract_tick_value(contract: FutureContract) -> TickValue:
    """What a one-tick move is worth on one contract of this month.

    ``tick_size * multiplier``, in the contract's settlement currency -- and
    computed by :func:`alphalab.conventions.tick.tick_value`, which is the one
    place in AlphaLab that multiplication happens. A tick size is a *price* and
    a tick value is *money*, and the returned
    :class:`~alphalab.conventions.tick.TickValue` keeps them as separate fields
    along with the inputs, so the figure can be audited rather than trusted.
    """
    return tick_value(
        tick_size=contract.tick_size,
        multiplier=Decimal(contract.multiplier),
        currency=contract.currency,
    )
