"""Reproducibility manifests over real runs and real studies.

Every manifest here is built from a result that was actually produced: a
backtest through the execution path, or a study result through
:func:`alphalab.research.study.build_result`, which derives its identity. The
properties that matter are that a manifest names each input by the identity its
owner gives it, that changing any input can change the manifest, that nothing it
cannot keep is claimed, and that the four questions -- identity, completeness,
rerun, external inputs -- stay separate.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.data.dataset import Dataset
from alphalab.data.exceptions import DataValidationError
from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    REPRODUCIBILITY_MANIFEST_SCHEME,
    CodeIdentity,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    EngineIdentity,
    ExternalInput,
    LifecycleInputError,
    RerunOutcome,
    ResultKind,
    SeedRole,
    assess_reproducibility,
    canonical_manifest_key,
    digest_run,
    external_requirements,
    manifest_for_run,
    manifest_for_study,
    manifest_gaps,
    research_configuration_for_study,
    verify_manifest,
)
from alphalab.research.study import ResearchStudy, StudyResult, build_result
from alphalab.runtime.run import ExecutionMode
from tests.unit.lifecycle.evidence_harness import (
    CODE,
    ENGINE,
    SEED,
    STRATEGY_ID,
    SYMBOL,
    fingerprint,
    ingest,
    run_backtest,
)

# --------------------------------------------------------------------------- #
# A run's record
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return ingest()


def test_a_run_digest_reads_every_identity_off_the_runs_own_record(dataset: Dataset) -> None:
    result = run_backtest(dataset)
    digest = digest_run(result)

    assert len(digest.result_id) == 64
    assert digest.seed == SEED
    assert digest.dataset_id == dataset.dataset_version
    assert digest.mode is ExecutionMode.BACKTEST
    assert digest.strategy_ids == (STRATEGY_ID,)
    recorded = json.loads(digest.configuration)
    assert recorded["mode"] == "ExecutionMode.BACKTEST"
    assert "seed" not in recorded, "the seed is its own field, so a change to it is attributable"
    assert f"strategy:{STRATEGY_ID}=BarStrategy" in digest.live_objects


def test_two_seeded_runs_of_the_same_inputs_have_one_record(dataset: Dataset) -> None:
    first, second = digest_run(run_backtest(dataset)), digest_run(run_backtest(dataset))

    assert first == second


def test_the_record_covers_what_the_run_did(dataset: Dataset) -> None:
    """A run that traded differently has a different record, on the same config."""

    base = digest_run(run_backtest(dataset))
    other = digest_run(run_backtest(dataset, plan={1: Decimal("5")}))

    assert other.configuration_id == base.configuration_id
    assert other.result_id != base.result_id


def test_the_recorded_configuration_holds_no_machine_path_or_clock(dataset: Dataset) -> None:
    digest = digest_run(run_backtest(dataset))

    for marker in ("/Users/", "/home/", "/tmp/", "C:\\", "object at 0x"):
        assert marker not in digest.configuration


# --------------------------------------------------------------------------- #
# Run manifests
# --------------------------------------------------------------------------- #


def test_a_run_manifest_names_each_input_by_its_owners_identity(dataset: Dataset) -> None:
    result = run_backtest(dataset)
    fp = fingerprint()
    manifest = manifest_for_run(result, dataset, fp, ENGINE)
    provenance = dataset.require_provenance()
    digest = digest_run(result)

    assert manifest.kind is ResultKind.RUN
    assert manifest.result_id == digest.result_id
    assert manifest.dataset_version == provenance.dataset_version
    assert manifest.dataset_content_hash == provenance.content_hash
    assert manifest.configuration == digest.configuration
    assert manifest.seed == SEED
    assert manifest.seed_role is SeedRole.IDENTIFIER_STREAM
    assert manifest.fingerprint == fp
    assert manifest.mode is ExecutionMode.BACKTEST
    assert verify_manifest(manifest)


def test_the_manifest_key_is_scheme_tagged_and_commits_to_the_fingerprint(
    dataset: Dataset,
) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    key = canonical_manifest_key(
        manifest.kind,
        manifest.result_id,
        manifest.dataset_version,
        manifest.dataset_content_hash,
        manifest.configuration_id,
        manifest.seed,
        manifest.seed_role,
        manifest.engine,
        None if manifest.fingerprint is None else manifest.fingerprint.fingerprint,
    )

    assert key.split("\n")[0] == REPRODUCIBILITY_MANIFEST_SCHEME
    assert repr(fingerprint().fingerprint) in key


def test_the_same_inputs_give_the_same_manifest(dataset: Dataset) -> None:
    first = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    second = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assert first.manifest_id == second.manifest_id
    assert first == second


def test_each_changed_input_changes_the_manifest(dataset: Dataset) -> None:
    base = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    other_data = ingest(closes=(Decimal("100"), Decimal("101"), Decimal("99"), Decimal("98")))
    changed = {
        "dataset": manifest_for_run(run_backtest(other_data), other_data, fingerprint(), ENGINE),
        "seed": manifest_for_run(
            run_backtest(dataset, seed=SEED + 1), dataset, fingerprint(), ENGINE
        ),
        "strategy": manifest_for_run(
            run_backtest(dataset),
            dataset,
            fingerprint(code=replace(CODE, version="1.0.1")),
            ENGINE,
        ),
        "engine": manifest_for_run(
            run_backtest(dataset), dataset, fingerprint(), EngineIdentity("alphalab", "3.6.1")
        ),
        "outputs": manifest_for_run(
            run_backtest(dataset, plan={1: Decimal("5")}), dataset, fingerprint(), ENGINE
        ),
    }

    for label, manifest in changed.items():
        assert manifest.manifest_id != base.manifest_id, label
    # The dataset changed *as a dataset*, not only through what it made the run do.
    assert changed["dataset"].dataset_version != base.dataset_version
    assert changed["dataset"].dataset_content_hash != base.dataset_content_hash


def test_a_changed_configuration_changes_the_manifest(dataset: Dataset) -> None:
    from alphalab.risk.limits import LeverageLimit
    from tests.unit.lifecycle.evidence_harness import RISK

    tighter = replace(RISK, leverage=LeverageLimit(Decimal("999")))
    base = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    changed = manifest_for_run(
        run_backtest(dataset, risk_limits=tighter), dataset, fingerprint(), ENGINE
    )

    assert changed.configuration_id != base.configuration_id
    assert changed.manifest_id != base.manifest_id


def test_an_unseeded_run_is_refused_rather_than_called_reproducible(dataset: Dataset) -> None:
    with pytest.raises(LifecycleInputError, match="unseeded"):
        manifest_for_run(run_backtest(dataset, seed=None), dataset, fingerprint(), ENGINE)


def test_a_dataset_the_run_did_not_read_is_refused(dataset: Dataset) -> None:
    other = ingest(name="OTHER")

    with pytest.raises(LifecycleInputError, match="never measured on"):
        manifest_for_run(run_backtest(dataset), other, fingerprint(), ENGINE)


def test_a_dataset_whose_provenance_records_no_bytes_is_refused() -> None:
    """``ingest_rows`` records the source it is given (ADR-0036). Given an empty
    payload, two different row sets under one name derive one version -- so a
    manifest refuses to name such a dataset rather than claim it identifies data.
    """

    from alphalab.api import ingest_rows
    from alphalab.data.corporate_actions import PriceBasis
    from alphalab.data.ingestion import IngestionRequest
    from alphalab.data.source import SourceKind, raw_source_from_bytes
    from alphalab.data.symbols import DataAssetClass
    from alphalab.data.time import TimeFrequency
    from tests.unit.lifecycle.evidence_harness import CLEANING

    def unrecorded(close: str) -> Dataset:
        rows = [
            {
                "symbol": SYMBOL,
                "timestamp": f"2024-03-0{day} 00:00:00",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1,
            }
            for day in range(1, 5)
        ]
        request = IngestionRequest(
            name="UNRECORDED",
            source=raw_source_from_bytes(SourceKind.IN_MEMORY, "x", b"", 1.0, "text/csv"),
            frequency=TimeFrequency.DAILY,
            asset_class=DataAssetClass.EQUITY,
            cleaning_policy=CLEANING,
            price_basis=PriceBasis.RAW,
            timezone_name="UTC",
        )
        return ingest_rows(rows, request).dataset

    first, second = unrecorded("100"), unrecorded("250")
    assert first.dataset_version == second.dataset_version, "the caveat this guard exists for"

    with pytest.raises(LifecycleInputError, match="empty source payload"):
        manifest_for_run(run_backtest(first), first, fingerprint(), ENGINE)


def test_a_dataset_without_provenance_is_refused(dataset: Dataset) -> None:
    bare = replace(dataset, provenance=None)

    with pytest.raises(DataValidationError):
        manifest_for_run(run_backtest(dataset), bare, fingerprint(), ENGINE)


def test_a_fingerprint_of_a_strategy_the_run_did_not_execute_is_refused(
    dataset: Dataset,
) -> None:
    from tests.unit.lifecycle.evidence_harness import DEFINITION, VERSION

    other = fingerprint(replace(VERSION, definition=replace(DEFINITION, strategy_id="ELSE")))

    with pytest.raises(LifecycleInputError, match="executed"):
        manifest_for_run(run_backtest(dataset), dataset, other, ENGINE)


def test_an_altered_fingerprint_is_refused(dataset: Dataset) -> None:
    altered = replace(fingerprint(), parameters={"entry": 1.0})

    with pytest.raises(LifecycleInputError, match="altered"):
        manifest_for_run(run_backtest(dataset), dataset, altered, ENGINE)


def test_an_edited_manifest_stops_verifying(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assert not verify_manifest(replace(manifest, seed=1))
    assert not verify_manifest(replace(manifest, configuration=manifest.configuration + " "))
    assert not verify_manifest(replace(manifest, result_id="0" * 64))
    assert not verify_manifest(replace(manifest, seed_role=SeedRole.ABSENT))
    assert not verify_manifest(replace(manifest, engine=EngineIdentity("alphalab", "0.1")))


# --------------------------------------------------------------------------- #
# The four questions
# --------------------------------------------------------------------------- #


def test_a_rerun_of_the_same_inputs_reproduces(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    rerun = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assessment = assess_reproducibility(manifest, rerun)

    assert assessment.identity_verified
    assert assessment.rerun is RerunOutcome.REPRODUCED
    assert assessment.metadata_complete
    assert assessment.recreatable


def test_no_rerun_establishes_nothing_about_reproduction(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    assessment = assess_reproducibility(manifest)

    assert assessment.identity_verified
    assert assessment.rerun is RerunOutcome.NOT_ATTEMPTED
    assert assessment.rerun_detail == ()


def test_a_rerun_of_other_inputs_is_not_a_failure_or_a_success(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    newer_engine = manifest_for_run(
        run_backtest(dataset), dataset, fingerprint(), EngineIdentity("alphalab", "3.7.0")
    )

    assessment = assess_reproducibility(manifest, newer_engine)

    assert assessment.rerun is RerunOutcome.INPUTS_DIFFER
    assert any(line.startswith("engine:") for line in assessment.rerun_detail)
    assert "nevertheless matched" in assessment.rerun_detail[-1]


def test_the_same_declared_inputs_with_a_different_result_diverged(dataset: Dataset) -> None:
    """A live object changed without the record noticing: the result tells."""

    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    changed_behaviour = manifest_for_run(
        run_backtest(dataset, plan={1: Decimal("5")}), dataset, fingerprint(), ENGINE
    )

    assessment = assess_reproducibility(manifest, changed_behaviour)

    assert assessment.rerun is RerunOutcome.DIVERGED
    assert manifest.result_id in assessment.rerun_detail[0]


def test_an_altered_rerun_manifest_is_not_evidence(dataset: Dataset) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    with pytest.raises(LifecycleInputError, match="does not verify"):
        assess_reproducibility(manifest, replace(manifest, seed=SEED + 5))


def test_a_rerun_is_never_compared_against_an_altered_manifest(dataset: Dataset) -> None:
    """Editing the original's result to match a rerun must not read as reproduction."""

    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    diverged = manifest_for_run(
        run_backtest(dataset, plan={1: Decimal("5")}), dataset, fingerprint(), ENGINE
    )
    doctored = replace(manifest, result_id=diverged.result_id)

    with pytest.raises(LifecycleInputError, match="does not verify"):
        assess_reproducibility(doctored, diverged)
    alone = assess_reproducibility(doctored)
    assert not alone.identity_verified
    assert alone.rerun is RerunOutcome.NOT_ATTEMPTED


def test_gaps_name_approximate_code_and_dependency_identities(dataset: Dataset) -> None:
    undeclared_code = CodeIdentity("pkg", "1.0.0", "pkg.S", None)
    direct = DependencyManifest(
        DependencyCompleteness.DIRECT_ONLY, (DependencyPin("numpy", "1.26.4"),)
    )
    manifest = manifest_for_run(
        run_backtest(dataset),
        dataset,
        fingerprint(code=undeclared_code, dependencies=direct),
        ENGINE,
    )

    gaps = manifest_gaps(manifest)

    assert len(gaps) == 2
    assert "source was not hashed" in gaps[0]
    assert "DIRECT_ONLY" in gaps[1]
    assert not assess_reproducibility(manifest).metadata_complete


def test_external_requirements_are_never_empty_and_say_what_is_not_held(
    dataset: Dataset,
) -> None:
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)

    inputs = [requirement.input for requirement in external_requirements(manifest)]

    assert inputs == [
        ExternalInput.DATASET_BYTES,
        ExternalInput.STRATEGY_CODE,
        ExternalInput.DEPENDENCIES,
        ExternalInput.ENGINE,
        ExternalInput.LIVE_OBJECTS,
        ExternalInput.NORMALIZATION_POLICY,
        ExternalInput.CONTEXT_FACTORY,
    ]
    live = external_requirements(manifest)[4]
    assert "simulator=ExecutionSimulator" in live.detail
    assert dataset.require_provenance().content_hash in external_requirements(manifest)[0].detail


def test_an_engine_that_differs_from_the_fingerprints_is_a_finding(dataset: Dataset) -> None:
    manifest = manifest_for_run(
        run_backtest(dataset), dataset, fingerprint(), EngineIdentity("alphalab", "3.7.0")
    )

    findings = assess_reproducibility(manifest).findings

    assert any("fingerprinted under alphalab==3.6.0" in finding for finding in findings)


# --------------------------------------------------------------------------- #
# Study manifests
# --------------------------------------------------------------------------- #


def _study_result(dataset: Dataset, seed: int | None) -> StudyResult:
    """A study result through ``build_result``, the one constructor that derives its id."""

    study = ResearchStudy(
        study_name="v36-study",
        dataset_version=dataset.require_provenance().dataset_version,
        universe=(SYMBOL,),
        features=(FeatureDefinition("mom_2", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=2),),
        horizons=(1,),
        seed=seed,
    )
    return build_result(study, {"mom_2.h1.ic_mean": 0.125, "mom_2.h1.samples": 4.0}, 5.0)


def test_a_study_manifest_takes_its_configuration_from_the_studys_own_identity(
    dataset: Dataset,
) -> None:
    result = _study_result(dataset, seed=11)

    manifest = manifest_for_study(result, dataset, ENGINE)

    assert manifest.kind is ResultKind.STUDY
    assert manifest.result_id == result.result_id
    assert manifest.configuration_id == result.study_id.rpartition("@")[2]
    assert manifest.seed == 11
    assert manifest.seed_role is SeedRole.STOCHASTIC_STEPS
    assert manifest.mode is None
    assert manifest.fingerprint is None
    assert verify_manifest(manifest)


def test_a_study_with_no_seed_records_the_absence_and_invents_nothing(dataset: Dataset) -> None:
    result = _study_result(dataset, seed=None)

    manifest = manifest_for_study(result, dataset, ENGINE)

    assert manifest.seed is None
    assert manifest.seed_role is SeedRole.ABSENT
    assert any(
        "records no seed" in finding for finding in assess_reproducibility(manifest).findings
    )
    assert (
        manifest.manifest_id
        != manifest_for_study(
            _study_result(dataset, seed=0),
            dataset,
            ENGINE,
        ).manifest_id
    )


def test_a_study_manifest_refuses_an_altered_result(dataset: Dataset) -> None:
    result = _study_result(dataset, seed=11)

    with pytest.raises(LifecycleInputError, match="altered"):
        manifest_for_study(replace(result, metrics={"x": 1.0}), dataset, ENGINE)


def test_a_study_fingerprint_must_name_this_study_if_it_names_one(dataset: Dataset) -> None:
    result = _study_result(dataset, seed=11)
    other = _study_result(dataset, seed=12)

    matching = fingerprint(research=research_configuration_for_study(result.study))
    mismatched = fingerprint(research=research_configuration_for_study(other.study))

    assert manifest_for_study(result, dataset, ENGINE, matching).fingerprint == matching
    with pytest.raises(LifecycleInputError, match="researched under study"):
        manifest_for_study(result, dataset, ENGINE, mismatched)


def test_a_study_rerun_reproduces_through_the_same_comparison(dataset: Dataset) -> None:
    first = _study_result(dataset, seed=11)
    second = _study_result(dataset, seed=11)

    assessment = assess_reproducibility(
        manifest_for_study(first, dataset, ENGINE), manifest_for_study(second, dataset, ENGINE)
    )

    assert assessment.rerun is RerunOutcome.REPRODUCED
    assert [requirement.input for requirement in assessment.external_requirements] == [
        ExternalInput.DATASET_BYTES,
        ExternalInput.ENGINE,
    ]


def test_a_live_run_manifest_says_its_venue_cannot_be_recreated(dataset: Dataset) -> None:
    """A manifest built by hand around a LIVE configuration, to read the rule."""

    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    recorded = json.loads(manifest.configuration)
    recorded["mode"] = "ExecutionMode.LIVE"
    live = replace(manifest, configuration=json.dumps(recorded, sort_keys=True))

    requirements = external_requirements(live)

    assert live.mode is ExecutionMode.LIVE
    assert requirements[-1].input is ExternalInput.VENUE_EXECUTIONS
    assert requirements[-1].recreatable is False
    assert not verify_manifest(live), "an edited configuration no longer matches its digest"


def test_no_dependencies_declared_exactly_is_complete(dataset: Dataset) -> None:
    manifest = manifest_for_run(
        run_backtest(dataset), dataset, fingerprint(dependencies=NO_DEPENDENCIES), ENGINE
    )

    assert manifest_gaps(manifest) == ()
