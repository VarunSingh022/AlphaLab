"""Attribution modeling mapping PnL strictly to entities and domains."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto

from alphalab.core.contribution import StrategyContribution

__all__ = [
    "AttributionDimension",
    "AttributionMetrics",
    "AttributionReport",
    "Availability",
    "DimensionAttribution",
    "TradeFacts",
    "TradeRecord",
    "attribute",
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


# --------------------------------------------------------------------------- #
# v3.3: the other six dimensions
# --------------------------------------------------------------------------- #
#
# ``calculate_attribution`` above answers three dimensions from what a
# ``TradeRecord`` already carries. The six below -- country, currency, venue,
# broker, factor and execution -- need facts the record does not hold, and the
# whole design of this section is about where those facts come from.
#
# They come from the caller, explicitly, in a ``TradeFacts``. They are not
# derived, guessed or defaulted, because AlphaLab does not have them:
#
# * there is no security master, so no **country** exists anywhere in the
#   package (``InstrumentRecord`` carries ``sector`` and a listing ``exchange``
#   and stops there -- and an exchange is not a country of risk);
# * **broker** never reaches an ``ExecutionReport``. The report carries
#   ``venue``, which ``tests/regression/test_venue_concepts_stay_distinct.py``
#   keeps deliberately apart from both a listing exchange and a broker. Reading
#   ``venue`` as a broker would make every simulated fill attribute to a broker
#   called ``"SIM"``;
# * a **factor** decomposition is the output of a factor model, and which model
#   is the caller's decision, not this module's.
#
# So each dimension is reported with an :class:`Availability` beside it. A
# dimension nothing was supplied for is ``NO_METADATA`` with an empty breakdown,
# never a single ``"UNKNOWN"`` bucket holding the whole P&L -- the rule the
# sector breakdown has followed since v2.11, applied to the rest.


class AttributionDimension(Enum):
    """A dimension P&L can be broken down along."""

    STRATEGY = auto()
    ASSET = auto()
    SECTOR = auto()
    COUNTRY = auto()
    CURRENCY = auto()
    VENUE = auto()
    BROKER = auto()
    FACTOR = auto()
    EXECUTION = auto()


class Availability(Enum):
    """Whether a dimension could be computed, and how completely."""

    #: Every trade carried the metadata. The breakdown reconciles.
    AVAILABLE = auto()

    #: Some trades carried it and some did not. The breakdown covers only the
    #: ones that did, and :attr:`DimensionAttribution.uncovered` names the rest.
    #: It does **not** reconcile to the portfolio total, and says so.
    PARTIAL = auto()

    #: No trade carried it. The breakdown is empty. This is the honest report of
    #: an absent input, and it is why this enum exists.
    NO_METADATA = auto()


@dataclass(frozen=True, slots=True)
class TradeFacts:
    """Facts about one execution that a :class:`TradeRecord` does not carry.

    Supplied by whoever projected the fill -- the execution path knows the
    currency and venue of every report, and an operator knows their own country
    and broker mapping. Every field is optional and ``None`` means *not
    supplied*, which produces an unavailable dimension rather than a guess.

    Attributes:
        trade_id: The execution this describes. Matched against
            :attr:`TradeRecord.trade_id`.
        currency: Settlement currency of the fill. From
            ``ExecutionReport.currency``.
        venue: Execution venue. From ``ExecutionReport.venue``. This is the
            venue the order *executed at* and is not a broker and not a listing
            exchange.
        broker: Normalized broker label, if the caller has one. AlphaLab does
            not derive this from ``venue``; see this section's note.
        country: Country of risk. AlphaLab holds no such field and never infers
            one from a listing exchange or a currency.
        factor_pnl: This trade's realized P&L decomposed by factor, from the
            caller's own factor model. It need not sum to the trade's realized
            P&L -- :func:`attribute` computes the unexplained remainder and
            reports it as the ``residual`` bucket, which is what makes a factor
            breakdown reconcile rather than merely appear to.
        execution_costs: What this fill cost, itemized by role. The mapping
            :func:`alphalab.execution.costs.itemized` produces. Costs are
            reported as the **negative** contributions they are.
    """

    trade_id: str
    currency: str | None = None
    venue: str | None = None
    broker: str | None = None
    country: str | None = None
    factor_pnl: Mapping[str, Decimal] | None = None
    execution_costs: Mapping[str, Decimal] | None = None


@dataclass(frozen=True, slots=True)
class DimensionAttribution:
    """One dimension's breakdown, and how far it can be trusted.

    Attributes:
        dimension: Which dimension this is.
        buckets: P&L by bucket label, ordered by label.
        availability: Whether every trade contributed.
        covered: How many trades carried the metadata.
        total: How many trades were considered.
        uncovered: Trade ids that did not carry it, ordered. Empty when
            ``availability`` is :attr:`Availability.AVAILABLE`.
        reconciles: Whether ``sum(buckets.values())`` equals the realized P&L of
            the trades considered. Always ``False`` for a ``PARTIAL`` dimension,
            and deliberately ``False`` for :attr:`AttributionDimension.CURRENCY`
            and :attr:`AttributionDimension.EXECUTION` -- see
            :class:`AttributionReport`.
    """

    dimension: AttributionDimension
    buckets: Mapping[str, Decimal]
    availability: Availability
    covered: int
    total: int
    uncovered: tuple[str, ...]
    reconciles: bool


@dataclass(frozen=True, slots=True)
class AttributionReport:
    """Every requested dimension, and the total they are measured against.

    Attributes:
        dimensions: The breakdowns, keyed by dimension.
        realized_pnl: Total realized P&L of the trades considered. This is the
            figure a reconciling dimension sums to.
        currencies: The distinct settlement currencies seen, ordered. Length
            above one means :attr:`realized_pnl` adds figures denominated
            differently and is **not** a meaningful single number; the currency
            dimension is the one to read instead.

    Two dimensions do not reconcile, by construction rather than by defect:

    :attr:`AttributionDimension.CURRENCY`
        Its buckets are denominated in *different currencies*. Summing them
        would require exchange rates, and ADR-0020 forbids AlphaLab inventing
        one. The breakdown is dimensionally coherent within each bucket and
        deliberately has no total -- which is the point of reporting it.

    :attr:`AttributionDimension.EXECUTION`
        Its buckets are costs, not a partition of P&L. They sum to what
        execution cost, which is a different quantity from what the strategy
        earned.
    """

    dimensions: Mapping[AttributionDimension, DimensionAttribution]
    realized_pnl: Decimal
    currencies: tuple[str, ...]


#: Dimensions whose buckets partition realized P&L and must therefore sum to it.
_RECONCILING = (
    AttributionDimension.STRATEGY,
    AttributionDimension.ASSET,
    AttributionDimension.SECTOR,
    AttributionDimension.COUNTRY,
    AttributionDimension.VENUE,
    AttributionDimension.BROKER,
    AttributionDimension.FACTOR,
)


def _fact(facts: Mapping[str, TradeFacts], trade_id: str) -> TradeFacts | None:
    return facts.get(trade_id)


def attribute(
    trades: Sequence[TradeRecord],
    facts: Mapping[str, TradeFacts] | None = None,
    dimensions: Sequence[AttributionDimension] | None = None,
) -> AttributionReport:
    """Break realized P&L down along every requested dimension.

    ``facts`` maps a trade id to the metadata AlphaLab does not itself hold; a
    trade with no entry simply carries none, which makes every caller-supplied
    dimension ``PARTIAL`` or ``NO_METADATA`` rather than wrong. ``dimensions``
    defaults to all nine.

    Strategy shares come from :func:`split_realized_pnl`, the one place this
    repository decides how a netted fill's P&L divides between the strategies
    that asked for it. This function does not re-derive that split.

    Raises:
        AnalyticsValidationError: If ``trades`` contains two records with the
            same ``trade_id`` -- one execution attributed twice would break
            every reconciliation here in a way the totals would hide.
    """

    from alphalab.analytics.exceptions import AnalyticsValidationError

    supplied = dict(facts) if facts is not None else {}
    wanted = tuple(dimensions) if dimensions is not None else tuple(AttributionDimension)

    seen = [trade.trade_id for trade in trades]
    if len(set(seen)) != len(seen):
        repeated = sorted({name for name in seen if seen.count(name) > 1})
        raise AnalyticsValidationError(
            f"Duplicate trade ids in attribution input: {repeated}. One execution "
            "attributed twice would double its P&L in every breakdown while the "
            "reconciliation still passed, because the total would double with it."
        )

    total_pnl = sum((trade.realized_pnl for trade in trades), Decimal("0.00"))
    currencies = sorted(
        {
            fact.currency
            for fact in (_fact(supplied, trade.trade_id) for trade in trades)
            if fact is not None and fact.currency is not None
        }
    )

    built: dict[AttributionDimension, DimensionAttribution] = {}
    for dimension in wanted:
        built[dimension] = _build(dimension, trades, supplied, total_pnl)

    return AttributionReport(
        dimensions=built,
        realized_pnl=total_pnl,
        currencies=tuple(currencies),
    )


def _build(
    dimension: AttributionDimension,
    trades: Sequence[TradeRecord],
    facts: Mapping[str, TradeFacts],
    total_pnl: Decimal,
) -> DimensionAttribution:
    buckets: dict[str, Decimal] = {}
    uncovered: list[str] = []
    covered = 0

    for trade in trades:
        fact = _fact(facts, trade.trade_id)
        label = _label(dimension, trade, fact)

        if dimension is AttributionDimension.STRATEGY:
            if not trade.contributions:
                uncovered.append(trade.trade_id)
                continue
            covered += 1
            for strategy_id, share in split_realized_pnl(trade.realized_pnl, trade.contributions):
                buckets[strategy_id] = buckets.get(strategy_id, Decimal("0.00")) + share
            continue

        if dimension is AttributionDimension.FACTOR:
            if fact is None or fact.factor_pnl is None:
                uncovered.append(trade.trade_id)
                continue
            covered += 1
            explained = Decimal("0.00")
            for factor, amount in sorted(fact.factor_pnl.items()):
                buckets[factor] = buckets.get(factor, Decimal("0.00")) + amount
                explained += amount
            # What the caller's factor model did not explain. Recorded rather
            # than discarded: a factor breakdown that silently dropped it would
            # claim the model accounted for P&L it never touched.
            residual = trade.realized_pnl - explained
            buckets["residual"] = buckets.get("residual", Decimal("0.00")) + residual
            continue

        if dimension is AttributionDimension.EXECUTION:
            if fact is None or fact.execution_costs is None:
                uncovered.append(trade.trade_id)
                continue
            covered += 1
            for role, amount in sorted(fact.execution_costs.items()):
                # Negated: a cost reduces the result, and an attribution that
                # reported costs as positive contributions would read as though
                # commission had made money.
                buckets[role] = buckets.get(role, Decimal("0.00")) - amount
            continue

        if label is None:
            uncovered.append(trade.trade_id)
            continue
        covered += 1
        buckets[label] = buckets.get(label, Decimal("0.00")) + trade.realized_pnl

    availability = (
        Availability.AVAILABLE
        if covered == len(trades) and trades
        else Availability.NO_METADATA
        if covered == 0
        else Availability.PARTIAL
    )
    reconciles = (
        dimension in _RECONCILING
        and availability is Availability.AVAILABLE
        and sum(buckets.values(), Decimal("0.00")) == total_pnl
    )

    return DimensionAttribution(
        dimension=dimension,
        buckets=dict(sorted(buckets.items())),
        availability=availability,
        covered=covered,
        total=len(trades),
        uncovered=tuple(sorted(uncovered)),
        reconciles=reconciles,
    )


def _label(
    dimension: AttributionDimension, trade: TradeRecord, fact: TradeFacts | None
) -> str | None:
    """The bucket one trade falls in, or ``None`` when the fact is absent."""

    if dimension is AttributionDimension.ASSET:
        return trade.asset_id
    if dimension is AttributionDimension.SECTOR:
        return trade.sector_id
    if fact is None:
        return None
    if dimension is AttributionDimension.COUNTRY:
        return fact.country
    if dimension is AttributionDimension.CURRENCY:
        return fact.currency
    if dimension is AttributionDimension.VENUE:
        return fact.venue
    if dimension is AttributionDimension.BROKER:
        return fact.broker
    return None
