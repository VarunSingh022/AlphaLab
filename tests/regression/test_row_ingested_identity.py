"""Rows ingested from memory are identified by what they contain (ledger KD-004).

A file's dataset version derives from its source's content hash -- the bytes the
rows were read from. Rows handed to :func:`alphalab.api.ingest_rows` have no
bytes behind them, and until v3.10 their version derived from whatever source
the caller supplied: two different sets of rows recorded with one empty-payload
source shared a dataset version, so the identity every downstream study and
fingerprint cites named neither of them.
"""

from alphalab.api import ingest_rows
from alphalab.data.source import SourceKind, raw_source_from_bytes
from tests.unit.test_api import RETRIEVED_AT, _request


def _rows(close: str) -> list[dict[str, str]]:
    return [
        {
            "symbol": "AAPL",
            "timestamp": "2025-01-02T16:00:00Z",
            "open": "1",
            "high": "2",
            "low": "0.5",
            "close": close,
            "volume": "10",
        }
    ]


def _version(close: str, payload: bytes = b"") -> str:
    source = raw_source_from_bytes(SourceKind.IN_MEMORY, "rows", payload, RETRIEVED_AT, "x")
    return ingest_rows(_rows(close), _request(source=source)).dataset.dataset_id


def test_different_rows_with_one_source_are_different_datasets() -> None:
    assert _version("1.5") != _version("1.6")


def test_the_same_rows_are_the_same_dataset() -> None:
    assert _version("1.5") == _version("1.5")


def test_the_declared_source_still_counts() -> None:
    assert _version("1.5", b"feed-a") != _version("1.5", b"feed-b")


def test_the_rows_identity_is_recorded_beside_the_sources() -> None:
    source = raw_source_from_bytes(SourceKind.IN_MEMORY, "rows", b"", RETRIEVED_AT, "x")
    metadata = ingest_rows(_rows("1.5"), _request(source=source)).dataset.metadata.metadata

    assert metadata["content_hash"] == source.content_hash
    assert len(metadata["rows_content_hash"]) == 64
