"""A durable home for evidence: values kept by what they are (ledger OFE-016, BDY-008).

AlphaLab produces values whose whole purpose is to be kept -- a
:class:`~alphalab.lifecycle.reproducibility.ReproducibilityManifest` saying how a
result can be reproduced, a :class:`~alphalab.lifecycle.fingerprint.StrategyFingerprint`
saying what a strategy is, a :class:`~alphalab.lifecycle.progression.StrategyProgression`
saying where it stands, a :class:`~alphalab.analytics.report.PerformanceReport` --
and until v3.12 nowhere to keep them: each application was left to invent a
format, which is the boundary ``ROADMAP.md`` recorded as "no durable state for
the v3.5/v3.6 values" (BDY-008).

:class:`EvidenceStore` is that home, and it is two things already in the
repository put together rather than a third:

* the bytes go into an :class:`~alphalab.model_registry.artifact_store.ArtifactStore`,
  which names them by their SHA-256 and verifies them on every read; and
* each is filed under the **value's own identity** -- a manifest's
  ``manifest_id``, a fingerprint's ``fingerprint`` -- in an
  :class:`EvidenceIndex`, so it is found by what it is, not by a path somebody
  chose. A value with no identity of its own is filed under its content digest.

An identity names one value: filing a *different* value under an identity
already filed is refused, and filing the same one again changes nothing. A
value is written as deterministic JSON in an envelope that repeats its kind and
identity, so the bytes say what they are wherever they are copied.

The store returns what it stored as plain JSON values; decoding them into a
typed value is the owning subsystem's, through its own ``from_primitives`` where
it has one. Nothing here changes :class:`~alphalab.lifecycle.state.LifecycleState`,
reads a clock or mints an identifier.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from alphalab.model_registry.artifact_store import (
    ArtifactStore,
    FileArtifactStore,
    MemoryArtifactStore,
    compute_digest,
    digest_of,
)
from alphalab.model_registry.registry import ArtifactRef
from alphalab.persistence.durable import ensure_directory, fsync_directory
from alphalab.persistence.exceptions import PersistenceValidationError, StorageError
from alphalab.persistence.serializer import deserialize, serialize

__all__ = [
    "EVIDENCE_SCHEMA",
    "EvidenceIndex",
    "EvidenceItem",
    "EvidenceRef",
    "EvidenceStore",
    "FileEvidenceIndex",
    "MemoryEvidenceIndex",
    "SelfIdentified",
    "file_evidence_store",
    "memory_evidence_store",
]

#: Version of the envelope every stored value is written in.
EVIDENCE_SCHEMA: Final = 1

#: The media type a stored value's bytes are recorded under.
_MEDIA_TYPE: Final = "application/json"

#: A kind is an identifier both a directory and a reader can quote.
_KIND: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


@runtime_checkable
class SelfIdentified(Protocol):
    """A value that says what kind of evidence it is and what identifies it."""

    @property
    def evidence_kind(self) -> str: ...

    @property
    def evidence_identity(self) -> str: ...


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Where one piece of evidence is: its kind, its identity, and its bytes' digest.

    Attributes:
        kind: What it is -- ``"reproducibility_manifest"``.
        identity: Its own identity within the kind.
        digest: The SHA-256 of its stored bytes.
    """

    kind: str
    identity: str
    digest: str


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One piece of evidence read back: where it was, and the value as JSON."""

    ref: EvidenceRef
    value: Any


def _require_kind(kind: str) -> str:
    if not isinstance(kind, str) or not _KIND.fullmatch(kind):
        raise PersistenceValidationError(
            f"An evidence kind is one to sixty-four lowercase letters, digits, '_', '-' or "
            f"'.', starting with a letter or digit; got {kind!r}."
        )
    return kind


def _require_identity(identity: str) -> str:
    if not isinstance(identity, str) or not identity.strip() or not identity.isprintable():
        raise PersistenceValidationError(
            f"An evidence identity is a non-blank printable string, got {identity!r}."
        )
    return identity


class EvidenceIndex(Protocol):
    """Which digest each ``(kind, identity)`` is filed under."""

    def get(self, kind: str, identity: str) -> str | None:
        """The digest filed under ``(kind, identity)``, or ``None``."""
        ...

    def set(self, kind: str, identity: str, digest: str) -> None:
        """File ``digest`` under ``(kind, identity)``; refuse a different one already there."""
        ...

    def identities(self, kind: str) -> tuple[str, ...]:
        """Every identity filed under ``kind``, sorted."""
        ...


def _conflict(kind: str, identity: str, held: str, digest: str) -> PersistenceValidationError:
    return PersistenceValidationError(
        f"{kind} {identity!r} is already filed as {held[:12]}; a different value "
        f"({digest[:12]}) under the same identity is refused. An identity names one value."
    )


@dataclass(slots=True)
class MemoryEvidenceIndex:
    """An in-memory index, for tests and short-lived processes. Named, never defaulted to."""

    entries: dict[tuple[str, str], str] = field(default_factory=dict)

    def get(self, kind: str, identity: str) -> str | None:
        return self.entries.get((kind, identity))

    def set(self, kind: str, identity: str, digest: str) -> None:
        held = self.entries.get((kind, identity))
        if held is not None and held != digest:
            raise _conflict(kind, identity, held, digest)
        self.entries[(kind, identity)] = digest

    def identities(self, kind: str) -> tuple[str, ...]:
        return tuple(sorted(identity for held, identity in self.entries if held == kind))


class FileEvidenceIndex:
    """The index on the filesystem: one small file per filed identity.

    ``<root>/<kind>/<sha256 of the identity>.json`` holds the kind, the
    identity and the digest, written atomically and flushed into its directory,
    as :class:`~alphalab.model_registry.artifact_store.FileArtifactStore` writes.
    The file name hashes the identity, so any identity is a safe path and none
    leaks into the tree's structure.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def _path(self, kind: str, identity: str) -> Path:
        name = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self._root / kind / f"{name}.json"

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StorageError(
                f"The evidence index entry {path.name} cannot be read: {exc}"
            ) from exc
        if not isinstance(decoded, dict) or not {"kind", "identity", "digest"} <= set(decoded):
            raise StorageError(f"The evidence index entry {path.name} is not an index entry.")
        return decoded

    def get(self, kind: str, identity: str) -> str | None:
        path = self._path(kind, identity)
        if not path.exists():
            return None
        entry = self._read(path)
        if (entry["kind"], entry["identity"]) != (kind, identity):
            raise StorageError(
                f"The evidence index entry {path.name} files {entry['kind']} "
                f"{entry['identity']!r}, not {kind} {identity!r}."
            )
        return str(entry["digest"])

    def set(self, kind: str, identity: str, digest: str) -> None:
        held = self.get(kind, identity)
        if held is not None:
            if held != digest:
                raise _conflict(kind, identity, held, digest)
            return
        path = self._path(kind, identity)
        ensure_directory(path.parent)
        payload = json.dumps(
            {"kind": kind, "identity": identity, "digest": digest}, sort_keys=True
        ).encode("utf-8")
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        fsync_directory(path.parent)

    def identities(self, kind: str) -> tuple[str, ...]:
        directory = self._root / kind
        if not directory.is_dir():
            return ()
        return tuple(sorted(str(self._read(path)["identity"]) for path in directory.glob("*.json")))


class EvidenceStore:
    """Values kept as evidence: bytes in an artifact store, filed by their own identity."""

    def __init__(self, artifacts: ArtifactStore, index: EvidenceIndex) -> None:
        self._artifacts = artifacts
        self._index = index

    def put(
        self, value: object, *, kind: str | None = None, identity: str | None = None
    ) -> EvidenceRef:
        """Keep ``value``, filed under its kind and identity.

        A :class:`SelfIdentified` value states both; any other states its kind
        here, and its identity too or else is filed under its content digest.
        Keeping the same value twice returns the same reference.

        Raises:
            PersistenceValidationError: If the kind or identity is missing or
                malformed, or a different value is already filed under the
                identity.
            SerializationError: If the value has no deterministic JSON form.
        """

        if isinstance(value, SelfIdentified):
            kind = value.evidence_kind if kind is None else kind
            identity = value.evidence_identity if identity is None else identity
        if kind is None:
            raise PersistenceValidationError(
                f"A {type(value).__name__} does not say what kind of evidence it is; name the kind."
            )
        _require_kind(kind)
        value_json = serialize(value)
        if identity is None:
            identity = compute_digest(value_json.encode("utf-8"))
        _require_identity(identity)
        envelope = serialize(
            {
                "evidence_schema": EVIDENCE_SCHEMA,
                "identity": identity,
                "kind": kind,
                "value": deserialize(value_json),
            }
        ).encode("utf-8")
        digest = compute_digest(envelope)
        held = self._index.get(kind, identity)
        if held is not None and held != digest:
            raise _conflict(kind, identity, held, digest)
        ref = self._artifacts.put(envelope, _MEDIA_TYPE)
        self._index.set(kind, identity, digest_of(ref))
        return EvidenceRef(kind, identity, digest_of(ref))

    def find(self, kind: str, identity: str) -> EvidenceRef | None:
        """Where ``(kind, identity)`` is filed, or ``None`` when it is not."""

        digest = self._index.get(_require_kind(kind), _require_identity(identity))
        return None if digest is None else EvidenceRef(kind, identity, digest)

    def get(self, ref: EvidenceRef) -> EvidenceItem:
        """Read evidence back, verified: its bytes hash to the digest, and say what it is.

        Raises:
            StorageError: If the bytes are missing or do not match the digest,
                or the envelope names another kind, identity or version.
        """

        payload = self._artifacts.get(
            ArtifactRef(f"alphalab-artifact:sha256:{ref.digest}", _MEDIA_TYPE, ref.digest)
        )
        envelope = deserialize(payload.decode("utf-8"))
        if not isinstance(envelope, dict) or envelope.get("evidence_schema") != EVIDENCE_SCHEMA:
            raise StorageError(f"Evidence {ref.digest[:12]} is not a version-1 evidence envelope.")
        if (envelope.get("kind"), envelope.get("identity")) != (ref.kind, ref.identity):
            raise StorageError(
                f"Evidence {ref.digest[:12]} says it is {envelope.get('kind')} "
                f"{envelope.get('identity')!r}, not {ref.kind} {ref.identity!r}."
            )
        return EvidenceItem(ref, envelope["value"])

    def identities(self, kind: str) -> tuple[str, ...]:
        """Every identity filed under ``kind``, sorted."""

        return self._index.identities(_require_kind(kind))


def file_evidence_store(root: Path | str) -> EvidenceStore:
    """An evidence store under ``root``: bytes in ``artifacts/``, the index in ``index/``.

    ``root`` must exist, as an artifact store's must: a store that created its
    own root would turn a mistyped path into an empty store. The two
    directories inside it are the store's own, and are created when missing.

    Raises:
        StorageError: If ``root`` is not an existing directory.
    """

    base = Path(root)
    if not base.is_dir():
        raise StorageError(
            f"Evidence store root {base} does not exist. It is not created here: a store that "
            "materializes its own root turns a mistyped path into an empty store."
        )
    ensure_directory(base / "artifacts")
    ensure_directory(base / "index")
    return EvidenceStore(FileArtifactStore(base / "artifacts"), FileEvidenceIndex(base / "index"))


def memory_evidence_store() -> EvidenceStore:
    """An in-memory evidence store, for tests. Named, never defaulted to."""

    return EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex())
