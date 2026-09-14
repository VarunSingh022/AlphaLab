"""Immutable StrategyContext providing a read-only window into the world.

The nine fields have existed since v0.10.0. What each of them *supplies* has
arrived in stages: v2.10 populated ``portfolio``, ``orders``, ``risk_view`` and
``market`` from the pipeline (ADR-0026), and v2.15 completed the boundary with
``history`` and ``universe``, which ADR-0026 deferred with reasons it also
recorded -- history "requires a clock-bounded accessor whose bound is enforced
at construction", universe "requires deciding whether membership is
configuration, instrument-registry state, or a risk control".

v2.16 finishes the job v2.15 started on two of the six. ADR-0031 identified a
field whose protocol "declares no methods, and every construction site in the
repository passes ``object()``" as a *decorative* surface, and gave
``HistoryAccessorProtocol`` and ``UniverseProtocol`` the members their views
implement. The four protocols v2.10 had already populated were left exactly as
they were: empty. The consequence was visible to every user of this library,
which ships ``py.typed`` -- under ``mypy --strict`` a strategy could write
``context.history.bars(asset)`` and not ``context.portfolio.cash("USD")``, which
reported *"PortfolioSnapshotProtocol has no attribute cash"* for the one surface
v2.10 exists to supply. The four protocols below now declare what
:mod:`alphalab.runtime.context_views` implements, and the ``object()``
placeholders are replaced by null objects that *say* they supply nothing.

**Payload types stay ``Any`` deliberately.** ADR-0016 decision 3 is normative:
"``alphalab.strategy`` acquires no dependency on ``alphalab.instrument`` or
``alphalab.market``". A quote, a bar, an instrument record and a risk limit
therefore cannot be named here. ``Decimal`` can, and is -- it is stdlib, it is
the canonical monetary type (ADR-0008), and it is where a wrong type would
actually cost money. This is the same line ``HistoryAccessorProtocol`` already
draws, not a new one.

``clock``, ``logger`` and ``config`` remain the caller's, deliberately and
unchanged. The time semantic that actually needed settling is the look-ahead
bound, and that lives on the history accessor as ``as_of``.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class PortfolioSnapshotProtocol(Protocol):
    """Read-only view of portfolio state.

    Implemented by :class:`~alphalab.runtime.context_views.PortfolioView`, which
    the pipeline overlays on every dispatch. The book is already marked at the
    dispatched event's prices when a strategy reads it (ADR-0026).
    """

    @property
    def account(self) -> Any:
        """The account the book belongs to."""
        ...

    @property
    def positions(self) -> Mapping[str, Any]:
        """Every open position, keyed by ``asset_id``."""
        ...

    @property
    def balances(self) -> Mapping[Any, Decimal]:
        """Cash balances, keyed by currency."""
        ...

    @property
    def reserved(self) -> Mapping[Any, Decimal]:
        """Cash reserved against working orders, keyed by currency."""
        ...

    @property
    def realized_pnl(self) -> Mapping[Any, Decimal]:
        """Cumulative realized profit and loss, keyed by settlement currency.

        Keyed rather than scalar as of v2.17. A book may settle in more than one
        currency (ADR-0035), and a single number summed across two would be a
        figure in no currency at all -- the defect ADR-0020 removed from
        valuation. Read one currency with :meth:`realized_pnl_in`.
        """
        ...

    @property
    def commission_paid(self) -> Mapping[Any, Decimal]:
        """Cumulative commission paid, keyed by settlement currency."""
        ...

    def realized_pnl_in(self, currency: str) -> Decimal:
        """Realized profit and loss settled in ``currency``; zero if none was.

        The exact sibling of :meth:`cash`, and for the same reason: a keyed
        lookup returns what it was asked for and claims nothing about any other
        currency, so it needs no rate and refuses nothing.
        """
        ...

    def commission_paid_in(self, currency: str) -> Decimal:
        """Commission expensed in ``currency``; zero if none was."""
        ...

    def position(self, asset_id: str) -> Any | None:
        """The position in ``asset_id``, or ``None`` when there is none."""
        ...

    def quantity(self, asset_id: str) -> Decimal:
        """Signed quantity held in ``asset_id``; zero when there is no position."""
        ...

    def cash(self, currency: str) -> Decimal:
        """Cash balance in ``currency``."""
        ...

    def available_cash(self, currency: str) -> Decimal:
        """Cash in ``currency`` not reserved against a working order."""
        ...


class MarketViewProtocol(Protocol):
    """Read-only view of current market data.

    Implemented by :class:`~alphalab.runtime.context_views.MarketView`. It
    exposes the *latest* record per asset and no history at all -- ADR-0026
    decision 9, which ``test_strategy_context_visibility`` pins. History is
    :attr:`StrategyContext.history`, which is bounded at the event's timestamp.
    """

    @property
    def prices(self) -> Mapping[str, Decimal]:
        """The price the pipeline is marking each asset at."""
        ...

    @property
    def assets(self) -> Sequence[str]:
        """Every ``asset_id`` the market state has seen."""
        ...

    def quote(self, asset_id: str) -> Any | None:
        """The latest quote for ``asset_id``, or ``None``."""
        ...

    def tick(self, asset_id: str) -> Any | None:
        """The latest trade print for ``asset_id``, or ``None``."""
        ...

    def bar(self, asset_id: str) -> Any | None:
        """The latest closed bar for ``asset_id``, or ``None``."""
        ...

    def price(self, asset_id: str) -> Decimal | None:
        """The mark for ``asset_id``, or ``None`` when it has never been priced."""
        ...


class ClockProtocol(Protocol):
    """Virtual or monotonic clock source."""

    def now(self) -> float: ...


class ScopedLoggerProtocol(Protocol):
    """Structured, strategy-scoped write-only logger."""

    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


class RiskViewProtocol(Protocol):
    """Read-only view of current risk limits and exposure.

    Implemented by :class:`~alphalab.runtime.context_views.RiskView`, built from
    the risk state *after* it was resynced from the marked book, so a strategy
    and the risk engine judging its order read the same numbers.
    """

    @property
    def active_limits(self) -> Any:
        """The limits in force for this run."""
        ...

    @property
    def cash(self) -> Decimal:
        """Cash as risk accounts for it."""
        ...

    @property
    def buying_power(self) -> Decimal:
        """Capital available to open new exposure."""
        ...

    @property
    def current_nav(self) -> Decimal:
        """Net asset value at the dispatched event."""
        ...

    @property
    def peak_nav(self) -> Decimal:
        """Highest net asset value reached, which drawdown is measured against."""
        ...

    @property
    def daily_loss(self) -> Decimal:
        """Loss accumulated against the daily-loss limit."""
        ...

    @property
    def margin(self) -> Any:
        """Current margin status."""
        ...

    @property
    def exposure(self) -> Any:
        """Current exposure status, including the sector breakdown."""
        ...


class OrderFacadeProtocol(Protocol):
    """Constrained facade for order queries, NOT for direct placement.

    Implemented by :class:`~alphalab.runtime.context_views.OrderView`, which
    yields this strategy's *share* of each working order. A netted order
    represents several strategies and has no single owner (ADR-0015 decision 4),
    so the view is built from the allocation contributions and never from
    ``OrderBook.orders_for_strategy`` -- which answers ``()`` for every order the
    execution path produces.

    Placement is not here and is not coming: a strategy emits an
    :class:`~alphalab.strategy.events.Intent` and
    :class:`~alphalab.allocation.engine.AllocationEngine` sizes it.
    """

    def __iter__(self) -> Iterator[Any]:
        """This strategy's share of each working order."""
        ...

    def __len__(self) -> int:
        """How many working orders this strategy contributes to."""
        ...

    def __bool__(self) -> bool:
        """Whether this strategy contributes to any working order."""
        ...

    def for_asset(self, asset_id: str) -> Sequence[Any]:
        """This strategy's shares of the working orders in ``asset_id``."""
        ...

    def net_quantity(self, asset_id: str) -> Decimal:
        """Signed quantity this strategy has working in ``asset_id``."""
        ...


class HistoryAccessorProtocol(Protocol):
    """Clock-bounded accessor for historical data.

    Until v2.15 this declared no methods and every construction site passed
    ``object()``, so the field promised a capability nothing supplied. The
    methods below are what
    :class:`~alphalab.runtime.context_views.HistoryView` implements and what the
    pipeline overlays onto every context it dispatches.

    ``as_of`` is the bound, and it is the *event's* timestamp rather than a wall
    clock: everything at or before it is visible, nothing after it is reachable.
    That is what makes a backtest and a live session see the same window.
    """

    @property
    def as_of(self) -> float:
        """The instant this accessor is bounded at."""
        ...

    def quotes(self, asset_id: str, limit: int | None = None) -> Sequence[Any]:
        """Quotes seen for ``asset_id`` up to :attr:`as_of`, oldest first."""
        ...

    def bars(self, asset_id: str, limit: int | None = None) -> Sequence[Any]:
        """Bars closed for ``asset_id`` up to :attr:`as_of`, oldest first."""
        ...

    def ticks(self, asset_id: str, limit: int | None = None) -> Sequence[Any]:
        """Trade prints for ``asset_id`` up to :attr:`as_of`, oldest first."""
        ...

    def __len__(self) -> int:
        """How many events are visible at :attr:`as_of`."""
        ...


class UniverseProtocol(Protocol):
    """Read-only registry of tradable instruments for this strategy.

    Also decorative until v2.15. Membership is the instrument registry's -- see
    :class:`~alphalab.runtime.context_views.UniverseView`, which implements this
    and which the pipeline overlays.
    """

    @property
    def configured(self) -> bool:
        """Whether the run declares a universe at all."""
        ...

    @property
    def assets(self) -> Sequence[str]:
        """Every ``asset_id`` in the universe."""
        ...

    def __contains__(self, asset_id: object) -> bool: ...

    def __iter__(self) -> Iterator[str]: ...

    def __len__(self) -> int: ...

    def instrument(self, asset_id: str) -> Any | None:
        """What ``asset_id`` is, or ``None`` when it is not in the universe."""
        ...

    def sector(self, asset_id: str) -> str | None:
        """The sector ``asset_id`` is classified as, or ``None``."""
        ...


@dataclass(frozen=True, slots=True)
class NoPortfolio:
    """A portfolio view for a context built outside the execution pipeline.

    The same rule as :class:`NoHistory`, applied to the surface v2.10 added:
    it answers "nothing", and :attr:`available` says the answer means *no
    portfolio was supplied* rather than *the book is empty*. Sizing against a
    zero it reports produces no orders, which is the safe direction, and the
    flag is there so a caller can refuse rather than trade on it.

    It replaces the bare ``object()`` every off-path construction site passed
    until v2.16 -- a value that satisfied the empty protocol, raised
    ``AttributeError`` on every access, and was then reported as a *strategy*
    failure by :class:`~alphalab.strategy.dispatcher.Dispatcher`.
    """

    @property
    def available(self) -> bool:
        """Always ``False``. Distinguishes "no portfolio" from "an empty book"."""

        return False

    @property
    def account(self) -> Any:
        return None

    @property
    def positions(self) -> Mapping[str, Any]:
        return {}

    @property
    def balances(self) -> Mapping[Any, Decimal]:
        return {}

    @property
    def reserved(self) -> Mapping[Any, Decimal]:
        return {}

    @property
    def realized_pnl(self) -> Mapping[Any, Decimal]:
        return {}

    @property
    def commission_paid(self) -> Mapping[Any, Decimal]:
        return {}

    def realized_pnl_in(self, currency: str) -> Decimal:
        return Decimal("0")

    def commission_paid_in(self, currency: str) -> Decimal:
        return Decimal("0")

    def position(self, asset_id: str) -> Any | None:
        return None

    def quantity(self, asset_id: str) -> Decimal:
        return Decimal("0")

    def cash(self, currency: str) -> Decimal:
        return Decimal("0")

    def available_cash(self, currency: str) -> Decimal:
        return Decimal("0")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class NoMarket:
    """A market view for a context built outside the execution pipeline.

    Answers ``None`` for every asset rather than a fabricated price. A strategy
    that marks off :meth:`price` sees "never priced", which is already a case
    the execution path produces for an unregistered asset.
    """

    @property
    def available(self) -> bool:
        return False

    @property
    def prices(self) -> Mapping[str, Decimal]:
        return {}

    @property
    def assets(self) -> Sequence[str]:
        return ()

    def quote(self, asset_id: str) -> Any | None:
        return None

    def tick(self, asset_id: str) -> Any | None:
        return None

    def bar(self, asset_id: str) -> Any | None:
        return None

    def price(self, asset_id: str) -> Decimal | None:
        return None

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class NoRiskView:
    """A risk view for a context built outside the execution pipeline.

    ``active_limits``, ``margin`` and ``exposure`` answer ``None``: there is no
    honest empty value for a limit set, and inventing a permissive one would
    read as "no limits apply" to a strategy checking its own headroom.
    """

    @property
    def available(self) -> bool:
        return False

    @property
    def active_limits(self) -> Any:
        return None

    @property
    def cash(self) -> Decimal:
        return Decimal("0")

    @property
    def buying_power(self) -> Decimal:
        return Decimal("0")

    @property
    def current_nav(self) -> Decimal:
        return Decimal("0")

    @property
    def peak_nav(self) -> Decimal:
        return Decimal("0")

    @property
    def daily_loss(self) -> Decimal:
        return Decimal("0")

    @property
    def margin(self) -> Any:
        return None

    @property
    def exposure(self) -> Any:
        return None

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class NoOrders:
    """An order facade for a context built outside the execution pipeline.

    Empty, and says so. A strategy that tops up only when it has nothing
    working will read "nothing working" here -- which is why :attr:`available`
    exists beside it.
    """

    @property
    def available(self) -> bool:
        return False

    def __iter__(self) -> Iterator[Any]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def for_asset(self, asset_id: str) -> Sequence[Any]:
        return ()

    def net_quantity(self, asset_id: str) -> Decimal:
        return Decimal("0")


@dataclass(frozen=True, slots=True)
class NoHistory:
    """A history accessor for a context built outside the execution pipeline.

    **Not a silent fallback.** It answers "nothing", says so through
    :attr:`available`, and is bounded at negative infinity so that no timestamp
    is ever inside its window -- an empty answer here means *no history was
    supplied*, never *nothing happened*.

    It exists because :class:`StrategyContext` is constructible by hand, by the
    reinforcement-learning environment and by tests, and those callers should
    not have to fabricate a view for a field the pipeline is going to overlay
    anyway. On the execution path this value is never seen: ``_populate_context``
    replaces it on every dispatch.
    """

    @property
    def available(self) -> bool:
        """Always ``False``. Distinguishes "no history" from "empty history"."""

        return False

    @property
    def as_of(self) -> float:
        return float("-inf")

    def quotes(self, asset_id: str, limit: int | None = None) -> Sequence[Any]:
        return ()

    def bars(self, asset_id: str, limit: int | None = None) -> Sequence[Any]:
        return ()

    def ticks(self, asset_id: str, limit: int | None = None) -> Sequence[Any]:
        return ()

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class NoUniverse:
    """A universe for a context built outside the execution pipeline.

    The counterpart of :class:`NoHistory`, and the same rule: it declares itself
    unconfigured rather than presenting an empty universe as a real one.
    """

    @property
    def configured(self) -> bool:
        return False

    @property
    def assets(self) -> Sequence[str]:
        return ()

    def __contains__(self, asset_id: object) -> bool:
        return False

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def instrument(self, asset_id: str) -> Any | None:
        return None

    def sector(self, asset_id: str) -> str | None:
        return None


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """
    The single object through which a strategy observes the outside world.
    Constructed or pooled per hook invocation. Purely immutable and read-only.

    ``history`` and ``universe`` default to :class:`NoHistory` and
    :class:`NoUniverse`. A caller building a context outside the execution
    pipeline -- a test, the reinforcement-learning environment -- omits them
    rather than fabricating a placeholder, and gets a value that *says* it
    supplies nothing instead of one that quietly looks empty. On the execution
    path the pipeline overlays both on every dispatch, so the defaults are never
    what a strategy in a run actually sees.

    ``portfolio``, ``market``, ``risk_view`` and ``orders`` take
    :class:`NoPortfolio`, :class:`NoMarket`, :class:`NoRiskView` and
    :class:`NoOrders` for the same purpose. They are **not** defaulted, because
    they precede ``clock``, ``logger`` and ``config`` -- which are the caller's
    and have no null value -- and reordering the fields would break every
    positional construction. An off-path caller names the null object it means;
    an on-path one is overlaid regardless.
    """

    portfolio: PortfolioSnapshotProtocol
    market: MarketViewProtocol
    clock: ClockProtocol
    logger: ScopedLoggerProtocol
    risk_view: RiskViewProtocol
    config: Any
    orders: OrderFacadeProtocol
    history: HistoryAccessorProtocol = field(default_factory=NoHistory)
    universe: UniverseProtocol = field(default_factory=NoUniverse)
