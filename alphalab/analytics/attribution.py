"""Attribution modeling mapping PnL strictly to entities and domains."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.core.contribution import StrategyContribution

__all__ = [
    "AttributionMetrics",
    "TradeRecord",
    "calculate_attribution",
    "split_realized_pnl",
]


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """Immutable analytics record for one execution.

    One record is produced per fill, not per round trip: an opening fill
    realizes nothing and still has a record, which is why ``realized_pnl`` is
    frequently zero and ``holding_period_seconds`` is frequently ``None``.

    Attributes:
        trade_id: Identifier of the execution this record describes.
        contributions: Which strategies asked for the order that produced this
            fill, signed and pre-netting, ordered by ``strategy_id``. Replaces
            the ``strategy_id`` field this record carried until v2.6, which held
            the fabricated ``"ALLOC-NETTED"`` for every pipeline fill and made
            every strategy-level number a statement about a strategy that does
            not exist. Empty when the producer supplied none.
        asset_id: Asset executed.
        sector_id: Sector the asset belongs to, or ``None`` when unknown.
            AlphaLab has no security master, so the execution path supplies
            ``None`` and the sector breakdown is empty rather than bucketed
            under a placeholder.
        realized_pnl: P&L this fill crystallised. Zero for an opening fill.
        notional_value: ``fill_quantity * fill_price``.
        holding_period_seconds: How long the reduced or closed position had been
            held, or ``None`` for a fill that opened or increased one -- such a
            fill has held nothing, and ``0.0`` would be a measurement it never
            made.
    """

    trade_id: str
    asset_id: str
    sector_id: str | None
    realized_pnl: Decimal
    notional_value: Decimal
    holding_period_seconds: float | None
    contributions: tuple[StrategyContribution, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AttributionMetrics:
    """Immutable view of PnL grouped by distinct architectural domains."""

    pnl_by_strategy: Mapping[str, Decimal]
    pnl_by_asset: Mapping[str, Decimal]
    pnl_by_sector: Mapping[str, Decimal]


def split_realized_pnl(
    realized_pnl: Decimal, contributions: Sequence[StrategyContribution]
) -> tuple[tuple[str, Decimal], ...]:
    """Divide one fill's realized P&L among the strategies that asked for it.

    Each strategy's weight is its **signed** contribution over the net::

        weight_i = q_i / sum(q_j)

    Signed, not absolute. Absolute weights stay inside ``[0, 1]`` but hand a
    strategy that wanted to *sell* a positive share of a *long* position's gain,
    which is a wrong number wearing a comfortable range. Signed weights sum to
    exactly one and carry the right sign, at the cost of leaving ``[0, 1]`` when
    strategies oppose each other: ``+60`` against ``-59`` nets to ``1``, and the
    weights ``+60`` and ``-59`` say precisely that -- 59 units crossed
    internally, one held. Those magnitudes are what internal crossing means.

    The net is never zero for an order that exists: an asset whose intents net
    flat produces no order, no fill and nothing to attribute.

    The parts sum to ``realized_pnl`` **exactly**. Every share but the last is
    rounded, and the last is obtained by subtraction -- the technique
    :meth:`alphalab.portfolio.position.Position._apply_long` already uses to
    split a cost basis, and the reason a split can never introduce a residue.
    """

    if not contributions:
        return ()

    net = sum((c.quantity for c in contributions), Decimal("0"))
    if net == 0:
        # Unreachable through allocation, which emits no order for a flat net.
        # A caller who builds such a record directly gets an equal split rather
        # than a division by zero.
        share = _quantize(realized_pnl / Decimal(len(contributions)))
        head = tuple((c.strategy_id, share) for c in contributions[:-1])
        return (*head, (contributions[-1].strategy_id, realized_pnl - share * (len(head))))

    parts: list[tuple[str, Decimal]] = []
    assigned = Decimal("0")
    for contribution in contributions[:-1]:
        share = _quantize(realized_pnl * contribution.quantity / net)
        parts.append((contribution.strategy_id, share))
        assigned += share
    parts.append((contributions[-1].strategy_id, realized_pnl - assigned))
    return tuple(parts)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def calculate_attribution(trades: Sequence[TradeRecord]) -> AttributionMetrics:
    """Generates immutable PnL groupings based on standard metadata tags.

    A trade whose sector is unknown is omitted from ``pnl_by_sector`` rather
    than collected under a placeholder: an absent breakdown is preferable to a
    fictional one. A trade with no contributions is omitted from
    ``pnl_by_strategy`` for the same reason.
    """

    by_strat: dict[str, Decimal] = {}
    by_asset: dict[str, Decimal] = {}
    by_sector: dict[str, Decimal] = {}

    for t in trades:
        by_asset[t.asset_id] = by_asset.get(t.asset_id, Decimal("0.00")) + t.realized_pnl
        if t.sector_id is not None:
            by_sector[t.sector_id] = by_sector.get(t.sector_id, Decimal("0.00")) + t.realized_pnl
        for strategy_id, share in split_realized_pnl(t.realized_pnl, t.contributions):
            by_strat[strategy_id] = by_strat.get(strategy_id, Decimal("0.00")) + share

    return AttributionMetrics(
        pnl_by_strategy=by_strat,
        pnl_by_asset=by_asset,
        pnl_by_sector=by_sector,
    )
