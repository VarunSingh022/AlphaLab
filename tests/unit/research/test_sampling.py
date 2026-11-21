"""Search spaces drawn by a seed, and rounds that keep the best (v4.3, plan I.4).

* **A drawn space is its seed's, everywhere.** The same seed draws the same
  candidates, another seed others, and two spaces are pinned by identity --
  computed identically under CPython 3.12, 3.13 and 3.14, a log-uniform axis
  through ``decimal``'s correctly rounded logarithm and exponential.
* **A Latin hypercube draws every stratum of every axis once**: on a
  log-uniform axis, every decade once; on a discrete axis, every value as
  often as any other.
* **A random space never repeats a candidate**, and refuses to draw more than
  its discrete axes hold.
* **Successive halving keeps the best fraction**, ties to the earlier, never a
  candidate with no score, in the order they had, the budget replaced.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping

import pytest

from alphalab.research import (
    ChoiceAxis,
    IntegerAxis,
    LogUniformAxis,
    ParameterSpace,
    SampledSpace,
    SuccessiveHalving,
    UniformAxis,
    latin_hypercube_space,
    random_space,
)
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.sampling import Axis

AXES: Mapping[str, Axis] = {
    "rate": LogUniformAxis(1e-3, 1.0),
    "depth": IntegerAxis(1, 6),
    "shrink": UniformAxis(0.25, 0.75),
    "loss": ChoiceAxis(("squared", "logistic")),
}


@pytest.mark.parametrize(
    ("draw", "identity"),
    [
        (random_space, "665123665b0c8f66b3cf304388e34b25e23d60f177b510012f5524443cfb0e39"),
        (latin_hypercube_space, "7cdef0bf93d03958af85a529dac599f6ddf4606c76266921a13fc954df5ae01b"),
    ],
)
def test_a_drawn_space_is_its_seeds_on_every_host(
    draw: Callable[..., SampledSpace], identity: str
) -> None:
    sampled = draw(AXES, count=4, seed=17)
    assert sampled.space.space_id == identity
    assert draw(AXES, count=4, seed=17) == sampled
    assert draw(AXES, count=4, seed=18).space != sampled.space
    assert sampled.seed == 17
    assert "seed 17" in sampled.method and "log-uniform[0.001, 1.0)" in sampled.method
    for candidate in sampled.space.candidates:
        assert 1e-3 <= float(candidate["rate"]) < 1.0
        assert candidate["depth"] in range(1, 7)
        assert 0.25 <= float(candidate["shrink"]) < 0.75
        assert candidate["loss"] in ("squared", "logistic")


def test_a_latin_hypercube_draws_every_stratum_of_every_axis_once() -> None:
    sampled = latin_hypercube_space(
        {
            "uniform": UniformAxis(0.0, 1.0),
            "decades": LogUniformAxis(1e-3, 10.0),
            "choice": ChoiceAxis(("a", "b")),
            "integer": IntegerAxis(0, 3),
        },
        count=8,
        seed=5,
    )
    candidates = sampled.space.candidates
    strata = sorted(math.floor(float(c["uniform"]) * 8) for c in candidates)
    assert strata == list(range(8))
    decades = sorted(math.floor(math.log10(float(c["decades"])) + 3) for c in candidates)
    assert decades == [0, 0, 1, 1, 2, 2, 3, 3], "four decades, each twice in eight"
    assert Counter(c["choice"] for c in candidates) == {"a": 4, "b": 4}
    assert Counter(c["integer"] for c in candidates) == {0: 2, 1: 2, 2: 2, 3: 2}


def test_a_random_space_never_repeats_and_refuses_more_than_its_axes_hold() -> None:
    small: Mapping[str, Axis] = {"x": IntegerAxis(1, 3), "y": ChoiceAxis((True, False))}
    sampled = random_space(small, count=6, seed=1)
    assert len(sampled.space) == 6, "every one of the six combinations, none twice"
    with pytest.raises(ResearchValidationError, match="too few"):
        random_space(small, count=7, seed=1)


def test_every_axis_maps_its_coordinates_inside_its_range() -> None:
    below_one = math.nextafter(1.0, 0.0)
    assert float(UniformAxis(0.25, 0.75).value(below_one)) < 0.75
    assert LogUniformAxis(1e-3, 1.0).value(0.0) == 1e-3
    assert float(LogUniformAxis(1e-3, 1.0).value(below_one)) < 1.0
    assert IntegerAxis(1, 6).value(below_one) == 6
    assert ChoiceAxis(("a", "b", "c")).value(below_one) == "c"


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: UniformAxis(1.0, 1.0), "low < high"),
        (lambda: UniformAxis(0.0, math.inf), "finite"),
        (lambda: LogUniformAxis(0.0, 1.0), "0 < low < high"),
        (lambda: IntegerAxis(3, 2), "low <= high"),
        (lambda: IntegerAxis(1.0, 2), "is an integer"),  # type: ignore[arg-type]
        (lambda: ChoiceAxis(()), "at least one value"),
        (lambda: ChoiceAxis(("a", "a")), "repeats a value"),
        (lambda: random_space({}, count=2, seed=1), "at least one axis"),
        (lambda: random_space({"x": (1, 2)}, count=2, seed=1), "UniformAxis"),  # type: ignore[dict-item]
        (lambda: random_space({" x": UniformAxis(0.0, 1.0)}, count=2, seed=1), "not a name"),
        (lambda: random_space(AXES, count=0, seed=1), "at least one candidate"),
        (lambda: latin_hypercube_space(AXES, count=2, seed=True), "seed is an integer"),
        (
            lambda: latin_hypercube_space({"x": ChoiceAxis(("a",))}, count=2, seed=1),
            "repeats a candidate",
        ),
    ],
)
def test_what_a_drawn_space_cannot_take_is_refused(build: Callable[[], object], match: str) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        build()


def _space(count: int) -> ParameterSpace:
    return ParameterSpace.of([{"k": k} for k in range(count)])


def test_a_round_adds_its_budget_and_refuses_a_space_naming_it() -> None:
    halving = SuccessiveHalving(budgets=(10, 30, 90), keep=0.5, parameter="budget")
    assert halving.round(1, _space(2).candidates).candidates == (
        {"k": 0, "budget": 30},
        {"k": 1, "budget": 30},
    )
    with pytest.raises(ResearchValidationError, match="already name 'budget'"):
        halving.round(0, [{"k": 0, "budget": 5}])
    with pytest.raises(ResearchValidationError, match="rounds 0 to 2"):
        halving.round(3, _space(2).candidates)


def test_halving_keeps_the_best_fraction_in_their_order_ties_to_the_earlier() -> None:
    halving = SuccessiveHalving(budgets=(1, 2), keep=0.5, parameter="budget")
    space = halving.round(0, _space(5).candidates)
    scores: list[float | None] = [0.25, 0.75, None, 0.75, 0.5]
    kept = halving.kept(space, scores)
    assert kept == ({"k": 1}, {"k": 3}, {"k": 4}), "ceil(5 / 2) = 3: best three, in order"
    assert halving.kept(space, [0.5, 0.5, 0.5, 0.5, 0.5]) == ({"k": 0}, {"k": 1}, {"k": 2})
    assert halving.kept(space, [None, None, 1.0, None, None]) == ({"k": 2},)
    with pytest.raises(ResearchValidationError, match="no round can follow"):
        halving.kept(space, [None] * 5)
    with pytest.raises(ResearchValidationError, match="5 candidates was given 4 scores"):
        halving.kept(space, [1.0] * 4)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"budgets": (10,)}, "at least two"),
        ({"budgets": (10, 10)}, "increase round by round"),
        ({"budgets": (0, 10)}, "at least two positive"),
        ({"keep": 1.0}, r"in \(0, 1\)"),
        ({"keep": 0.0}, r"in \(0, 1\)"),
        ({"parameter": " "}, "is a name"),
    ],
)
def test_what_a_halving_cannot_take_is_refused(changes: dict[str, object], match: str) -> None:
    fields: dict[str, object] = {"budgets": (10, 30), "keep": 0.5, "parameter": "budget"}
    fields.update(changes)
    with pytest.raises(ResearchValidationError, match=match):
        SuccessiveHalving(**fields)  # type: ignore[arg-type]
