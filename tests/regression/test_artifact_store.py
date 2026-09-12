"""The artifact store's boundary contract, across both backends.

``ArtifactRef`` has recorded a checksum since v2.4 and nothing computed one:
"AlphaLab never computes it, because it never reads the bytes." These tests are
what make that sentence false.

Every behavioural test runs against **both** backends through the same
``ArtifactStore`` protocol, because a deterministic double that does not behave
like the real store is worse than no double -- it makes a test pass and a
production run fail. The parametrization is the point, not a convenience.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alphalab.common.ids import DeterministicIdSource, use_id_source
from alphalab.model_registry.artifact_store import (
    ARTIFACT_URI_SCHEME,
    DEFAULT_MEDIA_TYPE,
    ArtifactStore,
    FileArtifactStore,
    MemoryArtifactStore,
    artifact_uri,
    compute_digest,
    digest_of,
    verify_artifact,
)
from alphalab.model_registry.registry import ArtifactRef
from alphalab.persistence.exceptions import PersistenceValidationError, StorageError

_PAYLOAD = b"model-weights-or-whatever-the-caller-encoded"
_OTHER = b"a completely different artifact"


@pytest.fixture(params=["memory", "file"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ArtifactStore]:
    """One store of each backend, behind the protocol both implement."""

    if request.param == "memory":
        yield MemoryArtifactStore()
    else:
        root = tmp_path / "artifacts"
        root.mkdir()
        yield FileArtifactStore(root)


# ---------------------------------------------------------------------------
# Write, read, identity
# ---------------------------------------------------------------------------


def test_an_artifact_round_trips(store: ArtifactStore) -> None:
    ref = store.put(_PAYLOAD)
    assert store.get(ref) == _PAYLOAD


def test_the_reference_records_what_the_registry_asked_for(store: ArtifactStore) -> None:
    """`ArtifactRef`'s four fields, all now derived rather than claimed."""

    ref = store.put(_PAYLOAD, "application/octet-stream")

    assert ref.uri.startswith(ARTIFACT_URI_SCHEME)
    assert ref.media_type == "application/octet-stream"
    assert ref.checksum == compute_digest(_PAYLOAD)
    assert ref.size_bytes == len(_PAYLOAD)


def test_the_store_produces_the_canonical_reference_type(store: ArtifactStore) -> None:
    """No second reference type was introduced. This is `model_registry`'s own."""

    assert isinstance(store.put(_PAYLOAD), ArtifactRef)


def test_identity_is_the_content_and_nothing_else(store: ArtifactStore) -> None:
    """Two callers storing identical bytes name one artifact."""

    first = store.put(_PAYLOAD)
    second = store.put(_PAYLOAD)

    assert first == second
    assert first.uri == second.uri


def test_different_content_is_a_different_artifact(store: ArtifactStore) -> None:
    assert store.put(_PAYLOAD).uri != store.put(_OTHER).uri


def test_identity_is_deterministic_across_stores(tmp_path: Path) -> None:
    """Two independently configured stores agree with no shared state.

    The property `derive_asset_id` gives instruments, applied to bytes: identity
    is computed, not allocated, so two environments that never met still name
    the same artifact the same way.
    """

    root = tmp_path / "one"
    root.mkdir()

    assert FileArtifactStore(root).put(_PAYLOAD) == MemoryArtifactStore().put(_PAYLOAD)


def test_retrieval_is_repeatable(store: ArtifactStore) -> None:
    ref = store.put(_PAYLOAD)
    assert store.get(ref) == store.get(ref) == store.get(ref) == _PAYLOAD


def test_an_empty_artifact_is_a_real_artifact(store: ArtifactStore) -> None:
    """Zero bytes hash to something, so they are storable and retrievable."""

    ref = store.put(b"")
    assert ref.size_bytes == 0
    assert store.get(ref) == b""


def test_a_large_artifact_round_trips(store: ArtifactStore) -> None:
    payload = bytes(range(256)) * 4096  # 1 MiB
    ref = store.put(payload)
    assert store.get(ref) == payload
    assert ref.size_bytes == len(payload)


def test_binary_content_is_not_decoded_or_normalized(store: ArtifactStore) -> None:
    """The store knows nothing about what it holds, including its encoding."""

    payload = b"\x00\xff\xfe\r\n\x1b[31mnot text\x00"
    assert store.get(store.put(payload)) == payload


# ---------------------------------------------------------------------------
# Verification, corruption, absence
# ---------------------------------------------------------------------------


def test_a_corrupted_artifact_is_refused_not_returned(tmp_path: Path) -> None:
    """The whole reason a digest is recorded. Altered bytes are never returned."""

    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)
    ref = store.put(_PAYLOAD)

    stored = next(root.rglob("*.artifact"))
    stored.write_bytes(_PAYLOAD + b"tampered")

    with pytest.raises(StorageError, match="does not match the digest"):
        store.get(ref)


def test_a_truncated_artifact_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)
    ref = store.put(_PAYLOAD)

    next(root.rglob("*.artifact")).write_bytes(_PAYLOAD[:10])

    with pytest.raises(StorageError, match="truncated or altered"):
        store.get(ref)


def test_a_missing_artifact_raises_and_says_so(store: ArtifactStore) -> None:
    absent = ArtifactRef(uri=artifact_uri(compute_digest(b"never stored")))

    with pytest.raises(StorageError, match="No artifact is stored"):
        store.get(absent)


def test_a_reference_from_somewhere_else_is_refused(store: ArtifactStore) -> None:
    """This store resolves its own references and does not guess at others."""

    with pytest.raises(PersistenceValidationError, match="was not produced by"):
        store.get(ArtifactRef(uri="s3://a-bucket/a-key"))


def test_a_reference_with_a_malformed_digest_is_refused(store: ArtifactStore) -> None:
    with pytest.raises(PersistenceValidationError, match="does not carry a SHA-256"):
        store.get(ArtifactRef(uri=f"{ARTIFACT_URI_SCHEME}not-a-digest"))


def test_verification_stands_alone_for_bytes_fetched_elsewhere() -> None:
    """`verify_artifact` is exported because where the bytes came from is irrelevant."""

    ref = MemoryArtifactStore().put(_PAYLOAD)

    assert verify_artifact(ref, _PAYLOAD) == _PAYLOAD
    with pytest.raises(StorageError, match="does not match the digest"):
        verify_artifact(ref, _OTHER)


def test_a_reference_inconsistent_with_itself_is_refused() -> None:
    """Right bytes, wrong recorded size: the reference disagrees with itself."""

    honest = MemoryArtifactStore().put(_PAYLOAD)
    lying = ArtifactRef(
        uri=honest.uri, media_type=honest.media_type, checksum=honest.checksum, size_bytes=1
    )

    with pytest.raises(StorageError, match="hashes correctly but is"):
        verify_artifact(lying, _PAYLOAD)


def test_the_digest_is_read_from_the_uri_not_the_checksum_field() -> None:
    """The URI is the identity; a disagreeing checksum field cannot redirect a read."""

    ref = MemoryArtifactStore().put(_PAYLOAD)
    tampered = ArtifactRef(uri=ref.uri, checksum="0" * 64, size_bytes=ref.size_bytes)

    assert digest_of(tampered) == digest_of(ref)


# ---------------------------------------------------------------------------
# Validation and lifecycle
# ---------------------------------------------------------------------------


def test_a_non_bytes_payload_is_refused(store: ArtifactStore) -> None:
    with pytest.raises(PersistenceValidationError, match="An artifact is bytes"):
        store.put("a string")  # type: ignore[arg-type]


def test_a_blank_media_type_is_refused(store: ArtifactStore) -> None:
    with pytest.raises(PersistenceValidationError, match="media_type cannot be blank"):
        store.put(_PAYLOAD, "   ")


def test_the_default_media_type_is_unspecific_rather_than_guessed(
    store: ArtifactStore,
) -> None:
    assert store.put(b'{"a": 1}').media_type == DEFAULT_MEDIA_TYPE


def test_containment_is_answerable_without_reading(store: ArtifactStore) -> None:
    ref = store.put(_PAYLOAD)
    absent = ArtifactRef(uri=artifact_uri(compute_digest(b"elsewhere")))

    assert store.contains(ref)
    assert not store.contains(absent)


def test_a_corrupt_artifact_is_present_not_absent(tmp_path: Path) -> None:
    """Reporting it missing would send a caller looking for the wrong problem."""

    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)
    ref = store.put(_PAYLOAD)
    next(root.rglob("*.artifact")).write_bytes(b"corrupt")

    assert store.contains(ref), "it is there; it is damaged"
    with pytest.raises(StorageError):
        store.get(ref)


def test_deleting_removes_the_artifact(store: ArtifactStore) -> None:
    ref = store.put(_PAYLOAD)

    assert store.delete(ref) is True
    assert not store.contains(ref)
    with pytest.raises(StorageError, match="No artifact is stored"):
        store.get(ref)


def test_deleting_something_absent_is_not_an_error(store: ArtifactStore) -> None:
    """The requested end state holds either way."""

    absent = ArtifactRef(uri=artifact_uri(compute_digest(b"never stored")))
    assert store.delete(absent) is False


def test_an_artifact_can_be_stored_again_after_deletion(store: ArtifactStore) -> None:
    ref = store.put(_PAYLOAD)
    store.delete(ref)

    assert store.put(_PAYLOAD) == ref, "same bytes, same identity"
    assert store.get(ref) == _PAYLOAD


# ---------------------------------------------------------------------------
# The file backend's own obligations
# ---------------------------------------------------------------------------


def test_the_file_store_refuses_a_root_that_does_not_exist(tmp_path: Path) -> None:
    """A store that creates its own root turns a typo into silent data loss."""

    with pytest.raises(StorageError, match="does not exist"):
        FileArtifactStore(tmp_path / "no-such-directory")


def test_the_file_store_survives_a_process_boundary(tmp_path: Path) -> None:
    """Durability: a second store over the same root reads what the first wrote."""

    root = tmp_path / "artifacts"
    root.mkdir()
    ref = FileArtifactStore(root).put(_PAYLOAD)

    assert FileArtifactStore(root).get(ref) == _PAYLOAD


def test_the_file_store_leaves_no_partial_files_behind(tmp_path: Path) -> None:
    """Writes are atomic, so a reader never sees a half-written artifact."""

    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)
    for payload in (_PAYLOAD, _OTHER, b"", b"x" * 10_000):
        store.put(payload)

    assert list(root.rglob("*.partial")) == []
    assert len(list(root.rglob("*.artifact"))) == 4


def test_no_filesystem_path_leaks_into_a_reference(tmp_path: Path) -> None:
    """A reference is an identity, not a location. It must travel between machines."""

    root = tmp_path / "artifacts"
    root.mkdir()
    ref = FileArtifactStore(root).put(_PAYLOAD)

    assert str(tmp_path) not in ref.uri
    assert "/" not in ref.uri.removeprefix(ARTIFACT_URI_SCHEME)
    assert ref.uri == artifact_uri(compute_digest(_PAYLOAD))


def test_artifacts_are_sharded_rather_than_piled_into_one_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)
    for index in range(32):
        store.put(f"artifact-{index}".encode())

    shards = [entry for entry in root.iterdir() if entry.is_dir()]
    assert len(shards) > 1, "one directory does not hold every artifact"
    assert all(len(shard.name) == 2 for shard in shards)


# ---------------------------------------------------------------------------
# The identifier-stream property (ADR-0029 decision 7)
# ---------------------------------------------------------------------------


def test_a_put_get_cycle_draws_no_identifier(store: ArtifactStore) -> None:
    """Storing inside a run's scope must not consume the run's own identifiers.

    The defect ADR-0029 decision 7 found in the legacy persistence store, which
    advanced ``draws`` 0 -> 2 per save. An artifact store that did this would
    shift the identity of every order and fill written after it.
    """

    source = DeterministicIdSource(2015)
    with use_id_source(source):
        before = source.draws
        ref = store.put(_PAYLOAD)
        store.get(ref)
        store.contains(ref)
        store.delete(ref)
        after = source.draws

    assert after == before, f"the artifact store drew {after - before} of the run's identifiers"
