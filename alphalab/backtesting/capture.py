"""Streaming research capture: the series a search needs, and an identity retention cannot move.

v4.3, ADR-0055. A search over backtests needs one thing from every trial: its
returns over a grid of instants the other trials share. :func:`run_captured`
drives a run exactly as :meth:`~alphalab.backtesting.engine.BacktestEngine.run`
does -- the same private step loop, which an observer reads and never changes --
and returns the result ``run`` would have returned beside a
:class:`CaptureRecord`: the series, read as the run records its equity, and an
identity of the run's economic history that no retention policy can move.

::

    captured = run_captured(
        config,
        dataset,
        strategy_state,
        contexts,
        capture=CaptureSpec(
            ReturnExtractor(
                grid=EVERY_INSTANT,
                costs=Costs.NET,
                cash_flows=CashFlows.INCLUDED,
                currency="USD",
                insolvency=Insolvency.REFUSE,
            ),
            trace=True,
        ),
    )
    captured.result  # what BacktestEngine.run returns, field for field
    captured.record.returns  # analytics' period_returns, value for value

What the series is
------------------
An extractor states every choice that changes the series, and none has a
default (AC-12):

* **grid** -- :data:`EVERY_INSTANT` reads each instant's last equity point, as
  analytics reduces a curve; :class:`FixedInterval` reads the last point at or
  before each instant ``origin + j * seconds``, compared exactly.
* **costs** -- :attr:`Costs.NET` keeps the commissions in the equity;
  :attr:`Costs.GROSS` adds back, at each point, the commission the valuation of
  that point reports: what the run had paid by then, converted at the rates the
  point was valued at.
* **cash_flows** -- :attr:`CashFlows.INCLUDED` keeps every booked flow in the
  period it arrived in (total return); :attr:`CashFlows.EXCLUDED` removes from
  each period's change the dividends, interest, fees and funding booked in it,
  and the cash deposited into or withdrawn from the account -- not the two legs
  of a currency conversion, which move no value in or out -- converted at the
  rates the period's closing point was valued at (price return).
* **currency** -- the run's base currency, which the series is in. Converting a
  series is not done here (LIM-012).
* **insolvency** -- equity at or below zero at a grid instant either refuses the
  capture (:attr:`Insolvency.REFUSE`) or ends the series there
  (:attr:`Insolvency.TRUNCATE`): the return into that instant, whose base was
  solvent, is the last, and the record names the instant.

A return between two grid instants is ``(E_t - E_s - F) / E_s`` in ``Decimal``
under :data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`, converted to
``float`` once -- analytics' own arithmetic, ``F`` being zero but where flows are
excluded. An ``EVERY_INSTANT``, ``NET``, ``INCLUDED`` series is therefore
:attr:`~alphalab.analytics.report.ReturnSummary.period_returns` of the same run
with full history, value for value, for every run that stays solvent.

How a booking meets the point that shows it
--------------------------------------------
The run keeps its equity points and its bookings in separate logs, and a step
records several of each. The capture is told the state after every operation the
step loop performs -- each schedule entry and each slice the cursor closes, the
record's slice close, the record, the algorithms' work -- and every operation
books what it books before the equity point it records. So what an operation
booked shows in that operation's last point, or, when it recorded none (an
algorithm's child fills, a funding), in the next point the run records. Every
point an operation records is at one instant; a run in which one did not would
refuse a ``GROSS`` or ``EXCLUDED`` capture, rather than misplace a booking.

The trace
---------
``trace_id`` is an incremental SHA-256 chain under :data:`CAPTURE_TRACE_SCHEME`,
started from the ``configuration_id`` of the run's configuration kept whole --
the identity it has with its retention policy set aside, which every retention
of the same run shares. A run's own ``configuration_id`` covers its retention
policy, so a chain started from it would differ across retentions (ADR-0055,
amended at implementation). After each step -- the run's
initialization, each record, the end of the data -- it absorbs the canonical
serialization of what the step added to the fills, the ledger's transactions,
the OMS's order events and the equity points, in that order, each entry named by
its type. What an operation added is read as the growth of each log's total ever
appended -- its length plus what retention dropped -- so the trace is the same
under every retention. If an operation added more entries than a log's bound
keeps, the capture refuses rather than chain a partial history: raise the bound.

The returns are class 2 under ADR-0058 -- exact ``Decimal`` arithmetic, rounded
once -- so ``series_id`` may enter an identity. A capture draws no identifier
(AC-05). A paused replay is not captured (FUT-049).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal
from enum import Enum, auto, unique
from fractions import Fraction
from itertools import pairwise
from json.encoder import encode_basestring_ascii as _quote
from operator import attrgetter
from types import MappingProxyType
from typing import Any, Final, overload
from uuid import UUID

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.algorithmic import ExecutionPlan
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import _drive
from alphalab.backtesting.exceptions import BacktestError
from alphalab.backtesting.schedule import EMPTY_SCHEDULE, RunSchedule
from alphalab.backtesting.state import AlgorithmicResult, BacktestResult, QueuedResult
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.execution.queue import QueueModel
from alphalab.persistence.exceptions import PersistenceError, SerializationError
from alphalab.persistence.serializer import DeterministicEncoder, serialize
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.events import CashDeposited, CashFlowBooked, CashWithdrawn
from alphalab.portfolio.fx import FxRates
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run import RunConfig, RunState
from alphalab.runtime.run_snapshot import _recorded_configuration
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "CAPTURE_EXTRACTOR_SCHEME",
    "CAPTURE_SERIES_SCHEME",
    "CAPTURE_TRACE_SCHEME",
    "EVERY_INSTANT",
    "CaptureError",
    "CaptureRecord",
    "CaptureSpec",
    "Captured",
    "CashFlows",
    "Costs",
    "EveryInstant",
    "FixedInterval",
    "Insolvency",
    "InsolventCaptureError",
    "ReturnExtractor",
    "TraceCounts",
    "canonical_captured_series_key",
    "canonical_extractor_key",
    "run_captured",
]

#: The scheme an extractor's canonical key is written under (ADR-0055).
CAPTURE_EXTRACTOR_SCHEME: Final = "alphalab.capture.extractor.v1"

#: The scheme a captured series' canonical text is written under.
CAPTURE_SERIES_SCHEME: Final = "alphalab.capture.series.v1"

#: The scheme a trace's chain starts from, with the run's ``configuration_id``.
CAPTURE_TRACE_SCHEME: Final = "alphalab.capture.trace.v1"

_HEX = frozenset("0123456789abcdef")

#: Analytics' arithmetic context, whose methods a series computes through.
_ACCOUNTING: Final = ACCOUNTING_CONTEXT

#: The retention a history's identity is taken under: every entry kept.
_WHOLE: Final = RetentionPolicy()


class CaptureError(BacktestError):
    """A capture refused: a choice it cannot honour, or a history it cannot read whole."""


class InsolventCaptureError(CaptureError):
    """A run's equity reached zero or below at a grid instant, under :attr:`Insolvency.REFUSE`.

    Attributes:
        at: The grid instant.
        equity: The series' equity there: the run's, with costs added back
            under :attr:`Costs.GROSS`.
    """

    def __init__(self, at: float, equity: Decimal) -> None:
        super().__init__(
            f"The run's equity is {equity} at {at}: a return cannot be taken from it, and "
            "the extractor refuses an insolvent run (Insolvency.REFUSE). Insolvency.TRUNCATE "
            "ends the series there instead, the record naming the instant."
        )
        self.at = at
        self.equity = equity


# --- the extractor --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EveryInstant:
    """Each instant's last equity point: the grid analytics reads a curve on."""


#: The grid of every instant the run recorded equity at.
EVERY_INSTANT: Final = EveryInstant()


@dataclass(frozen=True, slots=True)
class FixedInterval:
    """The instants ``origin + j * seconds``, each read at the last point at or before it.

    The series runs from the first grid instant at or after the run's first
    equity point to the last at or before its last. Grid instants are compared
    with the run's timestamps exactly, and reported as the nearest float.

    Attributes:
        seconds: The interval; positive and finite.
        origin: An instant on the grid; finite.

    Raises:
        CaptureError: If either is not a finite number, or ``seconds`` is not
            positive.
    """

    seconds: float
    origin: float

    def __post_init__(self) -> None:
        for name in ("seconds", "origin"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise CaptureError(f"FixedInterval.{name} must be a number, got {value!r}.")
            if not math.isfinite(value):
                raise CaptureError(f"FixedInterval.{name} must be finite, got {value!r}.")
            object.__setattr__(self, name, float(value))
        if self.seconds <= 0.0:
            raise CaptureError(f"FixedInterval.seconds must be positive, got {self.seconds!r}.")


@unique
class Costs(Enum):
    """Whether commissions stay in the equity a series is read from."""

    #: The equity as the run valued it, every commission paid.
    NET = auto()
    #: Each point's commission paid, as its valuation reports it, added back.
    GROSS = auto()


@unique
class CashFlows(Enum):
    """Whether booked flows are part of the period they arrive in."""

    #: Total return: every flow stays in the period it was booked in.
    INCLUDED = auto()
    #: Price return: dividends, interest, fees, funding, deposits and withdrawals
    #: removed from the period they were booked in.
    EXCLUDED = auto()


@unique
class Insolvency(Enum):
    """What equity at or below zero at a grid instant does to a capture."""

    #: Refuses it: :class:`InsolventCaptureError`.
    REFUSE = auto()
    #: Ends the series at that instant, the record naming it.
    TRUNCATE = auto()


@dataclass(frozen=True, slots=True)
class ReturnExtractor:
    """Every choice that turns a run's equity into a series, stated (ADR-0055, AC-12).

    Attributes:
        grid: :data:`EVERY_INSTANT` or a :class:`FixedInterval`.
        costs: :attr:`Costs.NET` or :attr:`Costs.GROSS`.
        cash_flows: :attr:`CashFlows.INCLUDED` or :attr:`CashFlows.EXCLUDED`.
        currency: The run's base currency, which the series is in.
        insolvency: :attr:`Insolvency.REFUSE` or :attr:`Insolvency.TRUNCATE`.

    Raises:
        CaptureError: If a choice is not one of its kind's.
    """

    grid: EveryInstant | FixedInterval
    costs: Costs
    cash_flows: CashFlows
    currency: str
    insolvency: Insolvency

    def __post_init__(self) -> None:
        if not isinstance(self.grid, EveryInstant | FixedInterval):
            raise CaptureError(
                f"ReturnExtractor.grid must be EVERY_INSTANT or a FixedInterval, got {self.grid!r}."
            )
        for name, kind in (("costs", Costs), ("cash_flows", CashFlows), ("insolvency", Insolvency)):
            value = getattr(self, name)
            if not isinstance(value, kind):
                raise CaptureError(
                    f"ReturnExtractor.{name} must be a {kind.__name__} member, got {value!r}."
                )
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise CaptureError(
                f"ReturnExtractor.currency must name a currency, got {self.currency!r}."
            )

    @property
    def extractor_id(self) -> str:
        """SHA-256 of :func:`canonical_extractor_key`: two series compare only under one."""

        return _sha256(canonical_extractor_key(self))


def canonical_extractor_key(extractor: ReturnExtractor) -> str:
    """The canonical text of ``extractor``, under :data:`CAPTURE_EXTRACTOR_SCHEME`."""

    grid: dict[str, Any] = (
        {
            "kind": "FIXED_INTERVAL",
            "origin": extractor.grid.origin,
            "seconds": extractor.grid.seconds,
        }
        if isinstance(extractor.grid, FixedInterval)
        else {"kind": "EVERY_INSTANT"}
    )
    return serialize(
        {
            "cash_flows": extractor.cash_flows.name,
            "costs": extractor.costs.name,
            "currency": extractor.currency,
            "grid": grid,
            "insolvency": extractor.insolvency.name,
            "scheme": CAPTURE_EXTRACTOR_SCHEME,
        }
    )


@dataclass(frozen=True, slots=True)
class CaptureSpec:
    """What a captured run captures: its series, and whether its trace (ADR-0055).

    Attributes:
        extractor: How the series is read.
        trace: Whether the run's economic history is chained into a ``trace_id``.

    Raises:
        CaptureError: If ``extractor`` is not a :class:`ReturnExtractor`, or
            ``trace`` is not a boolean.
    """

    extractor: ReturnExtractor
    trace: bool

    def __post_init__(self) -> None:
        if not isinstance(self.extractor, ReturnExtractor):
            raise CaptureError(
                f"CaptureSpec.extractor must be a ReturnExtractor, got {self.extractor!r}."
            )
        if not isinstance(self.trace, bool):
            raise CaptureError(f"CaptureSpec.trace must be True or False, got {self.trace!r}.")


# --- the record -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraceCounts:
    """How many entries of each log a trace absorbed."""

    fills: int
    transactions: int
    order_events: int
    equity_points: int


def canonical_captured_series_key(
    instants: Sequence[float], returns: Sequence[float], insolvent_at: float | None
) -> str:
    """The canonical text of a series, under :data:`CAPTURE_SERIES_SCHEME`.

    It is what ``series_id`` hashes.
    """

    return serialize(
        {
            "insolvent_at": insolvent_at,
            "instants": list(instants),
            "returns": list(returns),
            "scheme": CAPTURE_SERIES_SCHEME,
        }
    )


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    """What a captured run recorded beside its result (ADR-0055 section 3).

    Attributes:
        extractor_id: The extractor's identity; series compare only under one.
        instants: The grid instants read, in order.
        returns: One return per period: ``returns[i]`` is the period
            ``(instants[i], instants[i + 1]]``, at full precision.
        series_id: SHA-256 of :func:`canonical_captured_series_key`.
        insolvent_at: The grid instant the series ended at insolvency, under
            :attr:`Insolvency.TRUNCATE`; otherwise ``None``.
        steps: The run's steps: its initialization, one per record, the end of
            its data.
        configuration_id: The run's ``configuration_id``, as
            :func:`~alphalab.lifecycle.reproducibility.digest_run` derives it;
            ``None`` without a trace. The trace starts from the same
            configuration kept whole -- its retention policy set aside -- so it
            is the same under every retention, where this is not.
        trace_id: The trace's last link; ``None`` without a trace.
        traced: What the trace absorbed; ``None`` without a trace.

    Raises:
        CaptureError: If the series is not one: a return more or fewer than its
            periods, a return or an instant not finite, instants not ascending,
            an insolvency anywhere but at the last instant, a ``series_id`` that
            is not the series', or a trace given in part.
    """

    extractor_id: str
    instants: tuple[float, ...]
    returns: tuple[float, ...]
    series_id: str
    insolvent_at: float | None
    steps: int
    configuration_id: str | None
    trace_id: str | None
    traced: TraceCounts | None

    def __post_init__(self) -> None:
        if len(self.returns) != max(len(self.instants) - 1, 0):
            raise CaptureError(
                f"A series over {len(self.instants)} instants has "
                f"{max(len(self.instants) - 1, 0)} periods, not {len(self.returns)} returns."
            )
        for name in ("instants", "returns"):
            if not all(math.isfinite(value) for value in getattr(self, name)):
                raise CaptureError(f"CaptureRecord.{name} must be finite.")
        if any(later <= earlier for earlier, later in pairwise(self.instants)):
            raise CaptureError("CaptureRecord.instants must be strictly ascending.")
        if self.insolvent_at is not None and (
            not self.instants or self.insolvent_at != self.instants[-1]
        ):
            raise CaptureError(
                "A series ended at insolvency ends at that instant: insolvent_at must be its "
                "last instant."
            )
        if self.series_id != _sha256(
            canonical_captured_series_key(self.instants, self.returns, self.insolvent_at)
        ):
            raise CaptureError("CaptureRecord.series_id is not this series' identity.")
        traced = (self.configuration_id, self.trace_id, self.traced)
        if any(part is None for part in traced) and any(part is not None for part in traced):
            raise CaptureError(
                "A trace is recorded whole -- configuration_id, trace_id and traced -- or "
                "not at all."
            )
        for name in ("extractor_id", "series_id", "configuration_id", "trace_id"):
            value = getattr(self, name)
            if value is not None and (len(value) != 64 or not set(value) <= _HEX):
                raise CaptureError(f"CaptureRecord.{name} must be a SHA-256 hex digest.")

    @property
    def period_ends(self) -> tuple[float, ...]:
        """The instant each return's period ends at: one per return."""

        return self.instants[1:]


@dataclass(frozen=True, slots=True)
class Captured[R: BacktestResult]:
    """A captured run: the result :meth:`BacktestEngine.run` returns, and what was captured.

    Attributes:
        result: The run's result, equal field for field to an uncaptured run's.
        record: The capture.
    """

    result: R
    record: CaptureRecord


# --- the run ----------------------------------------------------------------------


@overload
def run_captured(
    config: RunConfig,
    dataset: MarketDataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    observations: Iterable[ObservationDelivery[Any]] = (),
    *,
    capture: CaptureSpec,
    schedule: RunSchedule = EMPTY_SCHEDULE,
    execution: None = None,
    queue: None = None,
) -> Captured[BacktestResult]: ...


@overload
def run_captured(
    config: RunConfig,
    dataset: MarketDataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    observations: Iterable[ObservationDelivery[Any]] = (),
    *,
    capture: CaptureSpec,
    schedule: RunSchedule = EMPTY_SCHEDULE,
    execution: ExecutionPlan,
    queue: None = None,
) -> Captured[AlgorithmicResult]: ...


@overload
def run_captured(
    config: RunConfig,
    dataset: MarketDataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    observations: Iterable[ObservationDelivery[Any]] = (),
    *,
    capture: CaptureSpec,
    schedule: RunSchedule = EMPTY_SCHEDULE,
    execution: None = None,
    queue: QueueModel,
) -> Captured[QueuedResult]: ...


@overload
def run_captured(
    config: RunConfig,
    dataset: MarketDataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    observations: Iterable[ObservationDelivery[Any]] = (),
    *,
    capture: CaptureSpec,
    schedule: RunSchedule = EMPTY_SCHEDULE,
    execution: ExecutionPlan | None,
    queue: QueueModel | None = None,
) -> Captured[BacktestResult]: ...


def run_captured(
    config: RunConfig,
    dataset: MarketDataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    observations: Iterable[ObservationDelivery[Any]] = (),
    *,
    capture: CaptureSpec,
    schedule: RunSchedule = EMPTY_SCHEDULE,
    execution: ExecutionPlan | None = None,
    queue: QueueModel | None = None,
) -> Captured[BacktestResult]:
    """Run ``dataset`` as :meth:`BacktestEngine.run` does, and capture it (ADR-0055).

    The run takes exactly the steps ``run`` takes with the same arguments --
    one private step loop drives both -- and its result is the one ``run``
    returns, with the same ``result_id``. Everything ``run`` accepts is
    accepted here, with the same meaning.

    Raises:
        CaptureError: If ``capture`` is not a :class:`CaptureSpec`; if its
            currency is not the run's; if retention dropped entries the capture
            had not read; or, with a trace, if the run's configuration has no
            deterministic form to start it from.
        InsolventCaptureError: If equity reaches zero or below at a grid
            instant under :attr:`Insolvency.REFUSE`.
    """

    if not isinstance(capture, CaptureSpec):
        raise CaptureError(f"capture must be a CaptureSpec, got {capture!r}.")
    observer = _Capture(capture)
    result = _drive(
        config,
        dataset,
        strategy_state,
        context_factory,
        observations,
        schedule,
        execution,
        queue,
        observer,
    )
    return Captured(result, observer.finish())


# --- the observer -----------------------------------------------------------------


#: One equity point as the series reads it: its instant, the series' equity
#: there, the flows booked by then per currency, and the rates it was valued at.
_Point = tuple[float, Decimal, Mapping[str, Decimal], FxRates]


class _Series:
    """The extractor's grid, read point by point as the run records them."""

    def __init__(self, extractor: ReturnExtractor) -> None:
        self._grid = extractor.grid
        self._fixed = isinstance(extractor.grid, FixedInterval)
        self._excluded = extractor.cash_flows is CashFlows.EXCLUDED
        self._refuse = extractor.insolvency is Insolvency.REFUSE
        self._currency = extractor.currency
        self._units: Any = None
        self.instants: list[float] = []
        self.returns: list[float] = []
        self.insolvent_at: float | None = None
        self._latest: _Point | None = None
        self._previous: _Point | None = None
        self.ended = False
        self._next: Fraction | None = None
        self._index = 0

    def bind(self, units: Any) -> None:
        self._units = units

    def point(self, point: _Point) -> None:
        latest = self._latest
        timestamp = point[0]
        if latest is not None and timestamp < latest[0]:
            raise CaptureError(
                f"The equity curve goes back in time: {timestamp} follows {latest[0]}."
            )
        if self._fixed:
            grid = self._grid
            assert isinstance(grid, FixedInterval)
            if self._next is None:
                origin, seconds = Fraction(grid.origin), Fraction(grid.seconds)
                self._index = math.ceil((Fraction(timestamp) - origin) / seconds)
                self._next = origin + self._index * seconds
            else:
                assert latest is not None
                while self._next < timestamp:
                    self._emit(float(self._next), latest)
                    if self.ended:
                        return
                    self._advance(grid)
        elif latest is not None and timestamp > latest[0]:
            self._emit(latest[0], latest)
            if self.ended:
                return
        self._latest = point

    def end(self) -> None:
        latest = self._latest
        if latest is None or self.ended:
            return
        grid = self._grid
        if isinstance(grid, FixedInterval):
            assert self._next is not None
            while not self.ended and self._next <= latest[0]:
                self._emit(float(self._next), latest)
                self._advance(grid)
        else:
            self._emit(latest[0], latest)

    def _advance(self, grid: FixedInterval) -> None:
        self._index += 1
        self._next = Fraction(grid.origin) + self._index * Fraction(grid.seconds)

    def _emit(self, instant: float, point: _Point) -> None:
        previous = self._previous
        equity = point[1]
        if previous is not None:
            # Analytics' arithmetic, under its context: (E_t - E_s - F) / E_s.
            change = _ACCOUNTING.subtract(equity, previous[1])
            if self._excluded:
                change = _ACCOUNTING.subtract(change, self._flows_between(previous, point))
            value = float(_ACCOUNTING.divide(change, previous[1]))
            if not math.isfinite(value):
                raise CaptureError(
                    f"The return into {instant} is {value}: it has no finite float form."
                )
            self.returns.append(value)
        self.instants.append(instant)
        self._previous = point
        if equity <= 0:
            if self._refuse:
                raise InsolventCaptureError(instant, equity)
            self.insolvent_at = instant
            self.ended = True

    def _flows_between(self, previous: _Point, point: _Point) -> Decimal:
        """The flows booked after ``previous`` up to ``point``, in the series' currency."""

        before, after = previous[2], point[2]
        if before is after:
            return Decimal(0)
        moved: dict[str, Decimal] = {}
        for currency in set(before) | set(after):
            amount = after.get(currency, Decimal(0)) - before.get(currency, Decimal(0))
            if amount:
                moved[currency] = amount
        total, _ = CurrencyAmounts(moved).total_in(
            self._currency, point[3], point[0], units=self._units
        )
        return total


def _decimal_text(value: Decimal) -> str:
    # The encoder writes a Decimal as its exact text, quoted.
    return _quote(str(value))


def _boolean(value: bool) -> str:
    return "true" if value else "false"


def _null(value: None) -> str:
    return "null"


#: How each type the trace has met is written, decided once per type. The JSON
#: scalars' exact types are written by the encoder's own rules from the start.
_WRITERS: dict[type, Callable[[Any], str]] = {
    str: _quote,
    Decimal: _decimal_text,
    int: int.__repr__,
    bool: _boolean,
    type(None): _null,
}

#: The encoder's own fallback, for a type with no rule here.
_DEFAULT: Final = DeterministicEncoder().default


def _write(value: Any) -> str:
    """``value`` as :func:`~alphalab.persistence.serializer.serialize` writes it, byte for byte.

    ``serialize`` converts every value through generic checks and then encodes
    the result; a trace asks the same few types for their text thousands of
    times a run. This decides, once per type, how that type is written -- in the
    order and by the rules of :func:`~alphalab.common.serialization.to_serializable`
    and :class:`~alphalab.persistence.serializer.DeterministicEncoder`, with
    JSON's own quoting and number forms -- and writes the text directly. A type
    it has no rule for is handed to the encoder's own fallback, as ``serialize``
    hands it, and the result written by the same rules. One lookup by exact type
    finds every writer, a JSON scalar's included.
    """

    kind = type(value)
    writer = _WRITERS.get(kind)
    if writer is None:
        writer = _WRITERS[kind] = _writer(kind)
    return writer(value)


def _writer(kind: type) -> Callable[[Any], str]:
    if issubclass(kind, type):
        return _defaulted
    if hasattr(kind, "__serializable__"):
        return _projected
    if is_dataclass(kind):
        return _record_writer(kind)
    if issubclass(kind, AppendOnlyLog | list | tuple):
        return _array
    if issubclass(kind, dict | MappingProxyType):
        return _object
    if issubclass(kind, str):
        return _quote
    if issubclass(kind, int):
        return int.__repr__
    if issubclass(kind, float):
        return _float
    if issubclass(kind, Decimal | Enum | UUID):
        return _quoted_text
    return _defaulted


def _record_writer(kind: type) -> Callable[[Any], str]:
    """A dataclass's fields in name order, as one template filled once per record."""

    names = sorted(item.name for item in fields(kind))
    if not names:
        return lambda value: "{}"
    template = "{" + ",".join(_quote(name).replace("%", "%%") + ":%s" for name in names) + "}"
    if len(names) == 1:
        (name,) = names
        return lambda value: template % (_write(getattr(value, name)),)
    read = attrgetter(*names)
    return lambda value: template % tuple(map(_write, read(value)))


def _float(value: float) -> str:
    if value != value or value in (math.inf, -math.inf):
        raise ValueError(f"Out of range float values are not JSON compliant: {value!r}")
    return float.__repr__(value)


_WRITERS[float] = _float


def _quoted_text(value: Any) -> str:
    return _quote(str(value))


def _defaulted(value: Any) -> str:
    return _write(_DEFAULT(value))


def _projected(value: Any) -> str:
    return _write(value.__serializable__())


def _array(value: Any) -> str:
    return "[" + ",".join(map(_write, value)) + "]"


def _object(value: Any) -> str:
    return (
        "{"
        + ",".join([_key(key) + ":" + _write(item) for key, item in sorted(value.items())])
        + "}"
    )


def _key(key: Any) -> str:
    """A mapping key as JSON writes one: a string, whatever it was."""

    if isinstance(key, str):
        return _quote(key)
    if key is True or key is False:
        return '"true"' if key else '"false"'
    if key is None:
        return '"null"'
    if isinstance(key, int):
        return '"' + int.__repr__(key) + '"'
    if isinstance(key, float):
        return '"' + _float(key) + '"'
    raise TypeError(f"keys must be str, int, float, bool or None, not {type(key).__name__}")


#: Each entry type's opening, ``["TypeName",``, written once per type.
_PREFIXES: dict[type, str] = {}


def _named(entry: Any) -> str:
    kind = type(entry)
    prefix = _PREFIXES.get(kind)
    if prefix is None:
        prefix = _PREFIXES[kind] = "[" + _quote(kind.__name__) + ","
    return prefix


def _payload(logs: Sequence[Sequence[Any]]) -> str:
    """One step's entries, each named by its type, as ``serialize`` writes them."""

    try:
        return (
            "["
            + ",".join(
                [
                    "[" + ",".join([_named(entry) + _write(entry) + "]" for entry in log]) + "]"
                    if log
                    else "[]"
                    for log in logs
                ]
            )
            + "]"
        )
    except Exception as error:
        raise SerializationError(f"Failed to serialize object: {error}") from error


def _tail[T](log: AppendOnlyLog[T], read: int, name: str) -> tuple[tuple[T, ...], int]:
    """What ``log`` gained since ``read`` of its entries were read, and its total ever appended.

    The growth of the total -- its length plus what retention dropped -- is what
    was appended since. If retention dropped some of it before it was read, the
    capture refuses rather than read a partial history.
    """

    kept = len(log)
    total = kept + log.dropped
    added = total - read
    if not added:
        return (), total
    if added > kept:
        raise CaptureError(
            f"Retention dropped {added - kept} of the {added} {name} one step added, before "
            f"the capture read them: the bound ({kept} kept) is smaller than what one step "
            "records. A trace or a series read from a partial history would describe another "
            "run, so the capture refuses: raise the bound."
        )
    if added < 0:
        raise CaptureError(f"The run's {name} lost entries it had appended.")
    return log[kept - added :], total


class _Trace:
    """The incremental SHA-256 chain over a run's economic history."""

    def __init__(self, configuration_id: str) -> None:
        start = serialize({"configuration_id": configuration_id, "scheme": CAPTURE_TRACE_SCHEME})
        self.link = hashlib.sha256(start.encode("utf-8")).digest()
        self.points: list[Any] = []
        self.counts = [0, 0, 0, 0]

    def absorb(
        self, fills: Sequence[Any], transactions: Sequence[Any], order_events: Sequence[Any]
    ) -> None:
        """Chain one step: its fills, transactions, order events and equity points, in order."""

        points = self.points
        payload = _payload((fills, transactions, order_events, points))
        self.link = hashlib.sha256(self.link + payload.encode("utf-8")).digest()
        counts = self.counts
        counts[0] += len(fills)
        counts[1] += len(transactions)
        counts[2] += len(order_events)
        counts[3] += len(points)
        self.points = []


class _Capture:
    """Reads a run as the step loop drives it, and changes nothing (ADR-0055)."""

    def __init__(self, spec: CaptureSpec) -> None:
        extractor = spec.extractor
        self._extractor = extractor
        self._gross = extractor.costs is Costs.GROSS
        self._excluded = extractor.cash_flows is CashFlows.EXCLUDED
        self._adjusted = self._gross or self._excluded
        self._wants_trace = spec.trace
        self._series = _Series(extractor)
        self._trace: _Trace | None = None
        self._points_read = 0
        self._events_read = 0
        self._fills_read = 0
        self._transactions_read = 0
        self._order_events_read = 0
        self._flows: Mapping[str, Decimal] = {}
        self._units: Any = None
        self._steps = 0
        self._started = False
        self._state: RunState | None = None

    def _start(self, state: RunState) -> None:
        pipeline = state.pipeline
        base = pipeline.config.currency
        if self._extractor.currency != base:
            raise CaptureError(
                f"The extractor reads a series in {self._extractor.currency}, and this run "
                f"values its book in {base}. A series is in the run's base currency: "
                "converting one needs a rate source the capture would have to state, and is "
                "not done here (LIM-012)."
            )
        self._units = pipeline.portfolio.account.currency_units
        self._series.bind(self._units)
        if self._wants_trace:
            try:
                recorded = _recorded_configuration(capture_run(state))
                configuration = serialize(recorded)
                # The same configuration kept whole: retention decides what a run
                # keeps, never what it does, so the history's identity sets it aside.
                whole = serialize(
                    {**recorded, "pipeline": replace(recorded["pipeline"], retention=_WHOLE)}
                )
            except PersistenceError as error:
                raise CaptureError(
                    "A trace starts from the run's configuration identity, and this run's "
                    f"configuration has no deterministic form: {error}"
                ) from error
            self._configuration_id = _sha256(configuration)
            self._trace = _Trace(_sha256(whole))
        self._started = True

    def seen(self, state: RunState, rates: FxRates) -> None:
        if state is self._state:
            # The operation returned the state already read: it added nothing.
            return
        if not self._started:
            self._start(state)
        self._state = state
        pipeline = state.pipeline
        if self._excluded:
            events, self._events_read = _tail(
                pipeline.portfolio.events, self._events_read, "portfolio events"
            )
            if events:
                self._book(events)
        points, self._points_read = _tail(
            pipeline.portfolio_snapshots, self._points_read, "equity points"
        )
        if not points:
            return
        if self._trace is not None:
            self._trace.points.extend(points)
        if self._adjusted and points[0].timestamp != points[-1].timestamp:
            raise CaptureError(
                f"One operation recorded equity at {points[0].timestamp} and at "
                f"{points[-1].timestamp}: what it booked cannot be placed between them, so "
                "a GROSS or EXCLUDED series cannot be read from this run."
            )
        series = self._series
        if series.ended:
            return
        flows = self._flows
        if self._gross:
            commission, _ = pipeline.portfolio.commission_paid.total_in(
                self._extractor.currency, rates, points[-1].timestamp, units=self._units
            )
            for point in points:
                series.point((point.timestamp, point.total_equity + commission, flows, rates))
                if series.ended:
                    return
        else:
            for point in points:
                series.point((point.timestamp, point.total_equity, flows, rates))
                if series.ended:
                    return

    def _book(self, events: Sequence[Any]) -> None:
        flows: dict[str, Decimal] | None = None
        for event in events:
            if isinstance(event, CashFlowBooked | CashDeposited):
                amount = event.amount
            elif isinstance(event, CashWithdrawn):
                amount = -event.amount
            else:
                continue
            if flows is None:
                flows = dict(self._flows)
            flows[event.currency] = flows.get(event.currency, Decimal(0)) + amount
        if flows is not None:
            self._flows = flows

    def step_ended(self) -> None:
        self._steps += 1
        trace = self._trace
        if trace is not None:
            # Read once a step, from the state it ended in: what the step added
            # survives its own trimming unless the step outgrew a bound.
            assert self._state is not None, "the loop tells of a state before a step ends"
            pipeline = self._state.pipeline
            fills, self._fills_read = _tail(pipeline.fills, self._fills_read, "fills")
            transactions, self._transactions_read = _tail(
                pipeline.portfolio.ledger.transactions,
                self._transactions_read,
                "ledger transactions",
            )
            order_events, self._order_events_read = _tail(
                pipeline.oms.events, self._order_events_read, "order events"
            )
            trace.absorb(fills, transactions, order_events)

    def finish(self) -> CaptureRecord:
        series = self._series
        series.end()
        instants, returns = tuple(series.instants), tuple(series.returns)
        trace = self._trace
        return CaptureRecord(
            extractor_id=self._extractor.extractor_id,
            instants=instants,
            returns=returns,
            series_id=_sha256(
                canonical_captured_series_key(instants, returns, series.insolvent_at)
            ),
            insolvent_at=series.insolvent_at,
            steps=self._steps,
            configuration_id=None if trace is None else self._configuration_id,
            trace_id=None if trace is None else trace.link.hex(),
            traced=None if trace is None else TraceCounts(*trace.counts),
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
