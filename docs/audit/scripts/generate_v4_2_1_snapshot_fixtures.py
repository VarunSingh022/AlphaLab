"""Write the v4.2.1 golden snapshot payloads that v4.3's upgrade tests read (plan WS-01, GAP-07).

This script targets the **v4.2.1 API** and is kept for provenance, not run by
the suite: the payloads it writes are frozen in ``tests/fixtures/snapshots/v4.2.1``
and every later build must keep reading them (or refuse them for the documented
reason). It runs against an export of the tag, with this repository's copy of
the script and of ``tests/regression/v4_2_1_workloads.py``::

    git archive v4.2.1 | tar -x -C "${EXPORT}"
    cd "${EXPORT}" && PYTHONPATH=. python3.12 \\
        "${REPO}/docs/audit/scripts/generate_v4_2_1_snapshot_fixtures.py" "${OUT}"

Run against the ``v4.2.0`` tag with ``--release 4.2.0``, it writes the same
bytes: v4.2.1 changed docstrings and the version string, so one set of payloads
is what both releases wrote (G0 decision 20, D-C1).

What it writes, every file the exact text ``alphalab.persistence`` wrote:

* what ``generate_v3_11_0_snapshot_fixtures.py`` writes -- run unchanged, as it
  was for v3.12.0 and v3.13.0 -- at v4.2.1's schemas: portfolio, OMS, allocation
  (twice), the instrument registry, lifecycle, the FX feed, a backtest run and
  one with trade prints, a live session and its broker, and the run-state store;
* ``replay_*.json``: eleven paused replays (replay schema 3), each of a workload
  ``v4_2_1_workloads.FROZEN_REPLAYS`` names, and ``instrument_*.json``: the
  registry each held (instrument schema 4);
* ``checkpoints/``: a checkpoint chain (checkpoint schema 2, run 5 inside) of
  the replay workload run as a session, and the full capture its last link
  reaches;
* ``evidence_store/``: a reproducibility manifest and a strategy fingerprint in
  a file evidence store (evidence envelope 1);
* ``expected/identities.json``: what the release computed from them -- the
  ``result_id`` of each workload run uninterrupted, each registry's digest,
  each piece of evidence's reference -- and the SHA-256 of every other file it
  wrote. It is under ``expected/`` because the certificate's REP-3 decodes
  every top-level ``*.json`` as a payload.

Before writing a replay, the script holds the release to its own contract: the
payload, read back and resumed with freshly built objects, finishes as the
uninterrupted run does. Every identifier comes from a seeded stream -- each run
seeds its own, and one outer stream covers the rest -- so the script writes the
same bytes on every run against the same source.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[3]

#: The identifier stream every payload not seeded by its own run is written under.
SEED = 4_210


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _import_with_seeded_uuid4(name: str) -> None:
    """Import the test helper ``name`` while ``uuid.uuid4`` draws from a seeded stream.

    ``tests/regression/test_allocation_snapshot.py``, which the earlier
    generator imports, draws its asset and strategy identifiers from ``uuid4``
    as it is imported, so ``allocation.json`` held other identifiers on every
    run. Imported first, here, it holds the same ones, and every payload is the
    same bytes on every run. Only the helper's inputs are seeded: the engine
    mints its own identifiers from ``alphalab.common.ids`` as it always does.
    """

    import uuid
    from unittest import mock

    from alphalab.common.ids import DeterministicIdSource

    source = DeterministicIdSource(SEED)
    with mock.patch("uuid.uuid4", lambda: uuid.UUID(source())):
        importlib.import_module(name)


def _write(root: Path, name: str, payload: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8")
    print("wrote", name, len(payload), "bytes")


def _replays(root: Path, workloads: ModuleType) -> tuple[dict[str, Any], dict[str, str]]:
    from alphalab.backtesting.replay_snapshot import capture, from_primitives
    from alphalab.instrument.snapshot import capture as capture_registry
    from alphalab.instrument.snapshot import registry_digest
    from alphalab.lifecycle import digest_run
    from alphalab.persistence import deserialize, serialize

    replays: dict[str, Any] = {}
    registries: dict[str, str] = {}
    for frozen in workloads.FROZEN_REPLAYS:
        inputs = frozen.inputs()
        paused = inputs.paused(frozen.paused_after)
        payload = serialize(capture(paused))
        whole = digest_run(inputs.uninterrupted()).result_id
        resumed = digest_run(inputs.resumed(from_primitives(deserialize(payload)))).result_id
        assert resumed == whole, f"{frozen.fixture}: resumed to {resumed}, not {whole}"
        _write(root, frozen.fixture, payload)
        replays[frozen.fixture] = {
            "workload": frozen.origin,
            "paused_after": frozen.paused_after,
            "records": len(inputs.dataset.records),
            "uninterrupted_result_id": whole,
        }
        registry = inputs.config.pipeline.instruments
        assert (registry is None) == (frozen.registry is None), frozen.fixture
        if frozen.registry is not None:
            _write(root, frozen.registry, serialize(capture_registry(registry)))
            registries[frozen.registry] = registry_digest(registry)
    return replays, registries


def _checkpoint_chain(root: Path, workloads: ModuleType) -> dict[str, Any]:
    from alphalab.lifecycle import digest_run
    from alphalab.runtime.checkpoint import restore_checkpoints

    links, full, finished = workloads.chain()
    whole = digest_run(finished).result_id
    for index, payload in enumerate(links):
        _write(root, f"checkpoints/chain_{index}.json", payload)
    _write(root, f"checkpoints/full_{workloads.CHAIN_TAKEN[-1]}.json", full)
    restored = restore_checkpoints(links, workloads.chain_objects())
    resumed = digest_run(workloads.chain_continued(restored)).result_id
    assert resumed == whole, f"the chain resumed to {resumed}, not {whole}"
    return {
        "workload": "tests/integration/replay_workload.py, run as a session",
        "taken": list(workloads.CHAIN_TAKEN),
        "records": finished.run.processed,
        "uninterrupted_result_id": whole,
    }


def _evidence(root: Path, workloads: ModuleType) -> list[dict[str, str]]:
    from alphalab.model_registry.evidence import file_evidence_store

    folder = root / "evidence_store"
    folder.mkdir()
    store = file_evidence_store(folder)
    refs = [store.put(value) for value in workloads.evidence()]
    print("wrote evidence_store/:", ", ".join(ref.kind for ref in refs))
    return [{"kind": ref.kind, "identity": ref.identity, "digest": ref.digest} for ref in refs]


def main(root: Path, release: str) -> None:
    import alphalab
    from alphalab.common.ids import id_scope

    source = Path(alphalab.__file__).resolve().parents[1]
    assert alphalab.__version__ == release, (
        f"run against the v{release} tag, not alphalab {alphalab.__version__} at {source}"
    )
    print(f"alphalab {alphalab.__version__} from {source}")
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"{root} is not empty: write a fresh set, never over an old one")
    root.mkdir(parents=True, exist_ok=True)

    earlier = _load(
        Path(__file__).with_name("generate_v3_11_0_snapshot_fixtures.py"),
        "generate_v3_11_0_snapshot_fixtures",
    )
    workloads = _load(REPO / "tests" / "regression" / "v4_2_1_workloads.py", "v4_2_1_workloads")
    _import_with_seeded_uuid4("tests.regression.test_allocation_snapshot")
    with id_scope(SEED):
        earlier.main(root)
        replays, registries = _replays(root, workloads)
        chain = _checkpoint_chain(root, workloads)
        evidence = _evidence(root, workloads)
    # Every file's digest, so a payload edited after it was written -- even in a
    # field no resumed run carries to its end -- is found.
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    identities = {
        "written_by": "docs/audit/scripts/generate_v4_2_1_snapshot_fixtures.py",
        "replays": replays,
        "checkpoints": chain,
        "registries": registries,
        "evidence": evidence,
        "files": files,
    }
    _write(root, "expected/identities.json", json.dumps(identities, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("root", type=Path, help="an empty directory to write the payloads into")
    parser.add_argument("--release", default="4.2.1", help="the release being run (4.2.1)")
    arguments = parser.parse_args()
    main(arguments.root, arguments.release)
