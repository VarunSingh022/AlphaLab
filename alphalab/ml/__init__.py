"""AlphaLab Machine Learning Engine.

The research model protocol (ADR-0064): a :class:`Model` states its kind and
hyperparameters, fits purely, predicts from named features, and captures itself
as canonical bytes -- the bytes are the trained model's identity, filed in an
artifact store and declared by the strategy that trades it. Three reference
models implement it in pure Python: :class:`RidgeRegression`,
:class:`LogisticRegression` and :class:`GradientBoostedTrees`. Adapters for
external frameworks live outside the core, in ``alphalab-models`` (ADR-0057).

Beside the protocol: feature pipelines (built directly from Feature Store, not
point-in-time), linear and logistic regression, k-fold and walk-forward
cross-validation, and regression/classification evaluation metrics.

Zero runtime dependencies -- no numpy. `alphalab.ml.linalg` is a small,
purpose-built linear algebra module, not a general-purpose replacement.
`walk_forward_split` exists alongside `k_fold_split`, not instead of it, because
naive k-fold on time-ordered financial features leaks future data into training
folds -- the same look-ahead bias risk this project's research/bias-audit work
targets in backtests, applying equally to ML validation.
"""

from alphalab.ml.cross_validation import TrainTestSplit, k_fold_split, walk_forward_split
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
from alphalab.ml.models import (
    GRADIENT_BOOSTED_TREES_KIND,
    LOGISTIC_KIND,
    RIDGE_KIND,
    BoostingLoss,
    GradientBoostedTrees,
    LinearModelFit,
    LogisticRegression,
    RidgeRegression,
    TreeEnsembleFit,
    TreeLeaf,
    TreeSplit,
)
from alphalab.ml.protocol import (
    MODEL_SCHEME,
    TRAINING_SET_SCHEME,
    Capture,
    FeatureMatrix,
    Model,
    TrainingRecord,
    TrainingSet,
    canonical_capture,
    file_model,
    read_capture,
    restore_model,
    restored,
    train_model,
)

__all__ = [
    "GRADIENT_BOOSTED_TREES_KIND",
    "LOGISTIC_KIND",
    "MODEL_SCHEME",
    "RIDGE_KIND",
    "TRAINING_SET_SCHEME",
    "BoostingLoss",
    "Capture",
    "ConfusionMatrix",
    "Dataset",
    "FeatureMatrix",
    "FeatureSpec",
    "GradientBoostedTrees",
    "LinearModelFit",
    "LinearRegressionModel",
    "LogisticRegression",
    "LogisticRegressionModel",
    "MLComputationError",
    "MLError",
    "MLInputError",
    "Matrix",
    "Model",
    "RidgeRegression",
    "TrainTestSplit",
    "TrainingRecord",
    "TrainingSet",
    "TreeEnsembleFit",
    "TreeLeaf",
    "TreeSplit",
    "accuracy",
    "build_dataset_from_feature_store",
    "canonical_capture",
    "confusion_matrix",
    "f1_score",
    "file_model",
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
    "read_capture",
    "recall",
    "restore_model",
    "restored",
    "root_mean_squared_error",
    "train_linear_regression",
    "train_logistic_regression",
    "train_model",
    "transpose",
    "walk_forward_split",
]
