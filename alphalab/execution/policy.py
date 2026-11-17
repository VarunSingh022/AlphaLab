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

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from alphalab.core.enums import Side
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.fill import FillStatus


class FillTiming(StrEnum):
    """When a simulated order fills, relative to the event that decided it.

    Recorded with every run and its results, because it decides how optimistic
    they are (ledger EXE-001).
    """

    #: At the price and instant of the event whose observation produced the
    #: order -- the bar close or quote midpoint the strategy has just seen. No
    #: real order trades at the price that triggered it, so this is optimistic by
    #: construction. It is what every simulated run did before v3.10, and stays
    #: the default so that a v3.9 run means what it meant.
    SAME_EVENT = "same_event"
    #: The order works until its asset's next market event and fills at that
    #: event's price and instant -- for bars, the next bar's close. The strategy
    #: decides on one observation and trades on the next: no look-ahead, at the
    #: cost of one event's delay. It gets one attempt there, as a same-event
    #: order gets one at its event; what a partial fill leaves is withdrawn.
    NEXT_EVENT = "next_event"
    #: As ``NEXT_EVENT``, but a market order's turn at a bar fills at the bar's
    #: **open**, where ``NEXT_EVENT`` fills it at the close (v4.3, ADR-0060):
    #: what an order sent after one close meets at the next open, a gap
    #: included. The fill is recorded at the instant of the event that delivered
    #: the bar, as an ``OPG`` auction fill is. Every other order kind already
    #: meets the next bar from its open under ``NEXT_EVENT`` and is unchanged;
    #: at a quote, trade or tick the turn is ``NEXT_EVENT``'s. With a trading
    #: calendar declared for the order's listing venue, an intraday bar outside
    #: the venue's sessions gives no working order its turn, so an order decided
    #: after a session's last bar fills at the next session's first bar's open.
    #: Like ``NEXT_EVENT`` it governs simulated orders: an execution plan's
    #: children are worked at the events their algorithm schedules.
    NEXT_OPEN = "next_open"


@dataclass(frozen=True, slots=True)
class RestRemainder:
    """What a partial fill leaves keeps working, for a stated while (v4.3, ADR-0060).

    Declared on ``ExecutionPipelineConfig.remainder``; without it a partial
    fill's remainder is cancelled in the step that partially filled it, as every
    run before ADR-0060 did (ADR-0014 decision 3). With it, the remainder of an
    order that would otherwise be cancelled keeps working: a turn at each of its
    asset's following events, of the order's own kind -- a market remainder
    makes a market attempt at each -- and a turn that fills nothing counts
    against its rest. When the rest ends the remainder is cancelled, and what it
    holds is freed. An immediate-or-cancel or fill-or-kill order is withdrawn
    after its first turn whatever the policy, and a limit order's remainder
    already rests at its limit.

    Attributes:
        events: How many of its asset's following events the remainder works
            for, a positive integer; or ``None`` when ``seconds`` is stated.
        seconds: How long after the partial fill the remainder works, in
            seconds, positive and finite; or ``None`` when ``events`` is stated.
            A turn at or after that instant is not taken.

    Raises:
        ExecutionValidationError: Unless exactly one of the two is stated, and
            stated as above.
    """

    events: int | None = None
    seconds: float | None = None

    def __post_init__(self) -> None:
        if (self.events is None) == (self.seconds is None):
            raise ExecutionValidationError(
                "A resting remainder states how long it rests: events or seconds, exactly one."
            )
        if self.events is not None and (
            isinstance(self.events, bool) or not isinstance(self.events, int) or self.events < 1
        ):
            raise ExecutionValidationError(
                f"A remainder rests for a positive whole number of events, got {self.events!r}."
            )
        if self.seconds is not None and (
            isinstance(self.seconds, bool)
            or not isinstance(self.seconds, int | float)
            or not math.isfinite(self.seconds)
            or self.seconds <= 0
        ):
            raise ExecutionValidationError(
                f"A remainder rests for a positive, finite number of seconds, got {self.seconds!r}."
            )


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


@dataclass(frozen=True, slots=True)
class StrictLiquidityCappedFill:
    """Fill only up to a share of the liquidity the event showed, and refuse an event without it.

    :class:`LiquidityCappedFill`'s cap, in its strict mode (v4.3, ADR-0060):
    given an event that carries no size, it has nothing to cap against, and it
    refuses rather than fill in full -- a run that declared a cap is told its
    data cannot apply one. A new class rather than a field on
    :class:`LiquidityCappedFill`, whose description, and so every capped run's
    identity, a new field would move.

    Attributes:
        participation_rate: The share of ``available_quantity`` this
            participant may take, a ``Decimal`` in ``(0, 1]``.

    Raises:
        ExecutionValidationError: When made with another rate; and from
            :meth:`decide`, given an event with no size.
    """

    participation_rate: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        rate = self.participation_rate
        if not isinstance(rate, Decimal) or not rate.is_finite() or not 0 < rate <= 1:
            raise ExecutionValidationError(
                f"A participation rate is a Decimal in (0, 1], got {rate!r}."
            )

    def decide(self, context: LiquidityContext) -> FillDecision:
        available = context.available_quantity
        if available is None:
            raise ExecutionValidationError(
                f"The event for {context.asset_id} at {context.timestamp!r} shows no size, and the "
                "run declared a strict liquidity cap: it cannot be applied to data without sizes."
            )
        cap = available * self.participation_rate
        if cap <= Decimal("0"):
            return FillDecision(FillStatus.NO_FILL, None)
        if cap >= context.requested_quantity:
            return FillDecision(FillStatus.FULL_FILL, context.requested_quantity)
        return FillDecision(FillStatus.PARTIAL_FILL, cap)
