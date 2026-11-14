"""A convergence report describes a criterion that was met (ADR-0058, v4.3)."""

from __future__ import annotations

import math

import pytest

from alphalab.common.convergence import ConvergenceReport
from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError


def _report(**changes: object) -> ConvergenceReport:
    fields: dict[str, object] = {
        "method": "the cyclic Jacobi eigensystem",
        "criterion": "off-diagonal norm over the matrix norm",
        "tolerance": 1e-12,
        "limit": 30,
        "iterations": 6,
        "reached": 3e-15,
    }
    fields.update(changes)
    return ConvergenceReport(**fields)  # type: ignore[arg-type]


def test_a_report_keeps_what_it_was_given() -> None:
    report = _report()

    assert (report.tolerance, report.limit, report.iterations, report.reached) == (
        1e-12,
        30,
        6,
        3e-15,
    )
    with pytest.raises(AttributeError):
        report.iterations = 7  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"iterations": 0, "reached": 0.0},
        {"iterations": 30},
        {"reached": 1e-12},
        {"tolerance": 1, "reached": 0.5},
    ],
)
def test_the_edges_of_each_range_are_accepted(changes: dict[str, object]) -> None:
    _report(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"method": ""},
        {"method": "  "},
        {"criterion": ""},
        {"criterion": 3},
        {"tolerance": 0.0},
        {"tolerance": -1e-9},
        {"tolerance": math.inf},
        {"tolerance": math.nan},
        {"tolerance": True},
        {"limit": 0},
        {"limit": True},
        {"limit": 2.0},
        {"iterations": -1},
        {"iterations": 31},
        {"iterations": False},
        {"reached": -1e-20},
        {"reached": 2e-12},
        {"reached": math.nan},
        {"reached": math.inf},
    ],
)
def test_a_report_out_of_range_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(AlphaLabValidationError):
        _report(**changes)


def test_a_report_short_of_its_criterion_is_an_error_not_a_report() -> None:
    """What was reached above the tolerance is ConvergenceError's to carry."""

    with pytest.raises(AlphaLabValidationError, match="criterion that was met"):
        _report(reached=1e-6)
    error = ConvergenceError("the cyclic Jacobi eigensystem", 1e-12, 30, 1e-6)
    assert (error.tolerance, error.limit, error.reached) == (1e-12, 30, 1e-6)
