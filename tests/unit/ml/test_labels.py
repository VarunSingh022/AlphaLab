"""Labels that know when they became known (v4.3, plan I.3).

* **A fixed horizon** is the return ``horizon`` observations ahead, or its sign
  beyond a dead zone, and its end is that observation; the last ``horizon``
  events are unresolved, never invented.
* **A triple barrier** resolves at the first barrier touched -- a touch is a
  return reaching the barrier or passing it -- or at the vertical barrier;
  events before the volatility window fills, flat ones and unresolved ones are
  counted.
* **The volatility estimate** is the sample standard deviation of the window's
  simple returns ending at the event, equal to its exact value.
* **Meta-labels** orient the barriers by the primary side.
* **Average uniqueness** is a hand count of overlaps, per subject.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from fractions import Fraction

import pytest

from alphalab.factor_library import FeatureField
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.ml.exceptions import MLInputError
from alphalab.ml.labels import (
    FixedHorizon,
    Label,
    RollingVolatility,
    TripleBarrier,
    VerticalOutcome,
    _first_touch,
    average_uniqueness,
    fixed_horizon_labels,
    meta_labels,
    triple_barrier_labels,
)

DAY = 86400.0


def _frame(paths: dict[str, Sequence[float]], version: str = "prices@1") -> ObservationFrame:
    return ObservationFrame(
        FeatureField.CLOSE,
        {
            subject: ObservationSeries(
                subject, tuple(float(i) * DAY for i in range(len(values))), tuple(values)
            )
            for subject, values in paths.items()
        },
        "UTC",
        version,
    )


def test_a_fixed_horizon_is_the_return_ahead_and_its_end_that_observation() -> None:
    prices = _frame({"A": [100.0, 110.0, 99.0, 132.0], "B": [50.0, 50.0, 40.0, 60.0]})
    labels = fixed_horizon_labels(prices, FixedHorizon(horizon=2, threshold=None))
    assert labels.labels == (
        Label("A", 0.0, 2 * DAY, 99.0 / 100.0 - 1.0, 99.0 / 100.0 - 1.0),
        Label("B", 0.0, 2 * DAY, 40.0 / 50.0 - 1.0, 40.0 / 50.0 - 1.0),
        Label("A", DAY, 3 * DAY, 132.0 / 110.0 - 1.0, 132.0 / 110.0 - 1.0),
        Label("B", DAY, 3 * DAY, 60.0 / 50.0 - 1.0, 60.0 / 50.0 - 1.0),
    )
    assert dict(labels.unlabelled) == {"unresolved": 4}
    assert labels.clocks["A"] == (0.0, DAY, 2 * DAY, 3 * DAY)
    assert (labels.source, labels.dataset_version, labels.timezone_name) == (
        "CLOSE",
        "prices@1",
        "UTC",
    )
    signed = fixed_horizon_labels(prices, FixedHorizon(horizon=2, threshold=0.1))
    assert [label.value for label in signed.labels] == [0.0, -1.0, 1.0, 1.0]


def test_the_volatility_estimate_is_the_exact_sample_deviation_of_its_returns() -> None:
    prices = [100.0, 102.0, 99.0, 103.5, 101.25, 104.0]
    estimate = RollingVolatility(window=4)
    assert estimate.at(prices, 3) is None, "three returns before observation 3"
    returns = [Fraction(prices[j]) / Fraction(prices[j - 1]) - 1 for j in range(2, 6)]
    mean = sum(returns, Fraction(0)) / 4
    exact = math.sqrt(float(sum(((r - mean) ** 2 for r in returns), Fraction(0)) / 3))
    later = [*prices, 140.0, 60.0]
    assert estimate.at(later, 5) == pytest.approx(exact, rel=1e-14), "no close after the event"
    assert estimate.at(prices, 5) == estimate.at(later, 5)


def _barrier(**changes: object) -> TripleBarrier:
    fields: dict[str, object] = {
        "horizon": 3,
        "upper": 2.0,
        "lower": 1.0,
        "volatility": RollingVolatility(window=2),
        "at_vertical": VerticalOutcome.SIGN,
    }
    fields.update(changes)
    return TripleBarrier(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("path", "side", "expected"),
    [
        ([100.0, 120.0, 150.0, 80.0], 1.0, (2, 1.0)),  # the upper barrier, reached exactly
        ([100.0, 120.0, 75.0, 200.0], 1.0, (2, -1.0)),  # the lower barrier, reached exactly
        ([100.0, 110.0, 90.0, 105.0], 1.0, (3, 1.0)),  # the vertical barrier, a gain
        ([100.0, 110.0, 90.0, 100.0], 1.0, (3, 0.0)),  # the vertical barrier, no return
        ([100.0, 125.0, 75.0, 200.0], -1.0, (1, -1.0)),  # short: a rise of a quarter stops it
        ([100.0, 50.0, 200.0, 200.0], -1.0, (1, 1.0)),  # short: a halving is its profit
        ([100.0, 110.0], 1.0, None),  # the data ends before any barrier
    ],
)
def test_a_path_resolves_at_its_first_barrier(
    path: list[float], side: float, expected: tuple[int, float] | None
) -> None:
    """Barriers at returns of +2 x 0.25 = 0.5 and -1 x 0.25 = -0.25, three observations wide."""

    assert _first_touch(path, 0, side, _barrier(), 0.25) == expected


def test_a_vertical_outcome_of_zero_ignores_the_returns_sign() -> None:
    barrier = _barrier(at_vertical=VerticalOutcome.ZERO)
    assert _first_touch([100.0, 110.0, 90.0, 105.0], 0, 1.0, barrier, 0.25) == (3, 0.0)


def test_one_barrier_alone_is_a_triple_barrier_and_neither_is_refused() -> None:
    assert _first_touch([100.0, 50.0, 60.0, 70.0], 0, 1.0, _barrier(lower=None), 0.25) == (
        3,
        -1.0,
    )
    with pytest.raises(MLInputError, match="is a fixed horizon"):
        _barrier(upper=None, lower=None)


def test_a_triple_barrier_counts_what_it_cannot_label() -> None:
    labels = triple_barrier_labels(
        _frame({"A": [100.0, 101.0, 103.0, 99.0, 104.0, 100.0], "F": [5.0] * 6}),
        _barrier(),
    )
    # Each subject's first two events precede its two-return window; F never moves.
    assert dict(labels.unlabelled) == {"warm-up": 4, "flat": 4, "unresolved": 3}
    (label,) = labels.labels
    assert (label.subject, label.start, label.end, label.value) == ("A", 2 * DAY, 3 * DAY, -1.0)
    assert label.outcome == 99.0 / 103.0 - 1.0


def test_meta_labels_orient_the_barriers_by_the_side() -> None:
    prices = _frame(
        {
            "A": [100.0, 100.0, 101.0, 140.0, 141.0, 142.0],
            "B": [100.0, 101.0, 100.0, 60.0, 59.0, 58.0],
        }
    )

    def meta(subject: str, side: float) -> float:
        (label,) = meta_labels(prices, _barrier(), {(subject, 2 * DAY): side}).labels
        assert (label.start, label.end) == (2 * DAY, 3 * DAY)
        return label.value

    assert meta("A", 1.0) == 1.0, "long into a rise reached its profit barrier"
    assert meta("A", -1.0) == 0.0, "short into a rise reached its stop"
    assert meta("B", -1.0) == 1.0, "short into a fall reached its profit barrier"
    assert meta("B", 1.0) == 0.0, "long into a fall reached its stop"
    unresolved = meta_labels(prices, _barrier(), {("A", 3 * DAY): -1.0})
    assert unresolved.labels == () and unresolved.unlabelled["unresolved"] == 1
    assert unresolved.method.startswith("meta-labels of a primary side")


@pytest.mark.parametrize(
    ("sides", "match"),
    [
        ({("A", 2 * DAY): 0.0}, "a side is 1 or -1"),
        ({("A", 2 * DAY): True}, "a side is 1 or -1"),
        ({("Z", 2 * DAY): 1.0}, "prices do not hold"),
        ({("A", 2.5 * DAY): 1.0}, "has no price at"),
    ],
)
def test_a_meta_label_needs_a_side_at_a_priced_instant(
    sides: dict[tuple[str, float], float], match: str
) -> None:
    with pytest.raises(MLInputError, match=match):
        meta_labels(_frame({"A": [100.0, 101.0, 102.0, 103.0]}), _barrier(), sides)


def test_average_uniqueness_is_a_hand_count_of_overlaps_per_subject() -> None:
    clocks = {"A": (0.0, 1.0, 2.0, 3.0, 4.0, 5.0), "B": (0.0, 1.0, 2.0)}
    rows = [("A", 0.0, 2.0), ("A", 1.0, 3.0), ("A", 4.0, 5.0), ("B", 0.0, 2.0)]
    # A's first two rows share observation 2: each spans one alone and one shared.
    assert average_uniqueness(rows, clocks) == (0.75, 0.75, 1.0, 1.0)
    with pytest.raises(MLInputError, match="spans no observation"):
        average_uniqueness([("A", 4.5, 4.75)], clocks)
    with pytest.raises(MLInputError, match="has no clock"):
        average_uniqueness([("C", 0.0, 1.0)], clocks)


def test_a_label_sets_identity_is_its_method_source_labels_and_clocks() -> None:
    prices = _frame({"A": [100.0, 110.0, 99.0, 121.0]})
    base = fixed_horizon_labels(prices, FixedHorizon(horizon=1, threshold=None))
    assert base.label_id == fixed_horizon_labels(prices, FixedHorizon(1, None)).label_id
    others = [
        fixed_horizon_labels(prices, FixedHorizon(horizon=2, threshold=None)),
        fixed_horizon_labels(prices, FixedHorizon(horizon=1, threshold=0.0)),
        fixed_horizon_labels(_frame({"A": [100.0, 110.0, 99.0, 122.0]}), FixedHorizon(1, None)),
        fixed_horizon_labels(
            _frame({"A": [100.0, 110.0, 99.0, 121.0]}, "v2"), FixedHorizon(1, None)
        ),
    ]
    assert len({base.label_id, *(other.label_id for other in others)}) == 5


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: FixedHorizon(horizon=0, threshold=None), "at least 1"),
        (lambda: FixedHorizon(horizon=1, threshold=-0.1), "at least zero"),
        (lambda: RollingVolatility(window=1), "at least 2"),
        (lambda: _barrier(upper=0.0), "finite positive"),
        (lambda: _barrier(volatility=5), "RollingVolatility"),
        (lambda: _barrier(at_vertical="sign"), "VerticalOutcome"),
        (
            lambda: fixed_horizon_labels(_frame({"A": [1.0, 0.0]}), FixedHorizon(1, None)),
            "positive",
        ),
        (lambda: fixed_horizon_labels(_frame({}), FixedHorizon(1, None)), "no series"),
        (lambda: fixed_horizon_labels(_frame({"A": [1.0, 2.0]}), _barrier()), "FixedHorizon"),  # type: ignore[arg-type]
        (
            lambda: triple_barrier_labels(_frame({"A": [1.0, 2.0]}), FixedHorizon(1, None)),  # type: ignore[arg-type]
            "TripleBarrier",
        ),
    ],
)
def test_what_a_maker_cannot_take_is_refused(build: Callable[[], object], match: str) -> None:
    with pytest.raises(MLInputError, match=match):
        build()
