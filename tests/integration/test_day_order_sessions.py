"""A simulated day order expires when its venue's trading day ends (ledger EXE-010).

Until v3.12 the execution pipeline held no calendar, so a simulated resting day
order had to state its own close as ``expire_at`` and v3.11 refused one that did
not. ``ExecutionPipelineConfig.calendars`` declares the calendar of each listing
venue; the pipeline gives such an order the last close of its trading day, by
:meth:`~alphalab.data.calendar.MarketCalendar.day_order_expiry`, and still refuses
-- naming the venue -- when no calendar is declared for it.

The clock here is small Unix seconds: 1970-01-01, a Thursday, from 00:00:00 UTC.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.core.enums import AssetType, OrderStatus
from alphalab.data.calendar import CONTINUOUS_SESSION, MarketCalendar, SessionWindow
from alphalab.instrument import InstrumentRecord
from alphalab.market.quote import Quote
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.calendars import VenueCalendars, venue_calendars_from_primitives
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, OrderEvent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import context_factory, pipeline_config, registry_of

THURSDAY = 3
FRIDAY = 4
DAY = 86_400.0
LISTED = InstrumentRecord("AAA", AssetType.EQUITY, "XNYS", "USD")
OTHER = InstrumentRecord("BBB", AssetType.EQUITY, "XTKS", "USD")
UNLISTED = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def _window(opens: int, closes: int) -> SessionWindow:
    """A window from ``opens`` to ``closes`` seconds past local midnight."""

    return SessionWindow(time(0, opens // 60, opens % 60), time(0, closes // 60, closes % 60))


#: Thursday and Friday trade from 00:00:00 to 00:01:00 UTC.
MINUTE = MarketCalendar(
    "MINUTE",
    "UTC",
    {THURSDAY: (_window(0, 60),), FRIDAY: (_window(0, 60),)},
)
#: Thursday breaks for lunch from 00:00:30 to 00:00:40.
LUNCH = MarketCalendar(
    "LUNCH",
    "UTC",
    {THURSDAY: (_window(0, 30), _window(40, 60)), FRIDAY: (_window(0, 60),)},
)
#: Thursday trades until 00:02:00.
LONGER = MarketCalendar("LONGER", "UTC", {THURSDAY: (_window(0, 120),)})


class _Asker(BaseStrategy):
    """Places one planned order per timestamp and records what it is told back."""

    def __init__(self, asset_id: str, plan: Mapping[float, OrderTerms]) -> None:
        self.asset_id = asset_id
        self.plan = dict(plan)
        self.rejections: list[str] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        terms = self.plan.get(event.quote.timestamp)
        if terms is None:
            return ()
        return (
            Intent("A", self.asset_id, Decimal("10"), terms=terms, timestamp=event.quote.timestamp),
        )

    def on_order(self, context: StrategyContext, event: OrderEvent) -> Iterable[Intent]:
        if event.status == OrderStatus.REJECTED.value:
            self.rejections.append(event.reason)
        return ()


def _runtime(strategy: BaseStrategy) -> RuntimeState:
    runtime = register_strategy(create_runtime(), "A", strategy)
    entry = runtime.strategies["A"]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(runtime, strategies={"A": entry})


def _pipeline(
    strategy: _Asker, calendars: VenueCalendars, *, registered: bool = False
) -> ExecutionPipelineState:
    config = replace(
        pipeline_config("A"),
        calendars=calendars,
        instruments=registry_of(LISTED, OTHER) if registered else None,
    )
    return ExecutionPipeline.initialize(config, _runtime(strategy), 1.0)


def _quote(
    state: ExecutionPipelineState, asset_id: str, at: float, bid: str = "99.9", ask: str = "100.1"
) -> ExecutionPipelineResult:
    quote = Quote(
        asset_id, at, Decimal(bid), Decimal(ask), Decimal("1000"), Decimal("1000"), "X", "USD"
    )
    return ExecutionPipeline.process_quote(state, quote, context_factory)


def _order(state: ExecutionPipelineState) -> Any:
    (order,) = state.oms.orders.orders()
    return order


DAY_LIMIT = OrderTerms.limit(Decimal("99"), TimeInForce.DAY)


# --------------------------------------------------------------------------- #
# The calendar's answer
# --------------------------------------------------------------------------- #


def test_a_day_order_is_good_for_the_whole_trading_day_not_the_window_in_progress() -> None:
    assert LUNCH.next_close(2.0) == 30.0, "the morning window ends at 00:00:30"
    assert LUNCH.day_order_expiry(2.0) == 60.0, "the day ends at 00:01:00"
    assert LUNCH.day_order_expiry(35.0) == 60.0, "placed over lunch: the same day"


def test_a_day_order_placed_while_the_market_is_shut_is_good_for_the_next_trading_day() -> None:
    assert MINUTE.day_order_expiry(60.0) == DAY + 60.0, "at Thursday's close: Friday"
    assert MINUTE.day_order_expiry(70.0) == DAY + 60.0
    assert MINUTE.day_order_expiry(DAY + 60.0) == 7 * DAY + 60.0, "Friday's close: next Thursday"


def test_a_holiday_moves_the_day_and_a_half_day_shortens_it() -> None:
    thursday = date(1970, 1, 1)
    friday = date(1970, 1, 2)
    holiday = replace(MINUTE, holidays=frozenset({thursday}))
    half_day = replace(MINUTE, special_sessions={thursday: (_window(0, 20),)})

    assert holiday.day_order_expiry(2.0) == DAY + 60.0
    assert half_day.day_order_expiry(2.0) == 20.0
    assert replace(MINUTE, holidays=frozenset({thursday, friday})).day_order_expiry(2.0) == (
        7 * DAY + 60.0
    )


def test_an_overnight_session_belongs_to_the_day_it_opened() -> None:
    # Thursday 23:00 to Friday 22:00 local, in UTC.
    overnight = MarketCalendar("NIGHT", "UTC", {THURSDAY: (SessionWindow(time(23), time(22)),)})

    assert overnight.day_order_expiry(23 * 3600.0 + 5) == DAY + 22 * 3600.0
    assert overnight.day_order_expiry(DAY + 3600.0) == DAY + 22 * 3600.0


def test_a_continuous_market_ends_its_day_at_local_midnight() -> None:
    crypto = MarketCalendar.continuous("CRYPTO", "UTC")
    tokyo = MarketCalendar.continuous("CRYPTO-JST", "Asia/Tokyo")

    assert crypto.day_order_expiry(2.0) == DAY
    assert CONTINUOUS_SESSION.crosses_midnight
    # 00:00:02 UTC is 09:00:02 in Tokyo; its day ends at 15:00 UTC.
    assert tokyo.day_order_expiry(2.0) == 15 * 3600.0


def test_a_calendar_with_no_session_in_reach_has_no_day_to_offer() -> None:
    assert MarketCalendar("NEVER", "UTC", {}).day_order_expiry(2.0) is None


# --------------------------------------------------------------------------- #
# The pipeline reads it
# --------------------------------------------------------------------------- #


def test_a_simulated_day_order_expires_at_its_day_end_and_frees_its_capital() -> None:
    strategy = _Asker(UNLISTED, {2.0: DAY_LIMIT})
    state = _quote(_pipeline(strategy, VenueCalendars(default=LUNCH)), UNLISTED, 2.0).state

    order = _order(state)
    assert (order.status, order.expire_at) == (OrderStatus.ACCEPTED, 60.0)
    assert order.time_in_force is TimeInForce.DAY
    assert state.allocation.reservations

    over_lunch = _quote(state, UNLISTED, 35.0).state
    assert _order(over_lunch).status is OrderStatus.ACCEPTED, "a lunch break is not the close"
    expired = _quote(over_lunch, UNLISTED, 60.0, "80", "81").state  # would cross; too late

    assert _order(expired).status is OrderStatus.EXPIRED
    assert expired.allocation.reservations == {}
    assert expired.portfolio.positions.get(UNLISTED) is None
    assert strategy.rejections == []


def test_without_a_declared_calendar_a_simulated_day_order_is_refused_saying_why() -> None:
    unlisted = _Asker(UNLISTED, {2.0: DAY_LIMIT})
    refused = _quote(_pipeline(unlisted, VenueCalendars()), UNLISTED, 2.0)

    assert refused.oms_orders == ()
    assert refused.state.allocation.reservations == {}
    (reason,) = unlisted.rejections
    assert "its listing venue is not known" in reason
    assert "ExecutionPipelineConfig.calendars" in reason

    listed = _Asker(OTHER.asset_id, {2.0: DAY_LIMIT})
    only_nyse = VenueCalendars(by_exchange={"XNYS": MINUTE})
    _quote(_pipeline(listed, only_nyse, registered=True), OTHER.asset_id, 2.0)
    (reason,) = listed.rejections
    assert "its listing venue XTKS has no calendar declared" in reason


def test_each_instrument_reads_its_own_listing_venues_calendar() -> None:
    calendars = VenueCalendars(by_exchange={"xnys ": LONGER}, default=MINUTE)
    assert set(calendars.by_exchange) == {"XNYS"}, "keyed as InstrumentRecord keys exchange"

    on_nyse = _Asker(LISTED.asset_id, {2.0: DAY_LIMIT})
    state = _quote(_pipeline(on_nyse, calendars, registered=True), LISTED.asset_id, 2.0).state
    assert _order(state).expire_at == 120.0, "XNYS's own calendar"

    on_tokyo = _Asker(OTHER.asset_id, {2.0: DAY_LIMIT})
    state = _quote(_pipeline(on_tokyo, calendars, registered=True), OTHER.asset_id, 2.0).state
    assert _order(state).expire_at == 60.0, "XTKS has none declared: the default"


def test_a_stated_close_is_kept_and_other_lifetimes_read_no_calendar() -> None:
    stated = OrderTerms.limit(Decimal("99"), TimeInForce.DAY, expire_at=5.0)
    gtc = OrderTerms.limit(Decimal("99"), TimeInForce.GTC)
    gtd = OrderTerms.limit(Decimal("99"), TimeInForce.GTD, expire_at=50.0)
    market = OrderTerms.market(TimeInForce.DAY)

    for terms, expected in ((stated, 5.0), (gtc, None), (gtd, 50.0)):
        strategy = _Asker(UNLISTED, {2.0: terms})
        state = _quote(_pipeline(strategy, VenueCalendars(default=MINUTE)), UNLISTED, 2.0).state
        assert _order(state).expire_at == expected, terms
    # A market order does not rest, so it needs no close and none is invented.
    strategy = _Asker(UNLISTED, {2.0: market})
    filled = _quote(_pipeline(strategy, VenueCalendars()), UNLISTED, 2.0)
    assert [order.expire_at for order in filled.oms_orders] == [None]
    assert strategy.rejections == []


# --------------------------------------------------------------------------- #
# Declaration and persistence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: VenueCalendars(by_exchange={"": MINUTE}), "key is invalid"),
        (lambda: VenueCalendars(by_exchange={"X N": MINUTE}), "key is invalid"),
        (lambda: VenueCalendars(by_exchange={"xnys": MINUTE, "XNYS": LUNCH}), "Two venue"),
        (lambda: VenueCalendars(by_exchange={"XNYS": "MINUTE"}), "not a MarketCalendar"),  # type: ignore[dict-item]
        (lambda: VenueCalendars(default="MINUTE"), "not a MarketCalendar"),  # type: ignore[arg-type]
    ],
)
def test_a_malformed_declaration_is_refused(build: Any, message: str) -> None:
    with pytest.raises(RuntimeValidationError, match=message):
        build()


def test_the_declared_calendars_survive_a_snapshot_exactly() -> None:
    thursday = date(1970, 1, 1)
    calendars = VenueCalendars(
        by_exchange={
            "XNYS": replace(
                LUNCH,
                holidays=frozenset({date(1970, 1, 8), date(1970, 1, 15)}),
                special_sessions={thursday: (_window(0, 20), _window(25, 50))},
            ),
            "XTKS": MarketCalendar.continuous("CRYPTO-JST", "Asia/Tokyo"),
        },
        default=MINUTE,
    )
    strategy = _Asker(LISTED.asset_id, {2.0: DAY_LIMIT})
    state = _quote(_pipeline(strategy, calendars, registered=True), LISTED.asset_id, 2.0).state

    payload = deserialize(serialize(capture(state)))
    assert payload["config"]["calendars"]["by_exchange"]["XNYS"]["holidays"] == [
        "1970-01-08",
        "1970-01-15",
    ]
    restored = restore(
        from_primitives(payload),
        RuntimeObjects(
            sizing_model=state.config.sizing_model,
            simulator=state.config.simulator,
            instruments=state.config.instruments,
            strategies={"A": strategy},
        ),
    )

    assert restored.config.calendars == calendars
    assert serialize(capture(restored)) == serialize(capture(state))
    # The restored run keeps expiring on the same day.
    expired = _quote(restored, LISTED.asset_id, 50.0).state
    assert _order(expired).status is OrderStatus.EXPIRED


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.pop("default"), "default"),
        (lambda p: p["default"].update(timezone_name="Mars/Base"), "not a valid calendar"),
        (lambda p: p["default"]["weekly_sessions"].update({"7": []}), "weekday '7'"),
        (lambda p: p["default"]["weekly_sessions"].update({"3": [["00:00:00"]]}), "opens, closes"),
        (lambda p: p["default"].update(holidays=["Thursday"]), "not an ISO date"),
        (lambda p: p["by_exchange"].update({"": p["default"]}), "could not be declared"),
    ],
)
def test_a_malformed_calendar_payload_is_refused_naming_the_field(
    mutate: Any, message: str
) -> None:
    payload = deserialize(serialize(VenueCalendars(default=MINUTE)))
    mutate(payload)

    with pytest.raises(StateDecodeError, match=message):
        venue_calendars_from_primitives(payload, "config.calendars")
