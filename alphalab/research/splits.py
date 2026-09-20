"""Folds: what is in each one, and the exact instants that make it up.

A :class:`TimeSplit` carries the *timestamps* of its train, validation and test
sets, not the interval bounds they were derived from. That is a deliberate cost
-- a split over a million observations holds a million floats -- and it buys the
two properties the v3.2 roadmap makes release-blocking.

**Every fold is independently inspectable.** A caller can print a fold and see
which instants it trained on, and a test can assert the exact membership rather
than asserting that a boundary function returned the number it was asked to.
Phase 12 of the roadmap says leakage tests must "assert the exact split,
timestamps, included rows, and outputs" rather than merely that an exception
occurred, and that is only possible if the rows are there to assert against.

**Purging is a set operation, not an arithmetic one.** An interval cannot
express "everything up to here except the last eleven observations, whose
labels reach into the validation window". A tuple of timestamps can, and
:attr:`TimeSplit.purged` names exactly which ones were taken out.

Half-open, everywhere
---------------------

:class:`SplitInterval` is ``[start, end)``. Two adjacent intervals therefore
share no instant, and an observation belongs to exactly one of them. A closed
upper bound would put the boundary observation in both the training set and the
validation set of the same fold, which is a one-row train/test contamination
that no aggregate statistic would ever reveal.

What this module does not decide
--------------------------------

Nothing here knows what a model is, and nothing fits one. A split is a
partition of instants; what a caller does with each part is their business.
That is what makes the same splits usable for a factor study, a parameter
sweep and a strategy backtest without any of them inheriting assumptions from
the others.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from alphalab.research.exceptions import ResearchValidationError

__all__ = ["SplitInterval", "SplitReport", "TimeSplit", "require_chronological"]


@dataclass(frozen=True, slots=True)
class SplitInterval:
    """A half-open span of time, ``[start, end)``.

    Attributes:
        start: Inclusive lower bound, in Unix seconds.
        end: Exclusive upper bound. Must be strictly greater than ``start``;
            an empty span is not a span, and one that could be empty would let
            a fold report a validation window nothing was ever measured in.
    """

    start: float
    end: float

    def __post_init__(self) -> None:
        if not self.end > self.start:
            raise ResearchValidationError(
                f"A split interval must end after it starts, got [{self.start!r}, "
                f"{self.end!r}). An empty interval is not a window."
            )

    def contains(self, timestamp: float) -> bool:
        """Whether ``timestamp`` falls in ``[start, end)``."""

        return self.start <= timestamp < self.end

    def overlaps(self, other: SplitInterval) -> bool:
        """Whether two half-open intervals share any instant."""

        return self.start < other.end and other.start < self.end

    def __str__(self) -> str:
        return f"[{self.start!r}, {self.end!r})"


@dataclass(frozen=True, slots=True)
class TimeSplit:
    """One fold, as the set of instants in each of its parts.

    Attributes:
        index: Position in the scheme that generated it, from 0.
        train: Instants to fit on, ascending, after purging and embargo.
        validation: Instants to select on, ascending.
        test: Instants to report on, ascending. Empty for a scheme that draws
            no test set -- cross-validation selects, walk-forward selects and
            then reports, and conflating the two is how a "test" score ends up
            being the one a parameter was chosen by.
        purged: Instants removed from ``train`` because their label windows
            reached into the validation span. Empty when no purging was asked
            for.
        embargoed: Instants removed from ``train`` because they fell within the
            embargo after the validation span. Empty for a forward-only scheme,
            where the training set never extends past validation -- reported as
            empty rather than omitted, so a caller can see that the embargo
            they configured had nothing to act on.
        validation_purged: Instants removed from ``validation`` because their
            label windows reached into the *test* span. Only a scheme that
            draws a test set can produce these, and it matters for exactly the
            reason purging the training set does: a parameter selected on a
            validation row whose outcome overlaps the test window was selected
            using the data the test was meant to be independent of.
        train_span: The interval ``train`` was drawn from, before removals.
        validation_span: The interval ``validation`` was drawn from.
        test_span: The interval ``test`` was drawn from, or ``None``.

    Raises:
        ResearchValidationError: If any part is not ascending, if two parts
            share an instant, or if ``train`` is empty. A fold that trains on
            nothing is not a fold, and one whose parts intersect is a
            contamination whatever else it reports.
    """

    index: int
    train: tuple[float, ...]
    validation: tuple[float, ...]
    train_span: SplitInterval
    validation_span: SplitInterval
    test: tuple[float, ...] = ()
    test_span: SplitInterval | None = None
    purged: tuple[float, ...] = ()
    embargoed: tuple[float, ...] = ()
    validation_purged: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        for name, part in (
            ("train", self.train),
            ("validation", self.validation),
            ("test", self.test),
        ):
            if any(b <= a for a, b in pairwise(part)):
                raise ResearchValidationError(
                    f"Fold {self.index}: {name} instants are not strictly ascending. A fold "
                    "whose order is not the data's order cannot be reasoned about."
                )

        if not self.train:
            raise ResearchValidationError(
                f"Fold {self.index} has an empty training set. Purging and embargo can empty "
                "one legitimately when the label horizon is long relative to the fold; that "
                "is a reason to lengthen the training window or shorten the horizon, not a "
                "fold to evaluate."
            )
        if not self.validation:
            raise ResearchValidationError(
                f"Fold {self.index} has an empty validation set, so there is nothing to select on."
            )

        parts = {"train": set(self.train), "validation": set(self.validation)}
        if self.test:
            parts["test"] = set(self.test)
        names = sorted(parts)
        for left_index, left in enumerate(names):
            for right in names[left_index + 1 :]:
                shared = parts[left] & parts[right]
                if shared:
                    raise ResearchValidationError(
                        f"Fold {self.index}: {len(shared)} instant(s) are in both {left} and "
                        f"{right}, for example {sorted(shared)[:3]}. That is train/test "
                        "contamination, and no aggregate metric would reveal it."
                    )

    @property
    def train_size(self) -> int:
        """How many instants survived into the training set."""

        return len(self.train)

    @property
    def removed(self) -> int:
        """How many instants purging and embargo took out of the training set."""

        return len(self.purged) + len(self.embargoed)

    @property
    def is_forward_only(self) -> bool:
        """Whether every training instant precedes every validation instant.

        The property a walk-forward scheme claims and a blocked k-fold does
        not. Checked from the instants themselves rather than from the scheme
        that produced them, so it reports what the fold *is*.
        """

        return max(self.train) < min(self.validation)

    def describe(self) -> str:
        """One line naming the fold's sizes and spans, for a report or a log."""

        test = f" test={len(self.test)}" if self.test else ""
        return (
            f"fold {self.index}: train={len(self.train)} {self.train_span} "
            f"validation={len(self.validation)} {self.validation_span}{test} "
            f"purged={len(self.purged)} embargoed={len(self.embargoed)}"
        )


def require_chronological(timestamps: Sequence[float]) -> tuple[float, ...]:
    """Return ``timestamps`` as a tuple, or refuse.

    Every scheme in :mod:`alphalab.research.walk_forward` and
    :mod:`alphalab.research.cross_validation_schemes` starts here. Sorting
    silently would be the wrong kindness: a caller who passes unordered
    instants has a bug somewhere upstream, and a split generator that hid it
    would produce folds that look right and are drawn from a series whose order
    the caller does not know.

    Raises:
        ResearchValidationError: If ``timestamps`` is empty, or if it is not
            strictly ascending -- which includes a repeat, since two
            observations at one instant make "the next fold" ambiguous.
    """

    if not timestamps:
        raise ResearchValidationError(
            "A split needs a series of instants to partition and was given none."
        )
    for previous, current in pairwise(timestamps):
        if current <= previous:
            raise ResearchValidationError(
                f"Instants must be strictly ascending; {current!r} follows {previous!r}. "
                "Sort and deduplicate the series before splitting it -- doing it here would "
                "hide an ordering problem in the data rather than report one."
            )
    return tuple(timestamps)


@dataclass(frozen=True, slots=True)
class SplitReport:
    """Every fold a scheme produced, and the configuration that produced them.

    Attributes:
        scheme: A rendered description of the generator and its parameters, so
            a result can say how it was split without holding the generator.
        folds: The folds, in generation order.
        instants: How many instants the scheme partitioned.
        label_horizon_seconds: The label horizon purging was applied under, or
            ``None`` when no purging was asked for.
        embargo_seconds: The embargo that was applied, in seconds.
    """

    scheme: str
    folds: tuple[TimeSplit, ...] = field(default_factory=tuple)
    instants: int = 0
    label_horizon_seconds: float | None = None
    embargo_seconds: float = 0.0

    def __len__(self) -> int:
        return len(self.folds)

    @property
    def total_purged(self) -> int:
        """Instants purging removed across every fold."""

        return sum(len(fold.purged) for fold in self.folds)

    @property
    def total_embargoed(self) -> int:
        """Instants the embargo removed across every fold."""

        return sum(len(fold.embargoed) for fold in self.folds)

    @property
    def total_validation_purged(self) -> int:
        """Validation instants removed across every fold for overlapping test."""

        return sum(len(fold.validation_purged) for fold in self.folds)

    @property
    def is_forward_only(self) -> bool:
        """Whether every fold trains only on instants before its validation."""

        return all(fold.is_forward_only for fold in self.folds)
