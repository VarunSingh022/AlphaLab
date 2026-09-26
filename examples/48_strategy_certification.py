"""
AlphaLab Examples
=================

Example 48 : Strategy Certification

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 42 (deployment specifications)
✓ Example 43 (runtime health)
✓ Example 46 (strategy fingerprints)
✓ Example 47 (reproducible research artifacts)

Topics
------

• Eight machine-verifiable properties, and no overall score
• PASS and FAIL, and the two ways of not knowing
• Evidence that is observed, never asserted
• Leverage and drawdown read exactly as the pre-trade gate reads them
• A resource figure that says how and where it was measured
• What each assessment does *not* establish

What this shows
---------------

``certify_strategy`` assesses one strategy version -- its fingerprint -- against
what its deployment specification declares, from evidence somebody supplies:
runs, repeated runs, datasets, a reproducibility manifest and its rerun, runtime
observations and resource measurements. Every judgement is derived here through
the authority that owns it. A caller cannot hand over a health report that says
HEALTHY or an assessment that says REPRODUCED; it hands over what was observed.

An application deciding whether to admit, list or compare a strategy reads the
eight assessments. What it decides is its own business, and nothing here knows
who is asking.

Run

    python examples/48_strategy_certification.py
"""

import json
import platform
import time
import tracemalloc
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
    MomentumStrategy,
    banner,
    context_factory,
    ingest_prices,
    run_backtest,
    run_config,
)

from alphalab.api import backtest
from alphalab.broker.state import ConnectionStatus
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.dataset import Dataset
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    BrokerRequirements,
    CapitalPolicy,
    CertificationEvidence,
    CertificationProperty,
    CertificationReport,
    CertificationStatus,
    EngineIdentity,
    LifecycleState,
    MarketRequirements,
    MeasurementBasis,
    ResourceBudget,
    ResourceMeasurement,
    ResourceMetric,
    RuntimeObservation,
    RuntimeRequirements,
    Tolerance,
    certify_strategy,
    code_identity_for,
    dataset_assumption_from,
    fingerprint_for_version,
    get_strategy_version,
    manifest_for_run,
    register_strategy,
    research_configuration,
    specification_for_version,
    verify_certification_report,
)
from alphalab.persistence.serializer import serialize
from alphalab.risk.limits import LeverageLimit
from alphalab.strategy.runtime import create_runtime
from alphalab.strategy.runtime import register_strategy as register_instance
from alphalab.strategy.supervisor import RuntimeSupervisor

ENGINE = EngineIdentity("alphalab", "3.6.0")

BUDGETS = (
    ResourceBudget(ResourceMetric.RECORDS_PROCESSED, Decimal("100")),
    ResourceBudget(ResourceMetric.ORDERS_SUBMITTED, Decimal("10")),
    ResourceBudget(ResourceMetric.FILLS, Decimal("10")),
    ResourceBudget(ResourceMetric.CPU_SECONDS, Decimal("5")),
    ResourceBudget(ResourceMetric.PEAK_MEMORY_BYTES, Decimal("536870912")),
)


def table(report: CertificationReport) -> None:
    for assessment in report.assessments:
        print(f"  {assessment.claim.name:18} {assessment.status.name}")


def measure(dataset: Dataset) -> tuple[ResourceMeasurement, ...]:
    """Measure one backtest here, and say exactly how and where.

    These two figures are true of this machine, this interpreter and this
    moment, and the report says so rather than presenting them as a property of
    the strategy.
    """

    environment = (
        f"{platform.python_implementation()} {platform.python_version()} "
        f"on {platform.system()} {platform.machine()}"
    )
    tracemalloc.start()
    started = time.process_time()
    run_backtest(dataset)
    cpu = time.process_time() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return (
        ResourceMeasurement(
            ResourceMetric.CPU_SECONDS,
            Decimal(str(round(cpu, 6))),
            MeasurementBasis.MEASURED,
            "time.process_time around one BacktestEngine run",
            environment,
        ),
        ResourceMeasurement(
            ResourceMetric.PEAK_MEMORY_BYTES,
            Decimal(peak),
            MeasurementBasis.MEASURED,
            "tracemalloc peak during one BacktestEngine run",
            environment,
        ),
    )


def healthy(observed_at: float) -> RuntimeObservation:
    """A deployment watched at one instant, every category observed and within budget."""

    return RuntimeObservation(
        observed_at=observed_at,
        last_market_data_at=observed_at - 30.0,
        last_heartbeat_at=observed_at - 5.0,
        broker_connection=ConnectionStatus.CONNECTED,
        executions=(),
        observed_positions={ASSET_ID: Decimal("600")},
        expected_positions={ASSET_ID: Decimal("600")},
        risk_violations=(),
        state_expectations=(),
    )


def main() -> None:
    banner(48, "Strategy Certification")

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
            short_selling=False,
            fractional_quantities=False,
        ),
        market=MarketRequirements((ASSET_ID,), ("XNYS",), ("XNYS",), ("USD",)),
        runtime=RuntimeRequirements(
            max_data_staleness_seconds=90_000.0,
            max_heartbeat_silence_seconds=60.0,
            max_execution_latency_seconds=2.0,
            position_tolerance=Tolerance(absolute=Decimal("0")),
        ),
    )

    # ----------------------------------------------------------------- #
    # 1. Nothing supplied
    # ----------------------------------------------------------------- #

    print("\n[1] No evidence at all")
    empty = certify_strategy(fingerprint, specification, CertificationEvidence())
    table(empty)
    print("  (Nothing supplied is NOT_ASSESSED -- never PASS.)")

    # ----------------------------------------------------------------- #
    # 2. Complete evidence
    # ----------------------------------------------------------------- #

    print("\n[2] Everything observed")
    result = run_backtest(dataset)
    manifest = manifest_for_run(result, dataset, fingerprint, ENGINE)
    rerun = manifest_for_run(run_backtest(dataset), dataset, fingerprint, ENGINE)
    measurements = measure(dataset)
    evidence = CertificationEvidence(
        runs=(result,),
        repeated_runs=(result, run_backtest(dataset), run_backtest(dataset)),
        datasets=(dataset,),
        manifest=manifest,
        rerun=rerun,
        observations=(healthy(1_718_200_000.0), healthy(1_718_286_400.0)),
        measurements=measurements,
    )
    report = certify_strategy(fingerprint, specification, evidence, BUDGETS)
    table(report)

    leverage = report.assessment(CertificationProperty.MAX_LEVERAGE)
    risk = report.assessment(CertificationProperty.RISK_LIMITS)
    usage = report.assessment(CertificationProperty.RESOURCE_USAGE)
    print(
        f"\n  leverage   : peaked at {leverage.evidence['observed.peak_leverage']}x, cap "
        f"{leverage.evidence['declared.max_leverage']}x"
    )
    print(
        f"  drawdown   : peaked at {risk.evidence['observed.peak_drawdown_pct']}, limit "
        f"{risk.evidence['declared.drawdown.max_drawdown_pct']}"
    )
    print(
        f"  cpu        : {usage.evidence['observed.CPU_SECONDS']}s "
        f"({usage.evidence['environment.CPU_SECONDS']})"
    )
    print(f"  report id  : {report.report_id}")

    # ----------------------------------------------------------------- #
    # 3. What an assessment does not establish
    # ----------------------------------------------------------------- #

    print("\n[3] Stated every time, even on a PASS")
    for limitation in risk.limitations:
        print(f"  - {limitation[:88]}{'...' if len(limitation) > 88 else ''}")

    # ----------------------------------------------------------------- #
    # 4. FAIL and INSUFFICIENT_EVIDENCE
    # ----------------------------------------------------------------- #

    print("\n[4] The other statuses, each for a reason")

    reused = MomentumStrategy(STRATEGY_ID, DEFINITION.parameters)
    runtime = register_instance(create_runtime(), STRATEGY_ID, reused)
    instance = runtime.strategies[STRATEGY_ID]
    instance, _ = RuntimeSupervisor.configure(instance, {}, 1.0)
    instance, _ = RuntimeSupervisor.initialize(instance, 1.1)
    instance, _ = RuntimeSupervisor.subscribe(instance, frozenset({"bars"}), 1.2)
    instance, _ = RuntimeSupervisor.start(instance, 1.3)
    started = replace(runtime, strategies={STRATEGY_ID: instance})
    twice = tuple(
        backtest(run_config(), dataset, started, context_factory, NORMALIZATION) for _ in range(2)
    )
    stateful = certify_strategy(
        fingerprint, specification, CertificationEvidence(repeated_runs=twice)
    )
    print(
        "  one instance reused across two runs : DETERMINISTIC "
        f"{stateful.status_of(CertificationProperty.DETERMINISTIC).name}"
    )

    tighter = specification_for_version(
        version,
        datasets=specification.datasets,
        risk=replace(LIMITS, leverage=LeverageLimit(Decimal("0.05"))),
        capital=specification.capital,
        broker=specification.broker,
        market=specification.market,
        runtime=specification.runtime,
    )
    capped = certify_strategy(fingerprint, tighter, CertificationEvidence(runs=(result,)))
    print(
        "  a cap tighter than the runs ran at  : MAX_LEVERAGE "
        f"{capped.status_of(CertificationProperty.MAX_LEVERAGE).name}, RISK_LIMITS "
        f"{capped.status_of(CertificationProperty.RISK_LIMITS).name} (gated by other limits)"
    )

    estimate = ResourceMeasurement(
        ResourceMetric.CPU_SECONDS,
        Decimal("0.01"),
        MeasurementBasis.ESTIMATED,
        "records times a per-record cost",
        "",
    )
    estimated = certify_strategy(
        fingerprint,
        specification,
        CertificationEvidence(measurements=(estimate,)),
        (ResourceBudget(ResourceMetric.CPU_SECONDS, Decimal("5")),),
    )
    print(
        "  a CPU budget met only by an estimate: RESOURCE_USAGE "
        f"{estimated.status_of(CertificationProperty.RESOURCE_USAGE).name}"
    )

    stale = replace(healthy(1_718_200_000.0), last_market_data_at=1_718_000_000.0)
    breached = certify_strategy(
        fingerprint, specification, CertificationEvidence(observations=(stale,))
    )
    print(
        "  a feed older than its budget        : RUNTIME_BEHAVIOR "
        f"{breached.status_of(CertificationProperty.RUNTIME_BEHAVIOR).name}"
    )

    # ----------------------------------------------------------------- #
    # 5. Machine-readable
    # ----------------------------------------------------------------- #

    print("\n[5] The report as an application would receive it")
    payload = serialize(report)
    decoded = json.loads(payload)
    print(f"  deterministic JSON, {len(payload)} bytes, {len(decoded['assessments'])} assessments")
    print(f"  first assessment keys: {sorted(decoded['assessments'][0])}")

    # ----------------------------------------------------------------- #

    print("\n[6] Invariants")
    again = certify_strategy(fingerprint, specification, evidence, BUDGETS)
    checks = (
        ("no evidence is never a pass", set(empty.unassessed) == set(CertificationProperty)),
        ("complete evidence earns every property", report.passed == tuple(CertificationProperty)),
        ("identical inputs, identical report", again.report_id == report.report_id),
        ("the report verifies", verify_certification_report(report)),
        (
            "a hidden counter fails determinism",
            stateful.status_of(CertificationProperty.DETERMINISTIC) is CertificationStatus.FAIL,
        ),
        (
            "an estimate never meets a budget",
            estimated.status_of(CertificationProperty.RESOURCE_USAGE)
            is CertificationStatus.INSUFFICIENT_EVIDENCE,
        ),
        ("there is no overall score", "score" not in decoded and "passed" not in decoded),
    )
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAILED'}] {label}")
    assert all(passed for _, passed in checks)

    print("\n" + "=" * 68)
    print("Example 48 complete.")
    print("(Every verdict above was derived from what was observed. The CPU and")
    print(" memory figures are this machine's, and the report says whose they are.)")


if __name__ == "__main__":
    main()
