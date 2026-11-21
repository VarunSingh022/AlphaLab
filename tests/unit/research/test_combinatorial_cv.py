"""Combinatorial purged cross-validation's splits and paths (v4.3, ADR-0073).

López de Prado's construction, checked by hand on a small series and by its
counts on a larger one: ``C(N, k)`` splits in lexicographic order, each group
tested ``C(N - 1, k - 1)`` times, and ``k / N * C(N, k)`` paths that each cover
every group once. Purging and the embargo act against every test group.
"""

from __future__ import annotations

import math

import pytest

from alphalab.research import ResearchValidationError
from alphalab.research.combinatorial_cv import combinatorial_purged_splits
from alphalab.research.purging import PurgePolicy, label_ends_from_horizon

EIGHT = tuple(float(i) for i in range(8))


def _policy(instants: tuple[float, ...], horizon: int = 1, embargo: float = 0.0) -> PurgePolicy:
    return PurgePolicy(label_ends_from_horizon(instants, horizon), embargo_seconds=embargo)


def test_four_groups_two_tested_make_six_splits_and_three_paths_by_hand() -> None:
    report = combinatorial_purged_splits(EIGHT, 4, 2, _policy(EIGHT))

    assert report.groups == ((0.0, 1.0), (2.0, 3.0), (4.0, 5.0), (6.0, 7.0))
    assert [split.test_groups for split in report.splits] == [
        (0, 1),
        (0, 2),
        (0, 3),
        (1, 2),
        (1, 3),
        (2, 3),
    ]
    # Group 0 is tested by splits 0, 1, 2; group 1 by 0, 3, 4; 2 by 1, 3, 5; 3 by 2, 4, 5.
    assert report.paths == ((0, 0, 1, 2), (1, 3, 3, 4), (2, 4, 5, 5))
    assert report.scheme == "combinatorial_purged(groups=4,test_groups=2,embargo=0.0)"


def test_a_split_trains_on_the_other_groups_less_what_reaches_into_a_test_group() -> None:
    report = combinatorial_purged_splits(EIGHT, 4, 2, _policy(EIGHT, horizon=1))
    split = report.splits[1]  # test groups 0 and 2: instants 0, 1, 4, 5

    assert split.test == (0.0, 1.0, 4.0, 5.0)
    # Instant 3's label ends at 4, inside group 2: purged. 6 and 7 are after
    # every test group, with no embargo, and 2's label ends at 3, before group 2.
    assert split.purged == (3.0,)
    assert split.train == (2.0, 6.0, 7.0)
    assert set(split.train).isdisjoint(split.test)


def test_the_embargo_removes_what_follows_each_test_group() -> None:
    report = combinatorial_purged_splits(EIGHT, 4, 2, _policy(EIGHT, horizon=1, embargo=1.5))
    split = report.splits[1]  # test groups 0 and 2

    # 2.0 follows group 0 (ending at 1.0) within 1.5; 6.0 follows group 2 (ending at 5.0).
    assert split.embargoed == (2.0, 6.0)
    assert split.train == (7.0,)


@pytest.mark.parametrize(("groups", "tested"), [(6, 2), (6, 3), (10, 2), (5, 1)])
def test_every_group_is_tested_c_n_minus_1_k_minus_1_times_and_every_path_covers_all(
    groups: int, tested: int
) -> None:
    instants = tuple(float(i) for i in range(groups * 7 + 3))
    report = combinatorial_purged_splits(instants, groups, tested, _policy(instants))

    assert len(report.splits) == math.comb(groups, tested)
    assert len(report.paths) == math.comb(groups - 1, tested - 1)
    assert len(report.paths) * groups == tested * math.comb(groups, tested)
    for path in report.paths:
        assert len(path) == groups
        for group, index in enumerate(path):
            assert group in report.splits[index].test_groups
    for group in range(groups):
        tested_by = [p[group] for p in report.paths]
        assert len(set(tested_by)) == len(tested_by), "a path takes each test occurrence once"
    assert sorted(s for block in report.groups for s in block) == list(instants)


@pytest.mark.parametrize(
    ("groups", "tested", "match"),
    [
        (1, 1, "groups must be an integer of at least 2"),
        (True, 1, "groups must be an integer of at least 2"),
        (4, 0, "test_groups must be an integer from 1 to 3"),
        (4, 4, "test_groups must be an integer from 1 to 3"),
        (5, 2, "5 groups need at least 10 instants"),
    ],
)
def test_a_scheme_states_its_groups_and_what_it_tests(groups: int, tested: int, match: str) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        combinatorial_purged_splits(EIGHT, groups, tested, _policy(EIGHT))


def test_the_name_is_a_claim_so_a_purge_policy_is_required_and_must_leave_a_training_set() -> None:
    with pytest.raises(ResearchValidationError, match="no PurgePolicy was given"):
        combinatorial_purged_splits(EIGHT, 4, 2, None)  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="no training instants left"):
        combinatorial_purged_splits(EIGHT, 4, 3, _policy(EIGHT, horizon=1, embargo=10.0))
