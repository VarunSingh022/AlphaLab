"""The research model protocol: a trained model is its bytes (v4.3, ADR-0064).

A research model implements :class:`Model`. It states its kind and every
hyperparameter, fits purely -- returning a new fitted model, never mutating
itself -- predicts one value per row, and captures itself as canonical bytes
that :meth:`Model.restore` turns back into the same model.

The bytes are the identity
--------------------------

A model's identity is not its training run. A GPU, a thread pool or a
framework's version can make two runs of one training differ in their last
bits, and asking which result is "the" model has no answer. What a backtest
can be held to is what it traded: the fitted model, captured.
:func:`file_model` files the captured bytes in an
:class:`~alphalab.model_registry.artifact_store.ArtifactStore`, which names
them by their digest, ``alphalab-artifact:sha256:<hex>``. :func:`restore_model`
reads them back through the store's verification, so bytes whose digest is not
the name are refused before any model sees them.

A strategy that holds a model declares that name among its parameters. Its
fingerprint then carries the model, and a rerun given another artifact is
``INPUTS_DIFFER`` (ADR-0053): nothing new was needed to say so. What the model
was trained from -- the seed, the hyperparameters and the training set's
identity -- is a :class:`TrainingRecord` kept beside the artifact, never in its
name. Two trainings that produce the same bytes are one model.

Features are named
------------------

A :class:`TrainingSet` states its feature names, and a fitted model refuses a
:class:`FeatureMatrix` that names others, or names the same in another order.
A column handed in the wrong place is a prediction from the wrong feature, and
nothing downstream could tell.

Canonical capture
-----------------

The core's models (:mod:`alphalab.ml.models`) capture canonical JSON.
:func:`canonical_capture` writes sorted keys, no whitespace, and every float by
its shortest ``repr``, under :data:`MODEL_SCHEME`. :func:`read_capture` refuses
bytes that are not that, and :func:`restored` refuses a restore whose model
captures to other bytes than the ones it was read from, so a model has exactly
one artifact.

Adapters for external frameworks live outside the core, in ``alphalab-models``
(ADR-0057). The core never imports a framework, and never unpickles.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Protocol, Self

from alphalab.common.types import ParamValue
from alphalab.ml.exceptions import MLInputError
from alphalab.model_registry.artifact_store import ARTIFACT_URI_SCHEME, ArtifactStore
from alphalab.model_registry.registry import ArtifactRef

__all__ = [
    "MODEL_SCHEME",
    "TRAINING_SET_SCHEME",
    "Capture",
    "FeatureMatrix",
    "Model",
    "TrainingRecord",
    "TrainingSet",
    "canonical_capture",
    "file_model",
    "read_capture",
    "restore_model",
    "restored",
    "train_model",
]

#: The scheme tag a training set's identity is derived under: its first line.
TRAINING_SET_SCHEME: Final = "alphalab.training_set.v1"

#: The scheme tag every canonical capture of a core model carries.
MODEL_SCHEME: Final = "alphalab.model.v1"

#: The keys of a canonical capture, every one required.
_CAPTURE_KEYS: Final = frozenset({"scheme", "model_kind", "hyperparameters", "features", "state"})


def _names(names: object, what: str) -> tuple[str, ...]:
    if isinstance(names, str) or not isinstance(names, Sequence):
        raise MLInputError(f"{what} names its features as a sequence of names, got {names!r}.")
    result = tuple(names)
    if not result:
        raise MLInputError(f"{what} names no feature.")
    for name in result:
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise MLInputError(f"Feature name {name!r} is not a name.")
    if len(set(result)) != len(result):
        repeated = sorted({name for name in result if result.count(name) > 1})
        raise MLInputError(f"{what} repeats a feature: {repeated}.")
    return result


def _number(value: object, where: str) -> float:
    """``value`` as a finite float, refused when it is not one exactly."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MLInputError(f"{where} is {value!r}; a value is a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise MLInputError(f"{where} is {value!r}; a value is a finite number.")
    if isinstance(value, int) and number != value:
        raise MLInputError(
            f"{where} is the integer {value}, which no float holds exactly; pass the float "
            "it should be."
        )
    return number


def _row(row: object, width: int, index: int) -> tuple[float, ...]:
    if isinstance(row, str) or not isinstance(row, Sequence):
        raise MLInputError(f"Row {index} is {row!r}, not a sequence of values.")
    if len(row) != width:
        raise MLInputError(f"Row {index} holds {len(row)} values for {width} features.")
    return tuple(
        _number(value, f"Row {index}'s value {position}") for position, value in enumerate(row)
    )


@dataclass(frozen=True, slots=True)
class FeatureMatrix:
    """Rows of named features: each row's values in the order the names give.

    Attributes:
        names: The features, each a name, none repeated, in column order.
        rows: One tuple of finite floats per row. A matrix may hold no row.

    Raises:
        MLInputError: If a name is blank or repeated, a row's width is not the
            number of names, or a value is not a finite number.
    """

    names: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        names = _names(self.names, "A feature matrix")
        if isinstance(self.rows, str) or not isinstance(self.rows, Sequence):
            raise MLInputError(f"A feature matrix's rows are a sequence, got {self.rows!r}.")
        rows = tuple(_row(row, len(names), index) for index, row in enumerate(self.rows))
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "rows", rows)

    @classmethod
    def of(cls, names: Sequence[str], rows: Sequence[Sequence[float]]) -> FeatureMatrix:
        """A matrix from any sequences of names and rows."""

        return cls(tuple(names), tuple(tuple(row) for row in rows))

    def __len__(self) -> int:
        return len(self.rows)

    def column(self, name: str) -> tuple[float, ...]:
        """The values of feature ``name``, in row order."""

        if name not in self.names:
            raise MLInputError(f"The matrix has no feature {name!r}; it has {list(self.names)}.")
        position = self.names.index(name)
        return tuple(row[position] for row in self.rows)


@dataclass(frozen=True, slots=True)
class TrainingSet:
    """What a model is fitted on: named features, a label per row, and optional weights.

    Attributes:
        features: The rows, at least one.
        labels: One finite label per row.
        weights: One positive, finite weight per row, or ``None``: every row
            counts once.

    Raises:
        MLInputError: If there is no row, the labels or weights do not match
            the rows one for one, a label is not finite, or a weight is not a
            positive finite number.
    """

    features: FeatureMatrix
    labels: tuple[float, ...]
    weights: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.features, FeatureMatrix):
            raise MLInputError(
                f"A training set's features are a FeatureMatrix, got {self.features!r}."
            )
        if not self.features.rows:
            raise MLInputError("A training set needs at least one row.")
        count = len(self.features.rows)
        if isinstance(self.labels, str) or not isinstance(self.labels, Sequence):
            raise MLInputError(f"A training set's labels are a sequence, got {self.labels!r}.")
        if len(self.labels) != count:
            raise MLInputError(f"A training set has {count} rows and {len(self.labels)} labels.")
        labels = tuple(_number(label, f"Label {index}") for index, label in enumerate(self.labels))
        object.__setattr__(self, "labels", labels)
        if self.weights is not None:
            if isinstance(self.weights, str) or not isinstance(self.weights, Sequence):
                raise MLInputError(
                    f"A training set's weights are a sequence, got {self.weights!r}."
                )
            if len(self.weights) != count:
                raise MLInputError(
                    f"A training set has {count} rows and {len(self.weights)} weights."
                )
            weights = tuple(
                _number(weight, f"Weight {index}") for index, weight in enumerate(self.weights)
            )
            for index, weight in enumerate(weights):
                if weight <= 0.0:
                    raise MLInputError(
                        f"Weight {index} is {weight!r}; a weight is positive. Leave a row out "
                        "rather than weigh it at zero."
                    )
            object.__setattr__(self, "weights", weights)

    @classmethod
    def of(
        cls,
        names: Sequence[str],
        rows: Sequence[Sequence[float]],
        labels: Sequence[float],
        weights: Sequence[float] | None = None,
    ) -> TrainingSet:
        """A training set from any sequences."""

        return cls(
            FeatureMatrix.of(names, rows),
            tuple(labels),
            None if weights is None else tuple(weights),
        )

    def __len__(self) -> int:
        return len(self.labels)

    @property
    def training_id(self) -> str:
        """The derived identity of these values: names, rows, labels and weights, in order.

        Every value enters by its shortest ``repr``, so a training set built
        from the same numbers on any host has the same identity.
        """

        lines = [TRAINING_SET_SCHEME, json.dumps(list(self.features.names))]
        weights = self.weights
        for index, (row, label) in enumerate(zip(self.features.rows, self.labels, strict=True)):
            line = ",".join(repr(value) for value in row) + "|" + repr(label)
            if weights is not None:
                line += "|" + repr(weights[index])
            lines.append(line)
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


class Model(Protocol):
    """A research model: declared, fitted purely, and captured as bytes (ADR-0064).

    A model is fitted or not: :meth:`fit` returns a fitted one, and
    :meth:`predict` and :meth:`capture` refuse a model that was not.
    """

    @property
    def model_kind(self) -> str:
        """The family and its implementation, such as ``"alphalab:ridge"``."""
        ...

    def hyperparameters(self) -> Mapping[str, ParamValue]:
        """Every choice the model was given."""
        ...

    def fit(self, training: TrainingSet, *, seed: int) -> Self:
        """A new model fitted on ``training``; this one is unchanged.

        A model that draws nothing records the seed it was given, and its fit
        does not depend on it.
        """
        ...

    def predict(self, features: FeatureMatrix) -> tuple[float, ...]:
        """One prediction per row: a regression's value, or a classifier's P(label 1).

        The rows must name the features the model was fitted on, in that order.
        """
        ...

    def capture(self) -> bytes:
        """The fitted model's canonical bytes."""
        ...

    @classmethod
    def restore(cls, artifact: bytes) -> Self:
        """The fitted model ``artifact`` captures; refused if it is not one."""
        ...


@dataclass(frozen=True, slots=True)
class TrainingRecord:
    """What a filed model was trained from: kept beside its artifact, never in its name.

    Attributes:
        artifact_id: The model's name, ``alphalab-artifact:sha256:<hex>``.
        model_kind: The model's kind.
        hyperparameters: Every choice it was given.
        seed: The seed its fit was given.
        training_id: The training set's derived identity.
    """

    artifact_id: str
    model_kind: str
    hyperparameters: Mapping[str, ParamValue]
    seed: int
    training_id: str


def _seed(seed: object) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise MLInputError(f"A seed is an integer, got {seed!r}.")
    return seed


def file_model(model: Model, store: ArtifactStore) -> str:
    """File a fitted model's captured bytes in ``store``; return the artifact id naming them.

    Filing the same bytes again returns the same id: the same content is the
    same model.

    Raises:
        MLInputError: If the model is not fitted, or its capture is not bytes.
    """

    payload = model.capture()
    if not isinstance(payload, bytes):
        raise MLInputError(
            f"A model's capture is bytes; {model.model_kind!r} returned {type(payload).__name__}."
        )
    return store.put(payload).uri


def train_model[M: Model](
    model: M, training: TrainingSet, *, seed: int, store: ArtifactStore
) -> tuple[M, TrainingRecord]:
    """Fit ``model`` on ``training``, file the fitted model, and record what it was trained from.

    Returns:
        The fitted model and its :class:`TrainingRecord`, whose ``artifact_id``
        is the name a strategy declares.
    """

    fitted = model.fit(training, seed=_seed(seed))
    artifact_id = file_model(fitted, store)
    record = TrainingRecord(
        artifact_id=artifact_id,
        model_kind=fitted.model_kind,
        hyperparameters=MappingProxyType(dict(fitted.hyperparameters())),
        seed=seed,
        training_id=training.training_id,
    )
    return fitted, record


def restore_model[M: Model](kind: type[M], store: ArtifactStore, artifact_id: str) -> M:
    """The model ``artifact_id`` names, restored as ``kind`` from ``store``.

    The store verifies the bytes against the digest the id names before the
    model reads them, so altered bytes are refused, never restored.

    Raises:
        MLInputError: If ``artifact_id`` is not an artifact id, or the bytes
            are not a fitted ``kind``.
        StorageError: If the store does not hold the artifact, or its bytes do
            not hash to the id.
    """

    if not isinstance(artifact_id, str) or not artifact_id.startswith(ARTIFACT_URI_SCHEME):
        raise MLInputError(
            f"A model is named by its artifact id, {ARTIFACT_URI_SCHEME}<hex>; got {artifact_id!r}."
        )
    payload = store.get(ArtifactRef(uri=artifact_id))
    return kind.restore(payload)


def canonical_capture(
    model_kind: str,
    hyperparameters: Mapping[str, ParamValue],
    features: Sequence[str],
    state: Mapping[str, Any],
) -> bytes:
    """A core model's canonical bytes: sorted keys, no whitespace, floats by shortest ``repr``.

    Raises:
        MLInputError: If a value is not finite, which no capture holds.
    """

    document = {
        "scheme": MODEL_SCHEME,
        "model_kind": model_kind,
        "hyperparameters": dict(hyperparameters),
        "features": list(features),
        "state": state,
    }
    try:
        text = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
    except ValueError as refusal:
        raise MLInputError(
            f"A {model_kind} holding a value that is not finite cannot be captured: {refusal}"
        ) from refusal
    return text.encode("ascii")


@dataclass(frozen=True, slots=True)
class Capture:
    """A canonical capture, read.

    Attributes:
        model_kind: The kind it captures.
        hyperparameters: Its hyperparameters, as captured.
        features: The feature names it was fitted on.
        state: Its fitted state, as the model wrote it.
    """

    model_kind: str
    hyperparameters: Mapping[str, ParamValue]
    features: tuple[str, ...]
    state: Mapping[str, Any]


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a finite number")


def read_capture(artifact: bytes, model_kind: str) -> Capture:
    """Read ``artifact`` as the canonical capture of a ``model_kind``.

    Raises:
        MLInputError: If the bytes are not a canonical capture, or capture
            another kind.
    """

    if not isinstance(artifact, bytes):
        raise MLInputError(f"An artifact is bytes, got {type(artifact).__name__}.")
    try:
        document = json.loads(artifact.decode("ascii"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError) as refusal:
        raise MLInputError(f"The bytes are not a canonical model capture: {refusal}") from refusal
    if not isinstance(document, dict) or set(document) != _CAPTURE_KEYS:
        raise MLInputError(
            f"The bytes are not a canonical model capture: one holds exactly "
            f"{sorted(_CAPTURE_KEYS)}."
        )
    if document["scheme"] != MODEL_SCHEME:
        raise MLInputError(
            f"The capture's scheme is {document['scheme']!r}; this AlphaLab reads {MODEL_SCHEME!r}."
        )
    if document["model_kind"] != model_kind:
        raise MLInputError(f"The bytes capture a {document['model_kind']!r}, not a {model_kind!r}.")
    hyperparameters = document["hyperparameters"]
    if not isinstance(hyperparameters, dict) or not all(
        isinstance(value, str | int | float | bool) for value in hyperparameters.values()
    ):
        raise MLInputError("The capture's hyperparameters are not parameter values.")
    state = document["state"]
    if not isinstance(state, dict):
        raise MLInputError("The capture's state is not a mapping.")
    return Capture(
        model_kind=model_kind,
        hyperparameters=MappingProxyType(hyperparameters),
        features=_names(document["features"], "The capture"),
        state=MappingProxyType(state),
    )


def restored[M: Model](model: M, artifact: bytes) -> M:
    """``model``, restored from ``artifact``, if it captures to exactly those bytes.

    The last step of every core model's restore: bytes that parse to a model
    but differ from that model's capture -- reordered keys, another rendering
    of a float, whitespace -- are not its canonical bytes, and a second name for
    one model would let two artifacts claim it.

    Raises:
        MLInputError: If the model captures to other bytes.
    """

    if model.capture() != artifact:
        raise MLInputError(
            f"The bytes describe a {model.model_kind!r} but are not its canonical capture; "
            "one model has one artifact."
        )
    return model
