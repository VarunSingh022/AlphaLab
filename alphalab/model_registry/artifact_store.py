"""Where artifact bytes actually live.

:class:`~alphalab.model_registry.registry.ArtifactRef` has recorded a location,
a media type, a checksum and a size since v2.4, and said plainly what it was:

    AlphaLab stores no artifact bytes and implements no object store. A registry
    entry says *where* an artifact is and *what it should hash to*; fetching it
    is the caller's job, and so is putting it there. [...] AlphaLab never
    computes it, because it never reads the bytes.

So a `ModelVersion` could cite an artifact, and nothing in the repository could
produce one, fetch one, or tell you whether the file behind a version had
changed. The digest field was a place for a number nobody computed. This module
is the store that was missing.

The shape is not new
--------------------
This follows :mod:`alphalab.persistence.run_store` deliberately and almost
exactly -- a narrow protocol naming the minimal effectful operation, one real
implementation on the filesystem, one deterministic in-memory double the caller
constructs **by name**, a digest verified before anything is returned, atomic
writes, and refusal rather than repair. That module in turn follows
:mod:`alphalab.marketdata.transport`. Three subsystems, one shape; a fourth
should look the same.

**It is not a second persistence owner.** ``RunStateStore`` owns *run state*,
addressed by ``(run_id, sequence)``, holding a ``str``. This owns *artifact
bytes*, addressed by content, holding ``bytes``. Neither can answer the other's
question and neither is reachable from the other; they share a shape and an
error vocabulary, which is what stops them being two designs.

Why it lives here and not in ``alphalab.persistence``
-----------------------------------------------------
Because :class:`~alphalab.model_registry.registry.ArtifactRef` does, and a store
whose whole job is to produce and resolve that reference belongs beside it.
Putting it under ``alphalab.persistence`` would have made that package -- whose
every other module imports nothing but ``alphalab.common`` and its own siblings
-- depend on a domain package, which is the dependency direction inverted.

The reverse direction is the healthy one and is the only import here:
``PersistenceValidationError`` and ``StorageError`` come from the codec spine.
That reuse is ADR-0029's own reasoning applied again -- "the store needed **no
new exception type**  [...] so inventing a parallel hierarchy would have created
a surface to retire later".

Identity is the content
-----------------------
An artifact's identity is the SHA-256 of its bytes, and **is not chosen by the
caller**. That is the one significant difference from ``RunStateStore``, where
``run_id`` is caller-supplied and opaque (ADR-0029 decision 3), and the reason
for it is that the two references answer different questions. ``RunStateRef``
says *which checkpoint of which run*; an artifact reference says *these exact
bytes*. Content addressing makes four things true at once:

* Storing identical bytes twice is one artifact, not two.
* A reference cannot name bytes that hash to something else -- verification is
  a tautology the store checks rather than a claim it trusts.
* Two environments that never shared a database agree on an artifact's
  identity, exactly as :func:`~alphalab.instrument.identity.derive_asset_id`
  makes them agree on an instrument's.
* An artifact cannot be silently replaced, because replacing the bytes changes
  the name.

No path leaks
-------------
:attr:`~alphalab.model_registry.registry.ArtifactRef.uri` is documented as
"opaque to AlphaLab -- a path, an ``s3://`` URL, a content-addressed id", and
this store produces the third of those: ``alphalab-artifact:sha256:<hex>``.
A caller holding a reference learns the digest and nothing about the filesystem,
so a reference written into a registry snapshot on one machine says nothing
about where that machine keeps its files. :func:`digest_of` parses it back.

Nothing here mints an identifier
--------------------------------
No function in this module calls :func:`~alphalab.common.ids.new_id`, and none
has an event log to stamp. A complete ``put``/``get`` cycle inside a run's
:func:`~alphalab.common.ids.id_scope` advances ``draws`` by exactly zero --
ADR-0029 decision 7's property, which the legacy ``MemoryStorage`` violated, and
which ``tests/regression/test_artifact_store.py`` asserts here too.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from alphalab.model_registry.registry import ArtifactRef
from alphalab.persistence.exceptions import PersistenceValidationError, StorageError

__all__ = [
    "ARTIFACT_URI_SCHEME",
    "ArtifactStore",
    "FileArtifactStore",
    "MemoryArtifactStore",
    "artifact_uri",
    "compute_digest",
    "digest_of",
    "verify_artifact",
]

#: Scheme of the content-addressed URI this store writes onto an ``ArtifactRef``.
#:
#: Deliberately not ``file://``: a reference is an identity, and a ``file://``
#: URI would bind one to the machine that happened to write it. A reference
#: produced here is meaningful to any store holding the same bytes.
ARTIFACT_URI_SCHEME: Final = "alphalab-artifact:sha256:"

#: Media type recorded when the caller names none. Deliberately the generic
#: binary type rather than a guess from the content: AlphaLab does not sniff
#: bytes, and a wrong media type is worse than an unspecific one.
DEFAULT_MEDIA_TYPE: Final = "application/octet-stream"

#: Length of a SHA-256 hex digest.
_DIGEST_LENGTH: Final = 64

#: Suffix of the file holding one artifact's bytes.
_ARTIFACT_SUFFIX: Final = ".artifact"

#: How many leading digest characters name the shard directory. Keeps any one
#: directory from holding every artifact, which is what makes a store with many
#: of them still listable on an ordinary filesystem.
_SHARD_WIDTH: Final = 2


def compute_digest(payload: bytes) -> str:
    """The SHA-256 hex digest of ``payload``. An artifact's identity."""

    return hashlib.sha256(payload).hexdigest()


def artifact_uri(digest: str) -> str:
    """The opaque, content-addressed URI naming the artifact with this digest."""

    return f"{ARTIFACT_URI_SCHEME}{digest}"


def digest_of(ref: ArtifactRef) -> str:
    """The digest ``ref`` names, read from its URI.

    Read from the URI rather than from
    :attr:`~alphalab.model_registry.registry.ArtifactRef.checksum` because the
    URI *is* the identity: a reference whose checksum field disagreed with its
    URI would be a reference to two different artifacts, and this returns the
    one the store would actually look up.

    Raises:
        PersistenceValidationError: If the URI was not produced by this store,
            or does not carry a well-formed digest. A reference to somewhere
            else is not an error to work around -- this store simply cannot
            resolve it, and says which scheme it can.
    """

    if not ref.uri.startswith(ARTIFACT_URI_SCHEME):
        raise PersistenceValidationError(
            f"ArtifactRef uri {ref.uri!r} was not produced by an AlphaLab artifact "
            f"store: it does not begin with {ARTIFACT_URI_SCHEME!r}. This store "
            "resolves only its own content-addressed references; a reference to a "
            "location somewhere else is fetched by whoever owns that location."
        )

    digest = ref.uri[len(ARTIFACT_URI_SCHEME) :]
    if len(digest) != _DIGEST_LENGTH or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise PersistenceValidationError(
            f"ArtifactRef uri {ref.uri!r} does not carry a SHA-256 hex digest: "
            f"expected {_DIGEST_LENGTH} lowercase hex characters, got {digest!r}."
        )
    return digest


def verify_artifact(ref: ArtifactRef, payload: bytes) -> bytes:
    """Return ``payload`` if it is what ``ref`` names, and raise if it is not.

    The check every read goes through, exported because it is also what a caller
    fetching an artifact from somewhere *else* -- an object store, a release
    bundle -- should run before trusting the bytes. Verification does not depend
    on where they came from.

    Size is checked as well as digest. A digest match already implies it, but a
    mismatched ``size_bytes`` means the reference itself is internally
    inconsistent, and reporting that is more useful than ignoring it.

    Raises:
        StorageError: If the bytes do not hash to the digest the reference
            names, or if the reference's recorded size disagrees with them.
            Nothing partial is returned by either.
    """

    expected = digest_of(ref)
    actual = compute_digest(payload)
    if actual != expected:
        raise StorageError(
            f"The artifact at {ref.uri} does not match the digest it is named by: "
            f"expected {expected}, the bytes hash to {actual}. They are truncated "
            "or altered, and nothing is returned from them."
        )
    if ref.size_bytes is not None and ref.size_bytes != len(payload):
        raise StorageError(
            f"The artifact at {ref.uri} hashes correctly but is {len(payload)} bytes "
            f"where its reference records {ref.size_bytes}. The reference is "
            "inconsistent with itself and is refused."
        )
    return payload


class ArtifactStore(Protocol):
    """Content-addressed storage for artifact bytes.

    The complete interface, and deliberately no more: four methods over
    ``bytes -> ArtifactRef -> bytes``. There is no ``update``, because an
    artifact's name is its content and changing the content produces a different
    artifact. There is no query language, no transaction and no listing by
    metadata, because no caller in this repository needs one -- the same
    restraint :class:`~alphalab.persistence.run_store.RunStateStore` exercises.
    """

    def put(self, payload: bytes, media_type: str = DEFAULT_MEDIA_TYPE) -> ArtifactRef:
        """Store ``payload`` and return the reference naming it.

        Storing identical bytes again returns an equal reference and is not an
        error: the same content is the same artifact.
        """
        ...

    def get(self, ref: ArtifactRef) -> bytes:
        """Return the bytes ``ref`` names, verified against its digest."""
        ...

    def contains(self, ref: ArtifactRef) -> bool:
        """Whether this store holds the artifact ``ref`` names."""
        ...

    def delete(self, ref: ArtifactRef) -> bool:
        """Remove the artifact ``ref`` names. ``True`` if it was there."""
        ...


def _validate(payload: bytes, media_type: str) -> None:
    """The rules both backends apply before anything is written."""

    if not isinstance(payload, bytes | bytearray):
        raise PersistenceValidationError(
            f"An artifact is bytes, not {type(payload).__name__}. Encode the value "
            "first -- the store deliberately knows nothing about what it holds, so "
            "it cannot choose an encoding on the caller's behalf."
        )
    if not media_type.strip():
        raise PersistenceValidationError(
            "ArtifactRef.media_type cannot be blank. Pass the generic "
            f"{DEFAULT_MEDIA_TYPE!r} rather than an empty string: an unspecific type "
            "is a fact, an absent one is a question nobody can answer later."
        )


def _reference(payload: bytes, media_type: str) -> ArtifactRef:
    """The reference naming ``payload``. Every field is derived, none is claimed."""

    digest = compute_digest(payload)
    return ArtifactRef(
        uri=artifact_uri(digest),
        media_type=media_type,
        checksum=digest,
        size_bytes=len(payload),
    )


@dataclass(frozen=True, slots=True)
class FileArtifactStore:
    """An artifact store on the local filesystem. Standard library only.

    The real implementation, as
    :class:`~alphalab.persistence.run_store.FileRunStateStore` is for run state
    and :class:`~alphalab.marketdata.transport.HttpTransport` is for market
    data.

    The root must **already exist**. A store that silently creates directories
    is a smaller version of the silent fallback this layer refuses -- a typo in
    a path would produce an empty store rather than an error, and the artifacts
    written to it would be somewhere nobody looks.

    Attributes:
        root: Directory the artifacts live under.
    """

    root: Path

    def __init__(self, root: Path | str) -> None:
        resolved = Path(root)
        if not resolved.is_dir():
            raise StorageError(
                f"Artifact store root {resolved} does not exist. It is not created "
                "here: a store that materializes its own root turns a mistyped path "
                "into an empty store that silently loses everything written to it."
            )
        object.__setattr__(self, "root", resolved)

    def _path(self, digest: str) -> Path:
        """Where the artifact with this digest lives.

        Sharded on the first two hex characters, so no single directory holds
        every artifact in the store.
        """

        return self.root / digest[:_SHARD_WIDTH] / f"{digest}{_ARTIFACT_SUFFIX}"

    @staticmethod
    def _write_atomically(destination: Path, payload: bytes) -> None:
        """Write to a temporary file in the same directory, then rename over.

        The rename is atomic within a filesystem, so a reader never sees a
        half-written artifact -- and a crash mid-write leaves the temporary file
        rather than a corrupt artifact under a name that claims to hash
        correctly.
        """

        handle, temporary = tempfile.mkstemp(dir=str(destination.parent), suffix=".partial")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def put(self, payload: bytes, media_type: str = DEFAULT_MEDIA_TYPE) -> ArtifactRef:
        """Store ``payload`` and return the reference naming it.

        Storing bytes already held is a **no-op** returning an equal reference.
        This is the opposite of
        :meth:`~alphalab.persistence.run_store.FileRunStateStore.put`, which
        refuses a duplicate -- and the difference follows from the addressing.
        There, two payloads can contend for one ``(run_id, sequence)`` and
        overwriting would lose a checkpoint. Here, the name *is* the content, so
        a second write is the same artifact and there is nothing to lose.

        Raises:
            PersistenceValidationError: If ``payload`` is not bytes, or
                ``media_type`` is blank.
            StorageError: If the write fails.
        """

        _validate(payload, media_type)
        ref = _reference(bytes(payload), media_type)
        destination = self._path(digest_of(ref))

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Cannot create storage for artifact {ref.uri}: {exc}") from exc

        if destination.is_file():
            return ref

        try:
            self._write_atomically(destination, bytes(payload))
        except OSError as exc:
            raise StorageError(f"Cannot write artifact {ref.uri}: {exc}") from exc
        return ref

    def get(self, ref: ArtifactRef) -> bytes:
        """Return the bytes ``ref`` names, verified against its digest.

        Raises:
            PersistenceValidationError: If ``ref`` is not one of this store's.
            StorageError: If the artifact is absent, cannot be read, or does not
                hash to the digest it is named by. Nothing partial is returned
                by any of them -- a corrupt artifact is refused, not repaired.
        """

        digest = digest_of(ref)
        destination = self._path(digest)
        if not destination.is_file():
            raise StorageError(
                f"No artifact is stored under {ref.uri}. Nothing is returned for an "
                "artifact this store has never held."
            )

        try:
            payload = destination.read_bytes()
        except OSError as exc:
            raise StorageError(f"Cannot read artifact {ref.uri}: {exc}") from exc

        return verify_artifact(ref, payload)

    def contains(self, ref: ArtifactRef) -> bool:
        """Whether this store holds the artifact ``ref`` names.

        Checks for the bytes only, and does **not** verify them: a corrupt
        artifact is present, and reporting it absent would send a caller looking
        for a missing file instead of a damaged one. :meth:`get` is what refuses
        it.
        """

        return self._path(digest_of(ref)).is_file()

    def delete(self, ref: ArtifactRef) -> bool:
        """Remove the artifact ``ref`` names.

        Returns ``True`` if it was there and ``False`` if it was not. Deleting
        something absent is not an error: the requested end state -- this store
        does not hold that artifact -- is the state afterwards either way.

        Raises:
            StorageError: If the artifact exists and cannot be removed.
        """

        destination = self._path(digest_of(ref))
        if not destination.is_file():
            return False
        try:
            destination.unlink()
        except OSError as exc:
            raise StorageError(f"Cannot delete artifact {ref.uri}: {exc}") from exc
        return True


class MemoryArtifactStore:
    """A deterministic in-memory artifact store, constructed by name.

    The counterpart of
    :class:`~alphalab.persistence.run_store.MemoryRunStateStore` and of
    :class:`~alphalab.marketdata.transport.StaticTransport`, and named the same
    way for the same reason: a caller that wants storage which does not survive
    the process must **say so**. There is no default backend and no fallback to
    this one, because a store silently chosen for a caller who wanted a durable
    one is how a production run discovers at the end that it saved nothing.

    Verifies exactly as the file store does, so a test against this backend
    exercises the same contract.
    """

    __slots__ = ("_artifacts",)

    def __init__(self) -> None:
        self._artifacts: dict[str, bytes] = {}

    def put(self, payload: bytes, media_type: str = DEFAULT_MEDIA_TYPE) -> ArtifactRef:
        """Store ``payload`` in memory and return the reference naming it."""

        _validate(payload, media_type)
        frozen = bytes(payload)
        ref = _reference(frozen, media_type)
        self._artifacts.setdefault(digest_of(ref), frozen)
        return ref

    def get(self, ref: ArtifactRef) -> bytes:
        """Return the bytes ``ref`` names, verified against its digest.

        Raises:
            PersistenceValidationError: If ``ref`` is not one of this store's.
            StorageError: If the artifact is absent or does not verify.
        """

        digest = digest_of(ref)
        payload = self._artifacts.get(digest)
        if payload is None:
            raise StorageError(
                f"No artifact is stored under {ref.uri}. Nothing is returned for an "
                "artifact this store has never held."
            )
        return verify_artifact(ref, payload)

    def contains(self, ref: ArtifactRef) -> bool:
        """Whether this store holds the artifact ``ref`` names."""

        return digest_of(ref) in self._artifacts

    def delete(self, ref: ArtifactRef) -> bool:
        """Remove the artifact ``ref`` names. ``True`` if it was there."""

        return self._artifacts.pop(digest_of(ref), None) is not None
