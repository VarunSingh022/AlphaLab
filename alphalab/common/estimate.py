"""The identity of an estimate (v4.3, ADR-0058 section 6).

A fit returns an :class:`Estimate`: its parameters at full precision, their
standard errors, the method under its own scheme, the identity of the data it
was fitted on, the settings it was given, its convergence report and the
numerical class of its values. It is research evidence that says what was
estimated, from what and how.

What the identity covers
------------------------
:attr:`Estimate.estimate_id` is SHA-256 over :func:`canonical_estimate_key`,
which always renders **the estimation problem**: the method, the data's
identity, every setting the caller stated (the model's choices, the starting
point), and -- when the method iterates -- the tolerance and limit it was given.

It renders **the fitted values** -- parameters, standard errors, and the
iterations and measure the method stopped at -- only when they are class 1 or
2 (ADR-0058 section 3): exact, or computed by correctly rounded operations in a
fixed order, and so the same bits on every host. Least squares by Householder
QR is class 2, and its coefficients are in its identity. A likelihood maximised
through ``log`` is class 3: two hosts may differ in its last bits, so its
parameters are recorded beside the identity, never in it, and compared by a
stated tolerance. Two hosts that fit the same problem agree on its identity
either way.

The key is newline-separated ``name=value`` lines under the scheme
:data:`ESTIMATE_KEY_SCHEME`, names sorted, every float its shortest ``repr``,
every string JSON-quoted, and every setting tagged with its type, so no two
different estimates render the same key.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import AlphaLabValidationError

__all__ = [
    "ESTIMATE_KEY_SCHEME",
    "Estimate",
    "NumericalClass",
    "Setting",
    "canonical_estimate_key",
    "derive_estimate_id",
]

#: The scheme of :func:`canonical_estimate_key`; a change to the key is a new scheme.
ESTIMATE_KEY_SCHEME: Final = "alphalab.estimate.v1"

#: A setting's value: what a caller states about the problem.
Setting = str | int | float | bool


class NumericalClass(Enum):
    """How a value was computed, and so whether it may enter an identity (ADR-0058 section 3)."""

    #: Integers, ``Fraction``, ``Decimal`` under a stated context, hash-counter integers.
    EXACT = 1
    #: IEEE-754 ``+ - * /``, ``sqrt``, ``fsum`` and the other correctly rounded operations.
    CORRECTLY_ROUNDED = 2
    #: Anything through the host's libm: ``exp``, ``log``, ``pow``, ``erf``.
    HOST_LIBM = 3

    @property
    def identity_bearing(self) -> bool:
        """Whether values of this class are the same bits on every host."""

        return self is not NumericalClass.HOST_LIBM


def _name(name: object, what: str) -> str:
    if not isinstance(name, str) or not name or any(c in name for c in "=\n\r"):
        raise AlphaLabValidationError(
            f"{what} {name!r} must be a non-empty string without '=' or a line break."
        )
    return name


def _value(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise AlphaLabValidationError(f"{what} is {value!r}; it must be a finite number.")
    return float(value)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\n\r"):
        raise AlphaLabValidationError(
            f"{what} must be a non-empty string on one line, got {value!r}."
        )
    return value


def _render(value: Setting) -> str:
    if isinstance(value, bool):
        return f"bool:{'true' if value else 'false'}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        return f"float:{value!r}"
    return f"str:{json.dumps(value, ensure_ascii=False)}"


@dataclass(frozen=True, slots=True)
class Estimate:
    """A fitted model's parameters and the problem they answer (ADR-0058 section 6).

    Attributes:
        method: The method under its own versioned scheme, such as
            ``"alphalab.common.linalg.least_squares/householder-qr.v1"``.
        data_id: The identity of the data the model was fitted on, as its own
            record states it: a dataset version, a series id, a result id.
        parameters: Each parameter by name, at full precision. At least one.
        standard_errors: Standard errors by parameter name; every name must
            be a parameter's. Empty when the method gives none.
        settings: Everything the caller stated about the problem: the model's
            choices, the starting point, any tolerance the convergence report
            does not carry.
        numerical_class: The class of the fitted values, which decides
            whether they enter the identity.
        convergence: How an iterative method stopped; ``None`` for a direct
            one. Its tolerance and limit are part of the problem.

    Raises:
        AlphaLabValidationError: If a field is malformed: an empty or
            multi-line method or data id, no parameters, a name with ``=`` or a
            line break, a value that is not a finite number, a negative
            standard error or one for no parameter, or a setting of another
            type.
    """

    method: str
    data_id: str
    parameters: Mapping[str, float]
    standard_errors: Mapping[str, float]
    settings: Mapping[str, Setting]
    numerical_class: NumericalClass
    convergence: ConvergenceReport | None = None

    def __post_init__(self) -> None:
        _text(self.method, "An estimate's method")
        _text(self.data_id, "An estimate's data id")
        if not isinstance(self.numerical_class, NumericalClass):
            raise AlphaLabValidationError(
                f"numerical_class must be a NumericalClass, got {self.numerical_class!r}."
            )
        if self.convergence is not None and not isinstance(self.convergence, ConvergenceReport):
            raise AlphaLabValidationError(
                f"convergence must be a ConvergenceReport or None, got {self.convergence!r}."
            )
        parameters = {
            _name(name, "A parameter name"): _value(value, f"Parameter {name!r}")
            for name, value in self.parameters.items()
        }
        if not parameters:
            raise AlphaLabValidationError("An estimate needs at least one parameter.")
        errors = {}
        for name, value in self.standard_errors.items():
            if name not in parameters:
                raise AlphaLabValidationError(
                    f"A standard error is given for {name!r}, which is not a parameter."
                )
            error = _value(value, f"The standard error of {name!r}")
            if error < 0.0:
                raise AlphaLabValidationError(
                    f"The standard error of {name!r} is {value!r}; it cannot be negative."
                )
            errors[name] = error
        settings: dict[str, Setting] = {}
        for key, setting in self.settings.items():
            _name(key, "A setting name")
            if isinstance(setting, float) and not math.isfinite(setting):
                raise AlphaLabValidationError(f"Setting {key!r} is {setting!r}; it must be finite.")
            if not isinstance(setting, str | int | float | bool):
                raise AlphaLabValidationError(
                    f"Setting {key!r} is {setting!r}; a setting is a string, an integer, a "
                    "float or a boolean."
                )
            settings[key] = setting
        object.__setattr__(self, "parameters", MappingProxyType(dict(sorted(parameters.items()))))
        object.__setattr__(self, "standard_errors", MappingProxyType(dict(sorted(errors.items()))))
        object.__setattr__(self, "settings", MappingProxyType(dict(sorted(settings.items()))))

    @property
    def estimate_id(self) -> str:
        """SHA-256 over :func:`canonical_estimate_key`."""

        return derive_estimate_id(self)


def canonical_estimate_key(estimate: Estimate) -> str:
    """The text an estimate's identity is the digest of; see the module docstring."""

    lines = [
        ESTIMATE_KEY_SCHEME,
        f"method={json.dumps(estimate.method, ensure_ascii=False)}",
        f"data={json.dumps(estimate.data_id, ensure_ascii=False)}",
        f"class={estimate.numerical_class.value}",
        "settings",
        *(f"{name}={_render(value)}" for name, value in sorted(estimate.settings.items())),
    ]
    report = estimate.convergence
    if report is not None:
        lines += [
            "convergence",
            f"method={json.dumps(report.method, ensure_ascii=False)}",
            f"criterion={json.dumps(report.criterion, ensure_ascii=False)}",
            f"tolerance={report.tolerance!r}",
            f"limit={report.limit}",
        ]
    if estimate.numerical_class.identity_bearing:
        lines += [
            "parameters",
            *(f"{name}={value!r}" for name, value in sorted(estimate.parameters.items())),
            "standard_errors",
            *(f"{name}={value!r}" for name, value in sorted(estimate.standard_errors.items())),
        ]
        if report is not None:
            lines += ["outcome", f"iterations={report.iterations}", f"reached={report.reached!r}"]
    return "\n".join(lines)


def derive_estimate_id(estimate: Estimate) -> str:
    """SHA-256 over :func:`canonical_estimate_key`, as hexadecimal."""

    return hashlib.sha256(canonical_estimate_key(estimate).encode("utf-8")).hexdigest()
