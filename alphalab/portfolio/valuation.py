from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.position import Position


class PortfolioValuation:
    @staticmethod
    def asset_values(positions: Mapping[str, Position]) -> Mapping[str, Decimal]:
        return {asset: p.market_value for asset, p in positions.items()}

    @staticmethod
    def cash_value(cash_ledger: CashLedger, base_currency: str = "USD") -> Decimal:
        # Simplification: assumes base currency. A multi-ccy engine requires FX rates here.
        return cash_ledger.balance(base_currency)

    @staticmethod
    def portfolio_value(
        cash_ledger: CashLedger, positions: Mapping[str, Position], base_currency: str = "USD"
    ) -> Decimal:
        cash_val = PortfolioValuation.cash_value(cash_ledger, base_currency)
        pos_val = sum(PortfolioValuation.asset_values(positions).values(), Decimal("0.00"))
        return cash_val + pos_val
