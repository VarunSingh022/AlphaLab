"""A series' identity, and the checks every estimator makes of it (v4.3, ADR-0074 section 3).

An estimate says what it was fitted on by an identity derived from the values
themselves, never by a name the caller types (ADR-0017's rule for evidence). So
two fits of the same numbers name the same data, and a fit of other numbers
cannot borrow their name.

* :func:`series_identity` is SHA-256 over one series' values, each its shortest
  ``repr``, under :data:`SERIES_SCHEME`.
* :func:`system_identity` is SHA-256 over several series' identities, in their
  order: a cointegrating regression of ``y`` on ``x`` is not one of ``x`` on
  ``y``.

Both are class 1 (ADR-0058): a digest of exact representations.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Final

from alphalab.econometrics.exceptions import EconometricsInputError

__all__ = ["SERIES_SCHEME", "SYSTEM_SCHEME", "require_series", "series_identity", "system_identity"]

#: The scheme of :func:`series_identity`; a change to what it digests is a new scheme.
SERIES_SCHEME: Final = "alphalab.econometrics.series.v1"

#: The scheme of :func:`system_identity`.
SYSTEM_SCHEME: Final = "alphalab.econometrics.system.v1"


def require_series(values: Sequence[float], minimum: int, what: str) -> tuple[float, ...]:
    """``values`` as finite floats, at least ``minimum`` of them.

    Raises:
        EconometricsInputError: If a value is not a finite number, or there are
            fewer than ``minimum``.
    """

    held: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise EconometricsInputError(f"{what} holds {value!r} at {index}: not a number.")
        number = float(value)
        if not math.isfinite(number):
            raise EconometricsInputError(f"{what} holds {value!r} at {index}: not finite.")
        held.append(number)
    if len(held) < minimum:
        raise EconometricsInputError(
            f"{what} has {len(held)} values, and needs at least {minimum}."
        )
    return tuple(held)


def series_identity(values: Sequence[float]) -> str:
    """SHA-256 over ``values``' shortest ``repr``s, under :data:`SERIES_SCHEME`.

    Raises:
        EconometricsInputError: If a value is not a finite number, or there is none.
    """

    held = require_series(values, 1, "A series")
    text = "\n".join([SERIES_SCHEME, *(repr(value) for value in held)])
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def system_identity(series: Sequence[Sequence[float]]) -> str:
    """SHA-256 over each series' :func:`series_identity`, in order, under :data:`SYSTEM_SCHEME`.

    Raises:
        EconometricsInputError: If there is no series, or one is malformed.
    """

    if not series:
        raise EconometricsInputError("A system needs at least one series.")
    text = "\n".join([SYSTEM_SCHEME, *(series_identity(each) for each in series)])
    return hashlib.sha256(text.encode("ascii")).hexdigest()
