"""
AlphaLab Examples
=================

Example 49 : Strategy Portability

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 11 (the integrated execution path)
✓ Example 42 (deployment specifications)
✓ Example 46 (strategy fingerprints)

Topics
------

• One fingerprint moving from research to paper to live
• Environments as capability declarations -- "broker A" and "broker B" are two
• Eight requirements, each satisfied, blocked, unverified or not applicable
• A blocker named, never worked around
• A deployment that quietly retunes the strategy, caught
• The same code producing the same fills in two environments

What this shows
---------------

AlphaLab's four environments share one strategy code path: strategy dispatch,
allocation, risk, the order lifecycle and accounting are the same function in a
backtest, a replay, a paper run and a live run. So a strategy's quantitative
logic never *has* to change between them. What can stop it moving is the
environment: a broker that cannot short, a feed too slow for its freshness
budget, an account that allows less leverage, a contract with another
multiplier.

``evaluate_portability`` checks one fingerprint and one specification against
environments that declare what they offer, and says -- per requirement -- what
blocks the move. It adapts nothing: a strategy that needs to short is not made
long-only to fit a broker that cannot.

Run

    python examples/49_strategy_portability.py
"""

from dataclasses import replace
from decimal import Decimal

from _strategy_evidence import (
    ASSET_ID,
    CLASSES,
    DEFINITION,
    LIMITS,
    NORMALIZATION,
    SOURCES,
    START_CASH,
    STRATEGY_ID,
    banner,
    context_factory,
    ingest_prices,
    run_backtest,
    run_config,
    running,
)

from alphalab.api import to_market_dataset
from alphalab.backtesting.state import BacktestResult
from alphalab.conventions import (
    LotSpecification,
    MarketConvention,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
)
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.dataset import Dataset
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    BrokerCapabilities,
    BrokerRequirements,
    CapitalPolicy,
    EngineIdentity,
    LifecycleState,
    MarketAvailability,
    MarketRequirements,
    PortabilityRequirement,
    PortabilityStatus,
    RequirementOutcome,
    RuntimeProfile,
    RuntimeRequirements,
    TargetEnvironment,
    Tolerance,
    code_identity_for,
    dataset_assumption_from,
    evaluate_portability,
    fingerprint_for_version,
    get_strategy_version,
    register_strategy,
    research_configuration,
    specification_for_version,
    verify_fingerprint,
    verify_portability_report,
)
from alphalab.runtime.run import ExecutionMode, RunEngine
from alphalab.runtime.session import TradingSession

ENGINE = EngineIdentity("alphalab", "3.6.0")


def convention(multiplier: str) -> MarketConvention:
    """The instrument's contract terms, stated in full."""

    return MarketConvention(
        venue="XNYS",
        calendar_id="XNYS",
        quote_currency="USD",
        settlement_currency="USD",
        multiplier=Decimal(multiplier),
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
    )


#: What the strategy was researched under.
RESEARCHED = {ASSET_ID: convention("1")}

#: Everything an environment could offer the strategy.
FULL = BrokerCapabilities(
    order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
    time_in_force=frozenset({TimeInForce.DAY, TimeInForce.IOC}),
    asset_classes=frozenset({AssetType.EQUITY}),
    short_selling=True,
    fractional_quantities=True,
)

MARKETS = MarketAvailability(
    instruments=frozenset({ASSET_ID}),
    venues=frozenset({"XNYS"}),
    calendar_ids=frozenset({"XNYS"}),
    quote_currencies=frozenset({"USD"}),
)


def environments(dataset_version: str) -> tuple[TargetEnvironment, ...]:
    """Five environments, each exactly as it declares itself."""

    research = TargetEnvironment(
        "research",
        ExecutionMode.BACKTEST,
        FULL,
        MARKETS,
        datasets=frozenset({dataset_version}),
        conventions=RESEARCHED,
        max_leverage=Decimal("2"),
        account_currencies=frozenset({"USD"}),
    )
    return (
        research,
        replace(
            research,
            name="paper",
            mode=ExecutionMode.PAPER,
            datasets=None,
            runtime=RuntimeProfile(1.0, 5.0, 0.2),
        ),
        replace(
            research,
            name="live-broker-a",
            mode=ExecutionMode.LIVE,
            datasets=None,
            runtime=RuntimeProfile(0.5, 10.0, 0.8),
        ),
        replace(
            research,
            name="live-broker-b",
            mode=ExecutionMode.LIVE,
            broker=replace(FULL, short_selling=False, fractional_quantities=False),
            datasets=None,
            conventions={ASSET_ID: convention("10")},
            runtime=RuntimeProfile(0.5, 10.0, 4.0),
        ),
        TargetEnvironment("live-undeclared", ExecutionMode.LIVE, FULL, MARKETS),
    )


def paper_of(dataset: Dataset) -> BacktestResult:
    """The same registered strategy through the paper driver, record by record."""

    records = to_market_dataset(dataset, NORMALIZATION).records
    state = TradingSession.initialize(run_config(mode=ExecutionMode.PAPER), running())
    state = replace(state, source_id=dataset.require_provenance().dataset_version)
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return BacktestResult(RunEngine.finalize(state))


def main() -> None:
    banner(49, "Strategy Portability")

    dataset = ingest_prices()
    state, reference = register_strategy(LifecycleState(), "ex-momentum", DEFINITION, 1.0)
    version = get_strategy_version(state.strategies, reference.name, reference.version)
    code = code_identity_for(CLASSES.require(STRATEGY_ID), "alphalab-examples", "3.6.0", SOURCES)
    fingerprint = fingerprint_for_version(
        version,
        code,
        NO_DEPENDENCIES,
        research_configuration({"validation": "single in-sample backtest"}),
        ENGINE,
    )
    specification = specification_for_version(
        version,
        datasets=(dataset_assumption_from(dataset, "prices"),),
        risk=LIMITS,
        capital=CapitalPolicy("ACC-EX", "USD", START_CASH, ("USD",)),
        broker=BrokerRequirements(
            order_types=frozenset({OrderType.MARKET}),
            time_in_force=frozenset({TimeInForce.DAY}),
            asset_classes=frozenset({AssetType.EQUITY}),
            short_selling=True,
            fractional_quantities=False,
        ),
        market=MarketRequirements((ASSET_ID,), ("XNYS",), ("XNYS",), ("USD",)),
        runtime=RuntimeRequirements(
            max_data_staleness_seconds=2.0,
            max_heartbeat_silence_seconds=30.0,
            max_execution_latency_seconds=1.0,
            position_tolerance=Tolerance(absolute=Decimal("0")),
        ),
    )
    version_id = dataset.require_provenance().dataset_version

    # ----------------------------------------------------------------- #
    # 1. Five environments, one fingerprint
    # ----------------------------------------------------------------- #

    print("\n[1] One strategy, five declared environments")
    before = (fingerprint, specification)
    report = evaluate_portability(fingerprint, specification, environments(version_id), RESEARCHED)
    print(f"  fingerprint: {report.fingerprint}")
    for entry in report.environments:
        print(f"\n  {entry.environment} ({entry.mode.name}) -> {entry.status.name}")
        for check in entry.checks:
            mark = {
                RequirementOutcome.SATISFIED: "ok",
                RequirementOutcome.BLOCKED: "BLOCKED",
                RequirementOutcome.NOT_VERIFIED: "unverified",
                RequirementOutcome.NOT_APPLICABLE: "n/a",
            }[check.outcome]
            print(f"    {check.requirement.name:15} {mark}")
        for blocker in entry.blockers:
            print(f"    - {blocker}")

    # ----------------------------------------------------------------- #
    # 2. The logic did not change -- shown by running it
    # ----------------------------------------------------------------- #

    print("\n[2] The same code, two environments, the same fills")
    researched = run_backtest(dataset)
    papered = paper_of(dataset)
    fills = [(fill.side.value, str(fill.quantity), str(fill.price)) for fill in researched.fills]
    print(f"  backtest fills : {fills}")
    print(
        "  paper fills    : "
        f"{[(fill.side.value, str(fill.quantity), str(fill.price)) for fill in papered.fills]}"
    )
    print("  (One strategy dispatch path in every execution mode -- ADR-0012's parity.)")

    # ----------------------------------------------------------------- #
    # 3. A deployment that quietly retunes the strategy
    # ----------------------------------------------------------------- #

    print("\n[3] A specification that changes the parameters")
    retuned = specification_for_version(
        replace(
            version, definition=replace(DEFINITION, parameters={"entry": 800.0, "exit": -400.0})
        ),
        datasets=specification.datasets,
        risk=specification.risk,
        capital=specification.capital,
        broker=specification.broker,
        market=specification.market,
        runtime=specification.runtime,
    )
    mutated = evaluate_portability(
        fingerprint, retuned, environments(version_id)[:1], RESEARCHED
    ).environments[0]
    print(f"  research -> {mutated.status.name}")
    for blocker in mutated.blockers:
        print(f"    - {blocker}")

    # ----------------------------------------------------------------- #

    print("\n[4] Invariants")
    broker_b = report.for_environment("live-broker-b")
    checks = (
        ("the report verifies", verify_portability_report(report)),
        (
            "research, paper and broker A take it unchanged",
            report.portable_to == ("research", "paper", "live-broker-a"),
        ),
        ("broker B says why it cannot", broker_b.status is PortabilityStatus.NOT_PORTABLE),
        (
            "a multiplier of 10 is a blocker, not a warning",
            broker_b.check(PortabilityRequirement.CONTRACTS).outcome is RequirementOutcome.BLOCKED,
        ),
        (
            "undeclared capabilities are never portable",
            report.for_environment("live-undeclared").status
            is PortabilityStatus.INSUFFICIENT_EVIDENCE,
        ),
        ("a retuned deployment is caught", mutated.status is PortabilityStatus.NOT_PORTABLE),
        ("nothing handed in was changed", (fingerprint, specification) == before),
        ("the fingerprint still verifies", verify_fingerprint(fingerprint)),
        (
            "the same code filled the same way",
            fills
            == [(fill.side.value, str(fill.quantity), str(fill.price)) for fill in papered.fills],
        ),
    )
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAILED'}] {label}")
    assert all(passed for _, passed in checks)

    print("\n" + "=" * 68)
    print("Example 49 complete.")
    print("(The brokers above are capability declarations written in this file. No")
    print(" adapter was built, no venue was reached, and no strategy was adapted.)")


if __name__ == "__main__":
    main()
