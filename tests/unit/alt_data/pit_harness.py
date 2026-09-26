"""Shared fixtures for the v3.7 point-in-time tests.

Real instants (2024, New York and Tokyo wall clocks) and real
:class:`~alphalab.data.calendar.MarketCalendar` venues, so that "after the
close" and "before the open" are the venue's own statements rather than
numbers chosen to make a test pass. Nothing here reads a clock or draws a
random number.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time
from decimal import Decimal

from alphalab.alt_data import (
    ExternalObservation,
    FiscalPeriod,
    FundamentalObservation,
    InformationEvent,
    ObservationSet,
    ObservationSource,
    ReferencePeriod,
    StatementType,
    build_observation_set,
)
from alphalab.common import PointInTimeStamp
from alphalab.data.calendar import MarketCalendar, SessionWindow

#: A source whose records were read from bytes with this digest.
SOURCE = ObservationSource(
    source_id="panel.test_feed",
    version="2024.1",
    content_hash="a" * 64,
    retrieved_at=1_730_000_000.0,
)

#: A second version of the same source.
SOURCE_V2 = ObservationSource(
    source_id="panel.test_feed",
    version="2024.2",
    content_hash="b" * 64,
    retrieved_at=1_730_000_000.0,
)

#: New York regular session, with Independence Day 2024 as a holiday.
NYSE = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
    holidays=frozenset({date(2024, 7, 4)}),
)

#: Tokyo, with its lunch break as two windows.
TSE = MarketCalendar(
    calendar_id="XTKS",
    timezone_name="Asia/Tokyo",
    weekly_sessions=dict.fromkeys(
        range(5),
        (SessionWindow(time(9, 0), time(11, 30)), SessionWindow(time(12, 30), time(15, 0))),
    ),
)


def ny(text: str) -> float:
    """``"2024-05-01 16:05"`` in New York, as Unix seconds."""

    return datetime.fromisoformat(text).replace(tzinfo=NYSE_ZONE).timestamp()


def tokyo(text: str) -> float:
    """``"2024-05-01 12:00"`` in Tokyo, as Unix seconds."""

    return datetime.fromisoformat(text).replace(tzinfo=TSE_ZONE).timestamp()


# Zones are resolved by the calendars' own authority, never constructed here.
NYSE_ZONE = NYSE.local_datetime(0.0).tzinfo
TSE_ZONE = TSE.local_datetime(0.0).tzinfo


def observation(
    subject: str,
    metric: str,
    value: str,
    observed_at: float,
    available_at: float | None,
    *,
    revision: int = 0,
    category: str = "economic",
    ingested_at: float | None = None,
    effective_at: float | None = None,
    period: ReferencePeriod | None = None,
    source: ObservationSource = SOURCE,
) -> ExternalObservation:
    """A generic observation; ``available_at=None`` means availability unknown."""

    stamp = (
        PointInTimeStamp.unknown(observed_at, ingested_at=ingested_at, effective_at=effective_at)
        if available_at is None
        else PointInTimeStamp.declared(
            observed_at, available_at, ingested_at=ingested_at, effective_at=effective_at
        )
    )
    return ExternalObservation(
        category=category,
        subject=subject,
        metric=metric,
        value=Decimal(value),
        unit="percent",
        stamp=stamp,
        source=source,
        period=period,
        revision=revision,
    )


def event(
    event_type: str,
    subject: str,
    observed_at: float,
    available_at: float | None,
    *,
    measurements: dict[str, str] | None = None,
    revision: int = 0,
    effective_at: float | None = None,
    source: ObservationSource = SOURCE,
) -> InformationEvent:
    """An information event; ``available_at=None`` means availability unknown."""

    stamp = (
        PointInTimeStamp.unknown(observed_at, effective_at=effective_at)
        if available_at is None
        else PointInTimeStamp.declared(observed_at, available_at, effective_at=effective_at)
    )
    return InformationEvent(
        event_type=event_type,
        subject=subject,
        stamp=stamp,
        source=source,
        measurements={name: Decimal(text) for name, text in (measurements or {}).items()},
        attributes={},
        revision=revision,
    )


#: Calendar quarter ends, 2023-2024, as fiscal quarters of a calendar-year issuer.
_QUARTER_BOUNDS = {
    (2023, 1): ("2023-01-01", "2023-03-31"),
    (2023, 2): ("2023-04-01", "2023-06-30"),
    (2023, 3): ("2023-07-01", "2023-09-30"),
    (2023, 4): ("2023-10-01", "2023-12-31"),
    (2024, 1): ("2024-01-01", "2024-03-31"),
    (2024, 2): ("2024-04-01", "2024-06-30"),
    (2024, 3): ("2024-07-01", "2024-09-30"),
    (2024, 4): ("2024-10-01", "2024-12-31"),
}


def quarter(year: int, number: int) -> FiscalPeriod:
    """A calendar-aligned fiscal quarter, midnight to 23:59:59 New York time."""

    start, end = _QUARTER_BOUNDS[(year, number)]
    return FiscalPeriod(year, number, ny(f"{start} 00:00"), ny(f"{end} 23:59:59"))


def fiscal_year(year: int) -> FiscalPeriod:
    """A calendar-aligned fiscal year."""

    return FiscalPeriod(year, None, ny(f"{year}-01-01 00:00"), ny(f"{year}-12-31 23:59:59"))


def fundamental(
    subject: str,
    statement: StatementType,
    line_item: str,
    period: FiscalPeriod,
    value: str,
    published_at: float,
    *,
    unit: str = "USD",
    revision: int = 0,
    available_at: float | None = None,
    known: bool = True,
    source: ObservationSource = SOURCE,
) -> FundamentalObservation:
    """A statement figure, available when published unless told otherwise."""

    stamp = (
        PointInTimeStamp.declared(
            period.end, published_at if available_at is None else available_at
        )
        if known
        else PointInTimeStamp.unknown(period.end)
    )
    return FundamentalObservation(
        subject=subject,
        statement=statement,
        line_item=line_item,
        fiscal_period=period,
        value=Decimal(value),
        unit=unit,
        published_at=published_at,
        stamp=stamp,
        source=source,
        revision=revision,
    )


INCOME = StatementType.INCOME_STATEMENT
BALANCE = StatementType.BALANCE_SHEET
CASH_FLOW = StatementType.CASH_FLOW_STATEMENT

#: Publication instants: 16:05 New York, some weeks after each quarter ends.
PUBLISHED = {
    (2023, 1): ny("2023-05-02 16:05"),
    (2023, 2): ny("2023-08-01 16:05"),
    (2023, 3): ny("2023-10-31 16:05"),
    (2023, 4): ny("2024-02-06 16:05"),
    (2024, 1): ny("2024-05-07 16:05"),
    (2024, 2): ny("2024-08-06 16:05"),
    (2024, 3): ny("2024-11-05 16:05"),
}

#: FY2024Q1 revenue is restated from 120 to 104 on this instant.
RESTATED_AT = ny("2024-09-10 08:00")


def issuer_records(subject: str = "AAA", scale: int = 1) -> list[FundamentalObservation]:
    """Seven quarters of an issuer, with one restatement of FY2024Q1 revenue."""

    records: list[FundamentalObservation] = []
    for index, ((year, number), published) in enumerate(sorted(PUBLISHED.items())):
        period = quarter(year, number)
        revenue = 100 + 5 * index
        records.extend(
            [
                fundamental(subject, INCOME, "revenue", period, str(revenue * scale), published),
                fundamental(
                    subject, INCOME, "net_income", period, str((10 + index) * scale), published
                ),
                fundamental(
                    subject,
                    INCOME,
                    "eps_diluted",
                    period,
                    f"{(10 + index) / 10:.2f}",
                    published,
                    unit="USD/share",
                ),
                fundamental(
                    subject, BALANCE, "total_equity", period, str(400 * scale + index), published
                ),
                fundamental(
                    subject, BALANCE, "shares_diluted", period, "10", published, unit="shares"
                ),
                fundamental(subject, BALANCE, "total_debt", period, str(50 * scale), published),
                fundamental(subject, BALANCE, "cash", period, str(20 * scale), published),
            ]
        )
    records.append(
        fundamental(
            subject,
            INCOME,
            "revenue",
            quarter(2024, 1),
            str(104 * scale),
            RESTATED_AT,
            revision=1,
        )
    )
    return records


def issuer_set(
    records: Iterable[FundamentalObservation] | None = None,
) -> ObservationSet[FundamentalObservation]:
    """The issuer's records as one versioned set."""

    return build_observation_set(
        "fundamentals", issuer_records() if records is None else records, SOURCE
    )
