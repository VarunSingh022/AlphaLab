from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.position import Position


class NAVCalculator:
    @staticmethod
    def calculate(
        cash_ledger: CashLedger, positions: Mapping[str, Position], base_currency: str = "USD"
    ) -> Decimal:
        cash = cash_ledger.balance(base_currency)
        long_value = sum(
            (p.market_value for p in positions.values() if p.market_value > 0), Decimal("0.00")
        )
        short_liability = sum(
            (p.market_value for p in positions.values() if p.market_value < 0), Decimal("0.00")
        )
        return cash + long_value + short_liability  # Short value is inherently negative
