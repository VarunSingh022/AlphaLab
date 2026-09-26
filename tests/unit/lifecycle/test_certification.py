"""Strategy certification: every property, every status, from real evidence.

Each property is driven through the statuses it can take with runs that actually
happened -- the execution path, the pre-trade gate, the health evaluator -- so a
``PASS`` here is a pass a real strategy earned and a ``FAIL`` is one it caused.
The rules that matter most are the ones about what is *not* a pass: nothing
supplied is ``NOT_ASSESSED``, something that cannot settle the question is
``INSUFFICIENT_EVIDENCE``, and neither is ever ``PASS``.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.api import backtest
from alphalab.broker.state import ConnectionStatus
from alphalab.data.dataset import Dataset
from alphalab.lifecycle import (
    CertificationEvidence,
    CertificationProperty,
    CertificationReport,
    CertificationStatus,
    CodeIdentity,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    LifecycleInputError,
    MarketRequirements,
    MeasurementBasis,
    ResourceBudget,
    ResourceMeasurement,
    ResourceMetric,
    RuntimeObservation,
    StrategyLifecycleStage,
    advance_progression,
    begin_progression,
    certify_strategy,
    manifest_for_run,
    resource_counts,
    verify_certification_report,
)
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.risk.limits import DrawdownLimit, ExposureLimit, LeverageLimit, OrderSizeLimit
from alphalab.studio.strategy import StrategyDefinition
from tests.integration.harness import context_factory, running_strategy_state
from tests.unit.lifecycle.evidence_harness import (
    ASSET_ID,
    CODE,
    DEFINITION,
    ENGINE,
    NORMALIZATION,
    RISK,
    SEED,
    STRATEGY_ID,
    VERSION,
    BarStrategy,
    fingerprint,
    ingest,
    run_backtest,
    run_config,
    specification,
)

P = CertificationProperty
S = CertificationStatus


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return ingest()


def certify(
    dataset: Dataset,
    evidence: CertificationEvidence,
    budgets: tuple[ResourceBudget, ...] = (),
    **specification_overrides: object,
) -> CertificationReport:
    spec = specification(dataset, **specification_overrides)  # type: ignore[arg-type]
    return certify_strategy(fingerprint(), spec, evidence, budgets)


def healthy(observed_at: float = 1_000.0) -> RuntimeObservation:
    """An observation of every category, all of it within budget."""

    return RuntimeObservation(
        observed_at=observed_at,
        last_market_data_at=observed_at - 10.0,
        last_heartbeat_at=observed_at - 1.0,
        broker_connection=ConnectionStatus.CONNECTED,
        executions=(),
        observed_positions={ASSET_ID: Decimal("6")},
        expected_positions={ASSET_ID: Decimal("6")},
        risk_violations=(),
        state_expectations=(),
    )


# --------------------------------------------------------------------------- #
# The report as a whole
# --------------------------------------------------------------------------- #


def test_with_no_evidence_every_property_is_not_assessed(dataset: Dataset) -> None:
    report = certify(dataset, CertificationEvidence())

    assert [item.claim for item in report.assessments] == list(CertificationProperty)
    assert report.unassessed == tuple(CertificationProperty)
    assert report.passed == ()
    assert verify_certification_report(report)


def test_every_assessment_states_a_methodology_and_its_limits(dataset: Dataset) -> None:
    report = certify(dataset, CertificationEvidence(runs=(run_backtest(dataset),)))

    for assessment in report.assessments:
        assert assessment.methodology.strip()
        assert all(finding.strip() for finding in assessment.findings)
    for claim in (P.RISK_LIMITS, P.MAX_LEVERAGE, P.SUPPORTED_MARKETS):
        assert report.assessment(claim).limitations


def test_a_report_is_deterministic_and_tamper_evident(dataset: Dataset) -> None:
    result = run_backtest(dataset)
    evidence = CertificationEvidence(runs=(result,), repeated_runs=(result, run_backtest(dataset)))

    first = certify(dataset, evidence)
    second = certify(dataset, evidence)

    assert first == second
    assert first.report_id == second.report_id
    edited = replace(
        first,
        assessments=tuple(
            replace(item, status=S.PASS) if item.claim is P.REPRODUCIBLE else item
            for item in first.assessments
        ),
    )
    assert not verify_certification_report(edited)
    assert not verify_certification_report(replace(first, assessments=first.assessments[:-1]))


def test_there_is_no_overall_score(dataset: Dataset) -> None:
    fields = set(CertificationReport.__dataclass_fields__)

    assert not {"score", "overall", "grade", "certified", "passed_all"} & fields


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_an_altered_fingerprint_or_specification_is_refused(dataset: Dataset) -> None:
    spec = specification(dataset)

    with pytest.raises(LifecycleInputError, match="altered"):
        certify_strategy(
            replace(fingerprint(), parameters={"entry": 1.0}), spec, CertificationEvidence()
        )
    with pytest.raises(LifecycleInputError, match="altered"):
        certify_strategy(
            fingerprint(), replace(spec, parameters={"entry": 1.0}), CertificationEvidence()
        )


def test_a_specification_for_other_parameters_is_refused(dataset: Dataset) -> None:
    """Certifying the pair would attach one strategy's limits to another's logic."""

    retuned = replace(VERSION, definition=replace(DEFINITION, parameters={"entry": 12.0}))
    spelled = replace(
        VERSION, definition=replace(DEFINITION, parameters={"entry": 10, "exit": -4.0})
    )

    for version in (retuned, spelled):
        with pytest.raises(LifecycleInputError, match="parameters"):
            certify_strategy(
                fingerprint(), specification(dataset, version=version), CertificationEvidence()
            )


def test_a_specification_for_another_line_is_refused(dataset: Dataset) -> None:
    other = replace(VERSION, name="other-line")

    with pytest.raises(LifecycleInputError, match="strategy line"):
        certify_strategy(
            fingerprint(), specification(dataset, version=other), CertificationEvidence()
        )


def test_a_progression_of_another_version_is_refused(dataset: Dataset) -> None:
    from alphalab.lifecycle import StrategyVersionRef

    with pytest.raises(LifecycleInputError, match="progression"):
        certify_strategy(
            fingerprint(),
            specification(dataset),
            CertificationEvidence(),
            progression=begin_progression(StrategyVersionRef(VERSION.name, 2)),
        )


def test_a_supplied_count_is_a_claim_and_is_refused(dataset: Dataset) -> None:
    claimed = ResourceMeasurement(
        ResourceMetric.FILLS, Decimal("0"), MeasurementBasis.COUNTED, "trust me", ""
    )

    with pytest.raises(LifecycleInputError, match="COUNTED"):
        certify(dataset, CertificationEvidence(measurements=(claimed,)))


# --------------------------------------------------------------------------- #
# DETERMINISTIC
# --------------------------------------------------------------------------- #


def test_repeated_seeded_executions_with_one_record_pass(dataset: Dataset) -> None:
    runs = (run_backtest(dataset), run_backtest(dataset), run_backtest(dataset))

    assessment = certify(dataset, CertificationEvidence(repeated_runs=runs)).assessment(
        P.DETERMINISTIC
    )

    assert assessment.status is S.PASS
    assert assessment.evidence["executions"] == "3"
    assert assessment.evidence["distinct_records"] == "1"


def test_one_execution_is_insufficient(dataset: Dataset) -> None:
    assessment = certify(
        dataset, CertificationEvidence(repeated_runs=(run_backtest(dataset),))
    ).assessment(P.DETERMINISTIC)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


def test_unseeded_executions_are_insufficient_not_failed(dataset: Dataset) -> None:
    runs = (run_backtest(dataset, seed=None), run_backtest(dataset, seed=None))

    assessment = certify(dataset, CertificationEvidence(repeated_runs=runs)).assessment(
        P.DETERMINISTIC
    )

    assert assessment.status is S.INSUFFICIENT_EVIDENCE
    assert any("unseeded" in finding for finding in assessment.findings)


def test_executions_of_different_inputs_say_nothing_about_determinism(dataset: Dataset) -> None:
    runs = (run_backtest(dataset), run_backtest(dataset, seed=1))

    assessment = certify(dataset, CertificationEvidence(repeated_runs=runs)).assessment(
        P.DETERMINISTIC
    )

    assert assessment.status is S.INSUFFICIENT_EVIDENCE
    assert any("identical recorded inputs" in finding for finding in assessment.findings)


def test_a_strategy_that_carries_state_between_runs_fails(dataset: Dataset) -> None:
    """The same inputs, the same instance reused: its hidden counter makes run two differ."""

    strategy = BarStrategy(STRATEGY_ID, ASSET_ID, {1: Decimal("10")})

    def once() -> object:
        return backtest(
            run_config(),
            dataset,
            running_strategy_state(STRATEGY_ID, strategy),
            context_factory,
            NORMALIZATION,
        )

    first, second = once(), once()
    assessment = certify(
        dataset,
        CertificationEvidence(repeated_runs=(first, second)),  # type: ignore[arg-type]
    ).assessment(P.DETERMINISTIC)

    assert assessment.status is S.FAIL
    assert assessment.evidence["distinct_records"] == "2"


def test_a_shared_book_is_not_this_strategys_determinism(dataset: Dataset) -> None:
    other = run_backtest(dataset, strategy_id="SOMEONE-ELSE")

    assessment = certify(dataset, CertificationEvidence(repeated_runs=(other, other))).assessment(
        P.DETERMINISTIC
    )

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


# --------------------------------------------------------------------------- #
# REPRODUCIBLE
# --------------------------------------------------------------------------- #


def test_a_reproduced_complete_manifest_passes(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    rerun = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assessment = certify(dataset, CertificationEvidence(manifest=manifest, rerun=rerun)).assessment(
        P.REPRODUCIBLE
    )

    assert assessment.status is S.PASS
    assert assessment.evidence["rerun"] == "REPRODUCED"
    assert "DATASET_BYTES" in assessment.evidence["external_inputs"]
    assert any("AlphaLab does not hold" in limit for limit in assessment.limitations)


def test_an_identity_alone_is_not_reproduction(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assessment = certify(dataset, CertificationEvidence(manifest=manifest)).assessment(
        P.REPRODUCIBLE
    )

    assert assessment.status is S.INSUFFICIENT_EVIDENCE
    assert any("no rerun" in finding for finding in assessment.findings)


def test_a_reproduction_resting_on_approximate_provenance_is_insufficient(
    dataset: Dataset,
) -> None:
    direct = DependencyManifest(
        DependencyCompleteness.DIRECT_ONLY, (DependencyPin("numpy", "1.26.4"),)
    )
    fp = fingerprint(dependencies=direct)
    manifest = manifest_for_run(run_backtest(dataset), dataset, fp, ENGINE)
    rerun = manifest_for_run(run_backtest(dataset), dataset, fp, ENGINE)
    spec = specification(dataset)

    report = certify_strategy(fp, spec, CertificationEvidence(manifest=manifest, rerun=rerun))
    assessment = report.assessment(P.REPRODUCIBLE)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE
    assert any("DIRECT_ONLY" in finding for finding in assessment.findings)


def test_a_divergent_rerun_fails(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    diverged = manifest_for_run(
        run_backtest(dataset, plan={1: Decimal("5")}), dataset, fingerprint(), ENGINE
    )

    assessment = certify(
        dataset, CertificationEvidence(manifest=manifest, rerun=diverged)
    ).assessment(P.REPRODUCIBLE)

    assert assessment.status is S.FAIL


def test_a_manifest_of_another_strategy_version_is_insufficient(dataset: Dataset) -> None:
    other = fingerprint(code=replace(CODE, version="9.9.9"))
    manifest = manifest_for_run(run_backtest(dataset), dataset, other, ENGINE)

    assessment = certify(dataset, CertificationEvidence(manifest=manifest)).assessment(
        P.REPRODUCIBLE
    )

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


def test_an_altered_manifest_fails_identity(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assessment = certify(
        dataset, CertificationEvidence(manifest=replace(manifest, result_id="0" * 64))
    ).assessment(P.REPRODUCIBLE)

    assert assessment.status is S.FAIL


def test_an_altered_manifest_with_a_rerun_still_fails_identity(dataset: Dataset) -> None:
    """A rerun is never compared with a record that was altered to match it."""

    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    diverged = manifest_for_run(
        run_backtest(dataset, plan={1: Decimal("5")}), dataset, fingerprint(), ENGINE
    )
    doctored = replace(manifest, result_id=diverged.result_id)

    assessment = certify(
        dataset, CertificationEvidence(manifest=doctored, rerun=diverged)
    ).assessment(P.REPRODUCIBLE)

    assert assessment.status is S.FAIL
    assert assessment.evidence["rerun"] == "NOT_ATTEMPTED"
    assert any("not compared" in finding for finding in assessment.findings)


def test_an_altered_rerun_manifest_is_insufficient_not_raised(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    rerun = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assessment = certify(
        dataset, CertificationEvidence(manifest=manifest, rerun=replace(rerun, seed=SEED + 1))
    ).assessment(P.REPRODUCIBLE)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE
    assert assessment.evidence["rerun_verified"] == "False"
    assert any("establishes nothing" in finding for finding in assessment.findings)


# --------------------------------------------------------------------------- #
# RISK_LIMITS
# --------------------------------------------------------------------------- #


def test_runs_inside_their_declared_limits_pass(dataset: Dataset) -> None:
    assessment = certify(dataset, CertificationEvidence(runs=(run_backtest(dataset),))).assessment(
        P.RISK_LIMITS
    )

    assert assessment.status is S.PASS
    assert assessment.evidence["risk_decisions"] == "2"
    assert assessment.evidence["refusals"] == ""
    assert any("max_daily_loss is not assessed" in limit for limit in assessment.limitations)
    assert any("max_net_exposure is not assessed" in limit for limit in assessment.limitations)


def test_a_run_gated_by_other_limits_is_not_evidence_about_these(dataset: Dataset) -> None:
    looser = replace(RISK, leverage=LeverageLimit(Decimal("5000")))

    assessment = certify(
        dataset, CertificationEvidence(runs=(run_backtest(dataset, risk_limits=looser),))
    ).assessment(P.RISK_LIMITS)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


def test_orders_the_gate_refused_fail_the_limits(dataset: Dataset) -> None:
    tight = replace(RISK, order_size=OrderSizeLimit(Decimal("5"), Decimal("100000000")))

    assessment = certify(
        dataset,
        CertificationEvidence(runs=(run_backtest(dataset, risk_limits=tight),)),
        risk=tight,
    ).assessment(P.RISK_LIMITS)

    assert assessment.status is S.FAIL
    assert "OrderSizeQuantity" in assessment.evidence["refusals"]


def test_a_drawdown_past_the_limit_fails_without_any_refusal(dataset: Dataset) -> None:
    """The gate only sees drawdown when an order is asked for; this one never is."""

    shallow = replace(RISK, drawdown=DrawdownLimit(Decimal("0.001")))
    result = run_backtest(dataset, plan={1: Decimal("5000")}, risk_limits=shallow)

    assessment = certify(dataset, CertificationEvidence(runs=(result,)), risk=shallow).assessment(
        P.RISK_LIMITS
    )

    assert assessment.status is S.FAIL
    assert assessment.evidence["refusals"] == ""
    assert Decimal(assessment.evidence["observed.peak_drawdown_pct"]) > Decimal("0.001")


def test_gross_exposure_that_grows_past_the_limit_between_orders_fails(dataset: Dataset) -> None:
    capped = replace(RISK, exposure=ExposureLimit(Decimal("1030"), Decimal("1030")))
    result = run_backtest(dataset, plan={1: Decimal("10")}, risk_limits=capped)

    assessment = certify(dataset, CertificationEvidence(runs=(result,)), risk=capped).assessment(
        P.RISK_LIMITS
    )

    assert assessment.status is S.FAIL
    assert Decimal(assessment.evidence["observed.peak_gross_exposure"]) > Decimal("1030")


# --------------------------------------------------------------------------- #
# MAX_LEVERAGE
# --------------------------------------------------------------------------- #


def test_leverage_under_the_cap_passes_and_is_read_as_the_gate_reads_it(
    dataset: Dataset,
) -> None:
    result = run_backtest(dataset)

    assessment = certify(dataset, CertificationEvidence(runs=(result,))).assessment(P.MAX_LEVERAGE)

    assert assessment.status is S.PASS
    # The final snapshot's reading equals the gate's own final reading.
    final = result.equity_curve[-1]
    gross = final.long_exposure + abs(final.short_exposure)
    assert result.state.risk.current_leverage == (gross / final.total_equity).quantize(
        Decimal("0.0001")
    )


def test_leverage_past_the_declared_cap_fails(dataset: Dataset) -> None:
    capped = replace(RISK, leverage=LeverageLimit(Decimal("0.1")))
    result = run_backtest(dataset, plan={1: Decimal("5000")})

    assessment = certify(dataset, CertificationEvidence(runs=(result,)), risk=capped).assessment(
        P.MAX_LEVERAGE
    )

    assert assessment.status is S.FAIL
    assert Decimal(assessment.evidence["observed.peak_leverage"]) > Decimal("0.1")


def test_an_account_exposed_with_no_equity_is_unbounded_not_zero() -> None:
    """RiskState reads zero here; certification reports what zero would hide."""

    squeeze = ingest(
        name="SQUEEZE",
        closes=(Decimal("100"), Decimal("100"), Decimal("1000"), Decimal("1000")),
    )
    # Analytics refuses a negative ending capital, so the run compiles none; the
    # equity curve it records is all a leverage reading needs.
    result = backtest(
        replace(run_config(), compile_analytics=False),
        squeeze,
        running_strategy_state(
            STRATEGY_ID, BarStrategy(STRATEGY_ID, ASSET_ID, {1: Decimal("-9000")})
        ),
        context_factory,
        NORMALIZATION,
    )
    assert result.equity_curve[-1].total_equity < 0

    assessment = certify(squeeze, CertificationEvidence(runs=(result,))).assessment(P.MAX_LEVERAGE)

    assert assessment.status is S.FAIL
    assert any("unbounded" in finding for finding in assessment.findings)


# --------------------------------------------------------------------------- #
# SUPPORTED_MARKETS
# --------------------------------------------------------------------------- #


def test_trading_only_declared_instruments_passes(dataset: Dataset) -> None:
    assessment = certify(dataset, CertificationEvidence(runs=(run_backtest(dataset),))).assessment(
        P.SUPPORTED_MARKETS
    )

    assert assessment.status is S.PASS
    assert assessment.evidence["traded"] == ASSET_ID


def test_trading_an_undeclared_instrument_fails(dataset: Dataset) -> None:
    elsewhere = MarketRequirements(("some-other-asset",), ("XNYS",), ("XNYS",), ("USD",))

    assessment = certify(
        dataset, CertificationEvidence(runs=(run_backtest(dataset),)), market=elsewhere
    ).assessment(P.SUPPORTED_MARKETS)

    assert assessment.status is S.FAIL
    assert "does not declare" in assessment.findings[0]


def test_a_listing_venue_the_specification_does_not_declare_fails(dataset: Dataset) -> None:
    wrong_venue = MarketRequirements((ASSET_ID,), ("XNAS",), ("XNAS",), ("USD",))

    assessment = certify(
        dataset, CertificationEvidence(runs=(run_backtest(dataset),)), market=wrong_venue
    ).assessment(P.SUPPORTED_MARKETS)

    assert assessment.status is S.FAIL
    assert any("XNYS" in finding for finding in assessment.findings)


def test_runs_that_traded_nothing_exercised_no_market(dataset: Dataset) -> None:
    assessment = certify(
        dataset, CertificationEvidence(runs=(run_backtest(dataset, plan={}),))
    ).assessment(P.SUPPORTED_MARKETS)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


def test_every_property_read_off_runs_says_their_configuration_is_unobserved(
    dataset: Dataset,
) -> None:
    """A run records its strategy by id and type; a PASS must not claim its parameters."""

    report = certify(dataset, CertificationEvidence(runs=(run_backtest(dataset),)))

    for claim in (
        P.DETERMINISTIC,
        P.RISK_LIMITS,
        P.MAX_LEVERAGE,
        P.SUPPORTED_MARKETS,
        P.RESOURCE_USAGE,
    ):
        limitations = report.assessment(claim).limitations
        assert any(
            "matched to the fingerprint by the strategy id" in limit for limit in limitations
        ), claim


# --------------------------------------------------------------------------- #
# REQUIRED_DATA
# --------------------------------------------------------------------------- #


def test_declared_data_verified_by_provenance_passes(dataset: Dataset) -> None:
    assessment = certify(
        dataset, CertificationEvidence(runs=(run_backtest(dataset),), datasets=(dataset,))
    ).assessment(P.REQUIRED_DATA)

    assert assessment.status is S.PASS
    assert assessment.evidence["prices.frequency"] == "DAILY"
    assert assessment.evidence["prices.price_basis"] == "RAW"
    assert "CLOSE" in assessment.evidence["prices.fields"].split(",")


def test_an_unverified_declaration_is_insufficient(dataset: Dataset) -> None:
    assessment = certify(dataset, CertificationEvidence(runs=(run_backtest(dataset),))).assessment(
        P.REQUIRED_DATA
    )

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


def test_a_dataset_whose_provenance_records_no_bytes_verifies_nothing(
    dataset: Dataset,
) -> None:
    """The lineage hole a manifest refuses is not verified data here either."""

    from alphalab.data.source import SourceKind, raw_source_from_bytes

    empty = raw_source_from_bytes(SourceKind.IN_MEMORY, "x", b"", 1.0, "text/csv")
    hollow = replace(dataset, provenance=replace(dataset.require_provenance(), source=empty))

    assessment = certify(
        dataset, CertificationEvidence(runs=(run_backtest(dataset),), datasets=(hollow,))
    ).assessment(P.REQUIRED_DATA)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE
    assert any("empty source payload" in finding for finding in assessment.findings)
    assert assessment.evidence["verified"] == ""


def test_evidence_measured_on_undeclared_data_fails(dataset: Dataset) -> None:
    other = ingest(name="OTHER-PRICES")

    assessment = certify(
        dataset, CertificationEvidence(runs=(run_backtest(other),), datasets=(dataset,))
    ).assessment(P.REQUIRED_DATA)

    assert assessment.status is S.FAIL


# --------------------------------------------------------------------------- #
# RESOURCE_USAGE
# --------------------------------------------------------------------------- #


def test_counts_are_read_off_the_run(dataset: Dataset) -> None:
    result = run_backtest(dataset)

    counts = {count.metric: count.value for count in resource_counts(result)}

    assert counts[ResourceMetric.RECORDS_PROCESSED] == Decimal(result.records_processed)
    assert counts[ResourceMetric.FILLS] == Decimal(2)
    assert all(count.basis is MeasurementBasis.COUNTED for count in resource_counts(result))


def test_without_a_budget_usage_is_recorded_and_not_assessed(dataset: Dataset) -> None:
    assessment = certify(dataset, CertificationEvidence(runs=(run_backtest(dataset),))).assessment(
        P.RESOURCE_USAGE
    )

    assert assessment.status is S.NOT_ASSESSED
    assert assessment.evidence["observed.FILLS"] == "2"


def test_budgets_met_by_counts_and_measurements_pass(dataset: Dataset) -> None:
    measured = ResourceMeasurement(
        ResourceMetric.CPU_SECONDS,
        Decimal("0.05"),
        MeasurementBasis.MEASURED,
        "time.process_time around one run",
        "CPython 3.12, test machine",
    )
    report = certify(
        dataset,
        CertificationEvidence(runs=(run_backtest(dataset),), measurements=(measured,)),
        budgets=(
            ResourceBudget(ResourceMetric.FILLS, Decimal("10")),
            ResourceBudget(ResourceMetric.CPU_SECONDS, Decimal("1")),
        ),
    )
    assessment = report.assessment(P.RESOURCE_USAGE)

    assert assessment.status is S.PASS
    assert assessment.evidence["environment.CPU_SECONDS"] == "CPython 3.12, test machine"


def test_a_budget_exceeded_fails(dataset: Dataset) -> None:
    assessment = certify(
        dataset,
        CertificationEvidence(runs=(run_backtest(dataset),)),
        budgets=(ResourceBudget(ResourceMetric.FILLS, Decimal("1")),),
    ).assessment(P.RESOURCE_USAGE)

    assert assessment.status is S.FAIL


def test_an_estimate_never_meets_a_budget(dataset: Dataset) -> None:
    estimate = ResourceMeasurement(
        ResourceMetric.PEAK_MEMORY_BYTES,
        Decimal("1000"),
        MeasurementBasis.ESTIMATED,
        "records times bytes per record",
        "",
    )

    assessment = certify(
        dataset,
        CertificationEvidence(measurements=(estimate,)),
        budgets=(ResourceBudget(ResourceMetric.PEAK_MEMORY_BYTES, Decimal("1000000")),),
    ).assessment(P.RESOURCE_USAGE)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE
    assert "estimate" in assessment.findings[0]


def test_a_budget_nobody_measured_is_insufficient(dataset: Dataset) -> None:
    assessment = certify(
        dataset,
        CertificationEvidence(),
        budgets=(ResourceBudget(ResourceMetric.WALL_CLOCK_SECONDS, Decimal("1")),),
    ).assessment(P.RESOURCE_USAGE)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    "arguments",
    [
        (ResourceMetric.FILLS, Decimal("-1"), MeasurementBasis.MEASURED, "m", "e"),
        (ResourceMetric.FILLS, Decimal("1"), MeasurementBasis.MEASURED, " ", "e"),
        (ResourceMetric.CPU_SECONDS, Decimal("1"), MeasurementBasis.MEASURED, "m", ""),
        (ResourceMetric.CPU_SECONDS, Decimal("1"), MeasurementBasis.COUNTED, "m", ""),
    ],
)
def test_a_measurement_that_cannot_be_evidence_is_refused(arguments: tuple[object, ...]) -> None:
    with pytest.raises(LifecycleInputError):
        ResourceMeasurement(*arguments)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# RUNTIME_BEHAVIOR
# --------------------------------------------------------------------------- #


def test_fully_observed_healthy_behaviour_passes(dataset: Dataset) -> None:
    assessment = certify(
        dataset, CertificationEvidence(observations=(healthy(1_000.0), healthy(2_000.0)))
    ).assessment(P.RUNTIME_BEHAVIOR)

    assert assessment.status is S.PASS
    assert assessment.evidence["reports.HEALTHY"] == "2"


def test_an_observation_nobody_completed_is_insufficient(dataset: Dataset) -> None:
    assessment = certify(
        dataset, CertificationEvidence(observations=(RuntimeObservation(observed_at=5.0),))
    ).assessment(P.RUNTIME_BEHAVIOR)

    assert assessment.status is S.INSUFFICIENT_EVIDENCE


def test_a_breach_fails(dataset: Dataset) -> None:
    # Three days old, against a two-day budget.
    stale = replace(healthy(1_000_000.0), last_market_data_at=1_000_000.0 - 259_200.0)

    assessment = certify(dataset, CertificationEvidence(observations=(stale,))).assessment(
        P.RUNTIME_BEHAVIOR
    )

    assert assessment.status is S.FAIL
    assert any("STALE_DATA" in finding for finding in assessment.findings)


def test_runtime_evidence_for_a_version_that_never_ran_contradicts_the_lifecycle(
    dataset: Dataset,
) -> None:
    never_ran = begin_progression(VERSION.ref)

    report = certify_strategy(
        fingerprint(),
        specification(dataset),
        CertificationEvidence(observations=(healthy(),)),
        progression=never_ran,
    )

    assert report.status_of(P.RUNTIME_BEHAVIOR) is S.INSUFFICIENT_EVIDENCE
    assert report.stage is StrategyLifecycleStage.RESEARCH


def test_a_progression_that_reached_paper_supports_runtime_evidence(dataset: Dataset) -> None:
    progression = begin_progression(VERSION.ref)
    for index, stage in enumerate(
        (
            StrategyLifecycleStage.BACKTEST,
            StrategyLifecycleStage.VALIDATION,
            StrategyLifecycleStage.PAPER,
        )
    ):
        progression = advance_progression(progression, stage, "tests", float(index))

    report = certify_strategy(
        fingerprint(),
        specification(dataset),
        CertificationEvidence(observations=(healthy(),)),
        progression=progression,
    )

    assert report.status_of(P.RUNTIME_BEHAVIOR) is S.PASS


# --------------------------------------------------------------------------- #
# Nothing is mutated
# --------------------------------------------------------------------------- #


def test_certification_changes_none_of_its_inputs(dataset: Dataset) -> None:
    fp = fingerprint()
    spec = specification(dataset)
    result = run_backtest(dataset)
    before = (fp, spec, result.run)

    certify_strategy(fp, spec, CertificationEvidence(runs=(result,), repeated_runs=(result,)))

    assert (fp, spec, result.run) == before


def test_a_strategy_version_other_than_the_harness_one_certifies_its_own_evidence(
    dataset: Dataset,
) -> None:
    """The whole harness is not special: another line, another id, its own runs."""

    definition = StrategyDefinition("OTHER-ID", "o", "1", "a", "d", {"entry": 10.0, "exit": -4.0})
    version = StrategyVersion("other-line", 1, definition, VERSION.stage)
    fp = fingerprint(version, code=CodeIdentity("pkg", "1", "pkg.S", None))
    runs = (run_backtest(dataset, strategy_id="OTHER-ID"),)

    report = certify_strategy(
        fp, specification(dataset, version=version), CertificationEvidence(runs=runs)
    )

    assert report.status_of(P.RISK_LIMITS) is S.PASS
