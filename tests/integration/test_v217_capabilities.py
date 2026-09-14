"""The v2.17 capabilities driven together, along the paths they actually share.

Three capabilities, and none of them is a package that sits beside the execution
path:

* **Settlement-level multi-currency** -- a run settles fills in more than one
  currency, accrues P&L and commission in the currency each was earned in, and
  reports one figure in one currency with the rates that produced it.
* **The FX rate feed** -- where those rates come from, with ordering,
  deduplication and conflict rules, and with provenance that survives a restart.
* **The strategy-class registry** -- what turns the identity a deployment names
  into the code a run executes, deterministically and with a refusal.

The composition below is the honest one. The feed *feeds* settlement: the table
it produces is the table the pipeline values and settles against, so flow 1
drives them as one path rather than asserting each in isolation. The registry
joins somewhere else entirely -- at the start of a run, between the lifecycle's
record of what should run and the runtime's need for an instance -- which is
flow 2.

A fourth test states what none of them did: no capability moved another's
boundary. That is the same guarantee ``test_v216_capabilities`` makes, and it is
what distinguishes an addition from a rewrite.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.core.enums import AssetType
from alphalab.enterprise.identity import register_principal
from alphalab.enterprise.models import EnterpriseState
from alphalab.enterprise.rbac import define_role, grant_role
from alphalab.experiment_tracking import complete_run, log_metrics, start_run
from alphalab.instrument.record import InstrumentRecord
from alphalab.lifecycle import (
    LIFECYCLE_PERMISSIONS,
    PERMISSION_APPROVE,
    Governance,
    LifecycleState,
    MetricThreshold,
    StrategyVersionRef,
    ValidationMethod,
    ValidationPolicy,
    approve_deployment,
    authorize_run,
    build_evidence,
    deploy_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    run_plan,
)
from alphalab.model_registry import ModelStage, promote
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.fx import FxRate, MissingRateError
from alphalab.portfolio.fx_feed import (
    FxFeed,
    FxFeedOutcome,
    FxQuote,
    SequenceFxSource,
)
from alphalab.portfolio.fx_feed import capture as capture_feed
from alphalab.portfolio.fx_feed import from_primitives as feed_from_primitives
from alphalab.portfolio.fx_feed import restore as restore_feed
from alphalab.portfolio.valuation import PortfolioValuation
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.registry import StrategyClassRegistry, runtime_for
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.studio.strategy import StrategyDefinition
from tests.integration.harness import (
    context_factory,
    dataset_of_quotes,
    pipeline_config,
    quote,
    registry_of,
)

_STRATEGY = "ma-crossover"
_ENVIRONMENT = "live"
_STRATEGY_ID = "ma-001"

_APPLE = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", sector="Technology")
_SAP = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", sector="Technology")

POLICY = ValidationPolicy("prod-v1", (MetricThreshold("sharpe_ratio", 1.0),))
METRICS = {"sharpe_ratio": 1.4, "max_drawdown": 0.1}


# --------------------------------------------------------------------------- #
# The strategy, registered rather than hand-wired
# --------------------------------------------------------------------------- #


class CrossoverStrategy(BaseStrategy):
    """A strategy the registry constructs from a declared parameter set.

    It reads its context, which is what makes flow 2 an end-to-end check rather
    than a construction check: a strategy that never touched its
    ``StrategyContext`` would prove nothing about the run it was placed in.
    """

    def __init__(self, strategy_id: str, parameters: Mapping[str, float]) -> None:
        self.strategy_id = strategy_id
        self.size = Decimal(str(parameters.get("size", 0.0)))
        self.seen_equity: list[Decimal] = []
        self.traded = False

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.seen_equity.append(context.portfolio.cash("USD"))
        if self.traded or self.size == 0:
            return ()
        self.traded = True
        return (Intent(self.strategy_id, event.quote.asset_id, self.size),)


def _registry() -> StrategyClassRegistry:
    return StrategyClassRegistry().register(_STRATEGY_ID, CrossoverStrategy)


def _running(registry: StrategyClassRegistry, *declarations: StrategyDefinition) -> Any:
    """Registry-built strategies, driven to ``RUNNING`` by the supervisor.

    ``runtime_for`` leaves them ``CREATED``, exactly as ``register_strategy``
    does -- transitioning is ``RuntimeSupervisor``'s, and ``configure`` and
    ``subscribe`` take decisions a registry has no input for. Doing it here is
    what a caller does, spelled out rather than hidden in a helper.
    """

    state = runtime_for(registry, declarations)
    started = {}
    for strategy_id, strategy_state in state.strategies.items():
        configured, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"quotes"}), 1.2)
        started[strategy_id], _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies=started)


def _definition(size: float = 5.0) -> StrategyDefinition:
    return StrategyDefinition(_STRATEGY_ID, "MA Crossover", "1", "quant", "desc", {"size": size})


# --------------------------------------------------------------------------- #
# Governance, unchanged from v2.16 -- flow 2 runs through it
# --------------------------------------------------------------------------- #


def _enterprise() -> EnterpriseState:
    state = EnterpriseState()
    state, _ = register_principal(state, "releaser", "Release Engineer", 0.0)
    state, _ = register_principal(state, "approver", "Head of Trading", 0.0)
    state = define_role(state, "release", LIFECYCLE_PERMISSIONS - {PERMISSION_APPROVE})
    state = define_role(state, "approve", {PERMISSION_APPROVE})
    state = grant_role(state, "releaser", "release")
    return grant_role(state, "approver", "approve")


ENTERPRISE = _enterprise()
RELEASER = Governance(ENTERPRISE, "releaser", frozenset({_ENVIRONMENT}))
APPROVER = Governance(ENTERPRISE, "approver")


def _governed_lifecycle(size: float = 5.0) -> tuple[LifecycleState, StrategyVersionRef]:
    state = LifecycleState()
    tracker, run_id = start_run(state.experiments, "sweep", {"size": size}, 1.0)
    tracker = complete_run(log_metrics(tracker, run_id, {"sharpe": 1.4}), run_id, 2.0)
    state = replace(state, experiments=tracker)

    state, model = register_model_version(
        state, "momentum", object(), 3.0, run_id=run_id, metrics={"sharpe": 1.4}
    )
    state = replace(
        state, models=promote(state.models, model.name, model.version, ModelStage.STAGING, 4.0)
    )
    state, ref = register_strategy(
        state, _STRATEGY, _definition(size), 5.0, model=model, run_id=run_id
    )
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(ref), "ds-1", METRICS, 6.0, seed=7, source_id="rep-1"
    )
    state = record_evidence(state, evidence)
    state = promote_strategy_version(
        state, RELEASER, ref.name, ref.version, POLICY, evidence.evidence_id, 7.0
    )
    state = approve_deployment(
        state, APPROVER, ref.name, ref.version, _ENVIRONMENT, 7.5, note="reviewed"
    )
    state, _ = deploy_strategy_version(state, RELEASER, ref.name, ref.version, _ENVIRONMENT, 8.0)
    return state, ref


# --------------------------------------------------------------------------- #
# FLOW 1: feed -> FX state -> multi-currency execution -> settlement ->
#         portfolio accounting -> reporting valuation
# --------------------------------------------------------------------------- #


def test_a_rate_feed_carries_a_run_from_a_quote_to_a_reported_figure() -> None:
    """Every stage is the real one. No table is hand-built past the feed."""

    # --- The feed. Quotes arrive out of order and one is redelivered, which is
    # what a live source does; the fold is what makes that safe.
    source = SequenceFxSource.of(
        "ECB",
        [
            FxRate("EUR", "USD", Decimal("1.08"), 1.0, "ECB"),
            FxRate("USD", "EUR", Decimal("0.925926"), 1.0, "ECB"),
            FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"),
            FxRate("EUR", "USD", Decimal("1.02"), 1.5, "ECB"),  # late, superseded
            FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"),  # redelivered
        ],
    )
    feed, decisions = FxFeed.drain(FxFeed.initialize("ECB-FEED"), source)

    assert [d.outcome for d in decisions] == [
        FxFeedOutcome.APPLIED,
        FxFeedOutcome.APPLIED,
        FxFeedOutcome.APPLIED,
        FxFeedOutcome.SUPERSEDED,
        FxFeedOutcome.DUPLICATE,
    ]
    rates = feed.rates
    assert rates.rate_for("EUR", "USD").rate == Decimal("1.10")  # type: ignore[union-attr]

    # --- A pipeline that settles USD and EUR, funded in both. The EUR is bought
    # with USD at a rate from the feed, and the conversion records it.
    base = pipeline_config(_STRATEGY_ID)
    config = replace(
        base,
        account=Account("acct-217", "USD", "v2.17", 1.0),
        currency="USD",
        also_settles=frozenset({"EUR"}),
        budget=base.budget.in_currency("USD"),
        instruments=registry_of(_APPLE, _SAP),
    )
    state = ExecutionPipeline.initialize(config, _running(_registry(), _definition(10.0)), 1.0)
    state, funding = ExecutionPipeline.convert_cash(
        state, Decimal("11000"), "USD", "EUR", rates, 1.5
    )

    assert funding.rate.source == "ECB", "the feed's provenance reaches settlement"

    # --- Execution, in the instrument's own currency.
    result = ExecutionPipeline.process_quote(
        state, quote(_SAP.asset_id, 2.0, Decimal("100")), context_factory, rates=rates
    )

    assert [report.currency for report in result.execution_reports] == ["EUR"]
    assert not result.settlement_refusals

    # --- Settlement: the book now holds two currencies, natively.
    portfolio = result.state.portfolio
    assert portfolio.settlement_currencies == ("EUR", "USD")
    assert portfolio.positions[_SAP.asset_id].currency == "EUR"
    assert portfolio.commission_paid.currencies == ("EUR",)

    # --- Reporting: one figure, in one currency, saying how it got there.
    valuation = PortfolioValuation.snapshot(portfolio, 3.0, "USD", rates)

    assert valuation.currency == "USD"
    assert valuation.converted and valuation.rate_sources == ("ECB",)
    assert valuation.equity == (valuation.cash + valuation.long_value + valuation.short_value)


def test_the_feed_the_settlement_used_survives_a_restart_with_its_provenance() -> None:
    """A replayed or resumed run values its book with the rates it used, not today's."""

    feed, _ = FxFeed.drain(
        FxFeed.initialize("ECB-FEED", max_age_seconds=3600.0),
        SequenceFxSource.of("ECB", [FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB")]),
    )

    restored = restore_feed(feed_from_primitives(deserialize(serialize(capture_feed(feed)))))

    assert restored == feed
    conversion = restored.rates.convert(Decimal("100.00"), "EUR", "USD", 2.0)
    assert conversion.converted == Decimal("110.00")
    assert conversion.rate.source == "ECB" and conversion.rate.as_of == 2.0


def test_a_missing_rate_stops_the_run_at_the_first_figure_it_cannot_express() -> None:
    """The refusal is the capability, and it fires as early as it possibly can.

    This feed quotes USD/EUR and not EUR/USD. That is enough to *buy* euros --
    the conversion needs only the direction it is going -- and not enough to say
    what the resulting book is worth in USD. The run stops at the first
    operation that must express a mixed book as one figure, which is the risk
    resync inside the funding call itself, and the message names the pair.

    Nothing inverts USD/EUR to stand in for it. ADR-0020's non-goal, kept: a
    real quote has two sides, and ``1/0.925926`` is the mid-market identity
    rather than a rate anyone quoted.
    """

    feed, _ = FxFeed.drain(
        FxFeed.initialize("ECB-FEED"),
        SequenceFxSource.of("ECB", [FxRate("USD", "EUR", Decimal("0.925926"), 1.0, "ECB")]),
    )
    base = pipeline_config(_STRATEGY_ID)
    config = replace(
        base,
        account=Account("acct-217", "USD", "v2.17", 1.0),
        currency="USD",
        also_settles=frozenset({"EUR"}),
        budget=base.budget.in_currency("USD"),
        instruments=registry_of(_APPLE, _SAP),
    )
    state = ExecutionPipeline.initialize(config, _running(_registry(), _definition(10.0)), 1.0)

    with pytest.raises(MissingRateError, match="No rate for EUR/USD"):
        ExecutionPipeline.convert_cash(state, Decimal("11000"), "USD", "EUR", feed.rates, 1.5)

    # The state the caller passed in is untouched: nothing half-converted.
    assert state.portfolio.cash.balance("EUR") == Decimal("0.00")
    assert state.portfolio.settlement_currencies == ("USD",)

    # Quote the missing side and the same run proceeds.
    complete, decision = FxFeed.apply(
        feed, FxQuote(FxRate("EUR", "USD", Decimal("1.08"), 1.0, "ECB"))
    )
    assert decision.outcome is FxFeedOutcome.APPLIED
    funded, conversion = ExecutionPipeline.convert_cash(
        state, Decimal("11000"), "USD", "EUR", complete.rates, 1.5
    )
    assert funded.portfolio.cash.balance("EUR") == conversion.converted


# --------------------------------------------------------------------------- #
# FLOW 2: registry -> lifecycle identity -> governance -> RunEngine ->
#         execution -> StrategyContext -> continuation
# --------------------------------------------------------------------------- #


def test_a_deployed_identity_resolves_to_code_and_runs() -> None:
    """The join ADR-0033 left as "the caller's knowledge"."""

    lifecycle, ref = _governed_lifecycle(size=5.0)
    plan = run_plan(lifecycle, _ENVIRONMENT)

    # --- Governance already decided what should run; the registry says what code
    # that is, and refuses if nothing registered it.
    assert plan.reference == ref
    assert plan.deployed_by == "releaser"
    assert plan.strategy_id == _STRATEGY_ID

    registry = _registry()
    strategy = registry.construct_from(plan.definition)

    assert isinstance(strategy, CrossoverStrategy)
    assert strategy.size == Decimal("5"), "the deployed parameters, not defaults"

    # --- The run is authorized against the same identity before it starts.
    config = RunConfig(
        pipeline=replace(
            pipeline_config(_STRATEGY_ID),
            account=Account("acct-217", "USD", "v2.17", 1.0),
            instruments=registry_of(_APPLE),
        ),
        mode=ExecutionMode.BACKTEST,
        seed=4242,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    authorization = authorize_run(lifecycle, _ENVIRONMENT, ref, _STRATEGY_ID)
    assert authorization.reference == ref

    # --- And it runs, through RunEngine, with the constructed strategy.
    dataset = dataset_of_quotes(_APPLE.asset_id, [Decimal("100"), Decimal("105")])
    run = RunEngine.initialize(config, _running(registry, plan.definition))
    for record in dataset.records:
        run, _ = RunEngine.advance(run, record, context_factory)

    assert run.processed == 2
    assert run.steps[-1].equity > Decimal("0")

    # --- The strategy actually saw its context, which is what makes this a run
    # and not a construction test.
    instance = run.pipeline.strategy.strategies[_STRATEGY_ID].instance
    assert isinstance(instance, CrossoverStrategy)
    assert len(instance.seen_equity) == 2
    assert instance.traded


def test_a_run_serving_a_version_the_ledger_does_not_name_is_still_refused() -> None:
    """The registry resolving an identity does not weaken the authorization."""

    from alphalab.lifecycle.exceptions import LifecycleTransitionError

    lifecycle, ref = _governed_lifecycle()
    other = StrategyVersionRef(ref.name, ref.version + 1)

    with pytest.raises(LifecycleTransitionError, match="would serve"):
        authorize_run(lifecycle, _ENVIRONMENT, other, _STRATEGY_ID)


def test_an_unregistered_deployment_is_refused_rather_than_guessed_at() -> None:
    """The failure a name-guessing resolver would have turned into a wrong run."""

    from alphalab.strategy.registry import UnknownStrategyError

    lifecycle, _ = _governed_lifecycle()
    plan = run_plan(lifecycle, _ENVIRONMENT)

    with pytest.raises(UnknownStrategyError, match=_STRATEGY_ID):
        StrategyClassRegistry().construct_from(plan.definition)


def test_a_restored_run_rebuilds_its_strategy_from_the_same_registry() -> None:
    """Deterministic continuation: the identity is durable, the instance is not."""

    from alphalab.runtime.run_snapshot import RunObjects
    from alphalab.runtime.run_snapshot import capture as capture_run
    from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
    from alphalab.runtime.run_snapshot import restore as restore_run
    from alphalab.runtime.snapshot import RuntimeObjects
    from alphalab.strategy.registry import instances_for

    lifecycle, _ = _governed_lifecycle()
    plan = run_plan(lifecycle, _ENVIRONMENT)
    registry = _registry()

    config = RunConfig(
        pipeline=replace(
            pipeline_config(_STRATEGY_ID),
            account=Account("acct-217", "USD", "v2.17", 1.0),
            instruments=registry_of(_APPLE),
        ),
        mode=ExecutionMode.BACKTEST,
        seed=4242,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    dataset = dataset_of_quotes(_APPLE.asset_id, [Decimal("100")])
    run = RunEngine.initialize(config, _running(registry, plan.definition))
    for record in dataset.records:
        run, _ = RunEngine.advance(run, record, context_factory)

    payload = deserialize(serialize(capture_run(run)))

    # The strategies the restore needs are built from the registry, by identity.
    rebuilt = instances_for(registry, [plan.definition])
    restored = restore_run(
        run_from_primitives(payload),
        RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=config.pipeline.sizing_model,
                simulator=config.pipeline.simulator,
                strategies=rebuilt,
                instruments=config.pipeline.instruments,
            ),
            fill_policy=config.fill_policy,
        ),
    )

    assert restored.processed == run.processed
    assert type(restored.pipeline.strategy.strategies[_STRATEGY_ID].instance) is CrossoverStrategy


# --------------------------------------------------------------------------- #
# No capability moved another's boundary
# --------------------------------------------------------------------------- #


def test_no_capability_moved_another_ones_boundary() -> None:
    """What distinguishes three additions from a rewrite.

    Each clause is the thing that release could most plausibly have broken.
    """

    import ast
    import inspect
    from dataclasses import fields

    from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig, ExecutionPipelineState
    from alphalab.runtime.run import RunState
    from alphalab.runtime.snapshot import PIPELINE_SNAPSHOT_SCHEMA
    from alphalab.strategy import registry as registry_module

    # --- The registry added no field to any run or pipeline state, and no
    # dependency on anything above alphalab.strategy.
    assert len({f.name for f in fields(RunState)}) == 8, "ADR-0030 decision 2"
    assert len(fields(ExecutionPipelineState)) == 16, "ADR-0030's performance budget"
    assert "registry" not in {f.name for f in fields(ExecutionPipelineState)}
    registry_imports = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(registry_module)))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    reached = {m for m in registry_imports if m.startswith("alphalab")}
    assert all(m.startswith(("alphalab.common", "alphalab.strategy")) for m in reached), reached

    # --- FX rates are still not run configuration: a quote is time-varying data.
    config_fields = {f.name for f in fields(ExecutionPipelineConfig)}
    assert "rates" not in config_fields and "fx" not in config_fields
    assert "rates" not in {f.name for f in fields(ExecutionPipelineState)}

    # --- Multi-currency moved exactly one pipeline schema, and only its own.
    assert PIPELINE_SNAPSHOT_SCHEMA == 3
    from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
    from alphalab.oms.snapshot import OMS_SNAPSHOT_SCHEMA
    from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA

    assert (OMS_SNAPSHOT_SCHEMA, RUN_SNAPSHOT_SCHEMA, DEFAULT_SCHEMA_VERSION) == (1, 1, 1)

    # --- And the feed took no dependency on the execution path.
    from alphalab.portfolio import fx_feed

    feed_imports = {
        node.module
        for node in ast.walk(ast.parse(inspect.getsource(fx_feed)))
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("alphalab")
    }
    for module in feed_imports:
        assert module.startswith(
            ("alphalab.common", "alphalab.persistence", "alphalab.portfolio")
        ), module


def test_each_capability_refuses_on_its_own_terms() -> None:
    """Three refusals, three different questions, three different errors."""

    from alphalab.portfolio.exceptions import MixedCurrencyValuationError
    from alphalab.portfolio.fx_feed import ConflictingQuoteError
    from alphalab.strategy.registry import UnknownStrategyError

    # The feed refuses two answers to one question.
    feed, _ = FxFeed.apply(
        FxFeed.initialize("F"), FxQuote(FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"))
    )
    with pytest.raises(ConflictingQuoteError):
        FxFeed.apply(feed, FxQuote(FxRate("EUR", "USD", Decimal("1.20"), 2.0, "Reuters")))

    # Settlement refuses a figure it cannot express.
    from alphalab.portfolio.amounts import CurrencyAmounts

    with pytest.raises(MixedCurrencyValuationError):
        CurrencyAmounts().add(Decimal("1"), "EUR").add(Decimal("1"), "JPY").total_in("USD")

    # The registry refuses an identity nothing registered.
    with pytest.raises(UnknownStrategyError):
        StrategyClassRegistry().construct("nothing")
