"""Shared ingestion for the v3.2 research examples (17-24).

Every one of those examples starts from the same canonical dataset, and each of
them needs eight lines of ingestion configuration to get there. Repeating those
lines eight times would make the examples about ingestion rather than about
research, so they live here once and example 15 remains the place to read about
ingestion itself.

This is deliberately **not** a convenience that fetches data. The dataset is
built from one named file with one stated cleaning policy, and its derived
version is returned with it. An example that could reach for different data
than the example beside it would make their results incomparable, which is the
rule :class:`~alphalab.api.DataRequest` states for research selection.
"""

from __future__ import annotations

import csv
from pathlib import Path

from alphalab.api import ingest_csv
from alphalab.data import (
    CleaningPolicy,
    DataAssetClass,
    Dataset,
    DuplicatePolicy,
    IngestionRequest,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)

DATA = Path(__file__).parent / "data" / "research_panel.csv"
RETRIEVED_AT = 1_726_000_000.0

#: Ten names over two hundred daily sessions, with weekends absent -- so the
#: series is genuinely unevenly spaced and a horizon expressed in seconds would
#: be wrong. See ``examples/data/README.md``.
CLEANING = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)


def load_panel() -> Dataset:
    """Ingest the research panel into a canonical, versioned dataset."""

    request = IngestionRequest(
        name="RESEARCH-PANEL",
        source=raw_source_from_bytes(
            SourceKind.LOCAL_FILE, str(DATA), b"", RETRIEVED_AT, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
    )
    return ingest_csv(DATA, request).dataset


def sectors() -> dict[str, str]:
    """The sector each symbol belongs to, read from the file's own column.

    AlphaLab ships no sector taxonomy for this, and neither
    :func:`~alphalab.factor_library.neutralization.neutralize_group` nor
    :func:`~alphalab.factor_library.exposure.factor_exposure` will invent one --
    a classification is something somebody maintains. Here the file carries it,
    and the example passes it in.
    """

    with DATA.open(newline="") as handle:
        return {row["symbol"]: row["sector"] for row in csv.DictReader(handle)}


def banner(number: int, title: str) -> None:
    """The header every example prints."""

    line = "=" * 74
    print(line)
    print(f"AlphaLab Example {number} : {title}")
    print(line)


def lineage(dataset: Dataset) -> str:
    """The dataset's derived version, shortened for printing."""

    return dataset.require_provenance().dataset_version
