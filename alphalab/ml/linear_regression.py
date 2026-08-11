"""Linear regression via the closed-form normal equation.

Deliberately closed-form, not gradient descent: OLS has an exact solution, and
using it means training is a single deterministic computation with no convergence
criteria, no iteration count to tune, and no floating-point-order-dependent
behavior across runs -- a better fit for this framework's deterministic-execution
principle than an iterative approximation of an exact answer.
"""

from dataclasses import dataclass

from alphalab.ml.exceptions import MLInputError
from alphalab.ml.linalg import Matrix, matmul, matrix_inverse, transpose


@dataclass(frozen=True, slots=True)
class LinearRegressionModel:
    """A trained linear regression model.

    Attributes:
        feature_names: Names of the features this model was trained on, in the
            order `coefficients` corresponds to.
        coefficients: One weight per feature.
        intercept: The bias term.
        l2_penalty: The ridge regularization strength used during training.
    """

    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    l2_penalty: float = 0.0


def train_linear_regression(
    feature_names: tuple[str, ...], x: Matrix, y: tuple[float, ...], l2_penalty: float = 0.0
) -> LinearRegressionModel:
    """Trains a linear regression model via the (optionally ridge-regularized) normal equation.

    Solves for beta in: beta = (X^T X + lambda * I')^-1 X^T y, where X has an
    intercept column of 1s prepended, and I' is the identity matrix with the
    intercept's diagonal entry zeroed so the bias term is never regularized -- the
    standard ridge regression convention.

    Raises:
        MLInputError: If x and y have mismatched sample counts, x is empty,
            feature_names length doesn't match x's column count, or l2_penalty is
            negative.
    """
    if l2_penalty < 0:
        raise MLInputError(f"l2_penalty cannot be negative, got {l2_penalty}.")
    if not x or not y:
        raise MLInputError("x and y cannot be empty.")
    if len(x) != len(y):
        raise MLInputError(f"x has {len(x)} samples but y has {len(y)}.")
    if len(x[0]) != len(feature_names):
        raise MLInputError(
            f"feature_names has {len(feature_names)} entries but x has {len(x[0])} columns."
        )

    x_with_intercept: Matrix = tuple((1.0, *row) for row in x)
    y_column: Matrix = tuple((value,) for value in y)

    xt = transpose(x_with_intercept)
    xtx = matmul(xt, x_with_intercept)

    if l2_penalty > 0:
        xtx = tuple(
            tuple(
                value + (l2_penalty if (i == j and i != 0) else 0.0) for j, value in enumerate(row)
            )
            for i, row in enumerate(xtx)
        )

    xtx_inv = matrix_inverse(xtx)
    xty = matmul(xt, y_column)
    beta = matmul(xtx_inv, xty)

    beta_flat = tuple(row[0] for row in beta)
    return LinearRegressionModel(
        feature_names=feature_names,
        coefficients=beta_flat[1:],
        intercept=beta_flat[0],
        l2_penalty=l2_penalty,
    )


def predict_linear(model: LinearRegressionModel, x: Matrix) -> tuple[float, ...]:
    """Predicts target values for each row in x.

    Raises:
        MLInputError: If a row's length doesn't match the model's feature count.
    """
    n_features = len(model.coefficients)
    predictions = []
    for row in x:
        if len(row) != n_features:
            raise MLInputError(f"Expected {n_features} features per row, got {len(row)}.")
        prediction = model.intercept + sum(
            c * v for c, v in zip(model.coefficients, row, strict=True)
        )
        predictions.append(prediction)
    return tuple(predictions)
