"""v3.2 end to end: bytes on disk to a research result with exact lineage.

The path this release exists to make possible, exercised once through the
application-facing API rather than through the internals::

    CSV bytes
      -> canonical Dataset          (identity derived from content + config)
      -> ObservationFrame           (one field, per symbol, carrying the version)
      -> FeaturePanel               (identity derived from dataset + definition)
      -> forward returns            (the one quantity that looks ahead)
      -> SignalDiagnostics          (with the sample counts attached)
      -> StudyResult                (identity derived from study + numbers)
      -> ValidationEvidence         (the frozen digest, unchanged since v2.6)

Each hop is checked for the property that makes the next one trustworthy, and
the last two are checked for tamper-evidence: numbers edited after the fact
stop verifying.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from pathlib import Path

import pytest

from alphalab.api import (
    DataRequest,
    ResearchStudy,
    ingest_rows,
    observe,
    run_study,
    select,
    study_panels,
)
from alphalab.data import (
    CleaningPolicy,
    DataAssetClass,
    Dataset,
    DuplicatePolicy,
    IngestionRequest,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    bucket_panel,
    factor_decay,
    factor_exposure,
    factor_turnover,
    forward_returns,
    information_coefficient,
    neutralize_group,
    rank_panel,
    weights_from_buckets,
)
from alphalab.lifecycle import (
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    evaluate_policy,
    evidence_from_study,
    verify_evidence_id,
)
from alphalab.research import (
    CVMethod,
    OverfittingPolicy,
    PurgePolicy,
    ResearchValidationError,
    WindowMode,
    build_overfitting_report,
    conditional_diagnostics,
    cross_validation_splits,
    delay_signal,
    label_ends_from_horizon,
    parameter_sweep,
    period_stability,
    signal_diagnostics,
    walk_forward_splits,
)

RETRIEVED_AT = 1_726_000_000.0
PRODUCED_AT = 1_726_000_100.0
SEED = 20260920
SYMBOLS = tuple(f"SYM{index}" for index in range(10))
SESSIONS = 160

POLICY = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

MOMENTUM = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)
REVERSION = FeatureDefinition("rev_10", FeatureKind.MEAN_REVERSION, FeatureField.CLOSE, window=10)


def _rows() -> list[dict[str, object]]:
    """A deterministic multi-asset panel, seeded so the whole test reproduces."""

    prng = random.Random(SEED)
    level = {symbol: 100.0 + 9.0 * index for index, symbol in enumerate(SYMBOLS)}
    rows: list[dict[str, object]] = []
    day = 0
    produced = 0

    while produced < SESSIONS:
        stamp = 1_704_067_200 + day * 86_400
        day += 1
        if (day + 3) % 7 in (0, 1):  # a crude weekend, so the series has real gaps
            continue
        produced += 1
        for index, symbol in enumerate(SYMBOLS):
            drift = 0.00035 * (index - 4.5)
            level[symbol] *= 1.0 + drift + prng.gauss(0.0, 0.011)
            close = round(level[symbol], 4)
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": stamp,
                    "open": round(close * 0.999, 4),
                    "high": round(close * 1.004, 4),
                    "low": round(close * 0.996, 4),
                    "close": close,
                    "volume": 1_000_000 + produced * 13 + index,
                }
            )
    return rows


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    request = IngestionRequest(
        name="V32-PANEL",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "v32-integration", b"", RETRIEVED_AT, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=POLICY,
        price_basis=PriceBasis.RAW,
    )
    result = ingest_rows(_rows(), request)
    assert result.dataset.is_versioned
    return result.dataset


@pytest.fixture(scope="module")
def study(dataset: Dataset) -> ResearchStudy:
    return ResearchStudy(
        study_name="v32-integration",
        dataset_version=dataset.require_provenance().dataset_version,
        universe=SYMBOLS,
        features=(MOMENTUM, REVERSION),
        horizons=(1, 5, 20),
        splits="walk_forward(mode=ROLLING,train=60,validation=20,test=20,step=20)",
        seed=SEED,
        notes="the v3.2 capability test",
    )


# --------------------------------------------------------------------------- #
# The dataset, and what it carries into everything after it
# --------------------------------------------------------------------------- #


def test_the_dataset_is_versioned_and_its_version_reaches_the_frame(dataset: Dataset) -> None:
    version = dataset.require_provenance().dataset_version

    frame = observe(dataset, FeatureField.CLOSE)

    assert frame.dataset_version == version
    assert frame.symbols == SYMBOLS
    assert frame.source_field is FeatureField.CLOSE


def test_the_series_has_real_gaps_so_the_test_is_not_measuring_an_even_grid(
    dataset: Dataset,
) -> None:
    """Weekends are in the fixture on purpose: a horizon in seconds would break."""

    frame = observe(dataset, FeatureField.CLOSE)
    spacings = {
        round(later - earlier)
        for earlier, later in zip(frame.timestamps, frame.timestamps[1:], strict=False)
    }

    assert len(spacings) > 1, "the fixture must not be evenly spaced"


def test_a_panels_lineage_names_the_dataset_and_the_definition(
    dataset: Dataset, study: ResearchStudy
) -> None:
    panels = study_panels(study, dataset)

    assert set(panels) == set(study.feature_versions)
    for version, panel in panels.items():
        assert panel.dataset_version == study.dataset_version
        assert panel.lineage == version, "an untransformed panel's lineage is its version"


def test_a_study_refuses_a_dataset_it_was_not_written_for(
    dataset: Dataset, study: ResearchStudy
) -> None:
    """The dataset substitution ADR-0017 closed for evidence, closed for studies."""

    elsewhere = replace(study, dataset_version="SOMETHING-ELSE@deadbeef")

    with pytest.raises(ResearchValidationError, match="was written for dataset"):
        run_study(elsewhere, dataset)


def test_a_study_over_an_unversioned_dataset_is_refused(study: ResearchStudy) -> None:
    """A dataset built from rows in memory records no provenance, and says so.

    ``create_dataset`` is the path that produces one: no source, no bytes to
    hash, no version. A study cannot be run over it, because the result would
    name data nobody can identify -- the rule
    ``Dataset.require_provenance`` states one layer down.
    """

    from alphalab.data.feed import Bar as WireBar
    from alphalab.data.loader import create_dataset
    from alphalab.data.metadata import DatasetMetadata

    bars = tuple(
        WireBar(SYMBOLS[0], 1_704_067_200.0 + index * 86_400.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        for index in range(5)
    )
    unversioned = create_dataset(
        DatasetMetadata(
            dataset_id="unversioned",
            source_name="in-memory",
            asset_class=DataAssetClass.EQUITY,
            frequency=TimeFrequency.DAILY,
            start_timestamp=bars[0].timestamp,
            end_timestamp=bars[-1].timestamp,
        ),
        bars,
    )

    assert unversioned.provenance is None
    assert not unversioned.is_versioned
    with pytest.raises(ResearchValidationError, match="carries no provenance"):
        run_study(study, unversioned)


# --------------------------------------------------------------------------- #
# The study
# --------------------------------------------------------------------------- #


def test_a_study_runs_end_to_end_and_names_everything_it_used(
    dataset: Dataset, study: ResearchStudy
) -> None:
    result = run_study(study, dataset, produced_at=PRODUCED_AT)

    assert result.verify()
    assert result.dataset_version == study.dataset_version
    assert result.study_id == study.study_id
    assert set(result.feature_lineage) == set(study.feature_versions)
    assert result.metrics, "the study measured something"

    for feature in ("mom_20", "rev_10"):
        for horizon in (1, 5, 20):
            assert f"{feature}.h{horizon}.rank_ic" in result.metrics
            assert f"{feature}.h{horizon}.observations" in result.metrics


def test_the_result_reproduces_and_does_not_move_with_the_clock(
    dataset: Dataset, study: ResearchStudy
) -> None:
    first = run_study(study, dataset, produced_at=PRODUCED_AT)
    second = run_study(study, dataset, produced_at=PRODUCED_AT + 1_000_000.0)

    assert first.result_id == second.result_id
    assert first.metrics == second.metrics


def test_a_result_whose_numbers_were_edited_stops_verifying(
    dataset: Dataset, study: ResearchStudy
) -> None:
    result = run_study(study, dataset, produced_at=PRODUCED_AT)
    tampered = replace(result, metrics={**dict(result.metrics), "mom_20.h5.rank_ic": 0.99})

    assert result.verify()
    assert not tampered.verify()


def test_a_study_with_no_horizon_is_refused(dataset: Dataset, study: ResearchStudy) -> None:
    with pytest.raises(ResearchValidationError, match="declares no forward horizons"):
        run_study(replace(study, horizons=()), dataset)


# --------------------------------------------------------------------------- #
# Diagnostics on the panel
# --------------------------------------------------------------------------- #


def test_the_diagnostics_report_the_sample_every_number_rests_on(
    dataset: Dataset, study: ResearchStudy
) -> None:
    frame = observe(dataset, FeatureField.CLOSE)
    panel = study_panels(study, dataset)[MOMENTUM.feature_version]

    measured = signal_diagnostics(panel, forward_returns(frame, 5), buckets=5, minimum_assets=5)

    assert measured.observations > 0
    assert measured.instants > 0
    assert sum(bucket.observations for bucket in measured.quantiles) == measured.observations
    assert measured.rank_ic.instants_measured + measured.rank_ic.instants_skipped == len(
        panel.timestamps
    )


def test_a_decay_profile_shrinks_its_sample_as_the_horizon_grows(
    dataset: Dataset, study: ResearchStudy
) -> None:
    frame = observe(dataset, FeatureField.CLOSE)
    panel = study_panels(study, dataset)[MOMENTUM.feature_version]

    profile = factor_decay(panel, frame, [1, 5, 20], minimum_assets=5)
    counts = profile.instants_by_horizon

    assert counts[1] > counts[5] > counts[20]


def test_delaying_the_signal_changes_the_measurement(
    dataset: Dataset, study: ResearchStudy
) -> None:
    """The cheapest robustness test, run through the real pipeline."""

    frame = observe(dataset, FeatureField.CLOSE)
    panel = study_panels(study, dataset)[MOMENTUM.feature_version]
    realized = forward_returns(frame, 5)

    baseline = signal_diagnostics(panel, realized, buckets=5, minimum_assets=5)
    delayed = signal_diagnostics(delay_signal(panel, 5), realized, buckets=5, minimum_assets=5)

    assert baseline.rank_ic.mean_rank is not None
    assert delayed.rank_ic.mean_rank is not None
    assert delayed.rank_ic.mean_rank != baseline.rank_ic.mean_rank


def test_the_cross_sectional_chain_composes_from_the_dataset(
    dataset: Dataset, study: ResearchStudy
) -> None:
    panel = study_panels(study, dataset)[MOMENTUM.feature_version]
    groups = {symbol: ("even" if int(symbol[-1]) % 2 == 0 else "odd") for symbol in SYMBOLS}

    neutral = neutralize_group(panel, groups)
    bucketed = bucket_panel(rank_panel(neutral.panel).panel, 5)
    weights = weights_from_buckets(bucketed, 5)
    turnover = factor_turnover(weights)
    exposure = factor_exposure(weights, groups)

    assert [step.operation for step in bucketed.transforms] == [
        "neutralize_group",
        "rank",
        "bucket",
    ]
    assert exposure.net_exposure == pytest.approx(0.0)
    assert 0.0 <= turnover.mean_turnover <= 1.0
    assert math.isfinite(exposure.gross_exposure)


def test_conditioning_by_a_caller_supplied_regime_slices_the_sample(
    dataset: Dataset, study: ResearchStudy
) -> None:
    frame = observe(dataset, FeatureField.CLOSE)
    panel = study_panels(study, dataset)[MOMENTUM.feature_version]
    instants = panel.timestamps
    half = len(instants) // 2
    regimes = {
        stamp: ("first_half" if index < half else "second_half")
        for index, stamp in enumerate(instants)
    }

    measured = conditional_diagnostics(
        panel, forward_returns(frame, 5), regimes, buckets=5, minimum_assets=5
    )

    assert sorted(measured) == ["first_half", "second_half"]
    assert measured["first_half"].instants + measured["second_half"].instants <= len(instants)


# --------------------------------------------------------------------------- #
# Validation splits over the real index
# --------------------------------------------------------------------------- #


def test_walk_forward_folds_over_the_real_index_never_contaminate(dataset: Dataset) -> None:
    frame = observe(dataset, FeatureField.CLOSE)
    instants = list(frame.timestamps)
    horizon = 5
    policy = PurgePolicy(label_ends_from_horizon(instants, horizon))

    report = walk_forward_splits(instants, 60, 20, 20, WindowMode.ROLLING, policy=policy)

    assert len(report) >= 2
    assert report.is_forward_only
    assert report.total_purged > 0
    assert report.total_validation_purged > 0
    for fold in report.folds:
        assert not set(fold.train) & set(fold.validation)
        assert not set(fold.validation) & set(fold.test)
        last_train = instants.index(fold.train[-1])
        first_validation = instants.index(fold.validation[0])
        assert first_validation - last_train > horizon


def test_a_label_horizon_as_long_as_the_validation_window_is_refused(dataset: Dataset) -> None:
    """Not a corner case -- the configuration is genuinely unusable, and says why.

    With a 20-period label horizon and a 20-period validation window, *every*
    validation row's outcome falls inside the test window, so there is nothing
    left to select parameters on that has not already seen the data the test is
    meant to be independent of. A scheme that carried on would report a test
    score contaminated by the selection.
    """

    frame = observe(dataset, FeatureField.CLOSE)
    instants = list(frame.timestamps)
    policy = PurgePolicy(label_ends_from_horizon(instants, 20))

    with pytest.raises(ResearchValidationError, match="no validation instants left"):
        walk_forward_splits(instants, 60, 20, 20, WindowMode.ROLLING, policy=policy)


def test_every_cv_scheme_runs_over_the_real_index(dataset: Dataset) -> None:
    frame = observe(dataset, FeatureField.CLOSE)
    instants = list(frame.timestamps)
    purge = PurgePolicy(label_ends_from_horizon(instants, 5))
    embargo = PurgePolicy(label_ends_from_horizon(instants, 5), embargo_seconds=5 * 86400.0)

    schemes = {
        CVMethod.ROLLING: cross_validation_splits(instants, 4, CVMethod.ROLLING, purge),
        CVMethod.EXPANDING: cross_validation_splits(instants, 4, CVMethod.EXPANDING, purge),
        CVMethod.PURGED: cross_validation_splits(instants, 5, CVMethod.PURGED, purge),
        CVMethod.EMBARGOED: cross_validation_splits(instants, 5, CVMethod.EMBARGOED, embargo),
    }

    assert schemes[CVMethod.ROLLING].is_forward_only
    assert not schemes[CVMethod.PURGED].is_forward_only
    assert schemes[CVMethod.EMBARGOED].total_embargoed > 0
    assert schemes[CVMethod.PURGED].total_embargoed == 0


def test_a_sweep_and_a_stability_report_feed_one_overfitting_report(
    dataset: Dataset, study: ResearchStudy
) -> None:
    """Measurements, thresholds and findings, produced by the real pipeline."""

    frame = observe(dataset, FeatureField.CLOSE)
    realized = forward_returns(frame, 5)
    instants = list(frame.timestamps)

    def evaluate(configuration: str) -> float:
        window = int(configuration.split("=")[1])
        definition = FeatureDefinition(
            "sweep", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=window
        )
        panel = study_panels(replace(study, features=(definition,)), dataset)[
            definition.feature_version
        ]
        measured = information_coefficient(panel, realized, minimum_assets=5)
        return measured.mean_rank if measured.mean_rank is not None else 0.0

    configurations = [f"window={window}" for window in (10, 15, 20, 25, 30)]
    sweep = parameter_sweep("rank_ic", configurations, evaluate)

    assert sweep.trials == 5
    assert sweep.sensitivity is not None

    half = len(instants) // 2
    panel = study_panels(study, dataset)[MOMENTUM.feature_version]
    early = signal_diagnostics(
        panel, realized, minimum_assets=5, instants=instants[:half], label="early"
    )
    late = signal_diagnostics(
        panel, realized, minimum_assets=5, instants=instants[half:], label="late"
    )
    stability = period_stability(
        "rank_ic",
        {
            "early": early.rank_ic.mean_rank or 0.0,
            "late": late.rank_ic.mean_rank or 0.0,
        },
    )

    report = build_overfitting_report(
        metric="rank_ic",
        in_sample=sweep.best_score,
        out_of_sample=late.rank_ic.mean_rank or 0.0,
        policy=OverfittingPolicy(maximum_degradation=0.5, minimum_positive_share=0.5),
        effective_parameters=1,
        sweep=sweep,
        period=stability,
    )

    assert report.trials == 5
    assert report.bonferroni_alpha == pytest.approx(0.05 / 5)
    for finding in report.findings:
        assert "bound" in finding or "not measured" in finding


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def test_a_study_result_becomes_evidence_that_names_the_exact_data(
    dataset: Dataset, study: ResearchStudy
) -> None:
    result = run_study(study, dataset, produced_at=PRODUCED_AT)

    evidence = evidence_from_study(result, "strategy://v32@1", PRODUCED_AT)

    assert evidence.method is ValidationMethod.STUDY
    assert evidence.dataset_id == dataset.require_provenance().dataset_version
    assert evidence.seed == SEED
    assert evidence.source_id == result.result_id
    assert verify_evidence_id(evidence)


def test_evidence_from_a_tampered_result_is_refused(dataset: Dataset, study: ResearchStudy) -> None:
    from alphalab.lifecycle.exceptions import LifecycleInputError

    result = run_study(study, dataset, produced_at=PRODUCED_AT)
    tampered = replace(result, metrics={**dict(result.metrics), "mom_20.h5.rank_ic": 9.99})

    with pytest.raises(LifecycleInputError, match="altered after it was recorded"):
        evidence_from_study(tampered, "strategy://v32@1", PRODUCED_AT)


def test_a_policy_can_gate_a_promotion_on_a_study(dataset: Dataset, study: ResearchStudy) -> None:
    result = run_study(study, dataset, produced_at=PRODUCED_AT)
    evidence = evidence_from_study(result, "strategy://v32@1", PRODUCED_AT)

    generous = ValidationPolicy(
        policy_id="v32-generous",
        thresholds=(MetricThreshold("mom_20.h5.observations", minimum=1.0),),
        required_method=ValidationMethod.STUDY,
    )
    impossible = ValidationPolicy(
        policy_id="v32-impossible",
        thresholds=(MetricThreshold("mom_20.h5.rank_ic", minimum=0.99),),
        required_method=ValidationMethod.STUDY,
    )
    wrong_method = ValidationPolicy(
        policy_id="v32-backtest-only",
        thresholds=(MetricThreshold("mom_20.h5.observations", minimum=1.0),),
        required_method=ValidationMethod.BACKTEST,
    )

    assert evaluate_policy(generous, evidence).passed
    assert not evaluate_policy(impossible, evidence).passed
    outcome = evaluate_policy(wrong_method, evidence)
    assert not outcome.passed
    assert "requires BACKTEST" in outcome.failures[0]


def test_the_frozen_evidence_digest_did_not_move() -> None:
    """v3.2 added a ``ValidationMethod`` member and nothing else to the digest.

    The rendering hashes ``method.name``, so every promotion recorded since
    v2.6 still verifies. Pinned against a literal rather than against the
    function that produces it.
    """

    import hashlib

    from alphalab.lifecycle.evidence import evidence_id_for

    expected = hashlib.sha256(
        "\n".join(
            [
                "method=BACKTEST",
                "subject=strategy://x@1",
                "dataset=ds@abc",
                "seed=7",
                "metrics",
                "sharpe_ratio=1.5",
            ]
        ).encode("utf-8")
    ).hexdigest()

    assert (
        evidence_id_for(
            ValidationMethod.BACKTEST, "strategy://x@1", "ds@abc", 7, {"sharpe_ratio": 1.5}
        )
        == expected
    )


# --------------------------------------------------------------------------- #
# Point-in-time selection still narrows before any of this runs
# --------------------------------------------------------------------------- #


def test_a_point_in_time_selection_narrows_the_study_input(dataset: Dataset) -> None:
    frame = observe(dataset, FeatureField.CLOSE)
    instants = list(frame.timestamps)
    cutoff = instants[len(instants) // 2]

    everything = select(dataset, DataRequest(symbols=SYMBOLS))
    knowable = select(dataset, DataRequest(symbols=SYMBOLS, as_of=cutoff))

    assert len(knowable) < len(everything)
    assert max(record.timestamp for record in knowable.records) == cutoff
    assert knowable.dataset_version == dataset.dataset_version


def test_examples_directory_holds_the_v32_examples() -> None:
    """The release ships runnable examples for each new capability."""

    examples = Path(__file__).resolve().parents[2] / "examples"
    for number in range(17, 25):
        matches = list(examples.glob(f"{number}_*.py"))
        assert matches, f"example {number} is missing"
