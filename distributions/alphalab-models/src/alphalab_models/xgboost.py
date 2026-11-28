"""XGBoost's boosted trees as AlphaLab research models (ADR-0057, ADR-0064 section 4).

:class:`XGBoostModel` wraps an ``XGBClassifier`` or an ``XGBRegressor`` in the
core's :class:`~alphalab.ml.protocol.Model` protocol: it states its kind and
every parameter, fits a copy -- never the estimator it was given -- predicts
from named features, and captures itself as bytes an artifact store names.

The capture is XGBoost's own serialisation
------------------------------------------

An artifact is one line of canonical JSON -- the scheme, the model's kind, the
features it was fitted on, the XGBoost and adapter versions, the estimator's
parameters stated exactly, and the payload's SHA-256 -- then the fitted booster
in XGBoost's JSON model format (``Booster.save_raw``). A fit serialises the
booster once, and that payload is the model's identity; a restored model
captures as the very artifact it came from.

A fitted model predicts through the booster its own payload loads, as a restored
model does, so a model and its restore predict the same by construction. A
classifier is fitted on labels 0 and 1 under ``binary:logistic``, and its
prediction is the probability of the label 1.

The seed
--------

XGBoost reduces a seed modulo ``2**32``: the fit's seed enters modulo
:data:`RANDOM_STATES`, so each of the ledger's 64-bit trial seeds gives one
state, and two seeds ``2**32`` apart give the same model. A booster records no
seed, so a fit that draws nothing -- no subsample, no column sample -- gives the
same bytes whatever the seed.

A restore
---------

A restore is refused under another XGBoost version than the one that captured
the artifact: XGBoost reads its model format across versions, but a model's
predictions are its version's. It is refused, too, when the header is not
canonical, the payload's digest is not the header's, XGBoost cannot read the
payload, or the booster is not the model the header names.

Trust
-----

A restore parses JSON and unpickles nothing: XGBoost reads its own model format,
and the estimator is constructed from parameters stated as values. A parameter
that is code -- a callable objective, a callback -- is refused when the model
is made, because bytes cannot carry it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final, Self

import numpy as np
import xgboost
from sklearn.base import clone

from alphalab.common.types import ParamValue
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.protocol import FeatureMatrix, TrainingSet
from alphalab_models._capture import (
    ADAPTER_VERSION,
    captured,
    constructed,
    declared,
    matched,
    read,
    require_binary,
    require_training,
    require_weights,
    stated,
    unfitted,
)

__all__ = ["ADAPTER_VERSION", "RANDOM_STATES", "XGBOOST_SCHEME", "XGBoostModel"]

#: The scheme tag of an XGBoost artifact's header.
XGBOOST_SCHEME: Final = "alphalab-models.xgboost.v1"

#: XGBoost's seeds are 32 bits: a seed enters modulo this, as XGBoost would reduce it.
RANDOM_STATES: Final = 2**32

#: The objective whose prediction is the probability of the label 1.
PROBABILITY: Final = "binary:logistic"

_CLASSES: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "xgboost:XGBClassifier": xgboost.XGBClassifier,
        "xgboost:XGBRegressor": xgboost.XGBRegressor,
    }
)

_HEADER_KEYS: Final = frozenset(
    {"scheme", "model_kind", "features", "xgboost", "adapter", "parameters", "payload_sha256"}
)


def _kind(estimator: object) -> str:
    return f"xgboost:{type(estimator).__name__}"


def _booster(payload: bytes) -> Any:
    booster = xgboost.Booster()
    try:
        booster.load_model(bytearray(payload))
    except xgboost.core.XGBoostError as refusal:
        raise MLInputError(
            f"The artifact's payload is not an XGBoost model: {refusal}"
        ) from refusal
    return booster


def _objective(booster: Any) -> str:
    return str(json.loads(booster.save_config())["learner"]["objective"]["name"])


class XGBoostModel:
    """An ``XGBClassifier`` or ``XGBRegressor`` as a research model.

    Args:
        estimator: The estimator, unfitted or not: the model keeps a copy of
            its parameters, and :meth:`fit` fits another copy, given the fit's
            seed modulo :data:`RANDOM_STATES` as its ``random_state``.

    Raises:
        MLInputError: If ``estimator`` is not an ``XGBClassifier`` or an
            ``XGBRegressor``; a parameter is not a value (the parameters are
            stated exactly, ``alphalab_models._capture``); or a classifier's
            objective is not ``binary:logistic``.
    """

    __slots__ = ("_booster", "_estimator", "_features", "_payload")

    def __init__(self, estimator: Any) -> None:
        if type(estimator) not in _CLASSES.values():
            raise MLInputError(
                f"An XGBoostModel wraps an XGBClassifier or an XGBRegressor, got {estimator!r}."
            )
        kind = _kind(estimator)
        params = estimator.get_params(deep=False)
        stated(params, kind)
        if isinstance(estimator, xgboost.XGBClassifier) and params["objective"] != PROBABILITY:
            raise MLInputError(
                f"A classifier's prediction is the probability of the label 1, so the {kind}'s "
                f"objective is {PROBABILITY!r}, got {params['objective']!r}."
            )
        self._estimator: Any = clone(estimator)
        self._features: tuple[str, ...] | None = None
        self._payload: bytes | None = None
        self._booster: Any = None

    @classmethod
    def _fitted(cls, estimator: Any, features: tuple[str, ...], payload: bytes) -> Self:
        model = cls(estimator)
        model._features = features
        model._payload = payload
        model._booster = _booster(payload)
        return model

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, XGBoostModel):
            return NotImplemented
        if self._payload is None or other._payload is None:
            return self is other
        return self.capture() == other.capture()

    def __hash__(self) -> int:
        return hash((self._payload, self._features))

    def __repr__(self) -> str:
        state = "fitted" if self._payload is not None else "unfitted"
        return f"XGBoostModel({self.model_kind}, {state})"

    @property
    def model_kind(self) -> str:
        """``xgboost:`` and the estimator's class."""

        return _kind(self._estimator)

    @property
    def features(self) -> tuple[str, ...] | None:
        """The features a fitted model was fitted on, in column order; ``None`` before a fit."""

        return self._features

    def hyperparameters(self) -> Mapping[str, ParamValue]:
        """Every parameter of the estimator, each a parameter value or its ``repr``."""

        return MappingProxyType(declared(self._estimator.get_params(deep=False)))

    def fit(self, training: TrainingSet, *, seed: int) -> Self:
        """A new model: a copy of the estimator fitted on ``training``, the seed its random_state.

        Raises:
            MLInputError: If ``training`` is not a TrainingSet, the seed is not
                an integer, or a classifier's labels are not 0 and 1, both
                present.
        """

        training = require_training(training, seed)
        estimator = clone(self._estimator)
        require_weights(estimator, training, self.model_kind)
        if isinstance(estimator, xgboost.XGBClassifier):
            require_binary(training, self.model_kind)
        estimator.set_params(random_state=seed % RANDOM_STATES)
        weights = None if training.weights is None else np.asarray(training.weights)
        estimator.fit(
            np.asarray(training.features.rows, dtype=np.float64),
            np.asarray(training.labels, dtype=np.float64),
            sample_weight=weights,
        )
        payload = bytes(estimator.get_booster().save_raw(raw_format="json"))
        return self._fitted(self._estimator, training.features.names, payload)

    def predict(self, features: FeatureMatrix) -> tuple[float, ...]:
        """A regressor's values, or a classifier's probability of the label 1.

        Raises:
            MLInputError: If the model is not fitted, or the rows name other
                features or another order.
        """

        if self._features is None:
            raise unfitted(self.model_kind)
        matrix = matched(features, self._features, self.model_kind)
        if not matrix.rows:
            return ()
        design = xgboost.DMatrix(
            np.asarray(matrix.rows, dtype=np.float64),
            missing=self._estimator.get_params(deep=False)["missing"],
        )
        return tuple(float(value) for value in self._booster.predict(design))

    def capture(self) -> bytes:
        """The canonical header, a newline, and the booster serialised when the model was fitted.

        Raises:
            MLInputError: If the model is not fitted.
        """

        if self._payload is None or self._features is None:
            raise unfitted(self.model_kind)
        header = {
            "scheme": XGBOOST_SCHEME,
            "model_kind": self.model_kind,
            "features": list(self._features),
            "xgboost": xgboost.__version__,
            "adapter": ADAPTER_VERSION,
            "parameters": stated(self._estimator.get_params(deep=False), self.model_kind),
            "payload_sha256": hashlib.sha256(self._payload).hexdigest(),
        }
        return captured(header, self._payload)

    @classmethod
    def restore(cls, artifact: bytes) -> Self:
        """The fitted model ``artifact`` captures; nothing is unpickled.

        Raises:
            MLInputError: If the artifact is not a canonical capture of this
                adapter's scheme, was captured under another XGBoost, its
                payload's digest is not the header's, XGBoost cannot read the
                payload, or the booster is not the model the header names: its
                features or its objective are others.
        """

        header, payload, features = read(
            artifact,
            framework="XGBoost",
            scheme=XGBOOST_SCHEME,
            keys=_HEADER_KEYS,
            version_key="xgboost",
            version=xgboost.__version__,
            promise="XGBoost reads its models across versions, but predicts by its own.",
        )
        kind = header["model_kind"]
        if kind not in _CLASSES:
            raise MLInputError(
                f"The artifact names {kind!r}; this adapter restores {sorted(_CLASSES)}."
            )
        try:
            estimator = _CLASSES[kind](**constructed(header["parameters"]))
        except TypeError as refusal:
            raise MLInputError(
                f"The artifact's parameters do not construct the {kind}: {refusal}"
            ) from refusal
        model = cls._fitted(estimator, features, payload)
        width = model._booster.num_features()
        if width != len(features):
            raise MLInputError(
                f"The booster was fitted on {width} features and the header names {len(features)}."
            )
        objective = _objective(model._booster)
        declared_objective = estimator.get_params(deep=False)["objective"]
        if objective != declared_objective:
            raise MLInputError(
                f"The artifact's booster is not the {kind} its header names: its objective is "
                f"{objective!r}, and the parameters state {declared_objective!r}."
            )
        if model.capture() != artifact:
            raise MLInputError(
                "The bytes describe an XGBoost model but are not its canonical capture; one model "
                "has one artifact."
            )
        return model
