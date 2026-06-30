from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.position import Position


class PnLEngine:
    @staticmethod
    def realized_pnl(positions: Mapping[str, Position]) -> Decimal:
        return sum((p.realized_pnl for p in positions.values()), Decimal("0.00"))

    @staticmethod
    def unrealized_pnl(positions: Mapping[str, Position]) -> Decimal:
        return sum((p.unrealized_pnl for p in positions.values()), Decimal("0.00"))

    @staticmethod
    def portfolio_pnl(positions: Mapping[str, Position]) -> Decimal:
        return PnLEngine.realized_pnl(positions) + PnLEngine.unrealized_pnl(positions)
