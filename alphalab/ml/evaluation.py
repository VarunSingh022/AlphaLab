"""Regression and classification evaluation metrics."""

from dataclasses import dataclass

from alphalab.ml.exceptions import MLInputError


def _validate_paired(y_true: tuple[float, ...], y_pred: tuple[float, ...]) -> None:
    if not y_true:
        raise MLInputError("y_true and y_pred cannot be empty.")
    if len(y_true) != len(y_pred):
        raise MLInputError(f"y_true has {len(y_true)} values but y_pred has {len(y_pred)}.")


def mean_squared_error(y_true: tuple[float, ...], y_pred: tuple[float, ...]) -> float:
    """Computes the mean squared error."""
    _validate_paired(y_true, y_pred)
    return sum((t - p) ** 2 for t, p in zip(y_true, y_pred, strict=True)) / len(y_true)


def root_mean_squared_error(y_true: tuple[float, ...], y_pred: tuple[float, ...]) -> float:
    """Computes the root mean squared error."""
    return float(mean_squared_error(y_true, y_pred) ** 0.5)


def mean_absolute_error(y_true: tuple[float, ...], y_pred: tuple[float, ...]) -> float:
    """Computes the mean absolute error."""
    _validate_paired(y_true, y_pred)
    return sum(abs(t - p) for t, p in zip(y_true, y_pred, strict=True)) / len(y_true)


def r_squared(y_true: tuple[float, ...], y_pred: tuple[float, ...]) -> float:
    """Computes the coefficient of determination (R^2).

    Raises:
        MLInputError: If every value in y_true is identical, making the total sum
            of squares zero and R^2 undefined.
    """
    _validate_paired(y_true, y_pred)
    mean_true = sum(y_true) / len(y_true)
    total_sum_squares = sum((t - mean_true) ** 2 for t in y_true)
    if total_sum_squares == 0.0:
        raise MLInputError("R^2 is undefined when every y_true value is identical.")

    residual_sum_squares = sum((t - p) ** 2 for t, p in zip(y_true, y_pred, strict=True))
    return 1.0 - residual_sum_squares / total_sum_squares


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    """A binary classification confusion matrix.

    Attributes:
        true_positives: Predicted 1, actually 1.
        true_negatives: Predicted 0, actually 0.
        false_positives: Predicted 1, actually 0.
        false_negatives: Predicted 0, actually 1.
    """

    true_positives: int
    true_negatives: int
    false_positives: int
    false_negatives: int


def confusion_matrix(y_true: tuple[int, ...], y_pred: tuple[int, ...]) -> ConfusionMatrix:
    """Builds a binary confusion matrix.

    Raises:
        MLInputError: If y_true/y_pred are empty, mismatched in length, or contain
            labels other than 0 or 1.
    """
    if not y_true:
        raise MLInputError("y_true and y_pred cannot be empty.")
    if len(y_true) != len(y_pred):
        raise MLInputError(f"y_true has {len(y_true)} values but y_pred has {len(y_pred)}.")
    if any(v not in (0, 1) for v in (*y_true, *y_pred)):
        raise MLInputError("confusion_matrix requires binary 0/1 labels.")

    tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == 1 and p == 0)
    return ConfusionMatrix(
        true_positives=tp, true_negatives=tn, false_positives=fp, false_negatives=fn
    )


def accuracy(y_true: tuple[int, ...], y_pred: tuple[int, ...]) -> float:
    """Computes classification accuracy: fraction of correct predictions."""
    cm = confusion_matrix(y_true, y_pred)
    total = cm.true_positives + cm.true_negatives + cm.false_positives + cm.false_negatives
    return (cm.true_positives + cm.true_negatives) / total


def precision(y_true: tuple[int, ...], y_pred: tuple[int, ...]) -> float:
    """Computes precision: TP / (TP + FP). Returns 0.0 if no positive predictions were made."""
    cm = confusion_matrix(y_true, y_pred)
    denominator = cm.true_positives + cm.false_positives
    if denominator == 0:
        return 0.0
    return cm.true_positives / denominator


def recall(y_true: tuple[int, ...], y_pred: tuple[int, ...]) -> float:
    """Computes recall: TP / (TP + FN). Returns 0.0 if no actual positives exist."""
    cm = confusion_matrix(y_true, y_pred)
    denominator = cm.true_positives + cm.false_negatives
    if denominator == 0:
        return 0.0
    return cm.true_positives / denominator


def f1_score(y_true: tuple[int, ...], y_pred: tuple[int, ...]) -> float:
    """Computes the F1 score: the harmonic mean of precision and recall."""
    p = precision(y_true, y_pred)
    r = recall(y_true, y_pred)
    if p + r == 0.0:
        return 0.0
    return 2 * p * r / (p + r)
