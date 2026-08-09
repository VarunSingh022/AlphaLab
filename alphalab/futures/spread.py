"""Calendar spreads: opposing positions across two contract months of one underlying.

`CalendarSpreadLeg.side` reuses `alphalab.core.enums.Side` (BUY/SELL), the same
principle applied in `alphalab.options.strategy.OptionLeg` -- no new buy/sell enum.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.futures.contract import FutureContract, futures_symbol
from alphalab.futures.exceptions import FuturesInputError


@dataclass(frozen=True, slots=True)
class CalendarSpreadLeg:
    """A single leg of a calendar spread.

    Attributes:
        contract: The specific contract month this leg trades.
        side: BUY to go long this leg, SELL to go short.
        quantity: Number of contracts, always positive -- direction is carried by
            `side`, not the sign of `quantity`.
    """

    contract: FutureContract
    side: Side
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise FuturesInputError(f"quantity must be positive, got {self.quantity}.")


@dataclass(frozen=True, slots=True)
class CalendarSpread:
    """An immutable collection of futures legs traded as one spread.

    Typically two legs on the same underlying at different contract months (e.g.
    long the near month, short the far month), but any number of legs is supported.
    """

    legs: tuple[CalendarSpreadLeg, ...]


def compute_spread_value(spread: CalendarSpread, prices: Mapping[str, Decimal]) -> Decimal:
    """Computes the net notional value of a spread at given per-contract prices.

    Args:
        spread: The spread to evaluate.
        prices: Per-share/unit price for each leg, keyed by
            `futures_symbol(leg.contract)`.

    Raises:
        FuturesInputError: If prices is missing an entry for any leg.
    """
    total = Decimal("0")
    for leg in spread.legs:
        symbol = futures_symbol(leg.contract)
        if symbol not in prices:
            raise FuturesInputError(f"Missing price for leg '{symbol}'.")

        sign = Decimal("1") if leg.side is Side.BUY else Decimal("-1")
        total += sign * prices[symbol] * leg.contract.multiplier * leg.quantity
    return total


def compute_pnl(
    spread: CalendarSpread,
    entry_prices: Mapping[str, Decimal],
    current_prices: Mapping[str, Decimal],
) -> Decimal:
    """Computes net profit/loss of a spread between entry and current prices.

    Args:
        spread: The spread to evaluate.
        entry_prices: Per-unit entry price for each leg, keyed by
            `futures_symbol(leg.contract)`.
        current_prices: Per-unit current price for each leg, keyed the same way.

    Raises:
        FuturesInputError: If either price mapping is missing an entry for any leg.
    """
    total = Decimal("0")
    for leg in spread.legs:
        symbol = futures_symbol(leg.contract)
        if symbol not in entry_prices:
            raise FuturesInputError(f"Missing entry price for leg '{symbol}'.")
        if symbol not in current_prices:
            raise FuturesInputError(f"Missing current price for leg '{symbol}'.")

        sign = Decimal("1") if leg.side is Side.BUY else Decimal("-1")
        price_change = current_prices[symbol] - entry_prices[symbol]
        total += sign * price_change * leg.contract.multiplier * leg.quantity
    return total
