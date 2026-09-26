"""Strategy portability: one fingerprint, many declared environments, no adaptation.

What matters is that a blocker is named rather than worked around, that a
requirement nobody could check is never counted as met, that a requirement which
cannot arise in an environment is said not to apply rather than silently passed,
and that the strategy's identity is the same one everywhere and is never
replaced by something that would fit better.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.dataset import Dataset
from alphalab.lifecycle import (
    PORTABILITY_REPORT_SCHEME,
    BrokerCapabilities,
    BrokerRequirements,
    DeploymentSpecification,
    LifecycleInputError,
    MarketAvailability,
    PortabilityRequirement,
    PortabilityStatus,
    RequirementOutcome,
    RuntimeProfile,
    StrategyFingerprint,
    TargetEnvironment,
    evaluate_portability,
    specification_for_version,
    verify_portability_report,
)
from alphalab.runtime.run import ExecutionMode
from tests.unit.lifecycle.evidence_harness import (
    ASSET_ID,
    AVAILABLE,
    CAPITAL,
    DEFINITION,
    EVERYTHING,
    MARKET_REQUIREMENTS,
    RISK,
    RUNTIME_REQUIREMENTS,
    VERSION,
    equity_convention,
    fingerprint,
    ingest,
    specification,
)

R = PortabilityRequirement
Outcome = RequirementOutcome


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return ingest()


@pytest.fixture(scope="module")
def spec(dataset: Dataset) -> DeploymentSpecification:
    return specification(dataset)


@pytest.fixture(scope="module")
def fp() -> StrategyFingerprint:
    return fingerprint()


CONVENTIONS = {ASSET_ID: equity_convention()}


def environment(
    dataset: Dataset, mode: ExecutionMode = ExecutionMode.BACKTEST, **overrides: object
) -> TargetEnvironment:
    """An environment that declares everything, and offers everything the harness needs."""

    fields: dict[str, object] = {
        "name": mode.name.lower(),
        "mode": mode,
        "broker": EVERYTHING,
        "market": AVAILABLE,
        "datasets": frozenset({dataset.dataset_version}),
        "conventions": dict(CONVENTIONS),
        "runtime": RuntimeProfile(1.0, 1.0, 1.0),
        "max_leverage": RISK.leverage.max_leverage,
        "account_currencies": frozenset({"USD"}),
        **overrides,
    }
    return TargetEnvironment(**fields)  # type: ignore[arg-type]


def evaluate(
    fp: StrategyFingerprint,
    spec: DeploymentSpecification,
    *environments: TargetEnvironment,
    conventions: object = CONVENTIONS,
) -> object:
    return evaluate_portability(fp, spec, environments, conventions)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Portable where everything is declared and met
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", list(ExecutionMode))
def test_a_fully_declared_compatible_environment_is_portable(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint, mode: ExecutionMode
) -> None:
    report = evaluate_portability(fp, spec, (environment(dataset, mode),), CONVENTIONS)
    result = report.environments[0]

    assert result.status is PortabilityStatus.PORTABLE, result.checks
    assert result.blockers == ()
    assert [check.requirement for check in result.checks] == list(PortabilityRequirement)


def test_historical_and_realtime_environments_differ_in_what_applies(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    report = evaluate_portability(
        fp,
        spec,
        (environment(dataset, ExecutionMode.BACKTEST), environment(dataset, ExecutionMode.LIVE)),
        CONVENTIONS,
    )
    research, live = report.environments

    assert research.check(R.RUNTIME).outcome is Outcome.NOT_APPLICABLE
    assert research.check(R.DATA).outcome is Outcome.SATISFIED
    assert live.check(R.DATA).outcome is Outcome.NOT_APPLICABLE
    assert live.check(R.RUNTIME).outcome is Outcome.SATISFIED


def test_one_fingerprint_is_carried_to_every_environment_unchanged(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    before = (fp, spec)
    environments = tuple(
        environment(dataset, mode, name=f"env-{mode.name}") for mode in ExecutionMode
    )

    report = evaluate_portability(fp, spec, environments, CONVENTIONS)

    assert report.fingerprint == fp.fingerprint
    assert report.portable_to == tuple(env.name for env in environments)
    assert (fp, spec) == before, "evaluation replaced nothing it was given"


# --------------------------------------------------------------------------- #
# Each requirement blocks on its own, and says why
# --------------------------------------------------------------------------- #


def _single_blocker(report: object, requirement: PortabilityRequirement) -> tuple[str, ...]:
    result = report.environments[0]  # type: ignore[attr-defined]
    assert result.status is PortabilityStatus.NOT_PORTABLE
    blocked = [check.requirement for check in result.checks if check.outcome is Outcome.BLOCKED]
    assert blocked == [requirement]
    return tuple(result.check(requirement).blockers)


def test_an_unsupported_order_type_blocks_and_is_not_substituted(
    dataset: Dataset, fp: StrategyFingerprint
) -> None:
    needs_stops = specification_for_version(
        VERSION,
        datasets=specification(dataset).datasets,
        risk=RISK,
        capital=CAPITAL,
        broker=BrokerRequirements(
            order_types=frozenset({OrderType.MARKET, OrderType.STOP}),
            time_in_force=frozenset({TimeInForce.DAY}),
            asset_classes=frozenset({AssetType.EQUITY}),
            short_selling=True,
            fractional_quantities=True,
        ),
        market=MARKET_REQUIREMENTS,
        runtime=RUNTIME_REQUIREMENTS,
    )
    no_stops = replace(EVERYTHING, order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}))

    blockers = _single_blocker(
        evaluate(fp, needs_stops, environment(dataset, broker=no_stops)), R.EXECUTION
    )

    assert any("'stop'" in blocker for blocker in blockers)


def test_a_missing_execution_capability_blocks(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    no_shorting = replace(EVERYTHING, short_selling=False)

    blockers = _single_blocker(
        evaluate(fp, spec, environment(dataset, broker=no_shorting)), R.EXECUTION
    )

    assert any("short" in blocker for blocker in blockers)


def test_an_unlisted_instrument_blocks(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    elsewhere = replace(AVAILABLE, instruments=frozenset({"something-else"}))

    blockers = _single_blocker(evaluate(fp, spec, environment(dataset, market=elsewhere)), R.MARKET)

    assert ASSET_ID in blockers[0]


def test_missing_session_semantics_block(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    other_calendar = replace(AVAILABLE, calendar_ids=frozenset({"XLON"}))

    blockers = _single_blocker(
        evaluate(fp, spec, environment(dataset, market=other_calendar)), R.MARKET
    )

    assert "market calendars" in blockers[0]


def test_missing_research_data_blocks_a_historical_environment(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    blockers = _single_blocker(
        evaluate(fp, spec, environment(dataset, datasets=frozenset({"other@" + "0" * 64}))),
        R.DATA,
    )

    assert dataset.require_provenance().dataset_version in blockers[0]


def test_an_incompatible_multiplier_blocks(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    fifty = {ASSET_ID: equity_convention(Decimal("50"))}

    blockers = _single_blocker(
        evaluate(fp, spec, environment(dataset, conventions=fifty)), R.CONTRACTS
    )

    assert any("multiplier" in blocker for blocker in blockers)


def test_a_slow_environment_blocks_a_fast_strategy(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    slow = RuntimeProfile(1.0, 1.0, 30.0)

    blockers = _single_blocker(
        evaluate(fp, spec, environment(dataset, ExecutionMode.LIVE, runtime=slow)), R.RUNTIME
    )

    assert "execution latency" in blockers[0]


def test_exactly_at_a_budget_is_within_it(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    at_budget = RuntimeProfile(
        RUNTIME_REQUIREMENTS.max_data_staleness_seconds,
        RUNTIME_REQUIREMENTS.max_heartbeat_silence_seconds,
        RUNTIME_REQUIREMENTS.max_execution_latency_seconds,
    )

    report = evaluate(fp, spec, environment(dataset, ExecutionMode.PAPER, runtime=at_budget))

    assert report.environments[0].check(R.RUNTIME).outcome is Outcome.SATISFIED  # type: ignore[attr-defined]


def test_an_account_permitting_less_leverage_blocks(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    blockers = _single_blocker(
        evaluate(fp, spec, environment(dataset, max_leverage=Decimal("2"))), R.RISK
    )

    assert "refused here" in blockers[0]


def test_an_account_that_cannot_hold_a_settlement_currency_blocks(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    blockers = _single_blocker(
        evaluate(fp, spec, environment(dataset, account_currencies=frozenset({"EUR"}))),
        R.CAPITAL,
    )

    assert "USD" in blockers[0]


def test_a_specification_that_changes_the_parameters_blocks_the_logic(
    dataset: Dataset, fp: StrategyFingerprint
) -> None:
    """The silent-mutation case: deploy with other parameters, keep the old identity."""

    retuned = replace(VERSION, definition=replace(DEFINITION, parameters={"entry": 20.0}))
    mutated = specification(dataset, version=retuned)

    blockers = _single_blocker(evaluate(fp, mutated, environment(dataset)), R.STRATEGY_LOGIC)

    assert any("'entry'" in blocker for blocker in blockers)
    assert any("'exit'" in blocker for blocker in blockers)


# --------------------------------------------------------------------------- #
# Unverified is never satisfied
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mode, override, requirement",
    [
        (ExecutionMode.BACKTEST, {"datasets": None}, R.DATA),
        (ExecutionMode.BACKTEST, {"conventions": None}, R.CONTRACTS),
        (ExecutionMode.LIVE, {"runtime": None}, R.RUNTIME),
        (ExecutionMode.PAPER, {"max_leverage": None}, R.RISK),
        (ExecutionMode.REPLAY, {"account_currencies": None}, R.CAPITAL),
    ],
)
def test_an_undeclared_capability_leaves_the_environment_insufficient(
    dataset: Dataset,
    spec: DeploymentSpecification,
    fp: StrategyFingerprint,
    mode: ExecutionMode,
    override: dict[str, object],
    requirement: PortabilityRequirement,
) -> None:
    report = evaluate_portability(fp, spec, (environment(dataset, mode, **override),), CONVENTIONS)
    result = report.environments[0]

    assert result.status is PortabilityStatus.INSUFFICIENT_EVIDENCE
    assert result.check(requirement).outcome is Outcome.NOT_VERIFIED
    assert result.blockers == ()


def test_contract_terms_nobody_declared_for_the_strategy_cannot_be_checked(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    report = evaluate_portability(fp, spec, (environment(dataset),), None)

    assert report.environments[0].check(R.CONTRACTS).outcome is Outcome.NOT_VERIFIED
    assert report.environments[0].status is PortabilityStatus.INSUFFICIENT_EVIDENCE


def test_contract_terms_declared_for_only_some_instruments_are_not_satisfied(
    dataset: Dataset, fp: StrategyFingerprint
) -> None:
    """An instrument without researched terms could change multiplier unnoticed."""

    two = specification(
        dataset, market=replace(MARKET_REQUIREMENTS, instruments=(ASSET_ID, "V36-OTHER"))
    )
    wider = replace(AVAILABLE, instruments=AVAILABLE.instruments | {"V36-OTHER"})

    result = evaluate_portability(
        fp, two, (environment(dataset, market=wider),), CONVENTIONS
    ).environments[0]

    assert result.check(R.CONTRACTS).outcome is Outcome.NOT_VERIFIED
    assert "V36-OTHER" in result.check(R.CONTRACTS).detail
    assert result.status is PortabilityStatus.INSUFFICIENT_EVIDENCE


def test_a_blocker_outranks_an_unverified_requirement(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    both = environment(dataset, max_leverage=None, broker=replace(EVERYTHING, short_selling=False))

    report = evaluate_portability(fp, spec, (both,), CONVENTIONS)

    assert report.environments[0].status is PortabilityStatus.NOT_PORTABLE


# --------------------------------------------------------------------------- #
# Broker A and broker B
# --------------------------------------------------------------------------- #


def test_two_brokers_are_two_declarations_and_nothing_more(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    broker_a = environment(dataset, ExecutionMode.LIVE, name="live-broker-a")
    broker_b = environment(
        dataset,
        ExecutionMode.LIVE,
        name="live-broker-b",
        broker=BrokerCapabilities(
            order_types=frozenset({OrderType.LIMIT}),
            time_in_force=frozenset({TimeInForce.DAY}),
            asset_classes=frozenset({AssetType.EQUITY}),
            short_selling=False,
            fractional_quantities=False,
        ),
    )

    report = evaluate_portability(fp, spec, (broker_a, broker_b), CONVENTIONS)

    assert report.portable_to == ("live-broker-a",)
    b = report.for_environment("live-broker-b")
    assert b.status is PortabilityStatus.NOT_PORTABLE
    assert len(b.blockers) == 3, b.blockers
    assert all(blocker.startswith("EXECUTION:") for blocker in b.blockers)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def test_the_report_is_deterministic_and_tamper_evident(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    environments = (environment(dataset), environment(dataset, ExecutionMode.LIVE))

    first = evaluate_portability(fp, spec, environments, CONVENTIONS)
    second = evaluate_portability(fp, spec, environments, CONVENTIONS)

    assert first == second
    assert verify_portability_report(first)
    assert len(first.report_id) == 64
    assert not verify_portability_report(replace(first, environments=first.environments[:1]))
    assert PORTABILITY_REPORT_SCHEME == "alphalab.portability_report.v1"


def test_an_unknown_environment_name_is_refused(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    report = evaluate_portability(fp, spec, (environment(dataset),), CONVENTIONS)

    with pytest.raises(LifecycleInputError):
        report.for_environment("nowhere")


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_altered_inputs_are_refused(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    with pytest.raises(LifecycleInputError, match="altered"):
        evaluate_portability(replace(fp, parameters={"entry": 0.0}), spec, (environment(dataset),))
    with pytest.raises(LifecycleInputError, match="does not match"):
        evaluate_portability(fp, replace(spec, parameters={}), (environment(dataset),))


def test_an_evaluation_needs_distinctly_named_environments(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    with pytest.raises(LifecycleInputError, match="at least one"):
        evaluate_portability(fp, spec, ())
    with pytest.raises(LifecycleInputError, match="share a name"):
        evaluate_portability(fp, spec, (environment(dataset), environment(dataset)))


def test_contract_terms_for_an_instrument_the_strategy_does_not_trade_are_refused(
    dataset: Dataset, spec: DeploymentSpecification, fp: StrategyFingerprint
) -> None:
    with pytest.raises(LifecycleInputError, match="does not trade"):
        evaluate_portability(fp, spec, (environment(dataset),), {"elsewhere": equity_convention()})


def test_malformed_declarations_are_refused(dataset: Dataset) -> None:
    with pytest.raises(LifecycleInputError):
        environment(dataset, name=" ")
    with pytest.raises(LifecycleInputError):
        environment(dataset, max_leverage=Decimal("0"))
    with pytest.raises(LifecycleInputError):
        RuntimeProfile(-1.0, 1.0, 1.0)


def test_availability_is_the_v35_declaration_not_a_second_one() -> None:
    """The environment's market and broker fields are the v3.5 types, by identity."""

    fields = TargetEnvironment.__dataclass_fields__

    assert fields["broker"].type == "BrokerCapabilities"
    assert fields["market"].type == "MarketAvailability"
    assert MarketAvailability is type(AVAILABLE)
