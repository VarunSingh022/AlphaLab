"""Every payload v4.2.1 wrote is read by this build: ``tests/fixtures/snapshots/v4.2.1``.

The fixtures were produced by the v4.2.1 tag's own code (see the README there)
and are byte for byte what v4.2.0 wrote. They are the first frozen payloads of
the formats v4.1.1 and v4.2.0 moved or added -- instrument 4, replay 3, live 3 --
and of the terms a run writes only when it declares them: algorithm and queue
books, contract terms, margin, financing, borrow and recalls, elections and
payment amendments, auction books, delivered surfaces and auctions.

The oracle is the one the earlier releases' payloads are held to, made exact,
and one more. A subsystem that has not moved re-captures what it read byte for
byte. A paused replay or a checkpoint chain, given back to the workload that
wrote it with freshly built objects, finishes with the ``result_id`` v4.2.1
computed for the run that never stopped -- the identity a researcher's stored
result is filed under (plan WS-01; the M0 exit gate).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path
from typing import Any

import pytest

from alphalab.lifecycle import digest_run
from alphalab.persistence import FileRunStateStore, RunStateRef, deserialize, serialize
from tests.regression import v4_2_1_workloads as workloads

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "snapshots" / "v4.2.1"
IDENTITIES: dict[str, Any] = json.loads(
    (FIXTURES / "expected" / "identities.json").read_text(encoding="utf-8")
)

#: Which decoder reads each top-level payload, by file name -- the certificate's REP-3 map.
DECODERS = {
    "portfolio": "alphalab.portfolio.snapshot",
    "oms": "alphalab.oms.snapshot",
    "allocation": "alphalab.allocation.snapshot",
    "instrument": "alphalab.instrument.snapshot",
    "lifecycle": "alphalab.lifecycle.snapshot",
    "fx_feed": "alphalab.portfolio.fx_feed",
    "broker": "alphalab.broker.snapshot",
    "run": "alphalab.runtime.run_snapshot",
    "live": "alphalab.runtime.live_snapshot",
    "replay": "alphalab.backtesting.replay_snapshot",
}
PAYLOADS = sorted(path.name for path in FIXTURES.glob("*.json"))
REPLAYS = {frozen.fixture: frozen for frozen in workloads.FROZEN_REPLAYS}


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _decoder(name: str) -> str:
    stem = Path(name).stem
    (module,) = [
        module
        for prefix, module in DECODERS.items()
        if stem == prefix or stem.startswith(prefix + "_")
    ]
    return module


def test_every_durable_kind_and_envelope_state_model_lists_is_frozen() -> None:
    """Acceptance (a) of WS-01: every row of STATE_MODEL's durability table has a payload."""

    text = (ROOT / "docs" / "STATE_MODEL.md").read_text(encoding="utf-8")
    table = re.findall(r"^\| `\w+` \| `([\w.]+)` \| `\w+` \| \d+ \|$", text, re.MULTILINE)
    decoded = {_decoder(name).removeprefix("alphalab.") for name in PAYLOADS}

    assert len(table) == 11
    # The pipeline is the run's: every run, live and replay payload nests one.
    assert set(table) - decoded == {"runtime.snapshot"}
    assert {deserialize(_text("run_backtest.json"))["pipeline"]["schema_version"]} == {8}
    assert list((FIXTURES / "run_store").rglob("*.runstate"))
    assert len(list((FIXTURES / "checkpoints").glob("chain_*.json"))) == 3
    assert list((FIXTURES / "evidence_store" / "artifacts").rglob("*.artifact"))
    assert set(REPLAYS) == {name for name in PAYLOADS if name.startswith("replay_")}


def test_no_frozen_file_was_edited_after_v4_2_1_wrote_it() -> None:
    """Each file is the bytes the generator wrote, and no file is missing or added.

    A resumed run carries only what it still needs to its end, so a payload
    edited in a field the run drops later -- an allocation contribution of an
    order that completes -- would still resume to its ``result_id``. The
    digests the generator recorded are what finds that edit.
    """

    on_disk = {
        path.relative_to(FIXTURES).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(FIXTURES.rglob("*"))
        if path.is_file() and path.name != "README.md"
    }
    on_disk.pop("expected/identities.json")

    assert on_disk == IDENTITIES["files"]


@pytest.mark.parametrize("name", PAYLOADS)
def test_every_payload_is_read_and_written_back_byte_for_byte(name: str) -> None:
    """No subsystem moved since v4.2.1, so reading one changes nothing and warns of nothing."""

    text = _text(name)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        snapshot = importlib.import_module(_decoder(name)).from_primitives(json.loads(text))

    assert [str(item.message) for item in caught] == []
    assert serialize(snapshot) == text.rstrip("\n")


@pytest.mark.parametrize("fixture", sorted(REPLAYS))
def test_a_paused_replay_resumes_to_the_result_v4_2_1_computed(fixture: str) -> None:
    from alphalab.backtesting.replay_snapshot import from_primitives

    frozen, recorded = REPLAYS[fixture], IDENTITIES["replays"][fixture]
    assert (recorded["workload"], recorded["paused_after"]) == (frozen.origin, frozen.paused_after)
    snapshot = from_primitives(json.loads(_text(fixture)))

    resumed = frozen.inputs().resumed(snapshot)

    assert digest_run(resumed).result_id == recorded["uninterrupted_result_id"]


@pytest.mark.parametrize(
    "fixture", sorted(name for name, frozen in REPLAYS.items() if frozen.registry is not None)
)
def test_each_registry_is_the_one_its_replay_held(fixture: str) -> None:
    """Read back, a registry has the digest its replay recorded (FUT-041) and v4.2.1 computed."""

    from alphalab.instrument.snapshot import from_primitives, registry_digest, restore

    frozen = REPLAYS[fixture]
    assert frozen.registry is not None
    registry = restore(from_primitives(json.loads(_text(frozen.registry))))
    digest = registry_digest(registry)

    assert digest == IDENTITIES["registries"][frozen.registry]
    assert deserialize(_text(fixture))["instruments_digest"] == digest
    held = frozen.inputs().config.pipeline.instruments
    assert held is not None and registry_digest(held) == digest


def test_the_checkpoint_chain_reads_as_its_full_capture_and_continues_to_its_result() -> None:
    from alphalab.common.serialization import to_serializable
    from alphalab.runtime.checkpoint import read_checkpoints, restore_checkpoints
    from alphalab.runtime.run_snapshot import from_primitives

    recorded = IDENTITIES["checkpoints"]
    assert tuple(recorded["taken"]) == workloads.CHAIN_TAKEN
    folder = FIXTURES / "checkpoints"
    chain = [
        (folder / f"chain_{index}.json").read_text(encoding="utf-8").rstrip("\n")
        for index in range(len(workloads.CHAIN_TAKEN))
    ]
    assert {deserialize(payload)["schema_version"] for payload in chain} == {2}
    full = (folder / f"full_{workloads.CHAIN_TAKEN[-1]}.json").read_text(encoding="utf-8")

    assert to_serializable(read_checkpoints(chain)) == to_serializable(
        from_primitives(deserialize(full))
    )
    restored = restore_checkpoints(chain, workloads.chain_objects())
    continued = workloads.chain_continued(restored)
    assert digest_run(continued).result_id == recorded["uninterrupted_result_id"]


def test_the_run_store_file_holds_the_backtest_payload() -> None:
    store = FileRunStateStore(FIXTURES / "run_store")

    assert store.get(RunStateRef("golden-run", 1)) == _text("run_backtest.json").rstrip("\n")


def test_the_evidence_store_reads_back_verified_and_this_build_computes_the_same() -> None:
    """The envelope v4.2.1 wrote is read, and the values in it are what this build makes.

    A manifest's ``manifest_id`` is the identity a result is filed under: the
    lifecycle harness's run, made again by this build, files under the same one.
    """

    from alphalab.model_registry.evidence import EvidenceRef, file_evidence_store

    store = file_evidence_store(FIXTURES / "evidence_store")
    values = workloads.evidence()
    for recorded, value in zip(IDENTITIES["evidence"], values, strict=True):
        ref = store.find(recorded["kind"], recorded["identity"])
        assert ref == EvidenceRef(recorded["kind"], recorded["identity"], recorded["digest"])
        assert (value.evidence_kind, value.evidence_identity) == (ref.kind, ref.identity)
        assert store.get(ref).value == deserialize(serialize(value))


_RESUME_ELSEWHERE = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from alphalab.backtesting.replay_snapshot import from_primitives
    from alphalab.lifecycle import digest_run
    from tests.regression import v4_2_1_workloads as workloads
    frozen = {f.fixture: f for f in workloads.FROZEN_REPLAYS}[sys.argv[1]]
    payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(digest_run(frozen.inputs().resumed(from_primitives(payload))).result_id)
    """
)


def test_a_paused_replay_resumes_in_a_fresh_process_to_the_same_result() -> None:
    """Another interpreter, another hash seed: the payload and the workload are all it is given."""

    fixture = "replay_option_life.json"
    finished = subprocess.run(
        [sys.executable, "-c", _RESUME_ELSEWHERE, fixture, str(FIXTURES / fixture)],
        cwd=ROOT,
        env={**os.environ, "PYTHONHASHSEED": "20261110", "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == IDENTITIES["replays"][fixture]["uninterrupted_result_id"]
