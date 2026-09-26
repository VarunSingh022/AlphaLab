"""Event studies anchored where information could first be traded.

Daily closes stamped at 16:00 New York on the real trading days of May and June
2024. Each subject's return is 0.1% a day, the benchmark's the same, and a
subject jumps 10% on the session its news reached -- so a correctly anchored
study sees the whole jump at offset 0 and nothing before it, and a study
anchored on the occurrence instant rather than the tradable one would not.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date

import pytest

from alphalab.alt_data import InformationEvent, SessionTiming, build_observation_set
from alphalab.common import VisibilityRule
from alphalab.factor_library.definition import FeatureField
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.research import (
    AbnormalReturnModel,
    EventStudyDefinition,
    EventStudyResult,
    EventWindow,
    ResearchValidationError,
    event_study,
    event_study_metrics,
)
from tests.unit.alt_data.pit_harness import NYSE, SOURCE, event, ny

PUB = VisibilityRule.PUBLICATION
SESSIONS = NYSE.sessions_between(date(2024, 5, 1), date(2024, 6, 28))
CLOSES = tuple(NYSE.session_bounds(day)[1] for day in SESSIONS)  # type: ignore[index]
DAILY = 0.001
JUMP = 0.10


def _index_of(day: date) -> int:
    return SESSIONS.index(day)


def _path(jumps: set[int]) -> tuple[float, ...]:
    values = [100.0]
    for index in range(1, len(CLOSES)):
        values.append(values[-1] * (1.0 + DAILY) * (1.0 + JUMP if index in jumps else 1.0))
    return tuple(values)


def _prices(jumps: dict[str, set[int]], version: str | None = "prices@1") -> ObservationFrame:
    series = {
        symbol: ObservationSeries(symbol, CLOSES, _path(days)) for symbol, days in jumps.items()
    }
    series["MKT"] = ObservationSeries("MKT", CLOSES, _path(set()))
    return ObservationFrame(FeatureField.CLOSE, series, "America/New_York", version)


AFTER_CLOSE = event(
    "earnings.release", "AAA", ny("2024-05-14 16:05"), ny("2024-05-14 16:05")
)  # reaches 2024-05-15
PRE_MARKET = event(
    "earnings.release", "BBB", ny("2024-05-21 08:00"), ny("2024-05-21 08:00")
)  # reaches 2024-05-21


def _definition(**changes: object) -> EventStudyDefinition:
    arguments: dict[str, object] = {
        "name": "earnings",
        "event_types": ("earnings.release",),
        "window": EventWindow(before=3, after=3, estimation=0, gap=0),
        "model": AbnormalReturnModel.MARKET_ADJUSTED,
        "benchmark": "MKT",
        "visibility": PUB,
    }
    arguments.update(changes)
    return EventStudyDefinition(**arguments)  # type: ignore[arg-type]


def _run(
    events: list[InformationEvent],
    prices: ObservationFrame | None = None,
    definition: EventStudyDefinition | None = None,
) -> EventStudyResult:
    frame = prices or _prices(
        {"AAA": {_index_of(date(2024, 5, 15))}, "BBB": {_index_of(date(2024, 5, 21))}}
    )
    return event_study(
        build_observation_set("events", events, SOURCE), frame, NYSE, definition or _definition()
    )


# --------------------------------------------------------------------------- #
# Anchoring
# --------------------------------------------------------------------------- #


def test_news_after_the_close_is_anchored_at_the_next_session() -> None:
    result = _run([AFTER_CLOSE])
    (outcome,) = result.outcomes

    assert outcome.timing is SessionTiming.AFTER_CLOSE
    assert outcome.tradable_at == ny("2024-05-15 09:30")
    assert outcome.anchor == ny("2024-05-15 16:00"), "not the close it arrived after"
    assert outcome.abnormal_returns[3] == pytest.approx(JUMP * (1 + DAILY), rel=1e-9)
    assert all(value == pytest.approx(0.0, abs=1e-12) for value in outcome.abnormal_returns[:3])
    assert outcome.pre_event == pytest.approx(0.0, abs=1e-12)
    assert outcome.post_event == pytest.approx(outcome.cumulative)


def test_pre_market_news_is_anchored_at_the_same_days_close() -> None:
    (outcome,) = _run([PRE_MARKET]).outcomes

    assert outcome.timing is SessionTiming.BEFORE_OPEN
    assert outcome.anchor == ny("2024-05-21 16:00")
    assert outcome.abnormal_returns[3] == pytest.approx(JUMP * (1 + DAILY), rel=1e-9)


def test_anchoring_on_the_occurrence_instant_would_have_missed_the_reaction() -> None:
    """The same prices, the same event, delivered by the feed an hour after the close
    of the day it happened: the reaction belongs to the next session."""

    delivered_late = event(
        "earnings.release", "AAA", ny("2024-05-14 15:00"), ny("2024-05-14 17:00")
    )

    (outcome,) = _run([delivered_late]).outcomes

    assert outcome.anchor == ny("2024-05-15 16:00")
    assert outcome.abnormal_returns[3] == pytest.approx(JUMP * (1 + DAILY), rel=1e-9)


def test_the_raw_model_includes_the_market_and_the_mean_model_removes_the_drift() -> None:
    raw = _run([AFTER_CLOSE], definition=_definition(model=AbnormalReturnModel.RAW, benchmark=None))
    mean_adjusted = _run(
        [AFTER_CLOSE],
        definition=_definition(
            model=AbnormalReturnModel.MEAN_ADJUSTED,
            benchmark=None,
            window=EventWindow(before=2, after=2, estimation=5, gap=1),
        ),
    )

    assert raw.outcomes[0].abnormal_returns[0] == pytest.approx(DAILY)
    assert mean_adjusted.outcomes[0].abnormal_returns[0] == pytest.approx(0.0, abs=1e-12)
    assert mean_adjusted.outcomes[0].abnormal_returns[2] == pytest.approx(
        (1 + DAILY) * (1 + JUMP) - 1 - DAILY, rel=1e-9
    )


# --------------------------------------------------------------------------- #
# Exclusions, each with its reason
# --------------------------------------------------------------------------- #


def test_every_event_that_cannot_be_measured_is_excluded_by_name() -> None:
    unknown = event("earnings.release", "AAA", ny("2024-06-04 12:00"), None)
    correction = event(
        "earnings.release", "AAA", ny("2024-05-14 16:05"), ny("2024-05-16 09:00"), revision=1
    )
    no_prices = event("earnings.release", "ZZZ", ny("2024-05-20 12:00"), ny("2024-05-20 12:00"))
    too_early = event("earnings.release", "BBB", ny("2024-05-01 12:00"), ny("2024-05-01 12:00"))
    too_late = event("earnings.release", "BBB", ny("2024-06-27 12:00"), ny("2024-06-27 12:00"))

    result = _run([AFTER_CLOSE, unknown, correction, no_prices, too_early, too_late])
    reasons = {entry.event_id: entry.reason for entry in result.excluded}

    assert [outcome.event_id for outcome in result.outcomes] == [AFTER_CLOSE.record_id]
    assert "never established" in reasons[unknown.record_id]
    assert "already anchored at its first report" in reasons[correction.record_id]
    assert "no series for 'ZZZ'" in reasons[no_prices.record_id]
    assert "starts before" in reasons[too_early.record_id]
    assert "ends after" in reasons[too_late.record_id]


def test_the_first_report_is_anchored_whichever_order_the_reports_arrive_in() -> None:
    correction = event(
        "earnings.release", "AAA", ny("2024-05-14 16:05"), ny("2024-05-16 09:00"), revision=1
    )

    forward = _run([AFTER_CLOSE, correction])
    backward = _run([correction, AFTER_CLOSE])

    assert forward.outcomes[0].event_id == AFTER_CLOSE.record_id
    assert forward.result_id == backward.result_id


def test_a_benchmark_gap_excludes_the_event_rather_than_inventing_a_return() -> None:
    frame = _prices({"AAA": {_index_of(date(2024, 5, 15))}})
    market = frame.series["MKT"]
    missing = _index_of(date(2024, 5, 13))
    holed = ObservationSeries(
        "MKT",
        market.timestamps[:missing] + market.timestamps[missing + 1 :],
        market.values[:missing] + market.values[missing + 1 :],
    )
    frame = replace(frame, series={**frame.series, "MKT": holed})

    with pytest.raises(ResearchValidationError, match="benchmark has no observation"):
        _run([AFTER_CLOSE], prices=frame)


def test_a_study_that_measured_nothing_is_refused() -> None:
    unknown = event("earnings.release", "AAA", ny("2024-06-04 12:00"), None)

    with pytest.raises(ResearchValidationError, match="No event could be measured"):
        _run([unknown])
    with pytest.raises(ResearchValidationError, match="holds no event of the types"):
        _run([event("macro.cpi", "US", 1.7e9, 1.7e9)])


def test_a_missing_benchmark_series_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="has no series"):
        _run([AFTER_CLOSE], definition=_definition(benchmark="SPX"))


# --------------------------------------------------------------------------- #
# The result
# --------------------------------------------------------------------------- #


def test_the_aggregate_carries_its_counts_and_its_clustering() -> None:
    same_day = event("earnings.release", "BBB", ny("2024-05-14 16:10"), ny("2024-05-14 16:10"))
    frame = _prices({"AAA": {_index_of(date(2024, 5, 15))}, "BBB": {_index_of(date(2024, 5, 15))}})

    result = _run([AFTER_CLOSE, same_day], prices=frame)

    assert len(result.outcomes) == 2
    assert result.shared_anchor_events == 2, "both reached the market on 2024-05-15"
    assert result.average_abnormal[3] == pytest.approx(JUMP * (1 + DAILY), rel=1e-9)
    assert result.positive_share == 1.0
    assert result.cumulative_std == pytest.approx(0.0, abs=1e-12)
    assert result.offsets == (-3, -2, -1, 0, 1, 2, 3)


def test_one_event_has_no_dispersion_rather_than_a_zero_one() -> None:
    result = _run([AFTER_CLOSE])

    assert result.cumulative_std is None
    assert "car_std" not in event_study_metrics(result)


def test_the_result_identity_is_derived_and_tamper_evident() -> None:
    events = [AFTER_CLOSE, PRE_MARKET]
    shuffled = events[:]
    random.Random(2).shuffle(shuffled)

    first = _run(events)
    second = _run(shuffled)

    assert first.result_id == second.result_id
    assert first.verify()
    assert not replace(first, prices_version="other@1").verify()
    assert _run(events, prices=_prices({"AAA": set(), "BBB": set()})).result_id != first.result_id


def test_the_metrics_are_flat_and_named_for_a_study_result() -> None:
    metrics = event_study_metrics(_run([AFTER_CLOSE, PRE_MARKET]))

    assert metrics["events"] == 2.0
    assert metrics["aar[+0]"] == pytest.approx(JUMP * (1 + DAILY), rel=1e-9)
    assert set(metrics) >= {"mean_car", "median_car", "positive_share", "car_std", "aar[-3]"}


# --------------------------------------------------------------------------- #
# The definition
# --------------------------------------------------------------------------- #


def test_the_definition_identity_is_derived_and_order_insensitive() -> None:
    first = _definition(event_types=("earnings.release", "macro.cpi"))
    second = _definition(event_types=("macro.cpi", "earnings.release"))

    assert first.study_id == second.study_id
    assert first.study_id.startswith("earnings@")
    assert _definition(visibility=VisibilityRule.INGESTION).study_id != _definition().study_id


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"name": "a@b"}, "without '@'"),
        ({"event_types": ()}, "must name the event types"),
        ({"event_types": ("x.y", "x.y")}, "repeat"),
        ({"benchmark": None}, "names none"),
        ({"model": AbnormalReturnModel.RAW}, "reads no benchmark"),
        (
            {
                "model": AbnormalReturnModel.MEAN_ADJUSTED,
                "benchmark": None,
                "window": EventWindow(1, 1, 1, 0),
            },
            "at least two returns",
        ),
        ({"window": EventWindow(1, 1, 5, 0)}, "reads no estimation window"),
    ],
)
def test_an_incoherent_definition_is_refused(changes: dict[str, object], match: str) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        _definition(**changes)


def test_a_negative_window_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="count of observations"):
        EventWindow(-1, 1, 0, 0)
