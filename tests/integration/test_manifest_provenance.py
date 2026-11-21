"""A manifest held to the run's provenance, and the opt-in identity version 2 (ADR-0053 section 6).

A manifest names the schedule its producer says the run was given, and until
v4.3 nothing could refuse one naming less (ledger FUT-046): a run's record
keeps no trace of its rates. Given the run's provenance, ``manifest_for_run``
refuses every claim the provenance contradicts. Under identity version 1 the
manifest's identity is unchanged by it. Identity version 2 -- ADR-0052's option
C, opt-in -- records the registry the run held and cites the provenance.

The run is example 72's multi-asset composition, which holds a registry and is
given a schedule of delivered observations.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.backtesting import (
    RUN_SCHEDULE_DIGEST_SCHEME,
    BacktestResult,
    RunProvenance,
    RunSchedule,
    run_provenance,
)
from alphalab.lifecycle import (
    RerunOutcome,
    assess_reproducibility,
    canonical_manifest_key,
    manifest_for_run,
    manifest_gaps,
    running_build,
    running_engine,
    verify_manifest,
)
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.runtime import ScheduleBinding

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "72_compose_a_multi_asset_strategy.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_72_provenance", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(example: ModuleType) -> Any:
    return example.inputs()


@pytest.fixture(scope="module")
def result(example: ModuleType, data: Any) -> BacktestResult:
    run: BacktestResult = example.run(data)
    return run


@pytest.fixture(scope="module")
def provenance(data: Any, result: BacktestResult) -> RunProvenance:
    return run_provenance(result, schedule=data.schedule())


def manifest(example: ModuleType, data: Any, result: BacktestResult, **kw: Any) -> Any:
    schedule = kw.pop("schedule", data.schedule())
    return manifest_for_run(
        result,
        data.prices,
        example.fingerprint(data),
        running_engine(),
        build=running_build(),
        schedule=schedule,
        **kw,
    )


def test_under_version_1_provenance_is_checked_and_moves_no_identity(
    example: ModuleType, data: Any, result: BacktestResult, provenance: RunProvenance
) -> None:
    checked = manifest(example, data, result, provenance=provenance)

    assert checked == manifest(example, data, result)
    assert checked.identity_version == 1 and checked.provenance_id is None
    assert "identity.version" not in canonical_manifest_key(
        checked.kind,
        checked.result_id,
        checked.dataset_version,
        checked.dataset_content_hash,
        checked.configuration_id,
        checked.seed,
        checked.seed_role,
        checked.engine,
        None if checked.fingerprint is None else checked.fingerprint.fingerprint,
        checked.build,
        checked.schedule_digest,
    )


@pytest.mark.parametrize(
    ("edit", "named", "refused"),
    [
        (lambda p: replace(p, subject_digest="0" * 64), "data", "provenance of another run"),
        (
            lambda p: replace(p, schedule=ScheduleBinding(RUN_SCHEDULE_DIGEST_SCHEME, "1" * 64)),
            "data",
            "not the schedule this run was given",
        ),
        (lambda p: p, "none", "no schedule is named"),
        (
            lambda p: replace(p, schedule=RunSchedule().binding),
            "data",
            "says it was given none",
        ),
        (lambda p: replace(p, instruments_digest="0" * 64), "data", "under other terms"),
    ],
    ids=["another run", "another schedule", "none named", "one named where none", "registry"],
)
def test_a_manifest_the_runs_provenance_contradicts_is_refused(
    example: ModuleType,
    data: Any,
    result: BacktestResult,
    provenance: RunProvenance,
    edit: Any,
    named: str,
    refused: str,
) -> None:
    schedule = None if named == "none" else data.schedule()

    with pytest.raises(LifecycleInputError, match=refused):
        manifest(example, data, result, schedule=schedule, provenance=edit(provenance))


def test_version_2_records_the_registry_and_cites_the_provenance(
    example: ModuleType, data: Any, result: BacktestResult, provenance: RunProvenance
) -> None:
    first = manifest(example, data, result)
    second = manifest(example, data, result, provenance=provenance, identity_version=2)

    assert second.identity_version == 2
    assert canonical_manifest_key(
        second.kind,
        second.result_id,
        second.dataset_version,
        second.dataset_content_hash,
        second.configuration_id,
        second.seed,
        second.seed_role,
        second.engine,
        None if second.fingerprint is None else second.fingerprint.fingerprint,
        second.build,
        second.schedule_digest,
        second.dataset_parts,
        second.execution_plan_id,
        second.queue_model,
        second.instruments_digest,
        second.provenance_id,
        second.identity_version,
    ).endswith("\nidentity.version=2"), "the contract is named in the key it identifies"
    assert second.instruments_digest == provenance.instruments_digest is not None
    assert second.provenance_id == provenance.provenance_id
    assert verify_manifest(second)
    assert second.manifest_id != first.manifest_id, "a new contract, a new identity, by design"
    assert manifest_gaps(second) == manifest_gaps(first)
    without = manifest(example, data, result, identity_version=2)
    assert without.instruments_digest == second.instruments_digest
    assert without.provenance_id is None and without.manifest_id != second.manifest_id


def test_a_version_2_manifest_lacking_its_registry_is_a_gap_and_version_1_keeps_its_finding(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    second = manifest(example, data, result, identity_version=2)
    lacking = replace(second, instruments_digest=None)

    assert any(
        "identity version 2 records the instrument registry" in g for g in manifest_gaps(lacking)
    )
    first = manifest(example, data, result)
    assert not any("identity version 2" in g for g in manifest_gaps(first))
    assert any("names by type alone" in f for f in assess_reproducibility(first).findings)
    assert not any("names by type alone" in f for f in assess_reproducibility(second).findings)


def test_an_identity_version_this_build_does_not_make_is_refused(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    with pytest.raises(LifecycleInputError, match="identity version 1 or 2"):
        manifest(example, data, result, identity_version=3)
    assert not verify_manifest(replace(manifest(example, data, result), identity_version=3))


def test_a_version_2_rerun_of_a_version_1_original_is_compared_by_version_1s_rules(
    example: ModuleType, data: Any, result: BacktestResult, provenance: RunProvenance
) -> None:
    original = manifest(example, data, result)
    rerun = manifest(example, data, result, provenance=provenance, identity_version=2)

    assessment = assess_reproducibility(original, rerun)

    assert assessment.rerun is RerunOutcome.REPRODUCED
    assert any("compared by version 1's rules" in line for line in assessment.rerun_detail)


def test_a_version_2_manifest_without_provenance_says_its_schedule_is_unattested(
    example: ModuleType, data: Any, result: BacktestResult, provenance: RunProvenance
) -> None:
    """TD-07: the finding belongs to the contract that asks for provenance, version 2 alone.

    A version-1 manifest never cites provenance, so the finding there would be on
    every manifest ever made, and a finding is a change to every assessment.
    """

    unattested = "without the run's provenance"
    findings = {
        "version 1": assess_reproducibility(manifest(example, data, result)).findings,
        "version 2, attested": assess_reproducibility(
            manifest(example, data, result, provenance=provenance, identity_version=2)
        ).findings,
        "version 2, unattested": assess_reproducibility(
            manifest(example, data, result, identity_version=2)
        ).findings,
    }

    assert {name: any(unattested in f for f in found) for name, found in findings.items()} == {
        "version 1": False,
        "version 2, attested": False,
        "version 2, unattested": True,
    }


def test_a_version_1_manifest_is_stored_as_v4_2_1_stored_it_and_version_2_says_what_it_is(
    example: ModuleType, data: Any, result: BacktestResult, provenance: RunProvenance
) -> None:
    """An evidence store refuses another value under an identity it holds, so the bytes count.

    A version-1 manifest made now serializes without the two fields v4.3 added --
    the value a store holding v4.2.1's copy of it already has; a version-2
    manifest carries its contract and its provenance in what is stored.
    """

    first = deserialize(serialize(manifest(example, data, result, provenance=provenance)))
    second = deserialize(
        serialize(manifest(example, data, result, provenance=provenance, identity_version=2))
    )

    assert "identity_version" not in first and "provenance_id" not in first
    assert second["identity_version"] == 2
    assert second["provenance_id"] == provenance.provenance_id
