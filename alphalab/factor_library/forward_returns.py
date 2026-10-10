"""The thing a factor is measured against, and the one quantity that looks ahead.

Every diagnostic in v3.2 -- the information coefficient, decay, hit rate, a
quantile spread -- asks the same question: did the factor at instant ``t``
predict what happened *after* ``t``? Answering it requires a quantity computed
from data the factor was not allowed to see. That is not a leak; it is the
label, and the whole point of the exercise.

It becomes a leak the moment it is mistaken for a feature. So a forward return
is not a :class:`~alphalab.factor_library.panel.FeaturePanel` and cannot be
turned into one: it is a :class:`ForwardReturnPanel`, a separate type that
names its horizon, and no function in this package accepts one where a feature
is expected. The type system is doing the work that a naming convention would
not.

Horizons are periods, not seconds
---------------------------------

A horizon of 5 means five *observations* of that symbol's own series, not five
days. Two symbols trading on different calendars therefore get five of their
own bars each, which is what a cross-sectional comparison needs -- a horizon in
seconds would give a symbol that trades every day five bars and a symbol that
trades weekly less than one, and the cross-section would be comparing different
things.

The implementation lag is stated (v3.11)
----------------------------------------

Until v3.11 the forward return started at the very observation the factor was
computed from, so every information coefficient silently assumed the position
was entered at the close that produced the signal (ledger DAT-003). That is a
choice, often an optimistic one: the close is known only once it has printed.
``lag`` is now required -- ``0`` is allowed, and says exactly that -- and the
return at ``t`` runs from ``lag`` observations after ``t`` to ``lag + horizon``
after it::

    r(t) = v[t + lag + horizon] / v[t + lag] - 1

The lag is carried on the panel and on every diagnostic measured against it, so
an IC cannot be quoted without the entry assumption it rests on.

Delisted symbols realize their terminal return (v3.11)
------------------------------------------------------

The last ``lag + horizon`` observations of every series have no forward return:
the data ends before the exit does. For a series that ends because *the data
ends*, that is the truth and :attr:`ForwardReturnPanel.unrealized_instants`
counts it. For a series that ends because *the security stopped trading* --
acquired for cash, delisted for cause, bankrupt -- dropping those instants was
a survivorship bias (ledger DAT-002): the terminal, often catastrophic, return
was exactly the outcome a factor should be measured against, and it vanished.

A :class:`DelistingReturn` declares the terminal event: when the symbol stopped
trading and what a holder received, as a return on its last observed value.
A forward window that spans the delisting realizes that terminal value, and is
counted in :attr:`ForwardReturnPanel.delisted_instants`; an instant whose entry
would fall after the delisting could not have been traded at all, and is
counted in :attr:`ForwardReturnPanel.unenterable_instants`. The delisting set
is required -- ``()`` states that none applies -- because a default of "none"
is the silent assumption the ledger item exists to remove.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from alphalab.factor_library.exceptions import FactorComputationError, FactorInputError
from alphalab.factor_library.observations import ObservationFrame

__all__ = [
    "DELISTING_SET_SCHEME",
    "DelistingReturn",
    "ForwardReturnPanel",
    "delisting_set_id",
    "forward_returns",
]

#: Scheme tag of :func:`delisting_set_id`.
DELISTING_SET_SCHEME = "alphalab.delisting_set.v1"


@dataclass(frozen=True, slots=True)
class DelistingReturn:
    """A symbol's terminal event: it stopped trading, and what a holder received.

    Attributes:
        symbol: The symbol, as the observation frame names it.
        timestamp: When it stopped trading. Must be after its last observation
            in any frame it is applied to -- an observation after a delisting
            is a contradiction, and is refused rather than resolved.
        terminal_return: What a holder received, as a return on the last
            observed value: ``-1.0`` for a total loss, ``0.25`` for a cash
            acquisition at a 25% premium to the last price. The CRSP delisting
            return (``DLRET``) is this quantity.
    """

    symbol: str
    timestamp: float
    terminal_return: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise FactorInputError(f"A delisting must name its symbol, got {self.symbol!r}.")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int | float):
            raise FactorInputError(
                f"DelistingReturn.timestamp must be a number, got {self.timestamp!r}."
            )
        if not math.isfinite(self.timestamp):
            raise FactorInputError(
                f"DelistingReturn.timestamp must be finite, got {self.timestamp!r}."
            )
        if isinstance(self.terminal_return, bool) or not isinstance(
            self.terminal_return, int | float
        ):
            raise FactorInputError(
                f"DelistingReturn.terminal_return must be a number, got {self.terminal_return!r}."
            )
        if not math.isfinite(self.terminal_return) or self.terminal_return < -1.0:
            raise FactorInputError(
                f"The terminal return of {self.symbol} is {self.terminal_return!r}; a holder "
                "cannot lose more than everything, so it must be a finite number of at least -1."
            )
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "terminal_return", float(self.terminal_return))

    @classmethod
    def at_value(
        cls, symbol: str, timestamp: float, last_value: float, terminal_value: float
    ) -> DelistingReturn:
        """A delisting stated as the value a holder received rather than a return.

        Raises:
            FactorInputError: If ``last_value`` is not positive or
                ``terminal_value`` is negative.
        """

        if not last_value > 0.0:
            raise FactorInputError(
                f"The last value of {symbol} is {last_value!r}; a terminal return off a "
                "non-positive base is undefined."
            )
        if not terminal_value >= 0.0:
            raise FactorInputError(
                f"The terminal value of {symbol} is {terminal_value!r}; a holder receives "
                "something or nothing, not less than nothing."
            )
        return cls(symbol, timestamp, terminal_value / last_value - 1.0)


def delisting_set_id(delistings: Sequence[DelistingReturn]) -> str:
    """A digest naming a delisting set, for a study's ``inputs["delistings"]``.

    Order-free: the events are rendered sorted by symbol. Each float is
    rendered by ``repr``, which round-trips exactly, after adding ``0.0`` so
    that a negative zero -- equal to zero -- renders as zero.
    """

    lines = [DELISTING_SET_SCHEME]
    for event in sorted(delistings, key=lambda item: item.symbol):
        lines.append(f"{event.symbol}|{event.timestamp + 0.0!r}|{event.terminal_return + 0.0!r}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ForwardReturnPanel:
    """Realized forward returns at one horizon and one lag, indexed by instant.

    Attributes:
        horizon: How many observations the return spans, in periods.
        rows: Instant to ``{symbol: forward return}``. An instant is keyed by
            the observation the *factor* was computed at; the return it holds
            starts ``lag`` observations later.
        dataset_version: The dataset version the prices were read from, or
            ``None`` when the dataset recorded no provenance.
        timezone_name: The zone the timestamps are reported in.
        symbols: Every symbol that produced at least one forward return.
        unrealized_instants: Instants with no forward return because the
            series ended -- with no delisting declared -- before the exit. The
            tail of the sample nothing is known about.
        lag: How many observations after the factor instant the position is
            entered. ``0`` enters at the observation the factor was computed
            from.
        delisted_instants: Forward returns that realized a declared terminal
            return, because their window spans the symbol's delisting.
        unenterable_instants: Instants with no forward return because the
            entry would fall after a declared delisting: no position could have
            been opened.
        delistings: The declared terminal events that were applied, sorted by
            symbol.
    """

    horizon: int
    rows: Mapping[float, Mapping[str, float]]
    dataset_version: str | None
    timezone_name: str
    symbols: tuple[str, ...]
    unrealized_instants: int
    lag: int
    delisted_instants: int
    unenterable_instants: int
    delistings: tuple[DelistingReturn, ...]

    @property
    def timestamps(self) -> tuple[float, ...]:
        """Every instant a forward return exists at, sorted."""

        return tuple(sorted(self.rows))

    def cross_section(self, timestamp: float) -> Mapping[str, float]:
        """The symbols with a realized forward return at ``timestamp``."""

        return self.rows.get(timestamp, {})

    def __len__(self) -> int:
        return len(self.rows)


def _terminal_events(
    frame: ObservationFrame, delistings: Sequence[DelistingReturn]
) -> dict[str, DelistingReturn]:
    events: dict[str, DelistingReturn] = {}
    for event in delistings:
        if not isinstance(event, DelistingReturn):
            raise FactorInputError(f"delistings must hold DelistingReturn values, got {event!r}.")
        if event.symbol in events:
            raise FactorInputError(
                f"{event.symbol} is declared delisted twice. A security stops trading once; "
                "two terminal returns would be two answers to one question."
            )
        series = frame.series.get(event.symbol)
        if series is None:
            raise FactorInputError(
                f"A delisting is declared for {event.symbol}, which the frame does not hold. "
                "The delisting set and the prices describe different universes."
            )
        if len(series) and event.timestamp <= series.timestamps[-1]:
            raise FactorInputError(
                f"{event.symbol} is declared delisted at {event.timestamp!r} but is observed "
                f"at {series.timestamps[-1]!r}. A security observed after it stopped trading "
                "contradicts its own delisting, and choosing which to believe is the caller's."
            )
        events[event.symbol] = event
    return events


def forward_returns(
    frame: ObservationFrame,
    horizon: int,
    *,
    lag: int,
    delistings: Sequence[DelistingReturn],
) -> ForwardReturnPanel:
    """Compute realized forward returns over ``horizon`` observations, ``lag`` after.

    The return at instant ``t`` for a symbol is
    ``v[t + lag + horizon] / v[t + lag] - 1`` on that symbol's own
    observations. When the window runs past the last observation of a symbol
    declared in ``delistings``, the exit value is the terminal value
    ``v[last] * (1 + terminal_return)``.

    ``frame`` must hold a price-like field. Nothing here checks that a volume
    is not a price -- it cannot, since a fundamental observation is a perfectly
    good thing to compute a growth rate of -- but the entry value is required to
    be positive, because a return off a non-positive base is undefined rather
    than zero.

    Args:
        frame: The observations the returns are computed from.
        horizon: The span of each return, in observations. At least 1.
        lag: Observations between the factor instant and the entry. At least 0,
            and required: see the module docstring.
        delistings: Every terminal event that applies to the frame's symbols,
            or ``()`` to state that none does. Required, for the same reason.

    Raises:
        FactorInputError: If ``horizon`` is below 1 or ``lag`` below 0 (or
            either is not an integer), a delisting is repeated, names a symbol
            the frame does not hold or precedes one of its observations, or no
            forward return could be realized at all.
        FactorComputationError: If an entry value is not positive.
    """

    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise FactorInputError(
            f"A forward horizon must be at least 1 observation, got {horizon!r}. A horizon of "
            "zero is the present, which a factor is allowed to see and therefore cannot "
            "be measured against."
        )
    if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
        raise FactorInputError(
            f"An implementation lag is a whole number of observations of at least 0, got "
            f"{lag!r}. Zero enters at the observation the factor was computed from."
        )
    events = _terminal_events(frame, delistings)

    rows: dict[float, dict[str, float]] = {}
    present: set[str] = set()
    unrealized = 0
    delisted = 0
    unenterable = 0

    for symbol in frame.symbols:
        row = frame.series[symbol]
        count = len(row)
        event = events.get(symbol)
        for index in range(count):
            entry = index + lag
            if entry >= count:
                if event is None:
                    unrealized += 1
                else:
                    unenterable += 1
                continue
            base = row.values[entry]
            if base <= 0.0:
                raise FactorComputationError(
                    f"A {horizon}-period forward return is undefined for {symbol} at "
                    f"{row.timestamps[entry]!r}: the base value is {base!r}."
                )
            exit_index = entry + horizon
            if exit_index < count:
                value = row.values[exit_index] / base - 1.0
            elif event is not None:
                value = row.values[count - 1] * (1.0 + event.terminal_return) / base - 1.0
                delisted += 1
            else:
                unrealized += 1
                continue
            rows.setdefault(row.timestamps[index], {})[symbol] = value
            present.add(symbol)

    if not rows:
        raise FactorInputError(
            f"No symbol in the frame has more than {lag + horizon} observation(s) or a declared "
            f"delisting, so no forward return {horizon} period(s) ahead at lag {lag} has been "
            "realized. Either the horizon is longer than the sample or the sample is too short "
            "to study."
        )

    return ForwardReturnPanel(
        horizon=horizon,
        rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
        dataset_version=frame.dataset_version,
        timezone_name=frame.timezone_name,
        symbols=tuple(sorted(present)),
        unrealized_instants=unrealized,
        lag=lag,
        delisted_instants=delisted,
        unenterable_instants=unenterable,
        delistings=tuple(sorted(events.values(), key=lambda item: item.symbol)),
    )
