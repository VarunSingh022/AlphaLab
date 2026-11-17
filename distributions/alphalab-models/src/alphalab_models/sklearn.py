"""scikit-learn estimators as AlphaLab research models (ADR-0057, ADR-0064 section 4).

:class:`SklearnModel` wraps any scikit-learn estimator in the core's
:class:`~alphalab.ml.protocol.Model` protocol: it states its kind and every
parameter, fits a clone -- never the estimator it was given -- predicts from
named features, and captures itself as bytes an artifact store names.

The capture is scikit-learn's own serialisation, wrapped
--------------------------------------------------------

An artifact is one line of canonical JSON -- the scheme, the model's kind, the
features it was fitted on, the scikit-learn, NumPy and adapter versions and the
payload's SHA-256 -- then the pickled estimator. A fit is pickled once, when it
is made, and that payload is the model's identity: scikit-learn's pickles do not
round-trip byte for byte (a ``Ridge`` or a gradient-boosting model pickled,
loaded and pickled again gives other bytes), so a restored model captures as the
very artifact it came from rather than pickling itself again. A deterministic
fit of the same data pickles to the same bytes, so the same training gives the
same artifact.

A restore is refused under another scikit-learn version than the one that
captured it -- scikit-learn promises its pickles only within a version -- and
when the header is not canonical, the payload's digest is not the header's, or
the payload is not an estimator of the kind the header names.

Trust
-----

**Restoring unpickles, and unpickling runs code.** Restore only artifacts you
trust: ones your own runs filed, read back through an
:class:`~alphalab.model_registry.artifact_store.ArtifactStore`, which refuses
bytes whose digest is not the id a strategy declares. The core never unpickles
(ADR-0057 section 5); this adapter does, at the researcher's request.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final, Self

import numpy as np
import sklearn
from sklearn.base import BaseEstimator, clone, is_classifier

from alphalab.common.types import ParamValue
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.protocol import FeatureMatrix, TrainingSet

__all__ = ["ADAPTER_VERSION", "SKLEARN_SCHEME", "SklearnModel"]

#: This adapter's version, recorded in every artifact it captures.
ADAPTER_VERSION: Final = "0.1.0"

#: The scheme tag of a scikit-learn artifact's header.
SKLEARN_SCHEME: Final = "alphalab-models.sklearn.v1"

_HEADER_KEYS: Final = frozenset(
    {"scheme", "model_kind", "features", "sklearn", "numpy", "adapter", "payload_sha256"}
)


def _kind(estimator: object) -> str:
    return f"sklearn:{type(estimator).__name__}"


def _parameter(value: object) -> ParamValue:
    """A parameter as a parameter value: itself when it is one, else its ``repr``."""

    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return repr(value)


class SklearnModel:
    """A scikit-learn estimator as a research model.

    Args:
        estimator: The estimator, unfitted or not: :meth:`fit` fits a clone of
            it. An estimator that takes a ``random_state`` is given the fit's
            seed.

    Raises:
        MLInputError: If ``estimator`` is not a scikit-learn estimator.
    """

    __slots__ = ("_estimator", "_features", "_numpy", "_payload")

    def __init__(self, estimator: Any) -> None:
        if not isinstance(estimator, BaseEstimator):
            raise MLInputError(f"A SklearnModel wraps a scikit-learn estimator, got {estimator!r}.")
        self._estimator: Any = estimator
        self._features: tuple[str, ...] | None = None
        self._numpy: str = np.__version__
        self._payload: bytes | None = None

    @classmethod
    def _fitted(cls, estimator: Any, features: tuple[str, ...], payload: bytes, numpy: str) -> Self:
        model = cls(estimator)
        model._features = features
        model._numpy = numpy
        model._payload = payload
        return model

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SklearnModel):
            return NotImplemented
        if self._payload is None or other._payload is None:
            return self is other
        return self._payload == other._payload and self._features == other._features

    def __hash__(self) -> int:
        return hash((self._payload, self._features))

    def __repr__(self) -> str:
        state = "fitted" if self._payload is not None else "unfitted"
        return f"SklearnModel({self.model_kind}, {state})"

    @property
    def model_kind(self) -> str:
        """``sklearn:`` and the estimator's class."""

        return _kind(self._estimator)

    @property
    def features(self) -> tuple[str, ...] | None:
        """The features a fitted model was fitted on, in column order; ``None`` before a fit."""

        return self._features

    def hyperparameters(self) -> Mapping[str, ParamValue]:
        """Every parameter of the estimator, each a parameter value or its ``repr``."""

        params = self._estimator.get_params(deep=False)
        return MappingProxyType({name: _parameter(params[name]) for name in sorted(params)})

    def fit(self, training: TrainingSet, *, seed: int) -> Self:
        """A new model: a clone of the estimator fitted on ``training``, the seed its random_state.

        Raises:
            MLInputError: If ``training`` is not a TrainingSet, the seed is not
                an integer, or the estimator takes no sample weights and the
                training set has them.
        """

        if not isinstance(training, TrainingSet):
            raise MLInputError(f"A model is fitted on a TrainingSet, got {training!r}.")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise MLInputError(f"A seed is an integer, got {seed!r}.")
        estimator = clone(self._estimator)
        if "random_state" in estimator.get_params(deep=False):
            estimator.set_params(random_state=seed)
        design = np.asarray(training.features.rows, dtype=np.float64)
        labels = np.asarray(training.labels, dtype=np.float64)
        if training.weights is None:
            estimator.fit(design, labels)
        else:
            try:
                estimator.fit(design, labels, sample_weight=np.asarray(training.weights))
            except TypeError as refusal:
                raise MLInputError(
                    f"{self.model_kind} takes no sample weights, and the training set has them: "
                    f"{refusal}"
                ) from refusal
        payload = pickle.dumps(estimator, protocol=5)
        return self._fitted(estimator, training.features.names, payload, np.__version__)

    def predict(self, features: FeatureMatrix) -> tuple[float, ...]:
        """A regressor's values, or a binary classifier's probability of the label 1.

        Raises:
            MLInputError: If the model is not fitted, the rows name other
                features or another order, or a classifier's classes are not 0
                and 1.
        """

        if self._features is None:
            raise MLInputError(
                f"This {self.model_kind} is not fitted: fit it, or restore a fitted one."
            )
        if not isinstance(features, FeatureMatrix):
            raise MLInputError(f"A model predicts from a FeatureMatrix, got {features!r}.")
        if features.names != self._features:
            raise MLInputError(
                f"The {self.model_kind} was fitted on {list(self._features)}, in that order; the "
                f"rows name {list(features.names)}."
            )
        if not features.rows:
            return ()
        design = np.asarray(features.rows, dtype=np.float64)
        if is_classifier(self._estimator):
            classes = [float(label) for label in self._estimator.classes_]
            if sorted(classes) != [0.0, 1.0]:
                raise MLInputError(
                    f"The {self.model_kind} classifies {classes}; a prediction is P(label 1) of a "
                    "classifier of labels 0 and 1."
                )
            column = classes.index(1.0)
            return tuple(float(p) for p in self._estimator.predict_proba(design)[:, column])
        return tuple(float(value) for value in self._estimator.predict(design))

    def _header(self) -> dict[str, object]:
        assert self._payload is not None and self._features is not None
        return {
            "scheme": SKLEARN_SCHEME,
            "model_kind": self.model_kind,
            "features": list(self._features),
            "sklearn": sklearn.__version__,
            "numpy": self._numpy,
            "adapter": ADAPTER_VERSION,
            "payload_sha256": hashlib.sha256(self._payload).hexdigest(),
        }

    def capture(self) -> bytes:
        """The canonical header, a newline, and the payload pickled when the model was fitted.

        Raises:
            MLInputError: If the model is not fitted.
        """

        if self._payload is None:
            raise MLInputError(
                f"This {self.model_kind} is not fitted: fit it, or restore a fitted one."
            )
        header = json.dumps(
            self._header(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return header.encode("ascii") + b"\n" + self._payload

    @classmethod
    def restore(cls, artifact: bytes) -> Self:
        """The fitted model ``artifact`` captures. Unpickles: restore only artifacts you trust.

        Raises:
            MLInputError: If the artifact is not a canonical capture of this
                adapter's scheme, was captured under another scikit-learn, its
                payload's digest is not the header's, or the payload is not an
                estimator of the kind the header names.
        """

        if not isinstance(artifact, bytes):
            raise MLInputError(f"An artifact is bytes, got {type(artifact).__name__}.")
        line, newline, payload = artifact.partition(b"\n")
        if not newline:
            raise MLInputError(
                "The bytes are not a scikit-learn artifact: they hold no header line."
            )
        try:
            header = json.loads(line.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as refusal:
            raise MLInputError(
                f"The artifact's header is not canonical JSON: {refusal}"
            ) from refusal
        if not isinstance(header, dict) or set(header) != _HEADER_KEYS:
            raise MLInputError(
                f"A scikit-learn artifact's header holds exactly {sorted(_HEADER_KEYS)}."
            )
        if header["scheme"] != SKLEARN_SCHEME:
            raise MLInputError(
                f"The artifact's scheme is {header['scheme']!r}; this adapter reads "
                f"{SKLEARN_SCHEME!r}."
            )
        if header["sklearn"] != sklearn.__version__:
            raise MLInputError(
                f"The artifact was captured under scikit-learn {header['sklearn']} and this "
                f"environment has {sklearn.__version__}; scikit-learn promises its pickles only "
                "within a version. Restore it where that version is installed, or fit it again."
            )
        if header["adapter"] != ADAPTER_VERSION:
            raise MLInputError(
                f"The artifact was captured by alphalab-models {header['adapter']} and this is "
                f"{ADAPTER_VERSION}; restore it with the adapter that captured it."
            )
        if not isinstance(header["numpy"], str):
            raise MLInputError("The artifact's header names no NumPy version.")
        if hashlib.sha256(payload).hexdigest() != header["payload_sha256"]:
            raise MLInputError(
                "The artifact's payload does not hash to the digest its header names."
            )
        features = header["features"]
        if (
            not isinstance(features, list)
            or not features
            or not all(isinstance(name, str) and name for name in features)
        ):
            raise MLInputError("The artifact's header names no features.")
        estimator = pickle.loads(payload)
        if not isinstance(estimator, BaseEstimator) or _kind(estimator) != header["model_kind"]:
            raise MLInputError(
                f"The artifact's payload is not the {header['model_kind']} its header names."
            )
        width = getattr(estimator, "n_features_in_", len(features))
        if width != len(features):
            raise MLInputError(
                f"The estimator was fitted on {width} features and the header names "
                f"{len(features)}."
            )
        model = cls._fitted(estimator, tuple(features), payload, header["numpy"])
        if model.capture() != artifact:
            raise MLInputError(
                "The bytes describe a scikit-learn model but are not its canonical capture; one "
                "model has one artifact."
            )
        return model
