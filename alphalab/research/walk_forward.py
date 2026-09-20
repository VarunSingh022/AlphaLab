"""Walk-forward: train, validate, test, roll, repeat -- with the boundaries stated.

A walk-forward scheme cuts a series into folds that each carry three parts and
move forward together::

    fold 0   |--- train ---|-- val --|- test -|
    fold 1          |--- train ---|-- val --|- test -|
    fold 2                 |--- train ---|-- val --|- test -|

Three parts, not two, and the distinction is the whole point. Parameters are
*chosen* on validation and *reported* on test. A scheme with two parts has to
choose and report on the same data, and the number it reports is then an
in-sample number wearing an out-of-sample label -- which is the single most
common way a research result turns out not to reproduce.

:class:`WindowMode` decides whether the training window slides or grows.
``ROLLING`` keeps a fixed history, which is what a study assuming the world
changes wants; ``EXPANDING`` keeps everything, which is what a study assuming
a stable relationship wants. Neither is the default, because the choice is a
statement about the market and not about the software.

Sizes are periods, and are exact
--------------------------------

``train_size``, ``validation_size``, ``test_size`` and ``step`` are counts of
*observations*, so a fold holds exactly that many instants whatever the
calendar did in between. A scheme expressed in days would silently give a fold
spanning a holiday week fewer observations than its neighbour, and the folds
would not be comparable.

Purging reaches across both boundaries
--------------------------------------

Given a :class:`~alphalab.research.purging.PurgePolicy`, each fold purges twice
rather than once:

* the **training** set against the validation span, removing rows whose labels
  reach forward into the data the fold selects on;
* the **validation** set against the test span, removing rows whose labels
  reach forward into the data the fold reports on.

The second is the one that is usually left out. Without it a parameter can be
chosen using an outcome that overlaps the test window, and the test score is
then contaminated by the selection rather than by the fit -- a subtler leak,
and one that survives every check that only looks at the training set.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import Enum, auto

from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.purging import PurgePolicy, apply_purge_and_embargo
from alphalab.research.splits import (
    SplitInterval,
    SplitReport,
    TimeSplit,
    require_chronological,
)

__all__ = ["WindowMode", "walk_forward_splits"]


class WindowMode(Enum):
    """Whether the training window slides forward or grows."""

    #: A fixed-length history that moves with the fold. Assumes the recent past
    #: is more relevant than the distant one.
    ROLLING = auto()
    #: Everything from the start of the series to the fold. Assumes the
    #: relationship being fitted is stable.
    EXPANDING = auto()


def _span(instants: Sequence[float]) -> SplitInterval:
    """The tightest half-open interval containing exactly ``instants``.

    The upper bound is the next representable float above the last instant
    rather than an invented epsilon or the following observation's timestamp:
    it is exact, it depends on nothing outside the part, and it keeps adjacent
    parts from sharing an instant.
    """

    return SplitInterval(instants[0], math.nextafter(instants[-1], math.inf))


def walk_forward_splits(
    timestamps: Sequence[float],
    train_size: int,
    validation_size: int,
    test_size: int,
    mode: WindowMode,
    step: int | None = None,
    policy: PurgePolicy | None = None,
) -> SplitReport:
    """Generate walk-forward folds over ``timestamps``.

    ``step`` defaults to ``test_size``, which makes consecutive folds' test
    windows exactly adjacent and therefore partition the tested region with no
    instant tested twice and none skipped. Any other step is accepted and
    recorded, so a caller who wants overlapping test windows can have them and
    the scheme string says that is what they asked for.

    The number of folds is whatever fits: generation stops when the next fold
    would run past the end of the series, rather than producing a final short
    fold whose sizes differ from its neighbours'.

    Raises:
        ResearchValidationError: If any size is not positive; if ``step`` is
            not positive; if the series is too short to produce even one fold,
            with a message naming how many instants it would take; or if
            purging empties a fold's training or validation set -- that is a
            real configuration problem, and a scheme that silently dropped the
            fold would report a different experiment from the one requested.
    """

    instants = require_chronological(timestamps)

    for name, size in (
        ("train_size", train_size),
        ("validation_size", validation_size),
        ("test_size", test_size),
    ):
        if size < 1:
            raise ResearchValidationError(f"{name} must be at least 1 observation, got {size}.")

    stride = test_size if step is None else step
    if stride < 1:
        raise ResearchValidationError(f"step must be at least 1 observation, got {stride}.")

    fold_width = train_size + validation_size + test_size
    if len(instants) < fold_width:
        raise ResearchValidationError(
            f"A walk-forward fold of {train_size} + {validation_size} + {test_size} needs "
            f"{fold_width} instants and the series has {len(instants)}. Shorten the windows "
            "or lengthen the sample; there is no partial fold, because a fold with fewer "
            "observations than its neighbours is not comparable with them."
        )

    scheme = (
        f"walk_forward(mode={mode.name},train={train_size},validation={validation_size},"
        f"test={test_size},step={stride})"
    )

    folds: list[TimeSplit] = []
    anchor = 0
    while anchor + fold_width <= len(instants):
        train_start = 0 if mode is WindowMode.EXPANDING else anchor
        train_stop = anchor + train_size
        validation_stop = train_stop + validation_size
        test_stop = validation_stop + test_size

        train_instants = instants[train_start:train_stop]
        validation_instants = instants[train_stop:validation_stop]
        test_instants = instants[validation_stop:test_stop]

        train_span = _span(train_instants)
        validation_span = _span(validation_instants)
        test_span = _span(test_instants)

        trained = apply_purge_and_embargo(train_instants, validation_span, policy)
        selected = apply_purge_and_embargo(validation_instants, test_span, policy)

        index = len(folds)
        if not trained.kept:
            raise ResearchValidationError(
                f"Fold {index} of {scheme} has no training instants left after purging: all "
                f"{len(train_instants)} of them carry labels reaching into the validation "
                "span. Lengthen the training window or shorten the label horizon."
            )
        if not selected.kept:
            raise ResearchValidationError(
                f"Fold {index} of {scheme} has no validation instants left after purging "
                f"against the test span: all {len(validation_instants)} carry labels "
                "reaching into it. Lengthen the validation window or shorten the horizon."
            )

        folds.append(
            TimeSplit(
                index=index,
                train=trained.kept,
                validation=selected.kept,
                train_span=train_span,
                validation_span=validation_span,
                test=test_instants,
                test_span=test_span,
                purged=trained.purged,
                embargoed=trained.embargoed,
                validation_purged=selected.purged,
            )
        )
        anchor += stride

    return SplitReport(
        scheme=scheme,
        folds=tuple(folds),
        instants=len(instants),
        label_horizon_seconds=None,
        embargo_seconds=0.0 if policy is None else policy.embargo_seconds,
    )
