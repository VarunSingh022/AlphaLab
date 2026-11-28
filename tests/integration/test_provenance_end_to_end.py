"""S-11, M1's exit gate: a run, its provenance and a version-2 manifest, reproduced elsewhere.

The plan's end-to-end check for WS-02 (ADR-0053): a run is made, its provenance
issued, and a manifest made under identity version 2 that cites it. The run is
then made again in a fresh interpreter under another hash seed, and must come
out the same. The identities pinned here hold that across hosts, since CI runs
this module on Linux and macOS. The rerun harness, given the manifest, reruns
and reports REPRODUCED under the same contract. A continuation under a wrong
schedule is refused before anything is applied.

The run is example 72's multi-asset composition: a registry-holding run given a
schedule of delivered observations, as a researcher's run is.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestResult,
    ProvenanceError,
    RunProvenance,
    RunSchedule,
    ScheduleCursor,
    ScheduleValidationError,
    payload_provenance,
    run_provenance,
    verify_provenance,
)
from alphalab.lifecycle import (
    RerunOutcome,
    digest_run,
    manifest_for_run,
    rerun_from_manifest,
    running_build,
    running_engine,
)
from alphalab.persistence.serializer import serialize
from alphalab.runtime import ScheduleVerification
from alphalab.runtime.run_snapshot import capture as capture_run

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "72_compose_a_multi_asset_strategy.py"

#: The run's identity and its provenance's, as every host computes them: run records
#: are host-independent (REP-005), and provenance digests only what the run recorded,
#: the schedule it was given and the registry it held. A host that computes another
#: value here has another engine, not another platform.
PINNED_RESULT_ID = "0e3be7cbe98f44575defe7ea3bde2bdc42dbc50c9a684a73ee9f11e8269a4881"
PINNED_PROVENANCE_ID = "218292b0ef6c62779f883efbf44b5110a161611b5fcb79badb0bf3c0ad65d8d1"

#: The whole flow, run in a fresh interpreter: it is given the repository and nothing else.
#: Both processes load the example under one module name: a strategy's fingerprint names
#: its code by module, and the same class under another module name is other code.
_ELSEWHERE = """
import importlib.util, json, sys
from pathlib import Path

example_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("example_72_s11", example_path)
example = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = example
spec.loader.exec_module(example)

from alphalab.backtesting import run_provenance
from alphalab.lifecycle import digest_run, manifest_for_run, running_build, running_engine

data = example.inputs()
result = example.run(data)
result_id = digest_run(result).result_id
provenance = run_provenance(result, schedule=data.schedule(), result_id=result_id)
manifest = manifest_for_run(
    result, data.prices, example.fingerprint(data), running_engine(), build=running_build(),
    schedule=data.schedule(), provenance=provenance, identity_version=2,
)
print(json.dumps({
    "result_id": result_id,
    "provenance_id": provenance.provenance_id,
    "manifest_id": manifest.manifest_id,
}))
"""


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_72_s11", EXAMPLE)
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


@pytest.fixture(scope="module")
def manifest(
    example: ModuleType, data: Any, result: BacktestResult, provenance: RunProvenance
) -> Any:
    return manifest_for_run(
        result,
        data.prices,
        example.fingerprint(data),
        running_engine(),
        build=running_build(),
        schedule=data.schedule(),
        provenance=provenance,
        identity_version=2,
    )


def test_the_run_and_its_provenance_are_the_pinned_ones_on_this_host(
    result: BacktestResult, provenance: RunProvenance
) -> None:
    assert digest_run(result).result_id == PINNED_RESULT_ID
    assert provenance.subject_digest == PINNED_RESULT_ID
    assert provenance.provenance_id == PINNED_PROVENANCE_ID


def test_a_fresh_interpreter_under_another_hash_seed_reproduces_all_three(
    manifest: Any, provenance: RunProvenance
) -> None:
    finished = subprocess.run(
        [sys.executable, "-c", _ELSEWHERE, str(EXAMPLE)],
        cwd=ROOT,
        env={**os.environ, "PYTHONHASHSEED": "20261114", "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout) == {
        "result_id": PINNED_RESULT_ID,
        "provenance_id": provenance.provenance_id,
        "manifest_id": manifest.manifest_id,
    }


def test_the_rerun_harness_reproduces_it_under_the_same_contract(
    example: ModuleType, data: Any, result: BacktestResult, manifest: Any
) -> None:
    report = rerun_from_manifest(
        manifest,
        lambda: example.run(data),
        dataset=data.prices,
        fingerprint=example.fingerprint(data),
        engine=running_engine(),
        build=running_build(),
        original=result,
        schedule=data.schedule(),
    )

    assert report.outcome is RerunOutcome.REPRODUCED, report.detail
    assert report.rerun_manifest is not None
    assert report.rerun_manifest.identity_version == 2
    assert report.rerun_manifest.provenance_id == manifest.provenance_id
    assert report.rerun_manifest.manifest_id == manifest.manifest_id


def test_the_result_is_verified_and_a_continuation_under_a_wrong_schedule_is_refused(
    data: Any, result: BacktestResult, provenance: RunProvenance
) -> None:
    assert verify_provenance(result, provenance, data.schedule()) is ScheduleVerification.VERIFIED
    snapshot = serialize(capture_run(result.run))
    beside = payload_provenance(
        snapshot, schedule=data.schedule(), instruments_digest=provenance.instruments_digest
    )
    assert verify_provenance(snapshot, beside, data.schedule()) is ScheduleVerification.DECLARED

    wrong = RunSchedule()
    with pytest.raises(ProvenanceError):
        verify_provenance(snapshot, beside, wrong)
    with pytest.raises(ScheduleValidationError):
        ScheduleCursor.start(wrong, result.run.last_record_timestamp, expected=beside.schedule)
    resumed = ScheduleCursor.start(
        data.schedule(), result.run.last_record_timestamp, expected=beside.schedule
    )
    assert resumed.binding == beside.schedule
    assert resumed.position == data.schedule().position_after(result.run.last_record_timestamp)


@pytest.mark.parametrize("claimed", ["all but the last observation", "no schedule"])
def test_forged_provenance_the_runs_own_record_contradicts_is_never_verified(
    data: Any, result: BacktestResult, provenance: RunProvenance, claimed: str
) -> None:
    """R53-4: a matching binding is not enough for VERIFIED; the run's record is read too.

    The forgery binds exactly the schedule it is checked against, so every digest
    agrees; only the run's own delivery record -- observations it delivered that
    the claimed schedule does not hold -- shows it is not the run's schedule.
    """

    last = result.run.last_observation
    assert last is not None, "the run delivered observations"
    # Without the run's last delivery: an observation after the data ends was never
    # delivered, so leaving one of those out would contradict nothing.
    kept = [
        entry.item
        for entry in data.schedule().entries
        if entry.kind.value == "observation" and entry.identity != last[1]
    ]
    wrong = None if claimed == "no schedule" else RunSchedule.of(observations=kept)
    forged = RunProvenance(
        "result",
        provenance.subject_digest,
        (wrong if wrong is not None else RunSchedule()).binding,
        provenance.instruments_digest,
    )

    with pytest.raises(ProvenanceError, match=r"delivered|delivery"):
        verify_provenance(result, forged, wrong)
    with pytest.raises(ProvenanceError):
        run_provenance(result, schedule=wrong)
