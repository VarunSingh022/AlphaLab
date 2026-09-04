"""Portfolio valuation: the derived, read-only view of a marked portfolio.

Nothing here holds state. Every value is a deterministic function of the
canonical :class:`~alphalab.portfolio.engine.PortfolioState` -- its cash ledger,
its open positions and their current marks -- so a snapshot taken twice from the
same state is identical.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.money import CURRENCY_QUANT, ZERO_MONEY
from alphalab.portfolio.position import Position

__all__ = ["CURRENCY_QUANT", "PortfolioValuation", "PortfolioValuationSnapshot"]


@dataclass(frozen=True, slots=True)
class PortfolioValuationSnapshot:
    """Deterministic mark-to-market valuation of a portfolio at one instant.

    Attributes:
        timestamp: Instant the valuation was taken.
        currency: Base currency the valuation is expressed in.
        cash: Cash balance in ``currency``.
        long_value: Summed market value of long positions (>= 0).
        short_value: Summed market value of short positions (<= 0).
        positions_value: ``long_value + short_value``.
        unrealized_pnl: Open P&L across all positions at their current marks.
        realized_pnl: Cumulative P&L crystallised by reductions and closes.
        commission_paid: Cumulative commissions already expensed to cash.
        equity: Total account equity, ``cash + positions_value``.
    """

    timestamp: float
    currency: str
    cash: Decimal
    long_value: Decimal
    short_value: Decimal
    positions_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    commission_paid: Decimal
    equity: Decimal


class PortfolioValuation:
    @staticmethod
    def asset_values(positions: Mapping[str, Position]) -> Mapping[str, Decimal]:
        return {asset: p.market_value for asset, p in positions.items()}

    @staticmethod
    def cash_value(cash_ledger: CashLedger, base_currency: str = "USD") -> Decimal:
        # Simplification: assumes base currency. A multi-ccy engine requires FX rates here.
        return cash_ledger.balance(base_currency)

    @staticmethod
    def long_value(positions: Mapping[str, Position]) -> Decimal:
        """Summed market value of long positions; zero or positive."""

        return sum((p.market_value for p in positions.values() if p.quantity > 0), Decimal("0.00"))

    @staticmethod
    def short_value(positions: Mapping[str, Position]) -> Decimal:
        """Summed market value of short positions; zero or negative."""

        return sum((p.market_value for p in positions.values() if p.quantity < 0), Decimal("0.00"))

    @staticmethod
    def portfolio_value(
        cash_ledger: CashLedger, positions: Mapping[str, Position], base_currency: str = "USD"
    ) -> Decimal:
        cash_val = PortfolioValuation.cash_value(cash_ledger, base_currency)
        pos_val = sum(PortfolioValuation.asset_values(positions).values(), Decimal("0.00"))
        return cash_val + pos_val

    @staticmethod
    def snapshot(
        state: PortfolioState, timestamp: float, currency: str | None = None
    ) -> PortfolioValuationSnapshot:
        """Value ``state`` as it currently stands, at ``timestamp``.

        Positions are valued at whatever mark they currently carry, so run
        :meth:`~alphalab.portfolio.engine.PortfolioEngine.update_market_prices`
        first if fresh market data is available.
        """

        base_currency = currency if currency is not None else state.account.base_currency
        positions = state.positions

        cash = state.cash.balance(base_currency)
        long_value = PortfolioValuation.long_value(positions)
        short_value = PortfolioValuation.short_value(positions)
        positions_value = long_value + short_value
        unrealized = sum((p.unrealized_pnl for p in positions.values()), ZERO_MONEY)

        return PortfolioValuationSnapshot(
            timestamp=timestamp,
            currency=base_currency,
            cash=cash,
            long_value=long_value,
            short_value=short_value,
            positions_value=positions_value,
            unrealized_pnl=unrealized,
            realized_pnl=state.realized_pnl,
            commission_paid=state.commission_paid,
            equity=cash + positions_value,
        )
