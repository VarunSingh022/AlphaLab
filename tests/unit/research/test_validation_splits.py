"""Walk-forward and cross-validation: exact boundaries, and purging that bites.

The roadmap makes these release-blocking correctness contracts and says the
tests must "assert the exact split, timestamps, included rows, and outputs"
rather than merely that an exception occurred. So almost every assertion here
names instants rather than counts, and the purging tests check *which*
observations were removed, not how many.
"""

import math

import pytest

from alphalab.research import (
    CVMethod,
    PurgePolicy,
    ResearchValidationError,
    SplitInterval,
    TimeSplit,
    WindowMode,
    apply_purge_and_embargo,
    cross_validation_splits,
    label_ends_from_horizon,
    require_chronological,
    walk_forward_splits,
)

DAY = 86400.0
START = 1_735_689_600.0
INSTANTS = [START + index * DAY for index in range(100)]


def _policy(horizon: int, embargo_days: float = 0.0) -> PurgePolicy:
    return PurgePolicy(label_ends_from_horizon(INSTANTS, horizon), embargo_days * DAY)


# --------------------------------------------------------------------------- #
# The primitives
# --------------------------------------------------------------------------- #


def test_an_interval_is_half_open() -> None:
    interval = SplitInterval(10.0, 20.0)

    assert interval.contains(10.0)
    assert not interval.contains(20.0), "a closed upper bound would share an instant"
    assert interval.overlaps(SplitInterval(19.0, 25.0))
    assert not interval.overlaps(SplitInterval(20.0, 25.0))


def test_an_empty_interval_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="must end after it starts"):
        SplitInterval(10.0, 10.0)


def test_unordered_instants_are_refused_rather_than_sorted() -> None:
    """Sorting would hide an ordering problem upstream rather than report one."""

    with pytest.raises(ResearchValidationError, match="strictly ascending"):
        require_chronological([3.0, 1.0, 2.0])
    with pytest.raises(ResearchValidationError, match="strictly ascending"):
        require_chronological([1.0, 1.0, 2.0])
    with pytest.raises(ResearchValidationError, match="given none"):
        require_chronological([])


def test_a_fold_whose_parts_intersect_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="train/test contamination"):
        TimeSplit(
            index=0,
            train=(1.0, 2.0, 3.0),
            validation=(3.0, 4.0),
            train_span=SplitInterval(1.0, 3.5),
            validation_span=SplitInterval(3.0, 4.5),
        )


def test_a_fold_with_no_training_or_validation_instants_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="empty training set"):
        TimeSplit(0, (), (1.0,), SplitInterval(0.0, 1.0), SplitInterval(1.0, 2.0))
    with pytest.raises(ResearchValidationError, match="empty validation set"):
        TimeSplit(0, (1.0,), (), SplitInterval(0.0, 1.0), SplitInterval(1.0, 2.0))


# --------------------------------------------------------------------------- #
# Walk-forward boundaries
# --------------------------------------------------------------------------- #


def test_a_rolling_walk_forward_fold_holds_exactly_the_instants_it_should() -> None:
    report = walk_forward_splits(INSTANTS, 10, 5, 5, WindowMode.ROLLING)
    first, second = report.folds[0], report.folds[1]

    assert first.train == tuple(INSTANTS[0:10])
    assert first.validation == tuple(INSTANTS[10:15])
    assert first.test == tuple(INSTANTS[15:20])

    # The step defaults to test_size, so the second fold's test window begins
    # exactly where the first one's ended: the tested region is partitioned.
    assert second.train == tuple(INSTANTS[5:15])
    assert second.test == tuple(INSTANTS[20:25])


def test_an_expanding_walk_forward_keeps_everything_from_the_start() -> None:
    report = walk_forward_splits(INSTANTS, 10, 5, 5, WindowMode.EXPANDING)

    assert report.folds[0].train == tuple(INSTANTS[0:10])
    assert report.folds[1].train == tuple(INSTANTS[0:15])
    assert report.folds[2].train == tuple(INSTANTS[0:20])


def test_a_rolling_training_window_stays_the_same_length() -> None:
    report = walk_forward_splits(INSTANTS, 10, 5, 5, WindowMode.ROLLING)
    assert {len(fold.train) for fold in report.folds} == {10}


def test_every_walk_forward_fold_is_forward_only() -> None:
    report = walk_forward_splits(INSTANTS, 10, 5, 5, WindowMode.EXPANDING)

    assert report.is_forward_only
    for fold in report.folds:
        assert max(fold.train) < min(fold.validation) < min(fold.test)


def test_generation_stops_rather_than_producing_a_short_final_fold() -> None:
    """A fold with fewer observations is not comparable with its neighbours."""

    report = walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING)

    assert len(report) == 5
    assert all(len(fold.validation) == 10 and len(fold.test) == 10 for fold in report.folds)


def test_a_series_too_short_for_one_fold_is_refused_with_the_arithmetic() -> None:
    with pytest.raises(ResearchValidationError, match="needs 60 instants and the series has 50"):
        walk_forward_splits(INSTANTS[:50], 40, 10, 10, WindowMode.ROLLING)


def test_a_custom_step_is_recorded_in_the_scheme() -> None:
    report = walk_forward_splits(INSTANTS, 10, 5, 5, WindowMode.ROLLING, step=1)

    assert "step=1" in report.scheme
    assert report.folds[1].train == tuple(INSTANTS[1:11])


@pytest.mark.parametrize("size", ["train_size", "validation_size", "test_size"])
def test_a_non_positive_window_is_refused(size: str) -> None:
    sizes = {"train_size": 10, "validation_size": 5, "test_size": 5}
    sizes[size] = 0
    with pytest.raises(ResearchValidationError, match=f"{size} must be at least 1"):
        walk_forward_splits(
            INSTANTS,
            sizes["train_size"],
            sizes["validation_size"],
            sizes["test_size"],
            WindowMode.ROLLING,
        )


# --------------------------------------------------------------------------- #
# Purging
# --------------------------------------------------------------------------- #


def test_label_ends_read_the_actual_series_rather_than_assuming_even_spacing() -> None:
    """The distinction between purging and date subtraction, asserted directly."""

    uneven = [0.0, 1.0, 2.0, 100.0, 101.0]
    ends = label_ends_from_horizon(uneven, 2)

    assert ends[0.0] == 2.0
    assert ends[1.0] == 100.0, "two observations later, not two seconds later"
    assert ends[2.0] == 101.0


def test_the_tail_of_a_series_has_an_unrealized_label() -> None:
    ends = label_ends_from_horizon([0.0, 1.0, 2.0], 2)

    assert ends[1.0] == math.inf
    assert ends[2.0] == math.inf


def test_a_zero_horizon_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="needs no purging"):
        label_ends_from_horizon(INSTANTS, 0)


def test_a_purge_policy_cannot_be_built_without_a_horizon() -> None:
    with pytest.raises(ResearchValidationError, match="no default horizon"):
        PurgePolicy({})


def test_purging_removes_exactly_the_instants_whose_labels_overlap() -> None:
    """With a horizon of 5, the last 5 training instants must go, and no more."""

    train = tuple(INSTANTS[0:20])
    validation = SplitInterval(INSTANTS[20], math.nextafter(INSTANTS[24], math.inf))

    result = apply_purge_and_embargo(train, validation, _policy(5))

    assert result.purged == tuple(INSTANTS[15:20])
    assert result.kept == tuple(INSTANTS[0:15])
    assert result.embargoed == ()


def test_the_gap_purging_leaves_exceeds_the_label_horizon() -> None:
    """The property purging exists to create, stated as a property."""

    horizon = 5
    report = walk_forward_splits(
        INSTANTS, 40, 10, 10, WindowMode.EXPANDING, policy=_policy(horizon)
    )

    for fold in report.folds:
        last_train = INSTANTS.index(fold.train[-1])
        first_validation = INSTANTS.index(fold.validation[0])
        assert first_validation - last_train > horizon


def test_no_policy_means_nothing_is_removed_and_the_report_says_so() -> None:
    result = apply_purge_and_embargo(
        tuple(INSTANTS[:5]), SplitInterval(INSTANTS[5], INSTANTS[9]), None
    )

    assert result.kept == tuple(INSTANTS[:5])
    assert result.purged == ()


def test_an_instant_the_policy_does_not_cover_is_refused() -> None:
    policy = PurgePolicy(label_ends_from_horizon(INSTANTS[:10], 2))

    with pytest.raises(ResearchValidationError, match="no label end for the instant"):
        apply_purge_and_embargo((INSTANTS[50],), SplitInterval(INSTANTS[60], INSTANTS[70]), policy)


def test_walk_forward_also_purges_validation_against_the_test_window() -> None:
    """The leak that survives every check that only looks at the training set."""

    report = walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING, policy=_policy(5))

    assert report.total_validation_purged > 0
    for fold in report.folds:
        assert fold.validation_purged, "each fold must purge its own validation tail"
        assert not set(fold.validation) & set(fold.validation_purged)
        last_validation = INSTANTS.index(fold.validation[-1])
        first_test = INSTANTS.index(fold.test[0])
        assert first_test - last_validation > 5


def test_purging_that_empties_a_fold_is_refused_rather_than_dropped() -> None:
    """Silently dropping it would report a different experiment from the one asked for."""

    with pytest.raises(ResearchValidationError, match="no training instants left"):
        walk_forward_splits(INSTANTS, 3, 10, 10, WindowMode.ROLLING, policy=_policy(20))


# --------------------------------------------------------------------------- #
# Cross-validation schemes
# --------------------------------------------------------------------------- #


def test_rolling_cv_gives_every_fold_a_training_block_of_the_same_length() -> None:
    report = cross_validation_splits(INSTANTS, 4, CVMethod.ROLLING)

    assert len(report) == 4
    assert {len(fold.train) for fold in report.folds} == {20}
    assert report.folds[0].train == tuple(INSTANTS[0:20])
    assert report.folds[0].validation == tuple(INSTANTS[20:40])
    assert report.folds[1].train == tuple(INSTANTS[20:40])


def test_expanding_cv_grows_the_training_block() -> None:
    report = cross_validation_splits(INSTANTS, 4, CVMethod.EXPANDING)

    assert report.folds[0].train == tuple(INSTANTS[0:20])
    assert report.folds[1].train == tuple(INSTANTS[0:40])
    assert report.folds[3].train == tuple(INSTANTS[0:80])


def test_the_forward_schemes_are_forward_only() -> None:
    for method in (CVMethod.ROLLING, CVMethod.EXPANDING):
        report = cross_validation_splits(INSTANTS, 4, method)
        assert report.is_forward_only, method


def test_a_blocked_fold_trains_on_both_sides_of_its_validation_block() -> None:
    """What makes it a k-fold estimate, and what gives an embargo something to do."""

    report = cross_validation_splits(INSTANTS, 5, CVMethod.PURGED, _policy(2))
    middle = report.folds[2]

    assert not middle.is_forward_only
    assert any(instant < min(middle.validation) for instant in middle.train)
    assert any(instant > max(middle.validation) for instant in middle.train)


def test_every_instant_is_validated_exactly_once_across_a_blocked_scheme() -> None:
    report = cross_validation_splits(INSTANTS, 5, CVMethod.PURGED, _policy(2))

    validated = [instant for fold in report.folds for instant in fold.validation]

    assert sorted(validated) == INSTANTS
    assert len(validated) == len(set(validated))


def test_an_embargo_removes_training_instants_after_the_validation_block() -> None:
    report = cross_validation_splits(INSTANTS, 5, CVMethod.EMBARGOED, _policy(2, embargo_days=3))
    fold = report.folds[2]
    block_end = max(fold.validation)

    assert fold.embargoed, "the embargo must remove something on a blocked fold"
    assert all(block_end < instant <= block_end + 3 * DAY for instant in fold.embargoed)
    assert not any(block_end < instant <= block_end + 3 * DAY for instant in fold.train)


def test_an_embargo_on_a_forward_only_scheme_removes_nothing_and_says_so() -> None:
    """Not wrong to configure; it simply has nothing to act on, and reports zero."""

    report = cross_validation_splits(INSTANTS, 4, CVMethod.EXPANDING, _policy(2, embargo_days=3))

    assert report.total_embargoed == 0
    assert report.total_purged > 0
    assert report.embargo_seconds == 3 * DAY


def test_a_purged_method_without_a_policy_is_refused() -> None:
    """The name is a claim; an unpurged fold may not be recorded under it."""

    for method in (CVMethod.PURGED, CVMethod.EMBARGOED):
        with pytest.raises(ResearchValidationError, match="no default horizon"):
            cross_validation_splits(INSTANTS, 4, method)


def test_embargoed_without_an_embargo_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="embargo of zero removes nothing"):
        cross_validation_splits(INSTANTS, 4, CVMethod.EMBARGOED, _policy(2))


def test_purged_with_an_embargo_is_refused_as_ambiguous() -> None:
    """It would produce EMBARGOED's folds under PURGED's name."""

    with pytest.raises(ResearchValidationError, match="Ask for EMBARGOED"):
        cross_validation_splits(INSTANTS, 4, CVMethod.PURGED, _policy(2, embargo_days=3))


def test_a_single_fold_is_refused_because_that_is_a_walk_forward_split() -> None:
    with pytest.raises(ResearchValidationError, match="at least 2 folds"):
        cross_validation_splits(INSTANTS, 1, CVMethod.ROLLING)


def test_a_series_too_short_for_the_scheme_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="needs at least"):
        cross_validation_splits(INSTANTS[:5], 4, CVMethod.ROLLING)


def test_no_fold_in_any_scheme_shares_an_instant_between_its_parts() -> None:
    """The contamination check, run across every scheme this release ships."""

    reports = [
        walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING, policy=_policy(5)),
        walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.EXPANDING, policy=_policy(5)),
        cross_validation_splits(INSTANTS, 4, CVMethod.ROLLING, _policy(3)),
        cross_validation_splits(INSTANTS, 4, CVMethod.EXPANDING, _policy(3)),
        cross_validation_splits(INSTANTS, 5, CVMethod.PURGED, _policy(3)),
        cross_validation_splits(INSTANTS, 5, CVMethod.EMBARGOED, _policy(3, 2)),
    ]

    for report in reports:
        for fold in report.folds:
            assert not set(fold.train) & set(fold.validation), f"{report.scheme} fold {fold.index}"
            assert not set(fold.train) & set(fold.test), f"{report.scheme} fold {fold.index}"
            assert not set(fold.validation) & set(fold.test), f"{report.scheme} fold {fold.index}"


def test_split_generation_is_deterministic() -> None:
    first = walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING, policy=_policy(5))
    second = walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING, policy=_policy(5))

    assert first.scheme == second.scheme
    assert [fold.train for fold in first.folds] == [fold.train for fold in second.folds]
    assert [fold.purged for fold in first.folds] == [fold.purged for fold in second.folds]


def test_a_fold_describes_itself_in_one_readable_line() -> None:
    report = walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING, policy=_policy(5))
    described = report.folds[0].describe()

    assert "fold 0" in described
    assert "train=" in described and "purged=" in described and "embargoed=" in described
