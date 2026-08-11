"""AlphaLab Machine Learning Engine.

Feature pipelines (built directly from Feature Store), linear and logistic
regression, k-fold and walk-forward cross-validation, and regression/classification
evaluation metrics.

Zero runtime dependencies -- no numpy. `alphalab.ml.linalg` is a small,
purpose-built linear algebra module, not a general-purpose replacement.
`walk_forward_split` exists alongside `k_fold_split`, not instead of it, because
naive k-fold on time-ordered financial features leaks future data into training
folds -- the same look-ahead bias risk this project's research/bias-audit work
targets in backtests, applying equally to ML validation.
"""

from alphalab.ml.cross_validation import Split, k_fold_split, walk_forward_split
from alphalab.ml.dataset import Dataset, FeatureSpec, build_dataset_from_feature_store
from alphalab.ml.evaluation import (
    ConfusionMatrix,
    accuracy,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision,
    r_squared,
    recall,
    root_mean_squared_error,
)
from alphalab.ml.exceptions import MLComputationError, MLError, MLInputError
from alphalab.ml.linalg import Matrix, identity, matmul, matrix_inverse, transpose
from alphalab.ml.linear_regression import (
    LinearRegressionModel,
    predict_linear,
    train_linear_regression,
)
from alphalab.ml.logistic_regression import (
    LogisticRegressionModel,
    predict_logistic,
    predict_proba_logistic,
    train_logistic_regression,
)

__all__ = [
    "ConfusionMatrix",
    "Dataset",
    "FeatureSpec",
    "LinearRegressionModel",
    "LogisticRegressionModel",
    "MLComputationError",
    "MLError",
    "MLInputError",
    "Matrix",
    "Split",
    "accuracy",
    "build_dataset_from_feature_store",
    "confusion_matrix",
    "f1_score",
    "identity",
    "k_fold_split",
    "matmul",
    "matrix_inverse",
    "mean_absolute_error",
    "mean_squared_error",
    "precision",
    "predict_linear",
    "predict_logistic",
    "predict_proba_logistic",
    "r_squared",
    "recall",
    "root_mean_squared_error",
    "train_linear_regression",
    "train_logistic_regression",
    "transpose",
    "walk_forward_split",
]
