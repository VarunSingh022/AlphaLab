"""Trading calendar infrastructure and resolution logic."""

from datetime import UTC, datetime
from typing import Protocol


class HolidayCalendarProtocol(Protocol):
    """Protocol for resolving external exchange holidays."""

    def is_holiday(self, timestamp: float) -> bool: ...


class TradingCalendar:
    """Stateless trading calendar utilities."""

    @staticmethod
    def is_weekend(timestamp: float) -> bool:
        """Determines if a given Unix timestamp falls on a UTC weekend."""
        dt = datetime.fromtimestamp(timestamp, tz=UTC)
        return dt.weekday() >= 5  # 5 = Saturday, 6 = Sunday

    @staticmethod
    def is_trading_day(
        timestamp: float, holiday_calendar: HolidayCalendarProtocol | None = None
    ) -> bool:
        """Determines if a timestamp is a valid trading day."""
        return not (
            TradingCalendar.is_weekend(timestamp)
            or (holiday_calendar is not None and holiday_calendar.is_holiday(timestamp))
        )

    @staticmethod
    def next_trading_session(
        current_timestamp: float, holiday_calendar: HolidayCalendarProtocol | None = None
    ) -> float:
        """Calculates the start of the next valid trading day (aligned to UTC midnight)."""
        next_ts = current_timestamp + 86400.0
        while not TradingCalendar.is_trading_day(next_ts, holiday_calendar):
            next_ts += 86400.0

        next_dt = datetime.fromtimestamp(next_ts, tz=UTC)
        start_of_day = datetime(next_dt.year, next_dt.month, next_dt.day, tzinfo=UTC)
        return start_of_day.timestamp()

    @staticmethod
    def previous_trading_session(
        current_timestamp: float, holiday_calendar: HolidayCalendarProtocol | None = None
    ) -> float:
        """Calculates the start of the previous valid trading day."""
        prev_ts = current_timestamp - 86400.0
        while not TradingCalendar.is_trading_day(prev_ts, holiday_calendar):
            prev_ts -= 86400.0

        prev_dt = datetime.fromtimestamp(prev_ts, tz=UTC)
        start_of_day = datetime(prev_dt.year, prev_dt.month, prev_dt.day, tzinfo=UTC)
        return start_of_day.timestamp()
