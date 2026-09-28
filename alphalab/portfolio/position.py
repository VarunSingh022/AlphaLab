from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, in_accounting_context, plain
from alphalab.common.currency_units import MAX_MINOR_UNITS, STANDARD_CURRENCY_UNITS
from alphalab.conventions.economics import InstrumentEconomics
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.money import ZERO_MONEY
from alphalab.portfolio.types import PositionSide

__all__ = ["Position"]


class _KeepOpenedAt:
    """Sentinel for "leave ``opened_at`` as it is".

    A distinct type rather than a float, so that ``None`` stays available as a
    real value meaning "unrecorded" and no legitimate timestamp can collide
    with it.
    """

    __slots__ = ()


#: The one instance of that sentinel.
_KEEP_OPENED_AT = _KeepOpenedAt()

_ONE = Decimal("1")


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

    ``average_cost`` remains the reported per-unit cost, derived from the basis
    by exact division in the accounting context -- it is a reported figure, never
    rounded to a price grid. When a ``Position`` is constructed without an
    explicit ``cost_basis`` (as external callers and fixtures do), the basis is
    taken to be ``average_cost * |quantity|`` rounded to money.

    ``minor_units`` is the number of decimals of the position's currency, which
    is what "rounded to money" means for it. The portfolio engine records it when
    it opens a position, from the account's
    :class:`~alphalab.common.currency_units.CurrencyUnits`. ``None`` -- what a
    hand-constructed position carries -- means ISO 4217's figure for
    ``currency``; a currency outside ISO 4217 must state it, and reading a money
    figure of one that did not raises
    :class:`~alphalab.common.currency_units.UnknownCurrencyUnitsError`.

    Prices and quantities are exact: ``market_price`` and ``quantity`` are what
    the venue or the market reported, never quantized (see
    :mod:`alphalab.portfolio.money`, rule 3).

    ``opened_at`` is when the *current* exposure began, and is state rather than
    something derived from the event log. A reversal emits ``PositionReduced``,
    not ``PositionClosed`` followed by ``PositionOpened``, so a reader of the
    log cannot tell a reversal from a partial reduction without replaying
    running quantity and watching for a sign change. It is set when a flat
    position takes on exposure, left alone by an increase or a partial
    reduction -- the remaining quantity has been held since it opened -- and
    replaced at a reversal, because the old leg closed and a new opposite one
    opened at that instant. A closed position leaves ``positions`` entirely, so
    its ``opened_at`` goes with it. ``None`` means unrecorded, which is what a
    hand-constructed position reports. See ADR-0015 decision 6.

    ``economics`` is what one unit of the instrument is worth and how it settles
    (ledger ACC-005): its multiplier scales every money figure -- the cash a
    fill moves, the basis, the market value -- and ``quantity`` stays the count
    of contracts the venue reported. ``None`` is a fully paid unit with a
    multiplier of one, which is what every position was before v3.11 and what a
    hand-constructed position still is. A position whose gains settle as cash
    at every mark (a future, a perpetual) moves no notional when it trades: its
    ``average_cost`` is the price it was last settled at, what it adds to equity
    beyond that cash is its :attr:`carrying_value`, and its
    :attr:`market_value` is its notional exposure.
    """

    asset_id: str
    quantity: Decimal
    average_cost: Decimal
    market_price: Decimal
    realized_pnl: Decimal
    currency: str
    last_updated: float
    cost_basis: Decimal | None = None
    opened_at: float | None = None
    minor_units: int | None = None
    economics: InstrumentEconomics | None = None

    def __post_init__(self) -> None:
        units = self.minor_units
        if units is not None and (
            isinstance(units, bool)
            or not isinstance(units, int)
            or not 0 <= units <= MAX_MINOR_UNITS
        ):
            raise PortfolioError(
                f"minor_units must be an integer between 0 and {MAX_MINOR_UNITS}, got {units!r}."
            )
        if self.economics is not None and not isinstance(self.economics, InstrumentEconomics):
            raise PortfolioError(
                f"economics must be InstrumentEconomics, got {type(self.economics).__name__}."
            )

    @property
    def multiplier(self) -> Decimal:
        """Value per unit of price per unit of quantity; one for a fully paid unit."""

        return _ONE if self.economics is None else self.economics.multiplier

    @property
    def pays_notional(self) -> bool:
        """Whether a trade in this position exchanges its value for cash (ledger ACC-005)."""

        return self.economics is None or self.economics.settlement.pays_notional

    def _scaled(self, amount: Decimal) -> Decimal:
        """``amount`` per unit, times the multiplier -- untouched for a multiplier of one."""

        if self.economics is None or self.economics.multiplier == _ONE:
            return amount
        return ACCOUNTING_CONTEXT.multiply(amount, self.economics.multiplier)

    def _money(self, amount: Decimal) -> Decimal:
        """``amount`` rounded half-even to this position's currency's minor unit."""

        if self.minor_units is None:
            return STANDARD_CURRENCY_UNITS.round(amount, self.currency)
        return amount.quantize(
            Decimal(1).scaleb(-self.minor_units),
            rounding=ROUND_HALF_EVEN,
            context=ACCOUNTING_CONTEXT,
        )

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
        return self._money(
            self._scaled(ACCOUNTING_CONTEXT.multiply(abs(self.quantity), self.average_cost))
        )

    @property
    def market_value(self) -> Decimal:
        """Signed ``quantity * market_price * multiplier``, rounded once to money.

        The position's value in the instrument's currency -- its notional
        exposure, which is what risk and exposure read. What it adds to equity
        is :attr:`carrying_value`, which is the same figure for a fully paid
        position and not for one that settles as cash.
        """

        return self._money(
            self._scaled(ACCOUNTING_CONTEXT.multiply(self.quantity, self.market_price))
        )

    @property
    def carrying_value(self) -> Decimal:
        """What the position adds to equity beyond cash (ledger ACC-005).

        Its market value when it was paid for in full -- a share, an option's
        premium. For a position whose gains settle as cash at every mark, only
        what has moved since it was last settled: its unrealized P&L, which is
        nil once the portfolio has marked it, because the mark paid it.
        """

        return self.market_value if self.pays_notional else self.unrealized_pnl

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
        """This position marked at ``price`` as of ``timestamp``.

        Built field by field rather than through ``dataclasses.replace``, which
        introspects the class on every call: this runs once per marked position
        per market event.
        """

        return Position(
            self.asset_id,
            self.quantity,
            self.average_cost,
            price,
            self.realized_pnl,
            self.currency,
            timestamp,
            self.cost_basis,
            self.opened_at,
            self.minor_units,
            self.economics,
        )

    @in_accounting_context
    def split(self, ratio: Decimal, timestamp: float) -> Position:
        """This position after a split of ``ratio`` units for each unit held (ACC-006).

        Its value does not move: the quantity is multiplied, the basis is kept
        exactly, and the average cost and the mark are divided -- so the market
        value, the unrealized P&L and every realized figure are what they were.
        """

        return self._rebased(
            self.quantity * ratio,
            self.basis,
            self.realized_pnl,
            self.market_price / ratio,
            timestamp,
        )

    @in_accounting_context
    def settle(self, price: Decimal, timestamp: float) -> tuple[Position, Decimal]:
        """This position marked at ``price``, and the cash the mark settles (ledger ACC-005).

        A fully paid position is only re-marked, and settles nothing. One whose
        gains settle as cash pays the move since it was last settled --
        ``quantity * (price - average_cost) * multiplier``, rounded once to money
        -- and is settled at ``price`` from then on. The amount is realized P&L:
        it has become cash.
        """

        if self.pays_notional or self.quantity == 0:
            return self.update_market_price(price, timestamp), ZERO_MONEY
        amount = self._money(
            self._scaled(ACCOUNTING_CONTEXT.multiply(self.quantity, price - self.average_cost))
        )
        settled = self._rebased(
            self.quantity,
            self._money(self._scaled(ACCOUNTING_CONTEXT.multiply(abs(self.quantity), price))),
            self.realized_pnl + amount,
            price,
            timestamp,
        )
        return settled, amount

    def _rebased(
        self,
        quantity: Decimal,
        basis: Decimal,
        realized_total: Decimal,
        price: Decimal,
        timestamp: float,
        opened_at: float | _KeepOpenedAt | None = _KEEP_OPENED_AT,
    ) -> Position:
        """Rebuild the position from an exact quantity/basis pair.

        ``opened_at`` defaults to keeping whatever the position already had.
        The two callers that pass it are the ones where the current exposure
        genuinely begins now: opening from flat, and either direction of a
        reversal.
        """

        average = (
            plain(ACCOUNTING_CONTEXT.divide(basis, self._scaled(abs(quantity))))
            if quantity != 0
            else Decimal("0")
        )
        return replace(
            self,
            quantity=quantity,
            average_cost=average,
            cost_basis=basis,
            realized_pnl=realized_total,
            market_price=price,
            last_updated=timestamp,
            opened_at=self.opened_at if isinstance(opened_at, _KeepOpenedAt) else opened_at,
        )

    @in_accounting_context
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

        if quantity == 0:
            return (self, ZERO_MONEY)

        if not self.pays_notional:
            return self._apply_settled(quantity, price, timestamp)

        total = self._money(self._scaled(abs(quantity) * price))

        if self.side is PositionSide.FLAT:
            # Exposure begins now.
            return (
                self._rebased(
                    quantity, total, self.realized_pnl, price, timestamp, opened_at=timestamp
                ),
                ZERO_MONEY,
            )

        if self.side is PositionSide.LONG:
            return self._apply_long(quantity, price, total, timestamp)

        return self._apply_short(quantity, price, total, timestamp)

    # ---------------------------------------------------------------------
    # Positions whose gains settle as cash (futures, perpetuals)
    # ---------------------------------------------------------------------

    def _apply_settled(
        self, quantity: Decimal, price: Decimal, timestamp: float
    ) -> tuple[Position, Decimal]:
        """A fill of a position that moves no notional: settle it, then resize it.

        The whole open position is first settled at the fill price -- what the
        next mark would have paid -- so every leg, the one closing and the one
        staying open, is then carried at one price, and resizing moves no cash.
        What the settlement paid is the fill's realized P&L.
        """

        settled, amount = self.settle(price, timestamp)
        opened = settled.quantity == 0
        remaining = settled.quantity + quantity
        reverses = not opened and remaining != 0 and (remaining > 0) != (settled.quantity > 0)
        basis = self._money(self._scaled(ACCOUNTING_CONTEXT.multiply(abs(remaining), price)))
        position = settled._rebased(
            remaining,
            basis if remaining != 0 else ZERO_MONEY,
            settled.realized_pnl,
            price,
            timestamp,
            opened_at=timestamp if opened or reverses else _KEEP_OPENED_AT,
        )
        return position, amount

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
                    self.quantity + quantity,
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
            relieved = self._money(basis * sell_quantity / self.quantity)
            realized = total - relieved

            return (
                self._rebased(
                    self.quantity - sell_quantity,
                    basis - relieved,
                    self.realized_pnl + realized,
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
                    self.realized_pnl + realized,
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

        closing_proceeds = self._money(self._scaled(abs(self.quantity) * price))
        realized = closing_proceeds - basis
        short_quantity = sell_quantity - self.quantity

        return (
            self._rebased(
                -short_quantity,
                total - closing_proceeds,
                self.realized_pnl + realized,
                price,
                timestamp,
                # The long closed and a short opened, in one fill. Carrying the
                # long's open time onto the new short would be a false number.
                opened_at=timestamp,
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
                    -(current_short + abs(quantity)),
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
            relieved = self._money(basis * buy_quantity / current_short)
            realized = relieved - total

            return (
                self._rebased(
                    self.quantity + buy_quantity,
                    basis - relieved,
                    self.realized_pnl + realized,
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
                    self.realized_pnl + realized,
                    price,
                    timestamp,
                ),
                realized,
            )

        # ---------------------------------------------------------
        # Reverse Short -> Long
        # ---------------------------------------------------------

        closing_cost = self._money(self._scaled(current_short * price))
        realized = basis - closing_cost
        long_quantity = buy_quantity - current_short

        return (
            self._rebased(
                long_quantity,
                total - closing_cost,
                self.realized_pnl + realized,
                price,
                timestamp,
                opened_at=timestamp,
            ),
            realized,
        )
