"""The conservative choice, stated in every execution role by one builder (ADR-0060 section 6).

``conservative_execution`` takes the costs, the participation rate and the
latency a researcher states -- each required -- and returns the configuration
and fill policy that assume nothing generous: ``NEXT_OPEN`` fills,
``POST_LATENCY_EVENT`` latency and a strict liquidity cap. It refuses a cost
model that charges nothing, and changes no default.

The run below is calculated by hand: decided at the first daily bar's close of
100, ten shares fill at the second bar's open of 103 -- a gap included -- and a
cent a share in commission, so the purchase costs 1,030.10. The second bar shows
a volume of 1,000, so a cap of half fills all ten.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from alphalab.common.order_terms import MARKET
from alphalab.core.fill import Fill as CoreFill
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.costs import (
    FREE,
    ExecutionCostModel,
    NoFee,
    NoImpact,
    NoSlippage,
    NoSpread,
    NoTax,
)
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.latency import ConstantLatency
from alphalab.execution.policy import (
    FillTiming,
    LatencyPricing,
    StrictLiquidityCappedFill,
)
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.events import BarClosed
from alphalab.runtime import conservative_execution, execution_assumptions
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import context_factory, pipeline_config, running_strategy_state

D = Decimal
STRATEGY_ID = str(UUID(int=0x301))
ASSET = str(UUID(int=0x302, version=4))
CENT_A_SHARE = ExecutionCostModel(
    spread_model=NoSpread(),
    slippage_model=NoSlippage(),
    impact_model=NoImpact(),
    commission_model=PerShareCommission(D("0.01")),
    fee_model=NoFee(),
    tax_model=NoTax(),
)
SECOND = ConstantLatency(1.0)


def _conservative(rate: str = "0.5") -> Any:
    return conservative_execution(
        pipeline_config(STRATEGY_ID), costs=CENT_A_SHARE, participation_rate=D(rate), latency=SECOND
    )


def test_every_role_states_the_conservative_choice() -> None:
    base = pipeline_config(STRATEGY_ID)
    config, policy = conservative_execution(
        base, costs=CENT_A_SHARE, participation_rate=D("0.5"), latency=SECOND
    )
    assert config.fill_timing is FillTiming.NEXT_OPEN
    assert config.latency_pricing is LatencyPricing.POST_LATENCY_EVENT
    assert config.simulator.cost_model is CENT_A_SHARE
    assert config.simulator.latency_model is SECOND
    assert policy == StrictLiquidityCappedFill(D("0.5"))
    # Everything else the configuration states is kept, and nothing is changed in place.
    assert (
        replace(
            config,
            simulator=base.simulator,
            fill_timing=base.fill_timing,
            latency_pricing=base.latency_pricing,
        )
        == base
    )
    assert base.fill_timing is FillTiming.SAME_EVENT
    assert base.latency_pricing is LatencyPricing.STAMP


def test_its_assumptions_name_no_optimism_and_say_what_it_chose() -> None:
    config, policy = _conservative()
    assumed = execution_assumptions(config, policy)
    assert assumed.optimistic == ()
    assert assumed.fill_timing is FillTiming.NEXT_OPEN
    assert assumed.latency.endswith("; pricing=post_latency_event")
    assert assumed.fill_policy.startswith("StrictLiquidityCappedFill(")
    assert not assumed.frictionless and not assumed.unlimited_liquidity


def test_a_cost_model_that_charges_nothing_is_refused() -> None:
    with pytest.raises(RuntimeValidationError, match="charges nothing"):
        conservative_execution(
            pipeline_config(STRATEGY_ID), costs=FREE, participation_rate=D("0.5"), latency=SECOND
        )


@pytest.mark.parametrize("rate", ["0", "1.5", "-0.1"])
def test_a_participation_rate_outside_zero_to_one_is_refused(rate: str) -> None:
    with pytest.raises(ExecutionValidationError, match="participation rate"):
        _conservative(rate)


class _Buys(BaseStrategy):
    """Buys ten at the bar stamped ``at``."""

    def __init__(self, plan: Mapping[float, Decimal]) -> None:
        self._plan = dict(plan)

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        delta = self._plan.get(event.bar.timestamp)
        if delta is None:
            return ()
        return (Intent(STRATEGY_ID, ASSET, delta, timestamp=event.bar.timestamp, terms=MARKET),)


D1 = 1_767_657_600.0
D2 = D1 + 86_400.0


def _bar(at: float, open_: str, close: str, volume: str) -> BarClosed:
    o, c = D(open_), D(close)
    bar = Bar(ASSET, at, o, max(o, c), min(o, c), c, D(volume), None, None, TimeFrame.D1)
    return BarClosed(event_id=f"B-{at}", timestamp=at, bar=bar)


def test_a_run_under_it_fills_at_the_next_open_paying_its_costs() -> None:
    config, policy = _conservative()
    state = ExecutionPipeline.initialize(
        config, running_strategy_state(STRATEGY_ID, _Buys({D1: D("10")})), 1.0
    )
    cash = state.portfolio.cash.balance(config.currency)
    fills: list[CoreFill] = []
    for event in (_bar(D1, "98", "100", "1000"), _bar(D2, "103", "105", "1000")):
        result = ExecutionPipeline.process_market_event(
            state, event, context_factory, fill_policy=policy
        )
        fills.extend(result.fills)
        state = result.state
    (fill,) = fills
    assert (fill.quantity, fill.price, fill.filled_at) == (D("10"), D("103"), D2)
    assert fill.commission == D("0.10")
    assert cash - state.portfolio.cash.balance(config.currency) == D("1030.10")
