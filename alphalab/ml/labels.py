"""Labels for supervised research, each knowing the instant it became known (v4.3, plan I.3).

A label is an outcome after an event, and it is known only once that outcome
is. Every :class:`Label` carries its ``start`` -- the event's instant, when the
features describing it must already be known -- and its ``end``, the instant
its outcome was realized. A training set built at an instant may hold only
labels whose ``end`` came before it, and purging (:mod:`alphalab.research.purging`)
reads the same ends.

Three makers, every choice stated:

* :class:`FixedHorizon` -- the simple return ``horizon`` observations ahead,
  or its sign beyond a stated threshold.
* :class:`TripleBarrier` -- the first of an upper and a lower barrier, set at
  stated multiples of a declared, point-in-time volatility estimate, or a
  vertical barrier ``horizon`` observations ahead (Lopez de Prado, 2018, ch. 3).
* :func:`meta_labels` -- whether a primary rule's side, taken at each of its
  events, reached its profit barrier first: the barriers oriented by the side.

Every return is ``p_end / p_start - 1`` and every volatility the sample
standard deviation of simple returns, by correctly rounded operations (``fsum``,
division, ``sqrt``): labels are class 2 (ADR-0058), the same on every host.

What cannot be labelled is counted, never invented: an event without enough
history for its volatility estimate, one whose estimate is zero, and one whose
outcome the data has not yet reached.

:func:`average_uniqueness` weighs each label by how much of what it spans it
shares with no other label (Lopez de Prado, 2018, ch. 4), over the rows it is
given -- so a training set weighs its labels among themselves, never against
labels whose outcome it could not yet know.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from alphalab.factor_library.observations import ObservationFrame
from alphalab.ml.exceptions import MLInputError

__all__ = [
    "LABEL_SET_SCHEME",
    "FixedHorizon",
    "Label",
    "LabelSet",
    "RollingVolatility",
    "TripleBarrier",
    "VerticalOutcome",
    "average_uniqueness",
    "fixed_horizon_labels",
    "meta_labels",
    "triple_barrier_labels",
]

#: The scheme tag a label set's identity is derived under: its first line.
LABEL_SET_SCHEME: Final = "alphalab.label_set.v1"


def _count(value: object, name: str, least: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise MLInputError(f"{name} is an integer of at least {least}, got {value!r}.")
    return value


def _positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MLInputError(f"{name} is a number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise MLInputError(f"{name} is a finite positive number, got {value!r}.")
    return number


class VerticalOutcome(StrEnum):
    """A triple-barrier label's value when neither horizontal barrier is touched."""

    #: The sign of the return at the vertical barrier: 1, -1, or 0 for none.
    SIGN = "sign"
    #: Zero: no barrier touched is no outcome.
    ZERO = "zero"


@dataclass(frozen=True, slots=True)
class RollingVolatility:
    """The sample standard deviation of the last ``window`` simple returns up to an event.

    The returns end at the event's own observation, so the estimate uses
    nothing after it. An event with fewer than ``window`` returns before it has
    no estimate and no label.

    Attributes:
        window: How many returns, at least two.
    """

    window: int

    def __post_init__(self) -> None:
        _count(self.window, "A volatility window", 2)

    def describe(self) -> str:
        return f"rolling volatility of {self.window} simple returns"

    def at(self, prices: Sequence[float], index: int) -> float | None:
        """The estimate at observation ``index``, or ``None`` before the window fills."""

        if index < self.window:
            return None
        returns = [
            prices[j] / prices[j - 1] - 1.0 for j in range(index - self.window + 1, index + 1)
        ]
        mean = math.fsum(returns) / len(returns)
        variance = math.fsum((r - mean) * (r - mean) for r in returns) / (len(returns) - 1)
        return math.sqrt(variance)


@dataclass(frozen=True, slots=True)
class FixedHorizon:
    """The simple return ``horizon`` observations after each event, or its sign.

    Attributes:
        horizon: Observations ahead, at least one.
        threshold: ``None`` for the return itself, a regression label; or the
            dead zone of a classification: 1 above ``threshold``, -1 below
            ``-threshold``, 0 within.
    """

    horizon: int
    threshold: float | None

    def __post_init__(self) -> None:
        _count(self.horizon, "A horizon", 1)
        if self.threshold is not None:
            if isinstance(self.threshold, bool) or not isinstance(self.threshold, int | float):
                raise MLInputError(f"A threshold is a number or None, got {self.threshold!r}.")
            threshold = float(self.threshold)
            if not math.isfinite(threshold) or threshold < 0.0:
                raise MLInputError(
                    f"A threshold is a finite number of at least zero, got {self.threshold!r}."
                )
            object.__setattr__(self, "threshold", threshold)

    def describe(self) -> str:
        what = "the return" if self.threshold is None else f"its sign beyond {self.threshold!r}"
        return f"fixed horizon: {what} {self.horizon} observations ahead"


@dataclass(frozen=True, slots=True)
class TripleBarrier:
    """The first barrier a price path touches after an event (Lopez de Prado, 2018, ch. 3).

    With ``sigma`` the declared volatility estimate at the event, the upper
    barrier is a return of ``upper * sigma``, the lower one of
    ``-lower * sigma``, and the vertical one ``horizon`` observations ahead.
    The label is 1 when the upper barrier is touched first, -1 when the lower
    one is, and ``at_vertical``'s value at the vertical barrier. A barrier is
    touched when the return reaches it or passes it.

    Attributes:
        horizon: The vertical barrier, in observations ahead, at least one.
        upper: The upper barrier's multiple of ``sigma``, or ``None`` for none.
        lower: The lower barrier's multiple of ``sigma``, or ``None`` for none.
        volatility: The declared estimate barriers are set from.
        at_vertical: The label when the vertical barrier comes first.

    Raises:
        MLInputError: If a field is out of its range, or neither horizontal
            barrier is set: that is a fixed horizon, and is stated as one.
    """

    horizon: int
    upper: float | None
    lower: float | None
    volatility: RollingVolatility
    at_vertical: VerticalOutcome

    def __post_init__(self) -> None:
        _count(self.horizon, "A horizon", 1)
        if self.upper is None and self.lower is None:
            raise MLInputError(
                "A triple barrier with neither horizontal barrier is a fixed horizon; state "
                "it as FixedHorizon."
            )
        for name in ("upper", "lower"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _positive(value, f"The {name} multiple"))
        if not isinstance(self.volatility, RollingVolatility):
            raise MLInputError(f"volatility is a RollingVolatility, got {self.volatility!r}.")
        if not isinstance(self.at_vertical, VerticalOutcome):
            raise MLInputError(f"at_vertical is a VerticalOutcome, got {self.at_vertical!r}.")

    def describe(self) -> str:
        return (
            f"triple barrier: upper {self.upper!r} and lower {self.lower!r} times the "
            f"{self.volatility.describe()}, vertical {self.horizon} observations ahead, "
            f"{self.at_vertical.value} at the vertical"
        )


@dataclass(frozen=True, slots=True)
class Label:
    """One event's label.

    Attributes:
        subject: What the event happened to.
        start: The event's instant: the features describing it are those known
            then.
        end: The instant the outcome was realized, after ``start``.
        value: The label.
        outcome: The simple return from ``start`` to ``end``.
    """

    subject: str
    start: float
    end: float
    value: float
    outcome: float


@dataclass(frozen=True, slots=True)
class LabelSet:
    """Every label a maker gave, how it was made, and what it could not label.

    Attributes:
        method: The maker, in words, every choice stated.
        labels: The labels, by start, then subject.
        clocks: Each subject's observation instants, which uniqueness counts
            in.
        source: The price field the labels were computed from.
        dataset_version: The dataset the prices were read from, or ``None``.
        timezone_name: The zone the instants are reported in.
        unlabelled: Events no label was given, by reason.
    """

    method: str
    labels: tuple[Label, ...]
    clocks: Mapping[str, tuple[float, ...]]
    source: str
    dataset_version: str | None
    timezone_name: str
    unlabelled: Mapping[str, int]

    def __len__(self) -> int:
        return len(self.labels)

    @property
    def label_id(self) -> str:
        """The derived identity: the method, the source and every label and clock, in order."""

        lines = [
            LABEL_SET_SCHEME,
            self.method,
            f"{self.source}|{self.dataset_version}|{self.timezone_name}",
        ]
        lines.extend(
            f"{label.subject}|{label.start!r}|{label.end!r}|{label.value!r}|{label.outcome!r}"
            for label in self.labels
        )
        lines.extend(
            f"clock {subject}|" + ",".join(repr(t) for t in clock)
            for subject, clock in sorted(self.clocks.items())
        )
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _paths(prices: object) -> list[tuple[str, tuple[float, ...], tuple[float, ...]]]:
    """Each subject's instants and prices, refused unless every price is positive."""

    if not isinstance(prices, ObservationFrame):
        raise MLInputError(f"Labels are computed from an ObservationFrame, got {prices!r}.")
    paths = []
    for subject in sorted(prices.series):
        series = prices.series[subject]
        for value in series.values:
            if not value > 0.0 or not math.isfinite(value):
                raise MLInputError(
                    f"{subject}'s prices hold {value!r}; a return needs every price finite and "
                    "positive."
                )
        paths.append((subject, tuple(series.timestamps), tuple(series.values)))
    if not paths:
        raise MLInputError("The frame holds no series to label.")
    return paths


def _label_set(
    method: str,
    labels: list[Label],
    prices: ObservationFrame,
    unlabelled: Mapping[str, int],
) -> LabelSet:
    labels.sort(key=lambda label: (label.start, label.subject))
    return LabelSet(
        method=method,
        labels=tuple(labels),
        clocks=MappingProxyType(
            {subject: tuple(series.timestamps) for subject, series in sorted(prices.series.items())}
        ),
        source=prices.source_field.name,
        dataset_version=prices.dataset_version,
        timezone_name=prices.timezone_name,
        unlabelled=MappingProxyType(dict(unlabelled)),
    )


def fixed_horizon_labels(prices: ObservationFrame, maker: FixedHorizon) -> LabelSet:
    """Label every observation by the return ``maker.horizon`` observations later.

    The last ``horizon`` observations of each subject have no label yet: their
    outcome lies beyond the data, and they are counted as ``"unresolved"``.

    Raises:
        MLInputError: If ``maker`` is not a FixedHorizon, the frame holds no
            series, or a price is not finite and positive.
    """

    if not isinstance(maker, FixedHorizon):
        raise MLInputError(f"maker is a FixedHorizon, got {maker!r}.")
    labels: list[Label] = []
    unresolved = 0
    for subject, instants, values in _paths(prices):
        for index in range(len(values)):
            ahead = index + maker.horizon
            if ahead >= len(values):
                unresolved += 1
                continue
            outcome = values[ahead] / values[index] - 1.0
            if maker.threshold is None:
                value = outcome
            elif outcome > maker.threshold:
                value = 1.0
            elif outcome < -maker.threshold:
                value = -1.0
            else:
                value = 0.0
            labels.append(Label(subject, instants[index], instants[ahead], value, outcome))
    return _label_set(maker.describe(), labels, prices, {"unresolved": unresolved})


def _first_touch(
    values: Sequence[float],
    index: int,
    side: float,
    maker: TripleBarrier,
    sigma: float,
) -> tuple[int, float] | None:
    """The observation the event at ``index`` resolves at, and its label; ``None`` if unresolved.

    Returns are taken in the side's direction: ``side * (p / p_start - 1)``.
    """

    upper = None if maker.upper is None else maker.upper * sigma
    lower = None if maker.lower is None else -(maker.lower * sigma)
    start = values[index]
    last = min(index + maker.horizon, len(values) - 1)
    for ahead in range(index + 1, last + 1):
        signed = side * (values[ahead] / start - 1.0)
        if upper is not None and signed >= upper:
            return ahead, 1.0
        if lower is not None and signed <= lower:
            return ahead, -1.0
    if index + maker.horizon > len(values) - 1:
        return None
    vertical = side * (values[index + maker.horizon] / start - 1.0)
    if maker.at_vertical is VerticalOutcome.ZERO or vertical == 0.0:
        return index + maker.horizon, 0.0
    return index + maker.horizon, 1.0 if vertical > 0.0 else -1.0


def triple_barrier_labels(prices: ObservationFrame, maker: TripleBarrier) -> LabelSet:
    """Label every observation by the first of the maker's three barriers its path touches.

    Counted, not labelled: ``"warm-up"`` events before the volatility window
    fills, ``"flat"`` events whose estimate is zero -- every barrier would sit
    at the start's price -- and ``"unresolved"`` events that touched no
    barrier before the data ended short of their vertical barrier.

    Raises:
        MLInputError: If ``maker`` is not a TripleBarrier, the frame holds no
            series, or a price is not finite and positive.
    """

    if not isinstance(maker, TripleBarrier):
        raise MLInputError(f"maker is a TripleBarrier, got {maker!r}.")
    labels: list[Label] = []
    counts = {"warm-up": 0, "flat": 0, "unresolved": 0}
    for subject, instants, values in _paths(prices):
        for index in range(len(values)):
            sigma = maker.volatility.at(values, index)
            if sigma is None:
                counts["warm-up"] += 1
                continue
            if sigma == 0.0:
                counts["flat"] += 1
                continue
            touched = _first_touch(values, index, 1.0, maker, sigma)
            if touched is None:
                counts["unresolved"] += 1
                continue
            ahead, value = touched
            outcome = values[ahead] / values[index] - 1.0
            labels.append(Label(subject, instants[index], instants[ahead], value, outcome))
    return _label_set(maker.describe(), labels, prices, counts)


def meta_labels(
    prices: ObservationFrame,
    maker: TripleBarrier,
    sides: Mapping[tuple[str, float], float],
) -> LabelSet:
    """Whether each side a primary rule took reached its profit barrier first.

    ``sides`` maps an event -- a subject and an instant it was observed at --
    to the primary rule's side there: 1 for long, -1 for short. The barriers
    are oriented by the side: ``upper`` is the profit taken in the side's
    direction and ``lower`` the loss stopped against it. The meta-label is 1
    when the profit barrier is touched first, or when the vertical barrier
    comes first with a positive return in the side's direction; otherwise 0.
    A meta-model learns when to act on the primary rule, not which way.

    Counted, not labelled, as :func:`triple_barrier_labels` counts them.

    Raises:
        MLInputError: If a side is not 1 or -1, or an event names a subject or
            an instant the prices do not hold.
    """

    if not isinstance(maker, TripleBarrier):
        raise MLInputError(f"maker is a TripleBarrier, got {maker!r}.")
    paths = {subject: (instants, values) for subject, instants, values in _paths(prices)}
    labels: list[Label] = []
    counts = {"warm-up": 0, "flat": 0, "unresolved": 0}
    for (subject, instant), side in sorted(sides.items()):
        if isinstance(side, bool) or side not in (1, -1, 1.0, -1.0):
            raise MLInputError(
                f"The side at ({subject!r}, {instant!r}) is {side!r}; a side is 1 or -1."
            )
        if subject not in paths:
            raise MLInputError(f"An event names {subject!r}, which the prices do not hold.")
        instants, values = paths[subject]
        index = bisect_right(instants, instant) - 1
        if index < 0 or instants[index] != instant:
            raise MLInputError(
                f"An event at {instant!r} names an instant {subject!r} has no price at."
            )
        sigma = maker.volatility.at(values, index)
        if sigma is None:
            counts["warm-up"] += 1
            continue
        if sigma == 0.0:
            counts["flat"] += 1
            continue
        touched = _first_touch(values, index, float(side), maker, sigma)
        if touched is None:
            counts["unresolved"] += 1
            continue
        ahead, value = touched
        outcome = values[ahead] / values[index] - 1.0
        labels.append(
            Label(subject, instant, instants[ahead], 1.0 if value > 0.0 else 0.0, outcome)
        )
    return _label_set(f"meta-labels of a primary side: {maker.describe()}", labels, prices, counts)


def average_uniqueness(
    rows: Sequence[tuple[str, float, float]],
    clocks: Mapping[str, Sequence[float]],
) -> tuple[float, ...]:
    """Each row's average uniqueness among ``rows``: a weight in ``(0, 1]`` (Lopez de Prado, ch. 4).

    A row ``(subject, start, end)`` spans its subject's observations after
    ``start`` up to and including ``end`` -- the returns its outcome is made
    of. At each observation, the concurrency is how many of ``rows`` on that
    subject span it; a row's uniqueness is the mean, over what it spans, of one
    over the concurrency. A row no other overlaps has uniqueness one.

    Raises:
        MLInputError: If a row names a subject with no clock, or spans no
            observation of it.
    """

    spans: list[tuple[str, int, int]] = []
    for subject, start, end in rows:
        if subject not in clocks:
            raise MLInputError(f"A row names {subject!r}, which has no clock.")
        clock = clocks[subject]
        low = bisect_right(clock, start)
        high = bisect_right(clock, end)
        if high <= low:
            raise MLInputError(
                f"The row ({subject!r}, {start!r}, {end!r}) spans no observation of its subject."
            )
        spans.append((subject, low, high))
    concurrency: dict[str, list[int]] = {}
    for subject, low, high in spans:
        counts = concurrency.setdefault(subject, [0] * (len(clocks[subject]) + 1))
        for position in range(low, high):
            counts[position] += 1
    return tuple(
        math.fsum(1.0 / concurrency[subject][position] for position in range(low, high))
        / (high - low)
        for subject, low, high in spans
    )
