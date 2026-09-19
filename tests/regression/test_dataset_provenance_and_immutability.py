"""A dataset names the bytes it came from, and a version never changes.

Three promises, and the reasons each one is load-bearing:

**Provenance is preserved.** A dataset carries the source, schema, zone,
calendar, policy and every transformation applied to it. Without that, "we
backtested on the vendor file" is a claim nobody can check a year later.

**A version is immutable.** Cleaning derives a new dataset rather than editing
one. Before v3.1 it replaced the entry in state, so an evidence record naming a
dataset id stayed *verifiable* while the data behind that id had changed --
tamper-evidence that reported no tampering.

**Identity is derived, not minted.** The same bytes under the same
configuration produce the same version in every process. A ``uuid4`` would make
a dataset built in staging incomparable with the identical dataset built in
production, which is the property ``derive_asset_id`` exists to give
instruments and this gives datasets.

The join to a run
-----------------

The derived version *is* the ``MarketDataset.dataset_id``, so it flows through
``RunState.source_id`` into ``BacktestResult.dataset_id`` and is hashed into
``ValidationEvidence``. That is what makes a promotion name the exact bytes it
was measured on -- and it required no change to the evidence digest, which
``tests/regression/test_evidence_derives_dataset_identity.py`` keeps frozen.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from alphalab.api import to_market_dataset
from alphalab.data import (
    CleaningPolicy,
    CsvDialect,
    DataAssetClass,
    DatasetSchema,
    DataValidationError,
    DuplicatePolicy,
    IngestionRequest,
    InvalidRecordPolicy,
    MarketCalendar,
    MissingValuePolicy,
    OrderingPolicy,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    UniversalDataEngine,
    canonical_dataset_key,
    dataset_lineage,
    derive_dataset_version,
    derive_transformed_version,
    ingest_table,
    raw_source_from_bytes,
    read_delimited,
)
from alphalab.data.cleaning import TransformationRecord
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy

POLICY = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

CSV = (
    "symbol,timestamp,open,high,low,close,volume\n"
    "AAPL,2025-01-02T16:00:00Z,151.00,152.00,149.50,151.20,812345\n"
    "AAPL,2025-01-03T16:00:00Z,151.20,153.50,150.80,152.40,1045532\n"
)

RETRIEVED_AT = 1_726_000_000.0


def _request(name: str = "DS", content: str = CSV, **overrides: Any) -> IngestionRequest:
    fields: dict[str, object] = {
        "name": name,
        "source": raw_source_from_bytes(
            SourceKind.LOCAL_FILE,
            "fixtures/ds.csv",
            content.encode("utf-8"),
            RETRIEVED_AT,
            "text/csv",
            "utf-8",
        ),
        "frequency": TimeFrequency.DAILY,
        "asset_class": DataAssetClass.EQUITY,
        "cleaning_policy": POLICY,
        "price_basis": PriceBasis.RAW,
    }
    fields.update(overrides)
    return IngestionRequest(**fields)  # type: ignore[arg-type]


def _ingest(content: str = CSV, **overrides: Any):  # type: ignore[no-untyped-def]
    table = read_delimited(content, CsvDialect(","))
    return ingest_table(table, _request(content=content, **overrides))


# --------------------------------------------------------------------------- #
# A. Provenance is preserved
# --------------------------------------------------------------------------- #


def test_an_ingested_dataset_names_the_bytes_it_came_from() -> None:
    result = _ingest()
    provenance = result.dataset.require_provenance()

    assert provenance.source.kind is SourceKind.LOCAL_FILE
    assert provenance.source.location == "fixtures/ds.csv"
    assert provenance.source.retrieved_at == RETRIEVED_AT
    assert provenance.source.byte_count == len(CSV.encode("utf-8"))
    assert provenance.content_hash == provenance.source.content_hash


def test_provenance_records_every_decision_the_ingestion_was_given() -> None:
    calendar = MarketCalendar.continuous("CRYPTO", "UTC")
    result = _ingest(calendar=calendar)
    provenance = result.dataset.require_provenance()

    assert provenance.frequency is TimeFrequency.DAILY
    assert provenance.price_basis is PriceBasis.RAW
    assert provenance.cleaning_policy == POLICY
    assert provenance.calendar_id == "CRYPTO"
    assert provenance.timezone_name == "UTC"
    assert provenance.schema.data_type == "BAR"
    assert provenance.schema.bindings["CLOSE"] == "close"


def test_a_declared_zone_is_recorded_even_when_the_source_names_its_own_instants() -> None:
    """Two different jobs for one name, and both are honoured.

    An offset-bearing timestamp needs no zone to *parse*. Which local day it
    falls on still depends on one, so a caller may declare the zone the series
    is reported in, and it reaches provenance rather than being refused as
    redundant.
    """

    dataset = _ingest(timezone_name="America/New_York").dataset

    assert dataset.timezone == "America/New_York"
    assert dataset.require_provenance().timezone_name == "America/New_York"


def test_the_metadata_zone_and_the_provenance_zone_cannot_disagree() -> None:
    """Two stored copies of one fact is how they come to disagree.

    The pipeline is the only writer of both, and this is what holds it to
    writing the same value. ``Dataset.timezone`` reads through provenance, so
    the authoritative one is the one the identity was derived from.
    """

    calendar = MarketCalendar("XNSE", "Asia/Kolkata", dict.fromkeys(range(5), ()))
    dataset = _ingest(calendar=calendar).dataset

    assert dataset.metadata.timezone == dataset.require_provenance().timezone_name
    assert dataset.timezone == "Asia/Kolkata"


def test_a_dataset_built_from_rows_in_memory_records_no_provenance_rather_than_inventing_one() -> (
    None
):
    """ "Absent, not fabricated" -- the same rule a hand-driven run follows when
    it records ``source_id=None`` instead of an empty string."""

    from alphalab.data import DataAdapter

    legacy = UniversalDataEngine.load(
        DataAdapter.create_metadata("L-1", "memory", "EQUITY", "DAILY", 0.0, 1.0),
        [{"timestamp": 1.0, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}],
    )

    assert legacy.provenance is None
    assert legacy.dataset_version is None
    assert legacy.is_versioned is False

    with pytest.raises(DataValidationError) as error:
        legacy.require_provenance()
    assert "ingest_csv" in str(error.value), "the refusal says how to get one"


# --------------------------------------------------------------------------- #
# B. Identity is derived and reproducible
# --------------------------------------------------------------------------- #


def test_the_same_bytes_and_configuration_produce_the_same_version() -> None:
    assert _ingest().dataset_version == _ingest().dataset_version


def test_different_bytes_produce_a_different_version() -> None:
    altered = CSV.replace("151.20,812345", "151.25,812345")

    assert _ingest().dataset_version != _ingest(altered).dataset_version


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frequency", TimeFrequency.HOURLY),
        ("price_basis", PriceBasis.SPLIT_ADJUSTED),
        ("asset_class", DataAssetClass.CRYPTO),
    ],
)
def test_a_different_configuration_produces_a_different_version(field: str, value: object) -> None:
    """Everything that determines what the dataset *is* is in the identity.

    ``asset_class`` is the exception that proves the rule: it is recorded but
    does not change the records, so it is deliberately *not* hashed -- see the
    assertion below.
    """

    baseline = _ingest().dataset_version
    overrides: dict[str, Any] = {field: value}
    changed = _ingest(**overrides).dataset_version

    if field == "asset_class":
        assert changed == baseline, "classification does not change the data"
    else:
        assert changed != baseline


def test_the_retrieval_time_is_recorded_but_not_part_of_the_identity() -> None:
    """Re-downloading the same file tomorrow must not re-identify the dataset."""

    later = _request(
        content=CSV,
        source=raw_source_from_bytes(
            SourceKind.LOCAL_FILE,
            "fixtures/ds.csv",
            CSV.encode("utf-8"),
            RETRIEVED_AT + 86_400.0,
            "text/csv",
            "utf-8",
        ),
    )
    table = read_delimited(CSV, CsvDialect(","))
    tomorrow = ingest_table(table, later)

    assert tomorrow.dataset_version == _ingest().dataset_version
    assert tomorrow.dataset.require_provenance().source.retrieved_at == RETRIEVED_AT + 86_400.0


def test_the_canonical_key_is_scheme_tagged_and_starts_with_the_tag() -> None:
    """Following ``canonical_instrument_key``: a future change to how a dataset
    is identified bumps the tag and leaves every existing identity readable."""

    key = canonical_dataset_key(
        "DS",
        "deadbeef",
        DatasetSchema(("close",), "BAR", {"CLOSE": "close"}, "EPOCH_SECONDS", "UTC"),
        "UTC",
        None,
        TimeFrequency.DAILY,
        PriceBasis.RAW,
        POLICY,
        (),
        (),
    )

    assert key.splitlines()[0] == "alphalab.dataset.v1"
    assert "content=deadbeef" in key


def test_a_version_is_the_name_then_the_full_digest() -> None:
    version = _ingest(name="SAMPLE").dataset_version
    name, _, digest = version.partition("@")

    assert name == "SAMPLE"
    assert len(digest) == 64, "the full digest, as evidence_id is -- no collision argument needed"
    assert int(digest, 16) >= 0


def test_a_name_containing_the_separator_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        derive_dataset_version(
            "bad@name",
            "hash",
            DatasetSchema((), "BAR"),
            "UTC",
            None,
            TimeFrequency.DAILY,
            PriceBasis.RAW,
            POLICY,
            (),
            (),
        )

    assert "'@'" in str(error.value)


# --------------------------------------------------------------------------- #
# C. A version is immutable
# --------------------------------------------------------------------------- #


def test_cleaning_derives_a_new_version_and_leaves_the_original_untouched() -> None:
    """The realistic shape: ingest faithfully, clean later for a given study.

    The source is ingested under a policy that *keeps* an impossible bar, so
    the dataset in state reflects the file exactly. A later clean under a
    policy that drops it produces a second version, and both remain.
    """

    keep = replace(POLICY, invalid_records=InvalidRecordPolicy.KEEP)
    broken = CSV + "AAPL,2025-01-06T16:00:00Z,151.00,140.00,149.50,151.20,900000\n"
    dataset = _ingest(broken, cleaning_policy=keep).dataset

    assert len(dataset.records) == 3, "the impossible bar was ingested as it was written"

    state = UniversalDataEngine.ingest(UniversalDataEngine.initialize("E"), dataset, 1.0)
    cleaned = UniversalDataEngine.clean(state, dataset.dataset_id, POLICY, 2.0)

    original = cleaned.datasets[dataset.dataset_id]
    assert original.records == dataset.records, "byte for byte the version that was ingested"

    derived_id = next(name for name in cleaned.datasets if name != dataset.dataset_id)
    assert len(cleaned.datasets[derived_id].records) == 2, "the bad bar is gone from the child"
    assert dataset_lineage(cleaned, derived_id) == (derived_id, dataset.dataset_id)
    assert dataset_lineage(cleaned, dataset.dataset_id) == (dataset.dataset_id,), (
        "an ingested dataset is the root of its own lineage"
    )


def test_ingesting_a_version_that_is_already_held_is_refused() -> None:
    dataset = _ingest().dataset
    state = UniversalDataEngine.ingest(UniversalDataEngine.initialize("E"), dataset, 1.0)

    with pytest.raises(DataValidationError) as error:
        UniversalDataEngine.ingest(state, dataset, 2.0)

    assert "already exists" in str(error.value)


def test_measuring_quality_does_not_replace_the_dataset_object() -> None:
    """Quality is a measurement *of* a version, not part of it."""

    dataset = _ingest().dataset
    state = UniversalDataEngine.ingest(UniversalDataEngine.initialize("E"), dataset, 1.0)

    measured = UniversalDataEngine.quality(state, dataset.dataset_id, 2.0)

    assert measured.datasets[dataset.dataset_id] is state.datasets[dataset.dataset_id]
    assert measured.quality_reports[dataset.dataset_id].quality_score == 100.0


def test_a_derived_version_is_reproducible_from_its_parent_and_what_was_done() -> None:
    moves = (TransformationRecord("drop_duplicate", 1, "duplicate symbol+timestamp"),)

    derived = derive_transformed_version("DS@abc", moves)

    assert derived == derive_transformed_version("DS@abc", moves)
    assert derived != derive_transformed_version("DS@xyz", moves)
    assert derived.startswith("DS@")


def test_a_dataset_that_was_not_transformed_has_no_derived_version() -> None:
    with pytest.raises(DataValidationError) as error:
        derive_transformed_version("DS@abc", ())

    assert "was not transformed" in str(error.value)


# --------------------------------------------------------------------------- #
# D. The identity reaches a run
# --------------------------------------------------------------------------- #


def test_the_derived_version_becomes_the_market_dataset_identity() -> None:
    """This is the whole point of deriving it: the run names the exact bytes."""

    dataset = _ingest().dataset
    market = to_market_dataset(
        dataset, NormalizationPolicy(venue="XNYS", currency="USD", timeframe=TimeFrame.D1)
    )

    assert market.dataset_id == dataset.dataset_version
    assert len(market) == len(dataset.records)
    assert market.records[0].event_id.startswith(f"{dataset.dataset_version}-")


def test_a_dataset_with_no_provenance_cannot_reach_a_run() -> None:
    """A name invented here would make an unverifiable dataset indistinguishable
    from a verified one in the evidence that later cites it."""

    from alphalab.data import DataAdapter

    legacy = UniversalDataEngine.load(
        DataAdapter.create_metadata("L-1", "memory", "EQUITY", "DAILY", 0.0, 1.0),
        [{"timestamp": 1.0, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}],
    )

    with pytest.raises(DataValidationError):
        to_market_dataset(legacy, NormalizationPolicy(timeframe=TimeFrame.D1))


def test_the_dataset_is_frozen_in_the_ordinary_python_sense_too() -> None:
    dataset = _ingest().dataset

    with pytest.raises(AttributeError):
        dataset.records = ()

    # ``replace`` still produces a *new* object rather than editing this one.
    assert replace(dataset, records=()) is not dataset
    assert len(dataset.records) == 2
