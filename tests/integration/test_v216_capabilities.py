"""The three v2.16 capabilities in one lifecycle, and where they actually join.

v2.15 ended with five capabilities that each landed on an existing boundary.
v2.16 adds three that each close a *join*:

* **Integrated runtime** -- a deployment decision reaches a fill at a venue, and
  a run that would serve a version the ledger does not name is refused.
* **Approval, RBAC and audit** -- every act that changes what is live names its
  principal, an approval is independent of the deployer, and the ledger answers
  *who*.
* **True FX** -- a book holding two currencies values as one figure, and the
  figure says which rates got it there.

The composition below is the honest one, not a forced one. The first two join
inside the run: an authorization gates it and the venue settles it. The third
joins *beside* it, at the portfolio -- because the settlement boundary keeps a
run's own book single-currency on purpose (ADR-0028), and FX is what lets an
operator value that book against foreign holdings it also carries. Pretending
FX joined inside the pipeline would mean adding a rate table to run
configuration, where a time-varying quote does not belong.
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
    run_plan,
)
from alphalab.model_registry import ModelStage, promote
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.valuation import PortfolioValuation
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.live import LiveSession, live_health
from alphalab.runtime.live_snapshot import capture as capture_live
from alphalab.runtime.live_snapshot import from_primitives as live_from_primitives
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
EURUSD = FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB")


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


def _governed_lifecycle() -> tuple[LifecycleState, StrategyVersionRef]:
    state = LifecycleState()
    tracker, run_id = start_run(state.experiments, "sweep", {"fast": 5.0}, 1.0)
    tracker = complete_run(log_metrics(tracker, run_id, {"sharpe": 1.4}), run_id, 2.0)
    state = replace(state, experiments=tracker)

    state, model = register_model_version(
        state, "momentum", object(), 3.0, run_id=run_id, metrics={"sharpe": 1.4}
    )
    state = replace(
        state, models=promote(state.models, model.name, model.version, ModelStage.STAGING, 4.0)
    )
    state, ref = register_strategy(
        state,
        _STRATEGY,
        StrategyDefinition("ma-001", "MA Crossover", "1", "quant", "desc", {"fast": 5.0}),
        5.0,
        model=model,
        run_id=run_id,
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
# All three, once
# --------------------------------------------------------------------------- #


def test_all_three_capabilities_in_one_lifecycle() -> None:
    # --- 2. Governance: nothing reaches production unapproved or unattributed.
    lifecycle, ref = _governed_lifecycle()
    plan = run_plan(lifecycle, _ENVIRONMENT)
    assert plan.deployed_by == "releaser"
    assert governance_log(lifecycle)[-1].attributed

    # --- 1. Integrated runtime: the run is authorized against the ledger,
    #        then routes and settles against a real venue.
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

        state, first = LiveSession.advance(state, records[0], context_factory, broker)
        client_order_id = first.routed[0].broker_order_id
        assert client_order_id is not None and client_order_id in book.orders

        record_fill(book, client_order_id, fill("EX-1", "10", "100.50", timestamp=2.5))
        broker_state, applied, _ = broker.poll_executions(state.broker, 2.5)
        state = replace(state, broker=broker_state)
        state, second = LiveSession.advance(
            state, records[1], context_factory, broker, executions=applied
        )
        assert second.settled[0].booked
        assert live_health(state) == ()

        # The venue binding survives a process boundary.
        decoded = live_from_primitives(deserialize(serialize(capture_live(state))))
        assert decoded.broker.order_bindings

    portfolio = state.run.pipeline.portfolio
    assert portfolio.positions[_SYMBOL].quantity == Decimal("10")

    # --- 3. FX: the desk also carries EUR, and wants one figure.
    rates = FxRates.of([EURUSD], max_age_seconds=3600.0)
    with_eur = replace(portfolio, cash=portfolio.cash.deposit(Decimal("500.00"), "EUR"))

    settled_only = PortfolioValuation.snapshot(portfolio, 3.0, "USD")
    assert not settled_only.converted, "a single-currency run converted something"

    combined = PortfolioValuation.snapshot(with_eur, 3.0, "USD", rates)
    assert combined.converted
    assert combined.rate_sources == ("ECB",)
    assert combined.equity == settled_only.equity + Decimal("550.00")


def test_each_capability_refuses_on_its_own_terms() -> None:
    """Three refusals, three different fixes, none of them silent."""

    lifecycle, ref = _governed_lifecycle()

    # 2. An unapproved deployment to a gated environment.
    from alphalab.enterprise.exceptions import EnterprisePermissionError

    with pytest.raises(EnterprisePermissionError, match=r"lifecycle\.approve"):
        approve_deployment(lifecycle, RELEASER, ref.name, ref.version, _ENVIRONMENT, 9.0)

    # 1. A run serving a version the environment does not have live.
    with pytest.raises(LifecycleTransitionError, match="would serve"):
        authorize_run(lifecycle, _ENVIRONMENT, StrategyVersionRef(_STRATEGY, 99), _STRATEGY)

    # 3. A mixed book with no rate for the pair.
    from alphalab.portfolio.account import Account
    from alphalab.portfolio.cash import CashLedger
    from alphalab.portfolio.engine import PortfolioState
    from alphalab.portfolio.exceptions import MixedCurrencyValuationError

    mixed = PortfolioState(
        account=Account("acct", "USD", "Desk", 1.0),
        cash=CashLedger(balances={"USD": Decimal("100"), "JPY": Decimal("5000")}),
    )
    with pytest.raises(MixedCurrencyValuationError, match="none converts"):
        PortfolioValuation.snapshot(mixed, 3.0, "USD", FxRates.of([EURUSD]))


def test_no_capability_moved_another_ones_boundary() -> None:
    """The property that makes these three additions rather than a rewrite."""

    from dataclasses import fields

    from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
    from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
    from alphalab.runtime.run import RunState
    from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA
    from alphalab.runtime.snapshot import PIPELINE_SNAPSHOT_SCHEMA

    # The live driver added no field to the run and moved no run schema.
    assert len(fields(RunState)) == 8
    assert RUN_SNAPSHOT_SCHEMA == 1

    # FX added no field to run configuration and moved no pipeline schema.
    assert "fx_rates" not in {f.name for f in fields(ExecutionPipelineConfig)}
    assert PIPELINE_SNAPSHOT_SCHEMA == 2

    # Governance moved exactly one schema, and only its own.
    from alphalab.common.constants import DEFAULT_SCHEMA_VERSION

    assert LIFECYCLE_SNAPSHOT_SCHEMA == 2
    assert DEFAULT_SCHEMA_VERSION == 1
