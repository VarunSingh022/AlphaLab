"""The ingestion path stays linear in the number of rows.

Every stage is written to make one pass and accumulate into a list: reading,
coercion, set-level validation, cleaning and identity derivation. That is
deliberate rather than incidental, and it is the property most easily lost --
the obvious implementation of "is this record a duplicate?" is a scan of the
records so far, which is quadratic and still fast enough to look fine on the
thousand-row file somebody tests with.

The structural tests below assert the mechanism: the duplicate and ordering
checks use hashed lookups and a per-instrument high-water mark rather than
rescanning. The timing test is a coarse backstop with a wide tolerance -- it is
there to catch a return to quadratic behaviour, not to police constant factors.

Measured on the machine this was developed on: 1,000 rows in 7ms, 10,000 in
74ms, 100,000 in 0.87s, and 1,000,000 in 9.0s -- 10.3x, 11.7x and 10.3x for each
10x of data, against a linear prediction of 10x.
"""

from __future__ import annotations

import inspect
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
    cleaning,
    ingest_table,
    raw_source_from_bytes,
    read_delimited,
    validation,
)

#: Ratio of the two workload sizes used by the timing test.
SCALE = 10
SMALL = 5_000
LARGE = SMALL * SCALE

#: Linear growth predicts ~10x. Quadratic predicts ~100x. Anything under 30x
#: (3x the linear prediction) is comfortably not quadratic.
MAX_GROWTH = 30.0

#: 50,000 rows ingest in well under half a second on a development machine. A
#: 20s ceiling survives a slow CI runner while still failing a reintroduced
#: quadratic, which would take minutes.
LARGE_WORKLOAD_BUDGET_SECONDS = 20.0

POLICY = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)


def _csv(rows: int) -> str:
    """A well-formed minute series, so the timing measures the path and not the errors."""

    lines = ["symbol,timestamp,open,high,low,close,volume"]
    lines += [
        f"AAPL,{1_700_000_000 + index * 60},100.0,105.0,95.0,102.0,50.0" for index in range(rows)
    ]
    return "\n".join(lines) + "\n"


def _ingest(rows: int) -> float:
    """Ingest ``rows`` rows and return the elapsed seconds."""

    text = _csv(rows)
    request = IngestionRequest(
        name="COMPLEXITY",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "benchmark", text.encode("utf-8"), 1.0, "text/csv"
        ),
        frequency=TimeFrequency.MINUTE,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=POLICY,
        price_basis=PriceBasis.RAW,
    )

    started = time.perf_counter()
    table = read_delimited(text, CsvDialect(","))
    result = ingest_table(table, request)
    elapsed = time.perf_counter() - started

    assert len(result.dataset.records) == rows, "the workload must actually be done"
    return elapsed


# --------------------------------------------------------------------------- #
# Structural: the mechanism that makes it linear
# --------------------------------------------------------------------------- #


def test_duplicate_detection_uses_a_hashed_set_rather_than_rescanning() -> None:
    """The obvious implementation -- "is this key in the records so far?" -- is
    a scan per row, and is quadratic."""

    source = inspect.getsource(validation.validate_records)

    assert "seen: set[tuple[str, float]] = set()" in source
    assert "seen.add(key)" in source
    assert "in seen" in source


def test_ordering_is_checked_against_a_high_water_mark_per_instrument() -> None:
    """One dict lookup per record, rather than a comparison against every
    record already read."""

    source = inspect.getsource(validation.validate_records)

    assert "previous: dict[str, float] = {}" in source
    assert "previous.get(record.symbol)" in source


def test_deduplication_keeps_one_pass_and_one_dict() -> None:
    source = inspect.getsource(cleaning._deduplicate)

    assert "chosen: dict[tuple[str, float], CanonicalRecord] = {}" in source
    assert "for record in records:" in source
    assert source.count("for ") == 1, "one pass, not a nested scan"


def test_cleaning_does_not_copy_the_record_set_per_row() -> None:
    """A ``records = records + (record,)`` inside the loop would rebuild the
    whole tuple per row -- the same O(N^2) shape ADR-0034 removed from the
    standalone engines' event logs."""

    source = inspect.getsource(cleaning.clean_records)

    assert "working = list(records)" in source
    assert "working + [" not in source
    assert "working = working +" not in source


# --------------------------------------------------------------------------- #
# Timing: a coarse backstop
# --------------------------------------------------------------------------- #


def test_ingestion_does_not_grow_quadratically_with_row_count() -> None:
    small = _ingest(SMALL)
    large = _ingest(LARGE)

    # A floor on the small measurement keeps a fast machine's timer resolution
    # from turning a tiny denominator into a spurious growth ratio.
    growth = large / max(small, 1e-3)

    assert growth < MAX_GROWTH, (
        f"{SMALL} rows took {small:.3f}s and {LARGE} took {large:.3f}s ({growth:.1f}x for "
        f"{SCALE}x the data). Linear predicts ~{SCALE}x and quadratic ~{SCALE**2}x; "
        "something in the ingestion path is rescanning."
    )
    assert large < LARGE_WORKLOAD_BUDGET_SECONDS, (
        f"{LARGE} rows took {large:.3f}s, over the {LARGE_WORKLOAD_BUDGET_SECONDS}s ceiling"
    )
