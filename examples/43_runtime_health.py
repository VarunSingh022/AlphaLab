"""
AlphaLab Examples
=================

Example 43 : Runtime Health

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 42 (deployment specifications)

Topics
------

• Seven things that can be wrong with a running deployment
• Observations that were supplied, and observations that were not
• Why a clean report with something unevaluated is UNKNOWN and not HEALTHY
• Exactly-at-the-threshold, past it, and dated in the future
• Severity: a reconnecting adapter warns, a dead one breaches
• Detection is not remediation

What this shows
---------------

`runtime.live.live_health` has answered a version of "should a human look at
this?" since v2.16, in plain sentences. Sentences are right for a console and
wrong for everything else: nothing can count them by kind, act on the severe
ones, or say *what number* was wrong and *what number* would have been
acceptable. It also reads a `LiveRunState` directly, so it can only speak about
a run this process is driving, and it knows no thresholds because a run does not
carry any.

`evaluate_health` is the structured half: **supplied observations** judged
against the **runtime requirements a deployment specification declares**. Every
category is either evaluated or explicitly reported as unevaluated, so a reader
never has to wonder whether a check ran.

Every number below is declared in this file. AlphaLab observes nothing on its
own, and this example reaches no venue and no feed.

Run

    python examples/43_runtime_health.py
"""

from dataclasses import replace
from decimal import Decimal

from alphalab.broker.state import ConnectionStatus
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.lifecycle import (
    BrokerRequirements,
    CapitalPolicy,
    DatasetAssumption,
    ExecutionObservation,
    HealthCategory,
    HealthReport,
    HealthStatus,
    MarketRequirements,
    RuntimeObservation,
    RuntimeRequirements,
    StateExpectation,
    StrategyVersionRef,
    Tolerance,
    build_specification,
    evaluate_health,
)
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.risk.models import RiskViolation

#: The instant every observation below is *for*. Supplied, never read from a
#: clock: a health report that cannot be re-evaluated to the same answer a year
#: later is not evidence of anything.
NOW = 1_700_000_000.0

SPECIFICATION = build_specification(
    strategy=StrategyVersionRef("momentum", 4),
    parameters={"fast": 10.0, "slow": 30.0},
    datasets=(DatasetAssumption("prices", "alphalab.dataset.v1:example-43"),),
    risk=RiskLimits(
        order_size=OrderSizeLimit(Decimal("500"), Decimal("50000")),
        position=PositionLimit(Decimal("5000"), Decimal("500000")),
        exposure=ExposureLimit(Decimal("1500000"), Decimal("900000")),
        leverage=LeverageLimit(Decimal("2")),
        margin=MarginLimit(Decimal("0.5")),
        daily_loss=DailyLossLimit(Decimal("25000")),
        drawdown=DrawdownLimit(Decimal("0.15")),
    ),
    capital=CapitalPolicy("ACC-EU-EQUITY", "USD", Decimal("1000000"), ("USD",)),
    broker=BrokerRequirements(
        order_types=frozenset({OrderType.MARKET}),
        time_in_force=frozenset({TimeInForce.DAY}),
        asset_classes=frozenset({AssetType.EQUITY}),
        short_selling=True,
        fractional_quantities=False,
    ),
    market=MarketRequirements(("EXA",), ("XNYS",), ("XNYS",), ("USD",)),
    runtime=RuntimeRequirements(
        max_data_staleness_seconds=60.0,
        max_heartbeat_silence_seconds=30.0,
        max_execution_latency_seconds=2.0,
        position_tolerance=Tolerance(absolute=Decimal("0.5")),
    ),
)

BUDGETS = SPECIFICATION.runtime

#: Everything observed, and nothing wrong. Each field is a reading somebody took.
NOMINAL = RuntimeObservation(
    observed_at=NOW,
    last_market_data_at=NOW - 5.0,
    last_heartbeat_at=NOW - 2.0,
    broker_connection=ConnectionStatus.CONNECTED,
    executions=(
        ExecutionObservation("ord-001", NOW - 12.0, NOW - 11.4),
        ExecutionObservation("ord-002", NOW - 8.0, NOW - 7.5),
    ),
    observed_positions={"EXA": Decimal("1200"), "EXB": Decimal("-300")},
    expected_positions={"EXA": Decimal("1200"), "EXB": Decimal("-300")},
    risk_violations=(),
    state_expectations=(StateExpectation("serving", "momentum@4", "momentum@4"),),
)


def report_line(report: HealthReport) -> None:
    print(
        f"  status: {report.status.name:<10} findings: {len(report.findings):<3} "
        f"unevaluated: {len(report.unevaluated)}"
    )


def show(report: HealthReport) -> None:
    report_line(report)
    for finding in report.findings:
        subject = f" [{finding.subject}]" if finding.subject else ""
        print(f"    {finding.severity.name:<8} {finding.category.name}{subject}")
        print(f"             {finding.summary}")
    for entry in report.unevaluated:
        print(f"    (not evaluated) {entry.category.name}: {entry.reason}")


def main() -> None:
    print("=" * 68)
    print("AlphaLab Example 43 : Runtime Health")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    # 1. Everything observed, nothing wrong
    # ----------------------------------------------------------------- #

    print("\n[1] A complete, clean observation")
    show(evaluate_health(SPECIFICATION, NOMINAL))
    print("  (HEALTHY means every one of the seven was actually judged.)")

    # ----------------------------------------------------------------- #
    # 2. The property the whole module is built around
    # ----------------------------------------------------------------- #

    print("\n[2] Nothing observed at all")
    show(evaluate_health(SPECIFICATION, RuntimeObservation(observed_at=NOW)))
    print("  UNKNOWN, not HEALTHY: nobody looked, and an absent reading is not")
    print("  a passing one. There is no way to spell 'missing means fine'.")

    # ----------------------------------------------------------------- #
    # 3. Thresholds
    # ----------------------------------------------------------------- #

    print("\n[3] Data freshness, at the budget and past it")
    for age in (BUDGETS.max_data_staleness_seconds, BUDGETS.max_data_staleness_seconds + 1.0):
        report = evaluate_health(SPECIFICATION, replace(NOMINAL, last_market_data_at=NOW - age))
        verdict = "stale" if report.findings_in(HealthCategory.STALE_DATA) else "acceptable"
        print(
            f"  {age:>5.1f}s old against a {BUDGETS.max_data_staleness_seconds:.0f}s "
            f"budget -> {verdict}"
        )
    print("  (Exactly at the budget is inside it. A sixty-second budget that")
    print("   refuses a sixty-second-old reading is a fifty-nine-second budget.)")

    print("\n  Data stamped after the observation instant:")
    show(evaluate_health(SPECIFICATION, replace(NOMINAL, last_market_data_at=NOW + 10.0)))

    # ----------------------------------------------------------------- #
    # 4. Severity
    # ----------------------------------------------------------------- #

    print("\n[4] The connection, in each state an adapter can report")
    for status in ConnectionStatus:
        report = evaluate_health(SPECIFICATION, replace(NOMINAL, broker_connection=status))
        findings = report.findings_in(HealthCategory.BROKER_DISCONNECT)
        severity = findings[0].severity.name if findings else "-"
        print(
            f"  {status.name:<14} can_trade={status.can_trade!s:<5} "
            f"finding={severity:<8} status={report.status.name}"
        )
    print("  (Reconnecting warns because the adapter has not given up; failed")
    print("   breaches. The broker layer already draws that distinction.)")

    # ----------------------------------------------------------------- #
    # 5. Executions
    # ----------------------------------------------------------------- #

    print("\n[5] Executions")
    slow = replace(
        NOMINAL,
        executions=(
            ExecutionObservation("ord-003", NOW - 10.0, NOW - 3.0),
            ExecutionObservation("ord-004", NOW - 10.0, None),
            ExecutionObservation(
                "ord-005", NOW - 10.0, NOW - 9.0, rejected=True, detail="insufficient buying power"
            ),
        ),
    )
    show(evaluate_health(SPECIFICATION, slow))
    print("  (ord-004 has no measurable latency and is reported as such --")
    print("   never as zero, and never silently passed.)")

    # ----------------------------------------------------------------- #
    # 6. Positions and risk
    # ----------------------------------------------------------------- #

    print("\n[6] A position the book did not expect, and a breached limit")
    drifted = replace(
        NOMINAL,
        observed_positions={"EXA": Decimal("1200"), "EXB": Decimal("-300"), "EXC": Decimal("900")},
        risk_violations=(
            RiskViolation(
                rule="max_leverage",
                description="gross exposure 2.4x against a 2.0x cap",
                severity="CRITICAL",
                current_value=Decimal("2.4"),
                allowed_value=Decimal("2.0"),
            ),
        ),
    )
    show(evaluate_health(SPECIFICATION, drifted))
    for finding in evaluate_health(SPECIFICATION, drifted).findings:
        if finding.detail:
            print(f"    machine-readable: {dict(finding.detail)}")

    # ----------------------------------------------------------------- #
    # 7. Simultaneous faults, and recovery
    # ----------------------------------------------------------------- #

    print("\n[7] Several faults at once")
    incident = replace(
        NOMINAL,
        last_market_data_at=NOW - 900.0,
        last_heartbeat_at=NOW - 900.0,
        broker_connection=ConnectionStatus.FAILED,
        state_expectations=(StateExpectation("serving", "momentum@4", "momentum@3"),),
    )
    report_line(evaluate_health(SPECIFICATION, incident))
    print(
        f"  categories affected: "
        f"{sorted({f.category.name for f in evaluate_health(SPECIFICATION, incident).findings})}"
    )

    print("\n  The next observation, after the feed and the venue came back:")
    report_line(evaluate_health(SPECIFICATION, NOMINAL))
    print("  (Health has no memory: each evaluation is about one observation,")
    print("   so recovery is simply a clean report over the next one.)")

    # ----------------------------------------------------------------- #

    print("\n[8] Invariants")
    nominal = evaluate_health(SPECIFICATION, NOMINAL)
    empty = evaluate_health(SPECIFICATION, RuntimeObservation(observed_at=NOW))
    breached = evaluate_health(SPECIFICATION, incident)
    checks = (
        ("a complete clean observation is HEALTHY", nominal.status is HealthStatus.HEALTHY),
        ("an empty observation is UNKNOWN", empty.status is HealthStatus.UNKNOWN),
        ("and it produces no findings at all", empty.findings == ()),
        ("every category is accounted for", len(empty.unevaluated) == len(HealthCategory)),
        ("an incident is BREACHED", breached.status is HealthStatus.BREACHED),
        (
            "the report names the specification it was judged against",
            nominal.specification_id == SPECIFICATION.specification_id,
        ),
        (
            "the same observation evaluates identically",
            evaluate_health(SPECIFICATION, NOMINAL) == nominal,
        ),
        ("nothing was remediated", NOMINAL.broker_connection is ConnectionStatus.CONNECTED),
    )
    for label, held in checks:
        print(f"  [{'ok' if held else 'FAILED'}] {label}")
    assert all(held for _, held in checks)

    print("\n" + "=" * 68)
    print("Example 43 complete.")
    print("(Nothing here cancelled an order, reconnected an adapter or changed")
    print(" any state. A finding is a fact for a caller to act on.)")


if __name__ == "__main__":
    main()
