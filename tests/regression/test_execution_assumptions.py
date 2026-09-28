"""A simulated result states how execution was modelled (ledger EXE-002).

Until v3.10 a default backtest was the most optimistic simulation there is --
every order filled at the price that decided it, in full, at no cost -- and its
result said nothing about it. The defaults are unchanged; what changed is that a
result carries :class:`~alphalab.runtime.assumptions.ExecutionAssumptions`,
derived from the configuration that ran, naming each optimistic assumption in
force, and able to enter a strategy fingerprint as research settings.
"""

import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

from alphalab.backtesting import BacktestEngine, MarketDataset
from alphalab.execution import (
    FREE,
    ExecutionSimulator,
    FillTiming,
    LiquidityCappedFill,
    PercentageCommission,
)
from alphalab.lifecycle.fingerprint import research_configuration
from alphalab.runtime.assumptions import describe
from alphalab.runtime.run import ExecutionMode
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(UUID(int=0xE0E2))
ASSET = str(UUID(int=0xC, version=4))


def test_a_default_backtest_names_its_three_optimistic_assumptions() -> None:
    assumptions = backtest_config(STRATEGY_ID).execution_assumptions

    assert assumptions.simulated
    assert assumptions.fill_timing is FillTiming.SAME_EVENT
    assert assumptions.frictionless and assumptions.unlimited_liquidity
    assert len(assumptions.optimistic) == 3
    assert any("SAME_EVENT" in line for line in assumptions.optimistic)


def test_a_configured_backtest_has_none() -> None:
    config = backtest_config(
        STRATEGY_ID,
        simulator=ExecutionSimulator(commission_model=PercentageCommission(Decimal("0.001"))),
        fill_policy=LiquidityCappedFill(Decimal("0.1")),
    )
    config = replace(config, pipeline=replace(config.pipeline, fill_timing=FillTiming.NEXT_EVENT))

    assumptions = config.execution_assumptions

    assert assumptions.optimistic == ()
    assert assumptions.costs.count("PercentageCommission(rate=0.001)") == 1
    assert assumptions.fill_policy == "LiquidityCappedFill(participation_rate=0.1)"


def test_the_named_frictionless_model_is_recognised_as_the_default_is() -> None:
    assert ExecutionSimulator().is_frictionless
    assert ExecutionSimulator(cost_model=FREE).is_frictionless
    assert not ExecutionSimulator(
        commission_model=PercentageCommission(Decimal("0.001"))
    ).is_frictionless


def test_a_run_routed_elsewhere_simulated_nothing_and_claims_nothing() -> None:
    config = replace(backtest_config(STRATEGY_ID), mode=ExecutionMode.LIVE)

    assumptions = config.execution_assumptions

    assert not assumptions.simulated
    assert assumptions.optimistic == ()


def test_the_result_carries_the_assumptions_of_the_run_that_produced_it() -> None:
    config = backtest_config(STRATEGY_ID)
    dataset = MarketDataset.of(
        "DS-EA",
        [sized_quote(ASSET, 2.0 + step, Decimal("100"), Decimal("10")) for step in range(3)],
    )
    result = BacktestEngine.run(
        config,
        dataset,
        running_strategy_state(
            STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET, {2.0: Decimal("1")})
        ),
        context_factory,
    )

    assert result.execution_assumptions == config.execution_assumptions


def test_the_assumptions_enter_a_fingerprint_as_settings() -> None:
    free = backtest_config(STRATEGY_ID).execution_assumptions
    costed = backtest_config(
        STRATEGY_ID,
        simulator=ExecutionSimulator(commission_model=PercentageCommission(Decimal("0.001"))),
    ).execution_assumptions

    assert set(free.settings()) == {
        "execution.routing",
        "execution.fill_timing",
        "execution.fill_policy",
        "execution.costs",
        "execution.latency",
    }
    assert research_configuration({"validation": "walk-forward", **free.settings()}) != (
        research_configuration({"validation": "walk-forward", **costed.settings()})
    )


def test_a_description_is_the_same_in_every_process() -> None:
    """No memory address, no hash seed: the text is a function of the configuration."""

    script = (
        "from decimal import Decimal\n"
        "from alphalab.execution import ExecutionSimulator, PercentageCommission\n"
        "from alphalab.runtime.assumptions import describe\n"
        "print(describe(ExecutionSimulator(commission_model="
        "PercentageCommission(Decimal('0.001'))).costs))\n"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        ).stdout
        for _ in range(2)
    }

    assert len(outputs) == 1
    assert outputs.pop().strip() == describe(
        ExecutionSimulator(commission_model=PercentageCommission(Decimal("0.001"))).costs
    )
