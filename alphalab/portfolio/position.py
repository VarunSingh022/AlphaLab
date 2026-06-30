from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from alphalab.portfolio.types import PositionSide

SHARE_QUANT = Decimal("0.000001")
PRICE_QUANT = Decimal("0.0001")
CURRENCY_QUANT = Decimal("0.01")

@dataclass(frozen=True, slots=True)
class Position:
    """
    Immutable position for a single asset.

    Positive quantity = Long

    Negative quantity = Short

    Zero quantity = Flat
    """

    asset_id: str
    quantity: Decimal
    average_cost: Decimal
    market_price: Decimal
    realized_pnl: Decimal
    currency: str
    last_updated: float

    @property
    def side(self) -> PositionSide:
        if self.quantity > 0:
            return PositionSide.LONG

        if self.quantity < 0:
            return PositionSide.SHORT

        return PositionSide.FLAT

    @property
    def market_value(self) -> Decimal:
        return (self.quantity * self.market_price).quantize(CURRENCY_QUANT)

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.quantity == 0:
            return Decimal("0.00")

        if self.side is PositionSide.LONG:
            pnl = (self.market_price - self.average_cost) * self.quantity
        else:
            pnl = (self.average_cost - self.market_price) * abs(self.quantity)

        return pnl.quantize(CURRENCY_QUANT)

    def update_market_price(
        self,
        price: Decimal,
        timestamp: float,
    ) -> Position:

        return replace(
            self,
            market_price=price.quantize(PRICE_QUANT),
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
        """

        quantity = quantity.quantize(SHARE_QUANT)
        price = price.quantize(PRICE_QUANT)

        if quantity == 0:
            return (
                self,
                Decimal("0.00"),
            )

        if self.side is PositionSide.FLAT:
            return (
                replace(
                    self,
                    quantity=quantity,
                    average_cost=price,
                    market_price=price,
                    last_updated=timestamp,
                ),
                Decimal("0.00"),
            )

        if self.side is PositionSide.LONG:
            return self._apply_long(
                quantity,
                price,
                timestamp,
            )

        return self._apply_short(
            quantity,
            price,
            timestamp,
        )
        # ---------------------------------------------------------------------

    # Long Position Logic
    # ---------------------------------------------------------------------

    def _apply_long(
        self,
        quantity: Decimal,
        price: Decimal,
        timestamp: float,
    ) -> tuple[Position, Decimal]:
        """
        Apply a fill to an existing long position.

        quantity > 0  -> increase long

        quantity < 0  -> reduce / close / reverse
        """

        # ---------------------------------------------------------
        # Increase Long
        # ---------------------------------------------------------

        if quantity > 0:
            new_quantity = self.quantity + quantity

            average_cost = ((self.average_cost * self.quantity) + (price * quantity)) / new_quantity

            position = replace(
                self,
                quantity=new_quantity.quantize(SHARE_QUANT),
                average_cost=average_cost.quantize(PRICE_QUANT),
                market_price=price,
                last_updated=timestamp,
            )

            return position, Decimal("0.00")

        sell_quantity = abs(quantity)

        # ---------------------------------------------------------
        # Partial Close
        # ---------------------------------------------------------

        if sell_quantity < self.quantity:
            realized = ((price - self.average_cost) * sell_quantity).quantize(CURRENCY_QUANT)

            position = replace(
                self,
                quantity=(self.quantity - sell_quantity).quantize(SHARE_QUANT),
                realized_pnl=(self.realized_pnl + realized).quantize(CURRENCY_QUANT),
                market_price=price,
                last_updated=timestamp,
            )

            return position, realized

        # ---------------------------------------------------------
        # Close Position
        # ---------------------------------------------------------

        if sell_quantity == self.quantity:
            realized = ((price - self.average_cost) * self.quantity).quantize(CURRENCY_QUANT)

            position = replace(
                self,
                quantity=Decimal("0"),
                average_cost=Decimal("0"),
                realized_pnl=(self.realized_pnl + realized).quantize(CURRENCY_QUANT),
                market_price=price,
                last_updated=timestamp,
            )

            return position, realized

        # ---------------------------------------------------------
        # Reverse Long -> Short
        # ---------------------------------------------------------

        realized = ((price - self.average_cost) * self.quantity).quantize(CURRENCY_QUANT)

        short_quantity = (sell_quantity - self.quantity).quantize(SHARE_QUANT)

        position = replace(
            self,
            quantity=-short_quantity,
            average_cost=price,
            realized_pnl=(self.realized_pnl + realized).quantize(CURRENCY_QUANT),
            market_price=price,
            last_updated=timestamp,
        )

        return position, realized
        # ---------------------------------------------------------------------

    # Short Position Logic
    # ---------------------------------------------------------------------

    def _apply_short(
        self,
        quantity: Decimal,
        price: Decimal,
        timestamp: float,
    ) -> tuple[Position, Decimal]:
        """
        Apply a fill to an existing short position.

        quantity < 0 -> increase short

        quantity > 0 -> reduce / close / reverse
        """

        current_short = abs(self.quantity)

        # ---------------------------------------------------------
        # Increase Short
        # ---------------------------------------------------------

        if quantity < 0:
            added = abs(quantity)
            new_quantity = current_short + added

            average_cost = ((self.average_cost * current_short) + (price * added)) / new_quantity

            position = replace(
                self,
                quantity=-new_quantity.quantize(SHARE_QUANT),
                average_cost=average_cost.quantize(PRICE_QUANT),
                market_price=price,
                last_updated=timestamp,
            )

            return position, Decimal("0.00")

        buy_quantity = quantity

        # ---------------------------------------------------------
        # Partial Cover
        # ---------------------------------------------------------

        if buy_quantity < current_short:
            realized = ((self.average_cost - price) * buy_quantity).quantize(CURRENCY_QUANT)

            position = replace(
                self,
                quantity=(self.quantity + buy_quantity).quantize(SHARE_QUANT),
                realized_pnl=(self.realized_pnl + realized).quantize(CURRENCY_QUANT),
                market_price=price,
                last_updated=timestamp,
            )

            return position, realized

        # ---------------------------------------------------------
        # Close Short
        # ---------------------------------------------------------

        if buy_quantity == current_short:
            realized = ((self.average_cost - price) * current_short).quantize(CURRENCY_QUANT)

            position = replace(
                self,
                quantity=Decimal("0"),
                average_cost=Decimal("0"),
                realized_pnl=(self.realized_pnl + realized).quantize(CURRENCY_QUANT),
                market_price=price,
                last_updated=timestamp,
            )

            return position, realized

        # ---------------------------------------------------------
        # Reverse Short -> Long
        # ---------------------------------------------------------

        realized = ((self.average_cost - price) * current_short).quantize(CURRENCY_QUANT)

        long_quantity = (buy_quantity - current_short).quantize(SHARE_QUANT)

        position = replace(
            self,
            quantity=long_quantity,
            average_cost=price,
            realized_pnl=(self.realized_pnl + realized).quantize(CURRENCY_QUANT),
            market_price=price,
            last_updated=timestamp,
        )

        return position, realized
