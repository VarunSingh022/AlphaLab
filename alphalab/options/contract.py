"""Option contract identity and the bridge into AlphaLab's existing Position model.

Deliberately does not define a new Position type. `alphalab.portfolio.position.Position`
already models signed quantity, average cost, market price, and realized P&L for any
`asset_id: str` -- an option contract's economics (long/short, average premium paid,
mark-to-market, realized gains) are structurally identical. `occ_symbol` generates a
stable synthetic asset_id so an option contract can be tracked as a Position without
any change to the portfolio package.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.exceptions import OptionInputError
from alphalab.portfolio.position import Position


@dataclass(frozen=True, slots=True)
class OptionContract:
    """Immutable identity and terms of a single option contract.

    Attributes:
        underlying_asset_id: Identifier of the underlying asset.
        strike: Strike price per share.
        expiry: Unix timestamp of contract expiration.
        option_type: Call or put.
        style: American or European exercise.
        multiplier: Shares controlled per contract, 100 for standard US equity
            options.
    """

    underlying_asset_id: str
    strike: Decimal
    expiry: float
    option_type: OptionType
    style: ExerciseStyle = ExerciseStyle.AMERICAN
    multiplier: int = 100

    def __post_init__(self) -> None:
        if self.strike <= Decimal("0"):
            raise OptionInputError(f"strike must be positive, got {self.strike}.")
        if self.multiplier <= 0:
            raise OptionInputError(f"multiplier must be positive, got {self.multiplier}.")


def occ_symbol(contract: OptionContract) -> str:
    """Builds a stable, human-readable synthetic asset_id for a contract.

    Format: "{underlying}_{expiry:%Y%m%d}_{C|P}_{strike*1000:08d}", e.g.
    "AAPL_20261218_C_00150000" for a $150 strike call expiring 2026-12-18. This value
    is used directly as `Position.asset_id` -- it is not a real OCC symbol, but it is
    unique per (underlying, expiry, type, strike) and stable across calls.
    """
    expiry_date = datetime.fromtimestamp(contract.expiry, tz=UTC).strftime("%Y%m%d")
    type_code = "C" if contract.option_type is OptionType.CALL else "P"
    strike_code = int((contract.strike * 1000).to_integral_value())
    return f"{contract.underlying_asset_id}_{expiry_date}_{type_code}_{strike_code:08d}"


def open_option_position(
    contract: OptionContract,
    quantity: Decimal,
    price: Decimal,
    timestamp: float,
    currency: str = "USD",
) -> Position:
    """Opens a new options position using the unmodified portfolio Position model.

    `quantity` follows the same sign convention as every other Position in AlphaLab:
    positive to go long the contract (buy to open), negative to go short (sell to
    open/write). `price` is the per-share option premium; economic exposure is
    `quantity * price * contract.multiplier`, computed by the caller, not stored here
    -- Position itself has no concept of a multiplier, consistent with it never being
    modified for options.
    """
    return Position(
        asset_id=occ_symbol(contract),
        quantity=quantity,
        average_cost=price,
        market_price=price,
        realized_pnl=Decimal("0.00"),
        currency=currency,
        last_updated=timestamp,
    )
