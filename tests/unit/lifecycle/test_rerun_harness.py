"""Re-executing a run from its manifest (ledger REP-003, OFE-020, v3.13).

Every rerun here is a real backtest through the execution path. The harness is
pinned for the four things it must do: refuse other inputs before running,
reproduce what reproduces, call a divergence a divergence and say where it is,
and refuse what it cannot rerun.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.backtesting.state import BacktestResult
from alphalab.data.dataset import Dataset
from alphalab.lifecycle import (
    RERUN_DIFFERENCE_LIMIT,
    EngineBuild,
    EngineIdentity,
    LifecycleInputError,
    ReproducibilityManifest,
    RerunOutcome,
    manifest_for_run,
    rerun_from_manifest,
)
from tests.unit.lifecycle.evidence_harness import (
    BUILD,
    CLOSES,
    ENGINE,
    fingerprint,
    ingest,
    run_backtest,
)


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return ingest()


@pytest.fixture(scope="module")
def original(dataset: Dataset) -> BacktestResult:
    return run_backtest(dataset)


@pytest.fixture(scope="module")
def manifest(dataset: Dataset, original: BacktestResult) -> ReproducibilityManifest:
    return manifest_for_run(original, dataset, fingerprint(), ENGINE, build=BUILD)


def _never() -> BacktestResult:
    raise AssertionError("a rerun of other inputs must not run")


def test_the_same_inputs_reproduce_the_run(
    dataset: Dataset, original: BacktestResult, manifest: ReproducibilityManifest
) -> None:
    report = rerun_from_manifest(
        manifest,
        lambda: run_backtest(dataset),
        dataset=dataset,
        fingerprint=fingerprint(),
        engine=ENGINE,
        build=BUILD,
        original=original,
    )
    assert report.outcome is RerunOutcome.REPRODUCED
    assert report.differences == ()
    assert report.rerun_manifest is not None
    assert report.rerun_manifest.result_id == manifest.result_id
    assert report.manifest_id == manifest.manifest_id


def test_a_divergence_is_reported_with_where_the_records_part(
    dataset: Dataset, original: BacktestResult, manifest: ReproducibilityManifest
) -> None:
    """The same configuration trading differently: only the record can tell."""

    report = rerun_from_manifest(
        manifest,
        lambda: run_backtest(dataset, plan={1: Decimal("5")}),
        dataset=dataset,
        fingerprint=fingerprint(),
        engine=ENGINE,
        build=BUILD,
        original=original,
    )
    assert report.outcome is RerunOutcome.DIVERGED
    assert 0 < len(report.differences) <= RERUN_DIFFERENCE_LIMIT
    assert all(" -> " in line for line in report.differences)
    assert any(line.startswith("pipeline.") for line in report.differences)
    # Without the original, the verdict stands and the location is not claimed.
    unlocated = rerun_from_manifest(
        manifest,
        lambda: run_backtest(dataset, plan={1: Decimal("5")}),
        dataset=dataset,
        fingerprint=fingerprint(),
        engine=ENGINE,
        build=BUILD,
    )
    assert unlocated.outcome is RerunOutcome.DIVERGED
    assert unlocated.differences == ()


def test_another_dataset_or_engine_is_refused_before_anything_runs(
    manifest: ReproducibilityManifest,
) -> None:
    other = ingest("V36-OTHER", tuple(close + Decimal("1") for close in CLOSES))
    report = rerun_from_manifest(
        manifest, _never, dataset=other, fingerprint=fingerprint(), engine=ENGINE, build=BUILD
    )
    assert report.outcome is RerunOutcome.INPUTS_DIFFER
    assert report.rerun_manifest is None
    assert any(line.startswith("dataset") for line in report.detail)

    report = rerun_from_manifest(
        manifest,
        _never,
        dataset=ingest(),
        fingerprint=fingerprint(),
        engine=EngineIdentity("alphalab", "0.0.1"),
        build=BUILD,
    )
    assert report.outcome is RerunOutcome.INPUTS_DIFFER
    assert any(line.startswith("engine") for line in report.detail)

    report = rerun_from_manifest(
        manifest, _never, dataset=ingest(), fingerprint=fingerprint(), engine=ENGINE, build=None
    )
    assert report.outcome is RerunOutcome.INPUTS_DIFFER
    assert any("engine build" in line for line in report.detail)


def test_each_recorded_input_is_compared_on_its_own_and_named(
    dataset: Dataset, manifest: ReproducibilityManifest
) -> None:
    """One difference at a time, each named in the detail, and nothing run.

    A provenance is a value its caller can build, so a dataset can carry the
    recorded version label over other bytes: the content is compared as well as
    the label. The build's source and its time-zone database are compared one
    by one, so a rerun on another engine source -- the same version number, say,
    patched -- is refused for what it is.
    """

    def differences(rerun_on: Dataset, build: EngineBuild) -> list[str]:
        report = rerun_from_manifest(
            manifest,
            _never,
            dataset=rerun_on,
            fingerprint=fingerprint(),
            engine=ENGINE,
            build=build,
        )
        assert report.outcome is RerunOutcome.INPUTS_DIFFER
        assert report.rerun_manifest is None and report.assessment is None
        assert report.detail[-1].startswith("nothing was run")
        return [line.split(":", 1)[0] for line in report.detail[:-1]]

    provenance = dataset.require_provenance()
    relabelled = replace(
        dataset,
        provenance=replace(provenance, source=replace(provenance.source, content_hash="0" * 64)),
    )
    relabelled_provenance = relabelled.require_provenance()
    assert relabelled_provenance.dataset_version == manifest.dataset_version
    assert relabelled_provenance.content_hash != manifest.dataset_content_hash
    assert differences(relabelled, BUILD) == ["dataset content"]

    assert differences(dataset, replace(BUILD, source_digest="f" * 64)) == ["engine source"]
    assert differences(dataset, replace(BUILD, tz_database="1970a")) == ["time-zone database"]


def test_a_rerun_with_another_seed_is_of_other_inputs(
    dataset: Dataset, manifest: ReproducibilityManifest
) -> None:
    report = rerun_from_manifest(
        manifest,
        lambda: run_backtest(dataset, seed=7),
        dataset=dataset,
        fingerprint=fingerprint(),
        engine=ENGINE,
        build=BUILD,
    )
    assert report.outcome is RerunOutcome.INPUTS_DIFFER
    assert any(line.startswith("seed") for line in report.detail)


def test_an_unseeded_rerun_cannot_reproduce_and_says_so(
    dataset: Dataset, manifest: ReproducibilityManifest
) -> None:
    report = rerun_from_manifest(
        manifest,
        lambda: run_backtest(dataset, seed=None),
        dataset=dataset,
        fingerprint=fingerprint(),
        engine=ENGINE,
        build=BUILD,
    )
    assert report.outcome is RerunOutcome.INPUTS_DIFFER
    assert "unseeded" in report.detail[0]


def test_what_cannot_be_rerun_is_refused(
    dataset: Dataset, original: BacktestResult, manifest: ReproducibilityManifest
) -> None:
    altered = replace(manifest, result_id="0" * 64)
    with pytest.raises(LifecycleInputError, match="does not verify"):
        rerun_from_manifest(
            altered, _never, dataset=dataset, fingerprint=fingerprint(), engine=ENGINE, build=BUILD
        )
    other_run = run_backtest(dataset, plan={1: Decimal("5")})
    with pytest.raises(LifecycleInputError, match="not the one the manifest identifies"):
        rerun_from_manifest(
            manifest,
            _never,
            dataset=dataset,
            fingerprint=fingerprint(),
            engine=ENGINE,
            build=BUILD,
            original=other_run,
        )
