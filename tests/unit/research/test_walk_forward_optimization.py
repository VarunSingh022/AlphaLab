"""Walk-forward optimization (FEA-003): selection never sees its test window.

The central property is checked by brute force rather than by reading the
code: every call the harness makes is recorded, and no call made while a fold
is selecting holds an instant of that fold's test window or anything after it;
then the data inside each fold's test window is replaced by noise, and every
selection up to and including that fold must be unchanged. The selections and
scores themselves are recomputed by an independent loop.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

import pytest

from alphalab.common.types import ParamValue
from alphalab.factor_library import FeatureDefinition, FeatureField, FeatureKind
from alphalab.research import (
    ParameterSpace,
    Refit,
    ResearchStudy,
    ResearchValidationError,
    WalkForwardDesign,
    WindowMode,
    cross_validation_splits,
    walk_forward_optimize,
    walk_forward_splits,
)
from alphalab.research.time_series_cv import CVMethod

MOMENTUM = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)
INSTANTS = tuple(float(1_700_000_000 + 60 * index) for index in range(120))
SPLITS = walk_forward_splits(INSTANTS, 40, 15, 10, WindowMode.ROLLING)
SPACE = ParameterSpace.grid({"lookback": [2, 5, 10], "scale": [0.5, 1.0]})
DESIGN = WalkForwardDesign(SPACE, Refit.TRAIN)


def _study(
    design: WalkForwardDesign = DESIGN, splits: str = SPLITS.scheme, seed: int | None = 11
) -> ResearchStudy:
    return ResearchStudy(
        study_name="wfo",
        dataset_version="panel@abc123",
        universe=("AAA",),
        features=(MOMENTUM,),
        splits=splits,
        seed=seed,
        inputs={"walk_forward": design.design_id},
    )


def _series(seed: int) -> dict[float, float]:
    rng = random.Random(seed)
    return {instant: rng.gauss(0.0, 1.0) for instant in INSTANTS}


class Recorder:
    """An objective over a series that records every call it receives."""

    def __init__(self, series: Mapping[float, float]) -> None:
        self.series = series
        self.calls: list[tuple[tuple[float, ...], tuple[float, ...], int]] = []

    def __call__(
        self,
        parameters: Mapping[str, ParamValue],
        fit: tuple[float, ...],
        evaluate: tuple[float, ...],
        seed: int,
    ) -> float:
        self.calls.append((fit, evaluate, seed))
        lookback = int(parameters["lookback"])
        scale = float(parameters["scale"])
        # "Fit": the mean of the last `lookback` fitted values; "score": how
        # well that level times the scale predicts the evaluated values.
        level = math.fsum(self.series[t] for t in fit[-lookback:]) / lookback
        return -math.fsum((self.series[t] - scale * level) ** 2 for t in evaluate) / len(evaluate)


def test_no_selection_call_holds_an_instant_of_its_test_window_or_later() -> None:
    recorder = Recorder(_series(1))
    walk_forward_optimize(_study(), DESIGN, recorder, SPLITS, produced_at=0.0)

    per_fold = len(SPACE) + 1
    assert len(recorder.calls) == len(SPLITS.folds) * per_fold
    for fold in SPLITS.folds:
        calls = recorder.calls[fold.index * per_fold : (fold.index + 1) * per_fold]
        selecting, reporting = calls[:-1], calls[-1]
        first_test = min(fold.test)
        for fit, evaluate, _ in selecting:
            assert fit == fold.train
            assert evaluate == fold.validation
            assert max((*fit, *evaluate)) < first_test
        assert reporting[0] == fold.train
        assert reporting[1] == fold.test


@pytest.mark.parametrize("fold_index", range(len(SPLITS.folds)))
def test_noise_in_a_test_window_changes_no_selection_up_to_that_fold(fold_index: int) -> None:
    clean = _series(2)
    baseline = walk_forward_optimize(_study(), DESIGN, Recorder(clean), SPLITS, produced_at=0.0)
    rng = random.Random(100 + fold_index)
    noisy = dict(clean)
    for instant in SPLITS.folds[fold_index].test:
        noisy[instant] = rng.gauss(0.0, 50.0)
    perturbed = walk_forward_optimize(_study(), DESIGN, Recorder(noisy), SPLITS, produced_at=0.0)

    for before, after in zip(
        baseline.folds[: fold_index + 1], perturbed.folds[: fold_index + 1], strict=True
    ):
        assert after.selected == before.selected
        assert after.surface.scores == before.surface.scores
    # The noise did land somewhere: this fold's test score moved.
    assert perturbed.folds[fold_index].test_score != baseline.folds[fold_index].test_score


@pytest.mark.parametrize("refit", list(Refit))
def test_the_selections_and_scores_are_what_a_plain_loop_computes(refit: Refit) -> None:
    series = _series(3)
    design = WalkForwardDesign(SPACE, refit)
    outcome = walk_forward_optimize(
        _study(design), design, Recorder(series), SPLITS, produced_at=0.0
    )
    score = Recorder(series)

    for fold, selection in zip(SPLITS.folds, outcome.folds, strict=True):
        surface = [
            score(candidate, fold.train, fold.validation, 0) for candidate in SPACE.candidates
        ]
        best = max(range(len(surface)), key=lambda index: surface[index])
        fit = fold.train if refit is Refit.TRAIN else (*fold.train, *fold.validation)
        assert selection.position == best
        assert selection.selected == SPACE.rendered[best]
        assert selection.validation_score == surface[best]
        assert selection.test_score == score(SPACE.candidates[best], fit, fold.test, 0)
        assert list(selection.surface.scores.values()) == surface
    metrics = outcome.result.metrics
    assert metrics["wfo.trials"] == len(SPLITS.folds) * len(SPACE) == outcome.trials
    assert metrics["wfo.test.mean"] == pytest.approx(
        math.fsum(outcome.out_of_sample) / len(outcome.folds), rel=1e-15
    )


def test_ties_go_to_the_first_candidate_in_the_spaces_order() -> None:
    outcome = walk_forward_optimize(
        _study(), DESIGN, lambda parameters, fit, evaluate, seed: 1.0, SPLITS, produced_at=0.0
    )

    assert {fold.position for fold in outcome.folds} == {0}
    assert "wfo.degradation" in outcome.result.metrics


def test_the_objective_is_seeded_by_fold_phase_and_candidate_not_by_call_order() -> None:
    first = Recorder(_series(4))
    walk_forward_optimize(_study(), DESIGN, first, SPLITS, produced_at=0.0)
    reordered = ParameterSpace.of(list(reversed(SPACE.candidates)))
    design = WalkForwardDesign(reordered, Refit.TRAIN)
    second = Recorder(_series(4))
    walk_forward_optimize(_study(design), design, second, SPLITS, produced_at=0.0)
    other_seed = Recorder(_series(4))
    walk_forward_optimize(_study(seed=12), DESIGN, other_seed, SPLITS, produced_at=0.0)

    seeds = {call[2] for call in first.calls}
    assert len(seeds) == len(first.calls)  # distinct per fold, phase and candidate
    selection_seeds = {c[2] for c in first.calls if c[1] != SPLITS.folds[0].test}
    assert selection_seeds <= {c[2] for c in second.calls}
    assert seeds.isdisjoint({call[2] for call in other_seed.calls})


def test_the_result_identity_is_reproducible_and_covers_the_selections() -> None:
    one = walk_forward_optimize(_study(), DESIGN, Recorder(_series(5)), SPLITS, produced_at=1.0)
    two = walk_forward_optimize(_study(), DESIGN, Recorder(_series(5)), SPLITS, produced_at=2.0)
    other = walk_forward_optimize(_study(), DESIGN, Recorder(_series(6)), SPLITS, produced_at=1.0)

    assert one.result.result_id == two.result.result_id
    assert one.result.verify()
    assert one.result.splits is SPLITS
    assert other.result.result_id != one.result.result_id
    for fold in one.folds:
        assert one.result.metrics[f"wfo.fold{fold.fold}.selected"] == float(fold.position)


def test_the_study_must_describe_the_experiment_run() -> None:
    recorder = Recorder(_series(7))
    with pytest.raises(ResearchValidationError, match="split its study names"):
        walk_forward_optimize(_study(splits="other"), DESIGN, recorder, SPLITS, produced_at=0.0)
    with pytest.raises(ResearchValidationError, match="records no seed"):
        walk_forward_optimize(_study(seed=None), DESIGN, recorder, SPLITS, produced_at=0.0)
    refitted = WalkForwardDesign(SPACE, Refit.TRAIN_AND_VALIDATION)
    with pytest.raises(ResearchValidationError, match="walk_forward"):
        walk_forward_optimize(_study(), refitted, recorder, SPLITS, produced_at=0.0)
    assert refitted.design_id != DESIGN.design_id


def test_a_scheme_without_test_windows_is_refused() -> None:
    folds = cross_validation_splits(INSTANTS, 4, CVMethod.ROLLING)
    with pytest.raises(ResearchValidationError, match="no test window"):
        walk_forward_optimize(
            _study(splits=folds.scheme), DESIGN, Recorder(_series(8)), folds, produced_at=0.0
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "high", True])
def test_a_score_that_cannot_be_ranked_is_refused(bad: object) -> None:
    with pytest.raises(ResearchValidationError, match="Fold 0"):
        walk_forward_optimize(
            _study(),
            DESIGN,
            lambda parameters, fit, evaluate, seed: bad,  # type: ignore[arg-type,return-value]
            SPLITS,
            produced_at=0.0,
        )


def test_a_parameter_space_is_a_finite_set_of_same_shaped_candidates() -> None:
    assert SPACE.rendered[:2] == ("lookback=2,scale=0.5", "lookback=2,scale=1.0")
    assert len(SPACE) == 6
    with pytest.raises(ResearchValidationError, match="at least one"):
        ParameterSpace.of([])
    with pytest.raises(ResearchValidationError, match="same parameters"):
        ParameterSpace.of([{"a": 1}, {"b": 1}])
    with pytest.raises(ResearchValidationError, match="repeats"):
        ParameterSpace.of([{"a": 1}, {"a": 1}])
    with pytest.raises(ResearchValidationError, match="finite"):
        ParameterSpace.of([{"a": float("nan")}])
    with pytest.raises(ResearchValidationError, match="no value"):
        ParameterSpace.grid({"a": []})
    assert (
        ParameterSpace.grid({"b": [1], "a": [2]}).space_id
        == ParameterSpace.of([{"a": 2, "b": 1}]).space_id
    )
    assert SPACE.space_id != ParameterSpace.of(list(reversed(SPACE.candidates))).space_id
