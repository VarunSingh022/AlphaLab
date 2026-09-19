"""
AlphaLab Examples
=================

Example 15 : Universal Data Ingestion

Difficulty : Intermediate

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 07

Topics
------

• Ingesting a CSV
• Schema detection, and what it refuses to guess
• Structured validation findings
• Explicit cleaning policies
• Data quality reports
• The canonical dataset
• Provenance and the derived dataset version

What this shows
---------------

The whole of the v3.1 path, on a file that is deliberately broken::

    raw source -> schema detection -> validation -> cleaning
               -> quality report   -> canonical dataset
               -> provenance and an immutable version

``examples/data/messy_ohlcv.csv`` carries, on purpose: vendor column spellings
(``Ticker``/``Date``/``Vol``), a duplicated bar, a bar whose high is below its
low, two rows with missing prices, an out-of-order row and a row with one field
too many. Nothing here is repaired quietly -- every one of them is reported,
and what happens next is the policy's decision rather than AlphaLab's.

Run

    python examples/15_data_ingestion.py
"""

from datetime import time
from pathlib import Path

from alphalab.api import ingest_csv, inspect_csv
from alphalab.data import (
    CleaningPolicy,
    DataAssetClass,
    DuplicatePolicy,
    IngestionRequest,
    InvalidRecordPolicy,
    MarketCalendar,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    SessionWindow,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)

DATA = Path(__file__).parent / "data" / "messy_ohlcv.csv"

#: Unix seconds at which this example claims to have retrieved the file. Fixed
#: so the example is deterministic; in an application it is the real time of
#: the real retrieval, and it is recorded but deliberately *not* part of the
#: dataset's identity.
RETRIEVED_AT = 1_726_000_000.0

#: The venue the series belongs to. AlphaLab ships no holiday data, so the
#: calendar is declared here -- which is exactly how an application declares
#: its own market.
NYSE = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions={day: (SessionWindow(time(9, 30), time(16, 0)),) for day in range(5)},
)


def main() -> None:
    """Walk one broken file all the way to a versioned canonical dataset."""

    print("=" * 74)
    print("AlphaLab Example 15 : Universal Data Ingestion")
    print("=" * 74)

    # ------------------------------------------------------------------
    # Step 01 : Inspect the source before committing to anything
    # ------------------------------------------------------------------
    #
    # `inspect_csv` is the dry run. It hashes the bytes, reads the rows and
    # reports what it made of them -- and coerces, validates and cleans
    # nothing. This is where an ambiguity gets resolved, before a dataset
    # exists to be wrong.

    source, table, detection = inspect_csv(DATA, RETRIEVED_AT)

    print()
    print("Step 01 - Source")
    print(f"  kind          : {source.kind.name}")
    print(f"  location      : {source.location}")
    print(f"  bytes         : {source.byte_count}")
    print(f"  content hash  : {source.content_hash[:32]}...")
    print(f"  delimiter     : {table.dialect.delimiter!r}")
    print(f"  rows read     : {len(table.rows)}")
    print(f"  malformed     : {len(table.malformed)}")

    for malformed in table.malformed:
        print(f"    line {malformed.line_number}: {malformed.reason}")

    # ------------------------------------------------------------------
    # Step 02 : Schema detection
    # ------------------------------------------------------------------
    #
    # The file says `Ticker`, `Date` and `Vol`. Detection resolves each to a
    # canonical field, and reports the reason for every binding it made -- a
    # heuristic that cannot be read is a heuristic nobody can check.

    print()
    print("Step 02 - Schema detection")
    record_type = detection.record_type.name if detection.record_type else "-"
    stamp_format = detection.timestamp_format.name if detection.timestamp_format else "-"
    print(f"  record type   : {record_type}")
    print(f"    because     : {detection.record_type_rationale}")
    print(f"  timestamps    : {stamp_format}")
    print(f"    because     : {detection.timestamp_rationale}")
    print("  bindings      :")
    for binding in detection.bindings:
        print(f"    {binding.role.name:<12} <- {binding.column!r}")
    if detection.unmapped_columns:
        print(f"  unrecognised  : {list(detection.unmapped_columns)}")
    if detection.ambiguous:
        for ambiguous in detection.ambiguous:
            print(f"  AMBIGUOUS     : {ambiguous.role.name} could be {list(ambiguous.columns)}")
    print(f"  resolved      : {detection.is_resolved}")

    # ------------------------------------------------------------------
    # Step 03 : Ingest, under a policy that changes nothing it is not told to
    # ------------------------------------------------------------------
    #
    # Every field of the request is a decision AlphaLab refuses to make on its
    # own. A wrong frequency mis-bins a series; a wrong basis claims adjusted
    # prices are raw; a wrong zone shifts every bar by hours. None of them
    # raises -- they all produce plausible numbers -- which is why none of them
    # has a default.

    policy = CleaningPolicy(
        duplicates=DuplicatePolicy.KEEP_FIRST,
        ordering=OrderingPolicy.SORT,
        invalid_records=InvalidRecordPolicy.DROP,
        missing_values=MissingValuePolicy.DROP_ROW,
    )

    request = IngestionRequest(
        name="MESSY-EQUITY-1D",
        source=raw_source_from_bytes(
            SourceKind.LOCAL_FILE, str(DATA), b"", RETRIEVED_AT, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=policy,
        price_basis=PriceBasis.RAW,
        calendar=NYSE,
    )

    print()
    print("Step 03 - The decisions the caller made, because AlphaLab will not")
    print(f"  frequency     : {request.frequency.name}")
    print(f"  asset class   : {request.asset_class.name}")
    print(f"  price basis   : {request.price_basis.name}  (what the source prices already are)")
    print(f"  calendar      : {NYSE.calendar_id} in {NYSE.timezone_name}")
    print("  cleaning policy:")
    print(f"    duplicates    : {policy.duplicates.name}")
    print(f"    ordering      : {policy.ordering.name}")
    print(f"    invalid rows  : {policy.invalid_records.name}")
    print(f"    missing values: {policy.missing_values.name}  (there is no FILL, ever)")

    result = ingest_csv(DATA, request)

    # ------------------------------------------------------------------
    # Step 04 : Validation findings
    # ------------------------------------------------------------------
    #
    # Findings are facts, not verdicts. Each one names the defect, how much it
    # matters, and where -- so a person can open the file at that line.

    quality = result.quality

    print()
    print("Step 04 - Validation findings")
    print(f"  rows            : {quality.row_count}")
    print(f"  became records  : {quality.valid_rows}")
    print(f"  rejected        : {quality.rejected_count}")
    for rejection in quality.rejected_rows:
        reason = rejection.findings[0]
        column = f" [{reason.column}]" if reason.column else ""
        print(f"    line {rejection.line_number:<3} {reason.kind.name}{column}")

    print(f"  errors          : {len(quality.errors)}")
    for finding in quality.errors:
        if finding.line_number is None:
            print(f"    {finding.kind.name}: {finding.detail[:78]}")
    print(f"  warnings        : {len(quality.warnings)}")

    # ------------------------------------------------------------------
    # Step 05 : The quality report
    # ------------------------------------------------------------------
    #
    # The report describes the data AS FOUND, before cleaning. Reporting it
    # afterwards would make every dataset look clean: a file with 400 duplicate
    # rows and a KEEP_LAST policy would score 100 and say nothing about the 400.

    print()
    print("Step 05 - Quality report (the data as it was found)")
    print(f"  completeness  : {quality.completeness}%  (rows that became records)")
    print(f"  consistency   : {quality.consistency}%  (records with no error finding)")
    print(f"  duplicates    : {quality.duplicate_count}")
    print(f"  missing       : {quality.missing_count}")
    print(f"  invalid       : {quality.invalid_count}")
    print(f"  out of order  : {quality.out_of_order_count}")
    print(f"  score         : {quality.quality_score}  <- a summary, never the whole story")

    # ------------------------------------------------------------------
    # Step 06 : Cleaning, recorded
    # ------------------------------------------------------------------
    #
    # Nothing was altered silently. Each transformation says what was done, to
    # how many rows, and why -- and all of it rides into the provenance below.

    print()
    print("Step 06 - Transformations applied")
    if not result.transformations:
        print("  (none)")
    for transformation in result.transformations:
        print(f"  {transformation.operation:<22} rows={transformation.rows_affected}")
        print(f"    reason: {transformation.reason}")

    for assumption in result.assumptions:
        print(f"  ASSUMPTION: {assumption}")

    # ------------------------------------------------------------------
    # Step 07 : The canonical dataset and its provenance
    # ------------------------------------------------------------------

    dataset = result.dataset
    provenance = dataset.require_provenance()

    print()
    print("Step 07 - Canonical dataset")
    print(f"  records       : {len(dataset.records)}")
    print(f"  instruments   : {sorted({record.symbol for record in dataset.records})}")
    print(
        f"  span          : {dataset.metadata.start_timestamp} .. {dataset.metadata.end_timestamp}"
    )
    print(f"  timezone      : {dataset.timezone}")

    print()
    print("  Provenance")
    print(f"    source      : {provenance.source.kind.name} {provenance.source.location}")
    print(f"    retrieved   : {provenance.source.retrieved_at}")
    print(f"    content     : {provenance.content_hash[:48]}...")
    print(
        f"    schema      : {provenance.schema.data_type} read as "
        f"{provenance.schema.timestamp_format}"
    )
    print(f"    calendar    : {provenance.calendar_id}")
    print(f"    frequency   : {provenance.frequency.name}")
    print(f"    price basis : {provenance.price_basis.name}")
    print(
        f"    policy      : duplicates={provenance.cleaning_policy.duplicates.name}, "
        f"invalid={provenance.cleaning_policy.invalid_records.name}"
    )
    print(f"    engine      : AlphaLab {provenance.engine_version}")
    print(f"    transformed : {provenance.was_transformed}")

    print()
    print("  Dataset version (derived, immutable)")
    print(f"    {dataset.dataset_version}")

    # The identity is derived from the content and the configuration, so a
    # second ingestion of the same bytes under the same request resolves to the
    # same version -- in any process, on any machine, with no shared state.
    again = ingest_csv(DATA, request)
    print()
    print(
        f"  Re-ingesting the same file reproduces the version : "
        f"{again.dataset_version == result.dataset_version}"
    )

    print()
    print("Nothing above was repaired quietly. Every rejected row, every applied")
    print("transformation and every assumption is in the report or the provenance.")


if __name__ == "__main__":
    main()
