"""A strategy amends its own working order (phase 3 FUT-029; phase 4 FUT-031).

``IntentKind.AMEND`` names the quantity to leave working and the order's terms
-- its own type and time in force. A reduction at the same prices is made in
place and keeps the order's id, and so its place in a queue model's queue; any
other amend is prepared and committed (FUT-031): its replacement is judged by
allocation, risk and the OMS against the book without the order, at the back of
its queue, and only if it is placed is the order withdrawn. A replacement they
refuse leaves the order working, its reservation held, and is recorded as an
:class:`AmendRefused`; so is anything else that cannot be amended.

ACME (in CNY) quotes from 2.0, one quote a second; the strategy rests a bid of
10 at 95 at 2.0 (the helpers are tests/integration/test_trading_controls.py's).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation.events import BudgetExceeded
from alphalab.backtesting import QueuedResult, ReplayBacktest, RunSchedule
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.execution.queue import PriceTimeQueue
from alphalab.lifecycle import digest_run
from alphalab.strategy.events import AmendRefused, Intent, IntentKind
from alphalab.strategy.exceptions import InvalidIntentError
from alphalab.strategy.validation import validate_intent
from tests.integration import test_margin_and_liquidation as margin
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


def test_an_increase_risk_refuses_leaves_the_order_working_as_it_was() -> None:
    from alphalab.risk.limits import OrderSizeLimit

    tight = replace(permissive_risk_limits(), order_size=OrderSizeLimit(D("15"), D("100000000")))
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "20", bid()),)}
    mids = {ACME.asset_id: {2.0: "100", 3.0: "100", 4.0: "95"}}
    result, strategy = controls.run(script, mids, None, risk_limits=tight)
    # The 20 exceeds the order size limit of 15: risk refuses the replacement,
    # as it would a new order, and the amend is not made -- the 10 at 95 works
    # on, and fills at 4.0 (FUT-031; under FUT-029 it was withdrawn first).
    assert strategy.rejections == ["Rejected due to 1 constraint violations."]
    (order,) = orders(result)
    assert (order.quantity, order.status.name) == (D("10"), "FILLED")
    assert controls.trades(result) == [(4.0, D("10.000000"), D("95"))]
    (refusal,) = refused(result)
    assert "kept working as it was" in refusal.reason
    # Risk's refusal stays on its record, though the book it judged was not kept.
    from alphalab.risk.events import RiskRejected

    assert any(isinstance(e, RiskRejected) for e in result.state.risk.events)


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


# --------------------------------------------------------------------------- #
# Atomic: prepared, then committed or rolled back (FUT-031)
# --------------------------------------------------------------------------- #


def reservations(result: Any) -> dict[str, Decimal]:
    return dict(result.state.allocation.reservations.items())


def test_an_increase_the_cash_cannot_pay_keeps_the_order_and_its_reservation() -> None:
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "200", bid()),)}
    mids = {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}
    before, _ = controls.run(REST, mids, None, starting_cash=D("5000"))
    result, strategy = controls.run(script, mids, None, starting_cash=D("5000"))
    # 200 at 95 is 19,000 against 5,000 of cash: refused, and the book is the
    # book the run would have had without the amend -- the one order, the one
    # reservation, nothing released or reserved twice.
    (order,) = orders(result)
    assert order.is_open and order.quantity == D("10")
    assert reservations(result) == reservations(before)
    assert result.state.allocation.notional_allocated == before.state.allocation.notional_allocated
    # Allocation refused it, as it would refuse a new order, and its refusal is
    # on the record beside the amend's.
    (refusal,) = refused(result)
    assert "exceeds" in refusal.reason
    assert any(isinstance(e, BudgetExceeded) for e in result.state.allocation.events)
    assert not strategy.rejections  # an order never placed is not one rejected


@pytest.mark.parametrize(
    ("terms", "declared", "why"),
    [
        (bid("95.03"), None, "price grid"),
        (bid("120"), "band", "band"),
    ],
)
def test_a_new_price_the_venue_refuses_leaves_the_order_working(
    terms: OrderTerms, declared: str | None, why: str
) -> None:
    from alphalab.conventions.tick import TickSchedule
    from alphalab.instrument.controls import BandAction, PriceBand, TradingControls

    gridded = replace(ACME, tick=TickSchedule.flat(D("0.05")))
    venue = (
        TradingControls(
            bands=(PriceBand(ACME.asset_id, 1.0, None, D("90"), D("110"), BandAction.REFUSE),)
        )
        if declared
        else None
    )
    script = {
        (2.0, ACME.asset_id): (controls.buy(2.0, "10", terms=bid(), asset=gridded),),
        (3.0, ACME.asset_id): (amend(3.0, "10", terms),),
    }
    result, _ = controls.run(script, {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}, venue, gridded)
    (order,) = orders(result)
    assert (order.limit_price, order.is_open) == (D("95"), True)
    (refusal,) = refused(result)
    assert why in refusal.reason.lower()


def test_a_committed_amend_leaves_one_order_one_contribution_one_reservation() -> None:
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "12", bid("96")),)}
    result, _ = controls.run(script, {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}, None)
    first, second = orders(result)
    assert first.status.name == "CANCELLED" and second.is_open
    assert (second.quantity, second.limit_price) == (D("12"), D("96"))
    # No duplicate exposure: the withdrawn order holds nothing, the replacement
    # holds exactly its own 12 -- reserved, as allocation reserves any order, at
    # the market's 100 -- and only it is working.
    held = reservations(result)
    assert set(held) == {str(second.order_id.value)}
    assert held[str(second.order_id.value)] == D("1200")
    assert result.state.allocation.notional_allocated == D("1200")


def test_an_increase_is_exactly_the_quantity_asked_whatever_the_sizing_model() -> None:
    from alphalab.allocation.sizing import FixedDollarSizing

    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "25", bid()),)}
    base = controls.config(ACME)
    sized = replace(base, pipeline=replace(base.pipeline, sizing_model=FixedDollarSizing()))
    from alphalab.backtesting import BacktestEngine

    result = BacktestEngine.run(
        sized,
        controls.data({ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}),
        running_strategy_state("S", controls.Script(script)),
        context_factory,
        schedule=RunSchedule.of(),
    )
    *_, replacement = orders(result)
    assert replacement.quantity == D("25")


def test_an_order_cancelled_in_the_same_batch_is_not_amended() -> None:
    cancel = Intent("S", ACME.asset_id, D("0"), timestamp=3.0, kind=IntentKind.CANCEL)
    script = {**REST, (3.0, ACME.asset_id): (cancel, amend(3.0, "12", bid("96")))}
    result, _ = controls.run(script, {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}, None)
    # Cancels are carried out first: the amend finds nothing to amend, and
    # nothing is placed in the cancelled order's stead.
    (order,) = orders(result)
    assert order.status.name == "CANCELLED"
    (refusal,) = refused(result)
    assert "has 0" in refusal.reason


def test_an_order_filled_before_the_amend_arrives_is_not_amended() -> None:
    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "12", bid("96")),)}
    # 3.0's quote fills the bid at 95 before the strategy hears of it.
    result, _ = controls.run(script, {ACME.asset_id: {2.0: "100", 3.0: "94", 4.0: "94"}}, None)
    (order,) = orders(result)
    assert order.status.name == "FILLED"
    assert controls.trades(result) == [(3.0, D("10.000000"), D("95"))]
    assert "has 0" in refused(result)[0].reason


def test_a_price_amend_goes_to_the_back_of_the_queue() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "99", 4.0: "99"}}
    result, _ = controls.run(
        {**REST, (3.0, ACME.asset_id): (amend(3.0, "10", bid("96")),)},
        mids,
        None,
        size="1000",
        queue=PriceTimeQueue(),
    )
    assert isinstance(result, QueuedResult)
    first, second = orders(result)
    assert result.queue.position_of(str(first.order_id.value)) is None
    assert result.queue.position_of(str(second.order_id.value)) is not None


def test_a_refused_amend_replays_and_resumes_to_one_result() -> None:
    from alphalab.backtesting import PausedReplay
    from alphalab.backtesting.replay_snapshot import capture as capture_replay
    from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
    from alphalab.backtesting.replay_snapshot import restore as restore_replay
    from alphalab.persistence import deserialize, serialize
    from alphalab.runtime.run_snapshot import RunObjects
    from alphalab.runtime.snapshot import RuntimeObjects

    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "200", bid()),)}
    mids = {ACME.asset_id: {2.0: "100", 3.0: "100", 4.0: "95", 5.0: "95"}}
    base = controls.config(ACME, starting_cash=D("5000"))
    dataset = controls.data(mids)
    whole = ReplayBacktest.run(
        base, dataset, running_strategy_state("S", controls.Script(script)), context_factory,
        schedule=RunSchedule.of(),
    )  # fmt: skip
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            base, dataset, running_strategy_state("S", controls.Script(script)), context_factory,
            schedule=RunSchedule.of(),
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                base.pipeline.sizing_model,
                base.pipeline.simulator,
                {"S": controls.Script(script)},
                base.pipeline.instruments,
            ),
            base.fill_policy,
        )
        restored = restore_replay(
            replay_from_primitives(payload), objects, dataset, RunSchedule.of()
        )
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    assert refused(whole.backtest)


def test_an_amend_routed_to_a_venue_is_the_venues_request_and_never_made_here() -> None:
    from alphalab.market.quote import Quote
    from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting

    script = {**REST, (3.0, ACME.asset_id): (amend(3.0, "12", bid("96")),)}
    base = controls.config(ACME)
    pipeline = replace(base.pipeline, routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        pipeline, running_strategy_state("S", controls.Script(script)), 1.0
    )
    for record in controls.data({ACME.asset_id: controls.flat(2.0, 3.0)}).records:
        quote = record.payload
        assert isinstance(quote, Quote)
        state = ExecutionPipeline.process_quote(state, quote, context_factory).state
    # The order is the venue's: an amend there is LiveSession.amend's request,
    # confirmed or refused by the venue later -- never a promise of atomicity
    # the run cannot keep.
    (order,) = state.oms.orders.orders()
    assert order.is_open and order.limit_price == D("95")
    (refusal,) = [e for e in state.strategy.events if isinstance(e, AmendRefused)]
    assert "LiveSession.amend" in refusal.reason


def test_a_strategy_holding_the_instrument_has_its_replacement_sized_as_asked() -> None:
    held = {(2.0, ACME.asset_id): (controls.buy(2.0, "5"), controls.buy(2.0, "10", terms=bid()))}
    script = {**held, (3.0, ACME.asset_id): (amend(3.0, "12", bid("96")),)}
    result, _ = controls.run(script, {ACME.asset_id: controls.flat(2.0, 3.0, 4.0)}, None)
    # The 5 bought at 2.0 is held; the replacement is still the 12 asked.
    *_, replacement = orders(result)
    assert (replacement.quantity, replacement.is_open) == (D("12"), True)
