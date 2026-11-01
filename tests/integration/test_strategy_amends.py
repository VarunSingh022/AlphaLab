"""A strategy amends its own working order in place (phase 3, ledger FUT-029).

``IntentKind.AMEND`` names the quantity to leave working and the order's terms
-- its own type and time in force. A reduction at the same prices is made in
place and keeps the order's id, and so its place in a queue model's queue; any
other amend withdraws the order and places its replacement through allocation,
risk and the OMS, at the back of its queue. What cannot be amended is recorded
as an :class:`AmendRefused` and changes nothing.

ACME (in CNY) quotes from 2.0, one quote a second; the strategy rests a bid of
10 at 95 at 2.0 (the helpers are tests/integration/test_trading_controls.py's).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import QueuedResult, ReplayBacktest, RunSchedule
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.execution.queue import PriceTimeQueue
from alphalab.lifecycle import digest_run
from alphalab.strategy.events import AmendRefused, Intent, IntentKind
from alphalab.strategy.exceptions import InvalidIntentError
from alphalab.strategy.validation import validate_intent
from tests.integration import test_trading_controls as controls
from tests.integration.harness import (
    context_factory,
    permissive_risk_limits,
    running_strategy_state,
)

D = Decimal
ACME = controls.ACME


def bid(price: str = "95") -> OrderTerms:
    return OrderTerms.limit(D(price), TimeInForce.GTC)


def amend(at: float, quantity: str, terms: OrderTerms) -> Intent:
    return Intent("S", ACME.asset_id, D(quantity), timestamp=at, kind=IntentKind.AMEND, terms=terms)


REST = {(2.0, ACME.asset_id): (controls.buy(2.0, "10", terms=bid()),)}


def refused(result: Any) -> list[AmendRefused]:
    return [e for e in result.state.strategy.events if isinstance(e, AmendRefused)]


def orders(result: Any) -> list[Any]:
    return list(result.state.oms.orders.orders())


# --------------------------------------------------------------------------- #
# Amended
# --------------------------------------------------------------------------- #


def test_a_price_amend_replaces_the_order_and_trades_at_the_new_limit() -> None:
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "10", bid("97")),)}
    mids = {ACME.asset_id: {2.0: "100", 3.0: "99", 4.0: "97", 5.0: "96"}}
    result, _ = controls.run(script, mids, None)
    first, second = orders(result)
    assert (first.limit_price, first.status.name) == (D("95"), "CANCELLED")
    assert second.limit_price == D("97")
    # The market meets 97 at 4.0: the replacement fills there, at its limit.
    assert controls.trades(result) == [(4.0, D("10.000000"), D("97"))]


def test_an_amended_offer_is_replaced_by_an_offer() -> None:
    offer = Intent("S", ACME.asset_id, D("-10"), timestamp=2.0, terms=bid("105"))
    script = {
        (2.0, ACME.asset_id): (offer,),
        (3.0, ACME.asset_id): (amend(3.0, "10", bid("103")),),
    }
    mids = {ACME.asset_id: {2.0: "100", 3.0: "101", 4.0: "103"}}
    result, _ = controls.run(script, mids, None)
    _, replacement = orders(result)
    assert (replacement.side.name, replacement.limit_price) == ("SELL", D("103"))
    assert controls.trades(result) == [(4.0, D("-10.000000"), D("103"))]


def test_a_reduction_at_the_same_price_is_made_in_place() -> None:
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "4", bid()),)}
    mids = {ACME.asset_id: {2.0: "100", 3.0: "99", 4.0: "95"}}
    result, _ = controls.run(script, mids, None)
    (order,) = orders(result)
    assert order.quantity == D("4")
    assert controls.trades(result) == [(4.0, D("4.000000"), D("95"))]


def test_a_reduction_keeps_its_place_in_the_queue_and_an_increase_does_not() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "99", 4.0: "99"}}
    kept, _ = controls.run(
        {**REST, (3.0, ACME.asset_id): (amend(3.0, "4", bid()),)},
        mids,
        None,
        size="1000",
        queue=PriceTimeQueue(),
    )
    grown, _ = controls.run(
        {**REST, (3.0, ACME.asset_id): (amend(3.0, "20", bid()),)},
        mids,
        None,
        size="1000",
        queue=PriceTimeQueue(),
    )
    assert isinstance(kept, QueuedResult) and isinstance(grown, QueuedResult)
    (reduced,) = orders(kept)
    assert reduced.is_open and kept.queue.position_of(str(reduced.order_id.value)) is not None
    first, second = orders(grown)
    assert first.status.name == "CANCELLED" and second.quantity == D("20")
    # The replacement is a new order: it joined the queue as one at 3.0.
    assert grown.queue.position_of(str(second.order_id.value)) is not None
    assert grown.queue.position_of(str(first.order_id.value)) is None


def test_an_increase_is_judged_by_risk_as_a_new_order() -> None:
    from alphalab.risk.limits import OrderSizeLimit

    tight = replace(permissive_risk_limits(), order_size=OrderSizeLimit(D("15"), D("100000000")))
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "20", bid()),)}
    result, strategy = controls.run(
        script, {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}, None, risk_limits=tight
    )
    # The 20 exceeds the order size limit of 15: the replacement is refused by
    # risk, as a new order is, and the order it replaced was withdrawn -- an
    # amend that grows an order is a cancel and a new order, as at a venue
    # that cannot amend in place.
    assert strategy.rejections == ["Rejected due to 1 constraint violations."]
    assert [o.status.name for o in orders(result)] == ["CANCELLED"]


# --------------------------------------------------------------------------- #
# Refused
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("script", "why"),
    [
        ({(3.0, ACME.asset_id): (amend(3.0, "4", bid()),)}, "has 0"),
        (
            {
                (2.0, ACME.asset_id): (
                    controls.buy(2.0, "10", terms=bid()),
                    controls.buy(2.0, "5", terms=bid("90")),
                ),
                (3.0, ACME.asset_id): (amend(3.0, "4", bid()),),
            },
            "has 2",
        ),
        (
            {
                **REST,
                (3.0, ACME.asset_id): (
                    amend(3.0, "4", OrderTerms.limit(D("95"), TimeInForce.DAY)),
                ),
            },
            "type and time in force",
        ),
    ],
)
def test_an_amend_the_run_cannot_make_is_refused_and_changes_nothing(
    script: dict[tuple[float, str], tuple[Intent, ...]], why: str
) -> None:
    result, _ = controls.run(script, {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}, None)
    (refusal,) = refused(result)
    assert why in refusal.reason
    assert all(o.status.name != "CANCELLED" for o in orders(result))


@pytest.mark.parametrize(
    "intent",
    [
        Intent("S", "X", D("0"), kind=IntentKind.AMEND, terms=bid()),
        Intent("S", "X", D("5"), strength=D("0.5"), kind=IntentKind.AMEND, terms=bid()),
    ],
)
def test_an_amend_that_leaves_nothing_or_scales_is_invalid(intent: Intent) -> None:
    with pytest.raises(InvalidIntentError):
        validate_intent(intent)


def test_an_amend_replays_to_the_same_result() -> None:
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "10", bid("97")),)}
    mids = {ACME.asset_id: {2.0: "100", 3.0: "99", 4.0: "97", 5.0: "96"}}
    cfg, dataset = controls.config(ACME), controls.data(mids)
    backtest, _ = controls.run(script, mids, None)
    replay = ReplayBacktest.run(
        cfg,
        dataset,
        running_strategy_state("S", controls.Script(script)),
        context_factory,
        schedule=RunSchedule.of(),
    )
    assert replay.backtest.fills == backtest.fills
    assert digest_run(replay.backtest).result_id
