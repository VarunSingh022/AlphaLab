"""Validation designs: what each trial is fitted on and scored on (v4.3, ADR-0073).

The designs state every choice, draw their folds from research's splitters over
the dataset's instants before the lockbox, and give each trial a window: what it
may fit on, what it is scored on, and where its run starts -- a declared warm-up
before the scored window, run and never scored. A full-sample experiment's
identities are ADR-0072's.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from alphalab.lifecycle.designs import (
    REPORT,
    SEARCH,
    SELECT,
    TEST,
    VALIDATE,
    CombinatorialPurged,
    CrossValidated,
    ExperimentError,
    FullSample,
    Lockbox,
    PurgeSpec,
    WalkForward,
    report_window,
    windows,
)
from alphalab.research.selection import SelectionMeasure
from alphalab.research.time_series_cv import CVMethod
from alphalab.research.walk_forward import WindowMode
from alphalab.research.walk_forward_optimization import Refit, _render, _seed
from tests.integration.experiment_workload import spec

INSTANTS = tuple(float(t) for t in range(2, 62))  # the reference dataset's 60 instants


def _walk_forward(**changed: Any) -> WalkForward:
    fields: dict[str, Any] = {
        "train": 20,
        "validation": 10,
        "test": 10,
        "mode": WindowMode.ROLLING,
        "step": None,
        "warm_up": 5,
        "refit": Refit.TRAIN,
        "measure": SelectionMeasure.MEAN,
        "purge": None,
        "lockbox": None,
    }
    fields.update(changed)
    return WalkForward(**fields)


def test_a_full_sample_names_one_window_the_whole_sample() -> None:
    assert windows(FullSample(None), INSTANTS) == ((0, SEARCH, None),)


def test_walk_forward_scores_each_folds_validation_after_a_warm_up_it_never_scores() -> None:
    named = windows(_walk_forward(), INSTANTS)

    assert [(fold, phase) for fold, phase, _ in named] == [(0, SELECT), (1, SELECT), (2, SELECT)]
    first = named[0][2]
    assert first is not None
    assert first.fit == INSTANTS[:20]
    assert first.evaluate == INSTANTS[20:30]
    assert first.start == INSTANTS[15], "five instants of warm-up before the scored window"
    tested = report_window(_walk_forward(), INSTANTS, 0)
    assert tested.evaluate == INSTANTS[30:40] and tested.fit == INSTANTS[:20]
    refitted = report_window(_walk_forward(refit=Refit.TRAIN_AND_VALIDATION), INSTANTS, 0)
    assert refitted.fit == INSTANTS[:30]


def test_the_lockbox_is_held_back_before_any_fold_is_drawn() -> None:
    held = _walk_forward(lockbox=Lockbox(42.0, None))  # instants 2..41: 40 of them

    named = windows(held, INSTANTS)
    assert [fold for fold, _, _ in named] == [0]
    assert max(window.evaluate[-1] for _, _, window in named if window) < 42.0
    assert report_window(held, INSTANTS, 0).evaluate[-1] < 42.0


def test_a_blocked_fold_with_nothing_before_it_takes_its_warm_up_from_itself() -> None:
    design = CrossValidated(
        folds=3,
        method=CVMethod.PURGED,
        warm_up=4,
        measure=SelectionMeasure.MEAN,
        purge=PurgeSpec(1, 0.0),
        lockbox=None,
    )

    named = windows(design, INSTANTS)
    assert [(fold, phase) for fold, phase, _ in named] == [
        (0, VALIDATE),
        (1, VALIDATE),
        (2, VALIDATE),
    ]
    first, second = named[0][2], named[1][2]
    assert first is not None and second is not None
    assert first.start == INSTANTS[0]
    assert first.evaluate == INSTANTS[4:20], "the block's first four instants warm it up"
    assert second.evaluate == INSTANTS[20:40] and second.start == INSTANTS[16]


def test_a_designs_purge_and_embargo_reach_every_trials_fit_window() -> None:
    """A horizon purges what precedes a scored block; the embargo, what follows it (ADR-0073).

    Two of M5's leak mutants: a purge boundary off by one, and the embargo off.
    """

    cv = CrossValidated(3, CVMethod.EMBARGOED, 1, SelectionMeasure.MEAN, PurgeSpec(1, 1.5), None)
    middle = windows(cv, INSTANTS)[1][2]
    assert middle is not None and middle.evaluate == INSTANTS[20:40]
    assert middle.fit == INSTANTS[:19] + INSTANTS[41:], "21.0 purged, 42.0 embargoed"

    cpcv = CombinatorialPurged(4, 2, 0, SelectionMeasure.MEAN, PurgeSpec(1, 1.5), None)
    named = {(fold, phase): window for fold, phase, window in windows(cpcv, INSTANTS)}
    first = named[0, f"{TEST}:0"]
    assert first is not None
    assert first.fit == INSTANTS[31:], "32.0 follows group 1 within the embargo"
    inner = named[3, f"{TEST}:1"]
    assert inner is not None
    assert inner.fit == INSTANTS[:14] + INSTANTS[46:], (
        "16.0's label ends at 17.0, group 1's first instant: purged; 47.0 follows group 2"
    )


def test_cpcv_names_every_group_of_every_split_as_a_trial_of_its_own() -> None:
    design = CombinatorialPurged(
        groups=4,
        test_groups=2,
        warm_up=0,
        measure=SelectionMeasure.MEAN,
        purge=PurgeSpec(1, 0.0),
        lockbox=None,
    )

    named = windows(design, INSTANTS)
    assert len(named) == 6 * 2
    assert named[0][:2] == (0, f"{TEST}:0") and named[1][:2] == (0, f"{TEST}:1")
    assert named[-1][:2] == (5, f"{TEST}:3")
    group_three = named[-1][2]
    assert group_three is not None and group_three.evaluate == INSTANTS[45:]


@pytest.mark.parametrize(
    ("make", "match"),
    [
        (lambda: _walk_forward(train=0), "WalkForward.train must be an integer of at least 1"),
        (lambda: _walk_forward(warm_up=-1), "WalkForward.warm_up must be an integer of at least 0"),
        (lambda: _walk_forward(step=True), "WalkForward.step must be an integer of at least 1"),
        (lambda: _walk_forward(mode="rolling"), "WalkForward.mode must be a WindowMode"),
        (lambda: _walk_forward(lockbox=42.0), "WalkForward.lockbox must be a Lockbox"),
        (lambda: PurgeSpec(0, 0.0), "PurgeSpec.horizon must be an integer of at least 1"),
        (lambda: PurgeSpec(1, -1.0), "PurgeSpec.embargo_seconds must be a finite"),
        (
            lambda: CombinatorialPurged(4, 4, 0, SelectionMeasure.MEAN, PurgeSpec(1, 0.0), None),
            "test_groups must be below groups",
        ),
        (
            lambda: CrossValidated(1, CVMethod.ROLLING, 0, SelectionMeasure.MEAN, None, None),
            "CrossValidated.folds must be an integer of at least 2",
        ),
    ],
)
def test_a_design_states_every_choice(make: Any, match: str) -> None:
    with pytest.raises(ExperimentError, match=match):
        make()


def test_a_design_that_cannot_split_the_sample_says_why() -> None:
    with pytest.raises(ExperimentError, match="cannot split the sample"):
        windows(_walk_forward(train=50), INSTANTS)
    with pytest.raises(ExperimentError, match="cannot split the sample"):
        windows(CrossValidated(3, CVMethod.PURGED, 0, SelectionMeasure.MEAN, None, None), INSTANTS)
    with pytest.raises(ExperimentError, match="leaves 1 to score"):
        windows(_walk_forward(validation=2, warm_up=21), INSTANTS)


def test_a_designed_experiment_names_every_candidate_in_every_window_and_its_identity_moves() -> (
    None
):
    designed = replace(spec(), validation=_walk_forward())
    trials = designed.trials()
    candidates = len(designed.space.candidates)

    assert len(trials) == 3 * candidates
    assert {trial.phase for trial in trials} == {SELECT}
    assert all(trial.window is not None for trial in trials)
    assert len({trial.trial_id for trial in trials}) == len(trials)
    second = trials[candidates]
    assert second.fold == 1
    assert second.trial_seed == _seed(designed.seed, 1, SELECT, _render(second.candidate))
    assert designed.experiment_id != spec().experiment_id
    assert replace(designed, validation=_walk_forward(warm_up=6)).experiment_id != (
        designed.experiment_id
    )
    assert REPORT not in {trial.phase for trial in trials}, "a test window waits on a selection"
