"""One path from a deployment decision to a fill at a venue.

This is the integration v2.16's first capability exists to make real. Before it,
three things were separately true and never joined:

* ``alphalab.lifecycle`` recorded which strategy version an environment should
  be running, and its own docstring ended "**and the two are joined by the
  caller**";
* ``alphalab.runtime.session`` read market records and, in its own words,
  "produces working orders and stops";
* ``alphalab.broker`` could reach a venue, and nothing drove it as a run
  proceeded.

So a run could execute a version nobody promoted, and a promoted version could
sit deployed while something else traded. The test below walks the whole chain
in one go -- experiment, model version, strategy version, evidence, promotion,
approval, deployment, authorization, live run, venue fill, portfolio -- and then
asserts the joins that make it a chain rather than a sequence of coincidences.

Everything below the authorization runs over a **real socket** into the HTTP
venue in ``tests/integration/venue_server.py``, which verifies the HMAC
signature, the timestamp window and the idempotency key.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker.account import BrokerAccount
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.transport import HttpVenueTransport, VenueCredentials
from alphalab.broker.venue import RestVenueBroker, VenueConfig
from alphalab.enterprise.identity import register_principal
from alphalab.enterprise.models import EnterpriseState
from alphalab.enterprise.rbac import define_role, grant_role
from alphalab.experiment_tracking import complete_run, log_metrics, start_run
from alphalab.lifecycle import (
    LIFECYCLE_PERMISSIONS,
    PERMISSION_APPROVE,
    Governance,
    LifecycleState,
    LifecycleTransitionError,
    MetricThreshold,
    StrategyVersionRef,
    ValidationMethod,
    ValidationPolicy,
    approve_deployment,
    authorize_run,
    build_evidence,
    deploy_strategy_version,
    governance_log,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    rollback_environment,
    run_plan,
)
from alphalab.model_registry import ModelStage, promote
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.live import LiveSession, live_health
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.studio.strategy import StrategyDefinition
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    dataset_of_quotes,
    pipeline_config,
    running_strategy_state,
)
from tests.integration.venue_server import fill, record_fill, run_venue

_KEY = "TESTKEY-0001"
_SECRET = "test-signing-secret-not-a-real-credential"
_SYMBOL = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
_STRATEGY = "ma-crossover"
_ENVIRONMENT = "live"

POLICY = ValidationPolicy("prod-v1", (MetricThreshold("sharpe_ratio", 1.0),))
METRICS = {"sharpe_ratio": 1.4, "max_drawdown": 0.1}


# --------------------------------------------------------------------------- #
# Principals: a release engineer who may deploy, a head of trading who approves
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
#: Deploying to ``live`` requires an approval from someone other than the deployer.
RELEASER = Governance(ENTERPRISE, "releaser", frozenset({_ENVIRONMENT}))
APPROVER = Governance(ENTERPRISE, "approver")


# --------------------------------------------------------------------------- #
# The lifecycle, up to a governed deployment
# --------------------------------------------------------------------------- #


def _definition(version: str, fast: float) -> StrategyDefinition:
    return StrategyDefinition(
        f"ma-{version}", "MA Crossover", version, "quant", "desc", {"fast": fast}
    )


def _registered(
    state: LifecycleState, fast: float, at: float
) -> tuple[LifecycleState, StrategyVersionRef]:
    tracker, run_id = start_run(state.experiments, "sweep", {"fast": fast}, at)
    tracker = complete_run(log_metrics(tracker, run_id, {"sharpe": 1.4}), run_id, at + 1)
    state = replace(state, experiments=tracker)

    state, model = register_model_version(
        state, "momentum", object(), at + 2, run_id=run_id, metrics={"sharpe": 1.4}
    )
    state = replace(
        state,
        models=promote(state.models, model.name, model.version, ModelStage.STAGING, at + 3),
    )
    return register_strategy(
        state,
        _STRATEGY,
        _definition(str(int(fast)), fast),
        at + 4,
        model=model,
        run_id=run_id,
    )


def _deployed() -> tuple[LifecycleState, StrategyVersionRef]:
    """A governed lifecycle with one version live in ``live``."""

    state, ref = _registered(LifecycleState(), 5.0, 1.0)
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
# The venue side
# --------------------------------------------------------------------------- #


def _broker(base_url: str) -> RestVenueBroker:
    return RestVenueBroker(
        HttpVenueTransport(base_url, VenueCredentials(_KEY, _SECRET)),
        VenueConfig(broker_name="TESTVENUE", account_id="ACC-LIVE"),
    )


def _broker_state() -> BrokerState:
    return BrokerState(
        broker_name="TESTVENUE",
        connection_status=ConnectionStatus.DISCONNECTED,
        account=BrokerAccount(
            account_id="ACC-LIVE",
            cash=Decimal("1000000"),
            equity=Decimal("1000000"),
            buying_power=Decimal("1000000"),
            margin=Decimal("0"),
            available_funds=Decimal("1000000"),
            currency="USD",
        ),
    )


def _live_config() -> RunConfig:
    return RunConfig(
        pipeline=replace(pipeline_config(_STRATEGY), routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        seed=31337,
        start_timestamp=1.0,
        compile_analytics=False,
    )


# --------------------------------------------------------------------------- #
# 1 -- the whole chain, once
# --------------------------------------------------------------------------- #


def test_a_deployment_decision_reaches_a_fill_at_a_venue() -> None:
    """Experiment -> evidence -> promotion -> approval -> deployment ->
    authorization -> live run -> routed order -> venue fill -> portfolio."""

    lifecycle, ref = _deployed()

    # What the environment says should run, read from the ledger and nowhere else.
    plan = run_plan(lifecycle, _ENVIRONMENT)
    assert plan.reference == ref
    assert plan.version.stage is ModelStage.PRODUCTION
    assert plan.evidence is not None and plan.evidence.subject == str(ref)
    assert plan.deployed_by == "releaser", "the ledger does not say who deployed it"
    assert plan.attributed

    # The run is checked against it before it starts.
    authorization = authorize_run(lifecycle, _ENVIRONMENT, plan.reference, _STRATEGY)
    assert authorization.reference == ref

    with run_venue(VenueCredentials(_KEY, _SECRET)) as (base_url, book, _script):
        broker = _broker(base_url)
        state = LiveSession.initialize(
            _live_config(),
            running_strategy_state(
                _STRATEGY, ScriptedStrategy(_STRATEGY, _SYMBOL, {2.0: Decimal("10")})
            ),
            _broker_state(),
            RoutingConfig(venue="TESTVENUE", currency="USD"),
        )
        state, _ = LiveSession.connect(state, broker, 1.5)
        records = dataset_of_quotes(_SYMBOL, [Decimal("100"), Decimal("101")]).records

        # The strategy trades, and the order reaches the venue.
        state, first = LiveSession.advance(state, records[0], context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None
        assert client_order_id in book.orders

        # The venue fills it, and the fill reaches the portfolio.
        record_fill(book, client_order_id, fill("EX-1", "10", "100.50", timestamp=2.5))
        broker_state, applied, _ = broker.poll_executions(state.broker, 2.5)
        state = replace(state, broker=broker_state)
        state, second = LiveSession.advance(
            state, records[1], context_factory, broker, executions=applied
        )

        assert second.settled[0].booked
        assert state.run.pipeline.portfolio.positions[_SYMBOL].quantity == Decimal("10")
        assert live_health(state) == ()

    # And the governed decisions behind it are all attributed.
    log = governance_log(lifecycle)
    assert {act.action for act in log} >= {"promotion", "approval", "deployment"}
    assert all(act.attributed for act in log)


# --------------------------------------------------------------------------- #
# 2 -- the joins are joins, not coincidences
# --------------------------------------------------------------------------- #


def test_a_run_serving_a_version_the_environment_does_not_have_live_is_refused() -> None:
    """The check that did not exist before v2.16."""

    lifecycle, first = _deployed()

    # A second version, promoted and approved but never deployed.
    lifecycle, second = _registered(lifecycle, 8.0, 20.0)
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(second), "ds-1", METRICS, 26.0, seed=7, source_id="rep-2"
    )
    lifecycle = record_evidence(lifecycle, evidence)
    lifecycle = promote_strategy_version(
        lifecycle, RELEASER, second.name, second.version, POLICY, evidence.evidence_id, 27.0
    )

    with pytest.raises(LifecycleTransitionError, match="would serve"):
        authorize_run(lifecycle, _ENVIRONMENT, second, _STRATEGY)

    # The one that is live still authorizes.
    assert authorize_run(lifecycle, _ENVIRONMENT, first, _STRATEGY).reference == first


def test_a_rollback_changes_what_a_run_is_authorized_to_serve() -> None:
    """The ledger is the authority, and it moves."""

    lifecycle, first = _deployed()
    lifecycle, second = _registered(lifecycle, 8.0, 20.0)
    evidence = build_evidence(
        ValidationMethod.BACKTEST, str(second), "ds-1", METRICS, 26.0, seed=7, source_id="rep-2"
    )
    lifecycle = record_evidence(lifecycle, evidence)
    lifecycle = promote_strategy_version(
        lifecycle, RELEASER, second.name, second.version, POLICY, evidence.evidence_id, 27.0
    )
    lifecycle = approve_deployment(
        lifecycle, APPROVER, second.name, second.version, _ENVIRONMENT, 27.5
    )
    lifecycle, _ = deploy_strategy_version(
        lifecycle, RELEASER, second.name, second.version, _ENVIRONMENT, 28.0
    )

    assert run_plan(lifecycle, _ENVIRONMENT).reference == second

    lifecycle, _ = rollback_environment(lifecycle, RELEASER, _ENVIRONMENT, 29.0)

    assert run_plan(lifecycle, _ENVIRONMENT).reference == first
    with pytest.raises(LifecycleTransitionError, match="would serve"):
        authorize_run(lifecycle, _ENVIRONMENT, second, _STRATEGY)


def test_an_environment_with_nothing_live_refuses_rather_than_answering_none() -> None:
    """A ``None`` a caller forgets to check starts a run anyway."""

    from alphalab.lifecycle import LifecycleInputError

    lifecycle, _ = _deployed()

    with pytest.raises(LifecycleInputError, match="no active strategy version"):
        run_plan(lifecycle, "staging")

    with pytest.raises(LifecycleInputError, match="no active strategy version"):
        authorize_run(lifecycle, "staging", StrategyVersionRef(_STRATEGY, 1), _STRATEGY)


def test_an_ungoverned_deployment_is_visible_as_unattributed() -> None:
    """A deployment made before v2.16, or through the deployment manager directly."""

    from alphalab.deployment_manager.deployment import record_deployment

    lifecycle, ref = _deployed()
    package = lifecycle.deployments.releases[ref.name][-1]
    ungoverned = replace(
        lifecycle,
        deployments=record_deployment(
            lifecycle.deployments, "staging", package, is_rollback=False, timestamp=9.0
        ),
    )

    plan = run_plan(ungoverned, "staging")

    assert plan.reference == ref, "the staging ledger names the same version"
    assert plan.deployed_by == ""
    assert not plan.attributed, "an unattributed deployment claimed an actor"
    # And the governed one beside it still is attributed.
    assert run_plan(ungoverned, _ENVIRONMENT).deployed_by == "releaser"


# --------------------------------------------------------------------------- #
# 3 -- the join adds no second runtime
# --------------------------------------------------------------------------- #


def test_the_join_builds_no_state_and_starts_nothing() -> None:
    """A query with a refusal, not a runtime."""

    import inspect

    from alphalab.lifecycle import execution

    source = inspect.getsource(execution)
    for forbidden in ("RunEngine", "LiveSession", "ExecutionPipeline", "RunConfig("):
        assert forbidden not in source, f"the join reached into the runtime: {forbidden}"


def test_the_run_state_still_carries_no_deployment_reference() -> None:
    """ADR-0030 decision 2 fixes ``RunState`` at eight fields."""

    from dataclasses import fields

    from alphalab.runtime.run import RunState

    names = {f.name for f in fields(RunState)}
    assert "deployment" not in names
    assert "environment" not in names
    assert len(names) == 8
