"""AlphaLab Machine Learning Engine.

The research model protocol (ADR-0064): a :class:`Model` states its kind and
hyperparameters, fits purely, predicts from named features, and captures itself
as canonical bytes -- the bytes are the trained model's identity, filed in an
artifact store and declared by the strategy that trades it. Three reference
models implement it in pure Python: :class:`RidgeRegression`,
:class:`LogisticRegression` and :class:`GradientBoostedTrees`. Adapters for
external frameworks live outside the core, in ``alphalab-models`` (ADR-0057).

Point-in-time research data (plan I.3): label makers -- a fixed horizon, a
triple barrier, meta-labels -- whose every label knows when it became known,
weights by label uniqueness, and :class:`PointInTimePanel`, which joins the
factor library's feature panels to labels, event by event, for research's
purged splits.

Beside them: feature pipelines (built directly from Feature Store, not
point-in-time), linear and logistic regression, k-fold and walk-forward
cross-validation, and regression/classification evaluation metrics.

Zero runtime dependencies -- no numpy. `alphalab.ml.linalg`'s four helpers and
the two index splitters, `k_fold_split` and `walk_forward_split`, are
deprecated since 4.3 and removed in 5.0 (GAP-48): each one's docstring names
its replacement in `common.linalg` or `research`, the library's one authority
for each (ADR-0058).
"""

from alphalab.ml.cross_validation import TrainTestSplit, k_fold_split, walk_forward_split
from alphalab.ml.dataset import (
    Dataset,
    FeatureSpec,
    build_dataset_as_of,
    build_dataset_from_feature_store,
)
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
from alphalab.ml.labels import (
    LABEL_SET_SCHEME,
    FixedHorizon,
    Label,
    LabelSet,
    RollingVolatility,
    TripleBarrier,
    VerticalOutcome,
    average_uniqueness,
    fixed_horizon_labels,
    meta_labels,
    triple_barrier_labels,
)
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
from alphalab.ml.point_in_time import (
    PANEL_SCHEME,
    PointInTimePanel,
    SampleWeights,
    point_in_time_panel,
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
    "LABEL_SET_SCHEME",
    "LOGISTIC_KIND",
    "MODEL_SCHEME",
    "PANEL_SCHEME",
    "RIDGE_KIND",
    "TRAINING_SET_SCHEME",
    "BoostingLoss",
    "Capture",
    "ConfusionMatrix",
    "Dataset",
    "FeatureMatrix",
    "FeatureSpec",
    "FixedHorizon",
    "GradientBoostedTrees",
    "Label",
    "LabelSet",
    "LinearModelFit",
    "LinearRegressionModel",
    "LogisticRegression",
    "LogisticRegressionModel",
    "MLComputationError",
    "MLError",
    "MLInputError",
    "Matrix",
    "Model",
    "PointInTimePanel",
    "RidgeRegression",
    "RollingVolatility",
    "SampleWeights",
    "TrainTestSplit",
    "TrainingRecord",
    "TrainingSet",
    "TreeEnsembleFit",
    "TreeLeaf",
    "TreeSplit",
    "TripleBarrier",
    "VerticalOutcome",
    "accuracy",
    "average_uniqueness",
    "build_dataset_as_of",
    "build_dataset_from_feature_store",
    "canonical_capture",
    "confusion_matrix",
    "f1_score",
    "file_model",
    "fixed_horizon_labels",
    "identity",
    "k_fold_split",
    "matmul",
    "matrix_inverse",
    "mean_absolute_error",
    "mean_squared_error",
    "meta_labels",
    "point_in_time_panel",
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
    "triple_barrier_labels",
    "walk_forward_split",
]
