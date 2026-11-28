"""LightGBM's boosted trees as AlphaLab research models (ADR-0057, ADR-0064 section 4).

:class:`LightGBMModel` wraps an ``LGBMClassifier`` or an ``LGBMRegressor`` in
the core's :class:`~alphalab.ml.protocol.Model` protocol: it states its kind and
every parameter, fits a copy -- never the estimator it was given -- predicts
from named features, and captures itself as bytes an artifact store names.

The capture is LightGBM's own serialisation
-------------------------------------------

An artifact is one line of canonical JSON -- the scheme, the model's kind, the
features it was fitted on, the LightGBM and adapter versions, the estimator's
parameters stated exactly, and the payload's SHA-256 -- then the fitted booster
as LightGBM's model text (``Booster.model_to_string``). A fit serialises the
booster once, and that payload is the model's identity; a restored model
captures as the very artifact it came from.

A fitted model predicts through the booster its own payload loads, as a restored
model does, so a model and its restore predict the same by construction. A
classifier is fitted on labels 0 and 1 under the binary objective, and its
prediction is the probability of the label 1.

What the bytes record
---------------------

LightGBM writes its training parameters into the model text, the seed and the
thread count among them. Two fits with other seeds are other bytes, even where
they grew the same trees. A fit that leaves ``n_jobs`` unstated records the
number of cores of the host that ran it, so the same training is other bytes on
another host: state ``n_jobs`` for one artifact everywhere. LightGBM's seed is a
32-bit signed integer, so the fit's seed enters modulo :data:`RANDOM_STATES`,
and each of the ledger's 64-bit trial seeds gives one state.

A restore
---------

A restore is refused under another LightGBM version than the one that captured
the artifact: a model's predictions are its version's. It is refused, too, when
the header is not canonical, the payload's digest is not the header's, LightGBM
cannot read the payload, or the booster is not the model the header names.

Trust
-----

A restore parses LightGBM's model text and unpickles nothing, and the estimator
is constructed from parameters stated as values. A parameter that is code -- a
callable objective -- is refused when the model is made, because bytes cannot
carry it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final, Self

import lightgbm
import numpy as np
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

__all__ = ["ADAPTER_VERSION", "LIGHTGBM_SCHEME", "RANDOM_STATES", "LightGBMModel"]

#: The scheme tag of a LightGBM artifact's header.
LIGHTGBM_SCHEME: Final = "alphalab-models.lightgbm.v1"

#: LightGBM's seed is a 32-bit signed integer: a seed enters modulo this.
RANDOM_STATES: Final = 2**31

#: The objective whose prediction is the probability of the label 1.
PROBABILITY: Final = "binary"

_CLASSES: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "lightgbm:LGBMClassifier": lightgbm.LGBMClassifier,
        "lightgbm:LGBMRegressor": lightgbm.LGBMRegressor,
    }
)

_HEADER_KEYS: Final = frozenset(
    {"scheme", "model_kind", "features", "lightgbm", "adapter", "parameters", "payload_sha256"}
)


def _kind(estimator: object) -> str:
    return f"lightgbm:{type(estimator).__name__}"


def _booster(payload: bytes) -> Any:
    try:
        return lightgbm.Booster(model_str=payload.decode("utf-8"))
    except (UnicodeDecodeError, lightgbm.basic.LightGBMError) as refusal:
        raise MLInputError(
            f"The artifact's payload is not a LightGBM model: {refusal}"
        ) from refusal


def _described(payload: bytes) -> dict[str, str]:
    """The model text's opening ``key=value`` lines, up to its first tree."""

    described = {}
    for line in payload.decode("utf-8").splitlines():
        if line.startswith("Tree="):
            break
        key, equals, value = line.partition("=")
        if equals:
            described[key] = value
    return described


class LightGBMModel:
    """An ``LGBMClassifier`` or ``LGBMRegressor`` as a research model.

    Args:
        estimator: The estimator, unfitted or not: the model keeps a copy of
            its parameters, and :meth:`fit` fits another copy, given the fit's
            seed modulo :data:`RANDOM_STATES` as its ``random_state``.

    Raises:
        MLInputError: If ``estimator`` is not an ``LGBMClassifier`` or an
            ``LGBMRegressor``; a parameter is not a value (the parameters are
            stated exactly, ``alphalab_models._capture``); or a classifier's
            objective is stated and is not ``binary``.
    """

    __slots__ = ("_booster", "_estimator", "_features", "_payload")

    def __init__(self, estimator: Any) -> None:
        if type(estimator) not in _CLASSES.values():
            raise MLInputError(
                f"A LightGBMModel wraps an LGBMClassifier or an LGBMRegressor, got {estimator!r}."
            )
        kind = _kind(estimator)
        params = estimator.get_params(deep=False)
        stated(params, kind)
        if isinstance(estimator, lightgbm.LGBMClassifier) and params["objective"] not in (
            None,
            PROBABILITY,
        ):
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
        if not isinstance(other, LightGBMModel):
            return NotImplemented
        if self._payload is None or other._payload is None:
            return self is other
        return self.capture() == other.capture()

    def __hash__(self) -> int:
        return hash((self._payload, self._features))

    def __repr__(self) -> str:
        state = "fitted" if self._payload is not None else "unfitted"
        return f"LightGBMModel({self.model_kind}, {state})"

    @property
    def model_kind(self) -> str:
        """``lightgbm:`` and the estimator's class."""

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
        if isinstance(estimator, lightgbm.LGBMClassifier):
            require_binary(training, self.model_kind)
        estimator.set_params(random_state=seed % RANDOM_STATES)
        weights = None if training.weights is None else np.asarray(training.weights)
        estimator.fit(
            np.asarray(training.features.rows, dtype=np.float64),
            np.asarray(training.labels, dtype=np.float64),
            sample_weight=weights,
        )
        payload = estimator.booster_.model_to_string().encode("utf-8")
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
        values = self._booster.predict(np.asarray(matrix.rows, dtype=np.float64))
        return tuple(float(value) for value in values)

    def capture(self) -> bytes:
        """The canonical header, a newline, and the model text written when the model was fitted.

        Raises:
            MLInputError: If the model is not fitted.
        """

        if self._payload is None or self._features is None:
            raise unfitted(self.model_kind)
        header = {
            "scheme": LIGHTGBM_SCHEME,
            "model_kind": self.model_kind,
            "features": list(self._features),
            "lightgbm": lightgbm.__version__,
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
                adapter's scheme, was captured under another LightGBM, its
                payload's digest is not the header's, LightGBM cannot read the
                payload, or the booster is not the model the header names: its
                features, its classes or its objective are others.
        """

        header, payload, features = read(
            artifact,
            framework="LightGBM",
            scheme=LIGHTGBM_SCHEME,
            keys=_HEADER_KEYS,
            version_key="lightgbm",
            version=lightgbm.__version__,
            promise="a model's predictions are its version's.",
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
        width = model._booster.num_feature()
        if width != len(features):
            raise MLInputError(
                f"The booster was fitted on {width} features and the header names {len(features)}."
            )
        described = _described(payload)
        objective = described.get("objective", "").split(" ")[0]
        classifier = isinstance(estimator, lightgbm.LGBMClassifier)
        if described.get("num_class") != "1" or (objective == PROBABILITY) != classifier:
            raise MLInputError(
                f"The artifact's booster is not the {kind} its header names: its objective is "
                f"{described.get('objective')!r} over {described.get('num_class')} classes."
            )
        if model.capture() != artifact:
            raise MLInputError(
                "The bytes describe a LightGBM model but are not its canonical capture; one model "
                "has one artifact."
            )
        return model
