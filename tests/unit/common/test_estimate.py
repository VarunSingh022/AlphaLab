"""The identity of an estimate (v4.3, ADR-0058 section 6).

The identity always covers the estimation problem, and covers the fitted
values only when they are class 1 or 2. Both halves are held here: every
problem field moves the identity, a class-2 value moves it, a class-3 value
does not, and the key cannot render two different estimates the same. Two
identities are pinned for the cross-host check CI runs.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.estimate import (
    ESTIMATE_KEY_SCHEME,
    Estimate,
    NumericalClass,
    canonical_estimate_key,
    derive_estimate_id,
)
from alphalab.common.exceptions import AlphaLabValidationError


def _report(**changes: Any) -> ConvergenceReport:
    fields: dict[str, Any] = {
        "method": "BFGS",
        "criterion": "largest gradient component",
        "tolerance": 1e-8,
        "limit": 200,
        "iterations": 17,
        "reached": 3.5e-9,
    }
    fields.update(changes)
    return ConvergenceReport(**fields)


def _estimate(**changes: Any) -> Estimate:
    fields: dict[str, Any] = {
        "method": "alphalab.common.optimize.bfgs/ornstein-uhlenbeck.v1",
        "data_id": "series:3f2a9c",
        "parameters": {"mean": 0.0125, "speed": 4.75, "volatility": 0.31},
        "standard_errors": {"mean": 0.002, "speed": 0.9},
        "settings": {
            "start_speed": 1.0,
            "observations": 252,
            "annualise": True,
            "basis": "act/365",
        },
        "numerical_class": NumericalClass.CORRECTLY_ROUNDED,
        "convergence": _report(),
    }
    fields.update(changes)
    return Estimate(**fields)


# --------------------------------------------------------------------------- #
# What the identity covers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "alphalab.common.optimize.bfgs/ornstein-uhlenbeck.v2"},
        {"data_id": "series:3f2a9d"},
        {
            "settings": {
                "start_speed": 2.0,
                "observations": 252,
                "annualise": True,
                "basis": "act/365",
            }
        },
        {"settings": {"start_speed": 1.0, "observations": 252, "annualise": True}},
        {"convergence": _report(tolerance=1e-9, reached=3.5e-10)},
        {"convergence": _report(limit=300)},
        {"convergence": None},
    ],
)
@pytest.mark.parametrize("numerical_class", list(NumericalClass))
def test_every_part_of_the_problem_moves_the_identity(
    changes: dict[str, Any], numerical_class: NumericalClass
) -> None:
    base = _estimate(numerical_class=numerical_class)

    assert _estimate(numerical_class=numerical_class, **changes).estimate_id != base.estimate_id


def test_each_numerical_class_is_its_own_identity() -> None:
    identities = {_estimate(numerical_class=kind).estimate_id for kind in NumericalClass}

    assert len(identities) == len(NumericalClass)


@pytest.mark.parametrize(
    "changes",
    [
        {"parameters": {"mean": 0.0125, "speed": 4.750000000000001, "volatility": 0.31}},
        {"standard_errors": {"mean": 0.002, "speed": 0.91}},
        {"convergence": _report(iterations=18)},
        {"convergence": _report(reached=3.6e-9)},
    ],
)
def test_a_class_2_value_is_in_the_identity_to_its_last_bit(changes: dict[str, Any]) -> None:
    assert _estimate(**changes).estimate_id != _estimate().estimate_id


@pytest.mark.parametrize(
    "changes",
    [
        {"parameters": {"mean": 0.0125, "speed": 4.750000000000001, "volatility": 0.31}},
        {"standard_errors": {"mean": 0.002, "speed": 0.91}},
        {"convergence": _report(iterations=18)},
        {"convergence": _report(reached=3.6e-9)},
    ],
)
def test_a_class_3_value_is_beside_the_identity_never_in_it(changes: dict[str, Any]) -> None:
    libm = NumericalClass.HOST_LIBM
    changed = _estimate(numerical_class=libm, **changes)

    assert changed.estimate_id == _estimate(numerical_class=libm).estimate_id
    assert changed != _estimate(numerical_class=libm)


def test_the_order_mappings_are_given_in_does_not_matter() -> None:
    forward = _estimate()
    backward = _estimate(
        parameters={"volatility": 0.31, "speed": 4.75, "mean": 0.0125},
        standard_errors={"speed": 0.9, "mean": 0.002},
        settings={"basis": "act/365", "annualise": True, "observations": 252, "start_speed": 1.0},
    )

    assert backward.estimate_id == forward.estimate_id
    assert list(backward.parameters) == ["mean", "speed", "volatility"]


@pytest.mark.parametrize("value", [1, 1.0, True, "1", "true"])
def test_a_settings_type_is_part_of_its_value(value: Any) -> None:
    # 1, 1.0 and True are one key to a dict, so they are compared as a list.
    values: list[Any] = [1, 1.0, True, "1", "true"]
    identities = [_estimate(settings={"flag": other}).estimate_id for other in values]

    assert len(set(identities)) == 5
    position = next(
        i for i, other in enumerate(values) if type(other) is type(value) and other == value
    )
    assert _estimate(settings={"flag": value}).estimate_id == identities[position]


def test_a_string_cannot_forge_another_line_of_the_key() -> None:
    plain = _estimate(settings={"basis": "act/365", "x": 1})
    forged = _estimate(settings={"basis": 'act/365"\nx=int:1'})

    assert "\n" not in json_line(canonical_estimate_key(forged), "basis")
    assert forged.estimate_id != plain.estimate_id


def json_line(key: str, name: str) -> str:
    return next(line for line in key.splitlines() if line.startswith(f"{name}="))


def test_the_key_is_what_the_module_says_it_is() -> None:
    key = canonical_estimate_key(_estimate())

    assert key.splitlines() == [
        ESTIMATE_KEY_SCHEME,
        'method="alphalab.common.optimize.bfgs/ornstein-uhlenbeck.v1"',
        'data="series:3f2a9c"',
        "class=2",
        "settings",
        "annualise=bool:true",
        'basis=str:"act/365"',
        "observations=int:252",
        "start_speed=float:1.0",
        "convergence",
        'method="BFGS"',
        'criterion="largest gradient component"',
        "tolerance=1e-08",
        "limit=200",
        "parameters",
        "mean=0.0125",
        "speed=4.75",
        "volatility=0.31",
        "standard_errors",
        "mean=0.002",
        "speed=0.9",
        "outcome",
        "iterations=17",
        "reached=3.5e-09",
    ]
    assert derive_estimate_id(_estimate()) == _estimate().estimate_id


def test_values_are_kept_at_full_precision() -> None:
    estimate = _estimate(parameters={"beta": 0.1 + 0.2}, standard_errors={})

    assert estimate.parameters["beta"] == 0.30000000000000004
    assert "beta=0.30000000000000004" in canonical_estimate_key(estimate).splitlines()


def test_only_host_libm_values_leave_the_identity() -> None:
    assert NumericalClass.EXACT.identity_bearing
    assert NumericalClass.CORRECTLY_ROUNDED.identity_bearing
    assert not NumericalClass.HOST_LIBM.identity_bearing


# --------------------------------------------------------------------------- #
# Refusals and immutability
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "changes",
    [
        {"method": ""},
        {"method": "two\nlines"},
        {"data_id": "  "},
        {"parameters": {}},
        {"parameters": {"": 1.0}},
        {"parameters": {"a=b": 1.0}},
        {"parameters": {"mean": math.nan}},
        {"parameters": {"mean": True}},
        {"standard_errors": {"other": 0.1}},
        {"standard_errors": {"mean": -0.1}},
        {"standard_errors": {"mean": math.inf}},
        {"settings": {"tolerance": math.inf}},
        {"settings": {"start": (1.0, 2.0)}},
        {"settings": {"line\nbreak": 1}},
        {"numerical_class": 2},
        {"convergence": "BFGS"},
    ],
)
def test_a_malformed_estimate_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(AlphaLabValidationError):
        _estimate(**changes)


def test_an_estimate_cannot_be_changed() -> None:
    estimate = _estimate()

    with pytest.raises(AttributeError):
        estimate.data_id = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        estimate.parameters["mean"] = 1.0  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Pinned identities: the cross-host check (ADR-0058 section 5)
# --------------------------------------------------------------------------- #

PINNED_CLASS_2 = "896a8071e0c87012ea29fed588c4e3264ebaacf44b299c9d6fc6ece8fed59f73"
PINNED_CLASS_3 = "57714741bfb92804672f183def09515b4bbaf42fe6ec80710f1b40851358cc78"


def test_the_pinned_identities_are_the_same_on_every_host() -> None:
    assert _estimate().estimate_id == PINNED_CLASS_2
    assert _estimate(numerical_class=NumericalClass.HOST_LIBM).estimate_id == PINNED_CLASS_3
