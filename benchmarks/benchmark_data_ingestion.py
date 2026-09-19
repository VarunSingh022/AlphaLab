"""Benchmark for the v3.1 universal ingestion path.

Measures the whole path -- delimited read, schema detection, per-row coercion,
set-level validation, policy cleaning and identity derivation -- across four
orders of magnitude, so a return to super-linear behaviour shows up as a growth
ratio rather than as a slow afternoon.

Linear growth predicts a 10x time increase for each 10x of rows.
"""

import time

from alphalab.data import (
    CleaningPolicy,
    CsvDialect,
    DataAssetClass,
    DuplicatePolicy,
    IngestionRequest,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    detect_schema,
    ingest_table,
    raw_source_from_bytes,
    read_delimited,
)

POLICY = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

#: 1,000,000 is included but takes ~9s and dominates the run; the shape is
#: already visible by 100,000.
WORKLOADS = (1_000, 10_000, 100_000)


def _csv(rows: int) -> str:
    lines = ["symbol,timestamp,open,high,low,close,volume"]
    lines += [
        f"AAPL,{1_700_000_000 + index * 60},100.0,105.0,95.0,102.0,50.0" for index in range(rows)
    ]
    return "\n".join(lines) + "\n"


def run_benchmark() -> None:
    print("Universal Data Ingestion Benchmark")
    print("Full path: read -> detect -> coerce -> validate -> clean -> derive identity")
    print()
    print(
        f"{'rows':>10} {'read':>9} {'detect':>9} {'ingest':>9} {'total':>9} "
        f"{'us/row':>9} {'growth':>8}"
    )

    previous: float | None = None
    for rows in WORKLOADS:
        text = _csv(rows)
        request = IngestionRequest(
            name="BENCH",
            source=raw_source_from_bytes(
                SourceKind.IN_MEMORY, "benchmark", text.encode("utf-8"), 1.0, "text/csv"
            ),
            frequency=TimeFrequency.MINUTE,
            asset_class=DataAssetClass.EQUITY,
            cleaning_policy=POLICY,
            price_basis=PriceBasis.RAW,
        )

        start = time.perf_counter()
        table = read_delimited(text, CsvDialect(","))
        after_read = time.perf_counter()
        detect_schema(table)
        after_detect = time.perf_counter()
        result = ingest_table(table, request)
        end = time.perf_counter()

        total = end - start
        growth = "-" if previous is None else f"{total / previous:.2f}x"
        print(
            f"{rows:>10} {after_read - start:>9.4f} {after_detect - after_read:>9.4f} "
            f"{end - after_detect:>9.4f} {total:>9.4f} {total / rows * 1e6:>9.2f} {growth:>8}"
        )
        previous = total

        if len(result.dataset.records) != rows:
            raise AssertionError("the benchmark must actually do the work")

    print()
    print("Growth of ~10x per 10x of rows is linear. ~100x would be quadratic.")


if __name__ == "__main__":
    run_benchmark()
