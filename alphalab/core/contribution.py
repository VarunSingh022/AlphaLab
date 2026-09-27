"""What each strategy contributed to a netted order.

A netted order can represent several strategies at once -- ``MOMENTUM`` wanting
``+60`` and ``MEANREV`` wanting ``+40`` produce one ``BUY 100`` -- so a single
``strategy_id`` cannot describe it. Before v2.6 the allocator dropped strategy
identity in :func:`~alphalab.allocation.allocator.IntentAllocator.size_intents`,
one statement before netting, and stamped every emitted request with the
fabricated ``"ALLOC-NETTED"``. Every strategy-level P&L number the repository
could produce was therefore a number about a strategy that does not exist.

The information was never missing, only discarded: ``size_intents`` holds
``(strategy_id, instrument, quantity)`` and returned two of the three. A
contribution is that third element, kept.

Contributions carry **signed quantities**, not precomputed weights. Quantities
are exact inputs; a weight is derived, and deriving it once at the point of use
avoids a second rounding policy -- the same reason
:mod:`alphalab.portfolio.money` rounds once, at entry. See ADR-0015 decision 4.

This type lives in :mod:`alphalab.core` rather than in
:mod:`alphalab.allocation`, for the reason ADR-0008 gives: it is a field of
:class:`~alphalab.core.order_request.OrderRequest`, which allocation and risk
share, and a canonical entity's field type cannot live downstream of the
canonical package. It is re-exported from ``alphalab.allocation`` because
allocation is what produces it.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal

__all__ = ["StrategyContribution", "contributions_from", "split_by_contribution"]


@dataclass(frozen=True, slots=True)
class StrategyContribution:
    """One strategy's signed share of a netted order.

    Attributes:
        strategy_id: The strategy that asked for it. Always a real strategy
            identifier -- a contribution is never synthesised.
        quantity: Signed quantity in the asset's own units, positive to buy and
            negative to sell, as the sizing model produced it and *before*
            netting. It is not the quantity that executed.
    """

    strategy_id: str
    quantity: Decimal


def contributions_from(
    sized: Iterable[tuple[str, Decimal]],
) -> tuple[StrategyContribution, ...]:
    """Aggregate per-strategy quantities into a canonically ordered tuple.

    A strategy that expressed two intents for the same asset in one batch
    contributes their sum, so a contribution names each strategy exactly once.

    The result is ordered by ``strategy_id`` ascending rather than by the order
    intents happened to arrive in. Two runs that emit the same intents in a
    different sequence therefore produce equal ``OrderRequest`` values, which is
    what keeps a backtest and its replay comparable field for field.
    """

    totals: dict[str, Decimal] = {}
    for strategy_id, quantity in sized:
        totals[strategy_id] = totals.get(strategy_id, Decimal("0")) + quantity
    return _ordered(totals)


def _ordered(totals: Mapping[str, Decimal]) -> tuple[StrategyContribution, ...]:
    return tuple(
        StrategyContribution(strategy_id, totals[strategy_id]) for strategy_id in sorted(totals)
    )


def split_by_contribution(
    amount: Decimal,
    contributions: Sequence[StrategyContribution],
    quantum: Decimal,
) -> tuple[tuple[str, Decimal], ...]:
    """Divide ``amount`` among the strategies that asked for an order, exactly.

    Each strategy's weight is its **signed** contribution over the net::

        weight_i = q_i / sum(q_j)

    and every share but the last is ``amount * weight_i`` rounded to
    ``quantum``; the last is what remains, so the parts sum to ``amount``
    exactly and a split can never introduce a residue. A flat net -- which
    allocation never produces, because a flat net emits no order -- splits
    equally rather than dividing by zero. The arithmetic runs in a fixed
    context, never the caller's.

    This is the arithmetic :func:`alphalab.analytics.attribution.split_realized_pnl`
    has divided a fill's P&L by since v2.6, moved here in v3.9 so that the
    execution-quality measurements in :mod:`alphalab.execution.quality` divide
    an order's cost by the same rule without importing :mod:`alphalab.analytics`
    -- the two are siblings over this package. ``split_realized_pnl`` now calls
    this with a quantum of ``0.01``, and every figure it returns is unchanged.
    Signed weights, not absolute ones: see ``split_realized_pnl`` for why.
    """

    if not contributions:
        return ()

    ctx = _SPLIT_CONTEXT
    net = Decimal("0")
    for c in contributions:
        net = ctx.add(net, c.quantity)
    if net == 0:
        share = ctx.quantize(ctx.divide(amount, Decimal(len(contributions))), quantum)
        head = tuple((c.strategy_id, share) for c in contributions[:-1])
        remainder = ctx.subtract(amount, ctx.multiply(share, Decimal(len(head))))
        return (*head, (contributions[-1].strategy_id, remainder))

    parts: list[tuple[str, Decimal]] = []
    assigned = Decimal("0")
    for contribution in contributions[:-1]:
        share = ctx.quantize(ctx.divide(ctx.multiply(amount, contribution.quantity), net), quantum)
        parts.append((contribution.strategy_id, share))
        assigned = ctx.add(assigned, share)
    parts.append((contributions[-1].strategy_id, ctx.subtract(amount, assigned)))
    return tuple(parts)


#: The arithmetic a split is computed in: 28 significant digits, half-even -- the
#: values of Python's default decimal context, pinned so a split does not change
#: when a caller's thread changes its own. Every figure the v2.6 arithmetic
#: produced under the default context is reproduced exactly.
_SPLIT_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
