"""A 24/7 clock is not 24/7 data.

:meth:`alphalab.data.calendar.MarketCalendar.continuous` models a venue that
never closes, and it is the right model: the market really is open at 03:00 on a
Sunday. But "the market was open" and "there is an observation" are different
claims, and on a continuous venue the difference is invisible.

On a weekday market it is not. A missing Tuesday bar stands out because Monday
and Wednesday are there and Tuesday is a trading day. On a 24/7 venue every
instant is a trading instant, so a six-hour outage, a delisted pair, a feed
reconnect and a genuinely quiet period all look identical: a gap between two
timestamps. A research path that treats the continuous clock as its sample
silently claims observations it never had, and one that resamples onto a
regular grid silently *creates* them.

This module reports the gaps and creates nothing. There is no fill, no
forward-carry and no interpolation, for the reason
:class:`alphalab.data.cleaning.MissingValuePolicy` has no ``FILL`` member: an
invented print is invisible by the time it reaches an equity curve.

The expected cadence is declared
---------------------------------

A gap is only a gap relative to how often observations were expected, and that
is a property of the subscription rather than of the data -- one-minute bars,
hourly bars, every trade. So :func:`observation_gaps` requires the interval and
infers none. Inferring it from the modal spacing is what
:func:`alphalab.data.time.infer_frequency` does, and it reports the share of
intervals that agree precisely because the answer is a judgement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from alphalab.crypto.exceptions import CryptoInputError

__all__ = ["CoverageReport", "ObservationGap", "coverage", "observation_gaps"]


@dataclass(frozen=True, slots=True)
class ObservationGap:
    """A stretch where observations were expected and none arrived.

    Attributes:
        after: The last timestamp observed before the gap.
        before: The first timestamp observed after it.
        seconds: ``before - after``.
        missing: How many expected observations fall strictly inside. An
            integer count of *absences*, not a set of timestamps -- naming the
            timestamps would come within one step of producing rows for them.
    """

    after: float
    before: float
    seconds: float
    missing: int


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """How much of a continuous window was actually observed.

    Attributes:
        start: Window start, inclusive.
        end: Window end, exclusive.
        expected_interval_seconds: The declared cadence.
        expected: How many observations a continuous clock implies.
        observed: How many arrived within the window.
        gaps: Every gap between consecutive observations, in order.
        coverage: ``observed / expected`` as a fraction, or ``None`` when the
            window implies no observations at all. ``None`` rather than ``1.0``
            or ``0.0``: an unmeasurable ratio is not a full one, and AlphaLab's
            rule is that an unmeasurable statistic is ``None``.
    """

    start: float
    end: float
    expected_interval_seconds: float
    expected: int
    observed: int
    gaps: tuple[ObservationGap, ...]
    coverage: float | None

    @property
    def missing(self) -> int:
        """Expected observations that did not arrive."""

        return max(self.expected - self.observed, 0)


def _validate(timestamps: Sequence[float], expected_interval_seconds: float) -> None:
    if expected_interval_seconds <= 0:
        raise CryptoInputError(
            f"expected_interval_seconds is {expected_interval_seconds}; a non-positive "
            "cadence implies infinitely many observations in every gap."
        )
    for earlier, later in pairwise(timestamps):
        if later <= earlier:
            raise CryptoInputError(
                f"Timestamps are not strictly increasing: {later} follows {earlier}. A gap "
                "measured over unsorted or duplicated observations is measured against a "
                "sequence that did not happen."
            )


def observation_gaps(
    timestamps: Sequence[float], expected_interval_seconds: float
) -> tuple[ObservationGap, ...]:
    """Every stretch between consecutive observations longer than the cadence.

    A gap is reported when the spacing exceeds the expected interval by more
    than half of it -- so ordinary jitter around the cadence is not a gap, and a
    genuinely missing observation is. The tolerance is half an interval rather
    than a configurable number because at exactly one and a half intervals the
    two readings are equally defensible, and a caller wanting a different rule
    has the raw spacings.

    Args:
        timestamps: Strictly increasing observation instants.
        expected_interval_seconds: The declared cadence.

    Raises:
        CryptoInputError: If the cadence is not positive, or the timestamps are
            not strictly increasing.
    """

    _validate(timestamps, expected_interval_seconds)
    threshold = expected_interval_seconds * 1.5
    gaps: list[ObservationGap] = []
    for earlier, later in pairwise(timestamps):
        span = later - earlier
        if span <= threshold:
            continue
        gaps.append(
            ObservationGap(
                after=earlier,
                before=later,
                seconds=span,
                missing=int(span // expected_interval_seconds) - 1,
            )
        )
    return tuple(gaps)


def coverage(
    timestamps: Sequence[float],
    start: float,
    end: float,
    expected_interval_seconds: float,
) -> CoverageReport:
    """What fraction of a continuous window the observations actually cover.

    The window is half-open, ``[start, end)``, matching every other interval in
    AlphaLab. Observations outside it are ignored rather than counted, so a
    report about January is a report about January.

    The expected count is what a *theoretical* continuous clock implies over the
    window. It is deliberately computed from the window and the cadence rather
    than from the data: computing it from the data would make coverage
    identically 1.0 and the whole measurement vacuous.

    Raises:
        CryptoInputError: If the cadence is not positive, the timestamps are not
            strictly increasing, or ``end`` does not exceed ``start``.
    """

    _validate(timestamps, expected_interval_seconds)
    if end <= start:
        raise CryptoInputError(
            f"end {end} does not exceed start {start}; a window with no duration has no "
            "coverage to measure."
        )
    inside = tuple(value for value in timestamps if start <= value < end)
    expected = int((end - start) // expected_interval_seconds)
    return CoverageReport(
        start=start,
        end=end,
        expected_interval_seconds=expected_interval_seconds,
        expected=expected,
        observed=len(inside),
        gaps=observation_gaps(inside, expected_interval_seconds),
        coverage=None if expected == 0 else len(inside) / expected,
    )
