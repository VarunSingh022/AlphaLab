"""Fill policies: how a venue responds to one order at one market event.

The execution engine simulates *a* fill it is told to produce. What it is never
asked is whether that fill should happen at all, in full, or at that size --
until v2.2 the caller passed a fixed :class:`~alphalab.execution.fill.FillStatus`
that applied to every order on the event, which is fine for a test that scripts
one outcome and useless for a backtest that must decide per order.

A :class:`FillPolicy` is that decision, and nothing else. It reads a
:class:`LiquidityContext` -- what was asked for, at what price, against what the
venue had available -- and returns a :class:`FillDecision`. It never touches
state, so the same policy drives a backtest and a replay identically, and a run
is reproducible from its configuration.

Policies deliberately depend on nothing but :mod:`alphalab.core`: the pipeline
projects the order and the market event into a context, so a policy never needs
to know how an order or a quote is modelled.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from alphalab.core.enums import Side
from alphalab.execution.fill import FillStatus


@dataclass(frozen=True, slots=True)
class LiquidityContext:
    """What one order is asking of the venue at one market event.

    Attributes:
        asset_id: Asset being traded.
        side: Direction of the order.
        requested_quantity: Quantity still to fill.
        price: Reference market price for the event.
        available_quantity: Quantity the venue is showing, or ``None`` when the
            event carries no size information (a quote with no size, say).
        timestamp: Event timestamp.
    """

    asset_id: str
    side: Side
    requested_quantity: Decimal
    price: Decimal
    available_quantity: Decimal | None
    timestamp: float


@dataclass(frozen=True, slots=True)
class FillDecision:
    """The venue's answer: what to fill, and how much of it.

    ``quantity`` of ``None`` means "whatever remains on the order", which is the
    only sensible reading for a non-trading status.
    """

    status: FillStatus
    quantity: Decimal | None = None


class FillPolicy(Protocol):
    """Decides the execution outcome for one order at one market event."""

    def decide(self, context: LiquidityContext) -> FillDecision: ...


@dataclass(frozen=True, slots=True)
class ImmediateFill:
    """Fill the whole requested quantity at the event price.

    The default, and the semantics every pre-v2.2 caller got by passing
    ``FillStatus.FULL_FILL``: liquidity is assumed unlimited.
    """

    def decide(self, context: LiquidityContext) -> FillDecision:
        return FillDecision(FillStatus.FULL_FILL, context.requested_quantity)


@dataclass(frozen=True, slots=True)
class StaticFill:
    """Always return the same outcome, whatever the market shows.

    Expresses the fixed ``fill_status`` / ``fill_quantity`` arguments as a
    policy, so a configuration can select a rejecting, expiring or unfilled
    venue without a separate code path.
    """

    status: FillStatus = FillStatus.FULL_FILL
    quantity: Decimal | None = None

    def decide(self, context: LiquidityContext) -> FillDecision:
        quantity = self.quantity if self.quantity is not None else context.requested_quantity
        return FillDecision(self.status, quantity)


@dataclass(frozen=True, slots=True)
class LiquidityCappedFill:
    """Fill only up to a share of the liquidity the event showed.

    ``participation_rate`` is the fraction of ``available_quantity`` this
    participant may take. An order larger than that share fills partially; an
    event showing no liquidity produces no fill at all. When the event carries
    no size information the policy has nothing to cap against and fills in
    full, which keeps it usable on quote feeds without sizes.
    """

    participation_rate: Decimal = Decimal("1")

    def decide(self, context: LiquidityContext) -> FillDecision:
        available = context.available_quantity
        if available is None:
            return FillDecision(FillStatus.FULL_FILL, context.requested_quantity)

        cap = available * self.participation_rate
        if cap <= Decimal("0"):
            return FillDecision(FillStatus.NO_FILL, None)
        if cap >= context.requested_quantity:
            return FillDecision(FillStatus.FULL_FILL, context.requested_quantity)
        return FillDecision(FillStatus.PARTIAL_FILL, cap)
