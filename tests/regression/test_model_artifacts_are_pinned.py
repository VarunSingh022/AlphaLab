"""The reference models' artifacts are pinned, as every host computes them (ADR-0064, ADR-0058).

A trained model is its bytes, so a model fitted by class-2 arithmetic is pinned
by the digest of its capture. The ridge regression and the squared-loss tree
ensemble use only correctly rounded operations -- ``sqrt``, products, ``fsum``,
Householder QR, comparisons -- in a fixed order, and the ensemble subsamples by
SHA-256, so the same inputs give the same bytes on every IEEE-754 host. The
logistic models pass through ``exp`` and ``log``, the host's: they are held
across hosts by a stated tolerance, and their bytes are their own host's.

The inputs are built by integer arithmetic and exact scaling, never by a seeded
``gauss``, whose logarithm and cosine are the host's. Each value below was
computed identically under CPython 3.12, 3.13 and 3.14 before it was pinned. A
change here is a change to a model -- never a host's rounding -- and needs its
own record.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest

from alphalab.ml.models import (
    BoostingLoss,
    GradientBoostedTrees,
    LogisticRegression,
    RidgeRegression,
)
from alphalab.ml.protocol import FeatureMatrix, TrainingSet

NAMES = ("carry", "momentum", "value")

#: Class 3's tolerance across hosts: Newton steps converged to 1e-12 move only
#: in the last bits when another host's ``exp`` differs in its last bit.
CROSS_HOST = 1e-9


def _draws(seed: int) -> Callable[[int], int]:
    state = seed

    def draw(modulus: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) % 2**31
        return (state >> 8) % modulus

    return draw


def _data() -> tuple[TrainingSet, TrainingSet]:
    draw = _draws(4417)
    rows = [tuple((draw(64) - 32) / 16 for _ in NAMES) for _ in range(120)]
    labels = [(draw(512) - 256) / 256 + r[0] / 2 - r[1] / 4 for r in rows]
    classes = [1.0 if (draw(64) - 32) / 16 + r[0] - r[2] / 2 > 0 else 0.0 for r in rows]
    return TrainingSet.of(NAMES, rows, labels), TrainingSet.of(NAMES, rows, classes)


def _probe(training: TrainingSet) -> FeatureMatrix:
    return FeatureMatrix(NAMES, training.features.rows[:3])


def test_a_ridge_and_a_squared_loss_ensemble_are_pinned_byte_for_byte() -> None:
    regression, classification = _data()
    assert regression.training_id == (
        "6156c2297a0fbd1b9779144d40b6ee23bb4c552ac6e3175beeac188cecb01bb2"
    )
    assert classification.training_id == (
        "7746c47ddf51d7fa7ded66a781bd325aea3da2f571ad43355ffc049285bf81ef"
    )
    ridge = RidgeRegression(penalty=0.5, intercept=True, maximum_condition=1e8).fit(
        regression, seed=0
    )
    assert ridge.fitted is not None
    assert [ridge.fitted.intercept.hex(), *(c.hex() for c in ridge.fitted.coefficients)] == [
        "0x1.b16d5609dce45p-6",
        "0x1.0dc25a873de84p-1",
        "-0x1.a1c9902019464p-3",
        "-0x1.2eb231db4acf2p-4",
    ]
    assert hashlib.sha256(ridge.capture()).hexdigest() == (
        "a0b500e1c61e75aa611e8fdedf93988827455720cadf49932bf170f5544018bb"
    )
    trees = GradientBoostedTrees(
        loss=BoostingLoss.SQUARED,
        trees=5,
        learning_rate=0.25,
        depth=2,
        minimum_leaf=5,
        subsample=0.75,
    ).fit(regression, seed=7)
    assert [p.hex() for p in trees.predict(_probe(regression))] == [
        "-0x1.2d79689192632p-2",
        "-0x1.712da85d06d02p-1",
        "-0x1.0991a283f3438p-1",
    ]
    assert hashlib.sha256(trees.capture()).hexdigest() == (
        "0efccd13b634c572f6c18ea1e920efe138015702f9ec4143a7e9dab19caa8c50"
    )


def test_the_logistic_models_agree_across_hosts_within_their_stated_tolerance() -> None:
    _, classification = _data()
    assert sum(classification.labels) == 48.0
    logistic = LogisticRegression(
        penalty=1.0, intercept=True, tolerance=1e-12, max_iterations=50, maximum_condition=1e8
    ).fit(classification, seed=0)
    assert logistic.fitted is not None
    assert [logistic.fitted.intercept, *logistic.fitted.coefficients] == pytest.approx(
        [-0.24200567134622095, 0.9404121486724615, 0.18731949774950024, -0.48423676681904515],
        rel=CROSS_HOST,
    )
    boosted = GradientBoostedTrees(
        loss=BoostingLoss.LOGISTIC,
        trees=5,
        learning_rate=0.25,
        depth=2,
        minimum_leaf=5,
        subsample=1.0,
    ).fit(classification, seed=0)
    assert boosted.predict(_probe(classification)) == pytest.approx(
        (0.3690005835255608, 0.12852823942199498, 0.21653419703974336), rel=CROSS_HOST
    )
