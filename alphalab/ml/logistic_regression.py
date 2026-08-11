"""Binary logistic regression via deterministic gradient descent.

Unlike linear regression, logistic regression has no closed-form solution --
gradient descent is genuinely necessary. Determinism is preserved by using a fixed
iteration count (not a convergence tolerance that could vary run-to-run) and
zero-initialized weights (not random initialization).
"""

import math
from dataclasses import dataclass

from alphalab.ml.exceptions import MLInputError
from alphalab.ml.linalg import Matrix


@dataclass(frozen=True, slots=True)
class LogisticRegressionModel:
    """A trained binary logistic regression model.

    Attributes:
        feature_names: Names of the features this model was trained on, in the
            order `weights` corresponds to.
        weights: One weight per feature.
        bias: The bias term.
        iterations: Number of gradient descent iterations used during training.
        learning_rate: The learning rate used during training.
    """

    feature_names: tuple[str, ...]
    weights: tuple[float, ...]
    bias: float
    iterations: int
    learning_rate: float


def _sigmoid(z: float) -> float:
    """Numerically stable sigmoid, avoiding overflow for large-magnitude z."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def train_logistic_regression(
    feature_names: tuple[str, ...],
    x: Matrix,
    y: tuple[int, ...],
    learning_rate: float = 0.1,
    iterations: int = 1000,
) -> LogisticRegressionModel:
    """Trains a binary logistic regression model via batch gradient descent.

    Raises:
        MLInputError: If x and y have mismatched sample counts, x is empty, any
            label in y is not 0 or 1, feature_names length doesn't match x's
            column count, or learning_rate/iterations are not positive.
    """
    if not x or not y:
        raise MLInputError("x and y cannot be empty.")
    if len(x) != len(y):
        raise MLInputError(f"x has {len(x)} samples but y has {len(y)}.")
    if len(x[0]) != len(feature_names):
        raise MLInputError(
            f"feature_names has {len(feature_names)} entries but x has {len(x[0])} columns."
        )
    if any(label not in (0, 1) for label in y):
        raise MLInputError("y must contain only 0 or 1 labels for binary logistic regression.")
    if learning_rate <= 0:
        raise MLInputError(f"learning_rate must be positive, got {learning_rate}.")
    if iterations <= 0:
        raise MLInputError(f"iterations must be positive, got {iterations}.")

    n_samples = len(x)
    n_features = len(feature_names)
    weights = [0.0] * n_features
    bias = 0.0

    for _ in range(iterations):
        predictions = [
            _sigmoid(bias + sum(w * v for w, v in zip(weights, row, strict=True))) for row in x
        ]
        errors = [predictions[i] - y[i] for i in range(n_samples)]

        weight_gradients = [
            sum(errors[i] * x[i][j] for i in range(n_samples)) / n_samples
            for j in range(n_features)
        ]
        bias_gradient = sum(errors) / n_samples

        weights = [weights[j] - learning_rate * weight_gradients[j] for j in range(n_features)]
        bias -= learning_rate * bias_gradient

    return LogisticRegressionModel(
        feature_names=feature_names,
        weights=tuple(weights),
        bias=bias,
        iterations=iterations,
        learning_rate=learning_rate,
    )


def predict_proba_logistic(model: LogisticRegressionModel, x: Matrix) -> tuple[float, ...]:
    """Predicts P(y=1) for each row in x.

    Raises:
        MLInputError: If a row's length doesn't match the model's feature count.
    """
    n_features = len(model.weights)
    probabilities = []
    for row in x:
        if len(row) != n_features:
            raise MLInputError(f"Expected {n_features} features per row, got {len(row)}.")
        z = model.bias + sum(w * v for w, v in zip(model.weights, row, strict=True))
        probabilities.append(_sigmoid(z))
    return tuple(probabilities)


def predict_logistic(
    model: LogisticRegressionModel, x: Matrix, threshold: float = 0.5
) -> tuple[int, ...]:
    """Predicts binary class labels for each row in x, thresholding P(y=1).

    Raises:
        MLInputError: If threshold is not between 0 and 1.
    """
    if not (0.0 <= threshold <= 1.0):
        raise MLInputError(f"threshold must be between 0 and 1, got {threshold}.")
    return tuple(1 if p >= threshold else 0 for p in predict_proba_logistic(model, x))
