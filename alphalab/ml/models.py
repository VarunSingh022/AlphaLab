"""The core's reference models, each a :class:`~alphalab.ml.protocol.Model` (v4.3, ADR-0064).

Three models in pure Python, each fitted purely and captured as canonical JSON
(:func:`~alphalab.ml.protocol.canonical_capture`):

* :class:`RidgeRegression` -- least squares by Householder QR with a ridge
  penalty, class 2 (ADR-0058): every operation is correctly rounded, so a
  model fitted on any IEEE host is the same bytes.
* :class:`LogisticRegression` -- the penalized likelihood by Newton's method,
  each step a weighted least-squares solve. It passes through ``exp``, class 3:
  its bytes are its own host's, held across hosts by a tolerance.
* :class:`GradientBoostedTrees` -- gradient boosting on regression trees, the
  protocol's oracle for what an adapter's boosting model must satisfy. With
  the squared loss it is class 2; with the logistic loss, class 3.

Every choice is a stated hyperparameter, with no default (ADR-0058 section 2),
and the seed a fit is given is recorded by the caller (:func:`train_model`).
Only a tree ensemble that subsamples draws anything, and it draws from the
seed by SHA-256, the same on every host and interpreter.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Self

from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError
from alphalab.common.linalg import least_squares
from alphalab.common.types import ParamValue
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.protocol import (
    Capture,
    FeatureMatrix,
    TrainingSet,
    canonical_capture,
    read_capture,
    restored,
)

__all__ = [
    "GRADIENT_BOOSTED_TREES_KIND",
    "LOGISTIC_KIND",
    "RIDGE_KIND",
    "BoostingLoss",
    "GradientBoostedTrees",
    "LinearModelFit",
    "LogisticRegression",
    "RidgeRegression",
    "TreeEnsembleFit",
    "TreeLeaf",
    "TreeSplit",
]

#: :class:`RidgeRegression`'s kind.
RIDGE_KIND: Final = "alphalab:ridge"

#: :class:`LogisticRegression`'s kind.
LOGISTIC_KIND: Final = "alphalab:logistic"

#: :class:`GradientBoostedTrees`' kind.
GRADIENT_BOOSTED_TREES_KIND: Final = "alphalab:gradient-boosted-trees"


# -- shared checks ------------------------------------------------------------


def _seed(seed: object) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise MLInputError(f"A seed is an integer, got {seed!r}.")
    return seed


def _real(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MLInputError(f"{name} is a number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise MLInputError(f"{name} is a finite number, got {value!r}.")
    return number


def _count(value: object, name: str, least: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise MLInputError(f"{name} is an integer of at least {least}, got {value!r}.")
    return value


def _flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise MLInputError(f"{name} is True or False, got {value!r}.")
    return value


def _binary(training: TrainingSet, kind: str) -> None:
    """Refuse labels that are not 0 and 1, both present."""

    for index, label in enumerate(training.labels):
        if label not in (0.0, 1.0):
            raise MLInputError(f"A {kind} classifies labels 0 and 1; label {index} is {label!r}.")
    present = set(training.labels)
    if present != {0.0, 1.0}:
        raise MLInputError(
            f"Every label is {present.pop()!r}: a {kind} needs both classes, or the "
            "probability of the other is not estimable."
        )


def _unfitted(kind: str) -> MLInputError:
    return MLInputError(f"This {kind} is not fitted: fit it, or restore a fitted one.")


def _matched(features: object, fitted: tuple[str, ...], kind: str) -> FeatureMatrix:
    if not isinstance(features, FeatureMatrix):
        raise MLInputError(f"A {kind} predicts from a FeatureMatrix, got {features!r}.")
    if features.names != fitted:
        raise MLInputError(
            f"The {kind} was fitted on {list(fitted)}, in that order; the rows name "
            f"{list(features.names)}."
        )
    return features


def _float(value: object, where: str) -> float:
    """A captured float: a JSON number written as a float, never an integer."""

    if not isinstance(value, float) or not math.isfinite(value):
        raise MLInputError(f"The capture's {where} is {value!r}, not a finite float.")
    return value


def _floats(values: object, where: str) -> tuple[float, ...]:
    if not isinstance(values, list):
        raise MLInputError(f"The capture's {where} is not a list.")
    return tuple(_float(value, where) for value in values)


def _hyperparameters(capture: Capture, names: Sequence[str]) -> dict[str, ParamValue]:
    if sorted(capture.hyperparameters) != sorted(names):
        raise MLInputError(
            f"A {capture.model_kind} states {sorted(names)}; the capture states "
            f"{sorted(capture.hyperparameters)}."
        )
    return dict(capture.hyperparameters)


# -- linear models ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinearModelFit:
    """A fitted linear predictor: ``intercept + coefficients . x``.

    Attributes:
        features: The feature names it was fitted on, in coefficient order.
        intercept: The intercept; ``0.0`` for a model fitted without one.
        coefficients: One per feature.
    """

    features: tuple[str, ...]
    intercept: float
    coefficients: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.coefficients) != len(self.features):
            raise MLInputError(
                f"A linear fit has {len(self.features)} features and "
                f"{len(self.coefficients)} coefficients."
            )

    def score(self, row: Sequence[float]) -> float:
        """``intercept + coefficients . row``, exactly rounded once (``fsum``)."""

        return math.fsum(
            [self.intercept, *(c * v for c, v in zip(self.coefficients, row, strict=True))]
        )

    def state(self) -> dict[str, Any]:
        return {"intercept": self.intercept, "coefficients": list(self.coefficients)}

    @classmethod
    def from_capture(cls, capture: Capture) -> LinearModelFit:
        if sorted(capture.state) != ["coefficients", "intercept"]:
            raise MLInputError("A linear model's state holds its intercept and coefficients.")
        return cls(
            features=capture.features,
            intercept=_float(capture.state["intercept"], "intercept"),
            coefficients=_floats(capture.state["coefficients"], "coefficients"),
        )


def _linear_fit(fitted: object, intercept: bool, kind: str) -> None:
    """Refuse a fit the model's hyperparameters could not have produced."""

    if fitted is None:
        return
    if not isinstance(fitted, LinearModelFit):
        raise MLInputError(f"A {kind}'s fit is a LinearModelFit, got {fitted!r}.")
    if not intercept and fitted.intercept != 0.0:
        raise MLInputError(
            f"A {kind} fitted without an intercept has none; this fit's is {fitted.intercept!r}."
        )


def _weighted_least_squares(
    training: TrainingSet,
    row_weights: Sequence[float],
    targets: Sequence[float],
    *,
    intercept: bool,
    penalty: float,
    maximum_condition: float,
    kind: str,
) -> tuple[float, tuple[float, ...]]:
    """Minimize ``sum w (t - a - x b)^2 + penalty ||b||^2`` by QR; the intercept unpenalized.

    Every operation is correctly rounded -- ``sqrt``, products, ``fsum`` and
    Householder QR (ADR-0058, class 2) -- and each column is scaled to unit
    length first, so the condition measured is the problem's, not the units'.
    """

    names = training.features.names
    offset = 1 if intercept else 0
    width = offset + len(names)
    rows: list[list[float]] = []
    target: list[float] = []
    for x, weight, value in zip(training.features.rows, row_weights, targets, strict=True):
        root = math.sqrt(weight)
        rows.append([*([root] if intercept else []), *(root * v for v in x)])
        target.append(root * value)
    if penalty > 0.0:
        root = math.sqrt(penalty)
        for column in range(offset, width):
            rows.append([root if c == column else 0.0 for c in range(width)])
            target.append(0.0)
    scales = [math.sqrt(math.fsum(row[c] * row[c] for row in rows)) for c in range(width)]
    for column, scale in enumerate(scales):
        if scale == 0.0:
            what = "the intercept's column" if column < offset else repr(names[column - offset])
            raise MLInputError(
                f"Feature {what} is zero in every weighted row, so its coefficient is "
                f"undetermined; drop it, or state a penalty, before fitting the {kind}."
            )
    scaled = [[value / scales[c] for c, value in enumerate(row)] for row in rows]
    try:
        solution = least_squares(scaled, target, maximum_condition)
    except AlphaLabValidationError as refusal:
        raise MLInputError(f"The {kind} cannot be fitted as posed: {refusal}") from refusal
    beta = [b / scales[c] for c, b in enumerate(solution.coefficients)]
    return (beta[0] if intercept else 0.0), tuple(beta[offset:])


@dataclass(frozen=True, slots=True)
class RidgeRegression:
    """Ridge regression by Householder QR, class 2 (ADR-0064 section 3).

    Minimizes ``sum_i w_i (y_i - a - x_i b)^2 + penalty * ||b||^2`` -- the
    intercept ``a`` never penalized -- by least squares on the design with
    ``sqrt(penalty)`` rows appended. A penalty of zero is ordinary (weighted)
    least squares. Its fit draws nothing: the seed is recorded, not used.

    Attributes:
        penalty: The ridge penalty, a finite number of at least zero.
        intercept: Whether to fit an intercept.
        maximum_condition: The largest condition number of the column-scaled
            design accepted (:func:`~alphalab.common.linalg.least_squares`).
        fitted: The fit, or ``None`` for a model not yet fitted.

    Raises:
        MLInputError: If a hyperparameter is outside its range.
    """

    penalty: float
    intercept: bool
    maximum_condition: float
    fitted: LinearModelFit | None = None

    def __post_init__(self) -> None:
        penalty = _real(self.penalty, "A ridge penalty")
        if penalty < 0.0:
            raise MLInputError(f"A ridge penalty is at least zero, got {self.penalty!r}.")
        condition = _real(self.maximum_condition, "maximum_condition")
        if condition < 1.0:
            raise MLInputError(f"maximum_condition is at least 1, got {self.maximum_condition!r}.")
        object.__setattr__(self, "penalty", penalty)
        object.__setattr__(self, "intercept", _flag(self.intercept, "intercept"))
        object.__setattr__(self, "maximum_condition", condition)
        _linear_fit(self.fitted, self.intercept, "ridge regression")

    @property
    def model_kind(self) -> str:
        return RIDGE_KIND

    def hyperparameters(self) -> Mapping[str, ParamValue]:
        return MappingProxyType(
            {
                "penalty": self.penalty,
                "intercept": self.intercept,
                "maximum_condition": self.maximum_condition,
            }
        )

    def fit(self, training: TrainingSet, *, seed: int) -> Self:
        _seed(seed)
        if not isinstance(training, TrainingSet):
            raise MLInputError(f"A model is fitted on a TrainingSet, got {training!r}.")
        weights = training.weights or (1.0,) * len(training)
        intercept, coefficients = _weighted_least_squares(
            training,
            weights,
            training.labels,
            intercept=self.intercept,
            penalty=self.penalty,
            maximum_condition=self.maximum_condition,
            kind="ridge regression",
        )
        return replace(
            self, fitted=LinearModelFit(training.features.names, intercept, coefficients)
        )

    def predict(self, features: FeatureMatrix) -> tuple[float, ...]:
        if self.fitted is None:
            raise _unfitted("ridge regression")
        matrix = _matched(features, self.fitted.features, "ridge regression")
        return tuple(self.fitted.score(row) for row in matrix.rows)

    def capture(self) -> bytes:
        if self.fitted is None:
            raise _unfitted("ridge regression")
        return canonical_capture(
            RIDGE_KIND, self.hyperparameters(), self.fitted.features, self.fitted.state()
        )

    @classmethod
    def restore(cls, artifact: bytes) -> Self:
        capture = read_capture(artifact, RIDGE_KIND)
        stated = _hyperparameters(capture, ("penalty", "intercept", "maximum_condition"))
        model = cls(
            penalty=_float(stated["penalty"], "penalty"),
            intercept=_flag(stated["intercept"], "intercept"),
            maximum_condition=_float(stated["maximum_condition"], "maximum_condition"),
            fitted=LinearModelFit.from_capture(capture),
        )
        return restored(model, artifact)


def _sigmoid(eta: float) -> float:
    """``1 / (1 + exp(-eta))``, written so neither branch overflows."""

    if eta >= 0.0:
        return 1.0 / (1.0 + math.exp(-eta))
    tail = math.exp(eta)
    return tail / (1.0 + tail)


@dataclass(frozen=True, slots=True)
class LogisticRegression:
    """Binary logistic regression by Newton's method, class 3 (ADR-0064 section 3).

    Maximizes ``sum_i w_i [y_i log p_i + (1 - y_i) log(1 - p_i)] - (penalty / 2)
    ||b||^2`` with ``p_i = 1 / (1 + exp(-(a + x_i b)))``, the intercept never
    penalized. Each Newton step is the weighted least-squares solve of
    iteratively reweighted least squares, on M2's QR. It stops when no
    coefficient moved more than ``tolerance`` in a step, and raises
    :class:`~alphalab.common.exceptions.ConvergenceError` if that takes more than
    ``max_iterations`` steps. Labels are 0 and 1, and a prediction is P(label 1).

    Separated classes have no finite maximum without a penalty: the fit is
    refused, either when a probability rounds to 0 or 1 or at the iteration
    limit, never returned from a coefficient that was still growing.

    Attributes:
        penalty: The ridge penalty on the coefficients, at least zero.
        intercept: Whether to fit an intercept.
        tolerance: The largest coefficient change that ends the iteration.
        max_iterations: The Newton steps allowed.
        maximum_condition: The largest condition number of a step's
            column-scaled design accepted.
        fitted: The fit, or ``None`` for a model not yet fitted.

    Raises:
        MLInputError: If a hyperparameter is outside its range.
    """

    penalty: float
    intercept: bool
    tolerance: float
    max_iterations: int
    maximum_condition: float
    fitted: LinearModelFit | None = None

    def __post_init__(self) -> None:
        penalty = _real(self.penalty, "A logistic penalty")
        if penalty < 0.0:
            raise MLInputError(f"A logistic penalty is at least zero, got {self.penalty!r}.")
        tolerance = _real(self.tolerance, "A tolerance")
        if tolerance <= 0.0:
            raise MLInputError(f"A tolerance is positive, got {self.tolerance!r}.")
        condition = _real(self.maximum_condition, "maximum_condition")
        if condition < 1.0:
            raise MLInputError(f"maximum_condition is at least 1, got {self.maximum_condition!r}.")
        object.__setattr__(self, "penalty", penalty)
        object.__setattr__(self, "intercept", _flag(self.intercept, "intercept"))
        object.__setattr__(self, "tolerance", tolerance)
        object.__setattr__(self, "max_iterations", _count(self.max_iterations, "max_iterations", 1))
        object.__setattr__(self, "maximum_condition", condition)
        _linear_fit(self.fitted, self.intercept, "logistic regression")

    @property
    def model_kind(self) -> str:
        return LOGISTIC_KIND

    def hyperparameters(self) -> Mapping[str, ParamValue]:
        return MappingProxyType(
            {
                "penalty": self.penalty,
                "intercept": self.intercept,
                "tolerance": self.tolerance,
                "max_iterations": self.max_iterations,
                "maximum_condition": self.maximum_condition,
            }
        )

    def fit(self, training: TrainingSet, *, seed: int) -> Self:
        _seed(seed)
        if not isinstance(training, TrainingSet):
            raise MLInputError(f"A model is fitted on a TrainingSet, got {training!r}.")
        _binary(training, "logistic regression")
        names = training.features.names
        weights = training.weights or (1.0,) * len(training)
        current = LinearModelFit(names, 0.0, (0.0,) * len(names))
        change = math.inf
        for iteration in range(1, self.max_iterations + 1):
            working_weights: list[float] = []
            working: list[float] = []
            for index, (row, label) in enumerate(
                zip(training.features.rows, training.labels, strict=True)
            ):
                eta = current.score(row)
                probability = _sigmoid(eta)
                variance = probability * (1.0 - probability)
                if variance == 0.0:
                    raise MLInputError(
                        f"At Newton step {iteration}, row {index}'s probability rounds to "
                        f"{probability!r}: the classes are separated, or a feature is too "
                        "large to fit without a penalty. State a penalty, or rescale."
                    )
                working_weights.append(weights[index] * variance)
                working.append(eta + (label - probability) / variance)
            intercept, coefficients = _weighted_least_squares(
                training,
                working_weights,
                working,
                intercept=self.intercept,
                penalty=self.penalty,
                maximum_condition=self.maximum_condition,
                kind="logistic regression",
            )
            change = max(
                abs(intercept - current.intercept),
                *(
                    abs(new - old)
                    for new, old in zip(coefficients, current.coefficients, strict=True)
                ),
            )
            current = LinearModelFit(names, intercept, coefficients)
            if change <= self.tolerance:
                return replace(self, fitted=current)
        raise ConvergenceError(
            "logistic regression by Newton's method", self.tolerance, self.max_iterations, change
        )

    def predict(self, features: FeatureMatrix) -> tuple[float, ...]:
        if self.fitted is None:
            raise _unfitted("logistic regression")
        matrix = _matched(features, self.fitted.features, "logistic regression")
        return tuple(_sigmoid(self.fitted.score(row)) for row in matrix.rows)

    def capture(self) -> bytes:
        if self.fitted is None:
            raise _unfitted("logistic regression")
        return canonical_capture(
            LOGISTIC_KIND, self.hyperparameters(), self.fitted.features, self.fitted.state()
        )

    @classmethod
    def restore(cls, artifact: bytes) -> Self:
        capture = read_capture(artifact, LOGISTIC_KIND)
        stated = _hyperparameters(
            capture, ("penalty", "intercept", "tolerance", "max_iterations", "maximum_condition")
        )
        iterations = stated["max_iterations"]
        if isinstance(iterations, bool) or not isinstance(iterations, int):
            raise MLInputError(f"The capture's max_iterations is {iterations!r}, not an integer.")
        model = cls(
            penalty=_float(stated["penalty"], "penalty"),
            intercept=_flag(stated["intercept"], "intercept"),
            tolerance=_float(stated["tolerance"], "tolerance"),
            max_iterations=iterations,
            maximum_condition=_float(stated["maximum_condition"], "maximum_condition"),
            fitted=LinearModelFit.from_capture(capture),
        )
        return restored(model, artifact)


# -- gradient-boosted trees ---------------------------------------------------


class BoostingLoss(StrEnum):
    """What a tree ensemble minimizes."""

    #: Half the squared error: a regression; every tree fits the residuals.
    SQUARED = "squared"
    #: The binary log-loss: a classifier of labels 0 and 1, by Newton steps.
    LOGISTIC = "logistic"


@dataclass(frozen=True, slots=True)
class TreeLeaf:
    """A tree's leaf: the value it adds, already scaled by the learning rate."""

    value: float


@dataclass(frozen=True, slots=True)
class TreeSplit:
    """A tree's split: rows whose ``feature`` is at most ``threshold`` go left.

    The threshold is the largest value seen on the left, never a midpoint:
    halving the gap to the next value can round onto that value.
    """

    feature: int
    threshold: float
    left: TreeLeaf | TreeSplit
    right: TreeLeaf | TreeSplit


def _evaluate(node: TreeLeaf | TreeSplit, row: Sequence[float]) -> float:
    while isinstance(node, TreeSplit):
        node = node.left if row[node.feature] <= node.threshold else node.right
    return node.value


def _depth(node: TreeLeaf | TreeSplit) -> int:
    if isinstance(node, TreeLeaf):
        return 0
    return 1 + max(_depth(node.left), _depth(node.right))


def _node_state(node: TreeLeaf | TreeSplit) -> dict[str, Any]:
    if isinstance(node, TreeLeaf):
        return {"value": node.value}
    return {
        "feature": node.feature,
        "threshold": node.threshold,
        "left": _node_state(node.left),
        "right": _node_state(node.right),
    }


def _node_from(state: object, width: int) -> TreeLeaf | TreeSplit:
    if not isinstance(state, dict):
        raise MLInputError("A captured tree node is not a mapping.")
    if sorted(state) == ["value"]:
        return TreeLeaf(_float(state["value"], "leaf value"))
    if sorted(state) != ["feature", "left", "right", "threshold"]:
        raise MLInputError(f"A captured tree node holds {sorted(state)}.")
    feature = state["feature"]
    if isinstance(feature, bool) or not isinstance(feature, int) or not 0 <= feature < width:
        raise MLInputError(f"A captured split names feature {feature!r} of {width}.")
    return TreeSplit(
        feature=feature,
        threshold=_float(state["threshold"], "threshold"),
        left=_node_from(state["left"], width),
        right=_node_from(state["right"], width),
    )


@dataclass(frozen=True, slots=True)
class TreeEnsembleFit:
    """A fitted ensemble: ``base`` plus each tree's leaf, added in tree order.

    Attributes:
        features: The feature names it was fitted on, in the order splits index.
        base: The constant the boosting starts from: the weighted mean label,
            or its log-odds for the logistic loss.
        trees: The trees, in the order they were grown.
    """

    features: tuple[str, ...]
    base: float
    trees: tuple[TreeLeaf | TreeSplit, ...]

    def score(self, row: Sequence[float]) -> float:
        """The ensemble's raw value, summed in the order training summed it."""

        value = self.base
        for tree in self.trees:
            value = value + _evaluate(tree, row)
        return value


class _Grower:
    """Grows one tree on gradients and Hessians: exact greedy splits, deterministic ties."""

    def __init__(
        self,
        rows: tuple[tuple[float, ...], ...],
        order: list[list[int]],
        gradients: list[float],
        hessians: list[float],
        *,
        depth: int,
        minimum_leaf: int,
        learning_rate: float,
    ) -> None:
        self.rows = rows
        self.order = order
        self.gradients = gradients
        self.hessians = hessians
        self.depth = depth
        self.minimum_leaf = minimum_leaf
        self.learning_rate = learning_rate

    def leaf(self, members: Sequence[int]) -> TreeLeaf:
        hessian = math.fsum(self.hessians[i] for i in members)
        if hessian <= 0.0:
            return TreeLeaf(0.0)
        gradient = math.fsum(self.gradients[i] for i in members)
        return TreeLeaf(self.learning_rate * (gradient / hessian))

    def grow(self, members: Sequence[int], depth: int) -> TreeLeaf | TreeSplit:
        if depth == 0 or len(members) < 2 * self.minimum_leaf:
            return self.leaf(members)
        inside = set(members)
        total_g = math.fsum(self.gradients[i] for i in members)
        total_h = math.fsum(self.hessians[i] for i in members)
        if total_h <= 0.0:
            return TreeLeaf(0.0)
        parent = total_g * total_g / total_h
        best: tuple[float, int, float, list[int], list[int]] | None = None
        for feature, ordered_all in enumerate(self.order):
            ordered = [i for i in ordered_all if i in inside]
            left_g = 0.0
            left_h = 0.0
            for position in range(1, len(ordered)):
                previous = ordered[position - 1]
                left_g += self.gradients[previous]
                left_h += self.hessians[previous]
                if position < self.minimum_leaf or len(ordered) - position < self.minimum_leaf:
                    continue
                low = self.rows[previous][feature]
                if low == self.rows[ordered[position]][feature]:
                    continue
                right_g = total_g - left_g
                right_h = total_h - left_h
                if left_h <= 0.0 or right_h <= 0.0:
                    continue
                gain = left_g * left_g / left_h + right_g * right_g / right_h - parent
                if best is None or gain > best[0]:
                    best = (gain, feature, low, ordered[:position], ordered[position:])
        if best is None or not best[0] > 0.0:
            return self.leaf(members)
        _, feature, threshold, left, right = best
        return TreeSplit(
            feature, threshold, self.grow(left, depth - 1), self.grow(right, depth - 1)
        )


def _ordered(rows: Sequence[Sequence[float]], feature: int) -> list[int]:
    """Row indices by ``feature``'s value, ties by index: the order every split scans."""

    return sorted(range(len(rows)), key=lambda row: (rows[row][feature], row))


def _subsample(count: int, fraction: float, seed: int, tree: int) -> list[int]:
    """The rows tree ``tree`` grows on: all, or the first by SHA-256 of seed, tree and row."""

    if fraction == 1.0:
        return list(range(count))
    keep = max(1, math.floor(fraction * count))
    ranked = sorted(
        range(count),
        key=lambda row: (hashlib.sha256(f"{seed}|{tree}|{row}".encode()).digest(), row),
    )
    return sorted(ranked[:keep])


@dataclass(frozen=True, slots=True)
class GradientBoostedTrees:
    """Gradient boosting on regression trees: the protocol's oracle for boosting adapters.

    Starts from a constant -- the weighted mean label, or its log-odds -- and
    grows ``trees`` trees in turn, each on the current gradients and
    Hessians: the residuals with unit Hessians for the squared loss, and
    ``y - p`` with ``p (1 - p)`` for the logistic loss (Newton boosting). A
    split maximizes ``G_L^2 / H_L + G_R^2 / H_R - G^2 / H`` over every feature
    and every boundary between distinct values, ties going to the first
    feature, then the lowest threshold; it is made only if the gain is
    positive, both sides keep ``minimum_leaf`` rows and the tree is shallower
    than ``depth``. A leaf adds ``learning_rate * G / H``.

    With ``subsample`` below one, each tree grows on the
    ``floor(subsample * n)`` rows ranked first by SHA-256 of the seed, the
    tree's index and the row's, and updates every row. At one, the seed is
    recorded, not used.

    Attributes:
        loss: What the ensemble minimizes.
        trees: How many trees, at least one.
        learning_rate: The shrinkage of every leaf, in ``(0, 1]``.
        depth: A tree's greatest depth, at least one.
        minimum_leaf: The fewest rows a leaf may hold, at least one.
        subsample: The fraction of rows each tree grows on, in ``(0, 1]``.
        fitted: The fit, or ``None`` for a model not yet fitted.

    Raises:
        MLInputError: If a hyperparameter is outside its range.
    """

    loss: BoostingLoss
    trees: int
    learning_rate: float
    depth: int
    minimum_leaf: int
    subsample: float
    fitted: TreeEnsembleFit | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.loss, BoostingLoss):
            raise MLInputError(f"loss is a BoostingLoss, got {self.loss!r}.")
        rate = _real(self.learning_rate, "learning_rate")
        if not 0.0 < rate <= 1.0:
            raise MLInputError(f"learning_rate is in (0, 1], got {self.learning_rate!r}.")
        fraction = _real(self.subsample, "subsample")
        if not 0.0 < fraction <= 1.0:
            raise MLInputError(f"subsample is in (0, 1], got {self.subsample!r}.")
        object.__setattr__(self, "trees", _count(self.trees, "trees", 1))
        object.__setattr__(self, "learning_rate", rate)
        object.__setattr__(self, "depth", _count(self.depth, "depth", 1))
        object.__setattr__(self, "minimum_leaf", _count(self.minimum_leaf, "minimum_leaf", 1))
        object.__setattr__(self, "subsample", fraction)
        fitted = self.fitted
        if fitted is None:
            return
        if not isinstance(fitted, TreeEnsembleFit):
            raise MLInputError(f"A tree ensemble's fit is a TreeEnsembleFit, got {fitted!r}.")
        if len(fitted.trees) != self.trees:
            raise MLInputError(f"A tree ensemble of {self.trees} trees holds {len(fitted.trees)}.")
        deepest = max(_depth(tree) for tree in fitted.trees)
        if deepest > self.depth:
            raise MLInputError(
                f"A tree ensemble of depth {self.depth} holds a tree of depth {deepest}."
            )

    @property
    def model_kind(self) -> str:
        return GRADIENT_BOOSTED_TREES_KIND

    def hyperparameters(self) -> Mapping[str, ParamValue]:
        return MappingProxyType(
            {
                "loss": self.loss.value,
                "trees": self.trees,
                "learning_rate": self.learning_rate,
                "depth": self.depth,
                "minimum_leaf": self.minimum_leaf,
                "subsample": self.subsample,
            }
        )

    def fit(self, training: TrainingSet, *, seed: int) -> Self:
        seed = _seed(seed)
        if not isinstance(training, TrainingSet):
            raise MLInputError(f"A model is fitted on a TrainingSet, got {training!r}.")
        logistic = self.loss is BoostingLoss.LOGISTIC
        if logistic:
            _binary(training, "logistic tree ensemble")
        rows = training.features.rows
        labels = training.labels
        count = len(rows)
        weights = training.weights or (1.0,) * count
        mean = math.fsum(w * y for w, y in zip(weights, labels, strict=True)) / math.fsum(weights)
        base = math.log(mean / (1.0 - mean)) if logistic else mean
        order = [_ordered(rows, feature) for feature in range(len(training.features.names))]
        values = [base] * count
        grown: list[TreeLeaf | TreeSplit] = []
        for tree in range(self.trees):
            gradients: list[float] = []
            hessians: list[float] = []
            for value, label, weight in zip(values, labels, weights, strict=True):
                if logistic:
                    probability = _sigmoid(value)
                    gradients.append(weight * (label - probability))
                    hessians.append(weight * (probability * (1.0 - probability)))
                else:
                    gradients.append(weight * (label - value))
                    hessians.append(weight)
            grower = _Grower(
                rows,
                order,
                gradients,
                hessians,
                depth=self.depth,
                minimum_leaf=self.minimum_leaf,
                learning_rate=self.learning_rate,
            )
            root = grower.grow(_subsample(count, self.subsample, seed, tree), self.depth)
            grown.append(root)
            values = [value + _evaluate(root, row) for value, row in zip(values, rows, strict=True)]
        return replace(self, fitted=TreeEnsembleFit(training.features.names, base, tuple(grown)))

    def predict(self, features: FeatureMatrix) -> tuple[float, ...]:
        if self.fitted is None:
            raise _unfitted("tree ensemble")
        matrix = _matched(features, self.fitted.features, "tree ensemble")
        scores = (self.fitted.score(row) for row in matrix.rows)
        if self.loss is BoostingLoss.LOGISTIC:
            return tuple(_sigmoid(score) for score in scores)
        return tuple(scores)

    def capture(self) -> bytes:
        if self.fitted is None:
            raise _unfitted("tree ensemble")
        state = {
            "base": self.fitted.base,
            "trees": [_node_state(tree) for tree in self.fitted.trees],
        }
        return canonical_capture(
            GRADIENT_BOOSTED_TREES_KIND, self.hyperparameters(), self.fitted.features, state
        )

    @classmethod
    def restore(cls, artifact: bytes) -> Self:
        capture = read_capture(artifact, GRADIENT_BOOSTED_TREES_KIND)
        stated = _hyperparameters(
            capture, ("loss", "trees", "learning_rate", "depth", "minimum_leaf", "subsample")
        )
        if sorted(capture.state) != ["base", "trees"] or not isinstance(
            capture.state["trees"], list
        ):
            raise MLInputError("A tree ensemble's state holds its base and its trees.")
        loss = stated["loss"]
        if loss not in {member.value for member in BoostingLoss}:
            raise MLInputError(f"The capture's loss is {loss!r}.")
        integers: dict[str, int] = {}
        for name in ("trees", "depth", "minimum_leaf"):
            value = stated[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise MLInputError(f"The capture's {name} is {value!r}, not an integer.")
            integers[name] = value
        width = len(capture.features)
        fitted = TreeEnsembleFit(
            features=capture.features,
            base=_float(capture.state["base"], "base"),
            trees=tuple(_node_from(tree, width) for tree in capture.state["trees"]),
        )
        model = cls(
            loss=BoostingLoss(loss),
            trees=integers["trees"],
            learning_rate=_float(stated["learning_rate"], "learning_rate"),
            depth=integers["depth"],
            minimum_leaf=integers["minimum_leaf"],
            subsample=_float(stated["subsample"], "subsample"),
            fitted=fitted,
        )
        return restored(model, artifact)
