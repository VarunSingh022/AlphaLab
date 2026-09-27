"""Execution algorithms: schedules, sizing, timing, completion, cancellation and failure."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from uuid import UUID

import pytest

from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.algorithms import (
    MAX_URGENCY,
    TWAP,
    VWAP,
    AlgorithmState,
    AlgorithmStatus,
    AlgorithmTerms,
    ExecutionAlgorithm,
    Iceberg,
    IncompletePolicy,
    IntervalVolume,
    MissingVolumePolicy,
    Participation,
    ScheduleBasis,
    Slicing,
    Urgency,
    VolumeProfile,
    algorithm_configuration_id,
    cancel_algorithm,
    plan_schedule,
    record_child_execution,
    record_child_outcome,
    release_children,
    start_algorithm,
)
from alphalab.execution.exceptions import ExecutionValidationError

D = Decimal
PARENT_ID = str(UUID("0f1e2d3c-4b5a-4968-8776-655443322110"))
CONTRIBUTIONS = (
    StrategyContribution("MOMENTUM", D("60")),
    StrategyContribution("REVERSAL", D("40")),
)


def _parent(quantity: str = "100", side: Side = Side.BUY) -> OrderRequest:
    return OrderRequest(
        order_id=PARENT_ID,
        strategy_id="",
        asset_id="ASSET-1",
        side=side,
        quantity=D(quantity),
        price=D("50"),
        timestamp=0.0,
        contributions=CONTRIBUTIONS,
    )


def _terms(
    start: float = 0.0,
    end: float = 300.0,
    increment: str = "1",
    order_type: OrderType = OrderType.MARKET,
    limit: str | None = None,
) -> AlgorithmTerms:
    return AlgorithmTerms(start, end, D(increment), order_type, None if limit is None else D(limit))


def _profile(*volumes: str | None, width: float = 100.0) -> VolumeProfile:
    return VolumeProfile(
        tuple(
            IntervalVolume(index * width, (index + 1) * width, None if v is None else D(v))
            for index, v in enumerate(volumes)
        ),
        "twenty-day average",
    )


def _quantities(state: AlgorithmState) -> list[Decimal]:
    assert state.schedule is not None
    return [s.quantity for s in state.schedule.slices]


# --------------------------------------------------------------------------- #
# Urgency: the trajectory
# --------------------------------------------------------------------------- #


def test_neutral_urgency_is_a_straight_line() -> None:
    for x in ("0", "0.1", "0.5", "0.9", "1"):
        assert Urgency.neutral().fraction(D(x)) == D(x)


def test_urgency_is_monotone_pinned_at_both_ends_and_front_loads_as_it_grows() -> None:
    clock = [D(k) / 20 for k in range(21)]
    previous_curve: list[Decimal] | None = None
    for kappa in ("0", "0.5", "2", "8", "30"):
        curve = [Urgency(D(kappa)).fraction(x) for x in clock]
        assert curve[0] == 0 and curve[-1] == 1
        assert all(a <= b for a, b in pairwise(curve))
        if previous_curve is not None:
            assert all(a >= b for a, b in zip(curve, previous_curve, strict=True))
        previous_curve = curve


@pytest.mark.parametrize("kappa", ["-0.1", str(MAX_URGENCY + 1)])
def test_urgency_outside_its_range_is_refused(kappa: str) -> None:
    with pytest.raises(ExecutionValidationError, match="outside"):
        Urgency(D(kappa))


# --------------------------------------------------------------------------- #
# TWAP
# --------------------------------------------------------------------------- #


def test_twap_partitions_the_window_into_equal_intervals() -> None:
    schedule = plan_schedule(D("100"), TWAP(4, Urgency.neutral()), _terms(end=400.0))
    assert [(s.start, s.end) for s in schedule.slices] == [
        (0.0, 100.0),
        (100.0, 200.0),
        (200.0, 300.0),
        (300.0, 400.0),
    ]
    assert schedule.basis is ScheduleBasis.TIME
    assert [s.weight for s in schedule.slices] == [D("0.25")] * 4


@pytest.mark.parametrize(
    ("quantity", "slices", "expected"),
    [
        ("10", 3, ["4", "3", "3"]),
        ("11", 3, ["4", "4", "3"]),
        ("12", 3, ["4", "4", "4"]),
        ("1", 4, ["1", "0", "0", "0"]),
        ("2", 5, ["1", "1", "0", "0", "0"]),
    ],
)
def test_the_remainder_goes_to_the_earliest_slices(
    quantity: str, slices: int, expected: list[str]
) -> None:
    schedule = plan_schedule(D(quantity), TWAP(slices, Urgency.neutral()), _terms())
    assert [s.quantity for s in schedule.slices] == [D(q) for q in expected]
    assert schedule.slices[-1].target == D(quantity)


def test_the_increment_is_respected_and_a_parent_off_it_is_refused() -> None:
    schedule = plan_schedule(D("1.5"), TWAP(4, Urgency.neutral()), _terms(increment="0.25"))
    assert [s.quantity for s in schedule.slices] == [D("0.5"), D("0.5"), D("0.25"), D("0.25")]
    with pytest.raises(ExecutionValidationError, match="not a whole number"):
        plan_schedule(D("1.3"), TWAP(4, Urgency.neutral()), _terms(increment="0.25"))


def test_urgency_front_loads_a_twap_and_every_schedule_sums_exactly() -> None:
    neutral = plan_schedule(D("100"), TWAP(5, Urgency.neutral()), _terms())
    urgent = plan_schedule(D("100"), TWAP(5, Urgency(D("3"))), _terms())
    assert [s.quantity for s in neutral.slices] == [D("20")] * 5
    assert urgent.slices[0].quantity > neutral.slices[0].quantity
    assert urgent.slices[-1].quantity < neutral.slices[-1].quantity
    for schedule in (neutral, urgent):
        assert sum((s.quantity for s in schedule.slices), D(0)) == D("100")
        targets = [s.target for s in schedule.slices]
        assert targets == sorted(targets)


def test_a_zero_or_invalid_parent_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="nothing to work"):
        start_algorithm(_parent("0"), TWAP(3, Urgency.neutral()), _terms())
    with pytest.raises(ExecutionValidationError, match="no schedule"):
        TWAP(0, Urgency.neutral())


# --------------------------------------------------------------------------- #
# VWAP
# --------------------------------------------------------------------------- #


def test_vwap_spends_in_proportion_to_expected_volume() -> None:
    schedule = plan_schedule(
        D("100"),
        VWAP(_profile("5000", "1000", "4000"), MissingVolumePolicy.REFUSE, Urgency.neutral()),
        _terms(),
    )
    assert schedule.basis is ScheduleBasis.VOLUME
    assert [s.quantity for s in schedule.slices] == [D("50"), D("10"), D("40")]


def test_a_partly_covered_interval_expects_its_volume_pro_rata() -> None:
    schedule = plan_schedule(
        D("100"),
        VWAP(_profile("5000", "1000", "4000"), MissingVolumePolicy.REFUSE, Urgency.neutral()),
        _terms(start=50.0, end=250.0),
    )
    # 2500 : 1000 : 2000 of 5500, apportioned by largest remainder.
    assert [(s.start, s.end, s.quantity) for s in schedule.slices] == [
        (50.0, 100.0, D("46")),
        (100.0, 200.0, D("18")),
        (200.0, 250.0, D("36")),
    ]


def test_an_interval_expected_to_trade_nothing_gets_nothing() -> None:
    schedule = plan_schedule(
        D("100"),
        VWAP(_profile("6000", "0", "4000"), MissingVolumePolicy.REFUSE, Urgency.neutral()),
        _terms(),
    )
    assert [s.quantity for s in schedule.slices] == [D("60"), D("0"), D("40")]


@pytest.mark.parametrize(
    ("profile", "terms", "why"),
    [
        (_profile("5000", None, "4000"), _terms(), "an interval has no expected volume"),
        (_profile("5000", "4000"), _terms(), "does not cover the whole window"),
        (_profile("0", "0", "0"), _terms(), "expects no volume at all"),
    ],
)
def test_missing_volume_is_refused_or_replaced_by_time_as_the_caller_chose(
    profile: VolumeProfile, terms: AlgorithmTerms, why: str
) -> None:
    with pytest.raises(ExecutionValidationError, match=why):
        plan_schedule(D("100"), VWAP(profile, MissingVolumePolicy.REFUSE, Urgency.neutral()), terms)
    fallback = plan_schedule(
        D("100"), VWAP(profile, MissingVolumePolicy.TIME_WEIGHTED, Urgency.neutral()), terms
    )
    assert fallback.basis is ScheduleBasis.TIME_FALLBACK
    assert why in fallback.note
    assert sum((s.quantity for s in fallback.slices), D(0)) == D("100")
    state = start_algorithm(
        _parent(), VWAP(profile, MissingVolumePolicy.TIME_WEIGHTED, Urgency.neutral()), terms
    )
    assert state.notes and why in state.notes[0]


def test_a_profile_must_be_contiguous_and_attributed() -> None:
    with pytest.raises(ExecutionValidationError, match="contiguous"):
        VolumeProfile(
            (IntervalVolume(0.0, 10.0, D("1")), IntervalVolume(20.0, 30.0, D("1"))), "src"
        )
    with pytest.raises(ExecutionValidationError, match="no source"):
        _profile("1").__class__(_profile("1").intervals, " ")


# --------------------------------------------------------------------------- #
# Running a schedule
# --------------------------------------------------------------------------- #


def test_a_twap_run_releases_at_slice_starts_and_catches_up_what_did_not_fill() -> None:
    state = start_algorithm(_parent("10"), TWAP(3, Urgency.neutral()), _terms())
    state, released = release_children(state, 0.0)
    (first,) = released
    assert (first.quantity, first.expires_at, first.sequence) == (D("4"), 100.0, 1)

    state, nothing = release_children(state, 50.0)
    assert nothing == (), "the first slice's target is already committed"

    state = record_child_execution(state, first.child_id, "X-1", D("2"), 60.0)
    state = record_child_outcome(state, first.child_id, OrderStatus.EXPIRED, 100.0)
    state, (second,) = release_children(state, 100.0)
    assert second.quantity == D("5"), "7 due, 2 filled, nothing working"

    state = record_child_execution(state, second.child_id, "X-2", D("5"), 150.0)
    state, (third,) = release_children(state, 200.0)
    assert third.quantity == D("3")
    state = record_child_execution(state, third.child_id, "X-3", D("3"), 250.0)
    assert state.status is AlgorithmStatus.COMPLETED
    assert state.filled == D("10")


def test_a_late_caller_is_caught_up_by_one_child_not_one_per_missed_slice() -> None:
    state = start_algorithm(_parent("10"), TWAP(3, Urgency.neutral()), _terms())
    state, released = release_children(state, 250.0)
    assert [child.quantity for child in released] == [D("10")]
    assert released[0].expires_at == 300.0


def test_nothing_is_released_before_the_window_opens_or_after_it_closes() -> None:
    state = start_algorithm(_parent(), TWAP(3, Urgency.neutral()), _terms(start=100.0, end=400.0))
    state, early = release_children(state, 50.0)
    assert early == ()
    state, late = release_children(state, 400.0)
    assert late == ()
    assert state.status is AlgorithmStatus.EXPIRED
    assert "unfilled" in state.reason


def test_a_window_ending_with_quantity_unfilled_expires() -> None:
    state = start_algorithm(_parent("10"), TWAP(2, Urgency.neutral()), _terms(end=200.0))
    state, (first,) = release_children(state, 0.0)
    state, (second,) = release_children(state, 100.0)
    state = record_child_execution(state, first.child_id, "X-1", D("5"), 50.0)
    state = record_child_outcome(state, second.child_id, OrderStatus.EXPIRED, 200.0)
    assert state.status is AlgorithmStatus.EXPIRED
    assert state.remaining == D("5")


# --------------------------------------------------------------------------- #
# Participation
# --------------------------------------------------------------------------- #


def _pov(
    rate: str = "0.1",
    low: str | None = None,
    high: str | None = None,
    at_end: IncompletePolicy = IncompletePolicy.LEAVE_UNFILLED,
) -> Participation:
    return Participation(
        D(rate), None if low is None else D(low), None if high is None else D(high), at_end
    )


def test_participation_tops_up_to_its_share_of_observed_volume() -> None:
    state = start_algorithm(_parent(), _pov("0.1"), _terms())
    state, (first,) = release_children(state, 60.0, (IntervalVolume(0.0, 60.0, D("40")),))
    assert first.quantity == D("4")
    state = record_child_execution(state, first.child_id, "X-1", D("4"), 61.0)
    state, (second,) = release_children(state, 120.0, (IntervalVolume(60.0, 120.0, D("39")),))
    assert second.quantity == D("3"), "floor(0.1 * 79) = 7, of which 4 filled"
    assert state.observed_volume == D("79")


def test_participation_never_rounds_above_its_cap() -> None:
    state = start_algorithm(_parent(), _pov("0.1"), _terms())
    state, released = release_children(state, 60.0, (IntervalVolume(0.0, 60.0, D("19")),))
    assert [child.quantity for child in released] == [D("1")]


def test_insufficient_liquidity_releases_nothing_and_says_so() -> None:
    state = start_algorithm(_parent(), _pov("0.1", low="5"), _terms())
    state, released = release_children(state, 60.0, (IntervalVolume(0.0, 60.0, D("30")),))
    assert released == ()
    assert "insufficient" in state.notes[-1]


def test_a_child_above_the_maximum_is_capped() -> None:
    state = start_algorithm(_parent(), _pov("0.5", high="10"), _terms())
    state, (child,) = release_children(state, 60.0, (IntervalVolume(0.0, 60.0, D("100")),))
    assert child.quantity == D("10")


def test_missing_volume_is_recorded_and_sizes_nothing() -> None:
    state = start_algorithm(_parent(), _pov(), _terms())
    state, released = release_children(state, 60.0, (IntervalVolume(0.0, 60.0, None),))
    assert released == ()
    assert "No volume was observed" in state.notes[-1]
    assert state.observed_volume == 0


def test_volume_observed_twice_or_outside_the_window_is_refused() -> None:
    state = start_algorithm(_parent(), _pov(), _terms())
    state, _ = release_children(state, 60.0, (IntervalVolume(0.0, 60.0, D("10")),))
    with pytest.raises(ExecutionValidationError, match="overlaps"):
        release_children(state, 90.0, (IntervalVolume(30.0, 90.0, D("10")),))
    with pytest.raises(ExecutionValidationError, match="outside the window"):
        release_children(state, 400.0, (IntervalVolume(300.0, 400.0, D("10")),))


def test_at_the_end_participation_stops_or_completes_as_the_caller_chose() -> None:
    volumes = (IntervalVolume(0.0, 300.0, D("200")),)
    stop = start_algorithm(_parent(), _pov("0.1"), _terms())
    stop, (child,) = release_children(stop, 299.0, volumes)
    stop = record_child_execution(stop, child.child_id, "X-1", child.quantity, 299.5)
    stop, nothing = release_children(stop, 300.0)
    assert nothing == ()
    assert stop.status is AlgorithmStatus.EXPIRED
    assert stop.remaining == D("80")

    finish = start_algorithm(
        _parent(), _pov("0.1", at_end=IncompletePolicy.COMPLETE_AT_END), _terms()
    )
    finish, (child,) = release_children(finish, 299.0, volumes)
    finish = record_child_execution(finish, child.child_id, "X-1", child.quantity, 299.5)
    finish, (last,) = release_children(finish, 300.0)
    assert last.quantity == D("80")
    assert finish.status is AlgorithmStatus.WORKING


def test_participation_parameters_are_checked() -> None:
    for rate in ("0", "1.01", "-0.1"):
        with pytest.raises(ExecutionValidationError, match="not in"):
            _pov(rate)
    with pytest.raises(ExecutionValidationError, match="can release nothing"):
        _pov(low="10", high="5")
    with pytest.raises(ExecutionValidationError, match="not a whole number"):
        start_algorithm(_parent(), _pov(low="0.5"), _terms())


# --------------------------------------------------------------------------- #
# Slicing and iceberg
# --------------------------------------------------------------------------- #


def _drain(state: AlgorithmState, now: float = 1.0) -> list[Decimal]:
    """Release, fill and repeat until the run stops releasing."""

    sizes: list[Decimal] = []
    for step in range(100):
        state, released = release_children(state, now)
        if not released:
            break
        (child,) = released
        sizes.append(child.quantity)
        state = record_child_execution(state, child.child_id, f"X-{step}", child.quantity, now)
    return sizes


def test_slicing_by_size_sends_the_remainder_last() -> None:
    assert _drain(start_algorithm(_parent("10"), Slicing(D("4"), None), _terms())) == [
        D("4"),
        D("4"),
        D("2"),
    ]


def test_slicing_by_count_sends_the_remainder_first() -> None:
    assert _drain(start_algorithm(_parent("10"), Slicing(None, 4), _terms())) == [
        D("3"),
        D("3"),
        D("2"),
        D("2"),
    ]


def test_one_slice_works_at_a_time_and_unfilled_quantity_is_sliced_again() -> None:
    state = start_algorithm(_parent("10"), Slicing(D("4"), None), _terms())
    state, (first,) = release_children(state, 1.0)
    state, blocked = release_children(state, 2.0)
    assert blocked == ()
    state = record_child_execution(state, first.child_id, "X-1", D("1"), 3.0)
    state = record_child_outcome(state, first.child_id, OrderStatus.CANCELLED, 4.0)
    state, (second,) = release_children(state, 5.0)
    assert second.quantity == D("4")
    assert state.unreleased == D("5")


def test_slicing_takes_exactly_one_rule() -> None:
    with pytest.raises(ExecutionValidationError, match="exactly one"):
        Slicing(D("1"), 2)
    with pytest.raises(ExecutionValidationError, match="exactly one"):
        Slicing(None, None)
    with pytest.raises(ExecutionValidationError, match="not a whole number"):
        start_algorithm(_parent(), Slicing(D("2.5"), None), _terms())


def test_an_iceberg_shows_one_tranche_and_hides_the_rest() -> None:
    state = start_algorithm(_parent("10"), Iceberg(D("3")), _terms())
    state, (tranche,) = release_children(state, 1.0)
    assert (state.displayed_quantity, state.hidden_quantity) == (D("3"), D("7"))
    state, none_yet = release_children(state, 2.0)
    assert none_yet == ()
    state = record_child_execution(state, tranche.child_id, "X-1", D("3"), 3.0)
    state, (refill,) = release_children(state, 4.0)
    assert refill.quantity == D("3")
    assert _drain(start_algorithm(_parent("10"), Iceberg(D("3")), _terms())) == [
        D("3"),
        D("3"),
        D("3"),
        D("1"),
    ]


# --------------------------------------------------------------------------- #
# Fills, outcomes, cancellation, failure
# --------------------------------------------------------------------------- #


def _working() -> tuple[AlgorithmState, str]:
    state = start_algorithm(_parent("10"), TWAP(2, Urgency.neutral()), _terms(end=200.0))
    state, (child,) = release_children(state, 0.0)
    return state, child.child_id


def test_booking_a_fill_twice_is_a_no_op() -> None:
    state, child_id = _working()
    once = record_child_execution(state, child_id, "X-1", D("2"), 10.0)
    twice = record_child_execution(once, child_id, "X-1", D("2"), 11.0)
    assert twice is once
    assert once.filled == D("2")


def test_impossible_fills_are_refused() -> None:
    state, child_id = _working()
    with pytest.raises(ExecutionValidationError, match="overfill"):
        record_child_execution(state, child_id, "X-1", D("6"), 10.0)
    with pytest.raises(ExecutionValidationError, match="not a fill"):
        record_child_execution(state, child_id, "X-1", D("0"), 10.0)
    with pytest.raises(ExecutionValidationError, match="No child"):
        record_child_execution(state, "nobody/1", "X-1", D("1"), 10.0)
    closed = record_child_outcome(state, child_id, OrderStatus.CANCELLED, 10.0)
    with pytest.raises(ExecutionValidationError, match="already cancelled"):
        record_child_execution(closed, child_id, "X-2", D("1"), 11.0)


def test_outcomes_are_checked_and_idempotent() -> None:
    state, child_id = _working()
    with pytest.raises(ExecutionValidationError, match="not how a child finishes"):
        record_child_outcome(state, child_id, OrderStatus.FILLED, 10.0)
    cancelled = record_child_outcome(state, child_id, OrderStatus.CANCELLED, 10.0)
    assert record_child_outcome(cancelled, child_id, OrderStatus.CANCELLED, 11.0) is cancelled
    with pytest.raises(ExecutionValidationError, match="cannot also be"):
        record_child_outcome(cancelled, child_id, OrderStatus.EXPIRED, 12.0)


def test_a_rejected_child_fails_the_run_and_nothing_more_is_released() -> None:
    state, child_id = _working()
    failed = record_child_outcome(state, child_id, OrderStatus.REJECTED, 10.0)
    assert failed.status is AlgorithmStatus.FAILED
    assert "rejected" in failed.reason
    after, released = release_children(failed, 100.0)
    assert released == ()
    assert after.status is AlgorithmStatus.FAILED


def test_cancelling_returns_the_working_children_and_their_fills_still_count() -> None:
    state, child_id = _working()
    cancelled, to_cancel = cancel_algorithm(state, 20.0, "desk decision")
    assert [child.child_id for child in to_cancel] == [child_id]
    assert cancelled.status is AlgorithmStatus.CANCELLED
    assert release_children(cancelled, 100.0)[1] == ()
    late_fill = record_child_execution(cancelled, child_id, "X-1", D("5"), 21.0)
    assert late_fill.filled == D("5")
    assert cancel_algorithm(late_fill, 30.0, "again") == (late_fill, ())


# --------------------------------------------------------------------------- #
# Children: provenance and pricing
# --------------------------------------------------------------------------- #


def test_every_child_carries_the_parents_strategies_and_the_run_identity() -> None:
    state = start_algorithm(_parent(), TWAP(3, Urgency.neutral()), _terms())
    state, (child,) = release_children(state, 0.0)
    assert child.contributions == CONTRIBUTIONS
    assert child.strategy_id == ""
    assert child.algorithm_id == state.algorithm_id
    assert child.child_id == f"{PARENT_ID}/1"
    assert (child.asset_id, child.side) == ("ASSET-1", Side.BUY)


def test_a_limit_parent_prices_every_child_and_a_market_one_none() -> None:
    limited = start_algorithm(
        _parent(), Iceberg(D("5")), _terms(order_type=OrderType.LIMIT, limit="49.5")
    )
    limited, (child,) = release_children(limited, 1.0)
    assert (child.order_type, child.limit_price) == (OrderType.LIMIT, D("49.5"))


@pytest.mark.parametrize(
    ("order_type", "limit", "message"),
    [
        (OrderType.LIMIT, None, "names no limit price"),
        (OrderType.MARKET, "10", "carry no limit"),
        (OrderType.STOP, None, "MARKET or LIMIT"),
        (OrderType.LIMIT, "0", "not a price"),
    ],
)
def test_terms_refuse_what_cannot_be_sent(
    order_type: OrderType, limit: str | None, message: str
) -> None:
    with pytest.raises(ExecutionValidationError, match=message):
        _terms(order_type=order_type, limit=limit)


def test_a_window_must_contain_time_and_an_increment_a_unit() -> None:
    with pytest.raises(ExecutionValidationError, match="no time"):
        _terms(start=10.0, end=10.0)
    with pytest.raises(ExecutionValidationError, match="not a unit"):
        _terms(increment="0")


# --------------------------------------------------------------------------- #
# Determinism and identity
# --------------------------------------------------------------------------- #


ALGORITHMS: list[ExecutionAlgorithm] = [
    TWAP(4, Urgency(D("1.5"))),
    VWAP(_profile("300", "100", "600"), MissingVolumePolicy.REFUSE, Urgency(D("0.5"))),
    _pov("0.2", low="1", high="30"),
    Slicing(None, 3),
    Iceberg(D("7")),
]


def _run(algorithm: ExecutionAlgorithm) -> tuple[AlgorithmState, list[tuple[str, Decimal]]]:
    state = start_algorithm(_parent(), algorithm, _terms())
    released: list[tuple[str, Decimal]] = []
    for step in range(12):
        now = step * 25.0
        volumes = (IntervalVolume(now, now + 25.0, D(str(40 + step * 7))),) if now < 300 else ()
        state, children = release_children(state, now, volumes)
        for child in children:
            released.append((child.child_id, child.quantity))
            fill = child.quantity - (child.quantity // 3 if step % 2 else 0)
            state = record_child_execution(state, child.child_id, f"{child.child_id}#x", fill, now)
            if fill < child.quantity:
                state = record_child_outcome(state, child.child_id, OrderStatus.EXPIRED, now)
    return state, released


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_identical_inputs_reproduce_identical_runs(algorithm: ExecutionAlgorithm) -> None:
    first_state, first = _run(algorithm)
    second_state, second = _run(algorithm)
    assert first == second
    assert first_state == second_state
    assert first_state.algorithm_id == second_state.algorithm_id


def test_the_configuration_identity_sees_every_parameter_and_nothing_else() -> None:
    base = algorithm_configuration_id(TWAP(4, Urgency.neutral()))
    assert base == algorithm_configuration_id(TWAP(4, Urgency.neutral()))
    assert base != algorithm_configuration_id(TWAP(5, Urgency.neutral()))
    assert base != algorithm_configuration_id(TWAP(4, Urgency(D("0.1"))))
    identities = {algorithm_configuration_id(a) for a in ALGORITHMS}
    assert len(identities) == len(ALGORITHMS)
    one = start_algorithm(_parent("100"), TWAP(4, Urgency.neutral()), _terms())
    other = start_algorithm(_parent("200"), TWAP(4, Urgency.neutral()), _terms())
    assert one.algorithm_id != other.algorithm_id, "a run's identity includes its parent"


def test_the_schedule_identity_is_stable_and_content_sensitive() -> None:
    first = plan_schedule(D("100"), TWAP(4, Urgency.neutral()), _terms())
    assert (
        first.schedule_id
        == plan_schedule(D("100"), TWAP(4, Urgency.neutral()), _terms()).schedule_id
    )
    assert (
        first.schedule_id
        != plan_schedule(D("101"), TWAP(4, Urgency.neutral()), _terms()).schedule_id
    )


def test_the_schedule_does_not_read_the_callers_decimal_context() -> None:
    import decimal

    expected = plan_schedule(D("1000"), TWAP(7, Urgency(D("2.5"))), _terms())
    with decimal.localcontext() as context:
        context.prec = 3
        context.rounding = decimal.ROUND_UP
        assert plan_schedule(D("1000"), TWAP(7, Urgency(D("2.5"))), _terms()) == expected
