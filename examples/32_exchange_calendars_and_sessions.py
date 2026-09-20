"""
AlphaLab Examples
=================

Example 32 : Exchange Calendars and Sessions

Difficulty : Intermediate

Estimated Time : 8 minutes

Prerequisites
-------------

✓ Example 31 (global market conventions)

Topics
------

• Four venues, four timezones, four session shapes
• One instant, four different local days and four different answers
• Lunch breaks, overnight sessions and a market that never closes
• Daylight saving: the wall clock holds, the UTC instant moves
• Trading-day arithmetic, and the settlement date it produces

What this shows
---------------

A timestamp is an instant; a *session* is a local fact. Which trading day an
instant belongs to depends entirely on the venue, and the four calendars below
disagree about the same moment in every way it is possible to disagree.

AlphaLab ships **no holiday data**. Every calendar here is declared by this
example, including the one holiday it uses to show a settlement date moving.

Run

    python examples/32_exchange_calendars_and_sessions.py
"""

from datetime import UTC, date, datetime, time

from alphalab.conventions import SettlementBasis, SettlementRule, settlement_date
from alphalab.data.calendar import MarketCalendar, SessionWindow

NYSE = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
)
TOKYO = MarketCalendar(
    calendar_id="XTKS",
    timezone_name="Asia/Tokyo",
    weekly_sessions=dict.fromkeys(
        range(5),
        (SessionWindow(time(9, 0), time(11, 30)), SessionWindow(time(12, 30), time(15, 0))),
    ),
)
CME = MarketCalendar(
    calendar_id="XCME",
    timezone_name="America/Chicago",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(17, 0), time(16, 0)),)),
)
BINANCE = MarketCalendar.continuous("BINANCE", "UTC")

VENUES = (NYSE, TOKYO, CME, BINANCE)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def at(month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC).timestamp()


def main() -> None:
    print("=" * 68)
    print("Example 32 : Exchange Calendars and Sessions")
    print("=" * 68)

    # ----------------------------------------------------------------- #
    rule("Four venues, declared")

    for venue in VENUES:
        declared = venue.windows_on(date(2026, 3, 16))
        shape = ", ".join(f"{w.opens:%H:%M}-{w.closes:%H:%M}" for w in declared)
        overnight = " (overnight)" if any(w.crosses_midnight for w in declared) else ""
        print(f"  {venue.calendar_id:<9} {venue.timezone_name:<18} {shape}{overnight}")

    # ----------------------------------------------------------------- #
    rule("One instant, four local days")

    instant = at(3, 16, 21, 30)
    print(f"  {datetime.fromtimestamp(instant, tz=UTC):%Y-%m-%d %H:%M} UTC reads as:")
    for venue in VENUES:
        local = venue.local_datetime(instant)
        state = "OPEN " if venue.is_open(instant) else "shut "
        session_day = venue.trading_day_of(instant)
        print(
            f"    {venue.calendar_id:<9} {local:%Y-%m-%d %H:%M}  {state}"
            f" session day: {session_day if session_day else '-'}"
        )
    print()
    print("  Four answers to one question. Chicago is inside its daily maintenance")
    print("  break; Tokyo has not opened; New York has closed; Binance never does.")

    # ----------------------------------------------------------------- #
    rule("An overnight session belongs to the day it opened on")

    two_am = at(3, 17, 7)  # 02:00 in Chicago
    local = CME.local_datetime(two_am)
    print(f"  wall clock in Chicago : {local:%Y-%m-%d %H:%M}")
    print(f"  open                  : {CME.is_open(two_am)}")
    print(f"  session it belongs to : {CME.trading_day_of(two_am)}")
    print()
    print("  Which is what the venue's own daily statement says: a trade at 02:00")
    print("  settles against the session that started the previous evening.")

    # ----------------------------------------------------------------- #
    rule("A lunch break is two windows, not one long one")

    day = date(2026, 3, 16)
    bounds = TOKYO.session_bounds(day)
    spans = TOKYO.session_windows_on(day)
    assert bounds is not None
    print(
        f"  envelope : {datetime.fromtimestamp(bounds[0], tz=UTC):%H:%M} to "
        f"{datetime.fromtimestamp(bounds[1], tz=UTC):%H:%M} UTC"
    )
    for index, (window_open, window_close) in enumerate(spans, start=1):
        print(
            f"  window {index} : {datetime.fromtimestamp(window_open, tz=UTC):%H:%M} to "
            f"{datetime.fromtimestamp(window_close, tz=UTC):%H:%M} UTC"
        )
    noon = at(3, 16, 3)  # 12:00 in Tokyo
    print(f"\n  12:00 Tokyo is inside the envelope and the market is open: {TOKYO.is_open(noon)}")

    # ----------------------------------------------------------------- #
    rule("Daylight saving: the wall clock holds, the instant moves")

    for label, session_date in (("before", date(2026, 3, 6)), ("after ", date(2026, 3, 9))):
        span = NYSE.session_bounds(session_date)
        assert span is not None
        opened = span[0]
        print(
            f"  {label} the change  {session_date}  "
            f"local {NYSE.local_datetime(opened):%H:%M}  "
            f"UTC {datetime.fromtimestamp(opened, tz=UTC):%H:%M}"
        )
    print()
    print("  The open stays 09:30 in New York and moves an hour in UTC. A research")
    print("  path that pinned the UTC hour would silently start reading an hour")
    print("  of the wrong session twice a year.")

    # ----------------------------------------------------------------- #
    rule("Trading days, and the settlement date they produce")

    friday = date(2026, 3, 13)
    holiday = date(2026, 3, 16)
    with_holiday = MarketCalendar(
        calendar_id="XNYS+holiday",
        timezone_name="America/New_York",
        weekly_sessions=NYSE.weekly_sessions,
        holidays=frozenset({holiday}),
    )

    t_plus_one = SettlementRule(SettlementBasis.TRADING_DAYS, 1)
    t_plus_two_calendar = SettlementRule(SettlementBasis.CALENDAR_DAYS, 2)

    print(f"  trade date            : {friday} (a Friday)")
    print(f"  T+1 trading days      : {settlement_date(t_plus_one, friday, NYSE)}")
    print(f"  T+1 with a Monday off : {settlement_date(t_plus_one, friday, with_holiday)}")
    print(
        f"  T+2 calendar days     : {settlement_date(t_plus_two_calendar, friday, None)} "
        "(a Sunday -- which is why the basis is named)"
    )
    print(f"  T+1 on a 24/7 venue   : {settlement_date(t_plus_one, friday, BINANCE)}")

    # ----------------------------------------------------------------- #
    rule("A trading-day count is the venue's own")

    print(f"  {'venue':<9} {'+1':<12} {'+5':<12} sessions in March 2026")
    print("  " + "-" * 52)
    for venue in VENUES:
        plus_one = venue.add_trading_days(friday, 1)
        plus_five = venue.add_trading_days(friday, 5)
        count = len(venue.sessions_between(date(2026, 3, 1), date(2026, 3, 31)))
        print(f"  {venue.calendar_id:<9} {plus_one!s:<12} {plus_five!s:<12} {count}")

    print("\n" + "=" * 68)
    print("Example 32 complete.")


if __name__ == "__main__":
    main()
