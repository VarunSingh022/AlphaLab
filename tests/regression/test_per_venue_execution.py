"""One execution model per declared venue, and every order routed to one (ADR-0061, FUT-045).

Every figure here is calculated by hand. A strategy buys ten shares at a daily
bar's close of 100. Two venues are declared: ALPHA charges a cent a share and
answers in a second, BETA two cents and two seconds.

* **By the router.** ``CheapestVenue`` prices the order at each venue by that
  venue's cost model: ten cents at ALPHA, twenty at BETA. ALPHA fills it, its
  commission ten cents, stamped one second after the bar.
* **By the terms.** An order naming BETA goes there: twenty cents, two seconds.
* **By the listing venue.** With no router, an instrument listed on XNAS goes to
  a declared XNAS.
* **Refused.** An order naming a venue the run does not declare, or one no
  declared venue routes, is refused, holding nothing.
* **Reconciled per venue.** Two orders, one to each venue, are charged each
  venue's own commission, and the run's commissions are their sum (GR-4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.order_terms import MARKET, OrderTerms
from alphalab.crypto import FeeSchedule, PriceSource, VenueSpecification, crypto_venue_execution
from alphalab.execution import CheapestVenue, VenueExecution
from alphalab.execution.commission import PercentageCommission, PerShareCommission
from alphalab.execution.costs import NoSpread
from alphalab.execution.latency import ConstantLatency
from alphalab.execution.policy import FillTiming, ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import FixedSlippage
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.decode import as_order_terms
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.assumptions import describe, execution_assumptions
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineResult
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, OrderEvent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    context_factory,
    equity,
    pipeline_config,
    registry_of,
    running_strategy_state,
)

D = Decimal
S = str(UUID(int=0x401))
ACME = equity("ACME")  # listed on XNAS
A = ACME.asset_id


def _venue(per_share: str, latency: float) -> VenueExecution:
    return VenueExecution(
        ExecutionSimulator(
            commission_model=PerShareCommission(D(per_share)),
            latency_model=ConstantLatency(latency),
        )
    )


VENUES = {"BETA": _venue("0.02", 2.0), "ALPHA": _venue("0.01", 1.0)}


class Buys(BaseStrategy):
    """Buys ``quantity`` at the bar stamped 1.0, on each of ``terms``; hears its orders."""

    def __init__(self, *terms: OrderTerms, quantity: str = "10") -> None:
        self._terms = terms or (MARKET,)
        self._quantity = D(quantity)
        self.heard: list[OrderEvent] = []

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.bar.timestamp != 1.0:
            return ()
        return tuple(
            Intent(S, A, self._quantity, timestamp=1.0, terms=terms) for terms in self._terms
        )

    def on_order(self, context: StrategyContext, event: OrderEvent) -> Iterable[Intent]:
        self.heard.append(event)
        return ()


def _bar(at: float, price: str) -> BarClosed:
    p = D(price)
    bar = Bar(A, at, p, p, p, p, D("1000000"), None, None, TimeFrame.D1)
    return BarClosed(event_id=f"B-{at}", timestamp=at, bar=bar)


def _config(venues: Mapping[str, VenueExecution] = VENUES, router: Any = None, **kw: Any) -> Any:
    base = replace(pipeline_config(S), instruments=registry_of(ACME))
    return replace(base, venues=venues, router=router, **kw)


def _step(strategy: Buys, **kw: Any) -> ExecutionPipelineResult:
    state = ExecutionPipeline.initialize(_config(**kw), running_strategy_state(S, strategy), 0.5)
    return ExecutionPipeline.process_market_event(state, _bar(1.0, "100"), context_factory)


def _record(result: ExecutionPipelineResult) -> list[tuple[str, str, str]]:
    return [
        (o.metadata["venue"], o.metadata["venue_by"], o.metadata["venue_at"])
        for o in result.oms_orders
    ]


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def test_the_cheapest_venue_is_chosen_and_its_model_fills_the_order() -> None:
    result = _step(Buys(), router=CheapestVenue())
    ((fill,), (report,)) = (result.fills, result.state.execution.history)
    assert (fill.price, fill.commission, fill.filled_at) == (D("100"), D("0.10"), 2.0)
    assert report.venue == "ALPHA"
    assert _record(result) == [("ALPHA", "router", repr(1.0))]


def test_an_order_naming_a_venue_is_routed_there() -> None:
    result = _step(Buys(OrderTerms(venue="BETA")), router=CheapestVenue())
    (fill,) = result.fills
    assert (fill.commission, fill.filled_at) == (D("0.20"), 3.0)
    assert _record(result) == [("BETA", "terms", repr(1.0))]


def test_a_tie_goes_to_the_first_venue_by_code() -> None:
    twins = {"ZULU": _venue("0.01", 1.0), "YANKEE": _venue("0.01", 1.0)}
    result = _step(Buys(), venues=twins, router=CheapestVenue())
    assert _record(result) == [("YANKEE", "router", repr(1.0))]


def test_without_a_router_the_listing_venue_routes_the_order() -> None:
    listed = {**VENUES, "XNAS": _venue("0.03", 0.0)}
    result = _step(Buys(), venues=listed)
    (fill,) = result.fills
    assert fill.commission == D("0.30")
    assert _record(result) == [("XNAS", "listing", repr(1.0))]


@pytest.mark.parametrize(
    ("terms", "venues", "reason"),
    [
        (OrderTerms(venue="GAMMA"), VENUES, "names venue GAMMA"),
        (MARKET, VENUES, "No declared venue routes the order"),
    ],
    ids=["undeclared", "unroutable"],
)
def test_an_order_no_declared_venue_routes_is_refused(
    terms: OrderTerms, venues: Mapping[str, VenueExecution], reason: str
) -> None:
    strategy = Buys(terms)
    result = _step(strategy, venues=venues)
    assert result.fills == () and result.oms_orders == ()
    assert not result.state.allocation.reservations
    (refused,) = strategy.heard
    assert refused.status == "rejected" and reason in refused.reason


def test_a_run_declaring_no_venue_is_unchanged() -> None:
    result = _step(Buys(), venues={})
    (fill,) = result.fills
    assert (fill.commission, fill.filled_at) == (D("0"), 1.0)
    (order,) = result.oms_orders
    assert "venue" not in order.metadata
    assert result.state.execution.history[0].venue == "SIM"
    payload = deserialize(serialize(capture(result.state)))
    assert payload["schema_version"] == 8
    assert "venues" not in payload["config"] and "router_type" not in payload["config"]


def test_costs_reconcile_per_venue() -> None:
    strategy = Buys(OrderTerms(venue="ALPHA"), OrderTerms(venue="BETA"))
    result = _step(strategy, router=CheapestVenue())
    by_venue = {r.venue: r.commission for r in result.state.execution.history}
    assert by_venue == {"ALPHA": D("0.10"), "BETA": D("0.20")}
    assert sum(f.commission for f in result.fills) == D("0.30")


def test_the_assumptions_describe_each_venue_and_the_router() -> None:
    assumed = execution_assumptions(_config(router=CheapestVenue()), ImmediateFill())
    assert f"; venues={describe(dict(VENUES))}" in assumed.costs
    assert assumed.costs.endswith("; router=CheapestVenue()")
    assert not assumed.frictionless
    plain = execution_assumptions(_config(venues={}), ImmediateFill())
    assert "venues=" not in plain.costs and plain.frictionless


def test_a_router_needs_venues_and_a_venue_needs_a_model() -> None:
    with pytest.raises(RuntimeValidationError, match="declares none"):
        _config(venues={}, router=CheapestVenue())
    with pytest.raises(RuntimeValidationError, match="is a VenueExecution"):
        _config(venues={"ALPHA": ExecutionSimulator()})  # type: ignore[dict-item]


# --------------------------------------------------------------------------- #
# What is recorded
# --------------------------------------------------------------------------- #


def test_a_run_paused_with_an_order_working_resumes_with_its_venues() -> None:
    config = _config(router=CheapestVenue(), fill_timing=FillTiming.NEXT_EVENT)
    state = ExecutionPipeline.initialize(config, running_strategy_state(S, Buys()), 0.5)
    decided = ExecutionPipeline.process_market_event(state, _bar(1.0, "100"), context_factory)
    payload = deserialize(serialize(capture(decided.state)))
    assert payload["schema_version"] == 9
    assert [v["code"] for v in payload["config"]["venues"]] == ["ALPHA", "BETA"]
    assert payload["config"]["router_type"] == "CheapestVenue"

    snapshot = from_primitives(payload)
    strategy = decided.state.strategy.strategies[S].instance

    def objects(**kw: Any) -> RuntimeObjects:
        fields = {
            "sizing_model": config.sizing_model,
            "simulator": config.simulator,
            "strategies": {S: strategy},
            "instruments": config.instruments,
            "venues": VENUES,
            "router": CheapestVenue(),
            **kw,
        }
        return RuntimeObjects(**fields)

    resumed = restore(snapshot, objects())
    following = _bar(2.0, "101")
    expected = ExecutionPipeline.process_market_event(decided.state, following, context_factory)
    again = ExecutionPipeline.process_market_event(resumed, following, context_factory)
    assert (
        [(f.price, f.commission, f.filled_at) for f in again.fills]
        == [(f.price, f.commission, f.filled_at) for f in expected.fills]
        == [(D("101"), D("0.10"), 3.0)]
    )
    with pytest.raises(StateDecodeError, match="venues"):
        restore(snapshot, objects(venues={}))
    with pytest.raises(StateDecodeError, match="simulator of venue ALPHA"):
        restore(snapshot, objects(venues={**VENUES, "ALPHA": _venue("0.05", 1.0)}))
    with pytest.raises(StateDecodeError, match="venue router"):
        restore(snapshot, objects(router=None))


def test_terms_naming_no_venue_keep_their_form() -> None:
    assert "venue" not in deserialize(serialize(MARKET))
    named = OrderTerms(venue="ALPHA")
    payload = deserialize(serialize(named))
    assert payload["venue"] == "ALPHA"
    assert as_order_terms(payload, "terms") == named
    with pytest.raises(AlphaLabValidationError, match="by its code"):
        OrderTerms(venue=" ")


def test_a_crypto_venue_specification_becomes_its_model() -> None:
    specification = VenueSpecification(
        "BINANCE", 8, FeeSchedule(D("-1"), D("4")), PriceSource.MARK, D("5"), "USDT"
    )
    venue = crypto_venue_execution(
        specification,
        spread=NoSpread(),
        slippage=FixedSlippage(D("0")),
        latency=ConstantLatency(0.1),
    )
    simulator = venue.simulator
    assert describe(simulator.costs.commission_model) == describe(PercentageCommission(D("0.0004")))
    assert describe(simulator.maker_commission_model) == describe(
        PercentageCommission(D("-0.0001"))
    )
    assert describe(simulator.latency_model) == describe(ConstantLatency(0.1))
