"""The application-facing data and research API.

This is the module an application imports. It exists so that an external
platform can go from a file on disk to a backtest result with recorded lineage
without importing anything from deep inside AlphaLab. Every function here is
typed, explicit and stateless.

It is a **Python** API. There is no server, no CLI and no daemon here; AlphaLab
remains a library with no composition root, and a test asserts it.

Why it lives here and not in ``alphalab.data``
-----------------------------------------------

This module joins the data layer to the execution path, so it depends on both.
Putting it inside ``alphalab.data`` would give that package an edge to
``alphalab.market`` -- which already imports ``alphalab.data.feed`` for the wire
records -- and close a **package-level import cycle**. Zero package-level cycles
is a frozen invariant (``nowandfuture.md`` section 14, item 10), and a joining
layer belongs *above* the things it joins rather than inside one of them.

So ``alphalab.data`` keeps exactly two outward edges, to ``alphalab.common`` and
``alphalab.options`` (one leaf enum), and nothing in the package imports this
module. ``import alphalab.data`` therefore pulls in no part of the execution
path. Import this by name::

    from alphalab.api import ingest_csv, to_market_dataset

The join that makes lineage exact
----------------------------------

:func:`to_market_dataset` hands a dataset's derived version to
:meth:`~alphalab.backtesting.dataset.MarketDataset.of` as its ``dataset_id``.
That identifier is carried by the run as ``RunState.source_id``, read back as
``BacktestResult.dataset_id``, and hashed into ``ValidationEvidence`` by
``evidence_from_backtest``. So a promotion's evidence names the exact bytes,
schema, policy and transformations the measurement was taken on -- and none of
that required a change to the evidence digest, which stays frozen.

What is *not* wrapped, and why
-------------------------------

``research()`` and ``analyze()`` are re-exported rather than wrapped.
:class:`~alphalab.research.engine.ResearchEngine` and
:class:`~alphalab.analytics.engine.AnalyticsEngine` consume a *run's output* --
trades, returns, portfolio snapshots -- not market data. A ``research(dataset)``
wrapper would have to fabricate a payload from a dataset it cannot produce one
from, which would be a plausible-looking function that does not mean anything.
They are named here so an application imports one module; they are not
re-implemented here, so there is exactly one research engine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from alphalab.analytics.engine import AnalyticsEngine
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.backtesting.state import BacktestResult, ReplayResult
from alphalab.data.cleaning import CleaningPolicy, clean_records
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.csv_source import CsvDialect, RawTable, decode_text, read_delimited
from alphalab.data.dataset import Dataset
from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import Bar, CanonicalRecord, Quote
from alphalab.data.ingestion import IngestionRequest, IngestionResult, ingest_table
from alphalab.data.quality import DataQualityReport, evaluate_quality
from alphalab.data.schema import SchemaDetection, detect_schema
from alphalab.data.source import RawSource, raw_source_from_path
from alphalab.data.validation import ValidationFinding, validate_records
from alphalab.market.normalization import (
    NormalizationPolicy,
    normalize_wire_bar,
    normalize_wire_quote,
)
from alphalab.market.record import MarketInput
from alphalab.research.engine import ResearchEngine
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.run import RunConfig
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "AnalyticsEngine",
    "DataRequest",
    "DataSelection",
    "ResearchEngine",
    "backtest",
    "clean_dataset",
    "ingest_csv",
    "ingest_rows",
    "inspect_csv",
    "normalize_records",
    "replay",
    "select",
    "to_market_dataset",
    "validate_dataset",
]


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def inspect_csv(
    path: str | Path,
    retrieved_at: float,
    encoding: str = "utf-8",
    dialect: CsvDialect | None = None,
) -> tuple[RawSource, RawTable, SchemaDetection]:
    """Read a CSV and report what it contains, committing to nothing.

    The dry run. Nothing is coerced, validated, cleaned or versioned -- this
    answers "what would AlphaLab make of this file?" so that a caller can
    resolve an ambiguity before ingesting rather than after. The returned
    detection carries the bindings, the ambiguities, the unresolved roles and
    every assumption; see :class:`~alphalab.data.schema.SchemaDetection`. The
    source is returned alongside so the content hash is known before anything
    commits to it.
    """

    source, payload = raw_source_from_path(path, retrieved_at, "text/csv", encoding)
    table = read_delimited(decode_text(payload, encoding), dialect)
    return source, table, detect_schema(table)


def ingest_csv(
    path: str | Path,
    request: IngestionRequest,
    encoding: str = "utf-8",
    dialect: CsvDialect | None = None,
) -> IngestionResult:
    """Read a CSV from disk into a canonical, versioned dataset.

    ``request.source`` is replaced with provenance describing the file that was
    actually read, so a caller cannot accidentally record a source that does not
    match the bytes ingested.

    Args:
        path: The file to read.
        request: Every decision the ingestion may not make on its own.
        encoding: Character encoding. Undecodable bytes are refused rather
            than replaced.
        dialect: How to read the file. ``None`` detects the delimiter, and
            refuses when detection is ambiguous.
    """

    source, payload = raw_source_from_path(path, request.source.retrieved_at, "text/csv", encoding)
    table = read_delimited(decode_text(payload, encoding), dialect)
    from dataclasses import replace as _replace

    return ingest_table(table, _replace(request, source=source))


def ingest_rows(
    rows: Sequence[Mapping[str, object]],
    request: IngestionRequest,
) -> IngestionResult:
    """Ingest rows already in memory, through the same pipeline as a file.

    The rows become a :class:`~alphalab.data.csv_source.RawTable` through
    :meth:`~alphalab.data.csv_source.RawTable.from_rows` and take the same path
    a file's rows take from there -- the same detection, the same coercion, the
    same findings, the same transformations and the same identity. Two paths
    that agreed only approximately would make a dataset's identity depend on
    how it was delivered.

    This is also how JSON is ingested: parse it with the standard library and
    hand the rows here. AlphaLab ships no JSON reader because it needs none.

    ``request.source`` is used as given -- the caller performed whatever
    retrieval produced these rows and is the only thing that knows what it was.
    Use :func:`~alphalab.data.source.raw_source_from_bytes` with
    :attr:`~alphalab.data.source.SourceKind.IN_MEMORY` for rows with no
    external origin.

    Unlike :func:`~alphalab.data.parser.parse_raw_rows`, this **reports** rather
    than refuses: a row that cannot be translated is returned in the result's
    :class:`~alphalab.data.quality.DataQualityReport` with its reason, and the
    rows that could be are ingested.
    """

    return ingest_table(RawTable.from_rows(rows), request)


# --------------------------------------------------------------------------- #
# The stages, individually
# --------------------------------------------------------------------------- #


def validate_dataset(dataset: Dataset) -> tuple[ValidationFinding, ...]:
    """Re-check a dataset's records, returning every finding.

    Useful on a dataset built by a path that recorded no quality report, and as
    a way to confirm that a cleaned version is in fact clean.
    """

    return validate_records(dataset.records, dataset.metadata.frequency)


def clean_dataset(
    dataset: Dataset, policy: CleaningPolicy
) -> tuple[tuple[CanonicalRecord, ...], DataQualityReport]:
    """Clean a dataset's records under ``policy`` and report what was found.

    Returns the cleaned records and the quality report of the data **as it was
    found**, before cleaning. Use
    :meth:`~alphalab.data.engine.UniversalDataEngine.clean` to derive a new
    dataset version in engine state instead.
    """

    findings = validate_records(dataset.records, dataset.metadata.frequency)
    report = evaluate_quality(
        dataset_id=dataset.dataset_id,
        records=dataset.records,
        row_count=len(dataset.records),
        rejected=(),
        findings=findings,
    )
    return clean_records(dataset.records, policy, findings).records, report


def normalize_records(
    records: Sequence[CanonicalRecord], policy: NormalizationPolicy
) -> tuple[MarketInput, ...]:
    """Lift wire records into canonical domain records.

    Delegates to :mod:`alphalab.market.normalization`, which is the one wire ->
    domain boundary in AlphaLab: ``float`` becomes ``Decimal`` through ``str``,
    a provider ``symbol`` becomes an ``asset_id``, and venue, currency and
    timeframe are supplied by the policy because the wire shape has no room for
    them. Nothing here re-implements any of that.

    ``policy`` is required. :data:`~alphalab.market.normalization.DEFAULT_POLICY`
    names the venue ``"UNKNOWN"`` and resolves no identities, and defaulting to
    it here would quietly produce records that cannot reach a fill.

    Raises:
        DataValidationError: If a record is neither a wire bar nor a wire quote.
    """

    lifted: list[MarketInput] = []
    for record in records:
        if isinstance(record, Bar):
            lifted.append(normalize_wire_bar(record, policy))
        elif isinstance(record, Quote):
            lifted.append(normalize_wire_quote(record, policy))
        else:
            raise DataValidationError(
                f"{type(record).__name__} has no canonical domain form on the execution "
                "path; only wire bars and wire quotes can be normalized into market inputs."
            )
    return tuple(lifted)


# --------------------------------------------------------------------------- #
# Research data access
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DataRequest:
    """What a research run is asking for, stated rather than implied.

    Every field narrows a dataset the caller already holds. There is no field
    naming a dataset to go and fetch, which is deliberate: a research function
    that could fetch its own data could fetch *different* data than the one
    beside it, and no result would be comparable. The dataset is passed in.

    Attributes:
        symbols: Instruments to keep. Empty means every instrument present.
        start: Inclusive lower bound on timestamps, or ``None`` for unbounded.
        end: Exclusive upper bound on timestamps, or ``None`` for unbounded.
        price_basis: The basis the caller requires. When the dataset is on a
            different one, :func:`select` refuses rather than converting --
            converting would need corporate actions nobody supplied.
        as_of: Point-in-time cut-off. Records timestamped after it are
            excluded, which is the look-ahead guard for a study that must only
            see what was knowable at that instant.
    """

    symbols: tuple[str, ...] = ()
    start: float | None = None
    end: float | None = None
    price_basis: PriceBasis | None = None
    as_of: float | None = None


@dataclass(frozen=True, slots=True)
class DataSelection:
    """Records matching a request, and the exact dataset they came from."""

    records: tuple[CanonicalRecord, ...]
    dataset_version: str | None
    request: DataRequest

    def __len__(self) -> int:
        return len(self.records)


def select(dataset: Dataset, request: DataRequest) -> DataSelection:
    """Narrow a dataset to what a research run asked for.

    The selection names the dataset version it came from, so a result can
    always be traced back to the exact data behind it.

    Raises:
        DataValidationError: If the request asks for a price basis the dataset
            is not on.
    """

    if request.price_basis is not None:
        provenance = dataset.require_provenance()
        if provenance.price_basis is not request.price_basis:
            raise DataValidationError(
                f"{dataset.dataset_id} is on PriceBasis.{provenance.price_basis.name} and the "
                f"request asks for PriceBasis.{request.price_basis.name}. Converting between "
                "them needs the corporate actions for this instrument, which a selection does "
                "not have; ingest the dataset on the basis you need."
            )

    wanted = set(request.symbols)
    records = tuple(
        record
        for record in dataset.records
        if (not wanted or record.symbol in wanted)
        and (request.start is None or record.timestamp >= request.start)
        and (request.end is None or record.timestamp < request.end)
        and (request.as_of is None or record.timestamp <= request.as_of)
    )
    return DataSelection(records=records, dataset_version=dataset.dataset_version, request=request)


# --------------------------------------------------------------------------- #
# The join to the execution path
# --------------------------------------------------------------------------- #


def to_market_dataset(dataset: Dataset, policy: NormalizationPolicy) -> MarketDataset:
    """Turn a canonical dataset into the ordered stream a run consumes.

    The dataset's **derived version** becomes the ``MarketDataset.dataset_id``,
    which is what makes a run's lineage exact rather than a label somebody
    typed. A dataset with no provenance is refused: it has no version to carry,
    and a name invented here would make an unverifiable dataset indistinguishable
    from a verified one in the evidence that later cites it.

    Raises:
        DataValidationError: If the dataset records no provenance.
        DatasetValidationError: If the records are not ordered, not unique, or
            empty -- ``MarketDataset`` validates on construction.
    """

    version = dataset.require_provenance().dataset_version
    inputs = normalize_records(dataset.records, policy)
    return MarketDataset.of(version, sorted(inputs, key=lambda item: item.timestamp))


def backtest(
    config: RunConfig,
    dataset: Dataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    policy: NormalizationPolicy,
) -> BacktestResult:
    """Run a backtest over a canonical dataset, carrying its identity into the result.

    ``result.dataset_id`` is the dataset's derived version, so the run names the
    exact data it consumed.
    """

    return BacktestEngine.run(
        config, to_market_dataset(dataset, policy), strategy_state, context_factory
    )


def replay(
    config: RunConfig,
    dataset: Dataset,
    strategy_state: StrategyRuntimeState,
    context_factory: ContextFactory,
    policy: NormalizationPolicy,
) -> ReplayResult:
    """Replay a canonical dataset through the execution path, with the same identity."""

    return ReplayBacktest.run(
        config, to_market_dataset(dataset, policy), strategy_state, context_factory
    )
