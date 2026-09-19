"""The path from bytes to a canonical, versioned dataset.

One function does the whole of it -- :func:`ingest_table` -- and the order of
its steps is the contract:

1. **Detect** the schema, and refuse unless every role resolved to exactly one
   column (:mod:`alphalab.data.schema`).
2. **Coerce** each row into a canonical record, keeping every row that could
   not be coerced, with the reason (:mod:`alphalab.data.validation`).
3. **Validate** the resulting series for the defects only a series can show.
4. **Clean** under the caller's policy, recording every change
   (:mod:`alphalab.data.cleaning`).
5. **Adjust** for corporate actions, if a basis other than raw was asked for
   (:mod:`alphalab.data.corporate_actions`).
6. **Derive** the dataset's identity from everything above
   (:mod:`alphalab.data.provenance`).

Diagnosis and treatment are reported separately
-----------------------------------------------

The :class:`~alphalab.data.quality.DataQualityReport` describes the data **as
it was found**, before cleaning. The transformations describe **what was done
about it**. Reporting quality after cleaning would make every dataset look
clean -- a file with 400 duplicate rows and a ``KEEP_LAST`` policy would score
100 and say nothing about the 400 -- which is precisely the reassuring lie this
package exists to avoid.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alphalab.common.version import __version__
from alphalab.data.assets import InstrumentSpec, asset_class_of
from alphalab.data.calendar import MarketCalendar
from alphalab.data.cleaning import CleaningPolicy, TransformationRecord, clean_records
from alphalab.data.corporate_actions import AdjustmentRecord, PriceBasis, apply_adjustments
from alphalab.data.csv_source import RawTable
from alphalab.data.dataset import Dataset
from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import Bar, CanonicalRecord, Dividend, Split
from alphalab.data.loader import create_dataset
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.parser import parse_raw_rows
from alphalab.data.provenance import DatasetProvenance, derive_dataset_version
from alphalab.data.quality import DataQualityReport, evaluate_quality
from alphalab.data.schema import DatasetSchema, RecordType, SchemaDetection, detect_schema
from alphalab.data.source import RawSource
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import DateOnlyPolicy, TimeFrequency, TimestampFormat, infer_frequency
from alphalab.data.validation import (
    FindingKind,
    RowRejection,
    Severity,
    ValidationFinding,
    coerce_row,
    validate_records,
)

__all__ = ["IngestionRequest", "IngestionResult", "ingest_table", "parse_and_load"]


@dataclass(frozen=True, slots=True)
class IngestionRequest:
    """Every decision an ingestion is not entitled to make on its own.

    None of these is defaulted. Each names something that, guessed wrongly,
    produces a plausible dataset rather than an error: a zone that silently
    shifts every bar by hours, a frequency that mis-bins a series, a basis that
    claims prices are raw when they are adjusted.

    Attributes:
        name: What the dataset is called. Becomes the readable half of its
            derived version, and may not contain ``"@"``.
        source: Provenance for the bytes being ingested.
        frequency: The series' frequency. Spacings that disagree are reported.
        asset_class: What kind of series this is.
        cleaning_policy: What cleaning is permitted to change.
        price_basis: What the prices in the source already are. ``RAW`` unless
            the vendor has already adjusted them.
        timezone_name: The zone this series is reported in. Required when the
            source's timestamps carry no offset, since they cannot name an
            instant without it; optional, and purely descriptive, when they do.
        date_policy: Which instant a bare date denotes.
        symbol: Instrument name for sources with no instrument column.
        calendar: The venue's calendar, when one is declared.
        instrument: What the series describes, when one spec covers it.
        declared_record_type: Overrides detection when a file is ambiguous.
        splits: Split actions, applied for a non-raw target basis.
        dividends: Cash dividends, applied for ``TOTAL_RETURN``.
        target_basis: The basis to produce. ``None`` leaves the prices as they
            were found.
    """

    name: str
    source: RawSource
    frequency: TimeFrequency
    asset_class: DataAssetClass
    cleaning_policy: CleaningPolicy
    price_basis: PriceBasis
    timezone_name: str | None = None
    date_policy: DateOnlyPolicy | None = None
    symbol: str | None = None
    calendar: MarketCalendar | None = None
    instrument: InstrumentSpec | None = None
    declared_record_type: RecordType | None = None
    splits: tuple[Split, ...] = ()
    dividends: tuple[Dividend, ...] = ()
    target_basis: PriceBasis | None = None

    def __post_init__(self) -> None:
        if self.instrument is not None:
            implied = asset_class_of(self.instrument)
            if implied is not self.asset_class:
                raise DataValidationError(
                    f"{self.name}: asset_class is {self.asset_class.name} but the instrument "
                    f"spec describes a {implied.name}. One of the two is wrong, and guessing "
                    "which would mis-classify the dataset."
                )
        if (
            self.calendar is not None
            and self.timezone_name is not None
            and self.calendar.timezone_name != self.timezone_name
        ):
            raise DataValidationError(
                f"{self.name}: timezone_name is {self.timezone_name!r} but calendar "
                f"{self.calendar.calendar_id} is in {self.calendar.timezone_name!r}. "
                "Reading timestamps in one zone and sessions in another silently "
                "misplaces every bar relative to its session."
            )


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """The dataset, and everything learned on the way to it."""

    dataset: Dataset
    detection: SchemaDetection
    quality: DataQualityReport
    transformations: tuple[TransformationRecord, ...]
    adjustments: tuple[AdjustmentRecord, ...]
    assumptions: tuple[str, ...]

    @property
    def dataset_version(self) -> str:
        """The derived identity of the dataset produced."""

        return self.dataset.require_provenance().dataset_version


def ingest_table(table: RawTable, request: IngestionRequest) -> IngestionResult:
    """Turn a raw table into a canonical, versioned dataset.

    Raises:
        DataValidationError: If the schema cannot be resolved, or if no row
            survived coercion.
        DataQualityError: If the cleaning policy refuses a defect that is
            present.
    """

    detection = detect_schema(table, request.declared_record_type).require()
    schema = DatasetSchema.from_detection(detection, request.timezone_name, table.columns)

    records: list[CanonicalRecord] = []
    rejections: list[RowRejection] = []
    findings: list[ValidationFinding] = []

    for malformed in table.malformed:
        finding = ValidationFinding(
            kind=FindingKind.MALFORMED_ROW,
            severity=Severity.ERROR,
            line_number=malformed.line_number,
            column=None,
            detail=malformed.reason,
        )
        findings.append(finding)
        rejections.append(
            RowRejection(
                line_number=malformed.line_number,
                values=malformed.values,
                findings=(finding,),
            )
        )

    # A zone is *applied* only where a timestamp needs one. An offset-bearing
    # source already names an instant, and parse_timestamp refuses a zone for
    # it -- but the caller may still legitimately have said which zone the
    # series is reported in, which is what `_reported_zone` uses below.
    parse_zone = (
        request.timezone_name
        if detection.timestamp_format in (TimestampFormat.ISO_8601_NAIVE, TimestampFormat.DATE_ONLY)
        else None
    )

    for row in table.rows:
        record, row_findings = coerce_row(
            table,
            row,
            detection,
            parse_zone,
            request.date_policy,
            request.symbol,
        )
        findings.extend(row_findings)
        if record is None:
            rejections.append(
                RowRejection(line_number=row.line_number, values=row.values, findings=row_findings)
            )
        else:
            records.append(record)

    if not records:
        raise DataValidationError(
            f"{request.name}: no row in {request.source.location} could be read as a "
            f"{detection.record_type.name if detection.record_type else 'record'}. "
            f"{len(rejections)} rows were rejected; the first reports: "
            f"{_first_reason(rejections)}"
        )

    findings.extend(validate_records(records, request.frequency))
    quality = evaluate_quality(
        dataset_id=request.name,
        records=records,
        row_count=len(table.rows) + len(table.malformed),
        rejected=rejections,
        findings=findings,
    )

    outcome = clean_records(records, request.cleaning_policy, findings)
    cleaned = list(outcome.records)
    transformations = list(outcome.transformations)

    adjustments: tuple[AdjustmentRecord, ...] = ()
    basis = request.price_basis
    if request.target_basis is not None and request.target_basis is not request.price_basis:
        bars = [record for record in cleaned if isinstance(record, Bar)]
        if len(bars) != len(cleaned):
            raise DataValidationError(
                f"{request.name}: corporate-action adjustment applies to bars, and this "
                "dataset carries records that are not bars."
            )
        adjusted = apply_adjustments(bars, request.splits, request.dividends, request.target_basis)
        cleaned = list(adjusted.bars)
        adjustments = adjusted.adjustments
        basis = adjusted.basis

    timezone_name = _reported_zone(request)
    version = derive_dataset_version(
        name=request.name,
        content_hash=request.source.content_hash,
        schema=schema,
        timezone_name=timezone_name,
        calendar_id=None if request.calendar is None else request.calendar.calendar_id,
        frequency=request.frequency,
        price_basis=basis,
        cleaning_policy=request.cleaning_policy,
        transformations=transformations,
        adjustments=adjustments,
    )

    provenance = DatasetProvenance(
        source=request.source,
        schema=schema,
        timezone_name=timezone_name,
        frequency=request.frequency,
        price_basis=basis,
        cleaning_policy=request.cleaning_policy,
        engine_version=__version__,
        dataset_version=version,
        calendar_id=None if request.calendar is None else request.calendar.calendar_id,
        transformations=tuple(transformations),
        adjustments=adjustments,
        instrument=request.instrument,
    )

    stamps = [record.timestamp for record in cleaned]
    metadata = DatasetMetadata(
        dataset_id=version,
        source_name=request.source.location,
        asset_class=request.asset_class,
        frequency=request.frequency,
        start_timestamp=min(stamps),
        end_timestamp=max(stamps),
        timezone=timezone_name,
        metadata={"name": request.name, "content_hash": request.source.content_hash},
    )

    inferred, rationale = infer_frequency(sorted(stamps))
    assumptions = list(detection.assumptions)
    if inferred is not None and inferred is not request.frequency:
        assumptions.append(
            f"the declared frequency is {request.frequency.name} but {rationale}; the "
            "declaration was used and the disagreement is recorded rather than resolved"
        )

    dataset = Dataset(
        metadata=metadata,
        schema=schema,
        quality=quality.summarize(),
        records=tuple(cleaned),
        provenance=provenance,
        quality_detail=quality,
    )

    return IngestionResult(
        dataset=dataset,
        detection=detection,
        quality=quality,
        transformations=tuple(transformations),
        adjustments=adjustments,
        assumptions=tuple(assumptions),
    )


def _reported_zone(request: IngestionRequest) -> str:
    """The zone the dataset's series is reported in.

    Distinct from the zone used to *parse* a timestamp. A source carrying UTC
    offsets names each instant without needing a zone declared, and yet the
    dataset still has to say which zone its series belongs to -- which local
    day a bar falls on depends on it, and so does every session comparison.

    In order: what the caller said, then the declared venue's zone, then UTC --
    which is what an offset-bearing timestamp was normalized to, and the only
    answer that is true rather than guessed when nothing else was stated.
    """

    if request.timezone_name is not None:
        return request.timezone_name
    if request.calendar is not None:
        return request.calendar.timezone_name
    return "UTC"


def _first_reason(rejections: Sequence[RowRejection]) -> str:
    for rejection in rejections:
        if rejection.findings:
            return rejection.findings[0].detail
    return "no reason was recorded"


def parse_and_load(metadata: DatasetMetadata, raw_rows: Sequence[Mapping[str, Any]]) -> Dataset:
    """Wrap rows already in memory into a typed dataset, with no provenance.

    The v1 path, with two v3.1 corrections, both about not losing data quietly:

    * it no longer overwrites every record's symbol with the dataset's id --
      that overwrite collapsed a three-instrument file into three records of one
      instrument, whose duplicate instants cleaning then removed;
    * :func:`~alphalab.data.parser.parse_raw_rows` beneath it now **refuses** a
      row it cannot translate instead of dropping it, so this function returns
      a dataset covering every row it was given or raises.

    The dataset this produces carries ``provenance=None``, because rows in
    memory have no bytes to hash and no basis anyone declared. Use
    :func:`~alphalab.api.ingest_csv` when lineage matters, or
    :func:`~alphalab.api.ingest_rows` when some rows are expected to be
    unusable and the rest are still wanted.
    """

    return create_dataset(metadata, parse_raw_rows(metadata.dataset_id, raw_rows))
