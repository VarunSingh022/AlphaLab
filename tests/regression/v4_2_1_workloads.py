"""The workloads behind the v4.2.1 golden payloads, defined once (plan WS-01, GAP-07).

``docs/audit/scripts/generate_v4_2_1_snapshot_fixtures.py`` runs each workload
here against an export of the v4.2.1 tag, pauses it, and freezes the payload --
with the ``result_id`` of the same workload run uninterrupted -- under
``tests/fixtures/snapshots/v4.2.1``. ``test_schema_upgrades_v4_2_1.py`` gives
each frozen payload back to the same workload, resumes it with this build and
holds the result to that ``result_id``. One definition serves both, so a payload
is restored with exactly the objects, dataset, schedule, plan or queue model it
was written with.

Every workload is one the repository already runs: the replay workload v4.1
froze; examples 74 to 79, which show v4.2's execution plans, an option's life, a
financed book, a recall through a halt, a prorated tender and a reopening
auction; and the integration workloads of the queue model (FUT-011), delivered
volatility surfaces (FUT-039), delivered auctions (FUT-043) and point-in-time
observations (FUT-003). A change to one of them changes what it replays, and the
restore then refuses the payload, naming the dataset, schedule or object that
differs. The answer is to keep the v4.2.1 workload, never to regenerate a
fixture.

The examples are found beside the ``alphalab`` being run, so the generator, run
against a tag's export, reads that tag's examples.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

import alphalab
from alphalab.backtesting import (
    EMPTY_SCHEDULE,
    BacktestResult,
    ExecutionPlan,
    MarketDataset,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.engine import finalize
from alphalab.backtesting.replay_snapshot import ReplaySnapshot, restore
from alphalab.common.ids import id_scope
from alphalab.execution.policy import FillTiming
from alphalab.runtime.run import RunConfig, RunState
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

#: The source tree of the ``alphalab`` being run: this repository, or a tag's export.
SOURCE = Path(alphalab.__file__).resolve().parents[1]


def example(number: int) -> ModuleType:
    """Example ``number`` of :data:`SOURCE`, loaded once under a name of its own."""

    name = f"v4_2_1_example_{number}"
    if name in sys.modules:
        return sys.modules[name]
    (path,) = (SOURCE / "examples").glob(f"{number}_*.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _instances(state: StrategyRuntimeState) -> dict[str, Any]:
    return {strategy_id: entry.instance for strategy_id, entry in state.strategies.items()}


@dataclass(frozen=True)
class Inputs:
    """What a replay is started with, and what its restore is given back.

    ``strategies`` and ``instances`` build fresh strategy objects on every call,
    as a host restoring a payload in another process would.
    """

    config: RunConfig
    dataset: MarketDataset
    strategies: Callable[[], StrategyRuntimeState]
    contexts: Any
    instances: Callable[[], Mapping[str, Any]]
    schedule: RunSchedule = EMPTY_SCHEDULE
    execution: ExecutionPlan | None = None
    queue: Any = None

    def objects(self) -> RunObjects:
        pipeline = self.config.pipeline
        return RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=pipeline.sizing_model,
                simulator=pipeline.simulator,
                strategies=dict(self.instances()),
                instruments=pipeline.instruments,
            ),
            fill_policy=self.config.fill_policy,
        )

    def paused(self, after: int) -> PausedReplay:
        """The replay, paused once it has taken ``after`` records."""

        paused = ReplayBacktest.run(
            self.config,
            self.dataset,
            self.strategies(),
            self.contexts,
            pause_when=lambda progress: bool(progress.records_replayed == after),
            schedule=self.schedule,
            execution=self.execution,
            queue=self.queue,
        )
        if not isinstance(paused, PausedReplay):
            raise AssertionError(f"the replay finished before it had taken {after} records")
        return paused

    def uninterrupted(self) -> BacktestResult:
        return ReplayBacktest.run(
            self.config,
            self.dataset,
            self.strategies(),
            self.contexts,
            schedule=self.schedule,
            execution=self.execution,
            queue=self.queue,
        ).backtest

    def restore(self, snapshot: ReplaySnapshot) -> PausedReplay:
        return restore(
            snapshot, self.objects(), self.dataset, self.schedule, self.execution, self.queue
        )

    def resumed(self, snapshot: ReplaySnapshot) -> BacktestResult:
        """``snapshot`` restored with freshly built objects and replayed to its end."""

        return ReplayBacktest.resume(self.restore(snapshot), self.contexts).backtest


def _replay_workload() -> Inputs:
    from tests.integration import replay_workload as workload
    from tests.integration.harness import context_factory

    return Inputs(
        workload.config(),
        workload.dataset(),
        workload.strategy_state,
        context_factory,
        lambda: {workload.STRATEGY_ID: workload.RememberingStrategy()},
    )


def _execution_plan() -> Inputs:
    ex = example(74)
    return Inputs(
        ex.run_config(),
        ex.dataset(),
        ex.running,
        ex.CONTEXTS,
        lambda: {ex.STRATEGY_ID: ex.Buyer()},
        execution=ex.plan(ex.CHOICES["twap"]),
    )


def _queue_model() -> Inputs:
    from tests.integration import test_queue_model as queue
    from tests.integration.harness import context_factory

    return Inputs(
        queue.config(),
        queue.dataset(queue.DEPLETION),
        queue.strategies,
        context_factory,
        lambda: {"S": queue.Poster()},
        queue=queue.QUEUE,
    )


def _option_life() -> Inputs:
    ex = example(75)
    return Inputs(
        ex.run_config(),
        ex.dataset(),
        ex.running,
        ex.CONTEXTS,
        lambda: {ex.STRATEGY_ID: ex.Holder()},
        schedule=ex.schedule(),
    )


def _financed_book() -> Inputs:
    ex = example(76)
    return Inputs(
        ex.config(),
        ex.data(),
        ex.running,
        ex.CONTEXTS,
        lambda: {ex.STRATEGY_ID: ex.PairTrader()},
        schedule=ex.schedule(),
    )


def _recalled_short() -> Inputs:
    ex = example(77)
    return Inputs(
        ex.config(),
        ex.data(),
        ex.running,
        ex.CONTEXTS,
        lambda: {ex.STRATEGY_ID: ex.Short()},
        schedule=ex.schedule(),
    )


def _prorated_tender() -> Inputs:
    ex = example(78)
    return Inputs(
        ex.config(),
        ex.data(),
        ex.running,
        ex.CONTEXTS,
        lambda: _instances(ex.running()),
        schedule=ex.schedule(),
    )


def _reopening_auction() -> Inputs:
    ex = example(79)
    return Inputs(
        ex.config(),
        ex.data(),
        ex.running,
        ex.CONTEXTS,
        lambda: _instances(ex.running()),
        schedule=RunSchedule.of(),
    )


def _delivered_surfaces() -> Inputs:
    from tests.integration import test_surface_feeds as feeds
    from tests.integration.harness import context_factory, running_strategy_state

    schedule = RunSchedule.of(
        surfaces=[
            feeds.delivered("0.30", feeds.T0, feeds.T0 + 20, "v1"),
            feeds.delivered("0.40", feeds.T0, feeds.T0 + 90, "v2"),
        ]
    )
    return Inputs(
        feeds.config(feeds.model(surface_max_age=600.0)),
        feeds.dataset(),
        lambda: running_strategy_state("S", feeds.Watcher()),
        context_factory,
        lambda: {"S": feeds.Watcher()},
        schedule=schedule,
    )


def _delivered_auctions() -> Inputs:
    from tests.integration import test_live_auctions as live
    from tests.integration import test_published_auctions as auctions
    from tests.integration import test_reopening_auctions as reopening
    from tests.integration import test_trading_controls as controls
    from tests.integration.harness import context_factory, running_strategy_state

    schedule = RunSchedule.of(
        auctions=(
            live.delivered(live.BOOK, 2.65, "b1"),
            live.delivered(
                live.indication(2.7, price="101", matched="20", imbalance="10"), 2.75, "i1"
            ),
            live.delivered(live.LATER_BOOK, 3.5, "b2"),
        )
    )
    base = controls.config(controls.ACME, controls.PEER, controls=auctions.computed())
    config = replace(base, pipeline=replace(base.pipeline, fill_timing=FillTiming.NEXT_EVENT))
    return Inputs(
        config,
        reopening.bars(auctions.BARS_AFTER),
        lambda: running_strategy_state("S", live.Feedback(follow=True)),
        context_factory,
        lambda: {"S": live.Feedback(follow=True)},
        schedule=schedule,
    )


def _observations() -> Inputs:
    from tests.integration import replay_workload as workload
    from tests.integration.harness import context_factory
    from tests.regression import test_heterogeneous_inputs_on_every_driver as informed

    return Inputs(
        workload.config(),
        workload.dataset(),
        informed.strategies,
        context_factory,
        lambda: {workload.STRATEGY_ID: informed.InformedStrategy()},
        schedule=RunSchedule.of(observations=informed.schedule()),
    )


@dataclass(frozen=True)
class FrozenReplay:
    """One frozen paused replay: its file, its workload and where it paused.

    Attributes:
        fixture: The payload's file under the release's fixture directory.
        registry: The file of the instrument registry the replay held, or
            ``None`` for a replay that held none.
        origin: Where the workload is defined, relative to the source tree.
        paused_after: How many records the replay had taken when it paused.
        inputs: Builds the workload's inputs.
        shows: What the payload holds that no earlier frozen payload does.
    """

    fixture: str
    registry: str | None
    origin: str
    paused_after: int
    inputs: Callable[[], Inputs]
    shows: str


FROZEN_REPLAYS: tuple[FrozenReplay, ...] = (
    FrozenReplay(
        "replay_paused.json",
        None,
        "tests/integration/replay_workload.py",
        11,
        _replay_workload,
        "the workload v4.1.0 froze at replay schema 1, written at schema 3",
    ),
    FrozenReplay(
        "replay_execution_plan.json",
        "instrument_execution_plan.json",
        "examples/74_evaluate_an_execution_algorithm.py",
        7,
        _execution_plan,
        "an algorithm book: a TWAP parent with children working (FUT-009)",
    ),
    FrozenReplay(
        "replay_queue.json",
        "instrument_queue.json",
        "tests/integration/test_queue_model.py",
        2,
        _queue_model,
        "a queue book: a resting limit order and the quantity ahead of it (FUT-011)",
    ),
    FrozenReplay(
        "replay_option_life.json",
        "instrument_option_life.json",
        "examples/75_exercise_assign_and_deliver.py",
        40,
        _option_life,
        "options with contract terms and settlement, a schedule of exercises and expiries",
    ),
    FrozenReplay(
        "replay_financed_book.json",
        "instrument_financed_book.json",
        "examples/76_a_financed_long_short_book.py",
        6,
        _financed_book,
        "a margin model, financing, borrow fees and a holding exchange (FUT-017 to FUT-021)",
    ),
    FrozenReplay(
        "replay_recalled_short.json",
        "instrument_recalled_short.json",
        "examples/77_a_short_squeezed_by_its_lender.py",
        5,
        _recalled_short,
        "a recall and its buy-in resting through a halt, daily accruals (FUT-022 to FUT-029)",
    ),
    FrozenReplay(
        "replay_prorated_tender.json",
        "instrument_prorated_tender.json",
        "examples/78_a_tender_split_between_holders.py",
        10,
        _prorated_tender,
        "elections, a record instant, a paid exchange and its amendment (FUT-030, FUT-034)",
    ),
    FrozenReplay(
        "replay_reopening_auction.json",
        "instrument_reopening_auction.json",
        "examples/79_bid_into_a_reopening_auction.py",
        6,
        _reopening_auction,
        "declared auction books and indications, and a held reopening (FUT-027, FUT-040)",
    ),
    FrozenReplay(
        "replay_delivered_surfaces.json",
        "instrument_delivered_surfaces.json",
        "tests/integration/test_surface_feeds.py",
        3,
        _delivered_surfaces,
        "volatility surfaces delivered as data and kept as they arrived (FUT-039)",
    ),
    FrozenReplay(
        "replay_delivered_auctions.json",
        "instrument_delivered_auctions.json",
        "tests/integration/test_live_auctions.py",
        4,
        _delivered_auctions,
        "auction books and an indication delivered as data, and a reopening (FUT-043)",
    ),
    FrozenReplay(
        "replay_observations.json",
        None,
        "tests/regression/test_heterogeneous_inputs_on_every_driver.py",
        4,
        _observations,
        "point-in-time observations delivered by the schedule (FUT-003)",
    ),
)

#: The records of the replay workload after which the checkpoint chain's links were taken.
CHAIN_TAKEN = (6, 12, 18)


def chain() -> tuple[list[str], str, BacktestResult]:
    """The replay workload run as a session, checkpointed after each of :data:`CHAIN_TAKEN`.

    Returns the chain's payloads, the full capture of the state its last link
    reaches, and the finished run. Each checkpoint is taken as its record is
    processed: a state held until later shares its live strategy object with
    every later state, and would record that object's later memory.
    """

    from alphalab.persistence import serialize
    from alphalab.runtime.checkpoint import CheckpointMark, checkpoint
    from alphalab.runtime.run_snapshot import capture
    from tests.integration import replay_workload as workload
    from tests.integration.harness import context_factory

    config = workload.config()
    links: list[str] = []
    full = ""
    mark: CheckpointMark | None = None
    with id_scope(config.seed):
        state: RunState = TradingSession.initialize(config, workload.strategy_state())
        for taken, record in enumerate(workload.dataset().records, start=1):
            state, _ = TradingSession.advance(state, record, context_factory)
            if taken in CHAIN_TAKEN:
                payload, mark = checkpoint(state, mark)
                links.append(payload)
                full = serialize(capture(state))
        # Finished inside the run's scope: compiling the analytics mints an identifier.
        return links, full, finalize(state)


def chain_objects() -> RunObjects:
    from tests.integration import replay_workload as workload

    return workload.objects()


def chain_continued(state: RunState) -> BacktestResult:
    """``state``, restored from the chain, run as a session to the workload's end.

    The run is finished inside the scope it resumed in, as :func:`chain`
    finishes inside the scope it started in: finishing compiles the run's
    analytics, and what that mints comes from the run's own identifier stream.
    """

    from tests.integration import replay_workload as workload
    from tests.integration.harness import context_factory

    with TradingSession.resume(state):
        for record in workload.dataset().records[CHAIN_TAKEN[-1] :]:
            state, _ = TradingSession.advance(state, record, context_factory)
        return finalize(state)


def evidence() -> tuple[Any, Any]:
    """A reproducibility manifest and a strategy fingerprint, as the lifecycle tests build them."""

    from alphalab.lifecycle import manifest_for_run
    from tests.unit.lifecycle.evidence_harness import (
        BUILD,
        ENGINE,
        fingerprint,
        ingest,
        run_backtest,
    )

    dataset = ingest()
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE, build=BUILD)
    return manifest, fingerprint()
