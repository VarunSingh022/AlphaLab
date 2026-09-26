"""The v3.7 paths stay near-linear, measured on every run.

Same shape as ``test_v36_complexity.py``: the assertion is on the **growth
ratio** between two input sizes, not on a duration, so it is about the algorithm
rather than the machine. Quadrupling the input quadruples a linear path and
multiplies a quadratic one by sixteen; the bound sits between the two.

Which paths, and why these
---------------------------

Each is one whose obvious implementation is quadratic:

* **Building a set and its view** -- one sort per index, one grouping pass. A
  vintage check that compared every record with every other would not be.
* **Point-in-time queries at many instants** -- a bisection each, where a filter
  per instant rescans the set.
* **Reading one figure** -- that figure's few vintages, however long its series;
  a filter over the series' visible history grows with every period it holds.
* **A knowledge frame** -- a timeline per series sampled by bisection, where
  "latest known at each instant" computed naively rescans every record at every
  instant for every subject.
* **A fundamental frame** -- the aggregate recomputed only when a figure
  arrives, never at every instant.
* **Regime classification and adaptive replay** -- a bounded window per step,
  never the whole history.
* **An event study** -- a bisection per event into its subject's prices.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from alphalab.alt_data import (
    Aggregation,
    ExternalObservation,
    FundamentalInput,
    FundamentalObservation,
    InformationEvent,
    ObservationSet,
    ReferencePeriod,
    VintagePolicy,
    build_observation_set,
)
from alphalab.common import PointInTimeIndex, VisibilityRule
from alphalab.factor_library import ResearchClock, fundamental_frame, observation_frame
from alphalab.factor_library.definition import FeatureField
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.research import (
    AbnormalReturnModel,
    EventStudyDefinition,
    EventWindow,
    RegimeDefinition,
    TrailingQuantileRule,
    classify_regimes,
    event_study,
)
from alphalab.strategy import (
    AdaptationMode,
    AdaptiveConfiguration,
    AdaptiveObservation,
    DecisionTiming,
    TrailingZScoreRule,
    UpdateCadence,
    observation_stream,
    replay_updates,
)
from tests.unit.alt_data.pit_harness import INCOME, SOURCE, event, fundamental, observation
from tests.unit.alt_data.pit_harness import quarter as calendar_quarter

#: A ratio above linear and well below quadratic.
LINEAR_BOUND = 8.0

DAY = 86_400.0


def _elapsed(work: Callable[[], object]) -> float:
    """Best of three, so one scheduling hiccup does not fail the suite."""

    return min(_once(work) for _ in range(3))


def _once(work: Callable[[], object]) -> float:
    start = time.perf_counter()
    work()
    return time.perf_counter() - start


def _growth(small: Callable[[], object], large: Callable[[], object]) -> float:
    return _elapsed(large) / max(_elapsed(small), 1e-4)


def _observations(count: int) -> list[ExternalObservation]:
    """``count`` monthly prints over 50 subjects, a quarter of them revised."""

    records: list[ExternalObservation] = []
    for index in range(count):
        subject = f"S{index % 50:02d}"
        month = index // 50
        observed = month * 30 * DAY + DAY
        period = ReferencePeriod(month * 30 * DAY, observed, f"M{month:05d}")
        records.append(
            observation(subject, "m", str(index % 97), observed, observed + 5 * DAY, period=period)
        )
        if index % 4 == 0:
            records.append(
                observation(
                    subject,
                    "m",
                    str(index % 89),
                    observed,
                    observed + 25 * DAY,
                    period=period,
                    revision=1,
                )
            )
    return records


def test_building_a_set_and_its_view_is_near_linear() -> None:
    small, large = _observations(2_000), _observations(8_000)

    def build(records: list[ExternalObservation]) -> Callable[[], object]:
        return lambda: build_observation_set("set", records, SOURCE).view(
            VisibilityRule.PUBLICATION
        )

    growth = _growth(build(small), build(large))
    assert growth < LINEAR_BOUND, f"set construction grew {growth:.1f}x for 4x input"


def test_counting_what_was_visible_at_many_instants_is_bisection() -> None:
    def index_of(count: int) -> PointInTimeIndex[ExternalObservation]:
        return PointInTimeIndex.build(_observations(count), VisibilityRule.PUBLICATION)

    small, large = index_of(2_000), index_of(8_000)
    small_clock = [float(instant) * DAY for instant in range(2_000)]
    large_clock = [float(instant) * DAY for instant in range(8_000)]

    growth = _growth(
        lambda: [small.count_visible(instant) for instant in small_clock],
        lambda: [large.count_visible(instant) for instant in large_clock],
    )
    assert growth < LINEAR_BOUND, f"visibility queries grew {growth:.1f}x for 4x input"


def test_reading_one_figure_does_not_scan_its_series() -> None:
    """Sixteen times the history, the same number of reads: the cost stays flat.

    Every read is made after the whole series is knowable, where a filter over
    the series' visible history would scan all of it.
    """

    def reads(months: int) -> Callable[[], object]:
        records = _observations(months * 50)
        view = build_observation_set("figures", records, SOURCE).view(VisibilityRule.PUBLICATION)
        key = records[0].vintage_key
        known = (months + 2) * 30 * DAY
        probes = [known + float(index % 60) * DAY for index in range(20_000)]
        return lambda: [view.vintage_as_of(key, at, VintagePolicy.AS_KNOWN) for at in probes]

    growth = _growth(reads(40), reads(640))
    assert growth < 4.0, f"reading one figure grew {growth:.1f}x for 16x the series' history"


def _frame_inputs(count: int) -> tuple[ObservationSet[ExternalObservation], tuple[float, ...]]:
    obs_set = build_observation_set("frame", _observations(count), SOURCE)
    months = count // 50
    return obs_set, tuple(float(day) * DAY for day in range(0, months * 30, 3))


def test_a_knowledge_frame_is_near_linear_in_records_and_instants() -> None:
    subjects = tuple(f"S{index:02d}" for index in range(50))
    small_set, small_clock = _frame_inputs(1_000)
    large_set, large_clock = _frame_inputs(4_000)

    def frame(
        obs_set: ObservationSet[ExternalObservation], clock: tuple[float, ...]
    ) -> Callable[[], object]:
        view = obs_set.view(VisibilityRule.PUBLICATION)
        return lambda: observation_frame(
            view,
            "economic",
            "m",
            ResearchClock.of_instants(clock, "UTC"),
            subjects,
            max_age_seconds=None,
            policy=VintagePolicy.AS_KNOWN,
        )

    growth = _growth(frame(small_set, small_clock), frame(large_set, large_clock))
    assert growth < LINEAR_BOUND, f"a knowledge frame grew {growth:.1f}x for 4x input"


def _issuers(count: int) -> list[FundamentalObservation]:
    records: list[FundamentalObservation] = []
    for issuer in range(count):
        for year, number in ((2023, 1), (2023, 2), (2023, 3), (2023, 4), (2024, 1), (2024, 2)):
            period = calendar_quarter(year, number)
            records.append(
                fundamental(
                    f"I{issuer:04d}",
                    INCOME,
                    "revenue",
                    period,
                    str(100 + issuer),
                    period.end + 40 * DAY,
                )
            )
    return records


def test_a_fundamental_frame_is_near_linear_in_issuers() -> None:
    spec = FundamentalInput("revenue", INCOME, "revenue", Aggregation.TRAILING_TWELVE_MONTHS)

    def frame(count: int) -> Callable[[], object]:
        view = build_observation_set("fund", _issuers(count), SOURCE).view(
            VisibilityRule.PUBLICATION
        )
        subjects = tuple(f"I{issuer:04d}" for issuer in range(count))
        clock = tuple(calendar_quarter(2024, 2).end + day * DAY for day in range(60, 120, 5))
        return lambda: fundamental_frame(
            view,
            spec,
            ResearchClock.of_instants(clock, "UTC"),
            subjects,
            max_age_seconds=None,
            policy=VintagePolicy.AS_KNOWN,
        )

    growth = _growth(frame(100), frame(400))
    assert growth < LINEAR_BOUND, f"a fundamental frame grew {growth:.1f}x for 4x issuers"


def test_regime_classification_is_linear_in_observations() -> None:
    model = RegimeDefinition(
        "vol", TrailingQuantileRule("x", 20, (0.3, 0.7), ("low", "mid", "high")), 3
    )

    def series(count: int) -> list[tuple[float, float]]:
        return [(float(index), float((index * 7919) % 101)) for index in range(count)]

    small, large = series(5_000), series(20_000)
    growth = _growth(
        lambda: classify_regimes(model, {"x": small}, input_id=None),
        lambda: classify_regimes(model, {"x": large}, input_id=None),
    )
    assert growth < LINEAR_BOUND, f"regime classification grew {growth:.1f}x for 4x input"


def test_an_adaptive_replay_is_linear_in_observations() -> None:
    configuration = AdaptiveConfiguration(
        name="z",
        rule_id="trailing_zscore",
        rule_version=1,
        inputs=("price",),
        parameters={"window": 20, "entry": 1.5},
        cadence=UpdateCadence.EVERY_OBSERVATION,
        cadence_every=None,
        cadence_seconds=None,
        decision_timing=DecisionTiming.BEFORE_UPDATE,
        warmup=20,
    )
    rule = TrailingZScoreRule()

    def stream(count: int) -> tuple[AdaptiveObservation, ...]:
        rows = [(float(i), {"price": float((i * 7919) % 101)}) for i in range(count)]
        return observation_stream("s", rows, first_sequence=0)

    small, large = stream(2_000), stream(8_000)
    growth = _growth(
        lambda: replay_updates(configuration, rule, small, AdaptationMode.LEARNING),
        lambda: replay_updates(configuration, rule, large, AdaptationMode.LEARNING),
    )
    assert growth < LINEAR_BOUND, f"an adaptive replay grew {growth:.1f}x for 4x input"


def test_an_event_study_is_linear_in_events() -> None:
    from alphalab.data.calendar import MarketCalendar

    calendar = MarketCalendar.continuous("CONT", "UTC")
    stamps = tuple(float(index) * DAY for index in range(6_000))
    values = tuple(100.0 + (index % 13) for index in range(6_000))
    prices = ObservationFrame(
        FeatureField.CLOSE,
        {
            "AAA": ObservationSeries("AAA", stamps, values),
            "MKT": ObservationSeries("MKT", stamps, values),
        },
        "UTC",
        "prices@1",
    )
    definition = EventStudyDefinition(
        "scaling",
        ("earnings.release",),
        EventWindow(5, 5, 0, 0),
        AbnormalReturnModel.MARKET_ADJUSTED,
        "MKT",
        VisibilityRule.PUBLICATION,
    )

    def events(count: int) -> ObservationSet[InformationEvent]:
        spacing = 5_000 // count
        records = [
            event(
                "earnings.release",
                "AAA",
                (10 + i * spacing) * DAY + 3600,
                (10 + i * spacing) * DAY + 3600,
            )
            for i in range(count)
        ]
        return build_observation_set("events", records, SOURCE)

    small, large = events(250), events(1_000)
    growth = _growth(
        lambda: event_study(small, prices, calendar, definition),
        lambda: event_study(large, prices, calendar, definition),
    )
    assert growth < LINEAR_BOUND, f"an event study grew {growth:.1f}x for 4x events"
