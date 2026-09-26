"""Shared setup for the v3.7 point-in-time examples (50-55).

Every example reads the same small world: six names trading on New York's real
trading days in spring 2024, bars stamped at the 16:00 close, and the external
information around them -- earnings releases, a daily news score, quarterly
statements -- written out below as the rows a vendor file would hold. Keeping it
here means each example is about its capability rather than about setup, the
reason ``_research_panel.py`` and ``_strategy_evidence.py`` exist.

Nothing here fetches anything, reads a clock or draws a random number. Every
row is written out, every instant is a New York wall-clock reading, and every
set is ingested with the bytes it stands for, so each example prints the same
identities on every machine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal

from alphalab.api import ingest_rows
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.data.cleaning import (
    CleaningPolicy,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
)
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.dataset import Dataset
from alphalab.data.ingestion import IngestionRequest
from alphalab.data.source import RawSource, SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency

SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")

#: New York's regular session. AlphaLab ships no holidays; the example declares
#: the one that falls in its window.
NYSE = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
    holidays=frozenset({date(2024, 3, 29)}),
)

SESSIONS = NYSE.sessions_between(date(2024, 3, 1), date(2024, 5, 31))
_ZONE = NYSE.local_datetime(0.0).tzinfo

CLEANING = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

#: Each earnings release: subject, announcement and delivery (New York wall
#: clock; an empty delivery is a feed that recorded none), actual, consensus,
#: and how far the price moved on the session the news could first be traded.
EARNINGS = (
    ("AAA", "2024-04-16 16:05", "2024-04-16 16:06", "1.60", "1.40", 0.06),
    ("BBB", "2024-04-18 07:30", "2024-04-18 07:31", "0.80", "0.95", -0.05),
    ("CCC", "2024-04-23 16:10", "2024-04-23 16:11", "2.10", "2.00", 0.03),
    ("DDD", "2024-04-25 12:00", "2024-04-25 12:00", "1.05", "1.20", -0.04),
    ("EEE", "2024-04-30 16:02", "", "0.55", "0.50", 0.02),
    ("FFF", "2024-05-02 06:45", "2024-05-02 06:45", "3.30", "3.00", 0.05),
)

#: Quarterly filings: fiscal year and quarter, the span, and the filing date --
#: a bare date, as most filing indexes publish it.
QUARTERS = (
    (2023, 1, "2023-01-01", "2023-03-31", "2023-04-27"),
    (2023, 2, "2023-04-01", "2023-06-30", "2023-07-27"),
    (2023, 3, "2023-07-01", "2023-09-30", "2023-10-26"),
    (2023, 4, "2023-10-01", "2023-12-31", "2024-02-01"),
    (2024, 1, "2024-01-01", "2024-03-31", "2024-04-25"),
)

#: AAA's FY2023Q4 earnings per share are restated from 0.65 to 0.20 on this date.
RESTATED_FILED = "2024-05-10"


def ny(text: str) -> float:
    """A New York wall-clock reading, ``"2024-04-16 16:05"``, as Unix seconds."""

    return datetime.fromisoformat(text).replace(tzinfo=_ZONE).timestamp()


def close_of(day: date) -> float:
    """The instant of a session's 16:00 close."""

    bounds = NYSE.session_bounds(day)
    if bounds is None:
        raise ValueError(f"{day} is not a trading day")
    return bounds[1]


def label(instant: float) -> str:
    """An instant as a New York wall clock, for printing."""

    return NYSE.local_datetime(instant).strftime("%Y-%m-%d %H:%M")


def csv_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    """The rows as the CSV bytes they stand for, header first."""

    header = list(rows[0])
    lines = [",".join(header), *(",".join(str(row[key]) for key in header) for row in rows)]
    return "\n".join(lines).encode("utf-8")


def vendor_file(name: str, rows: Sequence[Mapping[str, object]], retrieved_at: float) -> RawSource:
    """A vendor file's provenance: its name, its exact bytes, when it was read."""

    return raw_source_from_bytes(
        SourceKind.VENDOR_FILE, name, csv_bytes(rows), retrieved_at, "text/csv", "utf-8"
    )


def _reaction_day(delivered: str) -> date | None:
    if not delivered:
        return None
    opens = NYSE.next_open(ny(delivered))
    return None if opens is None else NYSE.trading_day_of(opens)


def price_rows() -> list[dict[str, str]]:
    """Six names' daily bars, with each earnings reaction on its tradable session."""

    jumps = {
        subject: (_reaction_day(delivered), move) for subject, _, delivered, *_, move in EARNINGS
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
                    "volume": str(50_000 + position),
                }
            )
    return rows


def ingest_prices() -> Dataset:
    """The price panel as a versioned dataset, stamped at each session's close."""

    rows = price_rows()
    request = IngestionRequest(
        name="PIT-PANEL",
        source=raw_source_from_bytes(
            SourceKind.VENDOR_FILE,
            "panel.csv",
            csv_bytes(rows),
            1_717_000_000.0,
            "text/csv",
            "utf-8",
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="America/New_York",
    )
    return ingest_rows(rows, request).dataset


def earnings_rows() -> list[dict[str, str]]:
    """The earnings calendar a vendor delivered, with one correction appended."""

    rows = [
        {
            "ticker": subject,
            "kind": "earnings.release",
            "announced": announced,
            "delivered": delivered,
            "actual": actual,
            "consensus": consensus,
            "revision": "0",
        }
        for subject, announced, delivered, actual, consensus, _ in EARNINGS
    ]
    rows.append({**rows[0], "delivered": "2024-04-17 11:00", "actual": "1.62", "revision": "1"})
    return rows


def sentiment_rows() -> list[dict[str, str]]:
    """A daily news score per name, observed at 18:00 on each trading day."""

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
    return rows


def filing_rows() -> list[dict[str, str]]:
    """Five quarters of four line items per name, and AAA's restatement."""

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
                ("net_income", "INCOME_STATEMENT", str(50 + 10 * position + 5 * index), "USD"),
                ("revenue", "INCOME_STATEMENT", str(500 + 50 * position + 20 * index), "USD"),
                ("total_equity", "BALANCE_SHEET", str(1000 + 100 * position + index), "USD"),
                ("shares_diluted", "BALANCE_SHEET", "100", "shares"),
                ("total_debt", "BALANCE_SHEET", str(300 + 10 * position), "USD"),
                ("cash", "BALANCE_SHEET", str(120 + 5 * position), "USD"),
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
            "issuer": "AAA",
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
    return rows


def banner(number: int, title: str) -> None:
    print("=" * 72)
    print(f"AlphaLab Example {number} : {title}")
    print("=" * 72)


def section(title: str) -> None:
    print()
    print(f"-- {title} " + "-" * max(0, 66 - len(title)))
