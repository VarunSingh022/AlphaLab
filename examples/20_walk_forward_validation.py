"""
AlphaLab Examples
=================

Example 20 : Walk-Forward Validation

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 19 (signal diagnostics)

Topics
------

• Train, validate, test, roll -- and why three parts rather than two
• Rolling versus expanding training windows
• Exact fold boundaries, inspectable instant by instant
• Purging the training set, and purging validation against test
• A configuration that is genuinely unusable, and the refusal that says so

What this shows
---------------

Parameters are *chosen* on validation and *reported* on test. A scheme with two
parts has to choose and report on the same data, and the number it reports is
then an in-sample number wearing an out-of-sample label.

Every fold carries the timestamps in each of its parts, so nothing here has to
be taken on trust: the example prints the boundaries and checks them.

Run

    python examples/20_walk_forward_validation.py
"""

from datetime import UTC, datetime
from itertools import pairwise

from _research_panel import banner, lineage, load_panel

from alphalab.api import observe
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    compute_panel,
    forward_returns,
    information_coefficient,
)
from alphalab.research import (
    PurgePolicy,
    ResearchValidationError,
    WindowMode,
    label_ends_from_horizon,
    signal_diagnostics,
    walk_forward_splits,
)

MOMENTUM = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)
HORIZON = 5


def _day(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d")


def main() -> None:
    banner(20, "Walk-Forward Validation")

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)
    instants = list(frame.timestamps)

    print()
    print("Step 01 - The time index being partitioned")
    print(f"  dataset version : {lineage(dataset)[:54]}...")
    print(f"  instants        : {len(instants)}")
    print(f"  span            : {_day(instants[0])} to {_day(instants[-1])}")
    spacings = {round(later - earlier) for earlier, later in pairwise(instants)}
    print(f"  distinct spacings : {len(spacings)}  (weekends are absent, so it is uneven)")
    print("  Window sizes are counts of OBSERVATIONS, never seconds -- a scheme")
    print("  expressed in days would give a fold spanning a holiday fewer rows")
    print("  than its neighbour, and the folds would not be comparable.")

    # ------------------------------------------------------------------
    # Step 02 : Rolling, without purging, to see the raw boundaries
    # ------------------------------------------------------------------

    plain = walk_forward_splits(instants, 60, 20, 20, WindowMode.ROLLING)

    print()
    print("Step 02 - Rolling walk-forward, no purging")
    print(f"  scheme          : {plain.scheme}")
    print(f"  folds           : {len(plain)}")
    print(f"  {'fold':>5} {'train':>6} {'val':>5} {'test':>5}   train ends    val starts")
    for fold in plain.folds:
        print(
            f"  {fold.index:>5} {len(fold.train):>6} {len(fold.validation):>5} "
            f"{len(fold.test):>5}   {_day(fold.train[-1])}    "
            f"{_day(fold.validation[0])}"
        )
    gap = instants.index(plain.folds[0].validation[0]) - instants.index(plain.folds[0].train[-1])
    print(f"  gap between last train and first validation : {gap} observation(s)")
    print(f"  purged          : {plain.total_purged}")

    # ------------------------------------------------------------------
    # Step 03 : The same scheme with purging
    # ------------------------------------------------------------------

    policy = PurgePolicy(label_ends_from_horizon(instants, HORIZON))
    purged = walk_forward_splits(instants, 60, 20, 20, WindowMode.ROLLING, policy=policy)

    print()
    print(f"Step 03 - The same scheme, purged for a {HORIZON}-period label horizon")
    print(f"  {'fold':>5} {'train':>6} {'purged':>7} {'val':>5} {'val purged':>11} {'test':>5}")
    for fold in purged.folds:
        print(
            f"  {fold.index:>5} {len(fold.train):>6} {len(fold.purged):>7} "
            f"{len(fold.validation):>5} {len(fold.validation_purged):>11} "
            f"{len(fold.test):>5}"
        )
    purged_gap = instants.index(purged.folds[0].validation[0]) - instants.index(
        purged.folds[0].train[-1]
    )
    print(
        f"  gap now         : {purged_gap} observation(s), which exceeds the horizon of {HORIZON}"
    )
    print()
    print("  Purging happens twice per fold:")
    print("    - training rows whose labels reach into the validation window;")
    print("    - validation rows whose labels reach into the TEST window.")
    print("  The second is usually left out. Without it a parameter can be")
    print("  chosen using an outcome that overlaps the test, and the test score")
    print("  is contaminated by the selection rather than by the fit.")

    # ------------------------------------------------------------------
    # Step 04 : Rolling versus expanding
    # ------------------------------------------------------------------

    expanding = walk_forward_splits(instants, 60, 20, 20, WindowMode.EXPANDING, policy=policy)

    print()
    print("Step 04 - Rolling versus expanding")
    print(f"  {'fold':>5} {'rolling train':>14} {'expanding train':>16}")
    for rolling_fold, expanding_fold in zip(purged.folds, expanding.folds, strict=True):
        print(
            f"  {rolling_fold.index:>5} {len(rolling_fold.train):>14} "
            f"{len(expanding_fold.train):>16}"
        )
    print()
    print("  Rolling keeps a fixed history and assumes the world changes.")
    print("  Expanding keeps everything and assumes the relationship is stable.")
    print("  Neither is the default: the choice is a statement about the market.")

    # ------------------------------------------------------------------
    # Step 05 : Every fold is independently inspectable
    # ------------------------------------------------------------------

    fold = purged.folds[0]

    print()
    print("Step 05 - One fold, in full")
    print(f"  {fold.describe()}")
    print(f"  train  first/last : {_day(fold.train[0])} .. {_day(fold.train[-1])}")
    print(f"  purged            : {[_day(s) for s in fold.purged]}")
    print(f"  validation        : {_day(fold.validation[0])} .. {_day(fold.validation[-1])}")
    print(f"  test              : {_day(fold.test[0])} .. {_day(fold.test[-1])}")
    print(f"  forward only      : {fold.is_forward_only}")
    print(f"  parts disjoint    : {not (set(fold.train) & set(fold.validation) & set(fold.test))}")

    # ------------------------------------------------------------------
    # Step 06 : Measuring per fold
    # ------------------------------------------------------------------

    signal = compute_panel(MOMENTUM, frame)
    realized = forward_returns(frame, HORIZON)

    print()
    print("Step 06 - The signal measured fold by fold")
    print(f"  {'fold':>5} {'validation IC':>14} {'test IC':>10} {'val n':>7} {'test n':>7}")
    for fold in purged.folds:
        validation = signal_diagnostics(
            signal, realized, minimum_assets=5, instants=fold.validation
        )
        test = signal_diagnostics(signal, realized, minimum_assets=5, instants=fold.test)
        val_ic = (
            "-" if validation.rank_ic.mean_rank is None else f"{validation.rank_ic.mean_rank:+.4f}"
        )
        test_ic = "-" if test.rank_ic.mean_rank is None else f"{test.rank_ic.mean_rank:+.4f}"
        print(
            f"  {fold.index:>5} {val_ic:>14} {test_ic:>10} "
            f"{validation.observations:>7} {test.observations:>7}"
        )

    whole = information_coefficient(signal, realized, minimum_assets=5)
    print(f"  whole sample IC : {whole.mean_rank:+.4f} over {whole.instants_measured} instants")
    print()
    print("  The per-fold numbers are what a walk-forward result reports. The")
    print("  whole-sample number is printed for comparison only: it is measured")
    print("  on data that includes every fold's training set.")

    # ------------------------------------------------------------------
    # Step 07 : A configuration that cannot be validated
    # ------------------------------------------------------------------

    print()
    print("Step 07 - A refusal worth reading")
    long_policy = PurgePolicy(label_ends_from_horizon(instants, 20))
    try:
        walk_forward_splits(instants, 60, 20, 20, WindowMode.ROLLING, policy=long_policy)
    except ResearchValidationError as error:
        print("  20-period labels with a 20-period validation window:")
        for line in str(error).split(". "):
            print(f"    {line.strip()}")
    print()
    print("  This is not a corner case. Every validation row's outcome falls")
    print("  inside the test window, so nothing is left to select on that has")
    print("  not already seen the data the test is meant to be independent of.")

    print()
    print("=" * 74)
    print(
        f"{len(purged)} folds, {purged.total_purged} training instants purged, "
        f"{purged.total_validation_purged} validation instants purged,"
    )
    print(f"forward-only: {purged.is_forward_only}.")
    print("=" * 74)


if __name__ == "__main__":
    main()
