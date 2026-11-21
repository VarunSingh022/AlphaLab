"""S-02's screening stage: 200 candidate pairs, the family corrected (v4.3, ADR-0074 s. 8).

* **Two hundred pairs screened** (M6's exit gate): planted cointegrated pairs
  are selected under Holm's family-wise correction, and no independent walk
  is.
* **The p-values are MacKinnon's**: each pair's is the test's own, and the
  family's adjusted values are the correction's over every candidate.
* **Every pair is counted**: a pair the test refuses enters the family with a
  p-value of one, and so does one bounded only from below; one bounded from
  above enters as its bound.
* **The identity is the problem and the class-2 results**: it moves with the
  sample, a choice or a series, and is stable for the same screen.
* **What it cannot take is refused.**
"""

from __future__ import annotations

import dataclasses
import math
import random

import pytest

from alphalab.econometrics import (
    Deterministic,
    FixedLags,
    LagCriterion,
    PBound,
    PValue,
    SelectedLags,
    engle_granger,
)
from alphalab.research.cointegration_screen import (
    CointegrationScreen,
    _entered,
    cointegration_screen,
)
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.multiple_testing import Correction, correct_p_values

CONDITION = 1e10
LAGS = FixedLags(1)


def _universe(seed: int, walks: int, planted: int, count: int) -> dict[str, list[float]]:
    """Independent random walks, and for each of the first ``planted`` a partner tied to it."""

    draws = random.Random(seed)
    universe: dict[str, list[float]] = {}
    for i in range(walks):
        walk = [0.0]
        for _ in range(count - 1):
            walk.append(walk[-1] + draws.gauss(0.0, 1.0))
        universe[f"W{i:02d}"] = walk
    for i in range(planted):
        base = universe[f"W{i:02d}"]
        noise, partner = 0.0, []
        for value in base:
            noise = 0.5 * noise + draws.gauss(0.0, 0.5)
            partner.append(2.0 + 1.5 * value + noise)
        universe[f"P{i:02d}"] = partner
    return universe


def _screen(
    universe: dict[str, list[float]],
    pairs: list[tuple[str, str]],
    *,
    start: int = 0,
    stop: int | None = None,
    correction: Correction = Correction.HOLM,
    alpha: float = 0.05,
) -> CointegrationScreen:
    return cointegration_screen(
        universe,
        pairs,
        start=start,
        stop=len(next(iter(universe.values()))) if stop is None else stop,
        deterministic=Deterministic.CONSTANT,
        lags=LAGS,
        maximum_condition=CONDITION,
        correction=correction,
        alpha=alpha,
    )


def test_two_hundred_pairs_are_screened_and_the_planted_ones_selected() -> None:
    universe = _universe(11, 20, 5, 250)
    names = sorted(universe)
    every = [(a, b) for i, a in enumerate(names) for b in names[i + 1 :]]
    planted = [(f"P{i:02d}", f"W{i:02d}") for i in range(5)]
    others = [pair for pair in every if pair not in planted and (pair[1], pair[0]) not in planted]
    candidates = planted + others[: 200 - len(planted)]
    screen = _screen(universe, candidates)

    assert len(screen.pairs) == 200 and screen.family.trials == 200
    selected = {(pair.dependent, pair.regressor) for pair in screen.selected}
    assert set(planted) <= selected
    tied = {f"W{i:02d}" for i in range(5)} | {f"P{i:02d}" for i in range(5)}
    for dependent, regressor in selected - set(planted):
        # Anything else selected is two partners of one walk, or two of its relatives.
        assert {dependent, regressor} <= tied
    for pair in screen.selected:
        if (pair.dependent, pair.regressor) in planted:
            assert pair.hedge_ratio == pytest.approx(1.5, abs=0.05)


def test_each_p_value_is_the_tests_and_the_family_the_corrections() -> None:
    universe = _universe(3, 6, 2, 200)
    pairs = [("P00", "W00"), ("W02", "W03"), ("P01", "W01"), ("W04", "W05")]
    screen = _screen(universe, pairs, start=20, stop=180, correction=Correction.BENJAMINI_HOCHBERG)

    expected = {}
    for pair, (dependent, regressor) in zip(screen.pairs, pairs, strict=True):
        test = engle_granger(
            universe[dependent][20:180],
            [universe[regressor][20:180]],
            deterministic=Deterministic.CONSTANT,
            lags=LAGS,
            maximum_condition=CONDITION,
        )
        assert pair.test == test
        expected[pair.hypothesis] = pair.p_value
        assert test.p_value is not None
        assert pair.p_value == (
            1.0 if test.p_value.bound is PBound.AT_LEAST else test.p_value.value
        )
    family = correct_p_values(expected, Correction.BENJAMINI_HOCHBERG, 0.05)
    assert screen.family == family
    assert (screen.start, screen.stop) == (20, 180)


def test_a_refused_pair_is_counted_with_a_p_value_of_one_and_never_selected() -> None:
    universe = _universe(5, 3, 1, 120)
    universe["FLAT"] = [3.0] * 120
    screen = _screen(universe, [("P00", "W00"), ("W01", "FLAT"), ("W01", "W02")])

    refused = screen.refused
    assert [(pair.dependent, pair.regressor) for pair in refused] == [("W01", "FLAT")]
    assert refused[0].p_value == 1.0 and refused[0].hedge_ratio is None
    assert refused[0].refusal is not None and "rank deficient" in refused[0].refusal
    assert screen.family.trials == 3
    assert "W01~FLAT" not in screen.family.rejected


def test_a_bound_enters_the_family_on_its_conservative_side() -> None:
    universe = _universe(7, 2, 1, 150)
    test = engle_granger(
        universe["P00"],
        [universe["W00"]],
        deterministic=Deterministic.CONSTANT,
        lags=LAGS,
        maximum_condition=CONDITION,
    )

    above = dataclasses.replace(test, p_value=PValue(0.001, PBound.AT_MOST, "MacKinnon"))
    below = dataclasses.replace(test, p_value=PValue(0.95, PBound.AT_LEAST, "MacKinnon"))
    within = dataclasses.replace(test, p_value=PValue(0.2, None, "MacKinnon"))
    unknown = dataclasses.replace(test, p_value=None)
    assert [_entered(t) for t in (above, below, within, unknown)] == [0.001, 1.0, 0.2, 1.0]


def test_the_identity_is_the_problems_and_moves_with_each_part() -> None:
    universe = _universe(9, 4, 1, 160)
    pairs = [("P00", "W00"), ("W01", "W02")]
    base = _screen(universe, pairs).screen_id

    moved = {name: list(values) for name, values in universe.items()}
    moved["W02"][7] += 2.0**-30
    assert _screen(universe, pairs).screen_id == base
    assert _screen(universe, pairs, start=1).screen_id != base
    assert _screen(universe, pairs, alpha=0.1).screen_id != base
    assert _screen(universe, pairs, correction=Correction.BONFERRONI).screen_id != base
    assert _screen(universe, list(reversed(pairs))).screen_id != base
    assert _screen(moved, pairs).screen_id != base
    selected = cointegration_screen(
        universe,
        pairs,
        start=0,
        stop=160,
        deterministic=Deterministic.CONSTANT,
        lags=SelectedLags(LagCriterion.AIC, 4, None),
        maximum_condition=CONDITION,
        correction=Correction.HOLM,
        alpha=0.05,
    )
    assert selected.screen_id != base


UNIVERSE = _universe(1, 3, 1, 100)


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"pairs": []}, "one candidate pair at least"),
        ({"pairs": [("W00",)]}, "two series' names"),
        ({"pairs": [("W00", "W00")]}, "names one series twice"),
        ({"pairs": [("W00", "W01"), ("W00", "W01")]}, "a candidate twice"),
        ({"pairs": [("W00", "ZZZ")]}, "'ZZZ', which is not given"),
        ({"series": {**UNIVERSE, "W01": UNIVERSE["W01"][:-1]}}, "one length"),
        ({"series": {**UNIVERSE, "W01": [*UNIVERSE["W01"][:-1], math.nan]}}, "not finite"),
        ({"start": 50, "stop": 50}, r"\[50, 50\) is not within"),
        ({"stop": 101}, "is not within"),
        ({"start": True}, "start is an index"),
        ({"deterministic": Deterministic.NONE}, "a constant at least"),
        ({"lags": 1}, "FixedLags or SelectedLags"),
        ({"maximum_condition": math.nan}, "maximum_condition"),
        ({"correction": "holm"}, "must be a Correction"),
        ({"alpha": 1.0}, "alpha must lie strictly between 0 and 1"),
    ],
)
def test_what_a_screen_cannot_take_is_refused(arguments: dict[str, object], match: str) -> None:
    call: dict[str, object] = {
        "series": UNIVERSE,
        "pairs": [("P00", "W00"), ("W01", "W02")],
        "start": 0,
        "stop": 100,
        "deterministic": Deterministic.CONSTANT,
        "lags": LAGS,
        "maximum_condition": CONDITION,
        "correction": Correction.HOLM,
        "alpha": 0.05,
        **arguments,
    }
    with pytest.raises(ResearchValidationError, match=match):
        cointegration_screen(**call)  # type: ignore[arg-type]
