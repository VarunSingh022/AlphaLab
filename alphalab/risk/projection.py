"""The book before and after one order, on every measure a risk limit reads.

Why a projection
----------------
Until v3.10 every pre-trade check added an order's full notional to the
current figure, whatever the order did to the book. A fully invested account
could not sell to reduce a position (buying power), a book at its gross limit
could not cut its exposure, and a breached drawdown refused its own liquidation
(ledger RSK-001..003). Working orders were invisible, so several of them could
jointly breach a limit none of them breached alone (RSK-004).

The gate now asks what the book **would be** after the order, counting the
orders already working, and judges each limit on that projection:

* the **committed** position of an asset is what is filled plus the signed
  remaining quantity of its working orders;
* the **projected** position adds the order;
* an order **increases exposure** when the projected position is larger in
  magnitude than the committed one. A reducing order is never refused by a
  position, exposure, leverage, margin, drawdown or daily-loss limit -- the
  rule "a trade whose projection is no worse than the current state on the
  limited measure is not refused by that limit".

Every figure is in the account's **base currency**. The caller supplies the
order's price in it (the pipeline converts with the rate in force at the event
instant) and the price each working asset is valued at.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
from alphalab.risk.state import RiskState

__all__ = ["NO_WORKING_ORDERS", "BucketExposure", "RiskProjection", "WorkingExposure", "project"]

_ZERO: Final = Decimal("0")


@dataclass(frozen=True, slots=True)
class WorkingExposure:
    """What an asset's working orders commit, before they fill.

    Attributes:
        quantity: Signed remaining quantity of the asset's working orders:
            positive for buys, negative for sells.
        price: The price they are valued at, in the base currency.
        position: The asset's signed *filled* quantity, which decides whether
            the working orders grow the position (and spend buying power) or
            reduce it (and spend none).
    """

    quantity: Decimal
    price: Decimal
    position: Decimal


#: No order is working: every simulated fill that completes on its event.
NO_WORKING_ORDERS: Final[Mapping[str, WorkingExposure]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class BucketExposure:
    """The bucket an order's instrument is in along one dimension, before and after it.

    What a :class:`~alphalab.risk.limits.ClassificationLimit` reads (ledger
    OFE-001). The gate does not know which instruments share a label -- the
    registry does -- so the caller sums the bucket and says what it holds.

    Attributes:
        dimension: The dimension, as the limit names it.
        label: The bucket: the label the order's instrument carries along the
            dimension, or ``None`` when it carries none.
        committed_gross: The bucket's gross exposure counting working orders,
            before the order, in the base currency. Zero for ``label=None``.
        projected_gross: The same, after it.
    """

    dimension: str
    label: str | None
    committed_gross: Decimal
    projected_gross: Decimal


@dataclass(frozen=True, slots=True)
class RiskProjection:
    """One order's effect on the book, in the base currency.

    Attributes:
        order_quantity: The order's signed quantity.
        price: The order's price in the base currency.
        committed_position: Filled plus working, signed, before the order.
        projected_position: ``committed_position + order_quantity``.
        committed_gross: Gross exposure counting working orders, before the order.
        projected_gross: The same, after it.
        committed_net: Net exposure counting working orders, before the order.
        projected_net: The same, after it.
        increasing_notional: The value of the part of the order that grows the
            position's magnitude; zero for a reducing order. What buying power
            is charged.
        available_buying_power: Cash, less what working orders will spend
            growing positions.
        nav: Net asset value the ratios are taken over.
    """

    asset_id: str
    order_quantity: Decimal
    price: Decimal
    committed_position: Decimal
    projected_position: Decimal
    committed_gross: Decimal
    projected_gross: Decimal
    committed_net: Decimal
    projected_net: Decimal
    increasing_notional: Decimal
    available_buying_power: Decimal
    nav: Decimal

    @property
    def increases_exposure(self) -> bool:
        """Whether the order grows the position's magnitude, or reverses it.

        A reversal -- long to short or short to long -- opens a position in the
        other direction even when the new one is smaller, so it is growth: a
        reduce-only order may bring a position to zero and no further, which is
        the meaning exchanges give the term.
        """

        return _grows(self.committed_position, self.projected_position)

    @property
    def projected_position_value(self) -> Decimal:
        """Magnitude of the projected position, valued at the order price."""

        return ACCOUNTING_CONTEXT.multiply(abs(self.projected_position), self.price)


def project(
    state: RiskState,
    request: OrderRequest,
    *,
    position: Decimal,
    price: Decimal,
    working: Mapping[str, WorkingExposure] = NO_WORKING_ORDERS,
) -> RiskProjection:
    """Project the book after ``request``, counting ``working`` orders.

    Args:
        state: The gate's view of the book: exposure, cash and NAV, all in the
            base currency.
        request: The order.
        position: The asset's current signed *filled* quantity.
        price: The order's price in the base currency.
        working: Every asset with working orders, and what they commit.
    """

    ctx = ACCOUNTING_CONTEXT
    signed = request.quantity if request.side is Side.BUY else -request.quantity
    exposure = state.exposure

    committed_gross = exposure.gross_exposure
    committed_net = exposure.net_exposure
    working_growth = _ZERO
    for asset_id, pending in working.items():
        filled_value = exposure.asset_exposure.get(asset_id, _ZERO)
        pending_value = ctx.multiply(pending.quantity, pending.price)
        committed_value = ctx.add(filled_value, pending_value)
        committed_gross = ctx.add(
            committed_gross, ctx.subtract(abs(committed_value), abs(filled_value))
        )
        committed_net = ctx.add(committed_net, pending_value)
        growth = _growth(pending.position, ctx.add(pending.position, pending.quantity))
        working_growth = ctx.add(working_growth, ctx.multiply(growth, pending.price))

    pending_here = working.get(request.asset_id)
    committed_position = (
        ctx.add(position, pending_here.quantity) if pending_here is not None else position
    )
    projected_position = ctx.add(committed_position, signed)

    filled_value = exposure.asset_exposure.get(request.asset_id, _ZERO)
    committed_value = (
        ctx.add(filled_value, ctx.multiply(pending_here.quantity, pending_here.price))
        if pending_here is not None
        else filled_value
    )
    order_value = ctx.multiply(signed, price)
    projected_value = ctx.add(committed_value, order_value)
    projected_gross = ctx.add(
        ctx.subtract(committed_gross, abs(committed_value)), abs(projected_value)
    )
    projected_net = ctx.add(committed_net, order_value)

    increasing = ctx.multiply(_growth(committed_position, projected_position), price)

    return RiskProjection(
        asset_id=request.asset_id,
        order_quantity=signed,
        price=price,
        committed_position=committed_position,
        projected_position=projected_position,
        committed_gross=committed_gross,
        projected_gross=projected_gross,
        committed_net=committed_net,
        projected_net=projected_net,
        increasing_notional=increasing,
        available_buying_power=ctx.subtract(state.buying_power, working_growth),
        nav=state.current_nav,
    )


def _reverses(before: Decimal, after: Decimal) -> bool:
    return (before > _ZERO > after) or (before < _ZERO < after)


def _grows(before: Decimal, after: Decimal) -> bool:
    return abs(after) > abs(before) or _reverses(before, after)


def _growth(before: Decimal, after: Decimal) -> Decimal:
    """Units of new exposure: the growth in magnitude, or the whole reversed leg."""

    if _reverses(before, after):
        return abs(after)
    return max(_ZERO, ACCOUNTING_CONTEXT.subtract(abs(after), abs(before)))
