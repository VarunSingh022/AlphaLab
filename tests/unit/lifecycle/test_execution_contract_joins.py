"""The lifecycle's joins to the v3.9 contract: projection, portability, children, fingerprints."""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.lifecycle import VenueEvent, apply_venue_event
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState
from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    AccountCapability,
    Capability,
    CapabilityDeclaration,
    MarketCapability,
    Support,
)
from alphalab.core.contribution import StrategyContribution
from alphalab.core.enums import AssetType, OrderStatus, OrderType, TimeInForce
from alphalab.core.lifecycle import ExecutionEventKind
from alphalab.core.order_request import OrderRequest
from alphalab.data.dataset import Dataset
from alphalab.execution.algorithms import (
    TWAP,
    AlgorithmTerms,
    Slicing,
    Urgency,
    release_children,
    start_algorithm,
)
from alphalab.execution.routing import RoutingObjective, RoutingPolicy
from alphalab.lifecycle import (
    BrokerCapabilities,
    LifecycleInputError,
    MismatchCategory,
    PortabilityRequirement,
    PortabilityStatus,
    ReconciliationTolerances,
    StudyIdentity,
    SymbolMapping,
    TargetEnvironment,
    Tolerance,
    broker_capabilities_from,
    evaluate_portability,
    reconcile_execution_state,
    research_configuration,
    research_configuration_with_execution,
    verify_fingerprint,
)
from alphalab.oms.order import Order as OMSOrder
from alphalab.runtime.broker_routing import (
    ChildOrderBindings,
    RoutingConfig,
    apply_broker_execution,
    child_broker_order_id,
    route_child_order,
)
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.runtime.run import ExecutionMode
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)
from tests.unit.lifecycle.evidence_harness import (
    ASSET_ID,
    AVAILABLE,
    CAPITAL,
    RISK,
    fingerprint,
    ingest,
    specification,
)
from tests.unit.lifecycle.evidence_harness import (
    equity_convention as convention,
)

YES, NO, SILENT = Support.SUPPORTED, Support.UNSUPPORTED, Support.UNDECLARED


def _market(asset: AssetType = AssetType.EQUITY, **overrides: Any) -> MarketCapability:
    base = MarketCapability(
        asset,
        ANY_LISTING_VENUE,
        frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP}),
        frozenset({TimeInForce.DAY, TimeInForce.GTC}),
        YES,
        YES,
        NO,
        NO,
    )
    return replace(base, **overrides)


def _declaration(
    *markets: MarketCapability, account: AccountCapability | None = None
) -> CapabilityDeclaration:
    return CapabilityDeclaration(
        "broker-a",
        frozenset({Capability.STREAMING}),
        frozenset(),
        markets or (_market(),),
        frozenset({AssetType.FUTURE}),
        (account,)
        if account is not None
        else (AccountCapability(CAPITAL.account_id, frozenset(AssetType), YES, YES),),
    )


def _project(declaration: CapabilityDeclaration, **overrides: Any) -> BrokerCapabilities:
    arguments: dict[str, Any] = {
        "listing_venues": frozenset({"XNYS"}),
        "asset_classes": frozenset({AssetType.EQUITY}),
        "account_id": CAPITAL.account_id,
        **overrides,
    }
    return broker_capabilities_from(declaration, **arguments)


# --------------------------------------------------------------------------- #
# Projection onto the v3.5 record
# --------------------------------------------------------------------------- #


def test_a_declaration_projects_onto_the_broker_wide_record() -> None:
    projected = _project(_declaration())
    assert projected.order_types == {OrderType.MARKET, OrderType.LIMIT, OrderType.STOP}
    assert projected.time_in_force == {TimeInForce.DAY, TimeInForce.GTC}
    assert projected.asset_classes == {AssetType.EQUITY}
    assert projected.short_selling and projected.fractional_quantities


def test_the_projection_intersects_the_markets_a_deployment_trades() -> None:
    declaration = _declaration(
        _market(),
        _market(
            AssetType.OPTION,
            order_types=frozenset({OrderType.LIMIT}),
            short_selling=NO,
            fractional_quantities=NO,
        ),
    )
    both = _project(declaration, asset_classes=frozenset({AssetType.EQUITY, AssetType.OPTION}))
    assert both.order_types == {OrderType.LIMIT}
    assert both.asset_classes == {AssetType.EQUITY, AssetType.OPTION}
    assert not both.short_selling and not both.fractional_quantities


def test_what_the_broker_does_not_offer_or_the_account_may_not_trade_is_left_out() -> None:
    withdrawn = _project(
        _declaration(), asset_classes=frozenset({AssetType.EQUITY, AssetType.FUTURE})
    )
    assert withdrawn.asset_classes == {AssetType.EQUITY}
    narrow = _declaration(
        account=AccountCapability(CAPITAL.account_id, frozenset({AssetType.OPTION}), YES, YES)
    )
    assert _project(narrow).asset_classes == frozenset()


@pytest.mark.parametrize(
    ("declaration", "gap"),
    [
        (_declaration(_market(AssetType.OPTION)), "equity market is not declared"),
        (_declaration(_market(short_selling=SILENT)), "short selling"),
        (_declaration(_market(fractional_quantities=SILENT)), "fractional"),
    ],
)
def test_silence_is_never_projected_as_either_answer(
    declaration: CapabilityDeclaration, gap: str
) -> None:
    with pytest.raises(LifecycleInputError, match=gap):
        _project(declaration)
    with pytest.raises(LifecycleInputError, match="is not declared"):
        _project(_declaration(), account_id="SOMEONE-ELSE")


# --------------------------------------------------------------------------- #
# Portability: broker A and broker B, one unchanged strategy
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return ingest()


def _environment(
    name: str, declaration: CapabilityDeclaration, dataset: Dataset
) -> TargetEnvironment:
    return TargetEnvironment(
        name=name,
        mode=ExecutionMode.PAPER,
        broker=_project(declaration),
        market=AVAILABLE,
        datasets=frozenset({dataset.require_provenance().dataset_version}),
        conventions={ASSET_ID: convention()},
        runtime=None,
        max_leverage=RISK.leverage.max_leverage,
        account_currencies=frozenset({"USD"}),
    )


def test_the_same_strategy_is_portable_to_one_broker_and_blocked_at_another(
    dataset: Dataset,
) -> None:
    """The difference lives in the declaration, never in the strategy."""

    broker_a = _declaration()
    broker_b = _declaration(_market(order_types=frozenset({OrderType.LIMIT})))
    fp = fingerprint()
    report = evaluate_portability(
        fp,
        specification(dataset),
        (_environment("broker-a", broker_a, dataset), _environment("broker-b", broker_b, dataset)),
        {ASSET_ID: convention()},
    )
    a, b = report.environments
    assert a.check(PortabilityRequirement.EXECUTION).outcome.name == "SATISFIED"
    assert b.status is PortabilityStatus.NOT_PORTABLE
    assert any(
        "market" in blocker for blocker in b.check(PortabilityRequirement.EXECUTION).blockers
    )
    assert report.fingerprint == fp.fingerprint


# --------------------------------------------------------------------------- #
# Reconciling a parent worked in children
# --------------------------------------------------------------------------- #

STRATEGY_ID = str(uuid.uuid4())
PIPE_ASSET = str(uuid.uuid4())
CONFIG = RoutingConfig(venue="VENUE", currency="USD", order_type=OrderType.LIMIT)
TOLERANCES = ReconciliationTolerances(
    order_quantity=Tolerance(absolute=Decimal("0")),
    order_price=Tolerance(absolute=Decimal("0")),
    fill_quantity=Tolerance(absolute=Decimal("0")),
    fill_price=Tolerance(absolute=Decimal("0")),
    commission=Tolerance(absolute=Decimal("0")),
    position_quantity=Tolerance(absolute=Decimal("0")),
    cash=Tolerance(absolute=Decimal("1000000")),
)


def _worked_in_children(
    fill_first: bool = True,
) -> tuple[ExecutionPipelineState, BrokerState, ChildOrderBindings, OMSOrder]:
    config = replace(pipeline_config(STRATEGY_ID), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, PIPE_ASSET, {2.0: Decimal("10")})
        ),
        1.0,
    )
    state = ExecutionPipeline.process_quote(
        state, sized_quote(PIPE_ASSET, 2.0, Decimal("100"), Decimal("100")), context_factory
    ).state
    parent = next(iter(state.oms.orders.open_orders()))
    request = OrderRequest(
        str(parent.order_id.value),
        parent.strategy_id,
        parent.asset_id,
        parent.side,
        parent.quantity,
        Decimal("100"),
        2.0,
        (StrategyContribution(STRATEGY_ID, parent.quantity),),
    )
    algorithm = start_algorithm(
        request,
        Slicing(Decimal("5"), None),
        AlgorithmTerms(2.0, 100.0, Decimal("1"), OrderType.LIMIT, Decimal("100")),
    )
    venue = BrokerEngine.initialize("VENUE", Decimal("1000000"), "USD")
    venue, _ = PaperBroker().connect(venue, 1.0)
    bindings = ChildOrderBindings()
    algorithm, (child,) = release_children(algorithm, 3.0)
    routed = route_child_order(
        venue,
        PaperBroker(),
        parent,
        child,
        3.0,
        mapping=ExternalOrderMap(),
        children=bindings,
        config=CONFIG,
        capability=None,
    )
    venue, bindings = routed.broker_state, routed.children
    handle = child_broker_order_id(parent, 1)
    if fill_first:
        fill = BrokerExecution(
            "VX-1", handle, PIPE_ASSET, Decimal("5"), Decimal("100"), Decimal("0"), 4.0
        )
        venue, decision = apply_venue_event(
            venue, VenueEvent(ExecutionEventKind.ORDER_FILLED, 4.0, handle, execution=fill)
        )
        assert decision.applied
        # The venue reports its own position; a fill does not move the mirror's.
        position = BrokerPosition(
            PIPE_ASSET, Decimal("5"), Decimal("100"), Decimal("500"), Decimal("0"), Decimal("0")
        )
        venue, held = apply_venue_event(
            venue, VenueEvent(ExecutionEventKind.POSITION_CHANGED, 4.0, position=position)
        )
        assert held.applied
        state, _, _ = apply_broker_execution(
            state, state.oms.orders.find(parent.order_id), fill, CONFIG
        )
    return state, venue, bindings, parent


def test_a_parent_worked_in_children_reconciles_clean() -> None:
    state, venue, bindings, _ = _worked_in_children()
    result = reconcile_execution_state(
        state, venue, ExternalOrderMap(), SymbolMapping.identity(), TOLERANCES, children=bindings
    )
    assert result.mismatches == (), result.mismatches
    assert result.compared_orders == 1 and result.compared_fills == 1


def test_without_the_bindings_every_child_is_an_order_nobody_sent() -> None:
    state, venue, _, _ = _worked_in_children()
    result = reconcile_execution_state(
        state, venue, ExternalOrderMap(), SymbolMapping.identity(), TOLERANCES
    )
    assert [m.category for m in result.mismatches] == [MismatchCategory.UNEXPECTED_OBSERVED_ORDER]


def test_a_child_missing_at_the_venue_and_a_finished_parent_with_live_children_are_breaks() -> None:
    state, venue, bindings, parent = _worked_in_children(fill_first=False)
    missing = reconcile_execution_state(
        state,
        venue,
        ExternalOrderMap(),
        SymbolMapping.identity(),
        TOLERANCES,
        children=bindings.bind(child_broker_order_id(parent, 2), str(parent.order_id.value)),
    )
    assert MismatchCategory.MISSING_EXPECTED_ORDER in {m.category for m in missing.mismatches}

    cancelled = ExecutionPipeline.apply_terminal_outcome(state, parent, OrderStatus.CANCELLED, 9.0)
    live_child = reconcile_execution_state(
        cancelled,
        venue,
        ExternalOrderMap(),
        SymbolMapping.identity(),
        TOLERANCES,
        children=bindings,
    )
    assert any(
        m.category is MismatchCategory.LIFECYCLE_STATE_MISMATCH
        and "children are still working" in m.reason
        for m in live_child.mismatches
    )


# --------------------------------------------------------------------------- #
# Execution configuration in a fingerprint
# --------------------------------------------------------------------------- #


def _policy() -> RoutingPolicy:
    return RoutingPolicy(RoutingObjective.LOWEST_ALL_IN_COST, 5.0, None, True, False, frozenset())


def test_execution_configuration_enters_a_fingerprint_through_research_settings() -> None:
    twap = TWAP(12, Urgency(Decimal("2")))
    research = research_configuration_with_execution(
        {"universe": "large-cap"},
        algorithms={"parent": twap},
        routing={"primary": _policy()},
        study=None,
    )
    assert research.settings["execution.parent.algorithm"] == twap.configuration_id
    assert research.settings["routing.primary.policy"] == _policy().policy_id

    with_twap = fingerprint(research=research)
    with_other = fingerprint(
        research=research_configuration_with_execution(
            {"universe": "large-cap"},
            algorithms={"parent": TWAP(12, Urgency(Decimal("3")))},
            routing={"primary": _policy()},
            study=None,
        )
    )
    assert verify_fingerprint(with_twap)
    assert with_twap.fingerprint != with_other.fingerprint
    assert verify_fingerprint(
        fingerprint(research=research_configuration({"universe": "large-cap"}))
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"algorithms": {}, "routing": {}}, "names no algorithm"),
        (
            {"algorithms": {"bad name": TWAP(2, Urgency.neutral())}, "routing": {}},
            "not an identifier",
        ),
        (
            {
                "algorithms": {"x": Slicing(None, 2)},
                "routing": {},
                "settings": {"execution.x.algorithm": "a"},
            },
            "supplied by the caller",
        ),
    ],
)
def test_the_execution_join_refuses_what_would_make_a_setting_ambiguous(
    arguments: dict[str, Any], message: str
) -> None:
    settings = arguments.pop("settings", {})
    with pytest.raises(LifecycleInputError, match=message):
        research_configuration_with_execution(settings, study=None, **arguments)


def test_a_study_identity_is_carried_through() -> None:
    class _Study:
        @property
        def study_id(self) -> str:
            return "a" * 64

    study: StudyIdentity = _Study()
    research = research_configuration_with_execution(
        {}, algorithms={"x": Slicing(None, 3)}, routing={}, study=study
    )
    assert research.study_id == "a" * 64
