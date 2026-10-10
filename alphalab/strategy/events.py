"""Events and Intent models crossing the Strategy boundary.

:data:`StrategyInboundEvent` is what :class:`~alphalab.strategy.dispatcher.Dispatcher`
accepts, and it is the narrowest type this package can honestly write.

Two families reach a strategy hook. The three declared here --
:class:`FillEvent`, :class:`OrderEvent`, :class:`TimerEvent` -- and the canonical
market vocabulary in ``alphalab.market.events``, which **cannot be named from
this package**: ADR-0016 decision 3 is normative that ``alphalab.strategy``
acquires no dependency on ``alphalab.market``, and
``tests/regression/test_instrument_identity_reaches_a_fill.py`` enforces it.

Both families derive from :class:`~alphalab.common.events.BaseEvent`, and
``alphalab.common`` is below both, so that is the supertype the union has in
common and the one this alias names. Until v2.17 the parameter was ``Any``,
which is what ADR-0032 category C finding 4 recorded; ``BaseEvent`` is a real
narrowing rather than a cosmetic one -- a bare ``object()``, a dict, a string or
an intent is now a static error at the call site instead of an event that
silently reaches no hook.

**It is deliberately not narrower, and the reason is the interesting part.**
What separates ``alphalab.market.events.TickReceived`` from another
``TickReceived`` (``alphalab.live.events`` had one until v3.10; a host
application may define its own) is the module it is defined in, and no
static type can express that -- which is exactly why
:func:`~alphalab.strategy.dispatcher.market_hook_for` discriminates on
``(module, name)`` at runtime. A ``Protocol`` here would admit the impostor, so
it would read as a stronger guarantee while being a weaker one. The precise
check stays where it can be precise, and the static type says what is true.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from alphalab.common.events import BaseEvent
from alphalab.common.order_terms import MARKET, OrderTerms
from alphalab.common.point_in_time import StampedRecord

__all__ = [
    "FillEvent",
    "Intent",
    "IntentKind",
    "LifecycleTransitioned",
    "ObservationReceived",
    "OrderEvent",
    "SliceClosed",
    "StrategyInboundEvent",
    "StrategyRuntimeEvent",
    "TimerEvent",
]

#: What the dispatcher accepts. See the module docstring for why it is this and
#: not the exact union of routed classes.
type StrategyInboundEvent = BaseEvent


class IntentKind(StrEnum):
    """What an :class:`Intent`'s ``target`` states.

    Made explicit in v3.10 so the meaning is carried by the data rather than by
    a docstring: until then :class:`Intent` described itself as a "target
    position/weight" while allocation treated ``target`` as an order delta, so a
    strategy that followed the docstring accumulated positions (ledger ALC-002).
    The two target kinds are v3.11's (ledger FEA-001); a v3.10 intent goes on
    meaning what it meant.

    A **target** is measured against the strategy's *own* position -- its share
    of every fill of an order it contributed to, which allocation keeps -- plus
    its share of what is still working, never the account's: two strategies
    trading one instrument each reach their own target. The difference is
    rounded toward zero onto the instrument's lot grid, and an order below the
    instrument's minimum notional is refused rather than scaled up. Emitting the
    same target twice asks for nothing the second time.
    """

    #: A signed order delta: how much to buy (positive) or sell (negative) now,
    #: in the units the configured sizing model reads -- a quantity for
    #: ``FixedQuantitySizing``, a notional for ``FixedDollarSizing``, a fraction
    #: of the strategy's capital for ``TargetWeightSizing``. It does not depend
    #: on the current position: emitting it twice asks for it twice.
    DELTA = "delta"
    #: The signed position the strategy wants to hold, in the asset's units,
    #: scaled by ``strength``. Not read by the sizing model: it is a quantity.
    TARGET_QUANTITY = "target_quantity"
    #: The signed fraction of the strategy's capital it wants to hold in the
    #: asset, scaled by ``strength`` -- the capital ``TargetWeightSizing`` reads,
    #: valued at the asset's price in the budget's currency.
    TARGET_WEIGHT = "target_weight"


@dataclass(frozen=True, slots=True)
class Intent:
    """What a strategy asks for: an order delta, or a position to hold.

    This is the sole mechanism for a strategy to affect the outside world.
    What ``target`` states is ``kind``'s: by default (:attr:`IntentKind.DELTA`)
    it is how much to trade now, read by the run's sizing model; since v3.11 it
    may instead be the position the strategy wants to hold, as a quantity or as
    a weight of its capital, and allocation computes the order that gets there
    (see :class:`IntentKind`). Allocation nets what every strategy asks for per
    instrument and terms into one order.

    Attributes:
        strategy_id: The strategy asking.
        instrument: The asset it is for.
        target: What ``kind`` says: a signed delta in the sizing model's units,
            or the signed position to hold, as a quantity or as a weight of
            the strategy's capital.
        strength: A scale in ``[0, 1]`` applied to ``target`` -- by the sizing
            model for a delta, by allocation for a target.
        terms: How the order is to be executed -- market, limit, stop, and how
            long it lives. A market order good for the day unless stated. Since
            v3.11; it replaced ``execution_directive``, a mapping nothing ever
            read (ledger EXE-003). Allocation nets only intents whose terms are
            equal: a limit and a market order for one asset are two orders.
        kind: What ``target`` states: a delta (the default), a target
            quantity or a target weight. See :class:`IntentKind`.
    """

    strategy_id: str
    instrument: str
    target: Decimal
    strength: Decimal = Decimal("1.0")
    horizon: str = "default"
    terms: OrderTerms = MARKET
    correlation_id: str = ""
    timestamp: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)
    kind: IntentKind = IntentKind.DELTA


@dataclass(frozen=True, slots=True)
class StrategyRuntimeEvent(BaseEvent):
    """Base class for all Strategy Runtime system events."""

    pass


@dataclass(frozen=True, slots=True)
class LifecycleTransitioned(StrategyRuntimeEvent):
    """Emitted when a strategy moves between lifecycle states."""

    strategy_id: str
    old_state: str
    new_state: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TimerEvent(StrategyRuntimeEvent):
    """Synthetic event injected by the Scheduler to wake up strategies."""

    timer_id: str
    schedule_type: str


@dataclass(frozen=True, slots=True)
class OrderEvent(StrategyRuntimeEvent):
    """What became of an order the strategy contributed to, as of the end of a step.

    Since v3.11 the execution pipeline delivers one after every step that
    touched an order -- created it, filled it, cancelled or expired it -- and
    one for every request risk refused, to each strategy that contributed to it
    and subscribed to ``orders`` (ledger EXE-005). A step that accepts and fills
    an order delivers one event, carrying ``"filled"``: the status the order
    ended the step in, not every status it passed through.

    Attributes:
        order_id: The order, or the refused request, by id.
        instrument: Its asset.
        status: The order's status -- an ``OrderStatus`` value -- or
            ``"rejected"`` for a request risk refused before it became an order.
        reason: Why, for a rejection. Empty otherwise.
        quantity: The order's quantity, when there is an order.
        filled_quantity: How much of it has filled, when there is an order.
    """

    order_id: str
    instrument: str
    status: str
    reason: str = ""
    quantity: Decimal | None = None
    filled_quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FillEvent(StrategyRuntimeEvent):
    """A fill of an order the strategy contributed to.

    Delivered by the execution pipeline after the step the fill happened in, to
    each contributing strategy subscribed to ``fills`` (ledger EXE-005). Until
    v3.11 the dispatcher routed this type and nothing constructed it, so a
    strategy on the canonical path never learned of its own fills.

    Attributes:
        order_id: The order that filled.
        instrument: Its asset.
        fill_quantity: What filled, unsigned -- the whole order's fill.
        fill_price: At what price.
        side: The order's side, ``"buy"`` or ``"sell"``.
        attributed_quantity: This strategy's signed share of the fill: the fill
            divided among the strategies a netted order represents by their
            signed contributions, exactly as realized P&L is. For an order one
            strategy asked for alone, the signed fill itself.
        execution_id: The fill's identity. A fill is delivered once.
    """

    order_id: str
    instrument: str
    fill_quantity: Decimal
    fill_price: Decimal
    side: str = ""
    attributed_quantity: Decimal | None = None
    execution_id: str = ""


@dataclass(frozen=True, slots=True)
class SliceClosed(StrategyRuntimeEvent):
    """Every record sharing one instant has been published (ledger EXE-004).

    A strategy trading across instruments is dispatched once per record, so at
    any single record it sees a market that is part-way through an instant. A
    slice is the instant complete: delivered to strategies subscribed to
    ``slices`` after the last record carrying ``timestamp``, it is the one
    moment a cross-sectional decision sees every price of that instant.

    Attributes:
        assets: The assets a record at this instant was about, sorted.
    """

    assets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationReceived(StrategyRuntimeEvent):
    """A point-in-time record has become knowable (ledger OFE-009).

    External information -- a statement figure, a news item, an alternative-data
    reading -- delivered on the execution path at its **knowledge instant**:
    ``timestamp`` is the instant the record became knowable under the rule its
    schedule was made with, never earlier. Delivered to strategies subscribed to
    ``observations`` (or to ``observations:<subject>``) that define
    ``on_observation``; what they ask for rests until each asset's next event,
    as a timer's orders do -- information is not a price.

    Attributes:
        delivery_id: ``"<set version>:<record id>"`` -- which set, which record.
        subject: What the record is about, by the source's key.
        record: The record itself: an
            :class:`~alphalab.alt_data.observation.ExternalObservation`,
            :class:`~alphalab.alt_data.information.InformationEvent` or
            :class:`~alphalab.alt_data.fundamentals.FundamentalObservation`.
            Typed by the protocol in ``alphalab.common`` they all satisfy,
            because this package imports nothing else.
    """

    delivery_id: str = ""
    subject: str = ""
    record: StampedRecord | None = None
