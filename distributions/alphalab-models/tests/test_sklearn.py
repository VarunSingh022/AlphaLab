"""scikit-learn estimators as research models (ADR-0057, ADR-0064 section 4).

* **Conformance**: a ridge, a logistic regression and both gradient-boosting
  models fit a clone, never the estimator given; predict from the features they
  were fitted on and refuse others; and restore from their capture to an equal
  model with the same bytes and the same predictions.
* **A model is its bytes**: the fit's seed reaches ``random_state``, so the same
  training gives the same artifact and another seed another; a restored model
  captures as the artifact it came from, though scikit-learn's pickles do not
  round-trip byte for byte.
* **The binding**: trained, filed and restored through an artifact store.
* **What a restore cannot take is refused**: another scikit-learn or adapter
  version, another scheme, a payload whose digest is not the header's, a
  payload of another kind, a header that is not canonical.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
from collections.abc import Callable
from typing import Any

import pytest
from alphalab_models.sklearn import ADAPTER_VERSION, RANDOM_STATES, SKLEARN_SCHEME, SklearnModel
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.neighbors import KNeighborsRegressor

from alphalab.ml import FeatureMatrix, TrainingSet, restore_model, train_model
from alphalab.ml.exceptions import MLInputError
from alphalab.model_registry.artifact_store import MemoryArtifactStore

NAMES = ("carry", "momentum", "value")


def _data(classes: bool) -> TrainingSet:
    state = 4417
    rows = []
    labels = []
    for _ in range(120):
        values = []
        for _ in NAMES:
            state = (1103515245 * state + 12345) % 2**31
            values.append(((state >> 8) % 64 - 32) / 16)
        state = (1103515245 * state + 12345) % 2**31
        noise = ((state >> 8) % 512 - 256) / 256
        target = noise + values[0] / 2 - values[1] / 4
        rows.append(tuple(values))
        labels.append((1.0 if target > 0 else 0.0) if classes else target)
    return TrainingSet.of(NAMES, rows, labels)


MODELS: list[tuple[str, Callable[[], Any], bool]] = [
    ("ridge", lambda: Ridge(alpha=0.5), False),
    ("logistic", lambda: LogisticRegression(C=1.0), True),
    ("boosted regressor", lambda: GradientBoostingRegressor(n_estimators=20, subsample=0.8), False),
    (
        "boosted classifier",
        lambda: GradientBoostingClassifier(n_estimators=20, subsample=0.8),
        True,
    ),
]
IDS = [name for name, _, _ in MODELS]


@pytest.mark.parametrize(("name", "make", "classes"), MODELS, ids=IDS)
def test_a_fit_is_a_clone_and_its_capture_restores_the_same_model(
    name: str, make: Callable[[], Any], classes: bool
) -> None:
    estimator = make()
    model = SklearnModel(estimator)
    training = _data(classes)
    fitted = model.fit(training, seed=7)
    assert not hasattr(estimator, "n_features_in_"), "the estimator given is never fitted"
    assert fitted.model_kind == f"sklearn:{type(estimator).__name__}"
    assert fitted.features == NAMES
    artifact = fitted.capture()
    again = SklearnModel.restore(artifact)
    assert again == fitted and again.capture() == artifact
    matrix = FeatureMatrix(NAMES, training.features.rows[:5])
    assert again.predict(matrix) == fitted.predict(matrix)
    assert len(fitted.predict(matrix)) == 5
    if classes:
        assert all(0.0 <= p <= 1.0 for p in fitted.predict(matrix))
    assert fitted.predict(FeatureMatrix(NAMES, ())) == ()


@pytest.mark.parametrize(("name", "make", "classes"), MODELS, ids=IDS)
def test_rows_naming_other_features_and_an_unfitted_model_are_refused(
    name: str, make: Callable[[], Any], classes: bool
) -> None:
    fitted = SklearnModel(make()).fit(_data(classes), seed=0)
    rows = _data(classes).features.rows[:2]
    with pytest.raises(MLInputError, match="in that order"):
        fitted.predict(FeatureMatrix(tuple(reversed(NAMES)), rows))
    unfitted = SklearnModel(make())
    with pytest.raises(MLInputError, match="not fitted"):
        unfitted.predict(FeatureMatrix(NAMES, rows))
    with pytest.raises(MLInputError, match="not fitted"):
        unfitted.capture()


def test_the_seed_reaches_random_state_and_the_same_training_is_the_same_artifact() -> None:
    model = SklearnModel(GradientBoostingClassifier(n_estimators=20, subsample=0.8))
    training = _data(True)
    first = model.fit(training, seed=1).capture()
    assert model.fit(training, seed=1).capture() == first, "a deterministic fit, the same bytes"
    assert model.fit(training, seed=2).capture() != first, "the subsample follows the seed"
    assert "random_state" in Ridge().get_params(), "Ridge's sag solvers draw: it takes the seed"
    plain = SklearnModel(LinearRegression())
    assert plain.fit(_data(False), seed=1).capture() == plain.fit(_data(False), seed=2).capture(), (
        "an estimator with no random_state is the same bytes whatever the seed"
    )


def test_a_ledgers_64_bit_seed_enters_scikit_learns_range_modulo_2_to_the_32() -> None:
    """The trial ledger draws 64-bit seeds; scikit-learn's random_state stops at 2**32."""

    model = SklearnModel(GradientBoostingClassifier(n_estimators=10, subsample=0.8))
    training = _data(True)
    wide = 10725776635129204304
    fitted = model.fit(training, seed=wide)
    assert fitted.capture() == model.fit(training, seed=wide % RANDOM_STATES).capture()
    assert fitted.capture() != model.fit(training, seed=wide + 1).capture()
    assert model.fit(training, seed=-1).capture() == model.fit(training, seed=2**32 - 1).capture()


def test_a_restored_model_captures_as_the_artifact_it_came_from() -> None:
    """scikit-learn's pickles do not round-trip byte for byte; the artifact does."""

    fitted = SklearnModel(GradientBoostingRegressor(n_estimators=10)).fit(_data(False), seed=3)
    artifact = fitted.capture()
    _, _, payload = artifact.partition(b"\n")
    repickled = pickle.dumps(pickle.loads(payload), protocol=5)
    assert repickled != payload, "the reason the adapter keeps its payload"
    assert SklearnModel.restore(artifact).capture() == artifact


def test_a_model_is_trained_filed_and_restored_through_an_artifact_store() -> None:
    store = MemoryArtifactStore()
    training = _data(True)
    fitted, record = train_model(
        SklearnModel(GradientBoostingClassifier(n_estimators=10)), training, seed=5, store=store
    )
    assert record.model_kind == "sklearn:GradientBoostingClassifier"
    assert record.training_id == training.training_id and record.seed == 5
    restored = restore_model(SklearnModel, store, record.artifact_id)
    assert restored == fitted and restored.capture() == fitted.capture()


def test_every_parameter_is_declared_as_a_value_or_its_repr() -> None:
    parameters = SklearnModel(GradientBoostingClassifier(max_depth=None)).hyperparameters()
    assert set(parameters) == set(GradientBoostingClassifier().get_params(deep=False))
    assert parameters["max_depth"] == "None"
    assert parameters["n_estimators"] == 100
    assert all(isinstance(value, str | int | float | bool) for value in parameters.values())


def _rewritten(artifact: bytes, **changes: object) -> bytes:
    line, _, payload = artifact.partition(b"\n")
    header = {**json.loads(line), **changes}
    return (
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n" + payload
    )


def test_what_a_restore_cannot_take_is_refused() -> None:
    artifact = SklearnModel(Ridge(alpha=0.5)).fit(_data(False), seed=0).capture()
    line, _, payload = artifact.partition(b"\n")
    with pytest.raises(MLInputError, match=r"captured under scikit-learn 0\.0"):
        SklearnModel.restore(_rewritten(artifact, sklearn="0.0"))
    with pytest.raises(MLInputError, match=f"this is {re.escape(ADAPTER_VERSION)}"):
        SklearnModel.restore(_rewritten(artifact, adapter="9.9.9"))
    with pytest.raises(MLInputError, match=SKLEARN_SCHEME):
        SklearnModel.restore(_rewritten(artifact, scheme="elsewhere"))
    with pytest.raises(MLInputError, match="does not hash"):
        SklearnModel.restore(line + b"\n" + payload[:-1] + b"\x00")
    other = pickle.dumps(42, protocol=5)
    with pytest.raises(MLInputError, match="is not the sklearn:Ridge"):
        SklearnModel.restore(
            _rewritten(artifact, payload_sha256=hashlib.sha256(other).hexdigest())[: -len(payload)]
            + other
        )
    spaced = json.dumps(json.loads(line), sort_keys=True).encode("ascii") + b"\n" + payload
    with pytest.raises(MLInputError, match="not its canonical capture"):
        SklearnModel.restore(spaced)
    with pytest.raises(MLInputError, match="no header line"):
        SklearnModel.restore(b"no newline at all")
    with pytest.raises(MLInputError, match="holds exactly"):
        SklearnModel.restore(_rewritten(artifact, extra=1))


def test_what_a_fit_cannot_take_is_refused() -> None:
    weighted = TrainingSet.of(NAMES, _data(False).features.rows, _data(False).labels, [1.0] * 120)
    with pytest.raises(MLInputError, match="fit takes no sample weights"):
        SklearnModel(KNeighborsRegressor()).fit(weighted, seed=0)
    with pytest.raises(MLInputError, match="scikit-learn estimator"):
        SklearnModel(object())
    with pytest.raises(MLInputError, match="seed is an integer"):
        SklearnModel(Ridge()).fit(_data(False), seed=True)
    three = TrainingSet.of(NAMES, _data(False).features.rows, [float(i % 3) for i in range(120)])
    classifier = SklearnModel(LogisticRegression()).fit(three, seed=0)
    with pytest.raises(MLInputError, match="labels 0 and 1"):
        classifier.predict(FeatureMatrix(NAMES, three.features.rows[:2]))
