"""
AlphaLab Examples
=================

Example 63 : Execution Algorithms

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 25 (execution simulation)
✓ Example 26 (capacity modelling)
✓ Example 62 (the normalized execution lifecycle)

Topics
------

• TWAP and VWAP as one construction: a clock, a trajectory shaped by a stated
  urgency, and whole increments apportioned by the largest remainder method
• VWAP planned on a supplied, attributed volume profile -- and an incomplete
  profile refused, or replaced by time and recorded as such
• Working a schedule: every release tops up to the trajectory, so a child
  that expired unfilled is caught up by the next one
• Participation: a share of the volume actually observed, never of volume
  nobody saw, with minimum and maximum children and a stated end-of-window rule
• Slicing by size and by count, and an iceberg-like tranche at a time, with
  the hidden remainder kept inside AlphaLab
• Every child carrying the strategies that asked for the parent, unchanged
• Configuration, run and schedule identities, and the algorithm entering a
  strategy's research record

What this shows
---------------

How to trade ten thousand shares is a separate question from whether to own
them. Each algorithm here states its objective, its inputs, how it sizes and
times each child and what it does at the end, and nothing else changes its
output: time is supplied rather than read, volume is supplied and attributed,
and the arithmetic runs in a fixed decimal context, so a schedule is identical
in a backtest, a replay and a live session on any machine. The shapes are
standard -- the TWAP/VWAP trajectory is the Almgren-Chriss curve with its
urgency stated by the caller -- and AlphaLab claims no more for them than it
states: it does not estimate urgency, and it does not call a schedule optimal.

Run

    python examples/63_execution_algorithms.py
"""

from dataclasses import replace
from decimal import Decimal

from _execution_world import (
    ASSET_ID,
    CLOSE,
    INTERVALS,
    OPEN,
    banner,
    clock,
    interval,
    number,
    observed,
    refusal,
    say,
    section,
    short,
    volume_profile,
)

from alphalab.core import OrderRequest, StrategyContribution
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.execution import (
    TWAP,
    VWAP,
    AlgorithmState,
    AlgorithmTerms,
    ExecutionValidationError,
    Iceberg,
    IncompletePolicy,
    MissingVolumePolicy,
    Participation,
    Slicing,
    Urgency,
    VolumeProfile,
    cancel_algorithm,
    plan_schedule,
    record_child_execution,
    record_child_outcome,
    release_children,
    start_algorithm,
)
from alphalab.lifecycle import research_configuration_with_execution

#: The desk's netted order: MOMENTUM asked for 6,000 and MEANREV for 4,000.
PARENT = OrderRequest(
    order_id="DESK-PARENT-1",
    strategy_id="",
    asset_id=ASSET_ID,
    side=Side.BUY,
    quantity=Decimal("10000"),
    price=Decimal("99.95"),
    timestamp=OPEN - 60.0,
    contributions=(
        StrategyContribution("MEANREV", Decimal("4000")),
        StrategyContribution("MOMENTUM", Decimal("6000")),
    ),
)

#: The morning, whole shares, and a limit every child carries.
TERMS = AlgorithmTerms(OPEN, CLOSE, Decimal("1"), OrderType.LIMIT, Decimal("100.30"))

STRAIGHT = Urgency.neutral()
FRONT_LOADED = Urgency(Decimal("2"))
DESK_VWAP = VWAP(volume_profile(), MissingVolumePolicy.REFUSE, STRAIGHT)


def schedules() -> None:
    section("Three schedules for one parent of 10,000")
    plans = {
        "TWAP k=0": plan_schedule(PARENT.quantity, TWAP(INTERVALS, STRAIGHT), TERMS),
        "TWAP k=2": plan_schedule(PARENT.quantity, TWAP(INTERVALS, FRONT_LOADED), TERMS),
        "VWAP k=0": plan_schedule(PARENT.quantity, DESK_VWAP, TERMS),
    }
    print(f"  {'slice':<8}{'from':<8}" + "".join(f"{label:>12}" for label in plans))
    for index in range(INTERVALS):
        start, _ = interval(index)
        cells = "".join(f"{number(plan.slices[index].quantity):>12}" for plan in plans.values())
        print(f"  {index + 1:<8}{clock(start):<8}{cells}")
    totals = "".join(f"{number(plan.slices[-1].target):>12}" for plan in plans.values())
    print(f"  {'total':<16}{totals}")
    for label, plan in plans.items():
        print(f"  {label}: basis {plan.basis}, schedule {short(plan.schedule_id)}")
    say(
        "Each slice's share is F(x_k) - F(x_(k-1)), where x is elapsed time for TWAP and "
        "cumulative expected volume for VWAP, and F(x) = 1 - sinh(k(1 - x)) / sinh(k) -- a "
        "straight line at urgency zero. The shares become whole shares by the largest "
        "remainder method, so every schedule sums to the parent exactly."
    )
    ten = plan_schedule(Decimal("10"), TWAP(3, STRAIGHT), TERMS)
    print(
        "  ten shares over three slices: "
        + ", ".join(number(planned.quantity) for planned in ten.slices)
        + " -- the remainder to the earliest slice"
    )


def incomplete_profile() -> None:
    section("A volume profile that cannot say how volume is distributed")
    gappy = VolumeProfile(
        tuple(observed(index) for index in range(INTERVALS)),
        "this morning's tape, with the fifth half hour lost",
    )
    try:
        plan_schedule(PARENT.quantity, VWAP(gappy, MissingVolumePolicy.REFUSE, STRAIGHT), TERMS)
    except ExecutionValidationError as error:
        refusal("VWAP on the tape, REFUSE", error)
    fallback = plan_schedule(
        PARENT.quantity, VWAP(gappy, MissingVolumePolicy.TIME_WEIGHTED, STRAIGHT), TERMS
    )
    print(f"  VWAP on the tape, TIME_WEIGHTED: basis {fallback.basis}")
    say(fallback.note, indent="    ")
    say(
        "A missing volume is None, never zero, and a schedule is never a patchwork of volume "
        "shares and time shares: the whole plan moves to time, and says so."
    )


def work_the_vwap() -> AlgorithmState:
    section("Working the VWAP: every release tops up to the trajectory")
    state = start_algorithm(PARENT, DESK_VWAP, TERMS)
    schedule = state.schedule
    assert schedule is not None
    print(f"  run {short(state.algorithm_id)}, schedule {short(schedule.schedule_id)}")
    print(f"  {'at':<8}{'child':>8}{'filled':>9}{'done':>9}{'target':>9}  what happened")
    for index in range(INTERVALS):
        start, end = interval(index)
        state, released = release_children(state, start)
        target, _ = schedule.target_at(start)
        (child,) = released
        if index == 2:
            state = record_child_execution(
                state, child.child_id, f"E-{index}", Decimal("500"), start + 600
            )
            state = record_child_outcome(state, child.child_id, OrderStatus.EXPIRED, end)
            happened = "500 filled, then expired"
        else:
            state = record_child_execution(
                state, child.child_id, f"E-{index}", child.quantity, start + 600
            )
            happened = "filled"
        progress = state.children[child.child_id]
        print(
            f"  {clock(start):<8}{number(child.quantity):>8}{number(progress.filled):>9}"
            f"{number(state.filled):>9}{number(target):>9}  {happened}"
        )
    print(f"  the run: {state.status}, {number(state.filled)} of {number(PARENT.quantity)} filled")
    say(
        "The child released at 11:00 was 476 more than its slice planned: the target less "
        "what had filled and what was working. Nothing is ever committed ahead of the "
        "trajectory, and a shortfall is made up by the next release rather than re-planned."
    )
    first = next(iter(state.children.values())).child
    print(
        "  every child carries the parent's strategies: "
        + ", ".join(f"{c.strategy_id} {number(c.quantity)}" for c in first.contributions)
    )
    return state


def participation() -> None:
    section("Participation: two percent of the volume actually printed")
    parent = replace(PARENT, order_id="DESK-PARENT-2", quantity=Decimal("15000"))
    for at_end in (IncompletePolicy.LEAVE_UNFILLED, IncompletePolicy.COMPLETE_AT_END):
        algorithm = Participation(Decimal("0.02"), Decimal("1500"), Decimal("5000"), at_end)
        state = start_algorithm(parent, algorithm, TERMS)
        sizes = []
        for index in range(1, INTERVALS + 1):
            now = interval(index)[0] if index < INTERVALS else CLOSE
            state, released = release_children(state, now, (observed(index - 1),))
            for child in released:
                sizes.append(f"{clock(now)} {number(child.quantity)}")
                state = record_child_execution(
                    state, child.child_id, f"P-{index}", child.quantity, now + 60
                )
        print(f"  {at_end}:")
        say(", ".join(sizes), indent="    ")
        say(
            f"{state.status}: {number(state.filled)} of {number(parent.quantity)} filled. "
            + (state.reason if state.filled < parent.quantity else ""),
            indent="    ",
        )
    print("  what the run noted:")
    for note in state.notes:
        say(note, indent="    ")
    say(
        "The 11:00 top-up of 1,400 fell below the 1,500 minimum and was withheld. The half "
        "hour from 11:30 printed no volume, so nothing was sized from it and nothing was "
        "released at 12:00. The last observation arrives at the close, when no window is "
        "left to trade it: LEAVE_UNFILLED reports the rest unfilled, and COMPLETE_AT_END -- "
        "chosen, never defaulted -- sends it anyway, above the participation rate."
    )


def slicing_and_iceberg() -> None:
    section("Slices and an iceberg-like tranche, one working at a time")
    for label, algorithm in (
        ("slices of 4,000", Slicing(Decimal("4000"), None)),
        ("three slices", Slicing(None, 3)),
    ):
        state = start_algorithm(PARENT, algorithm, TERMS)
        sizes = []
        for step in range(5):
            state, released = release_children(state, OPEN + step)
            for child in released:
                sizes.append(number(child.quantity))
                state = record_child_execution(
                    state, child.child_id, f"S-{step}", child.quantity, OPEN + step
                )
        print(f"  {label:<16} {' + '.join(sizes)} = {number(state.filled)}, {state.status}")

    state = start_algorithm(PARENT, Iceberg(Decimal("1000")), TERMS)
    state, (tranche,) = release_children(state, OPEN)
    print(
        f"  iceberg of 1,000: displayed {number(state.displayed_quantity)}, "
        f"hidden {number(state.hidden_quantity)}"
    )
    state = record_child_execution(state, tranche.child_id, "I-1", Decimal("1000"), OPEN + 30)
    state, (tranche,) = release_children(state, OPEN + 31)
    state = record_child_execution(state, tranche.child_id, "I-2", Decimal("400"), OPEN + 45)
    say(
        f"after one tranche and part of the next: filled {number(state.filled)}, "
        f"displayed {number(state.displayed_quantity)}, hidden {number(state.hidden_quantity)}",
        indent="    ",
    )
    state, to_cancel = cancel_algorithm(state, OPEN + 60, "the desk withdrew the order")
    working = ", ".join(f"{c.child_id} ({number(c.quantity)})" for c in to_cancel)
    print(f"    cancelled: {state.status}; cancel at the venue: {working}")
    state = record_child_execution(state, tranche.child_id, "I-3", Decimal("100"), OPEN + 60.2)
    print(f"    a fill that crossed the cancel still counts: filled {number(state.filled)}")
    say(
        "The hidden quantity never leaves AlphaLab: no venue sees it, and no venue's native "
        "reserve order is emulated. A venue that offers one is reached through its adapter."
    )


def identities(worked: AlgorithmState) -> None:
    section("Identities, and the algorithm in the research record")
    again = start_algorithm(PARENT, DESK_VWAP, TERMS)
    assert worked.schedule is not None and again.schedule is not None
    print(f"  VWAP configuration   {short(DESK_VWAP.configuration_id)}")
    print(f"  run, planned twice   {short(worked.algorithm_id)}  {short(again.algorithm_id)}")
    print(
        f"  schedule, twice      {short(worked.schedule.schedule_id)}  "
        f"{short(again.schedule.schedule_id)}"
    )
    other = VWAP(volume_profile(), MissingVolumePolicy.REFUSE, FRONT_LOADED)
    print(f"  another urgency      {short(other.configuration_id)}")
    research = research_configuration_with_execution(
        {"signal": "momentum-and-reversion", "universe": "ACME"},
        algorithms={"desk_parent": DESK_VWAP},
        routing={},
        study=None,
    )
    for key, value in sorted(research.settings.items()):
        print(f"  research setting     {key} = {short(value) if len(value) == 64 else value}")
    say(
        "A strategy whose orders were worked by this VWAP is a different measurement from "
        "the same strategy filled at once, so the configuration's identity enters its "
        "research record and, through it, its fingerprint. Change the urgency and the "
        "identity changes with it."
    )


def main() -> None:
    banner(63, "Execution Algorithms")
    schedules()
    incomplete_profile()
    worked = work_the_vwap()
    participation()
    slicing_and_iceberg()
    identities(worked)
    print()


if __name__ == "__main__":
    main()
