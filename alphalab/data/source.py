"""Where bytes came from, and what was received.

This module answers Phase 2's four questions about any input AlphaLab is asked
to ingest: *what* is the source, *where* did it come from, *when* was it
retrieved, and *what content* was actually received. A :class:`RawSource` is the
answer, recorded once at the moment of retrieval and carried unchanged into
every dataset derived from it.

Not a second ``MarketDataSource``
---------------------------------

:class:`~alphalab.market.source.MarketDataSource` is a *protocol*: something the
execution path pulls canonical records from, one at a time, for as long as it
runs. It describes a live relationship. A :class:`RawSource` is a *record*: an
immutable statement about bytes that were already read, made after the fact.
One is an interface a socket can satisfy; the other is evidence about a file.
``tests/regression/test_shared_names_stay_distinct.py`` holds the reason they
must not be merged.

What is deliberately not modelled
---------------------------------

No fetching. :func:`raw_source_from_path` reads a local file because that is a
stdlib call with no vendor semantics; everything else -- HTTP, object storage, a
broker's export endpoint -- is handed to :func:`raw_source_from_bytes` by the
caller that performed the retrieval. AlphaLab has no network egress, no
credential store and no vendor request shapes, so a transport here would be
invented rather than tested. The *kind* is recorded so provenance can say an
HTTP body was ingested; the retrieval itself belongs to the application.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from alphalab.common.types import MetadataMapping
from alphalab.data.exceptions import DataValidationError

__all__ = [
    "CONTENT_DIGEST_SCHEME",
    "RawSource",
    "SourceKind",
    "content_digest",
    "raw_source_from_bytes",
    "raw_source_from_path",
]

#: Scheme tag prefixed to every content digest, following the precedent set by
#: :data:`~alphalab.instrument.identity.INSTRUMENT_KEY_SCHEME`: a future change
#: to how content is hashed bumps this and leaves every digest recorded under
#: ``v1`` reproducible and recognisable.
CONTENT_DIGEST_SCHEME = "alphalab.content.v1"


class SourceKind(Enum):
    """What sort of thing the bytes were read from.

    This records the *retrieval channel*, not the file format -- a CSV read from
    disk and the same CSV downloaded over HTTPS are the same format and
    genuinely different provenance, and a reviewer asking "where did this come
    from?" is asking about the channel.
    """

    #: A file on the machine running AlphaLab, named by path.
    LOCAL_FILE = auto()

    #: A file supplied by a user through an application, whose original name is
    #: a claim by that user rather than a path AlphaLab resolved.
    UPLOADED_FILE = auto()

    #: A response body retrieved over HTTP or HTTPS by the caller.
    HTTP_RESPONSE = auto()

    #: An object read from object storage (S3, GCS, Azure Blob) by the caller.
    OBJECT_STORAGE = auto()

    #: A file exported by a broker or venue -- a statement, a fills export, a
    #: contract master. Distinct from ``VENDOR_FILE`` because its contents
    #: describe the *account*, not the market.
    BROKER_EXPORT = auto()

    #: A file supplied by a data vendor.
    VENDOR_FILE = auto()

    #: Rows constructed in the calling process rather than read from anywhere:
    #: a fixture, a generated series, a test. Named so that a dataset built this
    #: way cannot be mistaken for one with an external origin.
    IN_MEMORY = auto()


def content_digest(payload: bytes) -> str:
    """The scheme-tagged SHA-256 digest identifying exactly these bytes.

    The tag is part of the hashed input, not a prefix on the output, so a digest
    computed under a future scheme cannot collide with one computed under this
    one even for identical content.
    """

    tagged = CONTENT_DIGEST_SCHEME.encode("utf-8") + b"\n" + payload
    return hashlib.sha256(tagged).hexdigest()


@dataclass(frozen=True, slots=True)
class RawSource:
    """An immutable statement about content that was retrieved.

    Attributes:
        kind: The retrieval channel.
        location: Where the content came from -- a path, a URL, an object key,
            or the name a user gave an upload. Never empty: a source that cannot
            say where it came from is not a provenance record.
        retrieved_at: Unix seconds at which the content was read. Supplied by
            the caller that performed the retrieval, because only it knows.
        content_hash: :func:`content_digest` of the exact bytes received.
        byte_count: Length of those bytes.
        media_type: The format as the caller understands it, e.g. ``"text/csv"``.
        encoding: Character encoding the bytes were decoded with, where they
            were text. ``None`` for binary content.
        attributes: Retrieval facts that do not fit above -- an ETag, a
            ``Last-Modified`` header, an object version id. Free-form because
            what a channel reports is the channel's business.
    """

    kind: SourceKind
    location: str
    retrieved_at: float
    content_hash: str
    byte_count: int
    media_type: str
    encoding: str | None = None
    attributes: MetadataMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise DataValidationError(
                "A RawSource must name where its content came from; location is empty."
            )
        if not self.content_hash.strip():
            raise DataValidationError(f"RawSource {self.location} carries no content hash.")
        if self.byte_count < 0:
            raise DataValidationError(
                f"RawSource {self.location} reports a negative byte_count: {self.byte_count}."
            )


def raw_source_from_bytes(
    kind: SourceKind,
    location: str,
    payload: bytes,
    retrieved_at: float,
    media_type: str,
    encoding: str | None = None,
    attributes: MetadataMapping | None = None,
) -> RawSource:
    """Record a source for content the caller has already retrieved.

    This is the entry point for every channel AlphaLab does not read itself.
    The caller performed the HTTP request, the object-storage read or the
    upload handling; this turns what it received into provenance.

    Args:
        kind: The retrieval channel.
        location: Where the content came from.
        payload: The exact bytes received.
        retrieved_at: Unix seconds at which they were received.
        media_type: The format, as the caller understands it.
        encoding: Character encoding, for text content.
        attributes: Any further retrieval facts worth keeping.
    """

    return RawSource(
        kind=kind,
        location=location,
        retrieved_at=retrieved_at,
        content_hash=content_digest(payload),
        byte_count=len(payload),
        media_type=media_type,
        encoding=encoding,
        attributes=dict(attributes or {}),
    )


def raw_source_from_path(
    path: str | Path,
    retrieved_at: float,
    media_type: str,
    encoding: str = "utf-8",
) -> tuple[RawSource, bytes]:
    """Read a local file and record what was read.

    Returns both the provenance record and the bytes, so that the caller
    ingests exactly the content that was hashed. Returning only the source
    would invite a second read, and a file can change between two reads.

    Raises:
        DataValidationError: If the path does not name a readable file.
    """

    resolved = Path(path)
    if not resolved.is_file():
        raise DataValidationError(f"Not a readable file: {resolved}")

    payload = resolved.read_bytes()
    source = raw_source_from_bytes(
        kind=SourceKind.LOCAL_FILE,
        location=str(resolved),
        payload=payload,
        retrieved_at=retrieved_at,
        media_type=media_type,
        encoding=encoding,
    )
    return source, payload
