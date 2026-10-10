"""Linear regression by Householder QR.

Deliberately a direct solve, not gradient descent: least squares has an exact
solution, and computing it means training is a single deterministic computation
with no convergence criteria, no iteration count to tune, and no
floating-point-order-dependent behavior across runs.

Until v3.12 that solve formed the normal equations ``X'X b = X'y`` and inverted
``X'X`` by Gauss-Jordan elimination. Forming ``X'X`` squares the design's
condition number, so the digits a nearly collinear design loses were lost twice,
and a rank-deficient design was only noticed if the elimination happened to meet
an exact zero (ledger NUM-007). The design is now reduced by
:func:`alphalab.common.linalg.least_squares` -- orthogonal reflections, never
``X'X`` -- after each column is scaled to unit length, so the condition measured
is the problem's rather than the units'. A design past
:data:`MAXIMUM_DESIGN_CONDITION` is refused with the number. A ridge penalty
is the same minimizer written as extra rows, ``sqrt(lambda)`` on each feature's
diagonal with a target of zero, never on the intercept.
"""

from dataclasses import dataclass
from typing import Final

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.linalg import least_squares
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.linalg import Matrix

#: The largest condition number accepted for the column-scaled design. A
#: least-squares solution loses about ``log10(condition)`` of its sixteen digits
#: to rounding, so at this bound it keeps about ten; a design past it has
#: features so nearly collinear that their coefficients would be decided by
#: rounding. The bound :func:`alphalab.factor_library.neutralize_exposures` uses.
MAXIMUM_DESIGN_CONDITION: Final = 1e6


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
    feature_names: tuple[str, ...],
    x: Matrix,
    y: tuple[float, ...],
    l2_penalty: float = 0.0,
    *,
    maximum_condition: float = MAXIMUM_DESIGN_CONDITION,
) -> LinearRegressionModel:
    """Trains a linear regression model, optionally ridge-regularized, by QR.

    Minimizes ``||X b - y||^2 + lambda * ||b'||^2``, where ``X`` has an intercept
    column of 1s prepended and ``b'`` is every coefficient but the intercept's --
    the standard ridge convention, so the bias term is never shrunk.

    Raises:
        MLInputError: If x and y have mismatched sample counts, x is empty,
            feature_names length doesn't match x's column count, l2_penalty is
            negative, a feature is identically zero, there are fewer samples
            than coefficients and no penalty, or the column-scaled design's
            condition number exceeds ``maximum_condition`` (a rank-deficient
            design has an infinite one). The message names the condition.
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

    width = len(feature_names) + 1
    rows: list[list[float]] = [[1.0, *row] for row in x]
    target: list[float] = list(y)
    if l2_penalty > 0:
        root = l2_penalty**0.5
        for feature in range(1, width):
            rows.append([root if column == feature else 0.0 for column in range(width)])
            target.append(0.0)

    scales = [sum(row[column] ** 2 for row in rows) ** 0.5 for column in range(width)]
    for column, scale in enumerate(scales):
        if scale == 0.0:
            raise MLInputError(
                f"Feature {feature_names[column - 1]!r} is zero in every sample, so its "
                "coefficient is undetermined."
            )
    scaled = [[value / scales[column] for column, value in enumerate(row)] for row in rows]

    try:
        solution = least_squares(scaled, target, maximum_condition)
    except AlphaLabValidationError as refusal:
        raise MLInputError(f"The regression cannot be solved as posed: {refusal}") from refusal

    beta = [
        coefficient / scales[column] for column, coefficient in enumerate(solution.coefficients)
    ]
    return LinearRegressionModel(
        feature_names=feature_names,
        coefficients=tuple(beta[1:]),
        intercept=beta[0],
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
