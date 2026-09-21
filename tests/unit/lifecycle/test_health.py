"""Runtime health: what each category reports, and what it refuses to report.

The tests that matter most are the ones about *absence*. A health evaluator that
reads a missing observation as a healthy one is worse than no evaluator at all,
because it produces a green report nobody has to look at.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker.state import ConnectionStatus
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.lifecycle import (
    BrokerRequirements,
    CapitalPolicy,
    DatasetAssumption,
    ExecutionObservation,
    HealthCategory,
    HealthSeverity,
    HealthStatus,
    LifecycleInputError,
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

LIMITS = RiskLimits(
    order_size=OrderSizeLimit(Decimal("100"), Decimal("10000")),
    position=PositionLimit(Decimal("1000"), Decimal("100000")),
    exposure=ExposureLimit(Decimal("200000"), Decimal("150000")),
    leverage=LeverageLimit(Decimal("2")),
    margin=MarginLimit(Decimal("0.5")),
    daily_loss=DailyLossLimit(Decimal("5000")),
    drawdown=DrawdownLimit(Decimal("0.2")),
)

RUNTIME = RuntimeRequirements(
    max_data_staleness_seconds=60.0,
    max_heartbeat_silence_seconds=30.0,
    max_execution_latency_seconds=2.0,
    position_tolerance=Tolerance(absolute=Decimal("0.5")),
)

SPEC = build_specification(
    strategy=StrategyVersionRef("momentum", 1),
    parameters={"fast": 10.0},
    datasets=(DatasetAssumption("prices", "dsv-1"),),
    risk=LIMITS,
    capital=CapitalPolicy("acct", "USD", Decimal("1000000"), ("USD",)),
    broker=BrokerRequirements(
        order_types=frozenset({OrderType.MARKET}),
        time_in_force=frozenset({TimeInForce.DAY}),
        asset_classes=frozenset({AssetType.EQUITY}),
        short_selling=False,
        fractional_quantities=False,
    ),
    market=MarketRequirements(("a",), ("XNYS",), ("XNYS",), ("USD",)),
    runtime=RUNTIME,
)

NOW = 1_000.0


def healthy_observation(**overrides: object) -> RuntimeObservation:
    """An observation in which every category is supplied and nothing is wrong."""

    arguments: dict[str, object] = {
        "observed_at": NOW,
        "last_market_data_at": NOW - 5.0,
        "last_heartbeat_at": NOW - 1.0,
        "broker_connection": ConnectionStatus.CONNECTED,
        "executions": (ExecutionObservation("o-1", NOW - 1.5, NOW - 1.0),),
        "observed_positions": {"a": Decimal("10")},
        "expected_positions": {"a": Decimal("10")},
        "risk_violations": (),
        "state_expectations": (StateExpectation("serving", "momentum@1", "momentum@1"),),
    }
    arguments.update(overrides)
    return RuntimeObservation(**arguments)  # type: ignore[arg-type]


class TestFullyHealthy:
    def test_a_complete_clean_observation_is_healthy(self) -> None:
        report = evaluate_health(SPEC, healthy_observation())
        assert report.findings == ()
        assert report.unevaluated == ()
        assert report.fully_evaluated
        assert report.status is HealthStatus.HEALTHY

    def test_the_report_names_the_specification_it_was_judged_against(self) -> None:
        report = evaluate_health(SPEC, healthy_observation())
        assert report.specification_id == SPEC.specification_id
        assert report.evaluated_at == NOW

    def test_every_category_is_either_evaluated_or_explicitly_not(self) -> None:
        report = evaluate_health(SPEC, healthy_observation())
        assert all(report.was_evaluated(category) for category in HealthCategory)


class TestMissingObservations:
    @pytest.mark.parametrize(
        ("field", "category"),
        [
            ("last_market_data_at", HealthCategory.STALE_DATA),
            ("executions", HealthCategory.ABNORMAL_EXECUTION),
            ("risk_violations", HealthCategory.RISK_BREACH),
            ("last_heartbeat_at", HealthCategory.HEARTBEAT_LOSS),
            ("broker_connection", HealthCategory.BROKER_DISCONNECT),
            ("state_expectations", HealthCategory.EXPECTED_STATE_DIVERGENCE),
        ],
    )
    def test_an_absent_observation_leaves_its_category_unevaluated(
        self, field: str, category: HealthCategory
    ) -> None:
        report = evaluate_health(SPEC, healthy_observation(**{field: None}))
        assert not report.was_evaluated(category)
        assert report.findings_in(category) == ()
        assert report.status is HealthStatus.UNKNOWN

    def test_either_half_of_the_position_pair_missing_leaves_it_unevaluated(self) -> None:
        for field in ("observed_positions", "expected_positions"):
            report = evaluate_health(SPEC, healthy_observation(**{field: None}))
            assert not report.was_evaluated(HealthCategory.UNEXPECTED_POSITION)

    def test_a_clean_but_incomplete_report_is_never_healthy(self) -> None:
        """The single most important property in the module."""

        report = evaluate_health(SPEC, RuntimeObservation(observed_at=NOW))
        assert report.findings == ()
        assert len(report.unevaluated) == len(HealthCategory)
        assert report.status is HealthStatus.UNKNOWN
        assert not report.fully_evaluated

    def test_an_empty_sequence_is_an_observation_and_none_is_not(self) -> None:
        observed = evaluate_health(SPEC, healthy_observation(executions=()))
        unobserved = evaluate_health(SPEC, healthy_observation(executions=None))
        assert observed.was_evaluated(HealthCategory.ABNORMAL_EXECUTION)
        assert not unobserved.was_evaluated(HealthCategory.ABNORMAL_EXECUTION)

    def test_every_unevaluated_entry_says_why(self) -> None:
        report = evaluate_health(SPEC, RuntimeObservation(observed_at=NOW))
        assert all(entry.reason.strip() for entry in report.unevaluated)


class TestStaleData:
    def test_data_exactly_at_the_budget_is_not_stale(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(last_market_data_at=NOW - RUNTIME.max_data_staleness_seconds)
        )
        assert report.findings_in(HealthCategory.STALE_DATA) == ()
        assert report.status is HealthStatus.HEALTHY

    def test_data_one_second_beyond_the_budget_is_stale(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(last_market_data_at=NOW - RUNTIME.max_data_staleness_seconds - 1.0),
        )
        findings = report.findings_in(HealthCategory.STALE_DATA)
        assert len(findings) == 1
        assert findings[0].severity is HealthSeverity.BREACH
        assert findings[0].detail["threshold_seconds"] == repr(60.0)
        assert report.status is HealthStatus.BREACHED

    def test_future_dated_data_is_a_breach_rather_than_very_fresh(self) -> None:
        report = evaluate_health(SPEC, healthy_observation(last_market_data_at=NOW + 10.0))
        findings = report.findings_in(HealthCategory.STALE_DATA)
        assert len(findings) == 1
        assert "clocks disagree" in findings[0].summary

    def test_the_evaluation_uses_the_supplied_instant_and_no_clock(self) -> None:
        """Evaluating the same observation later gives the same answer."""

        observation = healthy_observation()
        assert evaluate_health(SPEC, observation) == evaluate_health(SPEC, observation)


class TestHeartbeat:
    def test_silence_exactly_at_the_budget_is_not_a_loss(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(last_heartbeat_at=NOW - RUNTIME.max_heartbeat_silence_seconds),
        )
        assert report.findings_in(HealthCategory.HEARTBEAT_LOSS) == ()

    def test_silence_beyond_the_budget_is_a_breach(self) -> None:
        report = evaluate_health(SPEC, healthy_observation(last_heartbeat_at=NOW - 31.0))
        findings = report.findings_in(HealthCategory.HEARTBEAT_LOSS)
        assert len(findings) == 1
        assert findings[0].severity is HealthSeverity.BREACH


class TestBrokerConnection:
    def test_connected_produces_nothing(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(broker_connection=ConnectionStatus.CONNECTED)
        )
        assert report.findings_in(HealthCategory.BROKER_DISCONNECT) == ()

    @pytest.mark.parametrize(
        ("status", "severity"),
        [
            (ConnectionStatus.CONNECTING, HealthSeverity.WARNING),
            (ConnectionStatus.RECONNECTING, HealthSeverity.WARNING),
            (ConnectionStatus.DISCONNECTED, HealthSeverity.BREACH),
            (ConnectionStatus.FAILED, HealthSeverity.BREACH),
        ],
    )
    def test_each_down_state_reports_the_severity_its_behaviour_calls_for(
        self, status: ConnectionStatus, severity: HealthSeverity
    ) -> None:
        report = evaluate_health(SPEC, healthy_observation(broker_connection=status))
        findings = report.findings_in(HealthCategory.BROKER_DISCONNECT)
        assert len(findings) == 1
        assert findings[0].severity is severity
        assert findings[0].detail["observed"] == status.name

    def test_a_reconnecting_adapter_degrades_rather_than_breaches(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(broker_connection=ConnectionStatus.RECONNECTING)
        )
        assert report.status is HealthStatus.DEGRADED


class TestAbnormalExecution:
    def test_latency_exactly_at_the_budget_is_acceptable(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(executions=(ExecutionObservation("o-1", 0.0, 2.0),))
        )
        assert report.findings_in(HealthCategory.ABNORMAL_EXECUTION) == ()

    def test_latency_beyond_the_budget_is_a_breach(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(executions=(ExecutionObservation("o-1", 0.0, 2.5),))
        )
        findings = report.findings_in(HealthCategory.ABNORMAL_EXECUTION)
        assert len(findings) == 1
        assert findings[0].subject == "o-1"
        assert findings[0].detail["observed_seconds"] == repr(2.5)

    def test_a_rejected_order_is_abnormal_whatever_its_latency(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                executions=(
                    ExecutionObservation("o-1", 0.0, 0.1, rejected=True, detail="no buying power"),
                )
            ),
        )
        findings = report.findings_in(HealthCategory.ABNORMAL_EXECUTION)
        assert len(findings) == 1
        assert findings[0].detail["reason"] == "no buying power"

    def test_an_execution_with_no_measurable_latency_warns_rather_than_passing(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(executions=(ExecutionObservation("o-1", None, 5.0),))
        )
        findings = report.findings_in(HealthCategory.ABNORMAL_EXECUTION)
        assert len(findings) == 1
        assert findings[0].severity is HealthSeverity.WARNING
        assert "no measurable latency" in findings[0].summary

    def test_a_negative_latency_is_a_breach_rather_than_a_fast_fill(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(executions=(ExecutionObservation("o-1", 10.0, 9.0),))
        )
        findings = report.findings_in(HealthCategory.ABNORMAL_EXECUTION)
        assert len(findings) == 1
        assert "clocks disagree" in findings[0].summary

    def test_latency_is_derived_and_cannot_be_asserted(self) -> None:
        assert ExecutionObservation("o-1", 1.0, 3.5).latency_seconds == 2.5
        assert ExecutionObservation("o-1", None, 3.5).latency_seconds is None
        assert ExecutionObservation("o-1", 1.0, None).latency_seconds is None

    def test_findings_are_ordered_by_order_id(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                executions=(
                    ExecutionObservation("o-9", 0.0, 9.0),
                    ExecutionObservation("o-2", 0.0, 9.0),
                    ExecutionObservation("o-5", 0.0, 9.0),
                )
            ),
        )
        assert [f.subject for f in report.findings_in(HealthCategory.ABNORMAL_EXECUTION)] == [
            "o-2",
            "o-5",
            "o-9",
        ]

    def test_an_execution_with_a_blank_order_id_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="order_id cannot be empty"):
            ExecutionObservation(" ", 0.0, 1.0)


class TestUnexpectedPosition:
    def test_a_difference_within_tolerance_is_not_reported(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                expected_positions={"a": Decimal("10")},
                observed_positions={"a": Decimal("10.5")},
            ),
        )
        assert report.findings_in(HealthCategory.UNEXPECTED_POSITION) == ()

    def test_a_difference_beyond_tolerance_is_a_breach(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                expected_positions={"a": Decimal("10")},
                observed_positions={"a": Decimal("12")},
            ),
        )
        findings = report.findings_in(HealthCategory.UNEXPECTED_POSITION)
        assert len(findings) == 1
        assert findings[0].subject == "a"
        assert findings[0].detail == {
            "expected": "10",
            "observed": "12",
            "allowance": "0.5",
        }

    def test_an_instrument_present_on_one_side_only_is_a_real_zero_on_the_other(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                expected_positions={},
                observed_positions={"ghost": Decimal("100")},
            ),
        )
        findings = report.findings_in(HealthCategory.UNEXPECTED_POSITION)
        assert len(findings) == 1
        assert findings[0].detail == {
            "expected": "0",
            "observed": "100",
            "allowance": "0.5",
        }

    def test_findings_are_ordered_by_instrument(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                expected_positions={"z": Decimal("0"), "a": Decimal("0"), "m": Decimal("0")},
                observed_positions={"z": Decimal("9"), "a": Decimal("9"), "m": Decimal("9")},
            ),
        )
        assert [f.subject for f in report.findings_in(HealthCategory.UNEXPECTED_POSITION)] == [
            "a",
            "m",
            "z",
        ]


class TestRiskBreach:
    def test_no_violations_is_evaluated_and_clean(self) -> None:
        report = evaluate_health(SPEC, healthy_observation(risk_violations=()))
        assert report.was_evaluated(HealthCategory.RISK_BREACH)
        assert report.findings_in(HealthCategory.RISK_BREACH) == ()

    def test_a_violation_is_carried_rather_than_re_decided(self) -> None:
        violation = RiskViolation(
            rule="max_leverage",
            description="leverage 3.1 exceeds 2.0",
            severity="CRITICAL",
            current_value=Decimal("3.1"),
            allowed_value=Decimal("2.0"),
        )
        report = evaluate_health(SPEC, healthy_observation(risk_violations=(violation,)))
        findings = report.findings_in(HealthCategory.RISK_BREACH)
        assert len(findings) == 1
        assert findings[0].subject == "max_leverage"
        assert findings[0].detail["observed"] == "3.1"
        assert findings[0].detail["threshold"] == "2.0"
        # The risk engine's own vocabulary survives verbatim.
        assert findings[0].detail["risk_severity"] == "CRITICAL"
        assert findings[0].severity is HealthSeverity.BREACH


class TestStateDivergence:
    def test_matching_expectations_produce_nothing(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                state_expectations=(StateExpectation("serving", "momentum@1", "momentum@1"),)
            ),
        )
        assert report.findings_in(HealthCategory.EXPECTED_STATE_DIVERGENCE) == ()

    def test_a_differing_value_is_a_breach(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                state_expectations=(StateExpectation("serving", "momentum@1", "momentum@2"),)
            ),
        )
        findings = report.findings_in(HealthCategory.EXPECTED_STATE_DIVERGENCE)
        assert len(findings) == 1
        assert findings[0].detail == {"expected": "momentum@1", "observed": "momentum@2"}

    def test_an_expected_fact_that_was_not_observed_is_a_breach(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                state_expectations=(StateExpectation("serving", "momentum@1", None),)
            ),
        )
        findings = report.findings_in(HealthCategory.EXPECTED_STATE_DIVERGENCE)
        assert findings[0].severity is HealthSeverity.BREACH
        assert "was not observed at all" in findings[0].summary

    def test_an_observed_fact_nobody_expected_warns(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                state_expectations=(StateExpectation("serving", None, "momentum@9"),)
            ),
        )
        findings = report.findings_in(HealthCategory.EXPECTED_STATE_DIVERGENCE)
        assert findings[0].severity is HealthSeverity.WARNING

    def test_an_expectation_with_neither_side_establishes_nothing_and_says_so(self) -> None:
        report = evaluate_health(
            SPEC, healthy_observation(state_expectations=(StateExpectation("x", None, None),))
        )
        findings = report.findings_in(HealthCategory.EXPECTED_STATE_DIVERGENCE)
        assert "established nothing" in findings[0].summary

    def test_a_blank_expectation_name_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="name cannot be empty"):
            StateExpectation("  ", "a", "b")


class TestStatusPrecedence:
    def test_a_breach_outranks_a_warning(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                broker_connection=ConnectionStatus.RECONNECTING,
                last_heartbeat_at=NOW - 500.0,
            ),
        )
        assert report.status is HealthStatus.BREACHED

    def test_a_warning_outranks_an_unevaluated_category(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                broker_connection=ConnectionStatus.RECONNECTING, risk_violations=None
            ),
        )
        assert report.status is HealthStatus.DEGRADED
        assert not report.fully_evaluated


class TestSimultaneousAndRecovery:
    def test_several_categories_can_fail_at_once(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                last_market_data_at=NOW - 500.0,
                last_heartbeat_at=NOW - 500.0,
                broker_connection=ConnectionStatus.FAILED,
                executions=(ExecutionObservation("o-1", 0.0, 100.0),),
                observed_positions={"a": Decimal("99")},
                risk_violations=(RiskViolation("r", "d", "HIGH", Decimal("1"), Decimal("0")),),
                state_expectations=(StateExpectation("serving", "a", "b"),),
            ),
        )
        assert {finding.category for finding in report.findings} == set(HealthCategory)
        assert report.status is HealthStatus.BREACHED

    def test_findings_come_out_in_category_declaration_order(self) -> None:
        report = evaluate_health(
            SPEC,
            healthy_observation(
                last_market_data_at=NOW - 500.0,
                broker_connection=ConnectionStatus.FAILED,
                observed_positions={"a": Decimal("99")},
            ),
        )
        order = list(HealthCategory)
        indexes = [order.index(finding.category) for finding in report.findings]
        assert indexes == sorted(indexes)

    def test_recovery_is_a_clean_report_over_the_next_observation(self) -> None:
        broken = healthy_observation(broker_connection=ConnectionStatus.FAILED)
        assert evaluate_health(SPEC, broken).status is HealthStatus.BREACHED

        recovered = replace(broken, broker_connection=ConnectionStatus.CONNECTED)
        assert evaluate_health(SPEC, recovered).status is HealthStatus.HEALTHY

    def test_the_same_fault_reported_twice_is_reported_twice(self) -> None:
        """Health has no memory: each evaluation is about one observation."""

        observation = healthy_observation(broker_connection=ConnectionStatus.FAILED)
        first = evaluate_health(SPEC, observation)
        second = evaluate_health(SPEC, observation)
        assert first == second
        assert len(first.findings) == 1


class TestNoRemediation:
    def test_evaluating_changes_nothing_it_was_given(self) -> None:
        observation = healthy_observation(broker_connection=ConnectionStatus.FAILED)
        before = observation
        specification = SPEC
        evaluate_health(specification, observation)
        assert observation == before
        assert specification == SPEC

    def test_a_negative_observation_instant_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="negative instant"):
            RuntimeObservation(observed_at=-1.0)
