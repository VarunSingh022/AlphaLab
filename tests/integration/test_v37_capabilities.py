"""The five v3.7 capabilities, joined through the real packages beneath them.

Nothing here is mocked. Rows are ingested with the bytes they stand for; the
price panel is a versioned dataset on New York's real trading days, stamped at
the close; events, sentiment and fundamentals are versioned point-in-time sets
read by the application API; research runs through the v3.2 feature engine; an
adaptive strategy runs through the execution path; and the v3.6 contracts --
fingerprint, manifest, certification -- record all of it.

.. code-block:: text

    rows --> price dataset ------------------------------+
    rows --> events / sentiment / fundamentals (PIT sets)|
                 |                                        |
                 v                                        v
           point-in-time views --> knowledge frames --> features, IC
                 |                        |
                 |                        +--> fundamental factors
                 +--> event study (anchored at the tradable session)
           volatility feature --> regime detection --> conditioned diagnostics
                 |
                 v
           research study (inputs name every set) --> manifest
                                                        |
    adaptive strategy --> backtest / replay --> run manifest + rerun
                 |                                      |
                 +--> research replay (same state)      v
                                          fingerprint (adaptive settings)
                                                        |
                                                        v
                                                  certification

What is looked for is the class of defect no unit test can see: a set, a frame
or a state whose identity changes between hops; information visible before it
was knowable anywhere along the path; an adaptive state that the run and the
research replay disagree about; and a reproducibility record that forgets a
point-in-time input.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from alphalab.alt_data import (
    EARNINGS_PER_SHARE,
    Aggregation,
    ExternalObservation,
    FundamentalInput,
    FundamentalObservation,
    InformationEvent,
    ObservationSet,
    SessionTiming,
    StatementType,
    VintagePolicy,
)
from alphalab.api import (
    AvailabilityAfterLag,
    AvailabilityAtNextOpen,
    AvailabilityFromColumn,
    EventColumns,
    FundamentalColumns,
    ObservationColumns,
    TimestampReading,
    backtest,
    ingest_events,
    ingest_fundamentals,
    ingest_observations,
    ingest_rows,
    observation_source,
    replay,
    to_market_dataset,
)
from alphalab.backtesting.state import BacktestResult
from alphalab.common import VisibilityRule
from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.dataset import Dataset
from alphalab.data.ingestion import IngestionRequest
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import DateOnlyPolicy, TimeFrequency, TimestampFormat
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    ObservationFrame,
    ResearchClock,
    SnapshotSpecification,
    align_prices,
    compute_feature,
    compute_panel,
    compute_value,
    divide_frames,
    forward_returns,
    fundamental_frame,
    fundamental_snapshot_as_of,
    information_coefficient,
    observation_frame,
    observations_from_dataset,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    BrokerRequirements,
    CapitalPolicy,
    CertificationEvidence,
    CertificationProperty,
    CertificationStatus,
    CodeIdentity,
    ExternalInput,
    LifecycleState,
    MarketRequirements,
    RerunOutcome,
    RuntimeRequirements,
    Tolerance,
    assess_adaptive_replay,
    assess_reproducibility,
    certify_strategy,
    dataset_assumption_from,
    external_requirements,
    fingerprint_for_version,
    manifest_for_run,
    manifest_for_study,
    register_strategy,
    research_configuration_with_adaptive,
    running_engine,
    source_digest,
    specification_for_version,
    verify_manifest,
)
from alphalab.lifecycle.strategy_version import get_strategy_version
from alphalab.market.bar import Bar as MarketBar
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.persistence.serializer import serialize
from alphalab.research import (
    AbnormalReturnModel,
    EventStudyDefinition,
    EventStudyResult,
    EventWindow,
    RegimeDefinition,
    ResearchStudy,
    StudyResult,
    ThresholdRule,
    build_result,
    classify_regimes,
    conditional_diagnostics,
    event_study,
    event_study_metrics,
    regime_profile,
    regime_series_from_features,
)
from alphalab.runtime.run import ExecutionMode, RunConfig
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveDecision,
    AdaptiveObservation,
    AdaptiveStrategy,
    DecisionTiming,
    TrailingZScoreRule,
    UpdateCadence,
    initial_state,
    observation_stream,
    replay_updates,
)
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.studio.strategy import StrategyDefinition
from tests.integration.harness import (
    START_CASH,
    context_factory,
    pipeline_config,
    running_strategy_state,
)
from tests.unit.lifecycle.evidence_harness import CLEANING, csv_payload

STRATEGY_ID = "V37-ADAPTIVE"
PROVIDER = "v37-vendor"
SEED = 370_700
SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
TRADED = "AAA"

#: New York's regular session, with Good Friday 2024 declared as the one holiday
#: in the window. AlphaLab ships no holidays; the caller declares them.
CALENDAR = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
    holidays=frozenset({date(2024, 3, 29)}),
)
SESSIONS = CALENDAR.sessions_between(date(2024, 3, 1), date(2024, 5, 31))
ZONE = CALENDAR.local_datetime(0.0).tzinfo


def ny(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=ZONE).timestamp()


def _close(day: date) -> float:
    bounds = CALENDAR.session_bounds(day)
    assert bounds is not None
    return bounds[1]


INSTRUMENTS = tuple(
    InstrumentRecord(symbol, AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: symbol})
    for symbol in SYMBOLS
)
ASSETS = {record.symbol: record.asset_id for record in INSTRUMENTS}
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), INSTRUMENTS)
NORMALIZATION = NormalizationPolicy(
    venue="XNYS", currency="USD", timeframe=TimeFrame.D1, identity=REGISTRY, provider=PROVIDER
)

#: Each earnings release: subject, announcement (New York wall clock), delivery
#: (empty when the feed recorded none), actual, consensus, and the price move on
#: the session the news could first be traded.
EARNINGS = (
    ("AAA", "2024-04-16 16:05", "2024-04-16 16:06", "1.60", "1.40", 0.06),
    ("BBB", "2024-04-18 07:30", "2024-04-18 07:31", "0.80", "0.95", -0.05),
    ("CCC", "2024-04-23 16:10", "2024-04-23 16:11", "2.10", "2.00", 0.03),
    ("DDD", "2024-04-25 12:00", "2024-04-25 12:00", "1.05", "1.20", -0.04),
    ("EEE", "2024-04-30 16:02", "", "0.55", "0.50", 0.02),
    ("FFF", "2024-05-02 06:45", "2024-05-02 06:45", "3.30", "3.00", 0.05),
)


def _reaction_session(delivered: str) -> date | None:
    if not delivered:
        return None
    placement = CALENDAR.next_open(ny(delivered))
    assert placement is not None
    return CALENDAR.trading_day_of(placement)


@pytest.fixture(scope="module")
def prices() -> Dataset:
    """Six names on 64 trading days, closes at 16:00 New York, with each
    earnings reaction on the session its news could first be traded."""

    jumps = {
        subject: (_reaction_session(delivered), move)
        for subject, _, delivered, _, _, move in EARNINGS
    }
    rows = []
    for position, symbol in enumerate(SYMBOLS):
        price = Decimal(100 + 10 * position)
        for day in SESSIONS:
            drift = Decimal(((position * 7 + day.toordinal() * 3) % 11) - 5) / Decimal(1000)
            jump_day, move = jumps[symbol]
            reaction = Decimal(str(move)) if day == jump_day else Decimal(0)
            price = (price * (Decimal(1) + drift + reaction)).quantize(Decimal("0.001"))
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": f"{day.isoformat()} 16:00:00",
                    "open": str(price),
                    "high": str(price * Decimal("1.01")),
                    "low": str(price * Decimal("0.99")),
                    "close": str(price),
                    "volume": 50_000 + position,
                }
            )
    request = IngestionRequest(
        name="V37-PANEL",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "v37-integration", csv_payload(rows), 1_717_000_000.0, "text/csv"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="America/New_York",
    )
    dataset = ingest_rows(rows, request).dataset
    assert dataset.is_versioned
    return dataset


AWARE = TimestampReading(TimestampFormat.ISO_8601_NAIVE, "America/New_York", None)


@pytest.fixture(scope="module")
def events() -> ObservationSet[InformationEvent]:
    rows = [
        {
            "ticker": subject,
            "kind": "earnings.release",
            "announced": announced,
            "delivered": delivered,
            "actual": actual,
            "consensus": consensus,
        }
        for subject, announced, delivered, actual, consensus, _ in EARNINGS
    ]
    rows.append({**rows[0], "delivered": "2024-04-17 11:00", "actual": "1.62"})
    source = observation_source(
        raw_source_from_bytes(
            SourceKind.VENDOR_FILE, "earnings.csv", csv_payload(rows), 1_717_000_100.0, "text/csv"
        ),
        "calendar.earnings",
        "2024.2",
    )
    columns = EventColumns(
        subject="ticker",
        event_type="kind",
        observed_at="announced",
        availability=AvailabilityFromColumn("delivered"),
        measurements=("actual", "consensus"),
        attributes=(),
        effective_at=None,
        revision="revision",
        ingested_at_retrieval=False,
    )
    rows = [{**row, "revision": "0"} for row in rows[:-1]] + [{**rows[-1], "revision": "1"}]
    ingestion = ingest_events(rows, "earnings", source, columns, AWARE)
    assert ingestion.rejected == ()
    return ingestion.observations


@pytest.fixture(scope="module")
def sentiment() -> ObservationSet[ExternalObservation]:
    """A daily news score per name, observed at 18:00 and delivered an hour later."""

    rows = []
    for position, symbol in enumerate(SYMBOLS):
        for index, day in enumerate(SESSIONS):
            score = ((position * 5 + index * 3) % 9 - 4) / 4
            rows.append(
                {
                    "ticker": symbol,
                    "metric": "news_score",
                    "value": f"{score:.2f}",
                    "observed": f"{day.isoformat()} 18:00",
                }
            )
    source = observation_source(
        raw_source_from_bytes(
            SourceKind.VENDOR_FILE, "sentiment.csv", csv_payload(rows), 1_717_000_200.0, "text/csv"
        ),
        "news.sentiment",
        "3.1",
    )
    columns = ObservationColumns(
        category="sentiment",
        unit="score",
        subject="ticker",
        metric="metric",
        value="value",
        observed_at="observed",
        availability=AvailabilityAfterLag(3600.0),
        effective_at=None,
        revision=None,
        period=None,
        ingested_at_retrieval=False,
    )
    ingestion = ingest_observations(rows, "sentiment", source, columns, AWARE)
    assert ingestion.rejected == ()
    return ingestion.observations


QUARTERS = (
    (2023, 1, "2023-01-01", "2023-03-31", "2023-04-27"),
    (2023, 2, "2023-04-01", "2023-06-30", "2023-07-27"),
    (2023, 3, "2023-07-01", "2023-09-30", "2023-10-26"),
    (2023, 4, "2023-10-01", "2023-12-31", "2024-02-01"),
    (2024, 1, "2024-01-01", "2024-03-31", "2024-04-25"),
)
RESTATED_FILED = "2024-05-10"


@pytest.fixture(scope="module")
def fundamentals() -> ObservationSet[FundamentalObservation]:
    rows = []
    for position, symbol in enumerate(SYMBOLS):
        for index, (fy, fq, start, end, filed) in enumerate(QUARTERS):
            for item, statement, value, unit in (
                (
                    "eps_diluted",
                    "INCOME_STATEMENT",
                    f"{0.5 + 0.1 * position + 0.05 * index:.2f}",
                    "USD/share",
                ),
                ("total_equity", "BALANCE_SHEET", str(1000 + 100 * position + index), "USD"),
                ("shares_diluted", "BALANCE_SHEET", "100", "shares"),
                ("dps", "INCOME_STATEMENT", "0.10", "USD/share"),
            ):
                rows.append(
                    {
                        "issuer": symbol,
                        "statement": statement,
                        "item": item,
                        "fy": str(fy),
                        "fq": str(fq),
                        "start": start,
                        "end": end,
                        "value": value,
                        "unit": unit,
                        "filed": filed,
                        "restatement": "0",
                    }
                )
    rows.append(
        {
            "issuer": TRADED,
            "statement": "INCOME_STATEMENT",
            "item": "eps_diluted",
            "fy": "2023",
            "fq": "4",
            "start": "2023-10-01",
            "end": "2023-12-31",
            "value": "0.20",
            "unit": "USD/share",
            "filed": RESTATED_FILED,
            "restatement": "1",
        }
    )
    source = observation_source(
        raw_source_from_bytes(
            SourceKind.VENDOR_FILE, "filings.csv", csv_payload(rows), 1_717_000_300.0, "text/csv"
        ),
        "filings.quarterly",
        None,
    )
    columns = FundamentalColumns(
        subject="issuer",
        statement="statement",
        line_item="item",
        fiscal_year="fy",
        fiscal_quarter="fq",
        period_start="start",
        period_end="end",
        value="value",
        unit="unit",
        published_at="filed",
        availability=AvailabilityAtNextOpen("filed", CALENDAR),
        revision="restatement",
        ingested_at_retrieval=False,
    )
    reading = TimestampReading(
        TimestampFormat.DATE_ONLY, "America/New_York", DateOnlyPolicy.END_OF_DAY
    )
    ingestion = ingest_fundamentals(rows, "filings", source, columns, reading)
    assert ingestion.rejected == ()
    return ingestion.observations


# --------------------------------------------------------------------------- #
# Research
# --------------------------------------------------------------------------- #


PUB = VisibilityRule.PUBLICATION
EVENT_STUDY = EventStudyDefinition(
    name="earnings-reaction",
    event_types=("earnings.release",),
    window=EventWindow(before=3, after=3, estimation=10, gap=2),
    model=AbnormalReturnModel.MEAN_ADJUSTED,
    benchmark=None,
    visibility=PUB,
)


@pytest.fixture(scope="module")
def reaction(events: ObservationSet[InformationEvent], prices: Dataset) -> EventStudyResult:
    return event_study(
        events, observations_from_dataset(prices, FeatureField.CLOSE), CALENDAR, EVENT_STUDY
    )


def test_events_are_anchored_where_they_could_first_be_traded(reaction: EventStudyResult) -> None:
    by_subject = {outcome.subject: outcome for outcome in reaction.outcomes}

    assert by_subject["AAA"].timing is SessionTiming.AFTER_CLOSE
    assert by_subject["AAA"].anchor == _close(date(2024, 4, 17))
    assert by_subject["BBB"].timing is SessionTiming.BEFORE_OPEN
    assert by_subject["BBB"].anchor == _close(date(2024, 4, 18))
    assert by_subject["DDD"].timing is SessionTiming.IN_SESSION
    for outcome in reaction.outcomes:
        move = next(m for s, *_, m in EARNINGS if s == outcome.subject)
        assert outcome.abnormal_returns[3] == pytest.approx(move, abs=0.012)
        assert all(abs(value) < 0.012 for value in outcome.abnormal_returns[:3])


def test_an_unknown_delivery_and_a_correction_are_excluded_by_name(
    reaction: EventStudyResult,
) -> None:
    reasons = sorted(entry.reason for entry in reaction.excluded)

    assert len(reaction.outcomes) == 5
    assert any("never established" in reason for reason in reasons)
    assert any("already anchored" in reason for reason in reasons)
    assert reaction.verify()


@pytest.fixture(scope="module")
def closes(prices: Dataset) -> ObservationFrame:
    return observations_from_dataset(prices, FeatureField.CLOSE)


@pytest.fixture(scope="module")
def clock(closes: ObservationFrame) -> ResearchClock:
    """The price frame's own clock: every knowledge frame below is sampled on it."""

    return ResearchClock.of_frame(closes)


def test_knowledge_frames_feed_the_feature_engine_with_lineage(
    sentiment: ObservationSet[ExternalObservation], prices: Dataset, clock: ResearchClock
) -> None:
    knowledge = observation_frame(
        sentiment.view(PUB),
        "sentiment",
        "news_score",
        clock,
        SYMBOLS,
        max_age_seconds=4 * 86_400.0,
        policy=VintagePolicy.AS_KNOWN,
    )
    rank = FeatureDefinition(
        "news_rank",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.OBSERVATION,
        scope=FeatureScope.CROSS_SECTIONAL,
    )
    panel = compute_panel(rank, knowledge.frame)
    aligned = align_prices(observations_from_dataset(prices, FeatureField.CLOSE), knowledge)
    returns = forward_returns(aligned, 1)
    ic = information_coefficient(panel, returns, minimum_assets=5)

    assert panel.dataset_version == knowledge.frame_id == returns.dataset_version
    assert knowledge.clock.dataset_version == prices.require_provenance().dataset_version
    assert ic.instants_measured > 10 and ic.is_measurable
    # The score observed at 18:00 is delivered at 19:00, after the 16:00 close:
    # the first close it can be sampled at is the next session's.
    first_known = knowledge.frame.series["AAA"].timestamps[0]
    assert first_known == _close(SESSIONS[1])


def test_fundamentals_are_read_as_knowable_and_restated_only_once_published(
    fundamentals: ObservationSet[FundamentalObservation], prices: Dataset, clock: ResearchClock
) -> None:
    spec = FundamentalInput(
        EARNINGS_PER_SHARE,
        StatementType.INCOME_STATEMENT,
        "eps_diluted",
        Aggregation.TRAILING_TWELVE_MONTHS,
    )
    view = fundamentals.view(PUB)
    eps_frame = fundamental_frame(
        view,
        spec,
        clock,
        SYMBOLS,
        max_age_seconds=None,
        policy=VintagePolicy.AS_KNOWN,
    )
    series = dict(
        zip(
            eps_frame.frame.series[TRADED].timestamps,
            eps_frame.frame.series[TRADED].values,
            strict=True,
        )
    )

    # FY2023Q1..Q4 until FY2024Q1 is filed on 2024-04-25 (tradable at the next open).
    assert series[_close(date(2024, 4, 25))] == pytest.approx(0.5 + 0.55 + 0.6 + 0.65)
    assert series[_close(date(2024, 4, 26))] == pytest.approx(0.55 + 0.6 + 0.65 + 0.7)
    # The FY2023Q4 restatement (0.65 -> 0.20) is filed 2024-05-10: invisible until the 13th.
    assert series[_close(date(2024, 5, 10))] == pytest.approx(0.55 + 0.6 + 0.65 + 0.7)
    assert series[_close(date(2024, 5, 13))] == pytest.approx(0.55 + 0.6 + 0.20 + 0.7)

    aligned = align_prices(observations_from_dataset(prices, FeatureField.CLOSE), eps_frame)
    earnings_yield = divide_frames(eps_frame.frame, aligned, name="earnings_yield")
    assert earnings_yield.undefined == 0
    assert set(earnings_yield.frame.series) == set(SYMBOLS)
    assert earnings_yield.frame.dataset_version == eps_frame.frame_id

    snapshot_spec = SnapshotSpecification(
        earnings_per_share=spec,
        book_equity=FundamentalInput(
            "book_equity", StatementType.BALANCE_SHEET, "total_equity", Aggregation.LATEST
        ),
        shares_outstanding=FundamentalInput(
            "shares_outstanding", StatementType.BALANCE_SHEET, "shares_diluted", Aggregation.LATEST
        ),
        dividends_per_share=FundamentalInput(
            "dividends_per_share",
            StatementType.INCOME_STATEMENT,
            "dps",
            Aggregation.TRAILING_TWELVE_MONTHS,
        ),
    )
    as_of = _close(date(2024, 5, 1))
    snapshot = fundamental_snapshot_as_of(
        view,
        TRADED,
        as_of,
        Decimal("100"),
        "USD/share",
        as_of,
        VintagePolicy.AS_KNOWN,
        snapshot_spec,
    )
    assert compute_value(snapshot, "earnings_yield", 1, as_of).value == pytest.approx(2.5 / 100)


@pytest.fixture(scope="module")
def regimes(prices: Dataset) -> Any:
    frame = observations_from_dataset(prices, FeatureField.CLOSE)
    volatility = FeatureDefinition(
        "vol_regime",
        FeatureKind.VOLATILITY_REGIME,
        FeatureField.CLOSE,
        window=5,
        parameters={"long_window": 15},
    )
    (series,) = [row for row in compute_feature(volatility, frame) if row.symbol == TRADED]
    model = RegimeDefinition(
        "aaa-volatility", ThresholdRule("vol", (1.0,), ("calm", "turbulent")), 2
    )
    return model, series, regime_series_from_features(model, {"vol": series})


def test_regimes_are_detected_from_a_feature_and_condition_a_diagnostic(
    regimes: Any,
    sentiment: ObservationSet[ExternalObservation],
    prices: Dataset,
    clock: ResearchClock,
) -> None:
    model, feature, detected = regimes
    knowledge = observation_frame(
        sentiment.view(PUB),
        "sentiment",
        "news_score",
        clock,
        SYMBOLS,
        max_age_seconds=None,
        policy=VintagePolicy.AS_KNOWN,
    )
    signal = compute_panel(
        FeatureDefinition(
            "news_rank",
            FeatureKind.CROSS_SECTIONAL_RANK,
            FeatureField.OBSERVATION,
            scope=FeatureScope.CROSS_SECTIONAL,
        ),
        knowledge.frame,
    )
    returns = forward_returns(
        align_prices(observations_from_dataset(prices, FeatureField.CLOSE), knowledge), 1
    )

    conditioned = conditional_diagnostics(
        signal, returns, detected.labels_by_instant(), buckets=2, minimum_assets=5
    )
    profile = regime_profile(detected, None)

    assert detected.input_id is not None, "the regime names the feature lineage it read"
    assert set(conditioned) <= set(model.labels)
    assert profile.classified + profile.unclassified == len(feature)
    split = len(feature) // 2
    head = classify_regimes(
        model,
        {"vol": list(zip(feature.timestamps[:split], feature.values[:split], strict=True))},
        input_id=detected.input_id,
    )
    tail = classify_regimes(
        model,
        {"vol": list(zip(feature.timestamps[split:], feature.values[split:], strict=True))},
        input_id=detected.input_id,
        initial=head.final_state,
    )
    assert head.regimes + tail.regimes == detected.regimes
    assert tail.final_state == detected.final_state


@pytest.fixture(scope="module")
def study_result(
    prices: Dataset,
    events: ObservationSet[InformationEvent],
    sentiment: ObservationSet[ExternalObservation],
    fundamentals: ObservationSet[FundamentalObservation],
    reaction: EventStudyResult,
    regimes: Any,
) -> StudyResult:
    model, _, detected = regimes
    study = ResearchStudy(
        study_name="v37-point-in-time",
        dataset_version=prices.require_provenance().dataset_version,
        universe=SYMBOLS,
        features=(
            FeatureDefinition(
                "vol_regime",
                FeatureKind.VOLATILITY_REGIME,
                FeatureField.CLOSE,
                window=5,
                parameters={"long_window": 15},
            ),
        ),
        inputs={
            "events": events.version,
            "sentiment": sentiment.version,
            "fundamentals": fundamentals.version,
            "regime": model.definition_id,
        },
    )
    metrics = {
        **{f"event.{name}": value for name, value in event_study_metrics(reaction).items()},
        "regime.classified": float(regime_profile(detected, None).classified),
    }
    return build_result(study, metrics, produced_at=20.0)


def test_the_study_manifest_names_every_point_in_time_input(
    study_result: StudyResult, prices: Dataset
) -> None:
    manifest = manifest_for_study(study_result, prices, running_engine())
    auxiliary = [
        requirement.detail
        for requirement in external_requirements(manifest)
        if requirement.input is ExternalInput.AUXILIARY_DATA
    ]

    assert verify_manifest(manifest)
    assert [detail.split(":")[0] for detail in auxiliary] == [
        "events",
        "fundamentals",
        "regime",
        "sentiment",
    ]
    assert study_result.study.inputs["events"] in manifest.configuration


# --------------------------------------------------------------------------- #
# The adaptive strategy on the execution path
# --------------------------------------------------------------------------- #


DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="V3.7 Adaptive Reversion",
    version="1",
    author="tests",
    description="trades AAA against its own trailing z-score",
    parameters={"window": 5.0, "entry": 1.0},
)


def _configuration(parameters: Mapping[str, float]) -> AdaptiveConfiguration:
    return AdaptiveConfiguration(
        name="aaa_reversion",
        rule_id="trailing_zscore",
        rule_version=1,
        inputs=("price",),
        parameters={"window": parameters["window"], "entry": parameters["entry"]},
        cadence=UpdateCadence.EVERY_OBSERVATION,
        cadence_every=None,
        cadence_seconds=None,
        decision_timing=DecisionTiming.BEFORE_UPDATE,
        warmup=5,
    )


CONFIGURATION = _configuration(DEFINITION.parameters)
RULE = TrailingZScoreRule()
STREAM = f"{PROVIDER}:{ASSETS[TRADED]}"


class Reversion(AdaptiveStrategy):
    def observation_for(self, event: Any, sequence: int) -> AdaptiveObservation | None:
        bar = getattr(event, "bar", None)
        if bar is None or bar.asset_id != ASSETS[TRADED]:
            return None
        return AdaptiveObservation(
            float(bar.timestamp), sequence, STREAM, {"price": float(bar.close)}
        )

    def intents_for(
        self, decision: AdaptiveDecision, event: Any, context: StrategyContext
    ) -> Iterable[Intent]:
        signal = decision.outputs.get("signal")
        if not signal:
            return ()
        return (
            Intent(
                strategy_id=self.strategy_id,
                instrument=ASSETS[TRADED],
                target=Decimal(str(signal)) * Decimal("10"),
                timestamp=event.bar.timestamp,
            ),
        )


def _traded_rows(prices: Dataset) -> list[tuple[float, dict[str, float]]]:
    """The traded name's closes, exactly as the run reads them from the dataset."""

    rows: list[tuple[float, dict[str, float]]] = []
    for record in to_market_dataset(prices, NORMALIZATION).records:
        bar = record.payload
        if isinstance(bar, MarketBar) and bar.asset_id == ASSETS[TRADED]:
            rows.append((float(record.timestamp), {"price": float(bar.close)}))
    return rows


def _run_config() -> RunConfig:
    return RunConfig(
        pipeline=replace(pipeline_config(STRATEGY_ID), instruments=REGISTRY),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        start_timestamp=1.0,
    )


def _strategy() -> Reversion:
    return Reversion(STRATEGY_ID, CONFIGURATION, RULE, AdaptationMode.LEARNING, None)


@pytest.fixture(scope="module")
def runs(prices: Dataset) -> tuple[BacktestResult, BacktestResult, Reversion]:
    strategy = _strategy()
    first = backtest(
        _run_config(),
        prices,
        running_strategy_state(STRATEGY_ID, strategy),
        context_factory,
        NORMALIZATION,
    )
    second = backtest(
        _run_config(),
        prices,
        running_strategy_state(STRATEGY_ID, _strategy()),
        context_factory,
        NORMALIZATION,
    )
    return first, second, strategy


def test_the_run_and_the_research_replay_learn_the_same_thing(
    runs: tuple[BacktestResult, BacktestResult, Reversion], prices: Dataset
) -> None:
    first, _, strategy = runs
    rows = _traded_rows(prices)

    research = replay_updates(
        CONFIGURATION,
        RULE,
        observation_stream(STREAM, rows, first_sequence=0),
        AdaptationMode.LEARNING,
    )

    assert strategy.adaptive_state == research.final
    signals = sum(1 for decision in research.decisions if decision.outputs.get("signal"))
    assert len(first.state.oms.orders.orders()) == signals > 0


def test_backtest_and_replay_agree_for_an_adaptive_strategy(
    runs: tuple[BacktestResult, BacktestResult, Reversion], prices: Dataset
) -> None:
    first, _, _ = runs
    replayed = replay(
        _run_config(),
        prices,
        running_strategy_state(STRATEGY_ID, _strategy()),
        context_factory,
        NORMALIZATION,
    )

    assert serialize(first.state.portfolio.positions) == serialize(
        replayed.backtest.state.portfolio.positions
    )


@pytest.fixture(scope="module")
def lifecycle(
    runs: tuple[BacktestResult, BacktestResult, Reversion],
    prices: Dataset,
    study_result: StudyResult,
) -> Any:
    first, second, _ = runs
    state, reference = register_strategy(LifecycleState(), STRATEGY_ID, DEFINITION, 30.0)
    version = get_strategy_version(state.strategies, STRATEGY_ID, reference.version)
    code = CodeIdentity(
        "v37-strategies",
        "1.0.0",
        "v37_strategies.reversion.Reversion",
        source_digest({"v37_strategies/reversion.py": b"class Reversion: ...\n"}),
    )
    research = research_configuration_with_adaptive(
        {"execution": "ExecutionSimulator defaults"},
        [(CONFIGURATION, initial_state(CONFIGURATION, RULE))],
        study=study_result,
    )
    engine = running_engine()
    fingerprint = fingerprint_for_version(version, code, NO_DEPENDENCIES, research, engine)
    manifest = manifest_for_run(first, prices, fingerprint, engine)
    rerun = manifest_for_run(second, prices, fingerprint, engine)
    specification = specification_for_version(
        version,
        datasets=(dataset_assumption_from(prices, "prices"),),
        risk=pipeline_config(STRATEGY_ID).risk_limits,
        capital=CapitalPolicy("acct-v21", "USD", START_CASH, ("USD",)),
        broker=BrokerRequirements(
            order_types=frozenset({OrderType.MARKET}),
            time_in_force=frozenset({TimeInForce.DAY}),
            asset_classes=frozenset({AssetType.EQUITY}),
            short_selling=True,
            fractional_quantities=True,
        ),
        market=MarketRequirements((ASSETS[TRADED],), ("XNYS",), ("XNYS",), ("USD",)),
        runtime=RuntimeRequirements(
            max_data_staleness_seconds=345_600.0,
            max_heartbeat_silence_seconds=345_600.0,
            max_execution_latency_seconds=5.0,
            position_tolerance=Tolerance(absolute=Decimal("0.000001")),
        ),
    )
    report = certify_strategy(
        fingerprint,
        specification,
        CertificationEvidence(
            runs=(first,),
            repeated_runs=(first, second),
            datasets=(prices,),
            manifest=manifest,
            rerun=rerun,
        ),
    )
    return fingerprint, manifest, rerun, report


def test_the_fingerprint_names_the_adaptive_configuration_and_the_study(
    lifecycle: Any, study_result: StudyResult
) -> None:
    fingerprint, *_ = lifecycle

    assert fingerprint.research.study_id == study_result.study_id
    assert fingerprint.research.settings["adaptive.aaa_reversion.configuration"] == (
        CONFIGURATION.configuration_id
    )
    assert dict(fingerprint.parameters) == dict(DEFINITION.parameters)


def test_the_run_is_reproduced_and_its_record_carries_the_learned_state(
    lifecycle: Any, runs: tuple[BacktestResult, BacktestResult, Reversion]
) -> None:
    _, manifest, rerun, _ = lifecycle
    _, _, strategy = runs

    assert assess_reproducibility(manifest, rerun).rerun is RerunOutcome.REPRODUCED
    snapshot = json.loads(serialize(capture_run(runs[0].run)))
    (record,) = snapshot["pipeline"]["strategy"]
    assert record["state"]["payload"]["state_id"] == strategy.adaptive_state.state_id


def test_certification_passes_determinism_and_reproducibility(lifecycle: Any) -> None:
    *_, report = lifecycle
    statuses = {assessment.claim: assessment.status for assessment in report.assessments}

    assert statuses[CertificationProperty.DETERMINISTIC] is CertificationStatus.PASS
    assert statuses[CertificationProperty.REPRODUCIBLE] is CertificationStatus.PASS
    assert statuses[CertificationProperty.REQUIRED_DATA] is CertificationStatus.PASS


def test_an_adaptive_research_replay_reproduces(prices: Dataset) -> None:
    rows = _traded_rows(prices)
    stream = observation_stream(STREAM, rows, first_sequence=0)

    first = replay_updates(CONFIGURATION, RULE, stream, AdaptationMode.LEARNING)
    second = replay_updates(CONFIGURATION, RULE, stream, AdaptationMode.LEARNING)

    assert assess_adaptive_replay(first, second).rerun is RerunOutcome.REPRODUCED


def test_every_v37_artifact_is_deterministic_json(
    events: ObservationSet[InformationEvent],
    reaction: EventStudyResult,
    study_result: StudyResult,
    regimes: Any,
) -> None:
    _, _, detected = regimes
    for value in (events, reaction, study_result, detected):
        payload = serialize(value)
        assert serialize(value) == payload
        assert json.loads(payload)
