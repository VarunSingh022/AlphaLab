"""External information on the execution path, and fundamentals that stay comparable.

Ledger OFE-009 and OFE-011. A point-in-time record reaches a strategy as an
:class:`~alphalab.strategy.events.ObservationReceived` at its knowledge instant,
merged with the market records by instant; a stream grows record by record with
every vintage check the set makes; a per-share figure is restated across splits
by the corporate-action authority's changes, and a figure is converted at a rate
the caller's table holds, which is recorded.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.alt_data import (
    AltDataInputError,
    ExternalObservation,
    ObservationStream,
    ShareCountChange,
    StatementType,
    adjusted_for_share_changes,
    build_observation_set,
    converted_fundamental,
    delivery_schedule,
)
from alphalab.backtesting import BacktestEngine
from alphalab.common.point_in_time import PointInTimeStamp, VisibilityRule
from alphalab.data.feed import Split
from alphalab.factor_library import share_count_changes
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.fx import FutureDatedRateError, FxRate, FxRates
from alphalab.runtime import RuntimeValidationError
from alphalab.runtime.run import RunEngine
from alphalab.runtime.run_snapshot import capture, from_primitives
from alphalab.strategy import BaseStrategy, ObservationReceived, defines_on_observation
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, SliceClosed
from alphalab.strategy.subscription import Subscriptions, Topic
from tests.integration.harness import (
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)
from tests.unit.alt_data.pit_harness import SOURCE, SOURCE_V2, fundamental, observation, quarter

ASSET = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
PUBLICATION = VisibilityRule.PUBLICATION


def _readings(*known: tuple[str, float]) -> Any:
    """One reading per ``(subject, known_at)``, observed a second before it is known."""

    return build_observation_set(
        "readings",
        [
            observation(subject, "level", str(index), at - 1.0, at)
            for index, (subject, at) in enumerate(known)
        ],
        SOURCE,
    )


# --------------------------------------------------------------------------- #
# A stream grows, and checks each arrival
# --------------------------------------------------------------------------- #


def test_a_stream_becomes_the_set_its_records_make_whatever_order_they_arrived_in() -> None:
    records = [observation("ACME", "level", str(n), 10.0 + n, 11.0 + n) for n in range(5)]

    opened: ObservationStream[ExternalObservation] = ObservationStream.open("readings", SOURCE)
    stream = opened.append(records[3:]).append(records[:3])

    assert len(stream) == 5
    assert stream.to_set().version == build_observation_set("readings", records, SOURCE).version


def test_a_stream_refuses_what_a_set_refuses_and_is_left_unchanged() -> None:
    original = observation("ACME", "level", "1", 10.0, 11.0)
    opened: ObservationStream[ExternalObservation] = ObservationStream.open("readings", SOURCE)
    stream = opened.append([original])

    with pytest.raises(AltDataInputError, match="arrived twice"):
        stream.append([original])
    with pytest.raises(AltDataInputError, match="claim revision 0"):
        stream.append([observation("ACME", "level", "2", 10.0, 12.0)])
    with pytest.raises(AltDataInputError, match="is read from"):
        stream.append([observation("ACME", "level", "1", 20.0, 21.0, source=SOURCE_V2)])
    with pytest.raises(AltDataInputError, match="before revision 0"):
        stream.append([observation("ACME", "level", "1.1", 10.0, 10.5, revision=1)])
    assert len(stream) == 1

    revised = stream.append([observation("ACME", "level", "1.1", 10.0, 15.0, revision=2)])
    with pytest.raises(AltDataInputError, match="after revision 2"):
        revised.append([observation("ACME", "level", "1.05", 10.0, 16.0, revision=1)])


def test_a_stream_holds_one_kind() -> None:
    opened: ObservationStream[Any] = ObservationStream.open("readings", SOURCE)
    stream = opened.append([observation("ACME", "level", "1", 10.0, 11.0)])
    figure = fundamental(
        "ACME", StatementType.INCOME_STATEMENT, "revenue", quarter(2024, 1), "1000", 1_712_000_000.0
    )

    with pytest.raises(AltDataInputError, match="received a"):
        stream.append([figure])


# --------------------------------------------------------------------------- #
# A schedule orders records by when they could be known
# --------------------------------------------------------------------------- #


def test_a_schedule_orders_by_knowledge_and_counts_what_it_cannot_place() -> None:
    known_late = observation("ACME", "level", "1", 1.0, 30.0)
    known_early = observation("ACME", "level", "2", 2.0, 20.0)
    never = observation("ACME", "level", "3", 3.0, None)
    ingested = observation("ACME", "level", "4", 4.0, 10.0, ingested_at=40.0)
    obs_set = build_observation_set("readings", [known_late, known_early, never, ingested], SOURCE)

    published = delivery_schedule(obs_set, PUBLICATION)
    arrived = delivery_schedule(obs_set, VisibilityRule.INGESTION)

    assert [d.known_at for d in published] == [10.0, 20.0, 30.0]
    assert published.unverified == 1
    assert [d.known_at for d in arrived] == [40.0], "only one recorded its arrival"
    assert arrived.unverified == 3
    assert published.deliveries[0].delivery_id == f"{obs_set.version}:{ingested.record_id}"


# --------------------------------------------------------------------------- #
# On the execution path
# --------------------------------------------------------------------------- #


class _Reader(BaseStrategy):
    """Records what it is told, and buys ten on each observation when asked to."""

    def __init__(self, *, buy: bool = False) -> None:
        self.buy = buy
        self.log: list[tuple[str, float, str]] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self.log.append(("quote", event.quote.timestamp, ""))
        return ()

    def on_observation(
        self, context: StrategyContext, event: ObservationReceived
    ) -> Iterable[Intent]:
        self.log.append(("observation", event.timestamp, event.subject))
        if not self.buy:
            return ()
        return (Intent("A", ASSET, Decimal("10"), timestamp=event.timestamp),)

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        self.log.append(("slice", event.timestamp, ""))
        return ()


def _backtest(
    strategy: BaseStrategy, readings: Any, subscriptions: frozenset[str] = frozenset({"*"})
) -> Any:
    return BacktestEngine.run(
        backtest_config("A"),
        dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101"), Decimal("102")]),
        running_strategy_state("A", strategy, subscriptions),
        context_factory,
        observations=delivery_schedule(readings, PUBLICATION),
    )


def test_an_observation_arrives_between_the_records_it_falls_between() -> None:
    strategy = _Reader()
    result = _backtest(strategy, _readings(("ACME", 2.5)))  # quotes at 2, 3 and 4

    assert strategy.log == [
        ("quote", 2.0, ""),
        ("slice", 2.0, ""),
        ("observation", 2.5, "ACME"),
        ("quote", 3.0, ""),
        ("slice", 3.0, ""),
        ("quote", 4.0, ""),
        ("slice", 4.0, ""),
    ]
    assert result.run.observations_delivered == 1


def test_one_known_at_a_records_instant_comes_after_it_and_before_its_slice() -> None:
    strategy = _Reader()
    _backtest(strategy, _readings(("ACME", 3.0)))

    assert strategy.log[2:5] == [
        ("quote", 3.0, ""),
        ("observation", 3.0, "ACME"),
        ("slice", 3.0, ""),
    ]


def test_an_order_decided_on_an_observation_fills_at_a_later_price() -> None:
    result = _backtest(_Reader(buy=True), _readings(("ACME", 3.0)))

    (fill,) = result.fills
    assert fill.price == Decimal("102"), "the next quote after 3.0, not the quote at 3.0"
    assert fill.filled_at == 4.0


def test_one_known_after_the_data_ends_is_not_delivered() -> None:
    strategy = _Reader()
    result = _backtest(strategy, _readings(("ACME", 2.5), ("ACME", 99.0)))

    assert result.run.observations_delivered == 1
    assert [entry for entry in strategy.log if entry[0] == "observation"] == [
        ("observation", 2.5, "ACME")
    ]


def test_a_subscription_can_name_one_subject() -> None:
    strategy = _Reader()
    _backtest(
        strategy,
        _readings(("ACME", 2.5), ("OTHER", 3.5)),
        frozenset({"quotes", "observations:OTHER"}),
    )

    assert [entry for entry in strategy.log if entry[0] == "observation"] == [
        ("observation", 3.5, "OTHER")
    ]
    parsed = Subscriptions.parse(["observations:OTHER"])
    assert parsed.accepts(Topic.OBSERVATIONS, "OTHER")
    assert not parsed.accepts(Topic.OBSERVATIONS, "ACME")


def test_a_strategy_without_the_hook_is_never_built_a_context_for_one() -> None:
    class Plain(BaseStrategy):
        pass

    assert not defines_on_observation(Plain())
    assert defines_on_observation(_Reader())

    config = backtest_config("A")
    state = RunEngine.initialize(config, running_strategy_state("A", Plain()))
    (delivery,) = delivery_schedule(_readings(("ACME", 2.5)), PUBLICATION)
    delivered = RunEngine.deliver_observation(state, delivery, context_factory)

    assert delivered.pipeline is state.pipeline, "nothing dispatched, nothing built"
    assert delivered.observations_delivered == 1


def test_a_delivery_is_made_once_and_never_into_the_past() -> None:
    strategy = _Reader()
    config = backtest_config("A")
    state = RunEngine.initialize(config, running_strategy_state("A", strategy))
    first, second = delivery_schedule(_readings(("ACME", 2.5), ("ACME", 3.5)), PUBLICATION)

    state = RunEngine.deliver_observation(state, second, context_factory)
    again = RunEngine.deliver_observation(state, second, context_factory)
    earlier = RunEngine.deliver_observation(state, first, context_factory)

    assert again is state and earlier is state
    assert state.last_observation == second.order_key
    record = dataset_of_quotes(ASSET, [Decimal("100")], first_timestamp=5.0).records[0]
    moved, _ = RunEngine.advance(state, record, context_factory)
    late = replace(second, known_at=4.0, delivery_id="LATE:x")
    with pytest.raises(RuntimeValidationError, match="market has already moved past"):
        RunEngine.deliver_observation(moved, late, context_factory)


def test_a_live_run_delivers_a_late_record_when_it_arrived() -> None:
    strategy = _Reader()
    state = RunEngine.initialize(backtest_config("A"), running_strategy_state("A", strategy))
    (delivery,) = delivery_schedule(_readings(("ACME", 2.5)), PUBLICATION)

    state = RunEngine.deliver_observation(state, delivery, context_factory, now=7.0)

    assert strategy.log == [("observation", 7.0, "ACME")]
    assert state.current_timestamp == 7.0


def test_the_cursor_survives_a_run_snapshot_and_a_version_3_payload_delivered_nothing() -> None:
    strategy = _Reader()
    result = _backtest(strategy, _readings(("ACME", 2.5), ("ACME", 3.5)))

    payload = deserialize(serialize(capture(result.run)))
    assert payload["observations_delivered"] == 2
    assert payload["last_observation"][0] == 3.5
    assert from_primitives(payload).last_observation == result.run.last_observation

    payload["schema_version"] = 3
    del payload["observations_delivered"], payload["last_observation"]
    upgraded = from_primitives(payload)
    assert (upgraded.observations_delivered, upgraded.last_observation) == (0, None)


# --------------------------------------------------------------------------- #
# Fundamentals across a split, and in another currency
# --------------------------------------------------------------------------- #

PUBLISHED = 1_714_000_000.0  # after Q1 2024 closed
SPLIT_AT = 1_720_000_000.0
EPS = fundamental(
    "ACME",
    StatementType.INCOME_STATEMENT,
    "eps_diluted",
    quarter(2024, 1),
    "3.00",
    PUBLISHED,
    unit="USD/share",
)
SHARES = fundamental(
    "ACME",
    StatementType.BALANCE_SHEET,
    "shares_outstanding",
    quarter(2024, 1),
    "1000",
    PUBLISHED,
    unit="shares",
)
REVENUE = fundamental(
    "ACME", StatementType.INCOME_STATEMENT, "revenue", quarter(2024, 1), "1500", PUBLISHED
)
THREE_FOR_ONE = ShareCountChange("ACME", SPLIT_AT, Decimal("3"), "corporate-actions-feed")


def test_a_per_share_figure_is_restated_in_the_shares_of_the_instant() -> None:
    before = adjusted_for_share_changes(EPS, [THREE_FOR_ONE], SPLIT_AT - 1)
    after = adjusted_for_share_changes(EPS, [THREE_FOR_ONE], SPLIT_AT)

    assert (before.value, before.applied) == (Decimal("3.00"), ())
    assert after.value == Decimal("1.00"), "one division, not a rounded reciprocal"
    assert after.applied == (THREE_FOR_ONE,)
    assert adjusted_for_share_changes(SHARES, [THREE_FOR_ONE], SPLIT_AT).value == Decimal("3000")


def test_a_split_before_publication_or_of_another_issuer_is_not_applied() -> None:
    earlier = ShareCountChange("ACME", PUBLISHED - 1, Decimal("2"), "feed")
    elsewhere = ShareCountChange("OTHER", SPLIT_AT, Decimal("2"), "feed")

    unchanged = adjusted_for_share_changes(EPS, [earlier, elsewhere], SPLIT_AT + 1)

    assert unchanged.value == Decimal("3.00") and unchanged.applied == ()


def test_an_amount_of_money_does_not_change_with_the_share_count() -> None:
    with pytest.raises(AltDataInputError, match="per-share unit"):
        adjusted_for_share_changes(REVENUE, [THREE_FOR_ONE], SPLIT_AT)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: ShareCountChange("ACME", 1.0, Decimal("1"), "feed"), "not one"),
        (lambda: ShareCountChange("ACME", 1.0, Decimal("-2"), "feed"), "positive"),
        (lambda: ShareCountChange("ACME", 1.0, Decimal("2"), "feed", known_at=2.0), "after it"),
        (lambda: ShareCountChange("ACME", 1.0, Decimal("2"), " "), "source"),
    ],
)
def test_a_malformed_share_count_change_is_refused(build: Any, message: str) -> None:
    with pytest.raises(AltDataInputError, match=message):
        build()


def test_the_data_layers_splits_are_read_exactly() -> None:
    changes = share_count_changes(
        [Split("ACME.N", SPLIT_AT, 1.5), Split("XYZ", 5.0, 2.0)],
        source="corporate-actions-feed",
        subjects={"ACME.N": "ACME"},
    )

    assert [(change.subject, change.ratio) for change in changes] == [
        ("XYZ", Decimal("2.0")),
        ("ACME", Decimal("1.5")),
    ]
    assert adjusted_for_share_changes(EPS, changes, SPLIT_AT).value == Decimal("2.00")


RATES = FxRates().with_rate(
    FxRate("USD", "EUR", Decimal("0.9"), as_of=PUBLISHED, source="ecb-reference")
)


def test_a_figure_is_converted_at_a_recorded_rate() -> None:
    converted = converted_fundamental(REVENUE, "EUR", RATES, PUBLISHED + 10)
    per_share = converted_fundamental(EPS, "EUR", RATES, PUBLISHED + 10)

    assert (converted.value, converted.unit) == (Decimal("1350.0"), "EUR")
    assert (converted.rate, converted.rate_pair) == (Decimal("0.9"), ("USD", "EUR"))
    assert (converted.rate_as_of, converted.rate_source) == (PUBLISHED, "ecb-reference")
    assert (per_share.value, per_share.unit) == (Decimal("2.700"), "EUR/share")
    same = converted_fundamental(REVENUE, "USD", RATES, PUBLISHED)
    assert (same.value, same.rate) == (Decimal("1500"), None)


def test_a_conversion_refuses_what_has_no_currency_and_a_rate_from_the_future() -> None:
    with pytest.raises(AltDataInputError, match="no currency"):
        converted_fundamental(SHARES, "EUR", RATES, PUBLISHED)
    with pytest.raises(FutureDatedRateError):
        converted_fundamental(REVENUE, "EUR", RATES, PUBLISHED - 1)


def test_a_stamp_is_still_the_only_authority_on_when() -> None:
    """Nothing here derived an instant: the schedule read each record's own stamp."""

    record = observation("ACME", "level", "1", 1.0, 5.0)
    assert record.stamp == PointInTimeStamp.declared(1.0, 5.0)
    (delivery,) = delivery_schedule(build_observation_set("r", [record], SOURCE), PUBLICATION)
    assert delivery.known_at == record.stamp.known_at(PUBLICATION) == 5.0
