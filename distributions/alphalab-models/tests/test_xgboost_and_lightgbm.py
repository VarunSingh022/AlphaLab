"""XGBoost's and LightGBM's boosted trees as research models (ADR-0057, ADR-0064 section 4).

* **Conformance**: each framework's classifier and regressor fit a copy, never
  the estimator given; predict what the framework's own estimator predicts,
  from the features they were fitted on, and refuse others; and restore from
  their capture to an equal model with the same bytes and the same predictions.
* **The payload is the framework's own serialisation**: XGBoost's JSON model and
  LightGBM's model text, which the framework itself reads back.
* **A model is its bytes**: the same training is the same artifact, another
  seed that draws another; the ledger's 64-bit seeds enter each framework's
  range; LightGBM records its seed and thread count, so they are in the bytes.
* **The binding**: trained, filed and restored through an artifact store.
* **Parameters are stated exactly**, non-finite floats and tuples included, and
  a parameter that is code is refused: a restore constructs from values.
* **A classifier is binary and predicts a probability**, and **what a restore
  cannot take is refused**: another framework or adapter version, another
  scheme or kind, a payload whose digest is not the header's or that the
  framework cannot read, a booster of another objective, a header that is not
  canonical.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import warnings
from collections.abc import Callable
from typing import Any

import lightgbm
import numpy as np
import pytest
import xgboost
from alphalab_models import _capture
from alphalab_models.lightgbm import LIGHTGBM_SCHEME, LightGBMModel
from alphalab_models.lightgbm import RANDOM_STATES as LIGHTGBM_STATES
from alphalab_models.xgboost import RANDOM_STATES as XGBOOST_STATES
from alphalab_models.xgboost import XGBOOST_SCHEME, XGBoostModel
from sklearn.base import clone
from sklearn.exceptions import NotFittedError
from sklearn.utils.validation import check_is_fitted

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


def _xgb_classifier() -> xgboost.XGBClassifier:
    return xgboost.XGBClassifier(n_estimators=20, max_depth=3, subsample=0.8, n_jobs=1)


def _xgb_regressor() -> xgboost.XGBRegressor:
    return xgboost.XGBRegressor(n_estimators=20, max_depth=3, subsample=0.8, n_jobs=1)


def _lgbm_classifier() -> lightgbm.LGBMClassifier:
    return lightgbm.LGBMClassifier(
        n_estimators=20, num_leaves=7, subsample=0.8, subsample_freq=1, n_jobs=1, verbose=-1
    )


def _lgbm_regressor() -> lightgbm.LGBMRegressor:
    return lightgbm.LGBMRegressor(
        n_estimators=20, num_leaves=7, subsample=0.8, subsample_freq=1, n_jobs=1, verbose=-1
    )


Adapter = type[XGBoostModel] | type[LightGBMModel]

MODELS: list[tuple[str, Adapter, Callable[[], Any], bool, int]] = [
    ("xgboost classifier", XGBoostModel, _xgb_classifier, True, XGBOOST_STATES),
    ("xgboost regressor", XGBoostModel, _xgb_regressor, False, XGBOOST_STATES),
    ("lightgbm classifier", LightGBMModel, _lgbm_classifier, True, LIGHTGBM_STATES),
    ("lightgbm regressor", LightGBMModel, _lgbm_regressor, False, LIGHTGBM_STATES),
]
IDS = [name for name, *_ in MODELS]


def _direct(estimator: Any, training: TrainingSet, seed: int, classes: bool) -> list[float]:
    """What the framework's own estimator predicts, fitted as the adapter fits it.

    LightGBM 4.6's estimator names a NumPy fit's columns itself, then warns
    that NumPy rows carry no names when it predicts from them. The adapter
    predicts through the booster and never meets it, so the reference call here
    ignores that one warning.
    """

    fitted = clone(estimator).set_params(random_state=seed)
    design = np.asarray(training.features.rows, dtype=np.float64)
    fitted.fit(design, np.asarray(training.labels, dtype=np.float64))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="X does not have valid feature names", category=UserWarning
        )
        values = fitted.predict_proba(design)[:, 1] if classes else fitted.predict(design)
    return [float(value) for value in values]


@pytest.mark.parametrize(("name", "adapter", "make", "classes", "states"), MODELS, ids=IDS)
def test_a_fit_is_a_copy_and_its_capture_restores_the_same_model(
    name: str, adapter: Adapter, make: Callable[[], Any], classes: bool, states: int
) -> None:
    estimator = make()
    training = _data(classes)
    fitted = adapter(estimator).fit(training, seed=7)
    with pytest.raises(NotFittedError):
        check_is_fitted(estimator)
    assert fitted.model_kind == f"{name.split()[0]}:{type(estimator).__name__}"
    assert fitted.features == NAMES
    matrix = FeatureMatrix(NAMES, training.features.rows)
    predictions = fitted.predict(matrix)
    assert list(predictions) == _direct(estimator, training, 7, classes), (
        "the adapter predicts what the framework's own estimator does"
    )
    artifact = fitted.capture()
    again = adapter.restore(artifact)
    assert again == fitted and again.capture() == artifact
    assert again.predict(matrix) == predictions
    assert again.hyperparameters() == fitted.hyperparameters()
    if classes:
        assert all(0.0 <= p <= 1.0 for p in predictions)
    assert fitted.predict(FeatureMatrix(NAMES, ())) == ()
    refitted = again.fit(training, seed=7)
    assert refitted.capture() == artifact, "a restored model fits as the model it restores"


def test_the_payload_is_the_frameworks_own_serialisation() -> None:
    xgb = XGBoostModel(_xgb_classifier()).fit(_data(True), seed=1).capture()
    _, _, payload = xgb.partition(b"\n")
    assert set(json.loads(payload)) == {"learner", "version"}, "XGBoost's JSON model"
    booster = xgboost.Booster()
    booster.load_model(bytearray(payload))
    assert bytes(booster.save_raw(raw_format="json")) == payload
    lgbm = LightGBMModel(_lgbm_classifier()).fit(_data(True), seed=1).capture()
    _, _, text = lgbm.partition(b"\n")
    assert text.startswith(b"tree\nversion="), "LightGBM's model text"
    assert lightgbm.Booster(model_str=text.decode()).model_to_string().encode() == text


@pytest.mark.parametrize(("name", "adapter", "make", "classes", "states"), MODELS, ids=IDS)
def test_rows_naming_other_features_and_an_unfitted_model_are_refused(
    name: str, adapter: Adapter, make: Callable[[], Any], classes: bool, states: int
) -> None:
    fitted = adapter(make()).fit(_data(classes), seed=0)
    rows = _data(classes).features.rows[:2]
    with pytest.raises(MLInputError, match="in that order"):
        fitted.predict(FeatureMatrix(tuple(reversed(NAMES)), rows))
    unfitted = adapter(make())
    with pytest.raises(MLInputError, match="not fitted"):
        unfitted.predict(FeatureMatrix(NAMES, rows))
    with pytest.raises(MLInputError, match="not fitted"):
        unfitted.capture()


@pytest.mark.parametrize(("name", "adapter", "make", "classes", "states"), MODELS, ids=IDS)
def test_the_seed_reaches_random_state_and_the_same_training_is_the_same_artifact(
    name: str, adapter: Adapter, make: Callable[[], Any], classes: bool, states: int
) -> None:
    model = adapter(make())
    training = _data(classes)
    first = model.fit(training, seed=1).capture()
    assert model.fit(training, seed=1).capture() == first, "a deterministic fit, the same bytes"
    assert model.fit(training, seed=2).capture() != first, "the subsample follows the seed"


@pytest.mark.parametrize(("name", "adapter", "make", "classes", "states"), MODELS, ids=IDS)
def test_a_ledgers_64_bit_seed_enters_the_frameworks_range(
    name: str, adapter: Adapter, make: Callable[[], Any], classes: bool, states: int
) -> None:
    """The trial ledger draws 64-bit seeds; XGBoost's are 32 bits, LightGBM's a signed int."""

    model = adapter(make())
    training = _data(classes)
    wide = 10725776635129204304
    fitted = model.fit(training, seed=wide)
    assert fitted.capture() == model.fit(training, seed=wide % states).capture()
    assert fitted.capture() != model.fit(training, seed=wide + 1).capture()
    assert model.fit(training, seed=-1).capture() == model.fit(training, seed=states - 1).capture()


def test_xgboost_records_no_seed_and_lightgbm_records_its_seed_and_threads() -> None:
    """A booster that draws nothing is XGBoost's same bytes; LightGBM writes its parameters."""

    plain = XGBoostModel(xgboost.XGBRegressor(n_estimators=10, max_depth=2, n_jobs=1))
    training = _data(False)
    assert plain.fit(training, seed=1).capture() == plain.fit(training, seed=2).capture()
    drawn = LightGBMModel(lightgbm.LGBMRegressor(n_estimators=10, n_jobs=1, verbose=-1))
    first, second = (drawn.fit(training, seed=s).capture() for s in (1, 2))
    assert first != second and b"[seed: 1]" in first and b"[seed: 2]" in second
    assert b"[num_threads: 1]" in first, "a stated n_jobs is the bytes on every host"
    unstated = LightGBMModel(lightgbm.LGBMRegressor(n_estimators=10, verbose=-1))
    assert re.search(rb"\[num_threads: \d+\]", unstated.fit(training, seed=1).capture())


@pytest.mark.parametrize(
    ("adapter", "make"),
    [(XGBoostModel, _xgb_classifier), (LightGBMModel, _lgbm_classifier)],
    ids=["xgboost", "lightgbm"],
)
def test_a_model_is_trained_filed_and_restored_through_an_artifact_store(
    adapter: Adapter, make: Callable[[], Any]
) -> None:
    store = MemoryArtifactStore()
    training = _data(True)
    fitted, record = train_model(adapter(make()), training, seed=5, store=store)
    assert record.model_kind == fitted.model_kind
    assert record.training_id == training.training_id and record.seed == 5
    restored = restore_model(adapter, store, record.artifact_id)
    assert restored == fitted and restored.capture() == fitted.capture()


def test_parameters_are_stated_exactly_and_a_parameter_that_is_code_is_refused() -> None:
    estimator = xgboost.XGBClassifier(n_estimators=5, monotone_constraints=(1, 0, -1), n_jobs=1)
    artifact = XGBoostModel(estimator).fit(_data(True), seed=0).capture()
    stated = json.loads(artifact.partition(b"\n")[0])["parameters"]
    assert stated["missing"] == {"float": "nan"} and stated["n_estimators"] == 5
    assert stated["monotone_constraints"] == {"tuple": [1, 0, -1]}
    restored = XGBoostModel.restore(artifact)
    assert restored.hyperparameters()["monotone_constraints"] == "(1, 0, -1)"
    assert restored.hyperparameters()["missing"] == "nan"
    with pytest.raises(MLInputError, match=r"'objective' is .*bytes cannot carry code"):
        XGBoostModel(xgboost.XGBRegressor(objective=lambda labels, scores: (labels, scores)))
    with pytest.raises(MLInputError, match="'callbacks'"):
        XGBoostModel(xgboost.XGBRegressor(callbacks=[object()]))
    with pytest.raises(MLInputError, match="'class_weight'"):
        LightGBMModel(lightgbm.LGBMClassifier(class_weight={0.0: 1.0, 1.0: 2.0}))
    values = {"a": None, "b": [1.5, (math.inf, -math.inf)], "c": "x", "d": True}
    round_trip = _capture.constructed(json.loads(json.dumps(_capture.stated(values, "k"))))
    assert round_trip["a"] is None and round_trip["c"] == "x" and round_trip["d"] is True
    assert round_trip["b"] == [1.5, (math.inf, -math.inf)]
    with pytest.raises(MLInputError, match="which no adapter writes"):
        _capture.constructed({"a": {"float": "1.0"}})


def test_a_classifier_is_binary_and_predicts_a_probability() -> None:
    with pytest.raises(MLInputError, match="objective is 'binary:logistic'"):
        XGBoostModel(xgboost.XGBClassifier(objective="binary:logitraw"))
    with pytest.raises(MLInputError, match="objective is 'binary'"):
        LightGBMModel(lightgbm.LGBMClassifier(objective="multiclass"))
    rows = _data(True).features.rows
    three = TrainingSet.of(NAMES, rows, [float(i % 3) for i in range(120)])
    one = TrainingSet.of(NAMES, rows, [0.0] * 120)
    for adapter, make in ((XGBoostModel, _xgb_classifier), (LightGBMModel, _lgbm_classifier)):
        for labels in (three, one):
            with pytest.raises(MLInputError, match="labels 0 and 1, both present"):
                adapter(make()).fit(labels, seed=0)
    with pytest.raises(MLInputError, match="XGBClassifier or an XGBRegressor"):
        XGBoostModel(xgboost.XGBRanker())
    with pytest.raises(MLInputError, match="LGBMClassifier or an LGBMRegressor"):
        LightGBMModel(lightgbm.LGBMRanker())
    with pytest.raises(MLInputError, match="seed is an integer"):
        XGBoostModel(_xgb_regressor()).fit(_data(False), seed=True)


def _rewritten(artifact: bytes, **changes: object) -> bytes:
    line, _, payload = artifact.partition(b"\n")
    header = {**json.loads(line), **changes}
    return _capture.captured(header, payload)


def _repaid(artifact: bytes, payload: bytes) -> bytes:
    """``artifact``'s header over another payload, its digest the new payload's."""

    return _rewritten(artifact, payload_sha256=hashlib.sha256(payload).hexdigest()).partition(
        b"\n"
    )[0] + (b"\n" + payload)


REFUSALS = [
    ("xgboost", XGBoostModel, _xgb_classifier, _xgb_regressor, "XGBoost", XGBOOST_SCHEME),
    ("lightgbm", LightGBMModel, _lgbm_classifier, _lgbm_regressor, "LightGBM", LIGHTGBM_SCHEME),
]


@pytest.mark.parametrize(
    ("version_key", "adapter", "classifier", "regressor", "framework", "scheme"),
    REFUSALS,
    ids=["xgboost", "lightgbm"],
)
def test_what_a_restore_cannot_take_is_refused(
    version_key: str,
    adapter: Adapter,
    classifier: Callable[[], Any],
    regressor: Callable[[], Any],
    framework: str,
    scheme: str,
) -> None:
    artifact = adapter(classifier()).fit(_data(True), seed=0).capture()
    line, _, payload = artifact.partition(b"\n")
    with pytest.raises(MLInputError, match=f"captured under {framework} 0\\.0"):
        adapter.restore(_rewritten(artifact, **{version_key: "0.0"}))
    with pytest.raises(MLInputError, match=f"this is {re.escape(_capture.ADAPTER_VERSION)}"):
        adapter.restore(_rewritten(artifact, adapter="9.9.9"))
    with pytest.raises(MLInputError, match=re.escape(scheme)):
        adapter.restore(_rewritten(artifact, scheme="elsewhere"))
    with pytest.raises(MLInputError, match="does not hash"):
        adapter.restore(line + b"\n" + payload[:-1] + b"\x00")
    with pytest.raises(MLInputError, match=f"is not a{'n' if framework == 'XGBoost' else ''} "):
        adapter.restore(_repaid(artifact, b"not a model"))
    with pytest.raises(MLInputError, match="this adapter restores"):
        adapter.restore(_rewritten(artifact, model_kind=f"{version_key}:Elsewhere"))
    regressed = adapter(regressor()).fit(_data(False), seed=0).capture()
    with pytest.raises(MLInputError, match=r"is not the .* its header names"):
        adapter.restore(_repaid(artifact, regressed.partition(b"\n")[2]))
    with pytest.raises(MLInputError, match="features and the header names"):
        adapter.restore(_rewritten(artifact, features=["one", "two"]))
    spaced = json.dumps(json.loads(line), sort_keys=True).encode("ascii") + b"\n" + payload
    with pytest.raises(MLInputError, match="not its canonical capture"):
        adapter.restore(spaced)
    with pytest.raises(MLInputError, match="no header line"):
        adapter.restore(b"no newline at all")
    with pytest.raises(MLInputError, match="holds exactly"):
        adapter.restore(_rewritten(artifact, extra=1))
    with pytest.raises(MLInputError, match="states no parameters"):
        adapter.restore(_rewritten(artifact, parameters=[1, 2]))
