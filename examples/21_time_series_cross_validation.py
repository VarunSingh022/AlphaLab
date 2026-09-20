"""
AlphaLab Examples
=================

Example 21 : Time-Series Cross-Validation

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 20 (walk-forward validation)

Topics
------

• Rolling and expanding CV, which are forward-only
• Blocked purged k-fold, which deliberately is not
• Purging defined by label windows, not by subtracting dates
• An embargo, and why it only bites on a blocked scheme
• Names that are claims: PURGED without a policy is refused

What this shows
---------------

Ordinary k-fold shuffles, and on a time series that is not a minor inaccuracy
-- it is the whole result. Every scheme here preserves order, and they differ
in what they do about the two ways information still crosses a boundary that
respects it:

    labels reach FORWARD   ->  purging
    features reach BACKWARD ->  embargo

Run

    python examples/21_time_series_cross_validation.py
"""

from datetime import UTC, datetime

from _research_panel import banner, lineage, load_panel

from alphalab.api import observe
from alphalab.factor_library import FeatureField
from alphalab.research import (
    CVMethod,
    PurgePolicy,
    ResearchValidationError,
    apply_purge_and_embargo,
    cross_validation_splits,
    label_ends_from_horizon,
)

HORIZON = 5
EMBARGO_DAYS = 5


def _day(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d")


def main() -> None:
    banner(21, "Time-Series Cross-Validation")

    dataset = load_panel()
    frame = observe(dataset, FeatureField.CLOSE)
    instants = list(frame.timestamps)

    print()
    print("Step 01 - The index, and the label horizon over it")
    print(f"  dataset version : {lineage(dataset)[:54]}...")
    print(f"  instants        : {len(instants)}")

    ends = label_ends_from_horizon(instants, HORIZON)
    sample = instants[30]
    print(f"  a {HORIZON}-period label on {_day(sample)} is realized on {_day(ends[sample])}")
    elapsed_days = round((ends[sample] - sample) / 86400)
    print(f"  which is {elapsed_days} CALENDAR days, not {HORIZON} -- the series has weekends.")
    print()
    print("  That is the difference between purging and date subtraction.")
    print("  'Drop the last N days' is right only if every observation's label")
    print("  spans exactly N days; label_ends_from_horizon reads the actual")
    print("  spacing, whatever it is.")

    unrealized = sum(1 for value in ends.values() if value == float("inf"))
    print(f"  unrealized labels at the end of the sample : {unrealized}")
    print("  Those rows have no outcome inside the data and are purged from")
    print("  every training set. Keeping them means training on invented targets.")

    # ------------------------------------------------------------------
    # Step 02 : The four schemes side by side
    # ------------------------------------------------------------------

    purge = PurgePolicy(ends)
    embargo = PurgePolicy(ends, embargo_seconds=EMBARGO_DAYS * 86400.0)

    schemes = {
        CVMethod.ROLLING: cross_validation_splits(instants, 5, CVMethod.ROLLING, purge),
        CVMethod.EXPANDING: cross_validation_splits(instants, 5, CVMethod.EXPANDING, purge),
        CVMethod.PURGED: cross_validation_splits(instants, 5, CVMethod.PURGED, purge),
        CVMethod.EMBARGOED: cross_validation_splits(instants, 5, CVMethod.EMBARGOED, embargo),
    }

    print()
    print("Step 02 - Four schemes")
    print(f"  {'method':<12} {'folds':>6} {'purged':>8} {'embargoed':>10} {'forward only':>13}")
    for method, report in schemes.items():
        print(
            f"  {method.name:<12} {len(report):>6} {report.total_purged:>8} "
            f"{report.total_embargoed:>10} {report.is_forward_only!s:>13}"
        )
    print()
    print("  ROLLING and EXPANDING train only on instants before their")
    print("  validation block, so an embargo -- which removes training rows")
    print("  AFTER it -- has nothing to act on. It is not wrong to configure")
    print("  one; it removes nothing, and the report reads zero so a caller")
    print("  can see that rather than believing a protection was applied.")

    # ------------------------------------------------------------------
    # Step 03 : A blocked fold trains on both sides
    # ------------------------------------------------------------------

    blocked = schemes[CVMethod.EMBARGOED]
    middle = blocked.folds[2]
    before = [s for s in middle.train if s < min(middle.validation)]
    after = [s for s in middle.train if s > max(middle.validation)]

    print()
    print("Step 03 - A blocked fold, in full")
    print(f"  {middle.describe()}")
    print(f"  validation block  : {_day(middle.validation[0])} .. {_day(middle.validation[-1])}")
    print(f"  training before   : {len(before)} instants, ending {_day(max(before))}")
    print(f"  training after    : {len(after)} instants, starting {_day(min(after))}")
    print(f"  forward only      : {middle.is_forward_only}")
    print()
    print("  Training data on the far side of the block is what makes this a")
    print("  k-fold estimate of generalization rather than a walk-forward one.")
    print("  It is ALSO what makes the backward leak possible -- a feature")
    print("  window reaching back into the block -- and therefore what makes")
    print("  the embargo a real protection rather than a ceremonial one.")
    print()
    print("  It is not a simulation of trading: a trader does not have next")
    print("  year's data. Use walk_forward_splits when you need that.")

    # ------------------------------------------------------------------
    # Step 04 : The embargo, as membership rather than as a count
    # ------------------------------------------------------------------

    block_end = max(middle.validation)
    window = [s for s in instants if block_end < s <= block_end + EMBARGO_DAYS * 86400.0]

    print()
    print(f"Step 04 - The {EMBARGO_DAYS}-day embargo after that block")
    print(f"  block ends        : {_day(block_end)}")
    print(f"  instants inside   : {[_day(s) for s in window]}")
    print(f"  removed from train: {[_day(s) for s in middle.embargoed]}")
    print(f"  none of them train: {not set(window) & set(middle.train)}")

    # ------------------------------------------------------------------
    # Step 05 : Purging, checked directly
    # ------------------------------------------------------------------

    fold = schemes[CVMethod.EXPANDING].folds[1]
    last_train = instants.index(fold.train[-1])
    first_validation = instants.index(fold.validation[0])

    print()
    print("Step 05 - Purging, checked against the labels themselves")
    print(
        f"  last training instant : {_day(fold.train[-1])} "
        f"(label realized {_day(ends[fold.train[-1]])})"
    )
    print(f"  validation starts     : {_day(fold.validation[0])}")
    print(
        f"  observation gap       : {first_validation - last_train}, "
        f"which exceeds the horizon of {HORIZON}"
    )
    print(f"  purged                : {[_day(s) for s in fold.purged]}")
    print(
        f"  every purged label reaches into validation : "
        f"{all(ends[s] >= fold.validation[0] for s in fold.purged)}"
    )

    # The same operation called directly, over every instant before the block.
    direct = apply_purge_and_embargo(
        tuple(instants[:first_validation]), fold.validation_span, purge
    )
    print(
        f"  apply_purge_and_embargo directly : {len(direct.kept)} kept, "
        f"{len(direct.purged)} purged, {len(direct.embargoed)} embargoed"
    )
    print(f"  which matches the fold            : {direct.purged == fold.purged}")

    # ------------------------------------------------------------------
    # Step 06 : A name is a claim
    # ------------------------------------------------------------------

    print()
    print("Step 06 - The refusals that keep a scheme's name true")

    try:
        cross_validation_splits(instants, 5, CVMethod.PURGED)
    except ResearchValidationError as error:
        print(f"  PURGED, no policy : {str(error)[:66]}...")

    try:
        cross_validation_splits(instants, 5, CVMethod.EMBARGOED, purge)
    except ResearchValidationError as error:
        print(f"  EMBARGOED, no gap : {str(error)[:66]}...")

    try:
        cross_validation_splits(instants, 5, CVMethod.PURGED, embargo)
    except ResearchValidationError as error:
        print(f"  PURGED with a gap : {str(error)[:66]}...")

    try:
        PurgePolicy({})
    except ResearchValidationError as error:
        print(f"  empty policy      : {str(error)[:66]}...")

    print()
    print("  There is no default horizon anywhere. A default would make an")
    print("  unpurged split report that it had been purged, which is worse")
    print("  than not purging at all.")

    # ------------------------------------------------------------------
    # Step 07 : Every instant is validated exactly once
    # ------------------------------------------------------------------

    validated = [s for fold in blocked.folds for s in fold.validation]

    print()
    print("Step 07 - Coverage of the blocked scheme")
    print(f"  instants validated : {len(validated)} of {len(instants)}")
    print(f"  each exactly once  : {len(validated) == len(set(validated))}")
    print(f"  covers the sample  : {sorted(validated) == instants}")

    print()
    print("=" * 74)
    print("Every fold above carries the instants in each of its parts, so none")
    print("of this has to be taken on trust -- the boundaries are printed, and")
    print("tests/regression/test_research_cannot_see_the_future.py asserts them.")
    print("=" * 74)


if __name__ == "__main__":
    main()
