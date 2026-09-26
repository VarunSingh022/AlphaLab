"""Which source a set of observations came from, and which version of it.

An alternative-data record is only as good as the answer to "whose number is
this, produced how, read from which bytes?". v1's
:class:`~alphalab.alt_data.provenance.DataProvenance` answers the first part as
quality metadata -- a vendor, a coverage note, an analyst's confidence -- and
says nothing about identity: two deliveries of different data from one vendor
carry the same record.

An :class:`ObservationSource` is the identity half. It names the source by a
stable identifier, records the source's own version (a methodology revision, a
delivery version, a schema version), and where the observations were read from
bytes, the digest of those bytes -- the same
:func:`~alphalab.data.source.content_digest` a
:class:`~alphalab.data.source.RawSource` records, lifted across the package
boundary by :func:`alphalab.api.observation_source`. ``DataProvenance`` rides
along as :attr:`ObservationSource.quality`, recorded and never hashed.

Why this is not ``DatasetProvenance``
-------------------------------------

:class:`~alphalab.data.provenance.DatasetProvenance` is the record of how a
*market series* came to exist: its schema roles, frequency, price basis and
cleaning policy are required, and none of them means anything for a sentiment
score or a statement line item. It also lives in :mod:`alphalab.data`, whose
outward edges are pinned to ``common`` and ``options`` and which the research
layer may not import. This package reaches the same facts -- a content digest,
a retrieval instant -- as plain values, so it stays a leaf over
:mod:`alphalab.common`. ``tests/regression/test_shared_names_stay_distinct.py``
records the three provenance records and why none replaces another.

What is recorded and what is identity
-------------------------------------

``source_id`` and ``version`` enter every *record's* identity: the same figure
from another source, or from another version of one, is a different record.
``content_hash`` enters the identity of a *set* read from those bytes and not of
the records in it -- the same record read from two files is one record, and the
two files are two sets. ``retrieved_at`` and ``quality`` are recorded facts and
enter no identity, following ``DatasetProvenance.retrieved_at``: fetching the
same bytes tomorrow, or revising an analyst's confidence, does not change what
the data is.
"""

from __future__ import annotations

from dataclasses import dataclass

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.provenance import DataProvenance
from alphalab.alt_data.validation import (
    require_finite_instant,
    require_identifier,
    require_label,
)

__all__ = ["ObservationSource"]


@dataclass(frozen=True, slots=True)
class ObservationSource:
    """The identity of a source of external observations.

    Attributes:
        source_id: What the source is, as a dotted lowercase identifier --
            ``"filings.quarterly"``, ``"panel.card_spend"``. Never a vendor's
            product name hard-coded into AlphaLab: the identifier is the
            caller's vocabulary.
        version: The source's own version -- a delivery, a methodology
            revision -- or ``None`` when the source declares none. ``None`` is
            recorded as the absence it is, never replaced by a guess.
        content_hash: The digest of the bytes the observations were read from,
            or ``None`` for observations constructed in memory. Without it, a
            set of observations names its source but cannot name the exact
            content, and :attr:`byte_provenance` says so.
        retrieved_at: Unix seconds at which those bytes were retrieved, or
            ``None``. Recorded; enters no identity.
        quality: The v1 vendor-quality record, where one exists. Recorded;
            enters no identity.

    Raises:
        AltDataInputError: If ``source_id`` is not an identifier, ``version``
            or ``content_hash`` is blank or spans a line, or ``retrieved_at`` is
            not a finite instant.
    """

    source_id: str
    version: str | None
    content_hash: str | None
    retrieved_at: float | None
    quality: DataProvenance | None = None

    def __post_init__(self) -> None:
        require_identifier(self.source_id, "ObservationSource.source_id")
        if self.version is not None:
            require_label(self.version, "ObservationSource.version")
        if self.content_hash is not None:
            require_label(self.content_hash, "ObservationSource.content_hash")
            if any(character.isspace() for character in self.content_hash):
                raise AltDataInputError(
                    f"ObservationSource.content_hash {self.content_hash!r} contains whitespace; "
                    "a digest has none."
                )
        if self.retrieved_at is not None:
            require_finite_instant(self.retrieved_at, "ObservationSource.retrieved_at")

    @property
    def byte_provenance(self) -> bool:
        """Whether the source names the exact bytes its observations were read from."""

        return self.content_hash is not None

    @property
    def record_lines(self) -> tuple[str, ...]:
        """The lines this source contributes to each record's identity.

        Rendered with ``repr``, the v3.6 rule, so ``version=None`` and
        ``version='None'`` can never collide.
        """

        return (f"source={self.source_id!r}", f"source_version={self.version!r}")

    @property
    def set_lines(self) -> tuple[str, ...]:
        """The lines this source contributes to a set's identity: the record
        lines and the digest of the bytes the set was read from."""

        return (*self.record_lines, f"content={self.content_hash!r}")
