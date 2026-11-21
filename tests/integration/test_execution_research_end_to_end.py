"""Plan scenario S-09, and GR-4: execution researched across venues (ADR-0060, ADR-0061).

``examples/82_research_execution_across_venues.py`` buys WIDGET through two
declared venues: ALPHA, a cent of half-spread, a fifth of a cent a share and
half a second; BETA, half a cent, half a cent a share and ninety seconds. Every
figure below is calculated by hand from its bars and those costs.

* **Queued children at each venue.** A TWAP of limit children at 50.10, waiting
  their turn in a queue model, fills 2,500 of the 3,000 shares in five fills at
  either venue -- a resting fill pays no spread -- so their commissions are 5.00
  at ALPHA and 12.50 at BETA.
* **The router prices taking the market.** For 3,000 shares ``CheapestVenue``
  finds 15.00 of spread and 15.00 of commission at BETA against 30.00 and 6.00
  at ALPHA, and routes the parent to BETA, where the passive order then pays
  more: the shortfall is higher. A router is only what it prices.
* **Latency priced.** A market order of 1,000 decided at the open's close fills,
  stamped only, at the next bar's close of 50.02 plus BETA's half-spread, 150
  seconds after the open; kept from the market for its ninety seconds, at the
  close of the bar they end within, 50.04 plus the half-spread, at 120 seconds.
* **GR-4.** A thousand shares to each venue in one run: ALPHA's at 50.03 with
  2.00 of commission, BETA's at 50.025 with 5.00, and the run's commission and
  spend their sum.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.backtesting import AlgorithmicQueuedResult
from alphalab.execution.policy import LatencyPricing

D = Decimal
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "82_research_execution_across_venues.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_82", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_each_venue_fills_the_queued_children_at_its_own_commission(example: ModuleType) -> None:
    for name, code, commission in (("alpha", "ALPHA", D("5.00")), ("beta", "BETA", D("12.50"))):
        result = example.queued(name)
        assert isinstance(result, AlgorithmicQueuedResult)
        assert example.routed_to(result) == [(code, "router")]
        (costs,) = example.costs_by_venue(result).values()
        assert (costs.venue, costs.filled, costs.fills, costs.commission) == (
            code,
            D("2500"),
            5,
            commission,
        )
        # Every child fills on its parent's venue, at its limit or better, as a maker.
        reports = result.state.execution.history
        assert {r.venue for r in reports} == {code}
        assert all(r.fill_price <= example.LIMIT for r in reports)
        assert {r.liquidity_flag for r in reports} == {"MAKER"}


def test_the_router_prices_taking_the_market_and_routes_a_passive_order_to_beta(
    example: ModuleType,
) -> None:
    routed, beta, alpha = example.queued("routed"), example.queued("beta"), example.queued("alpha")
    assert example.routed_to(routed) == [("BETA", "router")]
    assert [
        (r.fill_price, r.fill_quantity, r.commission) for r in routed.state.execution.history
    ] == [(r.fill_price, r.fill_quantity, r.commission) for r in beta.state.execution.history]
    # The same fills, a higher commission: the passive order pays more at BETA.
    assert example.shortfall_bps(beta) > example.shortfall_bps(alpha)


@pytest.mark.parametrize(
    ("pricing", "price", "after_open"),
    [
        (LatencyPricing.STAMP, D("50.025"), 150.0),
        (LatencyPricing.POST_LATENCY_EVENT, D("50.045"), 120.0),
    ],
    ids=["stamp", "post-latency-event"],
)
def test_latency_kept_from_the_market_fills_at_a_later_bar(
    example: ModuleType, pricing: LatencyPricing, price: Decimal, after_open: float
) -> None:
    (report,) = example.latency_priced(pricing).state.execution.history
    assert (report.venue, report.fill_price, report.fill_quantity) == ("BETA", price, D("1000"))
    assert report.timestamp - example.OPEN == after_open


def test_gr4_a_two_venue_run_reconciles_venue_by_venue(example: ModuleType) -> None:
    result = example.two_venues()
    venues = example.costs_by_venue(result)
    assert {code: (c.filled, c.average_price, c.commission) for code, c in venues.items()} == {
        "ALPHA": (D("1000"), D("50.0300"), D("2.00")),
        "BETA": (D("1000"), D("50.0250"), D("5.00")),
    }
    assert sorted(example.routed_to(result)) == [("ALPHA", "terms"), ("BETA", "terms")]
    commission = sum((c.commission for c in venues.values()), D("0"))
    spent = sum((c.notional for c in venues.values()), D("0")) + commission
    assert sum((f.commission for f in result.fills), D("0")) == commission == D("7.00")
    starting = result.config.pipeline.starting_cash
    assert starting - result.valuation.cash == spent == D("100062.00")


def test_the_example_runs_end_to_end(
    example: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    example.main()
    printed = capsys.readouterr().out
    assert "plan scenario S-09" in printed
    assert "total commission 7.00: the venues' sum" in printed
