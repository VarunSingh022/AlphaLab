"""The canonical dataset: records, what they are, and how they got here.

A :class:`Dataset` is immutable in the ordinary Python sense -- it is a frozen
dataclass -- but the guarantee v3.1 adds is stronger and lives one level up: a
dataset *version* is immutable in the engine's state. Cleaning a dataset does
not edit it; it derives a new version with a new identity, and the registry
refuses to overwrite a name it already holds. The raw version and the cleaned
version both remain, which is what lets a study say which of the two it used.

Provenance may be absent, and says so
-------------------------------------

``provenance`` is ``None`` for a dataset built by :func:`create_dataset` from
rows already in memory. That is deliberate and follows the rule this repository
applies wherever a fact is unavailable: a hand-driven run records
``source_id=None`` rather than ``""``, and evidence refuses to be built from
it. The same holds here -- a dataset assembled from a list of dicts has no
bytes to hash, no retrieval to date and no basis anyone declared, and
manufacturing a provenance record for it would make an unverifiable dataset
look exactly like a verified one.

:func:`~alphalab.api.ingest_csv` always produces provenance, and
:meth:`Dataset.require_provenance` is what a caller uses when lineage is not
optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import CanonicalRecord
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.provenance import DatasetProvenance
from alphalab.data.quality import DataQualityReport, QualityReport
from alphalab.data.schema import DatasetSchema

__all__ = ["Dataset"]


@dataclass(frozen=True, slots=True)
class Dataset:
    """One immutable version of a canonical dataset.

    Attributes:
        metadata: Identity, asset class, frequency, span and zone.
        schema: The shape the records were read under.
        quality: The scored summary, stored in state and shown in a catalogue.
        records: The canonical wire records themselves.
        provenance: How this version came to exist, or ``None`` when it was
            assembled from rows that had no source.
        quality_detail: The full findings the summary was projected from, where
            the ingestion that produced this dataset measured them.
    """

    metadata: DatasetMetadata
    schema: DatasetSchema
    quality: QualityReport
    records: tuple[CanonicalRecord, ...] = field(default_factory=tuple)
    provenance: DatasetProvenance | None = None
    quality_detail: DataQualityReport | None = None

    @property
    def dataset_id(self) -> str:
        """What this version is called. Read through metadata, never stored twice."""

        return self.metadata.dataset_id

    @property
    def dataset_version(self) -> str | None:
        """The derived identity, or ``None`` when no provenance was recorded."""

        return None if self.provenance is None else self.provenance.dataset_version

    @property
    def is_versioned(self) -> bool:
        """Whether this dataset carries a derived, reproducible identity."""

        return self.provenance is not None

    @property
    def timezone(self) -> str:
        """The zone this dataset's timestamps are reported in.

        Read from provenance when there is any, because provenance is what the
        identity was derived from and therefore the authoritative statement.
        Falls back to the metadata field for datasets built without one.
        """

        if self.provenance is not None:
            return self.provenance.timezone_name
        return self.metadata.timezone

    def require_provenance(self) -> DatasetProvenance:
        """Return this dataset's provenance, or refuse.

        What a caller uses when lineage is not optional -- a promotion, an
        evidence record, anything auditable. The refusal names the path that
        produces a dataset with provenance, so it is actionable rather than
        merely correct.

        Raises:
            DataValidationError: If no provenance was recorded.
        """

        if self.provenance is None:
            raise DataValidationError(
                f"Dataset {self.dataset_id!r} carries no provenance, so it cannot name the "
                "source, schema or transformations it came from. It was built from rows "
                "already in memory; use alphalab.api.ingest_csv or ingest_rows with a "
                "RawSource to produce a dataset whose lineage is recorded."
            )
        return self.provenance
