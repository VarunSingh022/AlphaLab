"""High-performance benchmark suite for the v3.7 point-in-time research paths.

Seven computational paths, each on a workload a research application produces:

* **Ingestion** -- vendor rows read into a versioned point-in-time set: every
  row validated, every timestamp read under its declared meaning, availability
  derived by a stated rule, the set's identity derived over every record.
* **Sets and views** -- a set of prints with revisions built, verified and
  indexed under the publication rule, as a research session does once per set.
* **Point-in-time queries** -- how much was visible at each of many instants,
  what one series held, and one figure read as known and as first published.
* **Knowledge frames** -- a metric sampled at a research clock for every
  subject, then joined to prices by the checked join.
* **Fundamentals** -- trailing twelve months, valuation inputs and a
  point-in-time fundamental frame over many issuers.
* **Event studies** -- many events anchored at their first tradable session
  and measured against a benchmark.
* **Regime detection** -- threshold, trailing-quantile and composite rules over
  long series, and a regime profile.

Each path is measured at two sizes, so the printed ops/sec is readable *and*
the scaling is visible. ``tests/regression/test_v37_complexity.py`` asserts the
growth ratios; this prints the absolute numbers those ratios are made of.
Everything is built before its timer starts, from the public API, with no
clock, no random number and no file.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from alphalab.alt_data import (
    EARNINGS,
    SHARES_OUTSTANDING,
    Aggregation,
    ExternalObservation,
    FiscalPeriod,
    FundamentalInput,
    FundamentalObservation,
    InformationEvent,
    ObservationSet,
    ObservationSource,
    ObservationView,
    ReferencePeriod,
    StatementType,
    VintagePolicy,
    build_observation_set,
    fundamental_inputs_as_of,
    trailing_twelve_months,
    valuation_metrics,
    verify_observation_set,
)
from alphalab.api import (
    AvailabilityAfterLag,
    ObservationColumns,
    TimestampReading,
    ingest_observations,
    observation_source,
)
from alphalab.common import PointInTimeIndex, PointInTimeStamp, VisibilityRule
from alphalab.data.calendar import MarketCalendar
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.time import TimestampFormat
from alphalab.factor_library import (
    KnowledgeFrame,
    ResearchClock,
    align_prices,
    fundamental_frame,
    observation_frame,
)
from alphalab.factor_library.definition import FeatureField
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.research import (
    AbnormalReturnModel,
    CompositeRule,
    EventStudyDefinition,
    EventWindow,
    RegimeDefinition,
    ThresholdRule,
    TrailingQuantileRule,
    classify_regimes,
    event_study,
    regime_profile,
)

DAY = 86_400.0
PUB = VisibilityRule.PUBLICATION
AS_KNOWN = VintagePolicy.AS_KNOWN
INCOME = StatementType.INCOME_STATEMENT
BALANCE = StatementType.BALANCE_SHEET
SOURCE = ObservationSource(
    source_id="bench.feed", version="1", content_hash="c" * 64, retrieved_at=1_730_000_000.0
)
CONTINUOUS = MarketCalendar.continuous("CONT", "UTC")
Series = list[tuple[float, float]]


def _timed(label: str, count: int, work: Callable[[], object]) -> None:
    start = time.perf_counter()
    work()
    duration = time.perf_counter() - start
    print(f"  {label:<60} {duration:.4f}s, {count / max(duration, 1e-9):>12,.0f} ops/sec")


# --------------------------------------------------------------------------- #
# Workloads
# --------------------------------------------------------------------------- #


def _prints(count: int, subjects: int = 50) -> list[ExternalObservation]:
    """``count`` monthly prints over ``subjects`` subjects, a quarter of them revised."""

    records: list[ExternalObservation] = []
    for index in range(count):
        month = index // subjects
        observed = month * 30 * DAY + DAY
        period = ReferencePeriod(month * 30 * DAY, observed, f"M{month:05d}")
        for revision, lag in ((0, 5), (1, 25)):
            if revision and index % 4:
                continue
            records.append(
                ExternalObservation(
                    category="economic",
                    subject=f"S{index % subjects:03d}",
                    metric="m",
                    value=Decimal(index % (97 - 8 * revision)),
                    unit="percent",
                    stamp=PointInTimeStamp.declared(observed, observed + lag * DAY),
                    source=SOURCE,
                    period=period,
                    revision=revision,
                )
            )
    return records


def _news_rows(count: int) -> list[dict[str, str]]:
    start = datetime(2024, 1, 1, tzinfo=UTC).timestamp()
    return [
        {
            "ticker": f"T{index % 100:03d}",
            "metric": "news_score",
            "value": f"{(index % 9 - 4) / 4:.2f}",
            "observed": datetime.fromtimestamp(start + index * 3_600, UTC).strftime(
                "%Y-%m-%d %H:%M"
            ),
        }
        for index in range(count)
    ]


def _prices(subjects: Sequence[str], instants: tuple[float, ...]) -> ObservationFrame:
    return ObservationFrame(
        FeatureField.CLOSE,
        {
            subject: ObservationSeries(
                subject,
                instants,
                tuple(100.0 + (offset * 7 + position) % 13 for offset in range(len(instants))),
            )
            for position, subject in enumerate(subjects)
        },
        "UTC",
        "bench-prices@1",
    )


def _quarter(year: int, number: int) -> FiscalPeriod:
    start = datetime(year, 3 * number - 2, 1, tzinfo=UTC).timestamp()
    following = (year + 1, 1) if number == 4 else (year, 3 * number + 1)
    end = datetime(*following, 1, tzinfo=UTC).timestamp() - 1.0
    return FiscalPeriod(year, number, start, end)


QUARTERS = tuple((year, number) for year in (2022, 2023) for number in (1, 2, 3, 4))


def _statements(issuers: int) -> list[FundamentalObservation]:
    """Eight quarters of net income and diluted shares for each issuer."""

    records: list[FundamentalObservation] = []
    for issuer in range(issuers):
        for year, number in QUARTERS:
            period = _quarter(year, number)
            published = period.end + 40 * DAY
            for statement, item, value, unit in (
                (INCOME, "net_income", Decimal(50 + issuer % 7), "USD"),
                (BALANCE, "shares", Decimal(100 + issuer), "shares"),
            ):
                records.append(
                    FundamentalObservation(
                        subject=f"I{issuer:04d}",
                        statement=statement,
                        line_item=item,
                        fiscal_period=period,
                        value=value,
                        unit=unit,
                        published_at=published,
                        stamp=PointInTimeStamp.declared(period.end, published),
                        source=SOURCE,
                        revision=0,
                    )
                )
    return records


def _cycle(count: int, period: float, step: int) -> Series:
    """A slow deterministic cycle with a little jitter: long spells, some flicker."""

    return [
        (float(index), 50.0 + 40.0 * math.sin(index / period) + float((index * step) % 11 - 5))
        for index in range(count)
    ]


def _events(count: int) -> ObservationSet[InformationEvent]:
    spacing = 5_000 // count
    return build_observation_set(
        "events",
        [
            InformationEvent(
                event_type="earnings.release",
                subject="AAA",
                stamp=PointInTimeStamp.declared(
                    (40 + index * spacing) * DAY + 3_600, (40 + index * spacing) * DAY + 3_660
                ),
                source=SOURCE,
                measurements={"actual": Decimal(index % 7)},
                attributes={},
                revision=0,
            )
            for index in range(count)
        ],
        SOURCE,
    )


# --------------------------------------------------------------------------- #
# Timed work, one closure per measurement
# --------------------------------------------------------------------------- #


def _ingest(rows: list[dict[str, str]], source: ObservationSource) -> Callable[[], object]:
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
    reading = TimestampReading(TimestampFormat.ISO_8601_NAIVE, "America/New_York", None)
    return lambda: ingest_observations(rows, "news", source, columns, reading)


def _build(records: list[ExternalObservation]) -> Callable[[], object]:
    return lambda: build_observation_set("prints", records, SOURCE)


def _build_and_view(records: list[ExternalObservation]) -> Callable[[], object]:
    return lambda: build_observation_set("prints", records, SOURCE).view(PUB)


def _verify(obs_set: ObservationSet[ExternalObservation]) -> Callable[[], object]:
    return lambda: verify_observation_set(obs_set)


def _count_visible(
    index: PointInTimeIndex[ExternalObservation], instants: Sequence[float]
) -> Callable[[], object]:
    return lambda: [index.count_visible(at) for at in instants]


def _series_reads(
    view: ObservationView[ExternalObservation], instants: Sequence[float]
) -> Callable[[], object]:
    key = view.series_keys[0]
    return lambda: [view.visible_in_series(key, at) for at in instants]


def _figure_reads(
    view: ObservationView[ExternalObservation],
    key: tuple[str, ...],
    instants: Sequence[float],
) -> Callable[[], object]:
    policies = (VintagePolicy.AS_KNOWN, VintagePolicy.ORIGINAL)
    return lambda: [view.vintage_as_of(key, at, policy) for at in instants for policy in policies]


def _frame(
    view: ObservationView[ExternalObservation], clock: ResearchClock, subjects: Sequence[str]
) -> Callable[[], KnowledgeFrame]:
    return lambda: observation_frame(
        view, "economic", "m", clock, subjects, max_age_seconds=None, policy=AS_KNOWN
    )


def _align(prices: ObservationFrame, knowledge: KnowledgeFrame) -> Callable[[], object]:
    return lambda: align_prices(prices, knowledge)


def _trailing(
    view: ObservationView[FundamentalObservation], subjects: Sequence[str], at: float
) -> Callable[[], object]:
    return lambda: [
        trailing_twelve_months(view, subject, INCOME, "net_income", at, AS_KNOWN)
        for subject in subjects
    ]


def _valuations(
    view: ObservationView[FundamentalObservation],
    subjects: Sequence[str],
    at: float,
    inputs: Sequence[FundamentalInput],
) -> Callable[[], object]:
    return lambda: [
        valuation_metrics(
            fundamental_inputs_as_of(view, subject, at, AS_KNOWN, inputs),
            Decimal("25"),
            "USD/share",
            at,
        )
        for subject in subjects
    ]


def _fundamental_frame(
    view: ObservationView[FundamentalObservation],
    spec: FundamentalInput,
    clock: ResearchClock,
    subjects: Sequence[str],
) -> Callable[[], object]:
    return lambda: fundamental_frame(
        view, spec, clock, subjects, max_age_seconds=None, policy=AS_KNOWN
    )


def _study(
    events: ObservationSet[InformationEvent],
    prices: ObservationFrame,
    definition: EventStudyDefinition,
) -> Callable[[], object]:
    return lambda: event_study(events, prices, CONTINUOUS, definition)


def _classify(definition: RegimeDefinition, signals: Mapping[str, Series]) -> Callable[[], object]:
    return lambda: classify_regimes(definition, signals, input_id=None)


# --------------------------------------------------------------------------- #
# Benchmarks
# --------------------------------------------------------------------------- #


def benchmark_ingestion() -> None:
    print("\n[1] Point-in-time ingestion")
    for count in (2_000, 8_000):
        rows = _news_rows(count)
        raw = raw_source_from_bytes(
            SourceKind.VENDOR_FILE,
            "bench.csv",
            repr(rows).encode("utf-8"),
            1_730_000_000.0,
            "text/csv",
            "utf-8",
        )
        source = observation_source(raw, "bench.news", "1")
        _timed(f"ingest_observations ({count:,} rows)", count, _ingest(rows, source))


def benchmark_sets() -> None:
    print("\n[2] Observation sets")
    for count in (5_000, 20_000):
        records = _prints(count)
        size = len(records)
        _timed(f"build_observation_set ({size:,} records)", size, _build(records))
        built = build_observation_set("prints", records, SOURCE)
        _timed(f"verify_observation_set ({size:,} records)", size, _verify(built))
        _timed(
            f"build and view under the publication rule ({size:,})", size, _build_and_view(records)
        )


def benchmark_queries() -> None:
    print("\n[3] Point-in-time queries")
    for count in (5_000, 20_000):
        records = _prints(count)
        index = PointInTimeIndex.build(records, PUB)
        view = build_observation_set("prints", records, SOURCE).view(PUB)
        instants = [float(day) * DAY for day in range(0, (count // 50) * 30, 3)]
        reads = len(instants)
        _timed(
            f"count_visible at {reads:,} instants ({count:,} prints)",
            reads,
            _count_visible(index, instants),
        )
        _timed(
            f"visible_in_series at {reads:,} instants ({count // 50} months)",
            reads,
            _series_reads(view, instants),
        )
        _timed(
            f"vintage_as_of, both policies, at {reads:,} instants",
            2 * reads,
            _figure_reads(view, records[0].vintage_key, instants),
        )


def benchmark_knowledge_frames() -> None:
    print("\n[4] Knowledge frames")
    subjects = tuple(f"S{index:03d}" for index in range(50))
    for count in (5_000, 20_000):
        view = build_observation_set("prints", _prints(count), SOURCE).view(PUB)
        instants = tuple(float(day) * DAY for day in range(0, (count // 50) * 30, 3))
        prices = _prices(subjects, instants)
        frame = _frame(view, ResearchClock.of_frame(prices), subjects)
        cells = len(subjects) * len(instants)
        shape = f"{len(subjects)} subjects x {len(instants):,} instants"
        _timed(f"observation_frame ({shape})", cells, frame)
        _timed(f"align_prices ({shape})", cells, _align(prices, frame()))


def benchmark_fundamentals() -> None:
    print("\n[5] Fundamentals")
    earnings = FundamentalInput(EARNINGS, INCOME, "net_income", Aggregation.TRAILING_TWELVE_MONTHS)
    shares = FundamentalInput(SHARES_OUTSTANDING, BALANCE, "shares", Aggregation.LATEST)
    at = _quarter(2023, 4).end + 60 * DAY
    clock = ResearchClock.of_instants(
        tuple(_quarter(2023, 1).end + day * DAY for day in range(0, 400, 5)), "UTC"
    )
    for issuers in (100, 400):
        view = build_observation_set("statements", _statements(issuers), SOURCE).view(PUB)
        subjects = tuple(f"I{issuer:04d}" for issuer in range(issuers))
        _timed(
            f"trailing_twelve_months ({issuers} issuers)",
            issuers,
            _trailing(view, subjects, at),
        )
        _timed(
            f"fundamental_inputs_as_of + valuation_metrics ({issuers} issuers)",
            issuers,
            _valuations(view, subjects, at, (earnings, shares)),
        )
        _timed(
            f"fundamental_frame, TTM ({issuers} issuers x {len(clock.instants)} instants)",
            issuers * len(clock.instants),
            _fundamental_frame(view, earnings, clock, subjects),
        )


def benchmark_event_studies() -> None:
    print("\n[6] Event studies")
    prices = _prices(("AAA", "MKT"), tuple(float(index) * DAY for index in range(6_000)))
    definitions = (
        EventStudyDefinition(
            "bench-market",
            ("earnings.release",),
            EventWindow(5, 5, 0, 0),
            AbnormalReturnModel.MARKET_ADJUSTED,
            "MKT",
            PUB,
        ),
        EventStudyDefinition(
            "bench-mean",
            ("earnings.release",),
            EventWindow(5, 5, 20, 2),
            AbnormalReturnModel.MEAN_ADJUSTED,
            None,
            PUB,
        ),
    )
    for count in (250, 1_000):
        events = _events(count)
        for definition in definitions:
            _timed(
                f"event_study ({count:,} events, {definition.model.name.lower()})",
                count,
                _study(events, prices, definition),
            )


def benchmark_regimes() -> None:
    print("\n[7] Regime detection")
    threshold = RegimeDefinition("vol", ThresholdRule("x", (40.0, 70.0), ("low", "mid", "high")), 3)
    quantile = RegimeDefinition(
        "quantile", TrailingQuantileRule("x", 60, (0.3, 0.7), ("low", "mid", "high")), 3
    )
    composite = RegimeDefinition(
        "appetite",
        CompositeRule.of(
            (
                ThresholdRule("x", (50.0,), ("bear", "bull")),
                ThresholdRule("y", (50.0,), ("quiet", "volatile")),
            ),
            {
                ("bear", "quiet"): "risk_off",
                ("bear", "volatile"): "risk_off",
                ("bull", "quiet"): "risk_on",
                ("bull", "volatile"): "neutral",
            },
        ),
        2,
    )
    for count in (10_000, 40_000):
        x = _cycle(count, 37.0, 41)
        y = _cycle(count, 53.0, 29)
        _timed(f"classify_regimes, threshold ({count:,})", count, _classify(threshold, {"x": x}))
        _timed(
            f"classify_regimes, trailing quantile of 60 ({count:,})",
            count,
            _classify(quantile, {"x": x}),
        )
        _timed(
            f"classify_regimes, composite of two ({count:,})",
            count,
            _classify(composite, {"x": x, "y": y}),
        )
        series = classify_regimes(threshold, {"x": x}, input_id=None)
        returns = {stamp: value / 1_000.0 for stamp, value in y}
        start = time.perf_counter()
        regime_profile(series, returns)
        duration = time.perf_counter() - start
        label = f"regime_profile with returns ({count:,})"
        print(f"  {label:<60} {duration:.4f}s, {count / max(duration, 1e-9):>12,.0f} ops/sec")


def run_benchmark() -> None:
    print("=" * 94)
    print("AlphaLab v3.7 -- point-in-time research: ingestion, sets, frames, events, regimes")
    print("=" * 94)
    benchmark_ingestion()
    benchmark_sets()
    benchmark_queries()
    benchmark_knowledge_frames()
    benchmark_fundamentals()
    benchmark_event_studies()
    benchmark_regimes()
    print()


if __name__ == "__main__":
    run_benchmark()
