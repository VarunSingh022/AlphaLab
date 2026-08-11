"""Comprehensive tests for the Machine Learning Engine: linalg, regression,
classification, cross-validation, evaluation, and the Feature Store bridge."""

import pytest

from alphalab.feature_store import (
    FeatureMetadata,
    FeatureRegistry,
    FeatureStoreEngine,
    FeatureType,
    FeatureValue,
    FeatureValueStore,
    FeatureValueType,
)
from alphalab.feature_store.state import FeatureStoreState
from alphalab.ml import (
    ConfusionMatrix,
    Dataset,
    LinearRegressionModel,
    LogisticRegressionModel,
    MLComputationError,
    MLInputError,
    accuracy,
    build_dataset_from_feature_store,
    confusion_matrix,
    f1_score,
    identity,
    k_fold_split,
    matmul,
    matrix_inverse,
    mean_absolute_error,
    mean_squared_error,
    precision,
    predict_linear,
    predict_logistic,
    predict_proba_logistic,
    r_squared,
    recall,
    root_mean_squared_error,
    train_linear_regression,
    train_logistic_regression,
    transpose,
    walk_forward_split,
)

# --------------------------------------------------------------------------- #
# linalg
# --------------------------------------------------------------------------- #


def test_matrix_inverse_matches_hand_computed_2x2() -> None:
    m = ((4.0, 7.0), (2.0, 6.0))
    inv = matrix_inverse(m)
    assert inv[0][0] == pytest.approx(0.6)
    assert inv[0][1] == pytest.approx(-0.7)
    assert inv[1][0] == pytest.approx(-0.2)
    assert inv[1][1] == pytest.approx(0.4)


def test_matrix_inverse_product_is_identity() -> None:
    m = ((4.0, 7.0), (2.0, 6.0))
    product = matmul(m, matrix_inverse(m))
    assert product[0][0] == pytest.approx(1.0)
    assert product[0][1] == pytest.approx(0.0)
    assert product[1][0] == pytest.approx(0.0)
    assert product[1][1] == pytest.approx(1.0)


def test_matrix_inverse_raises_on_singular_matrix() -> None:
    with pytest.raises(MLComputationError):
        matrix_inverse(((1.0, 2.0), (2.0, 4.0)))


def test_matrix_inverse_raises_on_non_square() -> None:
    with pytest.raises(MLInputError):
        matrix_inverse(((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)))


def test_transpose() -> None:
    result = transpose(((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)))
    assert result == ((1.0, 4.0), (2.0, 5.0), (3.0, 6.0))


def test_matmul_rejects_mismatched_dimensions() -> None:
    with pytest.raises(MLInputError):
        matmul(((1.0, 2.0),), ((1.0, 2.0),))


def test_identity_matrix() -> None:
    assert identity(2) == ((1.0, 0.0), (0.0, 1.0))


def test_identity_rejects_non_positive_n() -> None:
    with pytest.raises(MLInputError):
        identity(0)


# --------------------------------------------------------------------------- #
# Linear regression: exact recovery on clean signals
# --------------------------------------------------------------------------- #


def test_linear_regression_recovers_exact_line() -> None:
    x = ((1.0,), (2.0,), (3.0,), (4.0,))
    y = (5.0, 7.0, 9.0, 11.0)  # y = 2x + 3
    model = train_linear_regression(("x",), x, y)
    assert model.coefficients[0] == pytest.approx(2.0)
    assert model.intercept == pytest.approx(3.0)


def test_linear_regression_recovers_multi_feature_relationship() -> None:
    x = ((1.0, 1.0), (2.0, 1.0), (1.0, 2.0), (3.0, 2.0))
    y = (3.5, 4.5, 5.5, 7.5)  # y = x1 + 2*x2 + 0.5
    model = train_linear_regression(("x1", "x2"), x, y)
    assert model.coefficients[0] == pytest.approx(1.0, abs=1e-6)
    assert model.coefficients[1] == pytest.approx(2.0, abs=1e-6)
    assert model.intercept == pytest.approx(0.5, abs=1e-6)


def test_predict_linear_matches_training_relationship() -> None:
    x = ((1.0,), (2.0,), (3.0,), (4.0,))
    y = (5.0, 7.0, 9.0, 11.0)
    model = train_linear_regression(("x",), x, y)
    predictions = predict_linear(model, ((5.0,), (10.0,)))
    assert predictions[0] == pytest.approx(13.0)
    assert predictions[1] == pytest.approx(23.0)


def test_linear_regression_ridge_penalty_shrinks_coefficients() -> None:
    x = ((1.0,), (2.0,), (3.0,), (4.0,))
    y = (5.0, 7.0, 9.0, 11.0)
    unregularized = train_linear_regression(("x",), x, y, l2_penalty=0.0)
    regularized = train_linear_regression(("x",), x, y, l2_penalty=10.0)
    assert abs(regularized.coefficients[0]) < abs(unregularized.coefficients[0])


def test_linear_regression_raises_on_mismatched_sample_counts() -> None:
    with pytest.raises(MLInputError):
        train_linear_regression(("x",), ((1.0,), (2.0,)), (1.0,))


def test_linear_regression_raises_on_negative_l2_penalty() -> None:
    with pytest.raises(MLInputError):
        train_linear_regression(("x",), ((1.0,),), (1.0,), l2_penalty=-1.0)


def test_predict_linear_raises_on_feature_count_mismatch() -> None:
    model = LinearRegressionModel(feature_names=("x",), coefficients=(2.0,), intercept=1.0)
    with pytest.raises(MLInputError):
        predict_linear(model, ((1.0, 2.0),))


def test_linear_regression_model_is_immutable() -> None:
    model = train_linear_regression(("x",), ((1.0,), (2.0,)), (1.0, 2.0))
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        model.intercept = 99.0  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Logistic regression: perfect classification on separable data, determinism
# --------------------------------------------------------------------------- #


def _separable_data() -> tuple[tuple[tuple[float, ...], ...], tuple[int, ...]]:
    x = ((1.0,), (2.0,), (3.0,), (4.0,), (6.0,), (7.0,), (8.0,), (9.0,))
    y = (0, 0, 0, 0, 1, 1, 1, 1)
    return x, y


def test_logistic_regression_perfectly_classifies_separable_data() -> None:
    x, y = _separable_data()
    model = train_logistic_regression(("x",), x, y, learning_rate=0.5, iterations=2000)
    predictions = predict_logistic(model, x)
    assert predictions == y


def test_logistic_regression_is_deterministic() -> None:
    x, y = _separable_data()
    model_a = train_logistic_regression(("x",), x, y, learning_rate=0.5, iterations=500)
    model_b = train_logistic_regression(("x",), x, y, learning_rate=0.5, iterations=500)
    assert model_a.weights == model_b.weights
    assert model_a.bias == model_b.bias


def test_predict_proba_extreme_points_are_confident() -> None:
    x, y = _separable_data()
    model = train_logistic_regression(("x",), x, y, learning_rate=0.5, iterations=2000)
    probs = predict_proba_logistic(model, ((0.5,), (10.0,)))
    assert probs[0] < 0.01
    assert probs[1] > 0.99


def test_logistic_regression_rejects_non_binary_labels() -> None:
    with pytest.raises(MLInputError):
        train_logistic_regression(("x",), ((1.0,), (2.0,)), (0, 2))


def test_logistic_regression_rejects_non_positive_learning_rate() -> None:
    with pytest.raises(MLInputError):
        train_logistic_regression(("x",), ((1.0,), (2.0,)), (0, 1), learning_rate=0.0)


def test_predict_logistic_rejects_invalid_threshold() -> None:
    model = LogisticRegressionModel(
        feature_names=("x",), weights=(1.0,), bias=0.0, iterations=10, learning_rate=0.1
    )
    with pytest.raises(MLInputError):
        predict_logistic(model, ((1.0,),), threshold=1.5)


# --------------------------------------------------------------------------- #
# Cross-validation
# --------------------------------------------------------------------------- #


def test_k_fold_split_partitions_all_indices_exactly_once() -> None:
    splits = k_fold_split(10, 5)
    all_test_indices = sorted(idx for _, test in splits for idx in test)
    assert all_test_indices == list(range(10))


def test_k_fold_split_train_and_test_never_overlap() -> None:
    splits = k_fold_split(10, 5)
    for train, test in splits:
        assert set(train).isdisjoint(set(test))


def test_k_fold_split_rejects_k_below_two() -> None:
    with pytest.raises(MLInputError):
        k_fold_split(10, 1)


def test_k_fold_split_rejects_k_exceeding_n_samples() -> None:
    with pytest.raises(MLInputError):
        k_fold_split(3, 5)


def test_walk_forward_split_never_leaks_future_into_training_expanding() -> None:
    """The entire reason this function exists: no train index may exceed any test index."""
    splits = walk_forward_split(20, n_splits=3, min_train_size=10, expanding=True)
    for train, test in splits:
        assert max(train) < min(test)


def test_walk_forward_split_never_leaks_future_into_training_rolling() -> None:
    splits = walk_forward_split(20, n_splits=3, min_train_size=10, expanding=False)
    for train, test in splits:
        assert max(train) < min(test)


def test_walk_forward_split_expanding_window_grows() -> None:
    splits = walk_forward_split(20, n_splits=3, min_train_size=10, expanding=True)
    train_sizes = [len(train) for train, _ in splits]
    assert train_sizes == sorted(train_sizes)
    assert train_sizes[0] < train_sizes[-1]


def test_walk_forward_split_rolling_window_is_bounded() -> None:
    splits = walk_forward_split(20, n_splits=3, min_train_size=10, expanding=False)
    assert all(len(train) <= 10 for train, _ in splits)


def test_walk_forward_split_raises_with_insufficient_data() -> None:
    with pytest.raises(MLInputError):
        walk_forward_split(10, n_splits=5, min_train_size=8)


# --------------------------------------------------------------------------- #
# Evaluation: regression
# --------------------------------------------------------------------------- #


def test_mean_squared_error_matches_hand_computed_value() -> None:
    assert mean_squared_error((1.0, 2.0, 3.0), (1.0, 2.0, 4.0)) == pytest.approx(1 / 3)


def test_root_mean_squared_error_is_sqrt_of_mse() -> None:
    mse = mean_squared_error((1.0, 2.0, 3.0), (1.0, 2.0, 4.0))
    assert root_mean_squared_error((1.0, 2.0, 3.0), (1.0, 2.0, 4.0)) == pytest.approx(mse**0.5)


def test_mean_absolute_error_matches_hand_computed_value() -> None:
    assert mean_absolute_error((1.0, 2.0, 3.0), (1.0, 2.0, 4.0)) == pytest.approx(1 / 3)


def test_r_squared_perfect_fit_is_one() -> None:
    assert r_squared((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == pytest.approx(1.0)


def test_r_squared_raises_when_y_true_is_constant() -> None:
    with pytest.raises(MLInputError):
        r_squared((5.0, 5.0, 5.0), (1.0, 2.0, 3.0))


def test_regression_metrics_reject_empty_input() -> None:
    with pytest.raises(MLInputError):
        mean_squared_error((), ())


def test_regression_metrics_reject_mismatched_lengths() -> None:
    with pytest.raises(MLInputError):
        mean_squared_error((1.0, 2.0), (1.0,))


# --------------------------------------------------------------------------- #
# Evaluation: classification
# --------------------------------------------------------------------------- #


def test_confusion_matrix_matches_hand_computed_value() -> None:
    y_true = (1, 1, 1, 0, 0)
    y_pred = (1, 0, 1, 0, 1)
    cm = confusion_matrix(y_true, y_pred)
    assert cm == ConfusionMatrix(
        true_positives=2, true_negatives=1, false_positives=1, false_negatives=1
    )


def test_accuracy_matches_hand_computed_value() -> None:
    assert accuracy((1, 1, 1, 0, 0), (1, 0, 1, 0, 1)) == pytest.approx(0.6)


def test_precision_matches_hand_computed_value() -> None:
    assert precision((1, 1, 1, 0, 0), (1, 0, 1, 0, 1)) == pytest.approx(2 / 3)


def test_recall_matches_hand_computed_value() -> None:
    assert recall((1, 1, 1, 0, 0), (1, 0, 1, 0, 1)) == pytest.approx(2 / 3)


def test_f1_score_matches_hand_computed_value() -> None:
    p, r = 2 / 3, 2 / 3
    expected = 2 * p * r / (p + r)
    assert f1_score((1, 1, 1, 0, 0), (1, 0, 1, 0, 1)) == pytest.approx(expected)


def test_precision_returns_zero_with_no_positive_predictions() -> None:
    assert precision((1, 0, 1), (0, 0, 0)) == 0.0


def test_recall_returns_zero_with_no_actual_positives() -> None:
    assert recall((0, 0, 0), (1, 0, 1)) == 0.0


def test_confusion_matrix_rejects_non_binary_labels() -> None:
    with pytest.raises(MLInputError):
        confusion_matrix((1, 2, 0), (0, 1, 0))


# --------------------------------------------------------------------------- #
# Feature Store bridge: real end-to-end pipeline, not just typed
# --------------------------------------------------------------------------- #


def _feature_store_with_data() -> tuple[FeatureStoreState, tuple[str, ...]]:
    state = FeatureStoreEngine.initialize("ML-TEST")
    mom_meta = FeatureMetadata(
        feature_id="momentum",
        name="Momentum",
        version=1,
        feature_type=FeatureType.PRICE,
        value_type=FeatureValueType.FLOAT,
        owner="q",
        description="d",
    )
    target_meta = FeatureMetadata(
        feature_id="fwd_return",
        name="Fwd Return",
        version=1,
        feature_type=FeatureType.DERIVED,
        value_type=FeatureValueType.FLOAT,
        owner="q",
        description="d",
    )
    state = FeatureRegistry.register(state, mom_meta, 0.0)
    state = FeatureRegistry.register(state, target_meta, 0.0)

    assets = ("AAPL", "MSFT", "GOOG", "TSLA")
    data = {
        "AAPL": (0.05, 0.10),
        "MSFT": (0.02, 0.04),
        "GOOG": (0.08, 0.16),
        "TSLA": (-0.01, -0.02),
    }
    for asset, (mom, ret) in data.items():
        state, _ = FeatureValueStore.write(state, FeatureValue("momentum", 1, asset, mom, 0.0), 0.0)
        state, _ = FeatureValueStore.write(
            state, FeatureValue("fwd_return", 1, asset, ret, 0.0), 0.0
        )
    return state, assets


def test_build_dataset_from_feature_store_produces_correct_shape() -> None:
    state, assets = _feature_store_with_data()
    dataset = build_dataset_from_feature_store(state, (("momentum", 1),), ("fwd_return", 1), assets)
    assert isinstance(dataset, Dataset)
    assert len(dataset.x) == 4
    assert len(dataset.y) == 4
    assert dataset.asset_ids == assets


def test_dataset_from_feature_store_feeds_real_linear_regression() -> None:
    """End-to-end: Feature Store -> Dataset -> trained model recovering a known
    exact relationship (fwd_return = 2 * momentum)."""
    state, assets = _feature_store_with_data()
    dataset = build_dataset_from_feature_store(state, (("momentum", 1),), ("fwd_return", 1), assets)
    model = train_linear_regression(dataset.feature_names, dataset.x, dataset.y)
    assert model.coefficients[0] == pytest.approx(2.0, abs=1e-6)


def test_build_dataset_drops_assets_with_missing_features() -> None:
    state, assets = _feature_store_with_data()
    result = build_dataset_from_feature_store(
        state, (("momentum", 1),), ("fwd_return", 1), (*assets, "NONEXISTENT")
    )
    assert len(result.x) == 4
    assert "NONEXISTENT" not in result.asset_ids


def test_build_dataset_raises_when_no_asset_has_complete_data() -> None:
    state, _ = _feature_store_with_data()
    with pytest.raises(MLInputError):
        build_dataset_from_feature_store(state, (("momentum", 1),), ("fwd_return", 1), ("NOWHERE",))


def test_build_dataset_rejects_empty_feature_specs() -> None:
    state, assets = _feature_store_with_data()
    with pytest.raises(MLInputError):
        build_dataset_from_feature_store(state, (), ("fwd_return", 1), assets)
