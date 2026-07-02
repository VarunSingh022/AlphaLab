"""Attribution modeling mapping PnL strictly to entities and domains."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """Immutable representation of a resolved round-trip trade."""

    trade_id: str
    strategy_id: str
    asset_id: str
    sector_id: str
    realized_pnl: Decimal
    notional_value: Decimal
    holding_period_seconds: float


@dataclass(frozen=True, slots=True)
class AttributionMetrics:
    """Immutable view of PnL grouped by distinct architectural domains."""

    pnl_by_strategy: Mapping[str, Decimal]
    pnl_by_asset: Mapping[str, Decimal]
    pnl_by_sector: Mapping[str, Decimal]


def calculate_attribution(trades: tuple[TradeRecord, ...]) -> AttributionMetrics:
    """Generates immutable PnL groupings based on standard metadata tags."""
    by_strat: dict[str, Decimal] = {}
    by_asset: dict[str, Decimal] = {}
    by_sector: dict[str, Decimal] = {}

    for t in trades:
        by_strat[t.strategy_id] = by_strat.get(t.strategy_id, Decimal("0.00")) + t.realized_pnl
        by_asset[t.asset_id] = by_asset.get(t.asset_id, Decimal("0.00")) + t.realized_pnl
        by_sector[t.sector_id] = by_sector.get(t.sector_id, Decimal("0.00")) + t.realized_pnl

    return AttributionMetrics(
        pnl_by_strategy=MappingProxyType(by_strat),
        pnl_by_asset=MappingProxyType(by_asset),
        pnl_by_sector=MappingProxyType(by_sector),
    )
