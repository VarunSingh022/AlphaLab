"""Artifacts through the lifecycle they exist for.

``test_artifact_store.py`` holds the store's own boundary contract. This holds
the thing that contract was built for: bytes produced by a run, stored, named by
an ``ArtifactRef``, carried on a ``ModelVersion`` through the registry, and read
back and verified by a later reader who has only the reference.

Before v2.15 every step but the first and last of that was already in place and
the middle was a hole -- a version could cite an artifact nobody could fetch, and
record a checksum nobody computed.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.state import BacktestResult
from alphalab.model_registry.artifact_store import (
    FileArtifactStore,
    MemoryArtifactStore,
    compute_digest,
)
from alphalab.model_registry.registry import (
    ArtifactRef,
    ModelRegistry,
    get_version,
    register_model,
)
from alphalab.persistence.exceptions import StorageError
from alphalab.persistence.run_store import MemoryRunStateStore
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.runtime.run_snapshot import capture, from_primitives
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

_STRATEGY = "ARTIFACT-STRAT"
_ASSET = "8f14e45f-ceea-467a-9c2a-1b3c5d7e9f01"
_SEED = 1234


def _finished_run() -> BacktestResult:
    """One real seeded backtest, run through the actual execution path."""

    return BacktestEngine.run(
        backtest_config(_STRATEGY, seed=_SEED),
        dataset_of_quotes(_ASSET, [Decimal("100"), Decimal("101"), Decimal("102")]),
        running_strategy_state(
            _STRATEGY, ScriptedStrategy(_STRATEGY, _ASSET, {2.0: Decimal("10")})
        ),
        context_factory,
    )


def test_a_run_result_becomes_an_artifact_and_comes_back_intact(tmp_path: Path) -> None:
    """The lifecycle the release contract names: store -> ref -> retrieve -> verify."""

    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)

    result = _finished_run()
    payload = serialize(capture(result.run)).encode("utf-8")

    ref = store.put(payload, "application/json")
    retrieved = store.get(ref)

    assert retrieved == payload
    assert ref.checksum == compute_digest(payload)
    assert ref.size_bytes == len(payload)

    # And what came back is still a decodable run snapshot, not merely equal
    # bytes: `from_primitives` is the same door the durable-state path uses.
    snapshot = from_primitives(deserialize(retrieved.decode("utf-8")))
    assert snapshot.processed == result.run.processed
    assert snapshot.source_id == result.run.source_id


def test_a_model_version_can_cite_an_artifact_that_can_actually_be_fetched() -> None:
    """The hole v2.4 left: a reference nobody could resolve now resolves."""

    store = MemoryArtifactStore()
    weights = b"\x00\x01\x02 pretend these are trained parameters"
    ref = store.put(weights, "application/octet-stream")

    registry, version = register_model(ModelRegistry(), "momentum", object(), 1.0, artifact=ref)

    cited = get_version(registry, "momentum", version.version).artifact
    assert cited is not None
    assert store.get(cited) == weights, "the registry's reference resolves to the bytes"


def test_a_reader_holding_only_the_reference_can_verify_what_it_gets() -> None:
    """A later reader has the registry snapshot and no other context."""

    store = MemoryArtifactStore()
    payload = b"an artifact that will be checked later"
    registry, version = register_model(
        ModelRegistry(), "alpha", object(), 1.0, artifact=store.put(payload)
    )

    # Everything a later process has: the reference, off the version.
    reference = get_version(registry, "alpha", version.version).artifact
    assert reference is not None

    fetched = store.get(reference)
    assert compute_digest(fetched) == reference.checksum


def test_the_file_behind_a_version_changing_is_detectable(tmp_path: Path) -> None:
    """`ArtifactRef`'s stated purpose, finally true.

    Its docstring has always said a reference is "what lets a later reader
    detect that the file behind a version changed". Nothing could detect it,
    because nothing read the bytes.
    """

    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)
    registry, version = register_model(
        ModelRegistry(), "drifting", object(), 1.0, artifact=store.put(b"version one bytes")
    )

    reference = get_version(registry, "drifting", version.version).artifact
    assert reference is not None
    next(root.rglob("*.artifact")).write_bytes(b"somebody replaced this")

    with pytest.raises(StorageError, match="does not match the digest"):
        store.get(reference)


def test_two_versions_of_identical_bytes_share_one_artifact() -> None:
    """Content addressing: retraining to the same weights stores them once."""

    store = MemoryArtifactStore()
    weights = b"identical weights"

    registry, first = register_model(
        ModelRegistry(), "twin", object(), 1.0, artifact=store.put(weights)
    )
    registry, second = register_model(registry, "twin", object(), 2.0, artifact=store.put(weights))

    assert first.version != second.version, "two versions"
    assert first.artifact == second.artifact, "one artifact"


def test_artifact_storage_and_run_state_storage_stay_separate_owners() -> None:
    """Two stores, two questions, neither reachable from the other.

    `RunStateStore` addresses `(run_id, sequence)` and holds a `str`; the
    artifact store addresses content and holds `bytes`. This asserts they have
    not quietly become one thing with two spellings.
    """

    artifacts = MemoryArtifactStore()
    run_states = MemoryRunStateStore()

    result = _finished_run()
    payload = serialize(capture(result.run))

    state_ref = run_states.put("run-a", 0, payload)
    artifact_ref = artifacts.put(payload.encode("utf-8"), "application/json")

    assert str(state_ref) == "run-a@0", "run state is addressed by run and checkpoint"
    assert artifact_ref.uri.startswith("alphalab-artifact:sha256:")
    assert run_states.get(state_ref) == payload
    assert artifacts.get(artifact_ref).decode("utf-8") == payload

    # Neither store answers the other's question.
    assert not hasattr(run_states, "contains")
    assert not hasattr(artifacts, "latest")


def test_an_artifact_reference_survives_serialization_of_the_registry() -> None:
    """A reference written into a snapshot on one machine resolves on another.

    This is what content addressing buys and what a `file://` uri would have
    cost: the reference names bytes, not a location.
    """

    producer = MemoryArtifactStore()
    payload = b"portable artifact bytes"
    ref = producer.put(payload)

    # A different machine, a different store, the same bytes obtained some other
    # way -- the reference it produces is the same one.
    consumer = MemoryArtifactStore()
    assert consumer.put(payload) == ref

    rehydrated = ArtifactRef(
        uri=ref.uri, media_type=ref.media_type, checksum=ref.checksum, size_bytes=ref.size_bytes
    )
    assert consumer.get(rehydrated) == payload
