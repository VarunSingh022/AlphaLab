"""A run's recorded figures are the same on every host (v4.1, ledger LIM-005).

v4.0 stated that certified identities are "per host class": a float computed
through another ``libm`` may differ in its last bit, and so may an identity over
it. v4.1 traced which floats a run records. Money is ``Decimal``, the same
everywhere. Every other float a run records -- the analytics report compiled
into it at the end -- comes from ``+ - * /``, ``sqrt`` and ``fsum``, which IEEE
754 and CPython define to the bit, **except one**: CAGR and the geometric
return raised a ratio to a fractional power with ``math.pow``, which IEEE 754
does not require to be correctly rounded. Measured on macOS arm64, it was not,
for 409 of 300,000 inputs -- so one run could record two CAGRs, and carry two
``result_id`` s, on a Mac and on a Linux server.

``alphalab.analytics.returns._power`` now computes that power in ``decimal`` --
the same software on every host -- and rounds it once. These tests hold the
values it must give, its agreement with a 120-digit reference, and the report
path's freedom from any other host-dependent function, so a later change cannot
bring one back unnoticed. Hosted CI checks the release certificate on Linux and
macOS under CPython 3.12, 3.13 and 3.14.
"""

from __future__ import annotations

import ast
import decimal
import math
import random
from decimal import Decimal
from pathlib import Path

import pytest

from alphalab.analytics.returns import _power, cagr, geometric_return

ROOT = Path(__file__).resolve().parents[2]

#: Inputs where the development host's ``math.pow`` was not correctly rounded,
#: with the correctly rounded power (and, in the comment, what ``libm`` gave).
_CORRECTLY_ROUNDED = [
    (0.8537059140201401, 0.04895887962075716, 0.9922861526937221),  # libm: ...7222
    (3.227123898790133, 0.05065104957202614, 1.0611384374085426),  # libm: ...5424
    (4.232213092140721, 0.09538972067476612, 1.147540709643026),  # libm: ...30261
    (7.939397283960453, 0.026560864364211074, 1.0565720907551364),  # libm: ...1361
    (5.3789146585514205, 0.031670472118911774, 1.054730353868866),  # libm: ...88657
    (2.169479555325148, 0.033465419930696134, 1.0262573490289413),  # libm: ...28941
    (0.006995984571270865, 0.9674882289825646, 0.008220848105875022),  # libm: ...75502
]

_REFERENCE = decimal.Context(prec=120, Emax=10**6, Emin=-(10**6))


def _reference(base: float, exponent: float) -> float:
    return float(_REFERENCE.power(Decimal(base), Decimal(exponent)))


@pytest.mark.parametrize(("base", "exponent", "expected"), _CORRECTLY_ROUNDED)
def test_the_power_is_the_correctly_rounded_one(
    base: float, exponent: float, expected: float
) -> None:
    assert _power(base, exponent) == expected == _reference(base, exponent)


def test_the_power_agrees_with_a_120_digit_reference_on_generated_inputs() -> None:
    """Seeded, so the same 3,000 inputs every run: ratios and periods a CAGR meets."""

    generator = random.Random(4_100_005)
    for _ in range(3_000):
        base = math.exp(generator.uniform(-4.0, 4.0))
        exponent = 1.0 / generator.uniform(0.002, 60.0)
        assert _power(base, exponent) == _reference(base, exponent), (base, exponent)


def test_a_cagr_carries_the_correctly_rounded_power() -> None:
    base, exponent, expected = _CORRECTLY_ROUNDED[1]
    years = 1.0 / exponent
    assert 1.0 / years == exponent, "the period round-trips to the exponent tested"

    assert cagr(Decimal("1"), Decimal(base), years) == expected - 1.0


def test_the_power_overflows_as_math_pow_does_and_a_cagr_reports_none() -> None:
    with pytest.raises(OverflowError):
        _power(10.0, 400.0)
    with pytest.raises(OverflowError):
        math.pow(10.0, 400.0)
    # A doubling over one second, annualized, is a number no float holds.
    assert cagr(Decimal("1"), Decimal("2"), 1.0 / 31_557_600.0) is None


def test_the_edges_of_a_geometric_return() -> None:
    assert geometric_return((-1.0, 0.5)) == -1.0, "a total loss compounds to nothing"
    assert _power(0.0, 0.5) == 0.0
    assert _power(1.0, 123.456) == 1.0
    assert geometric_return((0.1, 0.1)) == _reference(1.1 * 1.1, 0.5) - 1.0


#: What a run's report is computed by, from ``AnalyticsEngine.compile_report`` down.
_REPORT_PATH = [
    "alphalab/analytics/engine.py",
    "alphalab/analytics/metrics.py",
    "alphalab/analytics/drawdown.py",
    "alphalab/analytics/returns.py",
    "alphalab/analytics/attribution.py",
    "alphalab/analytics/exposure.py",
    "alphalab/analytics/summary.py",
    "alphalab/analytics/report.py",
    "alphalab/analytics/validation.py",
]

#: ``math`` functions IEEE 754 or CPython itself define exactly, on every host.
_EXACT = {"sqrt", "fsum", "isfinite", "isnan", "isinf", "copysign", "floor", "ceil", "fabs"}


def _host_dependent(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "math"
            and node.func.attr not in _EXACT
        ):
            found.append(f"line {node.lineno}: math.{node.func.attr}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = node.right
            if not (isinstance(exponent, ast.Constant) and isinstance(exponent.value, int)):
                found.append(f"line {node.lineno}: {ast.unparse(node)}")
    return found


def test_the_report_path_calls_no_host_dependent_function() -> None:
    offenders = {
        path: found
        for path in _REPORT_PATH
        if (found := _host_dependent((ROOT / path).read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_the_guard_finds_a_libm_call_and_a_fractional_power() -> None:
    """A guard on the guard: the v4.0 form of CAGR is found."""

    v40 = "def cagr(r, y):\n    return math.pow(r, 1.0 / y) - 1.0\n"
    assert _host_dependent(v40) == ["line 2: math.pow"]
    assert _host_dependent("x = r ** (1 / y)\n") == ["line 1: r ** (1 / y)"]
    assert _host_dependent("x = math.sqrt(v) * r ** 2\n") == []


def test_the_variance_the_report_uses_is_exact_arithmetic() -> None:
    """``sample_variance`` is the report path's one call outside ``analytics``."""

    source = (ROOT / "alphalab" / "common" / "statistics.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    (function,) = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "sample_variance"
    ]
    assert _host_dependent(ast.unparse(function)) == []
