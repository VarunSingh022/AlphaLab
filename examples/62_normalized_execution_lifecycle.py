"""
AlphaLab Examples
=================

Example 62 : A Normalized Execution Lifecycle

Difficulty : Intermediate

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 05 (the broker boundary)
✓ Example 45 (broker reconciliation)
✓ Example 61 (broker capabilities)

Topics
------

• One order-transition table, read by the OMS and by the venue boundary alike
• Twelve normalized event kinds an adapter translates its venue's messages into
• Every reported event classified exactly once: APPLIED, DUPLICATE, STALE,
  CONFLICT, UNKNOWN_ORDER or INVALID -- redelivery and reordering are routine,
  contradictions are breaks
• A fill that arrives before its acknowledgement, a fill that crosses a cancel,
  a refused cancel, an amendment and a late fill after the cancel landed
• Cancels and amendments with identities, so a retry is never a second request
• Fills delivered out of order landing exactly where they would have in order
• A disconnect, a reconnect, and the snapshot reconciliation a reconnect demands

What this shows
---------------

A venue reports what happened to an order in whatever order its network
delivers the messages, sometimes twice. AlphaLab's mirror of the venue applies
each normalized report through one table of legal transitions -- the table the
OMS reads -- and says what every report was. A redelivered fill and a late
acknowledgement change nothing and are not faults; a fill against an order the
mirror holds as cancelled is a contradiction, reported as one and left for a
reconciliation to settle. Nothing here knows which venue sent a message: the
vendor's protocol ends in the application's adapter, and the contract starts at
the normalized event.

Run

    python examples/62_normalized_execution_lifecycle.py
"""

from dataclasses import replace
from decimal import Decimal

from _execution_world import (
    ASSET_ID,
    OPEN,
    ScriptedVenue,
    banner,
    clock,
    connected_venue,
    number,
    refusal,
    say,
    section,
    short,
)

from alphalab.broker import (
    BrokerExecution,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerState,
    CancelRequest,
    ExecutionEventKind,
    LifecycleDecision,
    ModifyRequest,
    RequestDecision,
    RequestLedger,
    VenueEvent,
    VenueSnapshot,
    apply_venue_event,
    issue_cancel,
    issue_modify,
    reconcile_snapshot,
)
from alphalab.core import (
    ORDER_TRANSITIONS,
    TERMINAL_ORDER_STATUSES,
    classify_order_event,
    reachable_statuses,
)
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms import InvalidTransitionError, Order, OrderId

E = ExecutionEventKind
CASH = Decimal("1000000")


def name(status: OrderStatus | BrokerOrderStatus | None) -> str:
    return "-" if status is None else status.name.lower()


#: Where the second line of each entry, and its commentary, starts.
DETAIL = " " * 16


def show(decision: LifecycleDecision, what: str = "") -> None:
    """Two lines per reported event: when and what; then the outcome and the status it left."""

    event = decision.event
    print(f"  {clock(event.timestamp):<14}{event.kind!s:<25}{what}")
    before, after = decision.status_before, decision.status_after
    if before is None:
        state = ""
    elif decision.applied:
        state = f"{name(before)} -> {name(after)}"
    else:
        state = f"order stays {name(before)}"
    print(f"{DETAIL}{decision.outcome!s:<14}{state}")
    if decision.implied:
        say(f"implies {', '.join(str(kind) for kind in decision.implied)}", indent=DETAIL + "  ")
    if decision.reason:
        say(("note: " if decision.applied else "") + decision.reason, indent=DETAIL + "  ")


def request_line(decision: RequestDecision, what: str) -> None:
    """Two lines per cancel or amendment: what was asked; then whether to send it."""

    request = decision.request
    kind = "cancel request" if isinstance(request, CancelRequest) else "modify request"
    print(f"  {clock(request.requested_at):<14}{kind:<25}{what}")
    sent = "send it" if decision.send else "do not send it"
    print(f"{DETAIL}{decision.outcome!s:<14}{sent}  (request {short(request.request_id)})")
    if decision.reason:
        say(decision.reason, indent=DETAIL + "  ")


def fill(handle: str, execution_id: str, quantity: str, price: str, at: float) -> BrokerExecution:
    return BrokerExecution(
        execution_id, handle, ASSET_ID, Decimal(quantity), Decimal(price), Decimal("0"), at
    )


def fill_event(execution: BrokerExecution, complete: bool) -> VenueEvent:
    kind = E.ORDER_FILLED if complete else E.ORDER_PARTIALLY_FILLED
    return VenueEvent(kind, execution.timestamp, execution.broker_order_id, execution=execution)


def submitted(state: BrokerState, handle: str, quantity: str, price: str, at: float) -> BrokerState:
    order = BrokerOrder(
        broker_order_id=handle,
        oms_order_id=f"OMS-{handle}",
        symbol=ASSET_ID,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_quantity=Decimal("0"),
        average_fill_price=Decimal("0"),
        status=BrokerOrderStatus.PENDING_SUBMIT,
        created_at=at,
        updated_at=at,
    )
    state, _ = ScriptedVenue().submit_order(state, order, at)
    return state


def the_table() -> None:
    section("One table of legal transitions")
    for status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_PENDING):
        row = ORDER_TRANSITIONS[status]
        moves = ", ".join(f"{event} -> {name(target)}" for event, target in row.items())
        print(f"  from {name(status)}:")
        say(moves, indent="      ")
    finished = ", ".join(sorted(name(status) for status in TERMINAL_ORDER_STATUSES))
    print(f"  terminal, with no way out: {finished}")
    reachable = ", ".join(sorted(name(s) for s in reachable_statuses(OrderStatus.ACCEPTED)))
    say(f"reachable from accepted: {reachable}")
    print()
    print("  a reported status, judged against the status an order holds:")
    for held, event, filled in (
        (OrderStatus.ACCEPTED, E.ORDER_CANCELLED, "0"),
        (OrderStatus.CANCELLED, E.ORDER_CANCELLED, "0"),
        (OrderStatus.PARTIALLY_FILLED, E.ORDER_ACCEPTED, "400"),
        (OrderStatus.FILLED, E.ORDER_CANCELLED, "1000"),
        (OrderStatus.PARTIALLY_FILLED, E.ORDER_REJECTED, "400"),
    ):
        verdict, _ = classify_order_event(held, event, Decimal(filled))
        print(f"    held {name(held):<17} reported {event!s:<18} -> {verdict}")
    say(
        "A report of the status an order already holds is a DUPLICATE; one the order has "
        "already moved past is STALE -- a late message, not a fault. A status the order can "
        "neither reach nor has passed through -- a cancel for a filled order, a rejection "
        "for one that traded -- is a CONFLICT: the two sides disagree."
    )


def a_venue_day() -> BrokerState:
    section("A venue's reports about one order, normalized and classified")
    venue = connected_venue("VENUE-NORTH", CASH)
    handle = "N-0001"
    venue = submitted(venue, handle, "1000", "100.02", OPEN)
    print(f"  {clock(OPEN):<14}buy 1,000 ACME, limit 100.02, sent as {handle}: submitted")
    ledger = RequestLedger()

    def deliver(event: VenueEvent, what: str = "") -> None:
        nonlocal venue
        venue, decision = apply_venue_event(venue, event)
        show(decision, what)

    first = fill(handle, "X-1", "400", "100.01", OPEN + 0.3)
    deliver(fill_event(first, complete=False), "X-1: 400 @ 100.01, before any ack")
    deliver(VenueEvent(E.ORDER_ACCEPTED, OPEN + 0.25, handle), "the ack, delivered late")
    deliver(fill_event(first, complete=False), "X-1, redelivered")

    amend = ModifyRequest(handle, 1, Decimal("1500"), Decimal("100.03"), OPEN + 5.0)
    venue, ledger, decided = issue_modify(venue, ledger, amend)
    request_line(decided, "1,500 @ 100.03, revision 1")
    retry = replace(amend, requested_at=OPEN + 6.0)
    venue, ledger, decided = issue_modify(venue, ledger, retry)
    request_line(decided, "the same amendment, retried")
    confirmed = VenueEvent(
        E.ORDER_REPLACED, OPEN + 6.2, handle, quantity=Decimal("1500"), price=Decimal("100.03")
    )
    deliver(confirmed, "now 1,500 @ 100.03")
    deliver(replace(confirmed, timestamp=OPEN + 6.4), "the same amendment, restated")

    deliver(
        fill_event(fill(handle, "X-2", "600", "100.03", OPEN + 60.0), False), "X-2: 600 @ 100.03"
    )

    venue, ledger, decided = issue_cancel(venue, ledger, CancelRequest(handle, 1, OPEN + 90.0))
    request_line(decided, "attempt 1")
    venue, ledger, decided = issue_cancel(venue, ledger, CancelRequest(handle, 1, OPEN + 90.1))
    request_line(decided, "attempt 1, retried")
    crossed = fill(handle, "X-3", "200", "100.03", OPEN + 90.2)
    deliver(fill_event(crossed, False), "X-3: 200, crossing the cancel")
    refused = VenueEvent(E.ORDER_CANCEL_REJECTED, OPEN + 90.5, handle, reason="too late")
    deliver(refused, "the venue refuses the cancel")
    venue, ledger, decided = issue_cancel(venue, ledger, CancelRequest(handle, 2, OPEN + 95.0))
    request_line(decided, "attempt 2")
    deliver(VenueEvent(E.ORDER_CANCELLED, OPEN + 95.3, handle), "the last 300 cancelled")
    late = fill(handle, "X-4", "100", "100.03", OPEN + 96.0)
    deliver(fill_event(late, False), "X-4: 100, after the cancel")
    deliver(VenueEvent(E.ORDER_CANCELLED, OPEN + 97.0, "N-9999"), "an order never sent")

    held = venue.orders[handle]
    print()
    print(
        f"  the mirror: {handle} {name(held.status)}, {number(held.filled_quantity)} of "
        f"{number(held.quantity)} filled at {held.average_fill_price:.4f}"
    )
    say(
        "The fill that crossed the cancel is real and was applied, and the order stayed "
        "PENDING_CANCEL: the cancel was still in flight. The venue's refusal returned it to "
        "the working status its fills imply. The fill reported after the cancel landed "
        "contradicts the mirror and was not applied; it is a break, and resolving it is a "
        "reconciliation's job, not a guess made here."
    )
    return venue


def account_and_connection(venue: BrokerState) -> BrokerState:
    section("Positions, balances and the connection")
    filled = venue.orders["N-0001"]
    position = BrokerPosition(
        ASSET_ID,
        filled.filled_quantity,
        filled.average_fill_price,
        filled.filled_quantity * Decimal("100.03"),
        Decimal("0"),
        Decimal("0"),
    )
    spent = filled.filled_quantity * filled.average_fill_price
    balances = replace(venue.account, cash=CASH - spent, available_funds=CASH - spent)
    for event, what in (
        (VenueEvent(E.POSITION_CHANGED, OPEN + 100.0, position=position), "holds 1,200 ACME"),
        (VenueEvent(E.POSITION_CHANGED, OPEN + 100.1, position=position), "the same, restated"),
        (VenueEvent(E.BALANCE_CHANGED, OPEN + 100.2, account=balances), "cash after the fills"),
        (VenueEvent(E.BROKER_DISCONNECTED, OPEN + 120.0, reason="heartbeat lost"), ""),
        (VenueEvent(E.BROKER_DISCONNECTED, OPEN + 121.0), "the same, restated"),
        (VenueEvent(E.BROKER_CONNECTED, OPEN + 150.0), ""),
    ):
        venue, decision = apply_venue_event(venue, event)
        show(decision, what)
        if decision.resync_required:
            say(
                "resync required: until a snapshot of what the venue holds is reconciled, "
                "the mirror is not known to be right and nothing new should be sent.",
                indent=DETAIL + "  ",
            )
    return venue


def out_of_order() -> None:
    section("Fills delivered out of order land where they would have in order")
    fills = [
        (fill("N-0002", "Y-1", "300", "100.00", OPEN + 1.0), False),
        (fill("N-0002", "Y-2", "300", "100.01", OPEN + 2.0), False),
        (fill("N-0002", "Y-3", "400", "100.02", OPEN + 3.0), True),
    ]
    ack = VenueEvent(E.ORDER_ACCEPTED, OPEN + 0.2, "N-0002")
    in_order = [ack, *(fill_event(execution, complete) for execution, complete in fills)]
    reversed_order = [*reversed(in_order[1:]), ack]
    finals = []
    for label, events in (("as they happened", in_order), ("reversed", reversed_order)):
        venue = submitted(connected_venue("VENUE-NORTH", CASH), "N-0002", "1000", "100.02", OPEN)
        outcomes = []
        for event in events:
            venue, decision = apply_venue_event(venue, event)
            label_of = "ack" if event.execution is None else event.execution.execution_id
            outcomes.append(f"{label_of} {decision.outcome}")
        order = venue.orders["N-0002"]
        finals.append((order.status, order.filled_quantity, order.average_fill_price))
        print(f"  {label:<17} {', '.join(outcomes)}")
        print(
            f"  {'':<17} ends {name(order.status)}, {number(order.filled_quantity)} filled, "
            f"average {order.average_fill_price:.4f}"
        )
    print(f"  the same order either way: {finals[0] == finals[1]}")
    say(
        "The fill named ORDER_FILLED arrived first and left 600 working: the venue named it "
        "by its own cumulative state, which was ahead of the mirror's. The quantities decide, "
        "so the reversed stream converges on the same order, and the acknowledgement that "
        "arrives last is STALE rather than an error."
    )


def snapshot(venue: BrokerState) -> None:
    section("Reconciling the mirror against the venue's own snapshot")
    at = OPEN + 160.0
    executions = tuple(venue.executions.values())
    reported = VenueSnapshot(
        as_of=at,
        orders=tuple(venue.orders.values()),
        executions=executions,
        positions=tuple(venue.positions.values()),
        account=venue.account,
    )
    agreed = reconcile_snapshot(venue, reported, evaluated_at=at + 1.0, max_age_seconds=5.0)
    say(
        f"the venue's own records at {clock(at)}: {agreed.freshness}; compared "
        f"{agreed.compared_orders} order, {agreed.compared_executions} fills and "
        f"{agreed.compared_positions} position; fully reconciled: {agreed.fully_reconciled}"
    )
    lost = fill("N-0001", "X-4", "100", "100.03", OPEN + 96.0)
    with_lost_fill = replace(
        reported,
        executions=(*executions, lost),
        orders=(replace(venue.orders["N-0001"], filled_quantity=Decimal("1300")),),
    )
    disagreed = reconcile_snapshot(
        venue, with_lost_fill, evaluated_at=at + 1.0, max_age_seconds=5.0
    )
    print(f"  the venue says X-4 filled after all -- reconciled: {disagreed.reconciled}")
    for divergence in disagreed.divergences:
        print(f"    {divergence.kind} {divergence.key}")
        say(
            f"mirror: {divergence.local}; venue: {divergence.remote}. {divergence.reason}",
            indent="      ",
        )
    early = reconcile_snapshot(
        venue, replace(reported, as_of=OPEN + 60.0), evaluated_at=at, max_age_seconds=300.0
    )
    print(
        f"  a snapshot the venue took at {clock(OPEN + 60.0)}, before the cancel: {early.freshness}"
    )
    print(
        f"    orders compared: {early.compared_orders}; divergences reported: "
        f"{len(early.divergences)}"
    )
    say(
        "The contested fill X-4 was a CONFLICT when it arrived; the venue's snapshot now says "
        "it happened, and the reconciliation reports exactly where the two disagree. A "
        "snapshot older than what the mirror has heard since is not compared at all -- every "
        "difference it showed could be the mirror being right."
    )


def the_oms_reads_it_too() -> None:
    section("The OMS refuses by the same table")
    order = Order(
        order_id=OrderId.generate(),
        strategy_id="MOMENTUM",
        asset_id=ASSET_ID,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.NEW,
        quantity=Decimal("1000"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("1000"),
        limit_price=Decimal("100.02"),
        stop_price=None,
        average_fill_price=Decimal("0"),
        created_at=OPEN,
        updated_at=OPEN,
    )
    order = order.accept(OPEN + 0.25)
    order = order.partial_fill(Decimal("400"), Decimal("100.01"), OPEN + 0.3)
    order = order.cancel(OPEN + 95.3)
    print(f"  OMS order: {name(order.status)} with {number(order.filled_quantity)} filled")
    try:
        order.fill(Decimal("600"), Decimal("100.03"), OPEN + 96.0)
    except InvalidTransitionError as error:
        refusal("the OMS asked to fill it afterwards", error)
    verdict, _ = classify_order_event(OrderStatus.CANCELLED, E.ORDER_REJECTED, Decimal("400"))
    print(f"  a venue reporting it rejected, judged by the same table: {verdict}")


def main() -> None:
    banner(62, "A Normalized Execution Lifecycle")
    the_table()
    venue = a_venue_day()
    venue = account_and_connection(venue)
    out_of_order()
    snapshot(venue)
    the_oms_reads_it_too()
    print()


if __name__ == "__main__":
    main()
