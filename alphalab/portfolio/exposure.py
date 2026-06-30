from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.position import Position
from alphalab.portfolio.valuation import PortfolioValuation


class ExposureEngine:
    @staticmethod
    def long_exposure(positions: Mapping[str, Position]) -> Decimal:
        return sum(
            (p.market_value for p in positions.values() if p.market_value > 0), Decimal("0.00")
        )

    @staticmethod
    def short_exposure(positions: Mapping[str, Position]) -> Decimal:
        return sum(
            (abs(p.market_value) for p in positions.values() if p.market_value < 0), Decimal("0.00")
        )

    @staticmethod
    def gross_exposure(positions: Mapping[str, Position]) -> Decimal:
        return ExposureEngine.long_exposure(positions) + ExposureEngine.short_exposure(positions)

    @staticmethod
    def net_exposure(positions: Mapping[str, Position]) -> Decimal:
        return ExposureEngine.long_exposure(positions) - ExposureEngine.short_exposure(positions)

    @staticmethod
    def asset_weights(
        cash_ledger: CashLedger, positions: Mapping[str, Position], base_currency: str = "USD"
    ) -> Mapping[str, Decimal]:
        nav = PortfolioValuation.portfolio_value(cash_ledger, positions, base_currency)
        if nav == 0:
            return {a: Decimal("0.00") for a in positions}
        return {
            asset: (p.market_value / nav).quantize(Decimal("0.0001"))
            for asset, p in positions.items()
        }
