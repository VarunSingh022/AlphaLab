"""What every adapter shares: a fit's checks, the artifact's header, parameters stated exactly.

An adapter's artifact is one line of canonical JSON -- sorted keys, no spaces,
ASCII, no non-finite number -- then a newline and the payload: the framework's
own serialisation of the fitted model (ADR-0064 section 4). The header names
the scheme, the model's kind, the features it was fitted on, the framework's and
the adapter's versions and the payload's SHA-256. A restore reads it strictly:
every key and no other, the scheme, the versions, the digest and the features
each checked, and in the end the restored model's own capture compared with the
bytes it came from, so one model has one artifact.

Parameters, where an adapter states them, are stated exactly: ``None``,
booleans, integers, strings and finite floats as themselves; a non-finite float
as ``{"float": "nan"}``, ``"inf"`` or ``"-inf"``; a list as a list and a tuple
as ``{"tuple": [...]}``. Anything else -- a callable, an object, a mapping -- is
refused: a model restored from its bytes is constructed from its parameters, and
bytes cannot carry code.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Final

from sklearn.utils.validation import has_fit_parameter

from alphalab.common.types import ParamValue
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.protocol import FeatureMatrix, TrainingSet

#: The adapters' version, recorded in every artifact they capture.
ADAPTER_VERSION: Final = "0.1.0"

_NON_FINITE: Final = frozenset({"nan", "inf", "-inf"})


def parameter(value: object) -> ParamValue:
    """A parameter as a parameter value: itself when it is one, else its ``repr``."""

    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return repr(value)


def declared(params: Mapping[str, object]) -> dict[str, ParamValue]:
    """Every parameter, by name, as a parameter value or its ``repr``."""

    return {name: parameter(params[name]) for name in sorted(params)}


def require_training(training: object, seed: object) -> TrainingSet:
    """``training``, a TrainingSet, fitted with an integer seed.

    Raises:
        MLInputError: If ``training`` is not a TrainingSet or the seed is not
            an integer.
    """

    if not isinstance(training, TrainingSet):
        raise MLInputError(f"A model is fitted on a TrainingSet, got {training!r}.")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise MLInputError(f"A seed is an integer, got {seed!r}.")
    return training


def require_weights(estimator: object, training: TrainingSet, kind: str) -> None:
    """Refuse a weighted training set for an estimator whose ``fit`` takes no sample weights."""

    if training.weights is not None and not has_fit_parameter(estimator, "sample_weight"):
        raise MLInputError(
            f"{kind}'s fit takes no sample weights, and the training set has them: fit it on a "
            "training set without weights, or choose an estimator that takes them."
        )


def require_binary(training: TrainingSet, kind: str) -> None:
    """Refuse a classifier's training set unless its labels are 0 and 1, both present."""

    labels = sorted(set(training.labels))
    if labels != [0.0, 1.0]:
        raise MLInputError(
            f"The {kind} would classify {labels}; a prediction is P(label 1) of a classifier of "
            "labels 0 and 1, both present."
        )


def unfitted(kind: str) -> MLInputError:
    """The refusal of a model that is not fitted."""

    return MLInputError(f"This {kind} is not fitted: fit it, or restore a fitted one.")


def matched(features: object, fitted: tuple[str, ...], kind: str) -> FeatureMatrix:
    """``features``, refused unless it names the fitted features in their order."""

    if not isinstance(features, FeatureMatrix):
        raise MLInputError(f"A model predicts from a FeatureMatrix, got {features!r}.")
    if features.names != fitted:
        raise MLInputError(
            f"The {kind} was fitted on {list(fitted)}, in that order; the rows name "
            f"{list(features.names)}."
        )
    return features


def captured(header: Mapping[str, object], payload: bytes) -> bytes:
    """The canonical header, a newline, and the payload."""

    line = json.dumps(
        dict(header), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return line.encode("ascii") + b"\n" + payload


def read(
    artifact: object,
    *,
    framework: str,
    scheme: str,
    keys: frozenset[str],
    version_key: str,
    version: str,
    promise: str,
) -> tuple[dict[str, Any], bytes, tuple[str, ...]]:
    """The header, payload and features of an artifact of ``scheme``, each checked.

    ``promise`` says why a restore is held to the framework's version.

    Raises:
        MLInputError: If the artifact is not bytes holding a header line of
            canonical JSON with exactly ``keys``; names another scheme, another
            framework version or another adapter version; its payload does not
            hash to the header's digest; or it names no features.
    """

    if not isinstance(artifact, bytes):
        raise MLInputError(f"An artifact is bytes, got {type(artifact).__name__}.")
    line, newline, payload = artifact.partition(b"\n")
    if not newline:
        raise MLInputError(f"The bytes are not a {framework} artifact: they hold no header line.")
    try:
        header = json.loads(line.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as refusal:
        raise MLInputError(f"The artifact's header is not canonical JSON: {refusal}") from refusal
    if not isinstance(header, dict) or set(header) != keys:
        raise MLInputError(f"A {framework} artifact's header holds exactly {sorted(keys)}.")
    if header["scheme"] != scheme:
        raise MLInputError(
            f"The artifact's scheme is {header['scheme']!r}; this adapter reads {scheme!r}."
        )
    if header[version_key] != version:
        raise MLInputError(
            f"The artifact was captured under {framework} {header[version_key]} and this "
            f"environment has {version}; {promise} Restore it where that version is installed, "
            "or fit it again."
        )
    if header["adapter"] != ADAPTER_VERSION:
        raise MLInputError(
            f"The artifact was captured by alphalab-models {header['adapter']} and this is "
            f"{ADAPTER_VERSION}; restore it with the adapter that captured it."
        )
    if hashlib.sha256(payload).hexdigest() != header["payload_sha256"]:
        raise MLInputError("The artifact's payload does not hash to the digest its header names.")
    features = header["features"]
    if (
        not isinstance(features, list)
        or not features
        or not all(isinstance(name, str) and name for name in features)
    ):
        raise MLInputError("The artifact's header names no features.")
    return header, payload, tuple(features)


def stated(params: Mapping[str, object], kind: str) -> dict[str, object]:
    """``params`` as exact JSON, by name.

    Raises:
        MLInputError: If a parameter is not a value the module docstring lists.
    """

    return {name: _stated(params[name], kind, name) for name in sorted(params)}


def _stated(value: object, kind: str, name: str) -> object:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else {"float": repr(float(value))}
    if isinstance(value, list):
        return [_stated(item, kind, name) for item in value]
    if isinstance(value, tuple):
        return {"tuple": [_stated(item, kind, name) for item in value]}
    raise MLInputError(
        f"The {kind}'s parameter {name!r} is {value!r}. An adapter's parameters are values -- "
        "None, booleans, numbers, strings, and lists or tuples of them -- because a model "
        "restored from its bytes is constructed from them, and bytes cannot carry code."
    )


def constructed(parameters: object) -> dict[str, Any]:
    """The keyword arguments a header's stated parameters construct an estimator with.

    Raises:
        MLInputError: If ``parameters`` is not a mapping of names to values the
            module docstring lists.
    """

    if not isinstance(parameters, dict) or not all(isinstance(name, str) for name in parameters):
        raise MLInputError("The artifact's header states no parameters by name.")
    return {name: _constructed(value) for name, value in parameters.items()}


def _constructed(value: object) -> object:
    if isinstance(value, dict):
        if set(value) == {"float"} and value["float"] in _NON_FINITE:
            return float(value["float"])
        if set(value) == {"tuple"} and isinstance(value["tuple"], list):
            return tuple(_constructed(item) for item in value["tuple"])
        raise MLInputError(f"The artifact's header states {value!r}, which no adapter writes.")
    if isinstance(value, list):
        return [_constructed(item) for item in value]
    return value
