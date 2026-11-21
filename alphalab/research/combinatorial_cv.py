"""Combinatorial purged cross-validation: every choice of test groups, and their paths (v4.3).

Cross-validation tests each block once, so a strategy scored by it has one
out-of-sample history. Combinatorial purged cross-validation (CPCV; López de
Prado, *Advances in Financial Machine Learning*, ch. 12) splits the series into
``groups`` blocks and tests every choice of ``test_groups`` of them, training on
the rest:

* there are ``C(groups, test_groups)`` splits, in lexicographic order of their
  test groups;
* each group is tested in ``C(groups - 1, test_groups - 1)`` of them, so the
  splits' test blocks assemble into that many **paths**, each covering every
  group once: path ``p`` takes, for each group, the ``p``-th split that tests
  it. That is ``test_groups / groups * C(groups, test_groups)`` paths, and each
  is an out-of-sample history of the whole series.

A split's training instants are purged and embargoed against each of its test
groups, by :func:`~alphalab.research.purging.apply_purge_and_embargo`: an
instant whose label reaches into a test group is removed, and so is one that
follows a test group within the embargo. The name is a claim that this was
done, so a purge policy is required, as ``CVMethod.PURGED`` requires one.

This module is research's split mathematics, beside
:mod:`~alphalab.research.walk_forward` and
:mod:`~alphalab.research.time_series_cv`; the trial ledger runs what it splits
(ADR-0073).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.purging import PurgePolicy, apply_purge_and_embargo
from alphalab.research.splits import SplitInterval, require_chronological

__all__ = ["CombinatorialSplit", "CombinatorialSplitReport", "combinatorial_purged_splits"]


@dataclass(frozen=True, slots=True)
class CombinatorialSplit:
    """One choice of test groups: what is trained on, what is tested, what was removed.

    Attributes:
        index: Position in the lexicographic order of the test groups, from 0.
        test_groups: The groups tested, ascending.
        train: The other groups' instants after purging and embargo, ascending.
        test: The test groups' instants, ascending.
        purged: Instants removed from ``train`` because their labels reached
            into a test group.
        embargoed: Instants removed from ``train`` because they followed a test
            group within the embargo.
    """

    index: int
    test_groups: tuple[int, ...]
    train: tuple[float, ...]
    test: tuple[float, ...]
    purged: tuple[float, ...]
    embargoed: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CombinatorialSplitReport:
    """Every split, the groups they choose among, and the paths the splits make.

    Attributes:
        scheme: A rendered description of the parameters, as a
            :class:`~alphalab.research.splits.SplitReport` names its own.
        groups: Each group's instants, in order.
        test_groups: How many groups each split tests.
        splits: Every split, in lexicographic order of its test groups.
        paths: ``paths[p][g]`` is the index of the split that tests group ``g``
            on path ``p``.
        embargo_seconds: The embargo that was applied.
    """

    scheme: str
    groups: tuple[tuple[float, ...], ...]
    test_groups: int
    splits: tuple[CombinatorialSplit, ...]
    paths: tuple[tuple[int, ...], ...]
    embargo_seconds: float


def _span(instants: Sequence[float]) -> SplitInterval:
    return SplitInterval(instants[0], instants[-1])


def combinatorial_purged_splits(
    timestamps: Sequence[float],
    groups: int,
    test_groups: int,
    policy: PurgePolicy,
) -> CombinatorialSplitReport:
    """Split ``timestamps`` into ``groups`` blocks and test every choice of ``test_groups``.

    The blocks are equal but for the last, which absorbs the remainder, as
    cross-validation's blocked folds are cut, so every instant is in exactly one
    group.

    Raises:
        ResearchValidationError: If ``groups`` is below 2, ``test_groups`` is
            not between 1 and ``groups - 1``, the series is too short for every
            group to hold two instants, no purge policy is given, or purging
            and embargo leave a split nothing to train on.
    """

    instants = require_chronological(timestamps)
    if isinstance(groups, bool) or not isinstance(groups, int) or groups < 2:
        raise ResearchValidationError(f"groups must be an integer of at least 2, got {groups!r}.")
    if (
        isinstance(test_groups, bool)
        or not isinstance(test_groups, int)
        or not 1 <= test_groups < groups
    ):
        raise ResearchValidationError(
            f"test_groups must be an integer from 1 to {groups - 1}, got {test_groups!r}: a "
            "split tests some groups and trains on the others."
        )
    if not isinstance(policy, PurgePolicy):
        raise ResearchValidationError(
            "Combinatorial purged cross-validation is a claim that label overlap was "
            f"removed, and no PurgePolicy was given (got {policy!r}). Build one with "
            "label_ends_from_horizon; there is no default horizon."
        )
    if len(instants) < groups * 2:
        raise ResearchValidationError(
            f"{groups} groups need at least {groups * 2} instants for each to hold two; the "
            f"series has {len(instants)}."
        )

    width = len(instants) // groups
    blocks = tuple(
        instants[g * width : len(instants) if g == groups - 1 else (g + 1) * width]
        for g in range(groups)
    )
    splits: list[CombinatorialSplit] = []
    for index, chosen in enumerate(itertools.combinations(range(groups), test_groups)):
        test = tuple(stamp for g in chosen for stamp in blocks[g])
        kept = tuple(stamp for g in range(groups) if g not in chosen for stamp in blocks[g])
        purged: list[float] = []
        embargoed: list[float] = []
        for g in chosen:
            result = apply_purge_and_embargo(kept, _span(blocks[g]), policy)
            kept = result.kept
            purged.extend(result.purged)
            embargoed.extend(result.embargoed)
        if not kept:
            raise ResearchValidationError(
                f"Split {index} (test groups {list(chosen)}) has no training instants left "
                "after purging and embargo. Use fewer test groups, shorten the label horizon "
                "or shorten the embargo."
            )
        splits.append(
            CombinatorialSplit(
                index=index,
                test_groups=tuple(chosen),
                train=kept,
                test=test,
                purged=tuple(sorted(purged)),
                embargoed=tuple(sorted(embargoed)),
            )
        )

    occurrences = [
        [split.index for split in splits if g in split.test_groups] for g in range(groups)
    ]
    count = math.comb(groups - 1, test_groups - 1)
    assert all(len(each) == count for each in occurrences)
    paths = tuple(tuple(occurrences[g][p] for g in range(groups)) for p in range(count))
    embargo = policy.embargo_seconds
    return CombinatorialSplitReport(
        scheme=(
            f"combinatorial_purged(groups={groups},test_groups={test_groups},embargo={embargo!r})"
        ),
        groups=blocks,
        test_groups=test_groups,
        splits=tuple(splits),
        paths=paths,
        embargo_seconds=embargo,
    )
