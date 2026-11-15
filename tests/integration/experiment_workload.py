"""The reference experiment: fixed identifiers, one trial of every recorded outcome (ADR-0072).

Ten trials of one scripted strategy over one dataset, its identifiers fixed so
that its ``experiment_id`` and ``experiment_record_id`` can be pinned and held
on every host (plan O.3). Six trials complete; one fails in the runner, one in
the strategy, one is given another dataset and refused, and one goes insolvent.
Imports nothing but AlphaLab and the integration harness, so a bare interpreter
can compute the pins.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
)
from alphalab.backtesting.schedule import EMPTY_SCHEDULE
from alphalab.lifecycle.experiments import (
    ExperimentDefinition,
    ExperimentRecord,
    ExperimentSpec,
    FullSample,
    RunnerRegistry,
    SequentialExecutor,
    TrialInputs,
    TrialLedger,
    TrialLimits,
    TrialSpec,
    dataset_identity,
    run_experiment,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.walk_forward_optimization import ParameterSpace
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.retention import RetentionPolicy
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

STRATEGY_ID = "6f1c2e44-0b7d-4f6a-9c3e-1d2b5a7e8f90"
ASSET_ID = "0a9b8c7d-6e5f-4a3b-8c2d-1e0f9a8b7c6d"
RECORDS = 60
MIDS = [Decimal(100) + Decimal(2 * i) for i in range(RECORDS)]
DATASET = dataset_of_quotes(ASSET_ID, MIDS, dataset_id="REFERENCE")
FOREIGN = dataset_of_quotes(ASSET_ID, MIDS[:-1], dataset_id="ELSEWHERE")
FINGERPRINT = "reference@" + "5e" * 32


@dataclass(frozen=True)
class Plan:
    """The configuration the reference experiment's candidates are typed as."""

    window: int
    size: int
    mode: str


class _Crashing(ScriptedStrategy):
    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.timestamp >= 12.0:
            raise RuntimeError("the strategy's own defect")
        return super().on_quote(context, event)


def _plan(window: int, size: int) -> dict[float, Decimal]:
    return {
        2.0 + i: Decimal(size) if (i // window) % 2 == 0 else -Decimal(size)
        for i in range(0, RECORDS, window)
    }


def build(trial: TrialSpec) -> TrialInputs:
    """The reference runner."""

    plan: Plan = trial.configuration
    if plan.mode == "raise":
        raise ValueError("the runner's own defect")
    config = backtest_config(STRATEGY_ID)
    dataset = DATASET
    strategy: ScriptedStrategy = ScriptedStrategy(
        STRATEGY_ID, ASSET_ID, _plan(plan.window, plan.size)
    )
    if plan.mode == "crash":
        strategy = _Crashing(STRATEGY_ID, ASSET_ID, _plan(plan.window, plan.size))
    elif plan.mode == "foreign":
        dataset = FOREIGN
    elif plan.mode == "squeeze":
        config = backtest_config(STRATEGY_ID, starting_cash=Decimal(1000), compile_analytics=False)
        # Short nine at 100 on 1,000 of capital: insolvent once the price passes 211.
        strategy = ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal(-9)})
    return TrialInputs(
        config=config,
        dataset=dataset,
        strategy_state=running_strategy_state(STRATEGY_ID, strategy),
        context_factory=context_factory,
        schedule=EMPTY_SCHEDULE,
        observations=(),
    )


CANDIDATES = [
    *(
        {"window": window, "size": size, "mode": "normal"}
        for window in (2, 3, 5)
        for size in (1, 2)
    ),
    *({"window": 7, "size": 1, "mode": mode} for mode in ("raise", "crash", "foreign", "squeeze")),
]


def phone(trial: TrialSpec) -> TrialInputs:
    """A runner that reaches for the network while it runs. Only ever run spawned."""

    import socket

    socket.getaddrinfo("localhost", 80)
    return build(trial)


def sleepy(trial: TrialSpec) -> TrialInputs:
    """A runner whose third trial outlasts the slow experiment's time limit."""

    import time

    if trial.candidate == CANDIDATES[2]:
        time.sleep(30)
    return build(trial)


def stagger(trial: TrialSpec) -> TrialInputs:
    """A runner whose earlier trials take longer, so they finish after later ones."""

    import time

    time.sleep(0.4 * (len(CANDIDATES) - CANDIDATES.index(dict(trial.candidate))) / len(CANDIDATES))
    return build(trial)


def dies(trial: TrialSpec) -> TrialInputs:
    """A runner whose worker dies on the second trial. Only ever run spawned."""

    import os

    if trial.candidate == CANDIDATES[1]:
        os._exit(3)
    return build(trial)


def chatty(trial: TrialSpec) -> TrialInputs:
    """A runner that writes to the standard output, through Python and around it."""

    import os
    import sys

    print(f"the trial {trial.label} speaks")
    sys.stdout.flush()
    os.write(1, b"and writes to its file descriptor directly\n")
    return build(trial)


REGISTRY = (
    RunnerRegistry()
    .register("reference", build)
    .register("chatty", chatty)
    .register("phone", phone)
    .register("sleepy", sleepy)
    .register("stagger", stagger)
    .register("dies", dies)
)


def spec(limits: TrialLimits | None = None) -> ExperimentSpec:
    """The reference experiment's specification; ``limits`` for the slow one."""

    config = backtest_config(STRATEGY_ID)
    return ExperimentSpec(
        name="reference",
        hypothesis="one trial of every recorded outcome",
        strategy=FINGERPRINT,
        configuration_type=Plan,
        space=ParameterSpace.of(CANDIDATES),
        dataset=dataset_identity(DATASET),
        schedule=EMPTY_SCHEDULE.binding,
        instruments=None,
        execution=execution_assumptions(config.pipeline, config.fill_policy),
        capture=CaptureSpec(
            ReturnExtractor(
                EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.TRUNCATE
            ),
            trace=True,
        ),
        retention=RetentionPolicy(market_history=24, steps=10, audit_events=64, results=64),
        validation=FullSample(None),
        seed=20261115,
        parent_id=None,
        limits=TrialLimits(None) if limits is None else limits,
    )


def run() -> tuple[ExperimentSpec, ExperimentRecord]:
    """The reference experiment, run sequentially into a fresh ledger."""

    reference = spec()
    ledger = TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))
    return reference, run_experiment(reference, REGISTRY, "reference", ledger, SequentialExecutor())


#: The reference experiment, where a spawned worker loads it.
DEFINITION = ExperimentDefinition(spec(), REGISTRY)

#: The same experiment with a two-second limit a trial may take.
SLOW_DEFINITION = ExperimentDefinition(spec(TrialLimits(2.0)), REGISTRY)
