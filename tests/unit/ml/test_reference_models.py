"""The core's reference models, each held to arithmetic it did not do (v4.3, ADR-0064).

* **Ridge regression** equals the exact solution of its normal equations,
  solved in rational arithmetic, with a penalty and without, with an intercept
  and without; a weight of two is the row counted twice; the seed changes
  nothing; and an undetermined or ill-conditioned design is refused.
* **Logistic regression** reaches the closed-form maximum of a two-cell
  design, satisfies its penalized score equations, refuses separated classes
  without a penalty, and raises when it does not converge within its limit.
* **Gradient-boosted trees** grow the split a hand computation chooses, with
  the largest left value as its threshold and ties to the first feature; keep
  every leaf to ``minimum_leaf`` rows; shrink by the learning rate; never raise
  the training loss; and draw a subsample from the seed alone.
* **Every hyperparameter is checked, and a capture its hyperparameters could
  not have produced is refused.**
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from fractions import Fraction
from itertools import pairwise

import pytest

from alphalab.common.exceptions import ConvergenceError
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.models import (
    BoostingLoss,
    GradientBoostedTrees,
    LinearModelFit,
    LogisticRegression,
    RidgeRegression,
    TreeEnsembleFit,
    TreeLeaf,
    TreeSplit,
)
from alphalab.ml.protocol import FeatureMatrix, TrainingSet

NAMES = ("a", "b")
ROWS = [
    (0.5, -1.25),
    (1.5, 0.25),
    (-0.75, 2.0),
    (2.25, -0.5),
    (0.125, 1.0),
    (-1.5, -2.0),
    (3.0, 0.75),
    (-2.25, 1.5),
]
LABELS = [0.75, 1.5, -0.5, 2.0, 0.25, -1.75, 2.5, -1.0]


def _ridge(penalty: float, intercept: bool = True) -> RidgeRegression:
    return RidgeRegression(penalty=penalty, intercept=intercept, maximum_condition=1e8)


def _exact_ridge(
    rows: Sequence[Sequence[float]],
    labels: list[float],
    weights: list[float],
    penalty: float,
    intercept: bool,
) -> list[Fraction]:
    """Solve ``(X'WX + penalty D) b = X'Wy`` exactly; ``D`` leaves the intercept out."""

    design = [[Fraction(1)] * intercept + [Fraction(v) for v in row] for row in rows]
    width = len(design[0])
    w = [Fraction(v) for v in weights]
    matrix = [
        [
            sum((w[i] * design[i][r] * design[i][c] for i in range(len(rows))), Fraction(0))
            for c in range(width)
        ]
        for r in range(width)
    ]
    for column in range(int(intercept), width):
        matrix[column][column] += Fraction(penalty)
    rhs = [
        sum((w[i] * design[i][r] * Fraction(labels[i]) for i in range(len(rows))), Fraction(0))
        for r in range(width)
    ]
    for column in range(width):  # Gauss-Jordan, exact
        pivot = next(r for r in range(column, width) if matrix[r][column] != 0)
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        rhs[column], rhs[pivot] = rhs[pivot], rhs[column]
        for r in range(width):
            if r != column and matrix[r][column] != 0:
                factor = matrix[r][column] / matrix[column][column]
                matrix[r] = [x - factor * y for x, y in zip(matrix[r], matrix[column], strict=True)]
                rhs[r] -= factor * rhs[column]
    return [rhs[r] / matrix[r][r] for r in range(width)]


@pytest.mark.parametrize("penalty", [0.0, 0.5, 4.0])
@pytest.mark.parametrize("intercept", [True, False])
def test_ridge_is_the_exact_solution_of_its_normal_equations(
    penalty: float, intercept: bool
) -> None:
    weights = [1.0, 2.0, 0.5, 1.0, 1.5, 1.0, 0.25, 3.0]
    fitted = _ridge(penalty, intercept).fit(TrainingSet.of(NAMES, ROWS, LABELS, weights), seed=0)
    assert fitted.fitted is not None
    exact = _exact_ridge(ROWS, LABELS, weights, penalty, intercept)
    got = [fitted.fitted.intercept] * intercept + list(fitted.fitted.coefficients)
    assert got == pytest.approx([float(v) for v in exact], rel=1e-13, abs=1e-15)
    if not intercept:
        assert fitted.fitted.intercept == 0.0


def test_a_weight_of_two_is_the_row_counted_twice() -> None:
    weighted = _ridge(0.5).fit(TrainingSet.of(NAMES, ROWS, LABELS, [2.0] + [1.0] * 7), seed=0)
    doubled = _ridge(0.5).fit(TrainingSet.of(NAMES, [ROWS[0], *ROWS], [LABELS[0], *LABELS]), seed=0)
    assert weighted.fitted is not None and doubled.fitted is not None
    assert weighted.fitted.coefficients == pytest.approx(doubled.fitted.coefficients, rel=1e-13)
    assert weighted.fitted.intercept == pytest.approx(doubled.fitted.intercept, rel=1e-13)


def test_a_ridges_prediction_is_its_linear_score_and_the_seed_changes_nothing() -> None:
    training = TrainingSet.of(NAMES, ROWS, LABELS)
    fitted = _ridge(0.5).fit(training, seed=0)
    assert fitted.capture() == _ridge(0.5).fit(training, seed=99).capture()
    assert fitted.fitted is not None
    a, (b1, b2) = fitted.fitted.intercept, fitted.fitted.coefficients
    for row, prediction in zip(ROWS, fitted.predict(FeatureMatrix.of(NAMES, ROWS)), strict=True):
        assert prediction == math.fsum([a, b1 * row[0], b2 * row[1]])


def test_what_a_ridge_cannot_fit_is_refused() -> None:
    zero = [(x, 0.0) for x, _ in ROWS]
    with pytest.raises(MLInputError, match="'b' is zero in every weighted row"):
        _ridge(0.0).fit(TrainingSet.of(NAMES, zero, LABELS), seed=0)
    penalized = _ridge(1.0).fit(TrainingSet.of(NAMES, zero, LABELS), seed=0)
    assert penalized.fitted is not None and abs(penalized.fitted.coefficients[1]) < 1e-15
    with pytest.raises(MLInputError, match="underdetermined"):
        _ridge(0.0).fit(TrainingSet.of(NAMES, ROWS[:2], LABELS[:2]), seed=0)
    collinear = [(x, 2.0 * x) for x, _ in ROWS]
    with pytest.raises(MLInputError, match="cannot be fitted as posed"):
        _ridge(0.0).fit(TrainingSet.of(NAMES, collinear, LABELS), seed=0)
    with pytest.raises(MLInputError, match="seed is an integer"):
        _ridge(0.0).fit(TrainingSet.of(NAMES, ROWS, LABELS), seed=1.0)  # type: ignore[arg-type]


def _logistic(
    penalty: float, *, iterations: int = 100, tolerance: float = 1e-12
) -> LogisticRegression:
    return LogisticRegression(
        penalty=penalty,
        intercept=True,
        tolerance=tolerance,
        max_iterations=iterations,
        maximum_condition=1e10,
    )


def test_a_two_cell_logistic_reaches_its_closed_form_maximum() -> None:
    """One binary feature: the intercept is the log-odds at 0, the slope the log odds ratio."""

    rows = [(0.0,)] * 10 + [(1.0,)] * 8
    labels = [1.0] * 3 + [0.0] * 7 + [1.0] * 6 + [0.0] * 2
    fitted = _logistic(0.0).fit(TrainingSet.of(("x",), rows, labels), seed=0)
    assert fitted.fitted is not None
    at_zero = math.log(3 / 7)
    assert fitted.fitted.intercept == pytest.approx(at_zero, rel=1e-12)
    assert fitted.fitted.coefficients[0] == pytest.approx(math.log(6 / 2) - at_zero, rel=1e-12)
    probabilities = fitted.predict(FeatureMatrix.of(("x",), [(0.0,), (1.0,)]))
    assert probabilities == pytest.approx((0.3, 0.75), rel=1e-12)


def test_a_penalized_logistic_satisfies_its_score_equations() -> None:
    labels = [1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    weights = [1.0, 2.0, 1.0, 0.5, 1.0, 1.5, 1.0, 1.0]
    penalty = 0.75
    fitted = _logistic(penalty).fit(TrainingSet.of(NAMES, ROWS, labels, weights), seed=0)
    assert fitted.fitted is not None
    probabilities = fitted.predict(FeatureMatrix.of(NAMES, ROWS))
    residuals = [w * (y - p) for w, y, p in zip(weights, labels, probabilities, strict=True)]
    assert abs(math.fsum(residuals)) < 1e-12  # the intercept's, unpenalized
    for column, coefficient in enumerate(fitted.fitted.coefficients):
        score = math.fsum(r * row[column] for r, row in zip(residuals, ROWS, strict=True))
        assert abs(score - penalty * coefficient) < 1e-12


def test_separated_classes_are_refused_without_a_penalty_and_fitted_with_one() -> None:
    rows = [(-2.0,), (-1.0,), (1.0,), (2.0,)]
    training = TrainingSet.of(("x",), rows, [0.0, 0.0, 1.0, 1.0])
    with pytest.raises(MLInputError, match="classes are separated"):
        _logistic(0.0).fit(training, seed=0)
    fitted = _logistic(1.0).fit(training, seed=0)
    assert fitted.fitted is not None and math.isfinite(fitted.fitted.coefficients[0])


def test_a_logistic_fit_short_of_its_tolerance_raises_with_what_it_reached() -> None:
    training = TrainingSet.of(NAMES, ROWS, [1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0])
    with pytest.raises(ConvergenceError) as raised:
        _logistic(0.5, iterations=1).fit(training, seed=0)
    assert raised.value.limit == 1 and raised.value.reached > 1e-12


def test_a_logistic_fit_stops_at_the_first_newton_step_within_its_tolerance() -> None:
    """And never before: each step's change is read from the refusal of a fit one step short."""

    training = TrainingSet.of(NAMES, ROWS, [1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0])
    changes: list[float] = []
    for limit in range(1, 30):
        try:
            _logistic(0.5, iterations=limit).fit(training, seed=0)
        except ConvergenceError as short:
            changes.append(short.reached)
        else:
            break
    assert len(changes) >= 4 and all(before > after for before, after in pairwise(changes))
    for step, (before, after) in enumerate(pairwise(changes), start=2):
        tolerance = math.sqrt(before * after)  # within this step's change, not the one before
        assert _logistic(0.5, iterations=step, tolerance=tolerance).fit(training, seed=0).fitted
        with pytest.raises(ConvergenceError) as raised:
            _logistic(0.5, iterations=step - 1, tolerance=tolerance).fit(training, seed=0)
        assert raised.value.reached == before


@pytest.mark.parametrize(
    ("labels", "match"),
    [
        ([1.0] * 8, "needs both classes"),
        ([0.0] * 7 + [2.0], "labels 0 and 1"),
        ([0.0] * 7 + [0.5], "labels 0 and 1"),
    ],
)
def test_a_classifier_takes_labels_zero_and_one_both_present(
    labels: list[float], match: str
) -> None:
    training = TrainingSet.of(NAMES, ROWS, labels)
    with pytest.raises(MLInputError, match=match):
        _logistic(1.0).fit(training, seed=0)
    with pytest.raises(MLInputError, match=match):
        _boosted(BoostingLoss.LOGISTIC).fit(training, seed=0)


def _boosted(
    loss: BoostingLoss = BoostingLoss.SQUARED,
    *,
    trees: int = 1,
    learning_rate: float = 1.0,
    depth: int = 1,
    minimum_leaf: int = 1,
    subsample: float = 1.0,
) -> GradientBoostedTrees:
    return GradientBoostedTrees(
        loss=loss,
        trees=trees,
        learning_rate=learning_rate,
        depth=depth,
        minimum_leaf=minimum_leaf,
        subsample=subsample,
    )


STEP_ROWS = [(1.0,), (2.0,), (3.0,), (4.0,), (5.0,), (6.0,)]
STEP_LABELS = [0.0, 0.0, 0.0, 10.0, 10.0, 10.0]


def test_a_stump_splits_where_a_hand_computation_does_at_the_largest_left_value() -> None:
    fitted = _boosted().fit(TrainingSet.of(("x",), STEP_ROWS, STEP_LABELS), seed=0)
    assert fitted.fitted == TreeEnsembleFit(
        ("x",), 5.0, (TreeSplit(0, 3.0, TreeLeaf(-5.0), TreeLeaf(5.0)),)
    )
    assert fitted.predict(FeatureMatrix.of(("x",), STEP_ROWS)) == tuple(STEP_LABELS)
    between = fitted.predict(FeatureMatrix.of(("x",), [(3.0,), (3.5,), (2.999,)]))
    assert between == (0.0, 10.0, 0.0)


def test_a_tie_between_features_goes_to_the_first() -> None:
    rows = [(x, x) for (x,) in STEP_ROWS]
    fitted = _boosted().fit(TrainingSet.of(("first", "second"), rows, STEP_LABELS), seed=0)
    assert fitted.fitted is not None
    root = fitted.fitted.trees[0]
    assert isinstance(root, TreeSplit) and root.feature == 0


def test_every_leaf_keeps_minimum_leaf_rows_and_the_learning_rate_shrinks_it() -> None:
    training = TrainingSet.of(("x",), STEP_ROWS, STEP_LABELS)
    unsplit = _boosted(minimum_leaf=4).fit(training, seed=0)
    assert unsplit.fitted is not None and unsplit.fitted.trees == (TreeLeaf(0.0),)
    uneven = _boosted(minimum_leaf=2).fit(
        TrainingSet.of(("x",), STEP_ROWS, [0.0, 0.0, 0.0, 0.0, 0.0, 12.0]), seed=0
    )
    assert uneven.fitted is not None
    root = uneven.fitted.trees[0]
    assert isinstance(root, TreeSplit) and root.threshold == 4.0, (
        "the best split keeping two rows a side"
    )
    shrunk = _boosted(learning_rate=0.5).fit(training, seed=0)
    assert shrunk.predict(FeatureMatrix.of(("x",), STEP_ROWS)) == (2.5, 2.5, 2.5, 7.5, 7.5, 7.5)


def test_more_trees_never_raise_the_training_loss() -> None:
    training = TrainingSet.of(NAMES, ROWS, LABELS)
    matrix = FeatureMatrix.of(NAMES, ROWS)
    losses = []
    for trees in range(1, 7):
        fitted = _boosted(trees=trees, learning_rate=0.5, depth=2).fit(training, seed=0)
        predictions = fitted.predict(matrix)
        losses.append(math.fsum((y - p) ** 2 for y, p in zip(LABELS, predictions, strict=True)))
    assert all(later <= earlier for earlier, later in pairwise(losses))
    assert losses[-1] < losses[0]


def test_a_logistic_ensemble_starts_from_the_log_odds_and_predicts_probabilities() -> None:
    labels = [1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    fitted = _boosted(BoostingLoss.LOGISTIC, trees=3, learning_rate=0.5, depth=2).fit(
        TrainingSet.of(NAMES, ROWS, labels), seed=0
    )
    assert fitted.fitted is not None and fitted.fitted.base == 0.0  # four of eight
    probabilities = fitted.predict(FeatureMatrix.of(NAMES, ROWS))
    assert all(0.0 < p < 1.0 for p in probabilities)
    positives = [p for p, y in zip(probabilities, labels, strict=True) if y == 1.0]
    negatives = [p for p, y in zip(probabilities, labels, strict=True) if y == 0.0]
    assert min(positives) > max(negatives)


def test_a_subsample_is_drawn_from_the_seed_alone() -> None:
    training = TrainingSet.of(NAMES, ROWS, LABELS)
    sampled = _boosted(trees=4, learning_rate=0.5, depth=2, subsample=0.5)
    assert sampled.fit(training, seed=3).capture() == sampled.fit(training, seed=3).capture()
    captures = {sampled.fit(training, seed=seed).capture() for seed in range(6)}
    assert len(captures) > 1, "every seed drew the same rows"
    whole = _boosted(trees=4, learning_rate=0.5, depth=2)
    assert whole.fit(training, seed=3).capture() == whole.fit(training, seed=4).capture()


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: _ridge(-1.0), "at least zero"),
        (lambda: _ridge(math.nan), "finite number"),
        (lambda: RidgeRegression(penalty=0.0, intercept=True, maximum_condition=0.5), "at least 1"),
        (lambda: RidgeRegression(penalty=0.0, intercept=1, maximum_condition=1e8), "True or False"),  # type: ignore[arg-type]
        (lambda: _logistic(0.0, tolerance=0.0), "positive"),
        (lambda: _logistic(0.0, iterations=0), "at least 1"),
        (lambda: _boosted(trees=0), "trees is an integer of at least 1"),
        (lambda: _boosted(learning_rate=0.0), r"learning_rate is in \(0, 1\]"),
        (lambda: _boosted(learning_rate=1.5), r"learning_rate is in \(0, 1\]"),
        (lambda: _boosted(depth=0), "depth is an integer"),
        (lambda: _boosted(minimum_leaf=0), "minimum_leaf is an integer"),
        (lambda: _boosted(subsample=0.0), r"subsample is in \(0, 1\]"),
        (lambda: replace(_boosted(), loss="squared"), "BoostingLoss"),  # type: ignore[arg-type]
        (lambda: replace(_boosted(), trees=True), "trees is an integer"),
    ],
)
def test_every_hyperparameter_is_checked(build: Callable[[], object], match: str) -> None:
    with pytest.raises(MLInputError, match=match):
        build()


def test_a_fit_its_hyperparameters_could_not_have_produced_is_refused() -> None:
    fit = LinearModelFit(NAMES, 1.0, (0.5, -0.5))
    with pytest.raises(MLInputError, match="without an intercept has none"):
        RidgeRegression(penalty=0.0, intercept=False, maximum_condition=1e8, fitted=fit)
    with pytest.raises(MLInputError, match="2 features and 1 coefficients"):
        LinearModelFit(NAMES, 0.0, (1.0,))
    ensemble = TreeEnsembleFit(("x",), 0.0, (TreeLeaf(1.0), TreeLeaf(2.0)))
    with pytest.raises(MLInputError, match="of 1 trees holds 2"):
        replace(_boosted(), fitted=ensemble)
    deep = TreeEnsembleFit(
        ("x",),
        0.0,
        (TreeSplit(0, 1.0, TreeSplit(0, 0.0, TreeLeaf(0.0), TreeLeaf(1.0)), TreeLeaf(2.0)),),
    )
    with pytest.raises(MLInputError, match="depth 1 holds a tree of depth 2"):
        replace(_boosted(), fitted=deep)


def test_a_captured_tree_is_read_strictly() -> None:
    fitted = _boosted(trees=1, depth=1).fit(TrainingSet.of(("x",), STEP_ROWS, STEP_LABELS), seed=0)
    document = json.loads(fitted.capture())

    def restored_with(tree: object) -> GradientBoostedTrees:
        changed = {**document, "state": {**document["state"], "trees": [tree]}}
        return GradientBoostedTrees.restore(
            json.dumps(changed, sort_keys=True, separators=(",", ":")).encode("ascii")
        )

    split = document["state"]["trees"][0]
    with pytest.raises(MLInputError, match="names feature 1 of 1"):
        restored_with({**split, "feature": 1})
    with pytest.raises(MLInputError, match="not a finite float"):
        restored_with({**split, "threshold": 3})
    with pytest.raises(MLInputError, match="holds"):
        restored_with({**split, "extra": 0.0})
    with pytest.raises(MLInputError, match="not a mapping"):
        restored_with([0.0])
