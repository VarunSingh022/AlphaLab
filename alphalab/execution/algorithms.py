"""Execution algorithms: working one parent order as a deterministic sequence of child orders.

A strategy decides *what* to own; allocation turns that into a canonical
:class:`~alphalab.core.order_request.OrderRequest`. How to trade a large request
-- all at once, evenly over an afternoon, in line with the market's volume, a
share of whatever trades, in fixed slices, or a visible tranche at a time -- is
the question this module answers, and it answers it the same way in a backtest,
a replay and a live session because nothing it reads is ambient.

Each algorithm is stated completely rather than named
-----------------------------------------------------

An institutional name on an opaque heuristic is how a backtest comes to claim a
behaviour nobody can check. Every algorithm here states its objective, the
inputs it reads, how it sizes and times each child, and what it does at the end
-- in its class docstring -- and nothing else changes its output.

=================== ==========================================================
:class:`TWAP`       Equal clock intervals. The trajectory spends quantity
                    uniformly in time, shaped by :class:`Urgency`.
:class:`VWAP`       The intervals of a supplied :class:`VolumeProfile`. The
                    trajectory spends quantity in proportion to the volume the
                    profile *expects*, shaped by urgency. Missing volume is
                    refused or explicitly replaced by time, per
                    :class:`MissingVolumePolicy`.
:class:`Participation` A fixed share of the volume actually observed. Reactive:
                    it sizes from :class:`IntervalVolume` observations as they
                    arrive and never from volume it has not seen.
:class:`Slicing`    Fixed-size or fixed-count children, one working at a time.
:class:`Iceberg`    One visible tranche working at a time; the rest of the parent
                    stays hidden in AlphaLab. Iceberg-*like*: a generic
                    behaviour, not an emulation of any venue's native reserve
                    order type.
=================== ==========================================================

The trajectory and the arithmetic
---------------------------------

The two schedule algorithms (TWAP, VWAP) are one construction. Each partitions
the window into slices and assigns every slice a *clock* ``x`` in ``[0, 1]`` --
elapsed time for TWAP, cumulative expected volume for VWAP. The fraction of the
parent due by the end of a slice is ``F(x)``::

    F(x) = x                                         urgency kappa = 0
    F(x) = 1 - sinh(kappa * (1 - x)) / sinh(kappa)   urgency kappa > 0

This is the shape of the Almgren-Chriss (2000) optimal liquidation trajectory,
with its ``kappa * T`` collapsed into one dimensionless number the caller states.
AlphaLab does not estimate ``kappa`` from risk aversion, volatility and impact,
and does not claim the schedule is optimal for any particular cost model: it
states the shape, which front-loads more as ``kappa`` grows and is a straight
line at zero. ``sinh`` is evaluated in :class:`~decimal.Decimal` at 34
significant digits, so the schedule is identical on every platform -- a float
transcendental is not.

Quantities move in whole multiples of a stated ``quantity_increment`` -- a share,
a contract, a lot, a satoshi -- which has no default because it is a market
convention. Each slice's ideal share ``Q * (F(x_k) - F(x_{k-1}))`` is converted
to increments by the **largest remainder method**: every slice gets the floor of
its ideal, and the increments left over go to the slices with the largest
fractional remainders, ties to the earlier slice. The slices sum to the parent
exactly, no slice differs from its ideal by a whole increment, and an equal-weight
remainder lands in the earliest slices -- ten over three slices is 4, 3, 3.

Working the parent
------------------

:class:`AlgorithmState` is a value, and every operation returns a new one:

* :func:`start_algorithm` validates everything and plans the schedule;
* :func:`release_children` is called with the time ``now`` -- supplied, never read
  from a clock -- and, for participation, the volume observed since the last
  call, and returns the children to send;
* :func:`record_child_execution` books a child's fill, idempotently in its
  execution id; :func:`record_child_outcome` books a child ending without
  filling;
* :func:`cancel_algorithm` stops it and returns the children still working.

Every release **tops up to the trajectory**: the child is the target less what
has filled and what is still working, so a child that expired unfilled is caught
up by the next one and nothing is ever committed ahead of the schedule. A child
carries the parent's ``strategy_id`` and every
:class:`~alphalab.core.contribution.StrategyContribution` unchanged -- an
algorithm never erases which strategies asked for the order -- and the
:attr:`~AlgorithmState.algorithm_id` of the configuration that produced it.

What connects a child to a venue is not here. A child is an instruction;
:func:`alphalab.runtime.broker_routing.route_child_order` sends it through the
broker boundary, and its fills come back to the parent's OMS order.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal
from enum import StrEnum, unique
from itertools import pairwise
from typing import Final

from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.exceptions import ExecutionValidationError

__all__ = [
    "ALGORITHM_CONFIGURATION_SCHEME",
    "ALGORITHM_RUN_SCHEME",
    "EXECUTION_SCHEDULE_SCHEME",
    "MAX_URGENCY",
    "TWAP",
    "VWAP",
    "AlgorithmState",
    "AlgorithmStatus",
    "AlgorithmTerms",
    "ChildOrder",
    "ChildProgress",
    "ExecutionAlgorithm",
    "ExecutionSchedule",
    "Iceberg",
    "IncompletePolicy",
    "IntervalVolume",
    "MissingVolumePolicy",
    "Participation",
    "ScheduleBasis",
    "ScheduledSlice",
    "Slicing",
    "Urgency",
    "VolumeProfile",
    "algorithm_configuration_id",
    "cancel_algorithm",
    "plan_schedule",
    "record_child_execution",
    "record_child_outcome",
    "release_children",
    "start_algorithm",
]

#: Scheme tag of an algorithm configuration's identity.
ALGORITHM_CONFIGURATION_SCHEME: Final = "alphalab.execution_algorithm.v1"

#: Scheme tag of one run's identity: configuration, terms and parent together.
ALGORITHM_RUN_SCHEME: Final = "alphalab.execution_algorithm_run.v1"

#: Scheme tag of a planned schedule's identity.
EXECUTION_SCHEDULE_SCHEME: Final = "alphalab.execution_schedule.v1"

#: The steepest trajectory accepted. At ``kappa = 100`` the first percent of the
#: clock already carries ``1 - e^-1`` of the parent; anything steeper is "send it
#: all now", which is not a schedule and is refused rather than approximated.
MAX_URGENCY: Final = Decimal("100")

#: The arithmetic every schedule is computed in, never the caller's thread
#: context: 34 significant digits, half-even. Fixed so a schedule is the same in
#: every process on every platform.
_CONTEXT: Final = Context(prec=34, rounding=ROUND_HALF_EVEN)

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _add(left: Decimal, right: Decimal) -> Decimal:
    return _CONTEXT.add(left, right)


def _sub(left: Decimal, right: Decimal) -> Decimal:
    return _CONTEXT.subtract(left, right)


def _sum(values: Iterable[Decimal]) -> Decimal:
    total = _ZERO
    for value in values:
        total = _CONTEXT.add(total, value)
    return total


def _decimal_instant(value: float) -> Decimal:
    """A float instant as the decimal it prints as -- exact, shortest, platform-stable."""

    return Decimal(repr(value))


def _require_instant(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ExecutionValidationError(f"{name} is {value!r}, which is not an instant.")


def _require_multiple(quantity: Decimal, increment: Decimal, name: str) -> int:
    """How many increments ``quantity`` is, refusing a quantity that is not whole in them."""

    units = _CONTEXT.divide(quantity, increment)
    if units != units.to_integral_value():
        raise ExecutionValidationError(
            f"{name} of {quantity} is not a whole number of {increment} increments; it could "
            "not be sent as whole children."
        )
    return int(units)


# --------------------------------------------------------------------------- #
# Urgency, and the trajectory
# --------------------------------------------------------------------------- #


def _sinh(value: Decimal) -> Decimal:
    grown = _CONTEXT.exp(value)
    return _CONTEXT.divide(_CONTEXT.subtract(grown, _CONTEXT.divide(_ONE, grown)), Decimal(2))


@dataclass(frozen=True, slots=True)
class Urgency:
    """How front-loaded a schedule is: the curvature of its trajectory.

    Attributes:
        kappa: Dimensionless, in ``[0, MAX_URGENCY]``. Zero spends quantity
            uniformly in the schedule's clock; larger values spend more of it
            early. It is the Almgren-Chriss product ``kappa * T``, stated by the
            caller -- AlphaLab does not estimate it.

    Raises:
        ExecutionValidationError: If ``kappa`` is negative or above the ceiling.
    """

    kappa: Decimal

    def __post_init__(self) -> None:
        if not _ZERO <= self.kappa <= MAX_URGENCY:
            raise ExecutionValidationError(
                f"Urgency kappa {self.kappa} is outside [0, {MAX_URGENCY}]. A negative kappa "
                "would back-load a schedule by a curve nobody has stated the meaning of."
            )

    @classmethod
    def neutral(cls) -> Urgency:
        """A straight trajectory: quantity spent uniformly in the schedule's clock."""

        return cls(_ZERO)

    def fraction(self, clock: Decimal) -> Decimal:
        """``F(x)``: the share of the parent due by clock ``x``. Monotone, 0 at 0, 1 at 1."""

        if clock <= _ZERO:
            return _ZERO
        if clock >= _ONE:
            return _ONE
        if self.kappa == _ZERO:
            return clock
        left = _sinh(_CONTEXT.multiply(self.kappa, _CONTEXT.subtract(_ONE, clock)))
        return _CONTEXT.subtract(_ONE, _CONTEXT.divide(left, _sinh(self.kappa)))


def _apportion(total: int, weights: Sequence[Decimal]) -> list[int]:
    """Split ``total`` whole units across ``weights`` by the largest remainder method.

    Floors first; the units left over go to the largest fractional remainders,
    ties to the lower index. Sums to ``total`` exactly.
    """

    whole = _sum(weights)
    ideals = [
        _CONTEXT.divide(_CONTEXT.multiply(Decimal(total), weight), whole) for weight in weights
    ]
    shares = [int(ideal.to_integral_value(rounding=ROUND_FLOOR)) for ideal in ideals]
    leftover = total - sum(shares)
    remainders = [_sub(ideal, Decimal(share)) for ideal, share in zip(ideals, shares, strict=True)]
    ranked = sorted(range(len(weights)), key=lambda index: (-remainders[index], index))
    for index in ranked[:leftover]:
        shares[index] += 1
    return shares


# --------------------------------------------------------------------------- #
# Volume
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class IntervalVolume:
    """Volume in one half-open interval ``[start, end)``, in units, or its absence.

    Used for both things an algorithm can know about volume: what a
    :class:`VolumeProfile` *expects* each interval to trade, and what
    :class:`Participation` *observed* one to trade. ``volume`` of ``None`` means
    not observed -- never zero -- and every algorithm that reads it says what it
    does then.
    """

    start: float
    end: float
    volume: Decimal | None

    def __post_init__(self) -> None:
        _require_instant(self.start, "IntervalVolume.start")
        _require_instant(self.end, "IntervalVolume.end")
        if not self.end > self.start:
            raise ExecutionValidationError(
                f"An interval from {self.start} to {self.end} contains no time."
            )
        if self.volume is not None and self.volume < _ZERO:
            raise ExecutionValidationError(f"Volume {self.volume} is not a volume.")


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """The volume a market is *expected* to trade in each interval. Supplied, never estimated.

    Attributes:
        intervals: Contiguous, in time order, non-overlapping.
        source: Where the expectation came from -- a historical average, a model.
            Required and never interpreted: a profile nobody can attribute is an
            assumption presented as data.
    """

    intervals: tuple[IntervalVolume, ...]
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ExecutionValidationError("A volume profile names no source.")
        if not self.intervals:
            raise ExecutionValidationError("A volume profile with no interval expects nothing.")
        for earlier, later in zip(self.intervals, self.intervals[1:], strict=False):
            if later.start != earlier.end:
                raise ExecutionValidationError(
                    f"Profile intervals must be contiguous and in order; one ends at "
                    f"{earlier.end} and the next starts at {later.start}."
                )


@unique
class MissingVolumePolicy(StrEnum):
    """What a VWAP schedule does when its profile cannot say how volume is distributed."""

    #: Refuse to plan. The caller learns the profile is incomplete.
    REFUSE = "refuse"

    #: Plan the whole schedule on time instead, and record that it did. Never a
    #: mix: a volume share and a time share are not the same unit, so a profile
    #: with one missing interval is replaced entirely, not patched.
    TIME_WEIGHTED = "time_weighted"


@unique
class IncompletePolicy(StrEnum):
    """What participation does if the window ends with quantity unreleased."""

    #: Stop. The quantity never released is reported unfilled.
    LEAVE_UNFILLED = "leave_unfilled"

    #: Release everything still unreleased at the end, above the participation
    #: rate. Chosen, never defaulted: it trades completion for market share.
    COMPLETE_AT_END = "complete_at_end"


# --------------------------------------------------------------------------- #
# The algorithms
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TWAP:
    """Time-weighted: equal clock intervals, shaped by urgency.

    **Objective** spend the parent evenly in time (``kappa = 0``) or front-loaded
    by a stated curvature. **Inputs** the window and ``slices``; no market data.
    **Schedule** ``slices`` equal intervals of ``[start, end)``. **Sizing** the
    trajectory at each slice's end, apportioned by largest remainder.
    **Timing** a child is released at its slice's start and expires at its end.
    **Completion** the last slice's target is the whole parent.
    """

    slices: int
    urgency: Urgency

    def __post_init__(self) -> None:
        if self.slices < 1:
            raise ExecutionValidationError(f"A TWAP over {self.slices} slices has no schedule.")

    @property
    def configuration_id(self) -> str:
        """This configuration's identity: :func:`algorithm_configuration_id` of it."""

        return algorithm_configuration_id(self)


@dataclass(frozen=True, slots=True)
class VWAP:
    """Volume-weighted: the profile's intervals, spending in proportion to expected volume.

    **Objective** trade in step with the volume the market is expected to print,
    so the parent's average price tracks the interval's VWAP. **Inputs** a
    :class:`VolumeProfile` -- the *volume basis*, supplied and attributed. **Schedule**
    the profile's intervals clipped to the window; an interval the window covers
    only partly expects its volume pro rata to the time covered, the
    uniform-within-interval assumption stated here. **Sizing** the trajectory at
    each interval's cumulative expected-volume share, apportioned by largest
    remainder. **Missing volume** an interval with no volume, a window the
    profile does not cover, or a total of zero follows ``missing_volume``.
    """

    profile: VolumeProfile
    missing_volume: MissingVolumePolicy
    urgency: Urgency

    @property
    def configuration_id(self) -> str:
        """This configuration's identity: :func:`algorithm_configuration_id` of it."""

        return algorithm_configuration_id(self)


@dataclass(frozen=True, slots=True)
class Participation:
    """A fixed share of observed volume: percent of volume.

    **Objective** keep this parent's executed quantity at or below ``rate`` of
    the market volume observed since the window opened. **Reference volume**
    every unit the supplied observations say traded, AlphaLab's own fills
    included -- a tape cannot tell them apart -- so ``rate`` is a share of total
    volume. **Sizing** after each observation the target is
    ``rate * observed volume``, floored to the increment (a cap is never
    exceeded by rounding), and the child tops up to it. **Limits** a child
    smaller than ``min_child_quantity`` is not released -- the liquidity was
    insufficient -- and one larger than ``max_child_quantity`` is capped. **Missing
    volume** an interval with no observation releases nothing and is recorded;
    participation never sizes from volume it did not see. **End** per ``at_end``.
    """

    rate: Decimal
    min_child_quantity: Decimal | None
    max_child_quantity: Decimal | None
    at_end: IncompletePolicy

    def __post_init__(self) -> None:
        if not _ZERO < self.rate <= _ONE:
            raise ExecutionValidationError(f"A participation rate of {self.rate} is not in (0, 1].")
        low, high = self.min_child_quantity, self.max_child_quantity
        if low is not None and low <= _ZERO:
            raise ExecutionValidationError(f"A minimum child of {low} is not a quantity.")
        if high is not None and high <= _ZERO:
            raise ExecutionValidationError(f"A maximum child of {high} is not a quantity.")
        if low is not None and high is not None and low > high:
            raise ExecutionValidationError(
                f"A minimum child of {low} above a maximum of {high} can release nothing."
            )

    @property
    def configuration_id(self) -> str:
        """This configuration's identity: :func:`algorithm_configuration_id` of it."""

        return algorithm_configuration_id(self)


@dataclass(frozen=True, slots=True)
class Slicing:
    """Fixed slices, one working at a time, in order.

    Exactly one of ``slice_quantity`` (each child's size; the last is the
    remainder) or ``slice_count`` (the parent in that many near-equal children,
    remainder to the earliest) is given. The next child is released when the
    previous one is finished, filled or not; quantity a child did not fill goes
    back to the parent and is sliced again. Time plays no part except that
    nothing is released after the window closes.
    """

    slice_quantity: Decimal | None
    slice_count: int | None

    def __post_init__(self) -> None:
        if (self.slice_quantity is None) == (self.slice_count is None):
            raise ExecutionValidationError(
                "Slicing takes exactly one of slice_quantity and slice_count."
            )
        if self.slice_quantity is not None and self.slice_quantity <= _ZERO:
            raise ExecutionValidationError(f"A slice of {self.slice_quantity} is not a quantity.")
        if self.slice_count is not None and self.slice_count < 1:
            raise ExecutionValidationError(f"{self.slice_count} slices is not a slicing.")

    @property
    def configuration_id(self) -> str:
        """This configuration's identity: :func:`algorithm_configuration_id` of it."""

        return algorithm_configuration_id(self)


@dataclass(frozen=True, slots=True)
class Iceberg:
    """One visible tranche at a time; the remainder stays hidden inside AlphaLab.

    **Displayed** the one child working, of ``display_quantity`` or whatever is
    left. **Hidden** everything else, which no venue sees. **Replenishment**
    when the working tranche is finished the next is released. This is the
    generic behaviour only: no venue's native reserve order is emulated, and a
    venue that offers one is reached through its adapter, not here.
    """

    display_quantity: Decimal

    def __post_init__(self) -> None:
        if self.display_quantity <= _ZERO:
            raise ExecutionValidationError(
                f"A displayed tranche of {self.display_quantity} shows nothing."
            )

    @property
    def configuration_id(self) -> str:
        """This configuration's identity: :func:`algorithm_configuration_id` of it."""

        return algorithm_configuration_id(self)


type ExecutionAlgorithm = TWAP | VWAP | Participation | Slicing | Iceberg


def _render_interval(interval: IntervalVolume) -> str:
    return f"{interval.start!r}|{interval.end!r}|{interval.volume}"


def _render_algorithm(algorithm: ExecutionAlgorithm) -> list[str]:
    if isinstance(algorithm, TWAP):
        return [
            "algorithm=twap",
            f"slices={algorithm.slices}",
            f"urgency={algorithm.urgency.kappa}",
        ]
    if isinstance(algorithm, VWAP):
        return [
            "algorithm=vwap",
            f"missing_volume={algorithm.missing_volume}",
            f"urgency={algorithm.urgency.kappa}",
            f"profile.source={algorithm.profile.source!r}",
            *(f"profile.interval={_render_interval(i)}" for i in algorithm.profile.intervals),
        ]
    if isinstance(algorithm, Participation):
        return [
            "algorithm=participation",
            f"rate={algorithm.rate}",
            f"min_child={algorithm.min_child_quantity}",
            f"max_child={algorithm.max_child_quantity}",
            f"at_end={algorithm.at_end}",
        ]
    if isinstance(algorithm, Slicing):
        return [
            "algorithm=slicing",
            f"slice_quantity={algorithm.slice_quantity}",
            f"slice_count={algorithm.slice_count}",
        ]
    return ["algorithm=iceberg", f"display={algorithm.display_quantity}"]


def algorithm_configuration_id(algorithm: ExecutionAlgorithm) -> str:
    """The identity of an algorithm's configuration alone: what enters a fingerprint.

    Two runs of the same configuration on different parents share it, which is
    what makes it the right identity for "this strategy is executed by TWAP over
    twelve slices at urgency 2"; :attr:`AlgorithmState.algorithm_id` is one run's.
    """

    return _digest([ALGORITHM_CONFIGURATION_SCHEME, *_render_algorithm(algorithm)])


# --------------------------------------------------------------------------- #
# Terms, and the schedule
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AlgorithmTerms:
    """How one parent is worked: the window, the increment, and how children are priced.

    Attributes:
        start: When the window opens, in Unix seconds. Supplied, like every
            instant here.
        end: When it closes. Nothing is released at or after it.
        quantity_increment: The smallest tradable unit. Every child is a whole
            multiple of it, and so must the parent be. No default: a lot size is
            a market convention.
        order_type: ``MARKET`` or ``LIMIT``. Stop orders are not a way of working
            a parent and are refused.
        limit_price: Every child's limit, and required for ``LIMIT``; refused for
            ``MARKET``.
    """

    start: float
    end: float
    quantity_increment: Decimal
    order_type: OrderType
    limit_price: Decimal | None

    def __post_init__(self) -> None:
        _require_instant(self.start, "AlgorithmTerms.start")
        _require_instant(self.end, "AlgorithmTerms.end")
        if not self.end > self.start:
            raise ExecutionValidationError(
                f"A window from {self.start} to {self.end} contains no time to trade in."
            )
        if self.quantity_increment <= _ZERO:
            raise ExecutionValidationError(
                f"A quantity increment of {self.quantity_increment} is not a unit."
            )
        if self.order_type not in (OrderType.MARKET, OrderType.LIMIT):
            raise ExecutionValidationError(
                f"Children are MARKET or LIMIT orders, not {self.order_type}."
            )
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ExecutionValidationError("A LIMIT parent names no limit price for its children.")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ExecutionValidationError("A MARKET parent's children carry no limit price.")
        if self.limit_price is not None and self.limit_price <= _ZERO:
            raise ExecutionValidationError(f"A limit of {self.limit_price} is not a price.")

    def _render(self) -> list[str]:
        return [
            f"start={self.start!r}",
            f"end={self.end!r}",
            f"increment={self.quantity_increment}",
            f"order_type={self.order_type}",
            f"limit={self.limit_price}",
        ]


@unique
class ScheduleBasis(StrEnum):
    """What a schedule's clock measured."""

    TIME = "time"
    VOLUME = "volume"

    #: A VWAP whose profile could not say how volume was distributed, planned
    #: on time instead under :attr:`MissingVolumePolicy.TIME_WEIGHTED`.
    TIME_FALLBACK = "time_fallback"


@dataclass(frozen=True, slots=True)
class ScheduledSlice:
    """One interval of a schedule and what is due by its end.

    Attributes:
        index: From 1.
        start: When the slice opens.
        end: When it closes.
        weight: The interval's share of the clock -- time or expected volume --
            before urgency shapes it.
        quantity: What this slice adds to the target.
        target: Everything due by the end of the slice.
    """

    index: int
    start: float
    end: float
    weight: Decimal
    quantity: Decimal
    target: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionSchedule:
    """A planned schedule: slices, their targets, and what the plan was based on.

    Attributes:
        slices: In time order. The last target is the whole parent.
        basis: What the clock measured.
        note: Why the basis is what it is, when it is not what was asked for.
    """

    slices: tuple[ScheduledSlice, ...]
    basis: ScheduleBasis
    note: str

    @property
    def schedule_id(self) -> str:
        """SHA-256 over every slice and the basis."""

        return _digest(
            [
                EXECUTION_SCHEDULE_SCHEME,
                f"basis={self.basis}",
                *(
                    f"slice={s.index}|{s.start!r}|{s.end!r}|{s.weight}|{s.quantity}|{s.target}"
                    for s in self.slices
                ),
            ]
        )

    def target_at(self, now: float) -> tuple[Decimal, ScheduledSlice | None]:
        """What is due by the end of the latest slice that has opened by ``now``.

        Found by bisection over the slice starts, so a run that asks once per
        slice costs the logarithm of the schedule per ask, not its length.
        """

        opened = bisect_right(self.slices, now, key=lambda planned: planned.start)
        if opened == 0:
            return _ZERO, None
        current = self.slices[opened - 1]
        return current.target, current


def _time_boundaries(start: float, end: float, count: int) -> list[tuple[float, float]]:
    span = end - start
    edges = [start + span * index / count for index in range(count)] + [end]
    return list(pairwise(edges))


def _profile_slices(
    profile: VolumeProfile, start: float, end: float
) -> tuple[list[tuple[float, float, Decimal | None]], bool]:
    """The profile's intervals clipped to the window, each with its pro-rata volume.

    Returns the slices and whether the profile covered the whole window.
    """

    slices: list[tuple[float, float, Decimal | None]] = []
    for interval in profile.intervals:
        low, high = max(interval.start, start), min(interval.end, end)
        if not high > low:
            continue
        volume: Decimal | None = None
        if interval.volume is not None:
            covered = _CONTEXT.divide(
                _sub(_decimal_instant(high), _decimal_instant(low)),
                _sub(_decimal_instant(interval.end), _decimal_instant(interval.start)),
            )
            volume = _CONTEXT.multiply(interval.volume, covered)
        slices.append((low, high, volume))
    covers = bool(slices) and slices[0][0] == start and slices[-1][1] == end
    return slices, covers


def plan_schedule(
    parent_quantity: Decimal, algorithm: TWAP | VWAP, terms: AlgorithmTerms
) -> ExecutionSchedule:
    """Plan a TWAP or VWAP schedule: slices, weights and targets.

    Pure and deterministic. See the module docstring for the trajectory and the
    apportionment.

    Raises:
        ExecutionValidationError: If the parent is not a whole number of
            increments, or a VWAP profile is incomplete under ``REFUSE``.
    """

    total = _require_multiple(parent_quantity, terms.quantity_increment, "The parent quantity")
    if total <= 0:
        raise ExecutionValidationError(f"A parent of {parent_quantity} has nothing to schedule.")

    note = ""
    if isinstance(algorithm, TWAP):
        bounds = _time_boundaries(terms.start, terms.end, algorithm.slices)
        weights = [_ONE] * len(bounds)
        basis = ScheduleBasis.TIME
    else:
        clipped, covers = _profile_slices(algorithm.profile, terms.start, terms.end)
        volumes = [volume for _, _, volume in clipped]
        missing: list[str] = []
        if not covers:
            missing.append("the profile does not cover the whole window")
        if any(volume is None for volume in volumes):
            missing.append("an interval has no expected volume")
        if (
            volumes
            and all(volume is not None for volume in volumes)
            and _sum(volume for volume in volumes if volume is not None) == _ZERO
        ):
            missing.append("the window expects no volume at all")
        if not missing:
            bounds = [(low, high) for low, high, _ in clipped]
            weights = [volume for volume in volumes if volume is not None]
            basis = ScheduleBasis.VOLUME
        elif algorithm.missing_volume is MissingVolumePolicy.REFUSE:
            raise ExecutionValidationError(
                f"VWAP cannot plan from profile {algorithm.profile.source!r}: "
                f"{'; '.join(missing)}. Supply the volume, or choose TIME_WEIGHTED to plan the "
                "whole schedule on time and say so."
            )
        else:
            bounds = (
                [(low, high) for low, high, _ in clipped]
                if covers
                else _time_boundaries(terms.start, terms.end, max(1, len(clipped)))
            )
            weights = [_sub(_decimal_instant(high), _decimal_instant(low)) for low, high in bounds]
            basis = ScheduleBasis.TIME_FALLBACK
            note = f"Planned on time, not volume: {'; '.join(missing)}."

    whole = _sum(weights)
    fractions: list[Decimal] = []
    running = _ZERO
    for weight in weights:
        running = _add(running, weight)
        fractions.append(algorithm.urgency.fraction(_CONTEXT.divide(running, whole)))
    increments = [
        _sub(fraction, previous)
        for fraction, previous in zip(fractions, [_ZERO, *fractions[:-1]], strict=True)
    ]
    units = _apportion(total, increments)

    slices: list[ScheduledSlice] = []
    target = _ZERO
    for index, ((low, high), weight, share) in enumerate(
        zip(bounds, weights, units, strict=True), start=1
    ):
        quantity = _CONTEXT.multiply(terms.quantity_increment, Decimal(share))
        target = _add(target, quantity)
        slices.append(
            ScheduledSlice(
                index=index,
                start=low,
                end=high,
                weight=_CONTEXT.divide(weight, whole),
                quantity=quantity,
                target=target,
            )
        )
    return ExecutionSchedule(slices=tuple(slices), basis=basis, note=note)


# --------------------------------------------------------------------------- #
# Children, and the state of a run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ChildOrder:
    """One instruction an algorithm released: what to send, when, and on whose behalf.

    Attributes:
        child_id: ``"<parent_order_id>/<sequence>"`` -- derived, never random, so
            a restarted run names its children as the first run did.
        parent_order_id: The parent :class:`~alphalab.core.order_request.OrderRequest`.
        sequence: From 1, in release order.
        asset_id: The parent's asset.
        side: The parent's side.
        quantity: This child's quantity, a whole number of increments.
        order_type: ``MARKET`` or ``LIMIT``, from the terms.
        limit_price: The terms' limit, or ``None`` for a market child.
        released_at: The ``now`` it was released at.
        expires_at: When its slice closes, for a schedule algorithm; ``None``
            for one that works a child until it finishes.
        strategy_id: The parent's.
        contributions: The parent's, unchanged. An algorithm never erases which
            strategies asked for the order; attribution splits by them.
        algorithm_id: The run's identity -- configuration, terms and parent.
    """

    child_id: str
    parent_order_id: str
    sequence: int
    asset_id: str
    side: Side
    quantity: Decimal
    order_type: OrderType
    limit_price: Decimal | None
    released_at: float
    expires_at: float | None
    strategy_id: str
    contributions: tuple[StrategyContribution, ...]
    algorithm_id: str


@dataclass(frozen=True, slots=True)
class ChildProgress:
    """A child and how it stands.

    Attributes:
        child: The instruction.
        filled: What has filled.
        outcome: ``None`` while working; ``FILLED``, ``CANCELLED``, ``EXPIRED``
            or ``REJECTED`` once finished.
    """

    child: ChildOrder
    filled: Decimal
    outcome: OrderStatus | None

    @property
    def working(self) -> Decimal:
        """Quantity still working at the venue: zero once the child is finished."""

        return _ZERO if self.outcome is not None else _sub(self.child.quantity, self.filled)


@unique
class AlgorithmStatus(StrEnum):
    """Where a run stands."""

    #: Releasing children as its rule says.
    WORKING = "working"

    #: Everything filled.
    COMPLETED = "completed"

    #: Stopped by the caller. Children already working may still fill, and do.
    CANCELLED = "cancelled"

    #: The window closed with quantity unfilled and nothing working.
    EXPIRED = "expired"

    #: A child was rejected. The algorithm does not retry a venue's refusal;
    #: it stops and says why, and what to do next is the caller's decision.
    FAILED = "failed"


_TERMINAL_STATUSES: Final = frozenset(
    {
        AlgorithmStatus.COMPLETED,
        AlgorithmStatus.CANCELLED,
        AlgorithmStatus.EXPIRED,
        AlgorithmStatus.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class AlgorithmState:
    """One run of one algorithm over one parent. A value; every operation returns a new one.

    Attributes:
        parent: The order being worked.
        algorithm: Its configuration.
        terms: Its window, increment and pricing.
        algorithm_id: SHA-256 over configuration, terms and parent.
        schedule: The plan, for TWAP and VWAP; ``None`` otherwise.
        children: Every child released, by ``child_id``, in release order.
        released: Total quantity released.
        filled: Total quantity filled.
        returned: Quantity children finished without filling, which goes back to
            the parent to be released again.
        executions: Every execution id booked. Membership is what makes booking
            a fill twice a no-op.
        observed_volume: Participation's cumulative observed volume.
        observed_until: The end of the last volume interval observed.
        status: Where the run stands.
        reason: Why it stopped, once it has.
        notes: Everything worth knowing that is not a failure: a slice that
            released nothing, an interval with no volume, a fallback.
    """

    parent: OrderRequest
    algorithm: ExecutionAlgorithm
    terms: AlgorithmTerms
    algorithm_id: str
    schedule: ExecutionSchedule | None
    children: PersistentMap[str, ChildProgress] = field(default_factory=PersistentMap)
    released: Decimal = _ZERO
    filled: Decimal = _ZERO
    returned: Decimal = _ZERO
    executions: PersistentSet[str] = field(default_factory=PersistentSet)
    observed_volume: Decimal = _ZERO
    observed_until: float | None = None
    status: AlgorithmStatus = AlgorithmStatus.WORKING
    reason: str = ""
    notes: tuple[str, ...] = ()

    @property
    def working(self) -> Decimal:
        """Quantity released and neither filled nor returned."""

        return _sub(_sub(self.released, self.filled), self.returned)

    @property
    def unreleased(self) -> Decimal:
        """Quantity the algorithm may still release: never released, or returned."""

        return _add(_sub(self.parent.quantity, self.released), self.returned)

    @property
    def remaining(self) -> Decimal:
        """Quantity not yet filled."""

        return _sub(self.parent.quantity, self.filled)

    @property
    def is_terminal(self) -> bool:
        """Whether the run will release no further child."""

        return self.status in _TERMINAL_STATUSES

    @property
    def working_children(self) -> tuple[ChildOrder, ...]:
        """Children still working, in release order."""

        return tuple(p.child for p in self.children.values() if p.outcome is None)

    @property
    def displayed_quantity(self) -> Decimal:
        """What the venue can see: quantity working in released children."""

        return self.working

    @property
    def hidden_quantity(self) -> Decimal:
        """What the venue cannot see: quantity not yet released."""

        return self.unreleased


def _run_id(parent: OrderRequest, algorithm: ExecutionAlgorithm, terms: AlgorithmTerms) -> str:
    return _digest(
        [
            ALGORITHM_RUN_SCHEME,
            f"configuration={algorithm_configuration_id(algorithm)}",
            *terms._render(),
            f"parent={parent.order_id!r}|{parent.asset_id!r}|{parent.side}|{parent.quantity}"
            f"|{parent.price}|{parent.timestamp!r}|{parent.strategy_id!r}",
            *(f"contribution={c.strategy_id!r}|{c.quantity}" for c in parent.contributions),
        ]
    )


def start_algorithm(
    parent: OrderRequest, algorithm: ExecutionAlgorithm, terms: AlgorithmTerms
) -> AlgorithmState:
    """Validate a run and plan it. Nothing is released until :func:`release_children`.

    Raises:
        ExecutionValidationError: If the parent is not positive or not a whole
            number of increments; a slice, tranche or child bound is not; or a
            schedule cannot be planned.
    """

    if parent.quantity <= _ZERO:
        raise ExecutionValidationError(
            f"Parent {parent.order_id} is for {parent.quantity}; there is nothing to work."
        )
    _require_multiple(parent.quantity, terms.quantity_increment, "The parent quantity")
    if isinstance(algorithm, Slicing) and algorithm.slice_quantity is not None:
        _require_multiple(algorithm.slice_quantity, terms.quantity_increment, "A slice")
    if isinstance(algorithm, Iceberg):
        _require_multiple(algorithm.display_quantity, terms.quantity_increment, "A tranche")
    if isinstance(algorithm, Participation):
        for bound, name in (
            (algorithm.min_child_quantity, "The minimum child"),
            (algorithm.max_child_quantity, "The maximum child"),
        ):
            if bound is not None:
                _require_multiple(bound, terms.quantity_increment, name)

    schedule = (
        plan_schedule(parent.quantity, algorithm, terms)
        if isinstance(algorithm, TWAP | VWAP)
        else None
    )
    notes = (schedule.note,) if schedule is not None and schedule.note else ()
    return AlgorithmState(
        parent=parent,
        algorithm=algorithm,
        terms=terms,
        algorithm_id=_run_id(parent, algorithm, terms),
        schedule=schedule,
        notes=notes,
    )


def _child(
    state: AlgorithmState, quantity: Decimal, now: float, expires: float | None
) -> ChildOrder:
    sequence = len(state.children) + 1
    return ChildOrder(
        child_id=f"{state.parent.order_id}/{sequence}",
        parent_order_id=state.parent.order_id,
        sequence=sequence,
        asset_id=state.parent.asset_id,
        side=state.parent.side,
        quantity=quantity,
        order_type=state.terms.order_type,
        limit_price=state.terms.limit_price,
        released_at=now,
        expires_at=expires,
        strategy_id=state.parent.strategy_id,
        contributions=state.parent.contributions,
        algorithm_id=state.algorithm_id,
    )


def _released(state: AlgorithmState, child: ChildOrder) -> AlgorithmState:
    return replace(
        state,
        children=state.children.set(child.child_id, ChildProgress(child, _ZERO, None)),
        released=_add(state.released, child.quantity),
    )


def _floor_to(quantity: Decimal, increment: Decimal) -> Decimal:
    units = _CONTEXT.divide(quantity, increment).to_integral_value(rounding=ROUND_FLOOR)
    return _CONTEXT.multiply(units, increment)


def _observe(
    state: AlgorithmState, volumes: Sequence[IntervalVolume]
) -> tuple[AlgorithmState, list[str]]:
    notes: list[str] = []
    observed, until = state.observed_volume, state.observed_until
    for interval in volumes:
        if interval.start < state.terms.start or interval.end > state.terms.end:
            raise ExecutionValidationError(
                f"Volume observed over [{interval.start}, {interval.end}) lies outside the "
                f"window [{state.terms.start}, {state.terms.end}); it is not this parent's."
            )
        if until is not None and interval.start < until:
            raise ExecutionValidationError(
                f"Volume observed from {interval.start} overlaps what was already observed up "
                f"to {until}. Counting it twice would oversize every later child."
            )
        until = interval.end
        if interval.volume is None:
            notes.append(
                f"No volume was observed over [{interval.start}, {interval.end}); nothing was "
                "sized from it."
            )
            continue
        observed = _add(observed, interval.volume)
    return replace(state, observed_volume=observed, observed_until=until), notes


def _participation_release(
    state: AlgorithmState, algorithm: Participation, now: float
) -> tuple[Decimal, list[str]]:
    """Participation's next child: the top-up to ``rate`` of observed volume.

    A child below ``min_child_quantity`` is withheld -- the observed liquidity
    was insufficient -- unless it is everything left to release, which a minimum
    would otherwise strand for ever.
    """

    increment = state.terms.quantity_increment
    if now >= state.terms.end:
        if algorithm.at_end is IncompletePolicy.COMPLETE_AT_END:
            return state.unreleased, []
        return _ZERO, []
    target = min(
        _floor_to(_CONTEXT.multiply(algorithm.rate, state.observed_volume), increment),
        state.parent.quantity,
    )
    quantity = min(_sub(_sub(target, state.filled), state.working), state.unreleased)
    if quantity <= _ZERO:
        return _ZERO, []
    low, high = algorithm.min_child_quantity, algorithm.max_child_quantity
    if low is not None and quantity < low and quantity < state.unreleased:
        return (
            _ZERO,
            [
                f"At {now!r} participation allowed {quantity}, below the minimum child of "
                f"{low}: the observed liquidity was insufficient and nothing was released."
            ],
        )
    if high is not None and quantity > high:
        quantity = high
    return quantity, []


def release_children(
    state: AlgorithmState, now: float, volumes: Sequence[IntervalVolume] = ()
) -> tuple[AlgorithmState, tuple[ChildOrder, ...]]:
    """Release whatever the algorithm's rule says is due at ``now``.

    ``now`` is supplied; nothing here reads a clock. ``volumes`` are the
    intervals of market volume observed since the last call, and only
    :class:`Participation` reads them -- a schedule algorithm's volume is the
    profile it was planned on, and nothing observed changes a plan.

    At most one child is released per call: the top-up to the current target.
    A caller that calls late is caught up by that one child rather than sent one
    per missed slice.

    Raises:
        ExecutionValidationError: If ``now`` is not an instant, or an observed
            interval lies outside the window or overlaps one already observed.
    """

    _require_instant(now, "now")
    state, notes = _observe(state, volumes)
    if state.is_terminal or now < state.terms.start:
        return replace(state, notes=(*state.notes, *notes)), ()

    algorithm = state.algorithm
    expires: float | None = None
    if isinstance(algorithm, TWAP | VWAP):
        assert state.schedule is not None
        target, current = state.schedule.target_at(now)
        quantity = (
            min(_sub(_sub(target, state.filled), state.working), state.unreleased)
            if now < state.terms.end
            else _ZERO
        )
        expires = None if current is None else current.end
    elif isinstance(algorithm, Participation):
        quantity, found = _participation_release(state, algorithm, now)
        notes.extend(found)
    else:
        if state.working > _ZERO or now >= state.terms.end:
            quantity = _ZERO
        elif isinstance(algorithm, Iceberg):
            quantity = min(algorithm.display_quantity, state.unreleased)
        elif algorithm.slice_quantity is not None:
            quantity = min(algorithm.slice_quantity, state.unreleased)
        else:
            assert algorithm.slice_count is not None
            quantity = _slice_by_count(state, algorithm.slice_count)

    released: tuple[ChildOrder, ...] = ()
    if quantity > _ZERO:
        child = _child(state, quantity, now, expires)
        state = _released(state, child)
        released = (child,)
    state = _settle(replace(state, notes=(*state.notes, *notes)), now)
    return state, released


def _slice_by_count(state: AlgorithmState, count: int) -> Decimal:
    """The next child of a count-sliced parent.

    The parent's increments are apportioned over ``count`` slices by largest
    remainder, which for equal slices gives the first ``total mod count`` one
    increment more -- computed for the slice due rather than re-apportioned per
    release. Quantity a finished slice did not fill is sent after the planned
    slices, one child of whatever remains per release.
    """

    increment = state.terms.quantity_increment
    total = int(_CONTEXT.divide(state.parent.quantity, increment))
    sent = len(state.children)
    share = total // count + (1 if sent < total % count else 0)
    if sent < count and share > 0:
        return min(_CONTEXT.multiply(increment, Decimal(share)), state.unreleased)
    return state.unreleased


def _settle(state: AlgorithmState, now: float) -> AlgorithmState:
    """Move a run to its terminal status when its quantities say it has one."""

    if state.status is AlgorithmStatus.COMPLETED:
        return state
    if state.filled == state.parent.quantity:
        return replace(state, status=AlgorithmStatus.COMPLETED, reason="Everything filled.")
    if state.is_terminal:
        return state
    if now >= state.terms.end and state.working == _ZERO:
        at_end = state.algorithm.at_end if isinstance(state.algorithm, Participation) else None
        if at_end is IncompletePolicy.COMPLETE_AT_END and state.unreleased > _ZERO:
            return state
        return replace(
            state,
            status=AlgorithmStatus.EXPIRED,
            reason=(
                f"The window closed at {state.terms.end!r} with {state.remaining} of "
                f"{state.parent.quantity} unfilled and nothing working."
            ),
        )
    return state


def record_child_execution(
    state: AlgorithmState,
    child_id: str,
    execution_id: str,
    quantity: Decimal,
    timestamp: float,
) -> AlgorithmState:
    """Book a child's fill. Idempotent in ``execution_id``.

    A fill still counts after the run is cancelled or failed: a child working
    when the run stopped can fill before its cancel lands, and that fill is
    real.

    Raises:
        ExecutionValidationError: If the child is unknown or already finished,
            the quantity is not positive, or it would fill the child beyond its
            quantity.
    """

    if execution_id in state.executions:
        return state
    progress = state.children.get(child_id)
    if progress is None:
        raise ExecutionValidationError(f"No child {child_id!r} was released by this run.")
    if progress.outcome is not None:
        raise ExecutionValidationError(
            f"Child {child_id} is already {progress.outcome}; a fill against it is a break for "
            "the venue boundary to resolve, not quantity for the schedule to absorb."
        )
    if quantity <= _ZERO:
        raise ExecutionValidationError(f"A fill of {quantity} is not a fill.")
    filled = _add(progress.filled, quantity)
    if filled > progress.child.quantity:
        raise ExecutionValidationError(
            f"A fill of {quantity} takes child {child_id} to {filled} of "
            f"{progress.child.quantity}: an overfill."
        )
    outcome = OrderStatus.FILLED if filled == progress.child.quantity else None
    booked = replace(
        state,
        children=state.children.set(child_id, replace(progress, filled=filled, outcome=outcome)),
        filled=_add(state.filled, quantity),
        executions=state.executions.add(execution_id),
    )
    return _settle(booked, timestamp)


#: The outcomes a child may finish with other than by filling.
_UNFILLED_OUTCOMES: Final = frozenset(
    {OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED}
)


def record_child_outcome(
    state: AlgorithmState, child_id: str, outcome: OrderStatus, timestamp: float
) -> AlgorithmState:
    """Book a child finishing without filling: cancelled, expired or rejected.

    Whatever it did not fill goes back to the parent, to be released again by
    the rule. A rejection fails the run -- the algorithm does not retry a
    venue's refusal. Recording the same outcome twice is a no-op.

    Raises:
        ExecutionValidationError: If the outcome is not one of the three, the
            child is unknown, or it already finished differently.
    """

    if outcome not in _UNFILLED_OUTCOMES:
        raise ExecutionValidationError(
            f"{outcome} is not how a child finishes without filling; fills are booked by "
            "record_child_execution."
        )
    progress = state.children.get(child_id)
    if progress is None:
        raise ExecutionValidationError(f"No child {child_id!r} was released by this run.")
    if progress.outcome is outcome:
        return state
    if progress.outcome is not None:
        raise ExecutionValidationError(
            f"Child {child_id} already finished {progress.outcome}; it cannot also be {outcome}."
        )
    closed = replace(
        state,
        children=state.children.set(child_id, replace(progress, outcome=outcome)),
        returned=_add(state.returned, _sub(progress.child.quantity, progress.filled)),
    )
    if outcome is OrderStatus.REJECTED and not closed.is_terminal:
        closed = replace(
            closed,
            status=AlgorithmStatus.FAILED,
            reason=f"Child {child_id} was rejected by the venue; the run stops rather than retry.",
        )
    return _settle(closed, timestamp)


def cancel_algorithm(
    state: AlgorithmState, now: float, reason: str
) -> tuple[AlgorithmState, tuple[ChildOrder, ...]]:
    """Stop a run and return the children the caller must cancel at the venue.

    Nothing further is released. A run already finished is returned unchanged
    with nothing to cancel.
    """

    _require_instant(now, "now")
    if state.is_terminal:
        return state, ()
    return (
        replace(state, status=AlgorithmStatus.CANCELLED, reason=reason or "Cancelled."),
        state.working_children,
    )
