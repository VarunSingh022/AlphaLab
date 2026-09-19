"""Where a dataset came from, and the identity derived from that.

A :class:`DatasetProvenance` is the complete answer to "how did these numbers
get here?" -- the bytes that were read, the schema they were read under, the
zone and calendar they were interpreted in, every transformation applied, and
the policy that permitted each one. A dataset that cannot answer that is not
research data; it is a spreadsheet with a good reputation.

Identity is derived, never minted
---------------------------------

:func:`derive_dataset_version` hashes a canonical rendering of everything that
determines the *content* of a dataset. Two ingestions of the same bytes under
the same configuration produce the same version, in any process, on any
machine, with no shared state -- the same property
:func:`~alphalab.instrument.identity.derive_asset_id` gives instruments, and
for the same reason: a ``uuid4`` would make a dataset built in staging
incomparable with the identical dataset built in production, for a reason that
has nothing to do with the data.

The rendering follows the three precedents this repository already had for
content digests -- ``evidence_id_for``, ``compute_checksum`` and
``canonical_instrument_key`` -- all of which join ``label=value`` lines with
newlines, fix the field order rather than sorting a closed field set, and hash
the result.

What is deliberately *not* in the identity
------------------------------------------

**``retrieved_at``.** Re-downloading the same file tomorrow must not produce a
different dataset; the retrieval time is a fact worth recording and not a fact
about the content.

**The AlphaLab version.** :data:`DATASET_KEY_SCHEME` is in the digest and
``alphalab.__version__`` is not, which is the distinction
``INSTRUMENT_KEY_SCHEME`` draws. A release that does not change how datasets
are derived leaves every existing identity reproducible; one that does changes
the scheme tag deliberately, and the old identities stay recognisable under
the old tag. Hashing the package version instead would re-identify every
dataset in existence on every release, including patch releases that touched
nothing.

Reaching a run
--------------

``dataset_version`` is a string, and it is the string a backtest is given as
its ``MarketDataset.dataset_id``. It therefore flows unchanged into
``RunState.source_id``, ``BacktestResult.dataset_id`` and
``ValidationEvidence.dataset_id``, where it is hashed into the frozen evidence
digest. That is what makes a promotion's evidence name the exact bytes it was
measured on, and it needs no change to the evidence digest whatsoever -- see
:mod:`alphalab.api` for the join.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from alphalab.data.assets import InstrumentSpec
from alphalab.data.cleaning import CleaningPolicy, TransformationRecord
from alphalab.data.corporate_actions import AdjustmentRecord, PriceBasis
from alphalab.data.exceptions import DataValidationError
from alphalab.data.schema import DatasetSchema
from alphalab.data.source import RawSource
from alphalab.data.time import TimeFrequency

__all__ = [
    "DATASET_KEY_SCHEME",
    "DatasetProvenance",
    "canonical_dataset_key",
    "derive_dataset_version",
    "derive_transformed_version",
]

#: Scheme tag, and the first line of every canonical dataset key.
#:
#: Frozen for the life of the scheme. Changing it changes every dataset version
#: in existence and requires an ADR, exactly as ``INSTRUMENT_KEY_SCHEME`` does.
DATASET_KEY_SCHEME: Final = "alphalab.dataset.v1"


@dataclass(frozen=True, slots=True)
class DatasetProvenance:
    """The complete record of how a dataset came to exist.

    Attributes:
        source: The bytes that were read, and where from.
        schema: The shape they were read under.
        timezone_name: The zone naive timestamps were interpreted in, or the
            zone the series is reported in for offset-bearing sources.
        calendar_id: The market calendar the series belongs to, if one was
            declared. ``None`` is honest for a series whose venue is unstated.
        frequency: The series' frequency.
        price_basis: Whether the prices are raw or adjusted.
        cleaning_policy: What cleaning was permitted to do.
        transformations: What it actually did, in the order it did it.
        adjustments: Corporate actions applied, with their factors.
        instrument: What the series describes, where a single instrument
            spec applies to the whole dataset.
        engine_version: The AlphaLab version that performed the ingestion.
            Recorded as a fact; deliberately not part of the identity.
        dataset_version: The derived, immutable identity.
    """

    source: RawSource
    schema: DatasetSchema
    timezone_name: str
    frequency: TimeFrequency
    price_basis: PriceBasis
    cleaning_policy: CleaningPolicy
    engine_version: str
    dataset_version: str
    calendar_id: str | None = None
    transformations: tuple[TransformationRecord, ...] = ()
    adjustments: tuple[AdjustmentRecord, ...] = ()
    instrument: InstrumentSpec | None = None

    def __post_init__(self) -> None:
        if not self.dataset_version.strip():
            raise DataValidationError("A dataset's provenance must carry its derived version.")

    @property
    def content_hash(self) -> str:
        """The digest of the bytes this dataset was built from."""

        return self.source.content_hash

    @property
    def was_transformed(self) -> bool:
        """Whether anything at all was changed between the source and the dataset."""

        return bool(self.transformations) or bool(self.adjustments)


def canonical_dataset_key(
    name: str,
    content_hash: str,
    schema: DatasetSchema,
    timezone_name: str,
    calendar_id: str | None,
    frequency: TimeFrequency,
    price_basis: PriceBasis,
    cleaning_policy: CleaningPolicy,
    transformations: Sequence[TransformationRecord],
    adjustments: Sequence[AdjustmentRecord],
) -> str:
    """Render the canonical key a dataset's version is derived from.

    Field order is fixed and part of the specification. The closed fields are
    listed in a chosen order rather than sorted, following
    ``canonical_instrument_key``; the two open-ended sequences are rendered in
    *application order* rather than sorted, because "deduplicated then sorted"
    and "sorted then deduplicated" are different pipelines that can produce
    different data, and an identity that could not tell them apart would be
    claiming a reproducibility it does not have.

    The function is public so that the rendering can be pinned by a test and
    read by anyone auditing a dataset version.
    """

    bindings = schema.bindings
    lines = [
        DATASET_KEY_SCHEME,
        f"name={name}",
        f"content={content_hash}",
        f"record_type={schema.data_type}",
        f"timestamp_format={schema.timestamp_format or 'none'}",
        f"timezone={timezone_name}",
        f"calendar={calendar_id or 'none'}",
        f"frequency={frequency.name}",
        f"price_basis={price_basis.name}",
        f"cleaning.duplicates={cleaning_policy.duplicates.name}",
        f"cleaning.ordering={cleaning_policy.ordering.name}",
        f"cleaning.invalid_records={cleaning_policy.invalid_records.name}",
        f"cleaning.missing_values={cleaning_policy.missing_values.name}",
        "schema",
        *(f"{role}={bindings[role]}" for role in sorted(bindings)),
        "transformations",
        *(f"{record.operation}={record.rows_affected}" for record in transformations),
        "adjustments",
        *(
            f"{record.action}:{record.symbol}@{record.effective_timestamp!r}={record.factor!r}"
            for record in adjustments
        ),
    ]
    return "\n".join(lines)


def derive_dataset_version(
    name: str,
    content_hash: str,
    schema: DatasetSchema,
    timezone_name: str,
    calendar_id: str | None,
    frequency: TimeFrequency,
    price_basis: PriceBasis,
    cleaning_policy: CleaningPolicy,
    transformations: Sequence[TransformationRecord],
    adjustments: Sequence[AdjustmentRecord],
) -> str:
    """Derive the immutable identity of one dataset.

    Returns ``"<name>@<digest>"``, where the digest is the SHA-256 of
    :func:`canonical_dataset_key`. The name is carried in front so that a
    version is readable in a log and a catalogue sorts sensibly; the digest is
    what makes it an identity. The full digest is used rather than a prefix,
    matching ``evidence_id``, so that no collision argument is ever needed.

    Raises:
        DataValidationError: If ``name`` is empty or contains ``"@"``, which
            would make the rendered version ambiguous to split.
    """

    cleaned = name.strip()
    if not cleaned:
        raise DataValidationError("A dataset must be named before its version can be derived.")
    if "@" in cleaned:
        raise DataValidationError(
            f"A dataset name may not contain '@': {name!r}. The character separates the name "
            "from the digest in a dataset version."
        )

    key = canonical_dataset_key(
        cleaned,
        content_hash,
        schema,
        timezone_name,
        calendar_id,
        frequency,
        price_basis,
        cleaning_policy,
        transformations,
        adjustments,
    )
    return f"{cleaned}@{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


def derive_transformed_version(
    parent_version: str, transformations: Sequence[TransformationRecord]
) -> str:
    """Derive the identity of a dataset produced by transforming another.

    The lineage rule, and the same rule as :func:`derive_dataset_version`
    applied recursively: a dataset's identity is derived from everything that
    determined its content. For a dataset read from a source that is the bytes
    and the configuration; for one derived from another dataset it is the
    *parent's identity* -- which already encodes all of that -- plus what was
    done to it.

    So a cleaned dataset's version changes when the raw dataset changes, when
    the cleaning policy changes, or when the cleaning removed a different
    number of rows, and is otherwise stable. Two processes cleaning the same
    raw dataset the same way arrive at the same identity with no coordination.

    Raises:
        DataValidationError: If ``parent_version`` is empty, or if no
            transformation is supplied -- a dataset that was not transformed is
            its parent, and minting a second identity for it would make one
            dataset answer to two names.
    """

    parent = parent_version.strip()
    if not parent:
        raise DataValidationError("A derived dataset must name the version it came from.")
    if not transformations:
        raise DataValidationError(
            f"{parent} was not transformed, so it has no derived version. A dataset that "
            "nothing was done to is the dataset it came from."
        )

    name = parent.split("@", 1)[0]
    key = "\n".join(
        [
            DATASET_KEY_SCHEME,
            f"derived_from={parent}",
            "transformations",
            *(f"{record.operation}={record.rows_affected}" for record in transformations),
        ]
    )
    return f"{name}@{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
