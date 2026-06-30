from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.exposure import ExposureEngine
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.position import Position


class MarginEngine:
    @staticmethod
    def initial_margin(
        positions: Mapping[str, Position], margin_rate: Decimal = Decimal("0.50")
    ) -> Decimal:
        return (ExposureEngine.gross_exposure(positions) * margin_rate).quantize(Decimal("0.01"))

    @staticmethod
    def maintenance_margin(
        positions: Mapping[str, Position], maint_rate: Decimal = Decimal("0.25")
    ) -> Decimal:
        return (ExposureEngine.gross_exposure(positions) * maint_rate).quantize(Decimal("0.01"))

    @staticmethod
    def buying_power(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        base_currency: str = "USD",
        margin_rate: Decimal = Decimal("0.50"),
    ) -> Decimal:
        nav = NAVCalculator.calculate(cash_ledger, positions, base_currency)
        used_margin = MarginEngine.initial_margin(positions, margin_rate)
        return max(Decimal("0.00"), (nav - used_margin) / margin_rate)

    @staticmethod
    def margin_remaining(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        base_currency: str = "USD",
        margin_rate: Decimal = Decimal("0.50"),
    ) -> Decimal:
        nav = NAVCalculator.calculate(cash_ledger, positions, base_currency)
        used = MarginEngine.initial_margin(positions, margin_rate)
        return nav - used
