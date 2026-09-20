"""Purging and embargo, defined by the information windows rather than by dates.

Splitting a time series at an instant does not separate it. Two things reach
across the cut, in opposite directions:

* **Labels reach forward.** A training observation at ``t`` whose target is a
  20-period forward return depends on prices up to ``t + 20``. If the
  validation window begins before ``t + 20``, that training row was fitted
  using the very outcomes the validation is supposed to be an out-of-sample
  measurement of. **Purging** removes it.
* **Features reach backward.** A training observation *after* the validation
  window whose feature is a 60-period moving average depends on prices inside
  that window. **Embargo** removes it, for a stated span after the validation
  ends.

Both are removals from the *training* set. The validation window is never
touched: shrinking it would change what is being measured, and the point is to
measure the same thing with a training set that does not already contain it.

Subtraction of dates is not purging
-----------------------------------

The roadmap is explicit that these must not be "superficial date subtraction",
and the difference is concrete. Purging by "drop the last N days of training"
is date subtraction: it is right only if every observation's label happens to
span exactly N days, and quietly wrong for any series with gaps, holidays or
uneven spacing. :func:`label_ends_from_horizon` instead reads the *actual*
series and reports, for each instant, the instant at which that observation's
label is realized -- twenty observations later means twenty observations later,
whether that is twenty-eight calendar days across a holiday or twenty.

Purging then removes every training instant whose realized label end reaches
into the validation span. On an evenly spaced series with no gaps the two
approaches agree; on a real one they do not, and this one is right.

An unrealized label is not a training row
-----------------------------------------

The final ``horizon`` instants of a series have no realized label at all --
the data ends before their outcome does. :func:`label_ends_from_horizon` maps
them to infinity, so purging removes them from every training set that has any
validation after it. That is the correct treatment and it is easy to get wrong
in the other direction: keeping them means training on rows whose target had to
be invented.

The horizon is required
-----------------------

:class:`PurgePolicy` has no default horizon and cannot be built without one.
Phase 7 of the roadmap requires that "where a method requires event horizons
and the input data does not provide them, fail explicitly rather than
guessing", and a default of zero would be exactly such a guess: it would make
every purged split silently identical to an unpurged one while reporting that
purging had been applied.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.splits import SplitInterval

__all__ = [
    "PurgePolicy",
    "PurgeResult",
    "apply_purge_and_embargo",
    "label_ends_from_horizon",
]


def label_ends_from_horizon(
    timestamps: Sequence[float], horizon_periods: int
) -> dict[float, float]:
    """When each instant's label is realized, read off the series itself.

    Returns a mapping from every instant to the instant ``horizon_periods``
    observations later. The final ``horizon_periods`` instants map to
    ``math.inf``: their labels are not realized inside this series, and
    infinity is what makes purging remove them rather than treat them as
    already settled.

    This is the exact answer, not an approximation of one. A horizon expressed
    in seconds would have to assume even spacing; this reads the spacing that
    is there.

    Raises:
        ResearchValidationError: If ``horizon_periods`` is not positive, or if
            ``timestamps`` is empty. A horizon of zero says the label is known
            at the moment of the decision, which needs no purging and should
            not claim to have had any.
    """

    if horizon_periods < 1:
        raise ResearchValidationError(
            f"A label horizon must be at least 1 observation, got {horizon_periods}. A "
            "horizon of zero means the outcome is known when the decision is made, which "
            "needs no purging -- omit the policy rather than configuring an empty one."
        )
    if not timestamps:
        raise ResearchValidationError("There are no instants to derive label ends from.")

    ends: dict[float, float] = {}
    count = len(timestamps)
    for index, stamp in enumerate(timestamps):
        target = index + horizon_periods
        ends[stamp] = timestamps[target] if target < count else math.inf
    return ends


@dataclass(frozen=True, slots=True)
class PurgePolicy:
    """How far each observation's information reaches, in both directions.

    Attributes:
        label_ends: Instant to the instant its label is realized at. Built by
            :func:`label_ends_from_horizon` from a horizon in periods, or
            supplied directly for a study whose labels have per-observation
            spans -- a triple-barrier label, for instance, whose horizon
            differs per event.
        embargo_seconds: How long after a validation span a training
            observation is still considered contaminated by it. Zero means no
            embargo, which is correct for a forward-only scheme where no
            training instant follows its own validation.

    Raises:
        ResearchValidationError: If ``label_ends`` is empty, or if
            ``embargo_seconds`` is negative.
    """

    label_ends: Mapping[float, float]
    embargo_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.label_ends:
            raise ResearchValidationError(
                "A purge policy needs a label end for every instant it will be applied to, "
                "and was given none. Build one with label_ends_from_horizon, or supply the "
                "per-observation spans directly; there is no default horizon, because a "
                "default would make an unpurged split report that it had been purged."
            )
        if self.embargo_seconds < 0.0:
            raise ResearchValidationError(
                f"embargo_seconds must not be negative, got {self.embargo_seconds!r}."
            )

    def label_end(self, timestamp: float) -> float:
        """When ``timestamp``'s label is realized.

        Raises:
            ResearchValidationError: If the policy does not cover ``timestamp``.
                Assuming a horizon for an instant nobody described would be the
                guess this module exists to refuse.
        """

        try:
            return self.label_ends[timestamp]
        except KeyError:
            raise ResearchValidationError(
                f"The purge policy has no label end for the instant {timestamp!r}, so how "
                "far that observation's information reaches is unknown. Build the policy "
                "from the same series the split partitions."
            ) from None


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """A training set after removal, and exactly what was removed from it.

    Attributes:
        kept: The training instants that survived, ascending.
        purged: Instants removed because their labels reached into the
            validation span.
        embargoed: Instants removed because they fell in the embargo after it.
    """

    kept: tuple[float, ...]
    purged: tuple[float, ...]
    embargoed: tuple[float, ...]


def apply_purge_and_embargo(
    candidates: Sequence[float],
    validation: SplitInterval,
    policy: PurgePolicy | None,
) -> PurgeResult:
    """Remove from ``candidates`` everything contaminated by ``validation``.

    An instant is purged when its label end reaches ``validation.start`` or
    later *and* the instant itself precedes the validation span -- the forward
    overlap. An instant is embargoed when it falls in
    ``[validation.end, validation.end + embargo_seconds)`` -- the backward
    overlap, which only exists when the training set extends past validation.

    An instant inside the validation span itself is purged rather than kept:
    it is not a training row at all, and a scheme that offered one has already
    made a mistake this function should not quietly absorb. It is reported
    under ``purged`` so the count says so.

    ``policy`` of ``None`` returns every candidate unchanged, with empty
    removals -- the honest representation of "no purging was asked for", as
    distinct from a zero-length purge that claims to have been applied.

    Raises:
        ResearchValidationError: If the policy does not cover one of the
            candidate instants.
    """

    if policy is None:
        return PurgeResult(kept=tuple(candidates), purged=(), embargoed=())

    embargo_end = validation.end + policy.embargo_seconds

    kept: list[float] = []
    purged: list[float] = []
    embargoed: list[float] = []

    for stamp in candidates:
        if validation.contains(stamp):
            purged.append(stamp)
            continue
        if stamp < validation.start:
            if policy.label_end(stamp) >= validation.start:
                purged.append(stamp)
            else:
                kept.append(stamp)
            continue
        # At or after the validation span ends.
        if stamp < embargo_end:
            embargoed.append(stamp)
        else:
            kept.append(stamp)

    return PurgeResult(kept=tuple(kept), purged=tuple(purged), embargoed=tuple(embargoed))
