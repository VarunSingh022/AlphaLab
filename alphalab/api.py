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

The v3.2 study path
-------------------

:func:`run_study` *is* the ``research(dataset)`` that could not be written
before, because v3.2 supplies the missing pieces: a dataset produces an
:class:`~alphalab.factor_library.observations.ObservationFrame`, a
:class:`~alphalab.factor_library.definition.FeatureDefinition` produces a
panel, and a panel measured against forward returns produces diagnostics with
their sample sizes attached. It joins ``alphalab.data``,
``alphalab.factor_library`` and ``alphalab.research``, which is exactly the
kind of join that belongs here and nowhere lower -- the v3.1 lesson that put
this module at the top of the graph rather than inside ``alphalab.data``.

It takes the dataset as an argument and **checks** it against the study's
recorded ``dataset_version`` rather than trusting either. A study that could
fetch its own data could fetch different data than the study beside it; one
that accepted any dataset handed to it would let a result name bytes it was
never measured on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from alphalab.analytics.engine import AnalyticsEngine
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.replay import ReplayBacktest
from alphalab.backtesting.state import BacktestResult, ReplayResult
from alphalab.conventions.lot import LotSpecification
from alphalab.conventions.market import MarketConvention
from alphalab.conventions.settlement import SettlementRule
from alphalab.conventions.tick import TickSchedule
from alphalab.data.assets import EquitySpec, FutureSpec, OptionSpec
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
from alphalab.factor_library.compute import compute_panel
from alphalab.factor_library.definition import FeatureField
from alphalab.factor_library.forward_returns import forward_returns
from alphalab.factor_library.observations import ObservationFrame, observations_from_dataset
from alphalab.factor_library.panel import FeaturePanel
from alphalab.futures.contract import FutureContract
from alphalab.market.normalization import (
    NormalizationPolicy,
    normalize_wire_bar,
    normalize_wire_quote,
)
from alphalab.market.record import MarketInput
from alphalab.options.contract import OptionContract
from alphalab.research.engine import ResearchEngine
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.signals import SignalDiagnostics, signal_diagnostics
from alphalab.research.study import ResearchStudy, StudyResult, build_result
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.run import RunConfig
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "AnalyticsEngine",
    "DataRequest",
    "DataSelection",
    "ResearchEngine",
    "ResearchStudy",
    "StudyResult",
    "backtest",
    "clean_dataset",
    "convention_from_spec",
    "future_contract_from_spec",
    "ingest_csv",
    "ingest_rows",
    "inspect_csv",
    "normalize_records",
    "observe",
    "option_contract_from_spec",
    "replay",
    "run_study",
    "select",
    "study_panels",
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


# --------------------------------------------------------------------------- #
# The v3.2 study path
# --------------------------------------------------------------------------- #


def observe(dataset: Dataset, source_field: FeatureField) -> ObservationFrame:
    """Read one field out of a dataset into the shape features compute over.

    A thin re-export of
    :func:`~alphalab.factor_library.observations.observations_from_dataset`,
    named here so an application importing this module has the whole study path
    in front of it. The frame carries the dataset's derived version, which is
    what every feature computed from it inherits.
    """

    return observations_from_dataset(dataset, source_field)


def _require_matching_dataset(study: ResearchStudy, dataset: Dataset) -> str:
    """The study and the dataset must name the same bytes, and are compared.

    Raises:
        ResearchValidationError: If the study names no dataset version, or if
            the dataset's version is not the one the study was written for.
    """

    declared = study.require_dataset()
    actual = dataset.dataset_version
    if actual is None:
        raise ResearchValidationError(
            f"Study {study.study_name!r} names dataset {declared!r} and the dataset supplied "
            "carries no provenance, so the two cannot be compared. Ingest through "
            "ingest_csv or ingest_rows with a RawSource."
        )
    if actual != declared:
        raise ResearchValidationError(
            f"Study {study.study_name!r} was written for dataset {declared!r} and was handed "
            f"{actual!r}. Running it anyway would produce a result whose recorded lineage "
            "names data it was not measured on -- the substitution ADR-0017 closed for "
            "evidence, closed here for studies."
        )
    return declared


def study_panels(study: ResearchStudy, dataset: Dataset) -> dict[str, FeaturePanel]:
    """Compute every feature the study declares, as panels keyed by feature version.

    Each field the study's features read is extracted from the dataset exactly
    once however many features read it, so ten features on one dataset's closes
    cost one pass over the records rather than ten.

    Raises:
        ResearchValidationError: If the dataset is not the one the study names.
        FactorInputError: For any reason
            :func:`~alphalab.factor_library.compute.compute_panel` raises --
            a symbol with too little history, a cross-section too small to
            measure, a field a record type does not carry.
    """

    _require_matching_dataset(study, dataset)

    frames: dict[FeatureField, ObservationFrame] = {}
    panels: dict[str, FeaturePanel] = {}
    for definition in study.features:
        field = definition.source_field
        if field not in frames:
            frames[field] = observations_from_dataset(dataset, field)
        panels[definition.feature_version] = compute_panel(definition, frames[field])
    return panels


def run_study(
    study: ResearchStudy,
    dataset: Dataset,
    price_field: FeatureField = FeatureField.CLOSE,
    buckets: int = 5,
    minimum_assets: int = 5,
    produced_at: float = 0.0,
) -> StudyResult:
    """Run a study end to end and record its result with full lineage.

    Every feature the study declares is computed into a panel, forward returns
    are measured at every horizon it declares, and each feature/horizon pair
    produces a :class:`~alphalab.research.signals.SignalDiagnostics`. The
    metrics are flattened as ``"<feature_id>.h<horizon>.<quantity>"`` so that a
    result mapping can be read, compared and hashed without a nested structure
    to walk.

    A diagnostic that could not be measured contributes a **finding** naming
    the feature, the horizon and the sample it had, and contributes no metric.
    That is the rule the whole release follows: an absent measurement is not a
    zero, and a study that measured nothing reports findings rather than an
    empty pass.

    ``produced_at`` is recorded on the result and is deliberately not hashed
    into its identity, so re-running the same study tomorrow reproduces the
    same ``result_id``.

    Raises:
        ResearchValidationError: If the dataset is not the one the study names,
            if the study declares no horizons -- there would be nothing to
            measure against -- or if no feature/horizon pair produced a single
            metric.
    """

    if not study.horizons:
        raise ResearchValidationError(
            f"Study {study.study_name!r} declares no forward horizons, so its features have "
            "nothing to be measured against. A study that only computes features is a "
            "feature computation; state at least one horizon to make it a study."
        )

    _require_matching_dataset(study, dataset)
    panels = study_panels(study, dataset)
    prices = observations_from_dataset(dataset, price_field)

    metrics: dict[str, float] = {}
    findings: list[str] = []
    lineage: dict[str, str] = {}

    for definition in study.features:
        panel = panels[definition.feature_version]
        lineage[definition.feature_version] = panel.lineage

        for horizon in study.horizons:
            prefix = f"{definition.feature_id}.h{horizon}"
            realized = forward_returns(prices, horizon)
            measured: SignalDiagnostics = signal_diagnostics(
                panel, realized, buckets, minimum_assets
            )

            if measured.rank_ic.mean_rank is None:
                findings.append(
                    f"{prefix}: no cross-section held the {minimum_assets} assets an "
                    f"information coefficient needs; {measured.rank_ic.instants_skipped} "
                    "instant(s) were skipped."
                )
            else:
                metrics[f"{prefix}.rank_ic"] = measured.rank_ic.mean_rank
                metrics[f"{prefix}.instants"] = float(measured.rank_ic.instants_measured)
                metrics[f"{prefix}.observations"] = float(measured.observations)
                if measured.rank_ic.mean_pearson is not None:
                    metrics[f"{prefix}.pearson_ic"] = measured.rank_ic.mean_pearson
                if measured.rank_ic.hit_rate is not None:
                    metrics[f"{prefix}.ic_hit_rate"] = measured.rank_ic.hit_rate

            if measured.spread is None:
                findings.append(
                    f"{prefix}: no cross-section held the {buckets} assets a quantile "
                    "profile needs, so no spread was measured."
                )
            else:
                metrics[f"{prefix}.spread"] = measured.spread
            if measured.monotonicity is not None:
                metrics[f"{prefix}.monotonicity"] = measured.monotonicity

    if not metrics:
        raise ResearchValidationError(
            f"Study {study.study_name!r} produced no measurable diagnostic over "
            f"{len(study.features)} feature(s) and {len(study.horizons)} horizon(s). The "
            f"findings were:\n  " + "\n  ".join(findings)
        )

    warnings = []
    if study.seed is None:
        warnings.append(
            "The study records no seed. Nothing in this run drew a random number, so the "
            "result reproduces; a robustness study built on it will need one."
        )

    return build_result(
        study=study,
        metrics=metrics,
        produced_at=produced_at,
        feature_lineage=lineage,
        findings=findings,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# The wire/domain contract join (v3.4)
# --------------------------------------------------------------------------- #
#
# ``alphalab.data.assets`` has said since v3.1 that a ``FutureSpec`` "says what a
# price series is about" while a ``FutureContract`` "opens a position in it", and
# that joining them "is v3.4's work". This is that join, and it lives here for
# the reason this module exists at all: ``alphalab.data`` must not import
# ``alphalab.futures`` or ``alphalab.portfolio``, because that would put a
# standalone engine on the ingestion path and close a package cycle. A joining
# layer belongs *above* the things it joins.
#
# The conversion is ``float`` -> ``Decimal`` through ``str``, which is the only
# rendering that does not carry a binary-float artefact into an exact decimal.
# ``Decimal(0.1)`` is 0.1000000000000000055511151231257827, and a tick size
# holding that value would put every price off its own grid.


def _exact(value: float, field: str, symbol: str) -> Decimal:
    """A wire ``float`` as the exact ``Decimal`` its printed form names."""

    converted = Decimal(str(value))
    if converted <= Decimal("0"):
        raise DataValidationError(f"{symbol}: {field} is {value!r}, which is not positive.")
    return converted


def future_contract_from_spec(spec: FutureSpec, currency: str | None = None) -> FutureContract:
    """Lift a wire :class:`~alphalab.data.assets.FutureSpec` into the domain.

    Args:
        spec: The provider's description of one contract month.
        currency: Settlement currency, when it differs from the one the spec
            carries. ``None`` uses ``spec.currency``, which is the ordinary
            case -- this is not a default currency but a way to say "the
            contract settles somewhere other than where its price series is
            labelled", which is a real and separate fact (ADR-0019's four
            currency roles).

    Raises:
        DataValidationError: If the spec names no ``contract_month``.
            :class:`~alphalab.futures.contract.FutureContract` requires one
            because ``futures_symbol`` is derived from it, and inventing one
            from the expiry would mint an identifier for a month the provider
            never stated.
    """

    if spec.contract_month is None:
        raise DataValidationError(
            f"{spec.symbol}: the spec states no contract_month, which a FutureContract needs "
            "to derive its symbol. Deriving one from the expiry would mint an identifier for "
            "a month the provider never named."
        )
    return FutureContract(
        underlying_asset_id=spec.root,
        contract_month=spec.contract_month,
        expiry=spec.expiry,
        multiplier=int(_exact(spec.multiplier, "multiplier", spec.symbol)),
        tick_size=_exact(spec.tick_size, "tick_size", spec.symbol),
        currency=spec.currency if currency is None else currency,
    )


def option_contract_from_spec(spec: OptionSpec) -> OptionContract:
    """Lift a wire :class:`~alphalab.data.assets.OptionSpec` into the domain.

    The premium currency does not travel: ``OptionContract`` carries none, and
    :func:`~alphalab.options.contract.open_option_position` takes it as a
    required argument (v2.17). ``spec.currency`` is what to pass it.
    """

    return OptionContract(
        underlying_asset_id=spec.underlying_symbol,
        strike=_exact(spec.strike, "strike", spec.symbol),
        expiry=spec.expiry,
        option_type=spec.option_type,
        style=spec.style,
        multiplier=int(_exact(spec.multiplier, "multiplier", spec.symbol)),
    )


def convention_from_spec(
    spec: EquitySpec | FutureSpec | OptionSpec,
    calendar_id: str,
    tick: TickSchedule,
    lot: LotSpecification,
    settlement: SettlementRule,
    settlement_currency: str | None = None,
) -> MarketConvention:
    """Build a :class:`~alphalab.conventions.market.MarketConvention` from a spec.

    A spec carries the conventions a data provider knows -- the exchange, the
    currency, and a multiplier for the two specs that have one. It does **not**
    carry the venue's calendar, its tick grid or its lot grid, because no price
    series states them: a tick schedule is the venue's and is tiered on most of
    them, and a lot size is revised by the exchange. Those are arguments, and a
    default for any of them would be one market's convention presented as a
    universal (ADR-0039).

    ``FutureSpec`` is the one spec carrying a ``tick_size`` of its own, and
    ``TickSchedule.flat(Decimal(str(spec.tick_size)))`` is how to turn it into
    the argument. It is not read here, because doing so would make this function
    silently produce a flat grid for a venue that publishes a tiered one.

    An :class:`~alphalab.data.assets.EquitySpec` has no multiplier: one unit is
    one share, so it takes ``Decimal("1")``, which is stated rather than
    assumed.

    Args:
        spec: The provider's description.
        calendar_id: The venue's calendar, by name. The calendar itself is
            :class:`alphalab.data.calendar.MarketCalendar` and is supplied
            separately; AlphaLab ships no holiday data.
        tick: The venue's price grid.
        lot: The venue's quantity grid.
        settlement: How a trade date becomes a settlement date.
        settlement_currency: Where cash moves, when it differs from where the
            price is quoted. ``None`` means the two are the same, which is the
            ordinary case and is a statement rather than a default.

    Raises:
        DataValidationError: If a numeric field is not positive.
    """

    multiplier = (
        Decimal("1")
        if isinstance(spec, EquitySpec)
        else _exact(spec.multiplier, "multiplier", spec.symbol)
    )
    return MarketConvention(
        venue=spec.exchange,
        calendar_id=calendar_id,
        quote_currency=spec.currency,
        settlement_currency=spec.currency if settlement_currency is None else settlement_currency,
        multiplier=multiplier,
        tick=tick,
        lot=lot,
        settlement=settlement,
    )
