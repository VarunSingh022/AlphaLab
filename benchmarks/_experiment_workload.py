"""The executor benchmark's experiment, defined where a spawned worker can load it (ADR-0072).

``benchmark_experiments.py`` runs it under each executor. It lives in a module
of its own because a spawned worker imports an experiment's definition by its
module's name, and a script's is ``__main__``. Its identifiers are fixed, so
every worker builds the experiment the parent submitted.

Forty-eight trials -- the size of M4's exit-gate experiment -- of one strategy
over ten years of daily records: on every ``every``-th record it flips between
long and short ``size`` shares, through the backtesting benchmark's engine
workload, whose strategy does no work of its own. A trial of it is short, so
the executor's own costs weigh on it as heavily as they would on any trial.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from benchmark_backtesting import _config, _context_factory, _dataset

from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
)
from alphalab.backtesting.schedule import EMPTY_SCHEDULE
from alphalab.common.registry import RunnerRegistry
from alphalab.lifecycle.experiments import (
    ExperimentDefinition,
    ExperimentSpec,
    FullSample,
    TrialInputs,
    TrialLimits,
    TrialSpec,
    dataset_identity,
)
from alphalab.research.walk_forward_optimization import ParameterSpace
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

STRATEGY_ID = "3d1f0b7e-5c2a-4e9b-8f64-2a7c9e1d0b53"
ASSET_ID = "9b2e6a41-7d3c-4f58-a1e0-6c8d2f4b7a19"
#: Ten years of daily records.
RECORDS = 2_520
DATASET = _dataset(ASSET_ID, RECORDS)


@dataclass(frozen=True)
class Flip:
    """A candidate: flip every ``every`` records, ``size`` shares."""

    every: int
    size: int


class _Flipping(BaseStrategy):
    """Flips between long and short ``size`` shares on every ``every``-th record."""

    def __init__(self, plan: Flip) -> None:
        self._plan = plan

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        index = int(event.quote.timestamp)
        every, size = self._plan.every, Decimal(self._plan.size)
        if index % every:
            return ()
        target = size if (index // every) % 2 == 0 else -size
        return (Intent(STRATEGY_ID, ASSET_ID, target, timestamp=event.quote.timestamp),)


def _running(plan: Flip) -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, _Flipping(plan))
    entry = state.strategies[STRATEGY_ID]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies={STRATEGY_ID: entry})


def build(trial: TrialSpec) -> TrialInputs:
    """The benchmark's runner."""

    return TrialInputs(
        config=_config(STRATEGY_ID),
        dataset=DATASET,
        strategy_state=_running(trial.configuration),
        context_factory=_context_factory,
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


REGISTRY = RunnerRegistry().register("flip", build)

_CONFIG = _config(STRATEGY_ID)
SPEC = ExperimentSpec(
    name="executor-benchmark",
    hypothesis="the executor's own cost is a small share of the trials'",
    strategy="flip@" + "b1" * 32,
    configuration_type=Flip,
    space=ParameterSpace.grid({"every": [1, 2, 3, 4, 6, 8], "size": [1, 2, 3, 4, 5, 6, 7, 8]}),
    dataset=dataset_identity(DATASET),
    schedule=EMPTY_SCHEDULE.binding,
    instruments=None,
    execution=execution_assumptions(_CONFIG.pipeline, _CONFIG.fill_policy),
    capture=CaptureSpec(
        ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE),
        trace=True,
    ),
    retention=RetentionPolicy(market_history=64, steps=64, audit_events=256, results=256),
    validation=FullSample(None),
    seed=4300,
    parent_id=None,
    limits=TrialLimits(None),
)

DEFINITION = ExperimentDefinition(SPEC, REGISTRY)
