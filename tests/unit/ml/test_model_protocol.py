"""The research model protocol: a trained model is its bytes (v4.3, ADR-0064).

* **Conformance**: every reference model fits purely, refuses to predict or
  capture before it is fitted, refuses rows naming other features, and
  restores from its capture to an equal model with the same bytes.
* **A training set's identity is its values**: names, rows, labels and
  weights, each by its shortest ``repr``; what a set cannot hold is refused.
* **Canonical capture**: a restore refuses bytes that parse but are not the
  model's own capture, another kind's bytes, another scheme and non-finite
  values.
* **The artifact binding**: a filed model is named by its digest, restored
  through the store's verification, and its training recorded beside it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from alphalab.ml.exceptions import MLInputError
from alphalab.ml.models import (
    BoostingLoss,
    GradientBoostedTrees,
    LogisticRegression,
    RidgeRegression,
)
from alphalab.ml.protocol import (
    MODEL_SCHEME,
    FeatureMatrix,
    Model,
    TrainingRecord,
    TrainingSet,
    canonical_capture,
    file_model,
    read_capture,
    restore_model,
    train_model,
)
from alphalab.model_registry.artifact_store import (
    ARTIFACT_URI_SCHEME,
    FileArtifactStore,
    MemoryArtifactStore,
    compute_digest,
)
from alphalab.persistence.exceptions import StorageError

NAMES = ("momentum", "value")
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
REGRESSION_LABELS = [0.75, 1.5, -0.5, 2.0, 0.25, -1.75, 2.5, -1.0]
CLASS_LABELS = [1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0]


def _ridge() -> RidgeRegression:
    return RidgeRegression(penalty=0.5, intercept=True, maximum_condition=1e8)


def _logistic() -> LogisticRegression:
    return LogisticRegression(
        penalty=1.0, intercept=True, tolerance=1e-10, max_iterations=50, maximum_condition=1e8
    )


def _trees() -> GradientBoostedTrees:
    return GradientBoostedTrees(
        loss=BoostingLoss.SQUARED,
        trees=4,
        learning_rate=0.5,
        depth=2,
        minimum_leaf=1,
        subsample=1.0,
    )


def _boosted_classifier() -> GradientBoostedTrees:
    return replace(_trees(), loss=BoostingLoss.LOGISTIC)


MODELS: list[tuple[str, Callable[[], Model], list[float]]] = [
    ("ridge", _ridge, REGRESSION_LABELS),
    ("logistic", _logistic, CLASS_LABELS),
    ("trees", _trees, REGRESSION_LABELS),
    ("boosted classifier", _boosted_classifier, CLASS_LABELS),
]
IDS = [name for name, _, _ in MODELS]


@pytest.mark.parametrize(("name", "make", "labels"), MODELS, ids=IDS)
def test_a_fit_is_pure_and_its_capture_restores_the_same_model(
    name: str, make: Callable[[], Model], labels: list[float]
) -> None:
    model = make()
    fitted = model.fit(TrainingSet.of(NAMES, ROWS, labels), seed=7)
    assert model == make(), "fit mutated the model it was called on"
    assert type(fitted) is type(model)
    artifact = fitted.capture()
    again = type(fitted).restore(artifact)
    assert again == fitted
    assert again.capture() == artifact
    assert dict(again.hyperparameters()) == dict(model.hyperparameters())
    matrix = FeatureMatrix.of(NAMES, ROWS)
    assert again.predict(matrix) == fitted.predict(matrix)
    assert len(fitted.predict(matrix)) == len(ROWS)
    assert fitted.predict(FeatureMatrix(NAMES, ())) == ()


@pytest.mark.parametrize(("name", "make", "labels"), MODELS, ids=IDS)
def test_an_unfitted_model_neither_predicts_nor_captures(
    name: str, make: Callable[[], Model], labels: list[float]
) -> None:
    model = make()
    with pytest.raises(MLInputError, match="not fitted"):
        model.predict(FeatureMatrix.of(NAMES, ROWS))
    with pytest.raises(MLInputError, match="not fitted"):
        model.capture()


@pytest.mark.parametrize(("name", "make", "labels"), MODELS, ids=IDS)
def test_rows_naming_other_features_or_another_order_are_refused(
    name: str, make: Callable[[], Model], labels: list[float]
) -> None:
    fitted = make().fit(TrainingSet.of(NAMES, ROWS, labels), seed=0)
    swapped = FeatureMatrix.of(tuple(reversed(NAMES)), [tuple(reversed(r)) for r in ROWS])
    with pytest.raises(MLInputError, match="in that order"):
        fitted.predict(swapped)
    with pytest.raises(MLInputError, match="in that order"):
        fitted.predict(FeatureMatrix.of(("momentum", "size"), ROWS))
    with pytest.raises(MLInputError, match="FeatureMatrix"):
        fitted.predict(ROWS)  # type: ignore[arg-type]


@pytest.mark.parametrize(("name", "make", "labels"), MODELS, ids=IDS)
def test_every_hyperparameter_is_a_parameter_value(
    name: str, make: Callable[[], Model], labels: list[float]
) -> None:
    for key, value in make().hyperparameters().items():
        assert isinstance(key, str) and key
        assert isinstance(value, str | int | float | bool), (key, value)


def _altered(artifact: bytes) -> list[tuple[str, bytes]]:
    document = json.loads(artifact)
    pretty = json.dumps(document, sort_keys=True, indent=1).encode("ascii")
    unsorted = json.dumps(dict(reversed(list(document.items()))), separators=(",", ":"))
    return [
        ("whitespace", pretty),
        ("key order", unsorted.encode("ascii")),
        ("trailing newline", artifact + b"\n"),
        ("another scheme", artifact.replace(MODEL_SCHEME.encode(), b"alphalab.model.v0")),
        ("not json", artifact[:-1]),
        ("not ascii", artifact.replace(b'"features"', '"féatures"'.encode())),
    ]


@pytest.mark.parametrize(("name", "make", "labels"), MODELS, ids=IDS)
def test_bytes_that_are_not_the_models_canonical_capture_are_refused(
    name: str, make: Callable[[], Model], labels: list[float]
) -> None:
    fitted = make().fit(TrainingSet.of(NAMES, ROWS, labels), seed=0)
    artifact = fitted.capture()
    for what, bytes_ in _altered(artifact):
        assert bytes_ != artifact, what
        with pytest.raises(MLInputError):
            type(fitted).restore(bytes_)


def test_a_float_rendered_any_other_way_is_not_the_same_artifact() -> None:
    fitted = _ridge().fit(TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS), seed=0)
    artifact = fitted.capture()
    assert b'"penalty":0.5' in artifact
    with pytest.raises(MLInputError, match="not its canonical capture"):
        RidgeRegression.restore(artifact.replace(b'"penalty":0.5', b'"penalty":0.50'))
    with pytest.raises(MLInputError, match="not a finite float"):
        RidgeRegression.restore(artifact.replace(b'"penalty":0.5', b'"penalty":1'))
    with pytest.raises(MLInputError, match="not a finite number"):
        RidgeRegression.restore(artifact.replace(b'"penalty":0.5', b'"penalty":NaN'))


def test_another_kinds_bytes_are_refused() -> None:
    training = TrainingSet.of(NAMES, ROWS, CLASS_LABELS)
    ridge = _ridge().fit(training, seed=0).capture()
    logistic = _logistic().fit(training, seed=0).capture()
    with pytest.raises(MLInputError, match="not a 'alphalab:logistic'"):
        LogisticRegression.restore(ridge)
    with pytest.raises(MLInputError, match="not a 'alphalab:ridge'"):
        RidgeRegression.restore(logistic)
    with pytest.raises(MLInputError, match="not a 'alphalab:gradient-boosted-trees'"):
        GradientBoostedTrees.restore(ridge)


def test_a_capture_must_hold_exactly_its_keys_and_parameter_values() -> None:
    fitted = _ridge().fit(TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS), seed=0)
    document = json.loads(fitted.capture())

    def encoded(changed: dict[str, object]) -> bytes:
        return json.dumps(changed, sort_keys=True, separators=(",", ":")).encode("ascii")

    with pytest.raises(MLInputError, match="exactly"):
        RidgeRegression.restore(encoded({**document, "extra": 1}))
    with pytest.raises(MLInputError, match="parameter values"):
        RidgeRegression.restore(
            encoded({**document, "hyperparameters": {**document["hyperparameters"], "x": [1]}})
        )
    with pytest.raises(MLInputError, match="states"):
        RidgeRegression.restore(
            encoded({**document, "hyperparameters": {"penalty": 0.5, "intercept": True}})
        )
    with pytest.raises(MLInputError, match="state is not a mapping"):
        RidgeRegression.restore(encoded({**document, "state": [1.0]}))
    with pytest.raises(MLInputError, match="repeats a feature"):
        RidgeRegression.restore(encoded({**document, "features": ["a", "a"]}))
    with pytest.raises(MLInputError, match="artifact is bytes"):
        read_capture("text", "alphalab:ridge")  # type: ignore[arg-type]


def test_a_capture_holding_a_value_that_is_not_finite_is_refused() -> None:
    with pytest.raises(MLInputError, match="cannot be captured"):
        canonical_capture("alphalab:ridge", {}, ("a",), {"intercept": float("inf")})


def test_a_training_sets_identity_is_its_values_in_order() -> None:
    base = TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS)
    assert base.training_id == TrainingSet.of(list(NAMES), ROWS, REGRESSION_LABELS).training_id
    assert TrainingSet.of(NAMES, [(1, 2)], [3]).training_id == (
        TrainingSet.of(NAMES, [(1.0, 2.0)], [3.0]).training_id
    )
    changed = [
        TrainingSet.of(("momentum", "carry"), ROWS, REGRESSION_LABELS),
        TrainingSet.of(NAMES, [*ROWS[:-1], (-2.25, 1.5000000000000002)], REGRESSION_LABELS),
        TrainingSet.of(NAMES, ROWS, [*REGRESSION_LABELS[:-1], -1.5]),
        TrainingSet.of(NAMES, list(reversed(ROWS)), list(reversed(REGRESSION_LABELS))),
        TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS, [1.0] * len(ROWS)),
        TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS, [1.0] * 7 + [2.0]),
    ]
    identities = [base.training_id, *(training.training_id for training in changed)]
    assert len(set(identities)) == len(identities)
    lines = [
        "alphalab.training_set.v1",
        json.dumps(list(NAMES)),
        *(f"{a!r},{b!r}|{y!r}" for (a, b), y in zip(ROWS, REGRESSION_LABELS, strict=True)),
    ]
    assert base.training_id == hashlib.sha256("\n".join(lines).encode()).hexdigest()


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: TrainingSet.of(NAMES, [], []), "at least one row"),
        (lambda: TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS[:-1]), "8 rows and 7 labels"),
        (lambda: TrainingSet.of(NAMES, [(1.0,)], [1.0]), "1 values for 2 features"),
        (lambda: TrainingSet.of(NAMES, [(1.0, float("nan"))], [1.0]), "finite number"),
        (lambda: TrainingSet.of(NAMES, [(1.0, 2.0)], [float("inf")]), "finite number"),
        (lambda: TrainingSet.of(NAMES, [(1.0, True)], [1.0]), "finite number"),
        (lambda: TrainingSet.of(NAMES, [(1.0, 2**60 + 1)], [1.0]), "no float holds exactly"),
        (lambda: TrainingSet.of(NAMES, [(1.0, 2.0)], [1.0], [0.0]), "positive"),
        (lambda: TrainingSet.of(NAMES, [(1.0, 2.0)], [1.0], [1.0, 1.0]), "1 rows and 2 weights"),
        (lambda: TrainingSet.of(("a", "a"), [(1.0, 2.0)], [1.0]), "repeats a feature"),
        (lambda: TrainingSet.of(("a", " b"), [(1.0, 2.0)], [1.0]), "not a name"),
        (lambda: TrainingSet.of((), [()], [1.0]), "names no feature"),
        (lambda: FeatureMatrix("ab", ()), "sequence of names"),  # type: ignore[arg-type]
        (lambda: TrainingSet(ROWS, (1.0,)), "FeatureMatrix"),  # type: ignore[arg-type]
    ],
)
def test_what_a_training_set_cannot_hold_is_refused(
    build: Callable[[], object], match: str
) -> None:
    with pytest.raises(MLInputError, match=match):
        build()


def test_a_feature_matrix_reads_a_column_by_name() -> None:
    matrix = FeatureMatrix.of(NAMES, ROWS)
    assert matrix.column("value") == tuple(row[1] for row in ROWS)
    assert len(matrix) == len(ROWS)
    with pytest.raises(MLInputError, match="no feature 'size'"):
        matrix.column("size")


def test_a_filed_model_is_named_by_its_digest_and_restored_through_the_store() -> None:
    store = MemoryArtifactStore()
    training = TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS)
    fitted, record = train_model(_ridge(), training, seed=11, store=store)
    artifact = fitted.capture()
    assert record == TrainingRecord(
        artifact_id=f"{ARTIFACT_URI_SCHEME}{compute_digest(artifact)}",
        model_kind="alphalab:ridge",
        hyperparameters=record.hyperparameters,
        seed=11,
        training_id=training.training_id,
    )
    assert dict(record.hyperparameters) == dict(_ridge().hyperparameters())
    assert file_model(fitted, store) == record.artifact_id, "the same bytes are the same model"
    restored = restore_model(RidgeRegression, store, record.artifact_id)
    assert restored == fitted
    assert restored.capture() == artifact


def test_two_trainings_with_the_same_bytes_are_one_model_whatever_the_seed() -> None:
    store = MemoryArtifactStore()
    training = TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS)
    _, first = train_model(_ridge(), training, seed=1, store=store)
    _, second = train_model(_ridge(), training, seed=2, store=store)
    assert first.artifact_id == second.artifact_id
    assert (first.seed, second.seed) == (1, 2)


def test_bytes_altered_in_the_store_are_refused_before_any_model_reads_them(
    tmp_path: Path,
) -> None:
    store = FileArtifactStore(tmp_path)
    training = TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS)
    _, record = train_model(_ridge(), training, seed=0, store=store)
    (path,) = tmp_path.rglob("*.artifact")
    other = replace(_ridge(), penalty=2.0).fit(training, seed=0)
    path.write_bytes(other.capture())
    with pytest.raises(StorageError, match="does not match the digest"):
        restore_model(RidgeRegression, store, record.artifact_id)


def test_a_restore_refuses_an_absent_artifact_another_kind_and_a_bare_digest() -> None:
    store = MemoryArtifactStore()
    training = TrainingSet.of(NAMES, ROWS, REGRESSION_LABELS)
    fitted, record = train_model(_ridge(), training, seed=0, store=store)
    other = replace(_ridge(), penalty=2.0).fit(training, seed=0)
    absent = f"{ARTIFACT_URI_SCHEME}{compute_digest(other.capture())}"
    with pytest.raises(StorageError, match="No artifact is stored"):
        restore_model(RidgeRegression, store, absent)
    with pytest.raises(MLInputError, match="not a 'alphalab:logistic'"):
        restore_model(LogisticRegression, store, record.artifact_id)
    with pytest.raises(MLInputError, match="artifact id"):
        restore_model(RidgeRegression, store, compute_digest(fitted.capture()))
    with pytest.raises(MLInputError, match="seed is an integer"):
        train_model(_ridge(), training, seed=True, store=store)
