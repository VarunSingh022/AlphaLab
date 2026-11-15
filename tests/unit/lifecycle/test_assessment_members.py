"""The members a statistic leaves out, and when it has none to take (v4.3, ADR-0073, WS-06).

Two of an experiment assessment's statistics divide by a member's dispersion.
Research refuses a member without one, and the assessment does not invent a
value: it leaves the member out, names it, and reports no statistic when too
few members remain.
"""

from __future__ import annotations

import math

from alphalab.lifecycle.assessment import _deflated, _superior
from alphalab.research.data_snooping import superior_predictive_ability
from alphalab.research.selection import TrialMatrix
from alphalab.research.sharpe_inference import deflated_sharpe_ratio, per_period_sharpe

PERIODS = 40
INSTANTS = tuple(float(t) for t in range(1, PERIODS + 1))


def _wave(scale: float, phase: int) -> tuple[float, ...]:
    return tuple(scale * (0.002 + 0.01 * math.sin(t + phase)) for t in range(PERIODS))


def _matrix(columns: dict[str, tuple[float, ...]]) -> TrialMatrix:
    return TrialMatrix.from_columns(columns, INSTANTS, "per-period return")


def test_the_deflation_leaves_out_members_without_a_sharpe_ratio_and_counts_the_rest() -> None:
    matrix = _matrix({"idle": (0.0,) * PERIODS, "a": _wave(1.0, 0), "b": _wave(0.5, 3)})

    deflated, flat = _deflated(matrix, 1)

    assert flat == ("idle",)
    assert deflated == deflated_sharpe_ratio(
        matrix.column("a"),
        [per_period_sharpe(matrix.column("a")), per_period_sharpe(matrix.column("b"))],
        independent_trials=2,
    )


def test_a_selection_without_a_sharpe_ratio_has_no_deflated_sharpe_ratio() -> None:
    matrix = _matrix({"idle": (0.0,) * PERIODS, "a": _wave(1.0, 0), "b": _wave(0.5, 3)})

    assert _deflated(matrix, 0) == (None, ("idle",))


def test_fewer_than_two_members_with_a_sharpe_ratio_deflate_nothing() -> None:
    matrix = _matrix({"idle": (0.0,) * PERIODS, "still": (0.0,) * PERIODS, "a": _wave(1.0, 0)})

    assert _deflated(matrix, 2) == (None, ("idle", "still"))


def test_spa_tests_the_varying_members_and_names_the_constant_ones() -> None:
    relative = _matrix(
        {
            "itself": (0.0,) * PERIODS,
            "a": _wave(1.0, 0),
            "carry": (0.0005,) * PERIODS,
            "b": _wave(0.5, 3),
        }
    )

    superior, constant = _superior(relative, replications=50, mean_block_length=4.0, seed=11)

    assert constant == ("itself", "carry"), "a constant of either sign is its own verdict"
    assert superior == superior_predictive_ability(
        _matrix({"a": relative.column("a"), "b": relative.column("b")}),
        replications=50,
        mean_block_length=4.0,
        seed=11,
    )


def test_spa_needs_two_varying_members() -> None:
    relative = _matrix({"itself": (0.0,) * PERIODS, "a": _wave(1.0, 0)})
    tested = _superior(relative, replications=50, mean_block_length=4.0, seed=11)

    assert tested == (None, ("itself",))
