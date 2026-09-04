from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from alphalab.portfolio.money import (
    CURRENCY_QUANT,
    PRICE_QUANT,
    SHARE_QUANT,
    ZERO_MONEY,
    notional,
    to_money,
    to_price,
    to_quantity,
)
from alphalab.portfolio.types import PositionSide

__all__ = ["CURRENCY_QUANT", "PRICE_QUANT", "SHARE_QUANT", "Position"]


@dataclass(frozen=True, slots=True)
class Position:
    """
    Immutable position for a single asset.

    Positive quantity = Long

    Negative quantity = Short

    Zero quantity = Flat

    ``cost_basis`` is the authoritative money figure: for a long it is the exact
    cash paid for the open quantity, for a short the exact cash received. P&L is
    derived from it, so realized P&L is always exactly the difference between the
    money that moved on the way in and the money that moved on the way out --
    never an independently rounded recomputation from ``average_cost``. See
    :mod:`alphalab.portfolio.money`.

    ``average_cost`` remains the reported per-unit cost, derived from the basis.
    When a ``Position`` is constructed without an explicit ``cost_basis`` (as
    external callers and fixtures do), the basis is taken to be
    ``average_cost * |quantity|`` rounded to money.
    """

    asset_id: str
    quantity: Decimal
    average_cost: Decimal
    market_price: Decimal
    realized_pnl: Decimal
    currency: str
    last_updated: float
    cost_basis: Decimal | None = None

    @property
    def side(self) -> PositionSide:
        if self.quantity > 0:
            return PositionSide.LONG

        if self.quantity < 0:
            return PositionSide.SHORT

        return PositionSide.FLAT

    @property
    def basis(self) -> Decimal:
        """Exact money cost basis of the open quantity, always non-negative."""

        if self.cost_basis is not None:
            return self.cost_basis
        return notional(self.quantity, self.average_cost)

    @property
    def market_value(self) -> Decimal:
        return to_money(self.quantity * self.market_price)

    @property
    def unrealized_pnl(self) -> Decimal:
        """Open P&L: the difference between current market value and cost basis.

        Both terms are exact money, so the result is exact money -- no further
        rounding is applied or needed.
        """

        if self.quantity == 0:
            return ZERO_MONEY

        if self.side is PositionSide.LONG:
            return self.market_value - self.basis

        # Short: market_value is negative, basis is the credit received.
        return self.market_value + self.basis

    def update_market_price(
        self,
        price: Decimal,
        timestamp: float,
    ) -> Position:

        return replace(
            self,
            market_price=to_price(price),
            last_updated=timestamp,
        )

    def _rebased(
        self,
        quantity: Decimal,
        basis: Decimal,
        realized_total: Decimal,
        price: Decimal,
        timestamp: float,
    ) -> Position:
        """Rebuild the position from an exact quantity/basis pair."""

        average = to_price(basis / abs(quantity)) if quantity != 0 else Decimal("0")
        return replace(
            self,
            quantity=quantity,
            average_cost=average,
            cost_basis=basis,
            realized_pnl=realized_total,
            market_price=price,
            last_updated=timestamp,
        )

    def apply_fill(
        self,
        quantity: Decimal,
        price: Decimal,
        timestamp: float,
    ) -> tuple[Position, Decimal]:
        """
        Apply a signed fill.

        BUY  -> positive quantity

        SELL -> negative quantity

        Returns the updated position and the realized P&L crystallised by this
        fill. ``total`` below is the same money value the cash ledger moves for
        this fill, so the two can never disagree.
        """

        quantity = to_quantity(quantity)
        price = to_price(price)

        if quantity == 0:
            return (self, ZERO_MONEY)

        total = notional(quantity, price)

        if self.side is PositionSide.FLAT:
            return (
                self._rebased(quantity, total, self.realized_pnl, price, timestamp),
                ZERO_MONEY,
            )

        if self.side is PositionSide.LONG:
            return self._apply_long(quantity, price, total, timestamp)

        return self._apply_short(quantity, price, total, timestamp)

    # ---------------------------------------------------------------------
    # Long Position Logic
    # ---------------------------------------------------------------------

    def _apply_long(
        self,
        quantity: Decimal,
        price: Decimal,
        total: Decimal,
        timestamp: float,
    ) -> tuple[Position, Decimal]:
        """
        Apply a fill to an existing long position.

        quantity > 0  -> increase long

        quantity < 0  -> reduce / close / reverse
        """

        basis = self.basis

        # ---------------------------------------------------------
        # Increase Long
        # ---------------------------------------------------------

        if quantity > 0:
            return (
                self._rebased(
                    to_quantity(self.quantity + quantity),
                    basis + total,
                    self.realized_pnl,
                    price,
                    timestamp,
                ),
                ZERO_MONEY,
            )

        sell_quantity = abs(quantity)

        # ---------------------------------------------------------
        # Partial Close
        # ---------------------------------------------------------

        if sell_quantity < self.quantity:
            # Relieve a proportional slice of the basis; the remainder is what
            # is left over, by subtraction, so the two always sum to `basis`.
            relieved = to_money(basis * sell_quantity / self.quantity)
            realized = total - relieved

            return (
                self._rebased(
                    to_quantity(self.quantity - sell_quantity),
                    basis - relieved,
                    to_money(self.realized_pnl + realized),
                    price,
                    timestamp,
                ),
                realized,
            )

        # ---------------------------------------------------------
        # Close Position
        # ---------------------------------------------------------

        if sell_quantity == self.quantity:
            realized = total - basis

            return (
                self._rebased(
                    Decimal("0"),
                    ZERO_MONEY,
                    to_money(self.realized_pnl + realized),
                    price,
                    timestamp,
                ),
                realized,
            )

        # ---------------------------------------------------------
        # Reverse Long -> Short
        # ---------------------------------------------------------
        # The closing leg's proceeds and the new short's credit must add up to
        # `total` exactly -- that is the cash the ledger moves -- so the opening
        # leg is derived by subtraction rather than rounded independently.

        closing_proceeds = notional(self.quantity, price)
        realized = closing_proceeds - basis
        short_quantity = to_quantity(sell_quantity - self.quantity)

        return (
            self._rebased(
                -short_quantity,
                total - closing_proceeds,
                to_money(self.realized_pnl + realized),
                price,
                timestamp,
            ),
            realized,
        )

    # ---------------------------------------------------------------------
    # Short Position Logic
    # ---------------------------------------------------------------------

    def _apply_short(
        self,
        quantity: Decimal,
        price: Decimal,
        total: Decimal,
        timestamp: float,
    ) -> tuple[Position, Decimal]:
        """
        Apply a fill to an existing short position.

        quantity < 0 -> increase short

        quantity > 0 -> reduce / close / reverse
        """

        current_short = abs(self.quantity)
        basis = self.basis

        # ---------------------------------------------------------
        # Increase Short
        # ---------------------------------------------------------

        if quantity < 0:
            return (
                self._rebased(
                    -to_quantity(current_short + abs(quantity)),
                    basis + total,
                    self.realized_pnl,
                    price,
                    timestamp,
                ),
                ZERO_MONEY,
            )

        buy_quantity = quantity

        # ---------------------------------------------------------
        # Partial Cover
        # ---------------------------------------------------------

        if buy_quantity < current_short:
            relieved = to_money(basis * buy_quantity / current_short)
            realized = relieved - total

            return (
                self._rebased(
                    to_quantity(self.quantity + buy_quantity),
                    basis - relieved,
                    to_money(self.realized_pnl + realized),
                    price,
                    timestamp,
                ),
                realized,
            )

        # ---------------------------------------------------------
        # Close Short
        # ---------------------------------------------------------

        if buy_quantity == current_short:
            realized = basis - total

            return (
                self._rebased(
                    Decimal("0"),
                    ZERO_MONEY,
                    to_money(self.realized_pnl + realized),
                    price,
                    timestamp,
                ),
                realized,
            )

        # ---------------------------------------------------------
        # Reverse Short -> Long
        # ---------------------------------------------------------

        closing_cost = notional(current_short, price)
        realized = basis - closing_cost
        long_quantity = to_quantity(buy_quantity - current_short)

        return (
            self._rebased(
                long_quantity,
                total - closing_cost,
                to_money(self.realized_pnl + realized),
                price,
                timestamp,
            ),
            realized,
        )
