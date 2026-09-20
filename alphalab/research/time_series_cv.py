"""Time-series cross-validation: four schemes, and what each one is for.

Ordinary k-fold shuffles. On a time series that is not a minor inaccuracy, it
is the whole result: a model trained on next month and validated on this one
will look excellent and predict nothing. Every scheme here preserves order, and
they differ in what they do about the two ways information still crosses a
boundary that respects it.

============================  =========================================
:attr:`CVMethod.ROLLING`      Fixed-length train, validation immediately
                              after, both sliding forward.
:attr:`CVMethod.EXPANDING`    Train from the start of the series to the
                              fold, validation immediately after.
:attr:`CVMethod.PURGED`       Blocked k-fold -- the series is cut into
                              ``folds`` contiguous blocks and each in turn
                              is the validation set, so training data
                              exists on *both* sides of it. Training rows
                              whose labels reach into the block are purged.
:attr:`CVMethod.EMBARGOED`    Purged, and additionally removing training
                              rows for a stated span *after* the block.
============================  =========================================

Why the last two need blocked folds to mean anything
-----------------------------------------------------

In ``ROLLING`` and ``EXPANDING`` every training instant precedes every
validation instant, so an embargo -- which removes training rows *after* the
validation window -- has nothing to act on. It is not wrong to configure one;
it simply removes nothing, and
:attr:`~alphalab.research.splits.SplitReport.total_embargoed` reads zero so a
caller can see that rather than believing a protection was applied.

Purging is different: it bites on every scheme, because in all four the
training set contains rows whose labels reach forward into validation.

``PURGED`` and ``EMBARGOED`` use blocked k-fold precisely so that both
directions exist. Training data on the far side of the validation block is what
makes the backward leak -- a feature window reaching back into the block --
possible, and therefore what makes an embargo a real protection rather than a
ceremonial one. This is the construction from the literature on purged k-fold
cross-validation, and it is implemented here as set membership rather than as
arithmetic on dates; see :mod:`alphalab.research.purging`.

Blocked folds are not forward-only, and say so
-----------------------------------------------

A ``PURGED`` fold trains on data that comes after its validation block. That is
deliberate -- it is what a k-fold estimate of generalization means -- and it is
also **not** a simulation of trading, because a trader does not have next
year's data. :attr:`~alphalab.research.splits.TimeSplit.is_forward_only`
reports ``False`` for these folds, and a study that needs a tradeable estimate
uses :mod:`alphalab.research.walk_forward` instead. Both are offered because
they answer different questions, and neither is a substitute for the other.
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

__all__ = ["CVMethod", "cross_validation_splits"]


class CVMethod(Enum):
    """Which time-series cross-validation construction to use."""

    #: Fixed-length training window sliding forward, validation after it.
    ROLLING = auto()
    #: Training window growing from the series start, validation after it.
    EXPANDING = auto()
    #: Blocked k-fold with label-overlap purging. Not forward-only.
    PURGED = auto()
    #: Purged, plus an embargo after each validation block. Not forward-only.
    EMBARGOED = auto()


def _span(instants: Sequence[float]) -> SplitInterval:
    """The tightest half-open interval containing exactly ``instants``."""

    return SplitInterval(instants[0], math.nextafter(instants[-1], math.inf))


def _forward_folds(
    instants: tuple[float, ...],
    folds: int,
    method: CVMethod,
    policy: PurgePolicy | None,
) -> list[TimeSplit]:
    """ROLLING and EXPANDING: validation blocks that follow their training set.

    The series is divided into ``folds + 1`` equal blocks. Block 0 is the
    initial training history that every fold needs, and blocks 1..folds are the
    validation sets in turn -- so every fold has a training set at least one
    block long, which a scheme dividing into ``folds`` blocks could not
    guarantee for its first fold.
    """

    blocks = folds + 1
    width = len(instants) // blocks
    built: list[TimeSplit] = []

    for index in range(folds):
        validation_start = (index + 1) * width
        validation_stop = validation_start + width
        train_start = 0 if method is CVMethod.EXPANDING else index * width

        train_instants = instants[train_start:validation_start]
        validation_instants = instants[validation_start:validation_stop]
        validation_span = _span(validation_instants)

        trained = apply_purge_and_embargo(train_instants, validation_span, policy)
        if not trained.kept:
            raise ResearchValidationError(
                f"Fold {index} has no training instants left after purging: all "
                f"{len(train_instants)} carry labels reaching into the validation block. "
                "Use fewer folds, so each block is longer, or shorten the label horizon."
            )

        built.append(
            TimeSplit(
                index=index,
                train=trained.kept,
                validation=validation_instants,
                train_span=_span(train_instants),
                validation_span=validation_span,
                purged=trained.purged,
                embargoed=trained.embargoed,
            )
        )
    return built


def _blocked_folds(
    instants: tuple[float, ...],
    folds: int,
    policy: PurgePolicy,
) -> list[TimeSplit]:
    """PURGED and EMBARGOED: each block in turn is validation, the rest is train.

    The training set is everything outside the block -- before it *and* after
    it -- which is what makes this a k-fold estimate rather than a walk-forward
    one, and what gives the embargo something to remove.
    """

    width = len(instants) // folds
    built: list[TimeSplit] = []

    for index in range(folds):
        start = index * width
        # The final block absorbs the remainder, so every instant is validated
        # exactly once across the scheme rather than a tail being silently
        # dropped.
        stop = len(instants) if index == folds - 1 else start + width

        validation_instants = instants[start:stop]
        train_candidates = instants[:start] + instants[stop:]
        if not train_candidates:
            raise ResearchValidationError(
                f"Fold {index} validates on the whole series, leaving nothing to train on. "
                "A blocked scheme needs at least 2 folds."
            )

        validation_span = _span(validation_instants)
        trained = apply_purge_and_embargo(train_candidates, validation_span, policy)
        if not trained.kept:
            raise ResearchValidationError(
                f"Fold {index} has no training instants left after purging and embargo: all "
                f"{len(train_candidates)} were removed. Use fewer folds, shorten the label "
                "horizon, or shorten the embargo."
            )

        built.append(
            TimeSplit(
                index=index,
                train=trained.kept,
                validation=validation_instants,
                train_span=_span(train_candidates),
                validation_span=validation_span,
                purged=trained.purged,
                embargoed=trained.embargoed,
            )
        )
    return built


def cross_validation_splits(
    timestamps: Sequence[float],
    folds: int,
    method: CVMethod,
    policy: PurgePolicy | None = None,
) -> SplitReport:
    """Generate cross-validation folds over ``timestamps``.

    ``policy`` is optional for :attr:`CVMethod.ROLLING` and
    :attr:`CVMethod.EXPANDING` -- a forward-only scheme without purging is a
    coherent, if optimistic, thing to run -- and **required** for
    :attr:`CVMethod.PURGED` and :attr:`CVMethod.EMBARGOED`, whose names are
    claims about what was done. ``EMBARGOED`` additionally requires the policy
    to state a positive embargo, for the same reason: a scheme called
    embargoed that embargoed nothing would be a false label on a result.

    Raises:
        ResearchValidationError: If ``folds`` is below 2; if the series is too
            short for the scheme to give every fold a non-empty training and
            validation block; if a purged method was asked for without a
            policy; if ``EMBARGOED`` was asked for without a positive embargo,
            or ``PURGED`` with one; or if purging empties a fold.
    """

    instants = require_chronological(timestamps)

    if folds < 2:
        raise ResearchValidationError(
            f"Cross-validation needs at least 2 folds, got {folds}. One fold is a single "
            "train/validation split, which is what walk_forward_splits produces and names "
            "correctly."
        )

    if method in (CVMethod.PURGED, CVMethod.EMBARGOED) and policy is None:
        raise ResearchValidationError(
            f"CVMethod.{method.name} is a claim that label overlap was removed, and no "
            "PurgePolicy was supplied. Build one with label_ends_from_horizon; there is no "
            "default horizon, because a default would make the claim untrue silently."
        )
    if method is CVMethod.EMBARGOED and policy is not None and policy.embargo_seconds <= 0.0:
        raise ResearchValidationError(
            "CVMethod.EMBARGOED was asked for with embargo_seconds="
            f"{policy.embargo_seconds!r}. An embargo of zero removes nothing, so the fold "
            "would be a PURGED one reported under a different name."
        )
    if method is CVMethod.PURGED and policy is not None and policy.embargo_seconds > 0.0:
        raise ResearchValidationError(
            "CVMethod.PURGED was asked for with a policy that states embargo_seconds="
            f"{policy.embargo_seconds!r}. Applying it would produce exactly the folds "
            "CVMethod.EMBARGOED produces while recording them under the other name, and "
            "ignoring it would leave the caller believing an embargo was in force. Ask for "
            "EMBARGOED, or set embargo_seconds to zero."
        )

    if method in (CVMethod.ROLLING, CVMethod.EXPANDING):
        blocks = folds + 1
        if len(instants) < blocks * 2:
            raise ResearchValidationError(
                f"CVMethod.{method.name} with {folds} folds divides the series into "
                f"{blocks} blocks and needs at least {blocks * 2} instants for each to hold "
                f"two; the series has {len(instants)}."
            )
        built = _forward_folds(instants, folds, method, policy)
    else:
        if len(instants) < folds * 2:
            raise ResearchValidationError(
                f"CVMethod.{method.name} with {folds} folds needs at least {folds * 2} "
                f"instants for each block to hold two; the series has {len(instants)}."
            )
        assert policy is not None  # checked above; narrows the type for the call
        built = _blocked_folds(instants, folds, policy)

    embargo = 0.0 if policy is None else policy.embargo_seconds
    return SplitReport(
        scheme=f"cross_validation(method={method.name},folds={folds},embargo={embargo!r})",
        folds=tuple(built),
        instants=len(instants),
        label_horizon_seconds=None,
        embargo_seconds=embargo,
    )
