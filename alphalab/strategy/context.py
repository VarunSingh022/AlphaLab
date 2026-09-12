"""Immutable StrategyContext providing a read-only window into the world.

The nine fields have existed since v0.10.0. What each of them *supplies* has
arrived in stages: v2.10 populated ``portfolio``, ``orders``, ``risk_view`` and
``market`` from the pipeline (ADR-0026), and v2.15 completes the boundary with
``history`` and ``universe``, which ADR-0026 deferred with reasons it also
recorded -- history "requires a clock-bounded accessor whose bound is enforced
at construction", universe "requires deciding whether membership is
configuration, instrument-registry state, or a risk control".

``clock``, ``logger`` and ``config`` remain the caller's, deliberately and
unchanged. The time semantic that actually needed settling is the look-ahead
bound, and that lives on the history accessor as ``as_of``.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class PortfolioSnapshotProtocol(Protocol):
    """Read-only view of portfolio state."""


class MarketViewProtocol(Protocol):
    """Read-only view of current market data."""


class ClockProtocol(Protocol):
    """Virtual or monotonic clock source."""

    def now(self) -> float: ...


class ScopedLoggerProtocol(Protocol):
    """Structured, strategy-scoped write-only logger."""

    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


class RiskViewProtocol(Protocol):
    """Read-only view of current risk limits and exposure."""


class OrderFacadeProtocol(Protocol):
    """Constrained facade for order queries, NOT for direct placement."""


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
