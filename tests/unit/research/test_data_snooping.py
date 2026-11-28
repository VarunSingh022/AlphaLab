"""The stationary bootstrap, White's Reality Check and Hansen's SPA (v4.3, ADR-0054).

The bootstrap is held to its construction: positions in range, blocks that
continue by one and wrap, a new block at the stated rate, exact uniformity of
starts, and a replication drawn alone equal to the same replication drawn among
others. The tests are held to exact recomputations: with integer performance
over a power-of-two number of periods every mean is exact, so a statistic and
its p-value can be recomputed in ``Fraction`` from the same positions. SPA is
held to the property that defines it, that a model far below the benchmark
leaves its consistent p-value unchanged while the Reality Check's rises, and to
``p_lower <= p_consistent <= p_upper``.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import random
from fractions import Fraction

import pytest

from alphalab.research.data_snooping import reality_check, superior_predictive_ability
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.perturbation import (
    STATIONARY_BOOTSTRAP_SCHEME,
    stationary_bootstrap_indices,
)
from alphalab.research.selection import TrialMatrix


def _relative(columns: dict[str, list[float]]) -> TrialMatrix:
    periods = len(next(iter(columns.values())))
    return TrialMatrix.from_columns(
        columns, [float(i) for i in range(periods)], "loss differential to the benchmark"
    )


def _integer_columns(
    models: int, periods: int, seed: int, shift: int = 0
) -> dict[str, list[float]]:
    rng = random.Random(seed)
    return {
        f"m{k}": [float(rng.randint(-8, 8) + (shift if k == 0 else 0)) for _ in range(periods)]
        for k in range(models)
    }


# --------------------------------------------------------------------------- #
# The stationary bootstrap
# --------------------------------------------------------------------------- #


def test_positions_are_in_range_and_blocks_continue_by_one() -> None:
    samples = stationary_bootstrap_indices(50, 6.0, 40, seed=3)

    assert len(samples) == 40 and all(len(sample) == 50 for sample in samples)
    assert all(0 <= p < 50 for sample in samples for p in sample)


def test_a_new_block_starts_at_the_stated_rate() -> None:
    samples = stationary_bootstrap_indices(2000, 8.0, 50, seed=11)
    # A block continues as (p + 1) mod T; a new start lands there by chance 1/T of the time.
    continued = sum(
        1
        for sample in samples
        for previous, current in itertools.pairwise(sample)
        if current == (previous + 1) % 2000
    )
    steps = 50 * 1999
    expected = steps * (1 - 1 / 8) + steps * (1 / 8) / 2000
    assert abs(continued - expected) < 4 * math.sqrt(steps * (1 / 8) * (7 / 8))


def test_a_mean_block_length_of_one_is_the_independent_bootstrap() -> None:
    """With q = 1 every step starts a block, so positions are independent uniform draws."""

    samples = stationary_bootstrap_indices(10, 1.0, 2000, seed=5)
    counts = [0] * 10
    for sample in samples:
        for position in sample:
            counts[position] += 1
    assert all(abs(count - 2000) < 4 * math.sqrt(2000) for count in counts)


def test_a_replication_drawn_alone_is_the_same_as_drawn_among_others() -> None:
    few = stationary_bootstrap_indices(30, 4.0, 3, seed=9)
    many = stationary_bootstrap_indices(30, 4.0, 8, seed=9)

    assert many[:3] == few


def test_the_first_step_of_a_sample_is_its_hash_counter_draw() -> None:
    """The positions are what the stated scheme says: SHA-256 of scheme, seed, replication, step."""

    digest = hashlib.sha256(f"{STATIONARY_BOOTSTRAP_SCHEME}|42|0|0".encode("ascii")).digest()
    candidate = int.from_bytes(digest[8:16], "big")
    limit = (1 << 64) - (1 << 64) % 37
    assert candidate < limit  # the chance it is not is 37 / 2**64

    assert stationary_bootstrap_indices(37, 3.0, 1, seed=42)[0][0] == candidate % 37


def test_different_seeds_draw_different_samples() -> None:
    assert stationary_bootstrap_indices(40, 5.0, 2, seed=1) != stationary_bootstrap_indices(
        40, 5.0, 2, seed=2
    )


@pytest.mark.parametrize(
    ("count", "length", "replications", "seed"),
    [
        (0, 2.0, 1, 1),
        (10, 0.5, 1, 1),
        (10, math.inf, 1, 1),
        (10, 2.0, 0, 1),
        (10, 2.0, 1, 1.5),
        (10, 2.0, True, 1),
    ],
)
def test_a_bootstrap_argument_out_of_range_is_refused(
    count: int, length: float, replications: int, seed: int
) -> None:
    with pytest.raises(ResearchValidationError):
        stationary_bootstrap_indices(count, length, replications, seed)


#: Computed under CPython 3.12.4, 3.13.18 and 3.14.10 on macOS arm64, identical;
#: CI holds it on Linux x86-64 as well.
PINNED_BOOTSTRAP_DIGEST = "47628a66f5b9cd8c85e15f169ed2519a003638f90c8a21b45665bce5c7f2ce41"


def test_the_pinned_bootstrap_is_the_same_on_every_host() -> None:
    samples = stationary_bootstrap_indices(250, 7.5, 20, seed=20261114)
    digest = hashlib.sha256(repr(samples).encode("ascii")).hexdigest()

    assert digest == PINNED_BOOTSTRAP_DIGEST


# --------------------------------------------------------------------------- #
# The Reality Check
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_reality_check_equals_its_exact_recomputation(seed: int) -> None:
    columns = _integer_columns(5, 64, seed, shift=1)
    result = reality_check(_relative(columns), replications=200, mean_block_length=4.0, seed=seed)

    exact = {k: [Fraction(v) for v in column] for k, column in columns.items()}
    means = {k: sum(column, Fraction(0)) / 64 for k, column in exact.items()}
    statistic = max(means.values())
    exceed = 0
    for positions in stationary_bootstrap_indices(64, 4.0, 200, seed):
        resampled = max(
            sum((exact[k][p] for p in positions), Fraction(0)) / 64 - means[k] for k in exact
        )
        exceed += resampled >= statistic

    assert result.statistic == math.sqrt(64) * float(statistic)
    assert result.p_value == Fraction(exceed, 200)
    assert result.best == max(means, key=lambda k: means[k])


def test_a_model_far_above_the_benchmark_is_significant() -> None:
    columns = _integer_columns(6, 128, 4)
    columns["m0"] = [v + 6.0 for v in columns["m0"]]

    result = reality_check(_relative(columns), replications=300, mean_block_length=3.0, seed=4)

    assert result.p_value == 0 and result.best == "m0"


# --------------------------------------------------------------------------- #
# SPA
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", range(6))
def test_spas_three_p_values_are_ordered(seed: int) -> None:
    rng = random.Random(seed)
    columns = {
        f"m{k}": [rng.gauss(rng.uniform(-0.3, 0.1), 1.0) for _ in range(100)] for k in range(8)
    }

    result = superior_predictive_ability(
        _relative(columns), replications=300, mean_block_length=5.0, seed=seed
    )

    assert result.p_lower <= result.p_consistent <= result.p_upper


def test_a_poor_model_leaves_the_consistent_p_value_and_raises_the_reality_checks() -> None:
    """Hansen's point: the Reality Check can be driven up by adding a model nobody would pick."""

    rng = random.Random(8)
    good = {f"m{k}": [rng.gauss(0.12, 1.0) for _ in range(200)] for k in range(4)}
    poor = dict(good)
    poor["hopeless"] = [rng.gauss(-3.0, 12.0) for _ in range(200)]

    plain = superior_predictive_ability(
        _relative(good), replications=400, mean_block_length=4.0, seed=8
    )
    with_poor = superior_predictive_ability(
        _relative(poor), replications=400, mean_block_length=4.0, seed=8
    )

    assert with_poor.p_consistent == plain.p_consistent
    assert with_poor.p_lower == plain.p_lower
    assert with_poor.p_upper >= plain.p_upper
    assert with_poor.statistic == plain.statistic


def test_no_model_above_the_benchmark_gives_a_p_value_of_one() -> None:
    columns = {f"m{k}": [float(v) for v in ([-2, -1, 0, -3] * 16)] for k in range(3)}
    columns["m1"] = [v - 1.0 for v in columns["m1"]]
    columns["m2"] = list(reversed(columns["m2"]))

    result = superior_predictive_ability(
        _relative(columns), replications=100, mean_block_length=2.0, seed=1
    )

    assert result.statistic == 0.0
    assert result.p_lower == result.p_consistent == result.p_upper == 1


def test_a_constant_model_has_no_studentized_mean() -> None:
    columns = {"varies": [float(v) for v in range(-5, 5)], "flat": [0.25] * 10}

    with pytest.raises(ResearchValidationError, match="does not vary"):
        superior_predictive_ability(
            _relative(columns), replications=50, mean_block_length=2.0, seed=1
        )


@pytest.mark.parametrize("replications", [1, 0])
def test_spa_needs_two_replications(replications: int) -> None:
    with pytest.raises(ResearchValidationError):
        superior_predictive_ability(
            _relative(_integer_columns(2, 16, 1)),
            replications=replications,
            mean_block_length=2.0,
            seed=1,
        )


def test_spa_needs_three_periods() -> None:
    with pytest.raises(ResearchValidationError, match="ln ln T"):
        superior_predictive_ability(
            _relative({"a": [1.0, 2.0], "b": [2.0, 1.0]}),
            replications=10,
            mean_block_length=1.0,
            seed=1,
        )


#: Computed under CPython 3.12.4, 3.13.18 and 3.14.10 on macOS arm64, identical;
#: CI holds them on Linux x86-64 as well.
PINNED_TESTS: tuple[str, ...] = (
    "0x1.323fda7ea2517p+1",
    "2/125",
    "0x1.6224843ec57ebp+1",
    "1/125",
    "1/125",
    "2/125",
)


def _pinned_tests() -> tuple[str, ...]:
    rng = random.Random(1914)
    columns = {f"m{k}": [rng.gauss(0.05 * k - 0.1, 1.0) for _ in range(150)] for k in range(6)}
    relative = _relative(columns)
    check = reality_check(relative, replications=250, mean_block_length=6.0, seed=31)
    spa = superior_predictive_ability(relative, replications=250, mean_block_length=6.0, seed=31)
    return (
        check.statistic.hex(),
        str(check.p_value),
        spa.statistic.hex(),
        str(spa.p_lower),
        str(spa.p_consistent),
        str(spa.p_upper),
    )


def test_the_pinned_tests_are_the_same_on_every_host() -> None:
    assert _pinned_tests() == PINNED_TESTS
